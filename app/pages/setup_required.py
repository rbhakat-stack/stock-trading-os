import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from config import get_app_url

st.title("Setup required")

missing = [v for v in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not os.environ.get(v)]
if missing:
    st.error(
        "Missing required configuration: "
        + ", ".join(missing)
        + ".\n\nCopy `.env.example` to `.env`, fill in your free Supabase project's URL and anon key "
        "(Project Settings -> API), then restart the app. See README.md for the full setup walkthrough."
    )

try:
    get_app_url()
except RuntimeError as e:
    st.error(str(e))
