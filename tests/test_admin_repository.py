"""Privilege-escalation guard tests for the admin repository (§30/§14).

Most of these guards reject before ever constructing a Supabase client, so most
tests here need no mocking at all — that's deliberate: an ADMIN (or a USER
that somehow reached this code path) can't escalate even if the admin client
construction itself were somehow reachable, because the role/target checks run
first, synchronously, in Python, before any network call.
"""
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import repository.admin_repository as admin_repo


class _RoleOnlyClient:
    """Fake client supporting only the user_roles lookup used by
    _get_role_for_user — enough to test guards that need to know the target's
    current role, without a full PostgREST-shaped mock."""

    def __init__(self, role_name: str):
        self._role_name = role_name

    def table(self, name):
        assert name == "user_roles"
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=[{"role_id": 1, "roles": {"name": self._role_name}}])


# ---- change_user_role: privilege escalation guards ----


def test_admin_cannot_change_roles(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN"):
        admin_repo.change_user_role("admin1", "ADMIN", "target1", "ADMIN")


def test_user_cannot_change_roles(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError):
        admin_repo.change_user_role("user1", "USER", "target1", "ADMIN")


def test_cannot_promote_to_super_admin_even_as_super_admin(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="USER and ADMIN"):
        admin_repo.change_user_role("super1", "SUPER_ADMIN", "target1", "SUPER_ADMIN")


def test_cannot_change_own_role(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="own role"):
        admin_repo.change_user_role("super1", "SUPER_ADMIN", "super1", "ADMIN")


def test_cannot_modify_a_super_admin_target(monkeypatch):
    monkeypatch.setattr(admin_repo, "new_admin_client", lambda: _RoleOnlyClient("SUPER_ADMIN"))
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN accounts cannot be modified"):
        admin_repo.change_user_role("super1", "SUPER_ADMIN", "target1", "ADMIN")


# ---- set_user_status: privilege escalation guards ----


def test_admin_cannot_suspend_users(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN"):
        admin_repo.set_user_status("admin1", "ADMIN", "target1", "SUSPENDED")


def test_user_cannot_suspend_users(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError):
        admin_repo.set_user_status("user1", "USER", "target1", "SUSPENDED")


def test_cannot_suspend_self(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="own account status"):
        admin_repo.set_user_status("super1", "SUPER_ADMIN", "super1", "SUSPENDED")


def test_cannot_suspend_a_super_admin_target(monkeypatch):
    monkeypatch.setattr(admin_repo, "new_admin_client", lambda: _RoleOnlyClient("SUPER_ADMIN"))
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN accounts cannot be modified"):
        admin_repo.set_user_status("super1", "SUPER_ADMIN", "target1", "SUSPENDED")


def test_invalid_status_value_rejected(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="Invalid status"):
        admin_repo.set_user_status("super1", "SUPER_ADMIN", "target1", "DELETED")


# ---- set_feature_flag: ADMIN cannot write; live_execution_enabled is hard-locked ----


def test_admin_cannot_change_feature_flags(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN"):
        admin_repo.set_feature_flag("admin1", "ADMIN", "maintenance_mode", True)


def test_live_execution_cannot_be_enabled_even_by_super_admin(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="not implemented"):
        admin_repo.set_feature_flag("super1", "SUPER_ADMIN", "live_execution_enabled", True)


def test_unknown_flag_rejected(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="Unknown feature flag"):
        admin_repo.set_feature_flag("super1", "SUPER_ADMIN", "not_a_real_flag", True)


# ---- list_audit_events: ADMIN has no audit-log access at all (§18 — safer default) ----


def test_admin_cannot_list_audit_events(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError, match="SUPER_ADMIN"):
        admin_repo.list_audit_events("ADMIN")


def test_user_cannot_list_audit_events(monkeypatch):
    with pytest.raises(admin_repo.AdminActionError):
        admin_repo.list_audit_events("USER")
