import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from auth import get_authed_client
from authorization import require_authenticated
from engine.data_integrity.checks import check_bars, has_failure
from engine.data_provider.synthetic_provider import SyntheticProvider
from engine.features.volatility import atr
from engine.market_state.structure import label_structure
from engine.market_state.support_resistance import build_zones
from engine.market_state.swings import detect_swings
from engine.market_state.trend import classify_trend
from repository import market_data as md_repo
from repository import watchlists as wl_repo

st.title("Market Reader")

user = require_authenticated()
client = get_authed_client()

TIMEFRAME_MINUTES = {"5min": 5, "15min": 15, "1hour": 60, "1day": 1440}


def _get_provider():
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        from engine.data_provider.alpaca_provider import AlpacaProvider

        return AlpacaProvider(), "alpaca"
    return SyntheticProvider(), "synthetic"


provider, provider_name = _get_provider()
if provider_name == "synthetic":
    st.caption("Data source: **synthetic demo data** — set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env for real data.")
else:
    st.caption("Data source: **Alpaca (free IEX feed)**")

watchlist = wl_repo.get_or_create_default_watchlist(client, user.id)
symbols = wl_repo.list_watchlist_items(client, watchlist["id"])
if not symbols:
    md_repo.upsert_symbol(client, "SPY", name="SPDR S&P 500 ETF Trust", exchange="ARCA", asset_type="ETF")
    wl_repo.add_watchlist_item(client, watchlist["id"], "SPY")
    symbols = ["SPY"]

col_a, col_b, col_c = st.columns([2, 1, 1])
with col_a:
    symbol = st.selectbox("Symbol", options=symbols, index=0)
    new_symbol = st.text_input("Add symbol to watchlist", key="new_symbol").strip().upper()
    if new_symbol and st.button("Add to watchlist"):
        md_repo.upsert_symbol(client, new_symbol, name=None, exchange=None)
        wl_repo.add_watchlist_item(client, watchlist["id"], new_symbol)
        st.rerun()
with col_b:
    timeframe = st.selectbox("Timeframe", options=list(TIMEFRAME_MINUTES), index=0)
with col_c:
    lookback_days = st.number_input("Lookback (days)", min_value=1, max_value=90, value=10)

if st.button("Refresh Data", type="primary"):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_days))

    with st.spinner("Fetching bars..."):
        df = provider.get_ohlcv(symbol, timeframe, start, end)

    issues = check_bars(df, TIMEFRAME_MINUTES[timeframe], now=end)
    if has_failure(issues):
        st.error("DATA QUALITY FAILURE — cannot analyze this symbol right now.")
        for issue in issues:
            st.write(f"- **{issue.severity}** `{issue.code}`: {issue.message}")
        st.stop()
    for issue in issues:
        if issue.severity == "WARNING":
            st.warning(f"`{issue.code}`: {issue.message}")

    swing_points = detect_swings(df)
    swing_points = label_structure(swing_points)
    events = classify_trend(df, swing_points)
    atr_series = atr(df)
    latest_atr = float(atr_series.dropna().iloc[-1]) if atr_series.notna().any() else 1.0
    zones = build_zones(swing_points, latest_atr)

    md_repo.upsert_symbol(client, symbol, name=None, exchange=None)
    md_repo.upsert_bars(client, symbol, timeframe, df, provider=provider_name)
    md_repo.upsert_swing_points(client, symbol, timeframe, swing_points, algorithm_version="swings-v1")
    md_repo.upsert_market_state_events(client, symbol, timeframe, events, algorithm_version="trend-v1")

    st.session_state["market_reader_data"] = {
        "df": df,
        "swing_points": swing_points,
        "events": events,
        "zones": zones,
        "symbol": symbol,
        "timeframe": timeframe,
    }
    st.success(f"Loaded {len(df)} bars, {len(swing_points)} swing points, {len(events)} structural events.")

data = st.session_state.get("market_reader_data")
if data and data["symbol"] == symbol and data["timeframe"] == timeframe:
    df = data["df"]
    swing_points = data["swing_points"]
    events = data["events"]
    zones = data["zones"]
    majors = [p for p in swing_points if p.significance.value == "MAJOR"]
    minors = [p for p in swing_points if p.significance.value == "MINOR"]

    chart_col, panel_col = st.columns([3, 1])

    with chart_col:
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=symbol
                )
            ]
        )
        if majors:
            fig.add_trace(
                go.Scatter(
                    x=[p.ts for p in majors],
                    y=[p.price for p in majors],
                    mode="markers+text",
                    text=[p.label.value if p.label.value != "NONE" else "" for p in majors],
                    textposition="top center",
                    marker=dict(size=9, symbol="diamond", color="#1f77b4"),
                    name="Major swing",
                )
            )
        if minors:
            fig.add_trace(
                go.Scatter(
                    x=[p.ts for p in minors],
                    y=[p.price for p in minors],
                    mode="markers",
                    marker=dict(size=5, symbol="circle-open", color="#888888"),
                    name="Minor swing",
                )
            )
        for zone in zones:
            fig.add_hrect(
                y0=zone["lower_boundary"],
                y1=zone["upper_boundary"],
                fillcolor="green" if zone["zone_type"] == "SUPPORT" else "red",
                opacity=0.08,
                line_width=0,
            )
        fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with panel_col:
        st.subheader("Market State")
        if events:
            latest = events[-1]
            st.metric("State", latest.state.value.replace("_", " "))
            if latest.state.value in ("UPTREND_WARNING", "DOWNTREND_WARNING"):
                st.warning("STRUCTURE WARNING — REVERSAL NOT YET CONFIRMED")
            st.caption(f"As of {latest.ts}")
            st.write(f"Last structural event: **{latest.evidence.get('event', '—')}**")
        else:
            st.info("No confirmed structure yet for this window — need more swing points.")
        st.divider()
        st.write(f"**Major swings:** {len(majors)}")
        st.write(f"**Minor swings:** {len(minors)}")
        st.write(f"**S/R zones:** {len(zones)}")

    with st.expander("Structural event log"):
        if events:
            ev_df = pd.DataFrame(
                [
                    {
                        "ts": e.ts,
                        "state": e.state.value,
                        "label": e.structure_label.value,
                        "event": e.evidence.get("event"),
                    }
                    for e in events
                ]
            )
            st.dataframe(ev_df, use_container_width=True)
        else:
            st.write("No events yet.")
else:
    st.info("Click **Refresh Data** to load and analyze this symbol.")
