import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st

from authorization import require_authenticated

user = require_authenticated()

st.title("Dashboard")
st.info(
    "This is a Phase 1 placeholder. Net liquidation value, PnL, open risk, portfolio heat, and "
    "drawdown (see TRADING_OS_DESIGN.md §85) arrive with the Risk Engine in Phase 3."
)
st.write(f"Signed in as **{user.email}**")
st.page_link("pages/market_reader.py", label="Go to Market Reader →", icon=":material/candlestick_chart:")
