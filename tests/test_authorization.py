import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

import pytest

import authorization


class _StopCalled(Exception):
    """Sentinel raised by our fake st.stop() so tests can assert a gate blocked access."""


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._single:
            if not self._rows:
                raise RuntimeError("no matching row")
            return SimpleNamespace(data=self._rows[0])
        return SimpleNamespace(data=self._rows)


class FakeClient:
    """Minimal fake covering exactly the table()/select()/eq()/single()/execute()
    chains authorization.py actually issues — not a general PostgREST mock."""

    def __init__(self, user_roles_rows=None, profile_row=None, flags_rows=None):
        self._user_roles_rows = user_roles_rows or []
        self._profile_row = profile_row
        self._flags_rows = flags_rows or []

    def table(self, name):
        if name == "user_roles":
            return _FakeQuery(self._user_roles_rows)
        if name == "profiles":
            return _FakeQuery([self._profile_row] if self._profile_row else [])
        if name == "feature_flags":
            return _FakeQuery(self._flags_rows)
        raise AssertionError(f"unexpected table {name!r}")


def _fake_user(uid="u1", email="user@example.com"):
    return SimpleNamespace(id=uid, email=email)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(authorization, "get_authed_client", lambda: client)


def _patch_stop_and_messages(monkeypatch):
    def _stop():
        raise _StopCalled()

    monkeypatch.setattr(authorization.st, "stop", _stop)
    monkeypatch.setattr(authorization.st, "error", lambda *a, **k: None)
    monkeypatch.setattr(authorization.st, "warning", lambda *a, **k: None)


# ---- get_current_user_role / get_current_account_status: fresh-fetch, fail-closed ----


def test_get_current_user_role_reads_from_supabase(monkeypatch):
    client = FakeClient(user_roles_rows=[{"role_id": 2, "roles": {"name": "ADMIN"}}])
    _patch_client(monkeypatch, client)
    assert authorization.get_current_user_role("u1") == "ADMIN"


def test_get_current_user_role_picks_highest_privilege_if_multiple_rows(monkeypatch):
    client = FakeClient(
        user_roles_rows=[{"role_id": 3, "roles": {"name": "USER"}}, {"role_id": 2, "roles": {"name": "ADMIN"}}]
    )
    _patch_client(monkeypatch, client)
    assert authorization.get_current_user_role("u1") == "ADMIN"


def test_get_current_user_role_none_when_no_row(monkeypatch):
    _patch_client(monkeypatch, FakeClient(user_roles_rows=[]))
    assert authorization.get_current_user_role("u1") is None


def test_get_current_user_role_fails_closed_on_error(monkeypatch):
    class Boom:
        def table(self, *_a, **_k):
            raise RuntimeError("network error")

    _patch_client(monkeypatch, Boom())
    assert authorization.get_current_user_role("u1") is None


def test_get_current_account_status_reads_from_supabase(monkeypatch):
    _patch_client(monkeypatch, FakeClient(profile_row={"status": "SUSPENDED"}))
    assert authorization.get_current_account_status("u1") == "SUSPENDED"


def test_get_current_account_status_fails_closed_when_row_missing(monkeypatch):
    _patch_client(monkeypatch, FakeClient(profile_row=None))
    assert authorization.get_current_account_status("u1") is None


# ---- require_authenticated: suspension + maintenance mode enforced on every protected page ----


