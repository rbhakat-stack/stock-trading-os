"""Session-scoped Supabase auth helpers for the Streamlit app.

The Supabase client instance itself lives in `st.session_state`, not a module
global — see repository/supabase_client.py for why that matters with multiple
concurrent users. Every RLS-protected read/write must go through
`get_authed_client()`, never a bare unauthenticated client.

Redirect handling: every Supabase call that accepts a redirect target uses
`config.get_app_url()`, never a hard-coded host — see config.py.
"""
from __future__ import annotations

import logging

import httpx
import streamlit as st
from supabase_auth.errors import AuthApiError, AuthError, AuthRetryableError, AuthWeakPasswordError

from config import get_app_url
from repository.supabase_client import new_client

logger = logging.getLogger("trading_os.auth")

# Codes explicitly acknowledged in Supabase's own API response — mapping them
# to friendlier text does not reveal anything Supabase hasn't already told us.
_FRIENDLY_MESSAGES: dict[str, str] = {
    "invalid_credentials": "Incorrect email or password.",
    "email_not_confirmed": "This email hasn't been verified yet. Check your inbox, or use "
    '"Resend verification email" below.',
    "over_email_send_rate_limit": "Too many emails requested recently. Please wait a few minutes and try again.",
    "over_request_rate_limit": "Too many attempts. Please wait a few minutes and try again.",
    "otp_expired": "That verification link has expired. Request a new one below.",
    "user_already_exists": "An account with this email already exists.",
    "email_exists": "An account with this email already exists.",
    "signup_disabled": "New account creation is currently disabled.",
    "weak_password": "That password is too weak. Please choose a stronger password.",
}

_GENERIC_RESEND_MESSAGE = (
    "If that email needs verification, a new confirmation link is on its way. "
    "Check your inbox (and spam folder)."
)


def _client():
    if "supabase_client" not in st.session_state:
        st.session_state["supabase_client"] = new_client()
    return st.session_state["supabase_client"]


def classify_auth_error(exc: Exception) -> str:
    """Maps a raw auth exception to a safe, user-facing message. Never surfaces
    a stack trace or internal exception text in the UI — full technical detail
    goes to the log only."""
    logger.warning("Auth operation failed: %s: %s", type(exc).__name__, exc)

    if isinstance(exc, httpx.TimeoutException):
        return "The request timed out. Check your connection and try again."
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "Could not reach the authentication service right now. Please try again shortly."
    if isinstance(exc, AuthRetryableError):
        return "The authentication service is temporarily unavailable. Please try again shortly."
    if isinstance(exc, AuthWeakPasswordError):
        return _FRIENDLY_MESSAGES["weak_password"]
    if isinstance(exc, AuthApiError):
        code = getattr(exc, "code", None)
        if code and code in _FRIENDLY_MESSAGES:
            return _FRIENDLY_MESSAGES[code]
        if exc.status == 400:
            # Supabase returns a generic 400 for bad login credentials on some API versions
            # without a machine-readable code — treat it the same as invalid_credentials.
            return _FRIENDLY_MESSAGES["invalid_credentials"]
        return "That request couldn't be completed. Please try again."
    if isinstance(exc, AuthError):
        return "That request couldn't be completed. Please try again."
    return "Something went wrong. Please try again."


def sign_in(email: str, password: str):
    client = _client()
    res = client.auth.sign_in_with_password({"email": email, "password": password})
    st.session_state["session"] = res.session
    st.session_state["user"] = res.user
    return res


def _sign_up_credentials(email: str, password: str, display_name: str) -> dict:
    return {
        "email": email,
        "password": password,
        "options": {
            "data": {"display_name": display_name},
            "email_redirect_to": get_app_url(),
        },
    }


def sign_up(email: str, password: str, display_name: str):
    client = _client()
    return client.auth.sign_up(_sign_up_credentials(email, password, display_name))


def _resend_signup_credentials(email: str) -> dict:
    return {
        "type": "signup",
        "email": email,
        "options": {"email_redirect_to": get_app_url()},
    }


def request_resend_verification(email: str) -> tuple[bool, str]:
    """Requests a fresh signup-confirmation email. Always returns a message
    that is safe to show the user directly.

    Deliberately returns the same success-shaped message whether or not the
    address is actually associated with a pending signup, so this never
    reveals account existence for an unrelated email — even when the
    underlying call fails with "user not found."
    """
    try:
        client = _client()
        client.auth.resend(_resend_signup_credentials(email))
        return True, _GENERIC_RESEND_MESSAGE
    except Exception as exc:  # noqa: BLE001 - classified below, never shown raw
        if getattr(exc, "code", None) == "user_not_found":
            logger.info("Resend requested for an email with no matching account.")
            return True, _GENERIC_RESEND_MESSAGE
        return False, classify_auth_error(exc)


def request_password_reset(email: str) -> tuple[bool, str]:
    """Sends a password-reset email using APP_URL as the redirect target.

    Helper only — no password-reset UI exists yet (see TRADING_OS_DESIGN.md
    §26/§94 for where that lands later). Kept here so the redirect wiring is
    ready when that UI is built.
    """
    try:
        client = _client()
        client.auth.reset_password_for_email(email, {"redirect_to": get_app_url()})
        return True, "If that email has an account, a password reset link is on its way."
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", None) == "user_not_found":
            return True, "If that email has an account, a password reset link is on its way."
        return False, classify_auth_error(exc)


def sign_out() -> None:
    client = _client()
    try:
        client.auth.sign_out()
    finally:
        st.session_state.pop("session", None)
        st.session_state.pop("user", None)


def get_authed_client():
    client = _client()
    session = st.session_state.get("session")
    if session:
        client.auth.set_session(session.access_token, session.refresh_token)
    return client


def require_login():
    user = st.session_state.get("user")
    if not user:
        st.warning("Please log in to continue.")
        st.stop()
    return user
