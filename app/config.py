"""Centralized, environment-driven app configuration.

Keeps environment-variable lookups for redirect handling in one place instead
of scattered across pages/auth code — see auth.py, which is the only module
that calls this.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

DEFAULT_LOCAL_APP_URL = "http://localhost:8501"


def get_app_url() -> str:
    """Base URL Supabase should redirect back to after email verification,
    password reset, or (later) a magic link / OAuth callback.

    - `APP_URL` unset -> falls back to the local-development default below.
    - `APP_URL` set -> must be a full http(s) URL, or this raises clearly
      rather than silently sending users to a broken redirect.

    Moving from local development to production is an environment-configuration
    change only (`APP_URL=https://<production-domain>`) — no code change.
    """
    raw = os.environ.get("APP_URL", "")
    trimmed = raw.strip()
    if not trimmed:
        return DEFAULT_LOCAL_APP_URL

    trimmed = trimmed.rstrip("/")
    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(
            f"APP_URL is set to an invalid value ({raw!r}). It must be a full URL starting with "
            "http:// or https://, e.g. http://localhost:8501 for local development or "
            "https://your-production-domain.com in production."
        )
    return trimmed
