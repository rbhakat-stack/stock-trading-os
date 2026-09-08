"""Supabase client construction.

Deliberately NOT a process-wide singleton for authenticated access: a Streamlit
deployment can serve multiple users' sessions from the same Python process, so
a single cached client mutated via `auth.set_session(...)` per user would leak
one user's session into another's requests. Callers (see app/auth.py) must keep
the authenticated client instance inside `st.session_state`, which Streamlit
already isolates per browser session.
"""
from __future__ import annotations

import os

from supabase import Client, create_client


def new_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_ANON_KEY are not configured. Copy .env.example to .env and fill them in."
        )
    return create_client(url, key)
