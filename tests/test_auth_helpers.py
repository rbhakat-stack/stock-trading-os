import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

import httpx
from supabase_auth.errors import AuthApiError, AuthRetryableError, AuthWeakPasswordError

import auth as app_auth


# ---- redirect wiring: signup / resend must use the configured APP_URL ----


def test_signup_credentials_use_configured_app_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://trading.example.com")
    creds = app_auth._sign_up_credentials("trader@example.com", "hunter2", "Trader")
    assert creds["options"]["email_redirect_to"] == "https://trading.example.com"
    assert creds["email"] == "trader@example.com"


def test_signup_credentials_use_local_fallback_when_unset(monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    creds = app_auth._sign_up_credentials("trader@example.com", "hunter2", "Trader")
    assert creds["options"]["email_redirect_to"] == "http://localhost:8501"


def test_resend_credentials_use_configured_app_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://trading.example.com")
    creds = app_auth._resend_signup_credentials("trader@example.com")
    assert creds["options"]["email_redirect_to"] == "https://trading.example.com"
    assert creds["type"] == "signup"


# ---- error classification: never a raw exception in the UI ----


def test_timeout_error_produces_friendly_message():
    message = app_auth.classify_auth_error(httpx.ReadTimeout("The read operation timed out"))
    assert "timed out" in message.lower()
    assert "Traceback" not in message
    assert "ReadTimeout" not in message


def test_connect_error_produces_friendly_message():
    message = app_auth.classify_auth_error(httpx.ConnectError("connection refused"))
    assert "try again" in message.lower()


def test_invalid_credentials_produces_friendly_message():
    exc = AuthApiError("Invalid login credentials", 400, "invalid_credentials")
    assert app_auth.classify_auth_error(exc) == "Incorrect email or password."


def test_email_not_confirmed_points_to_resend():
    exc = AuthApiError("Email not confirmed", 400, "email_not_confirmed")
    message = app_auth.classify_auth_error(exc)
    assert "resend" in message.lower()


def test_rate_limited_produces_friendly_message():
    exc = AuthApiError("rate limited", 429, "over_email_send_rate_limit")
    assert "wait" in app_auth.classify_auth_error(exc).lower()


def test_retryable_error_produces_friendly_message():
    exc = AuthRetryableError("service unavailable", 503)
    assert "temporarily unavailable" in app_auth.classify_auth_error(exc).lower()


def test_weak_password_error_produces_friendly_message():
    exc = AuthWeakPasswordError("password too weak", 400, ["length"])
    assert "weak" in app_auth.classify_auth_error(exc).lower()


def test_unknown_error_never_leaks_raw_exception_text():
    class WeirdInternalError(Exception):
        pass

    secret = "some_internal_db_connection_string_leak"
    message = app_auth.classify_auth_error(WeirdInternalError(secret))
    assert secret not in message


# ---- resend-verification UI helper: no account-existence leakage ----


def test_resend_verification_does_not_leak_account_existence(monkeypatch):
    class FakeAuth:
        def resend(self, credentials):
            raise AuthApiError("not found", 400, "user_not_found")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(app_auth, "_client", lambda: FakeClient())
    ok, message = app_auth.request_resend_verification("nobody@example.com")
    assert ok is True
    assert message == app_auth._GENERIC_RESEND_MESSAGE


def test_resend_verification_success_uses_app_url_and_generic_message(monkeypatch):
    captured = {}

    class FakeAuth:
        def resend(self, credentials):
            captured["credentials"] = credentials

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setenv("APP_URL", "https://trading.example.com")
    monkeypatch.setattr(app_auth, "_client", lambda: FakeClient())
    ok, message = app_auth.request_resend_verification("trader@example.com")
    assert ok is True
    assert message == app_auth._GENERIC_RESEND_MESSAGE
    assert captured["credentials"]["options"]["email_redirect_to"] == "https://trading.example.com"


def test_resend_verification_surfaces_rate_limit_not_hidden(monkeypatch):
    class FakeAuth:
        def resend(self, credentials):
            raise AuthApiError("too many requests", 429, "over_email_send_rate_limit")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(app_auth, "_client", lambda: FakeClient())
    ok, message = app_auth.request_resend_verification("trader@example.com")
    assert ok is False
    assert "wait" in message.lower()


def test_resend_verification_surfaces_timeout(monkeypatch):
    class FakeAuth:
        def resend(self, credentials):
            raise httpx.ReadTimeout("timed out")

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(app_auth, "_client", lambda: FakeClient())
    ok, message = app_auth.request_resend_verification("trader@example.com")
    assert ok is False
    assert "timed out" in message.lower()