def test_require_authenticated_allows_active_user(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    user = _fake_user()
    monkeypatch.setattr(authorization, "require_login", lambda: user)
    _patch_client(monkeypatch, FakeClient(profile_row={"status": "ACTIVE"}, flags_rows=[]))
    assert authorization.require_authenticated() is user


def test_require_authenticated_blocks_suspended_user(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    _patch_client(monkeypatch, FakeClient(profile_row={"status": "SUSPENDED"}))
    with pytest.raises(_StopCalled):
        authorization.require_authenticated()


def test_require_authenticated_fails_closed_when_status_unverifiable(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())

    class Boom:
        def table(self, *_a, **_k):
            raise RuntimeError("down")

    _patch_client(monkeypatch, Boom())
    with pytest.raises(_StopCalled):
        authorization.require_authenticated()


def test_require_authenticated_blocks_non_admin_during_maintenance(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(
        profile_row={"status": "ACTIVE"},
        user_roles_rows=[{"role_id": 3, "roles": {"name": "USER"}}],
        flags_rows=[{"key": "maintenance_mode", "enabled": True}],
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(_StopCalled):
        authorization.require_authenticated()


def test_require_authenticated_allows_super_admin_during_maintenance(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    user = _fake_user()
    monkeypatch.setattr(authorization, "require_login", lambda: user)
    client = FakeClient(
        profile_row={"status": "ACTIVE"},
        user_roles_rows=[{"role_id": 1, "roles": {"name": "SUPER_ADMIN"}}],
        flags_rows=[{"key": "maintenance_mode", "enabled": True}],
    )
    _patch_client(monkeypatch, client)
    assert authorization.require_authenticated() is user


# ---- require_admin / require_super_admin: the core privilege-escalation gates (§29/§30) ----


def test_require_admin_denies_user_role(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 3, "roles": {"name": "USER"}}])
    _patch_client(monkeypatch, client)
    with pytest.raises(_StopCalled):
        authorization.require_admin()


def test_require_admin_denies_when_role_unverifiable(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    _patch_client(monkeypatch, FakeClient(profile_row={"status": "ACTIVE"}, user_roles_rows=[]))
    with pytest.raises(_StopCalled):
        authorization.require_admin()


def test_require_admin_allows_admin_role(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    user = _fake_user()
    monkeypatch.setattr(authorization, "require_login", lambda: user)
    client = FakeClient(profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 2, "roles": {"name": "ADMIN"}}])
    _patch_client(monkeypatch, client)
    returned_user, role = authorization.require_admin()
    assert returned_user is user
    assert role == "ADMIN"


def test_require_admin_allows_super_admin_role(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(
        profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 1, "roles": {"name": "SUPER_ADMIN"}}]
    )
    _patch_client(monkeypatch, client)
    _, role = authorization.require_admin()
    assert role == "SUPER_ADMIN"


def test_require_super_admin_denies_admin_role(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 2, "roles": {"name": "ADMIN"}}])
    _patch_client(monkeypatch, client)
    with pytest.raises(_StopCalled):
        authorization.require_super_admin()


def test_require_super_admin_denies_user_role(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 3, "roles": {"name": "USER"}}])
    _patch_client(monkeypatch, client)
    with pytest.raises(_StopCalled):
        authorization.require_super_admin()


def test_require_super_admin_allows_super_admin(monkeypatch):
    _patch_stop_and_messages(monkeypatch)
    user = _fake_user()
    monkeypatch.setattr(authorization, "require_login", lambda: user)
    client = FakeClient(
        profile_row={"status": "ACTIVE"}, user_roles_rows=[{"role_id": 1, "roles": {"name": "SUPER_ADMIN"}}]
    )
    _patch_client(monkeypatch, client)
    returned_user, role = authorization.require_super_admin()
    assert returned_user is user
    assert role == "SUPER_ADMIN"


def test_require_super_admin_denies_suspended_super_admin(monkeypatch):
    # Suspension is checked before role — even a SUPER_ADMIN account marked
    # SUSPENDED must not pass require_super_admin().
    _patch_stop_and_messages(monkeypatch)
    monkeypatch.setattr(authorization, "require_login", lambda: _fake_user())
    client = FakeClient(
        profile_row={"status": "SUSPENDED"}, user_roles_rows=[{"role_id": 1, "roles": {"name": "SUPER_ADMIN"}}]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(_StopCalled):
        authorization.require_super_admin()
