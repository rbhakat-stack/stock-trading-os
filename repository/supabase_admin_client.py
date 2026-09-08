"""Server-side-only Supabase client authenticated with the service-role key.

SECURITY: this client bypasses Row Level Security entirely and unlocks the
`auth.admin.*` API. It must NEVER be:
  - stored in st.session_state (unlike the ordinary user client — see
    repository/supabase_client.py — this one authorizes as the platform, not
    as any particular user, so it must never ride along with a browser session)
  - imported by anything under app/pages/ directly
  - constructed unless the caller has already passed authorization.require_admin()
    or require_super_admin()

Only repository/admin_repository.py may import this module.
"""
from __future__ import annotations

import os

from supabase import Client, create_client


def new_admin_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not service_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured. This must be set as a server-side "
            "environment variable (never committed, never sent to the browser) — see README.md."
        )
    return create_client(url, service_key)
