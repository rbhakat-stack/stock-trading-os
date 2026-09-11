import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from auth import get_authed_client
from authorization import require_authenticated
from engine.backtest.calendar import SessionStatus, classify_session_status, normalize_intraday_for_live_analysis
from engine.data_provider.synthetic_provider import SyntheticProvider
from engine.market_state.bos_choch import choch_banner_message
from engine.market_state.market_intelligence import build_snapshot
from engine.market_state.opening_range import SUPPORTED_WINDOW_MINUTES
from engine.market_state.support_resistance import rank_zones_by_relevance
from engine.market_state.time_of_day import MARKET_TZ
from repository import market_data as md_repo
from repository import watchlists as wl_repo

logger = logging.getLogger("trading_os.market_reader")


def _et(ts):
    """Converts a tz-aware timestamp to America/New_York for display — engine
    computations stay in whatever tz the source data arrives in (UTC); this is
    a display-only conversion so the UI never shows an unlabeled/ambiguous
    timezone (see Phase 2 hardening notes)."""
    if ts is None:
        return None
    return pd.Timestamp(ts).tz_convert(MARKET_TZ)

st.title("Market Reader")
st.caption(
    "Describes what the market is doing — structure, trend, volume, volatility, context. "
    "This is market interpretation, not a trade recommendation (see TRADING_OS_DESIGN.md §1)."
)

user = require_authenticated()
client = get_authed_client()

TIMEFRAME_MINUTES = {"5min": 5, "15min": 15, "1hour": 60, "1day": 1440}
# Reasonable higher timeframes to check for multi-timeframe alignment, per primary selection.
MULTI_TIMEFRAME_CANDIDATES = {
    "5min": ["15min", "1hour"],
    "15min": ["1hour", "1day"],
    "1hour": ["1day"],
    "1day": [],
}


def _get_provider():
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        from engine.data_provider.alpaca_provider import AlpacaProvider

        return AlpacaProvider(), "alpaca"
    return SyntheticProvider(), "synthetic"


provider, provider_name = _get_provider()
data_source_label = "SYNTHETIC DEMO" if provider_name == "synthetic" else "REAL MARKET DATA (Alpaca)"
st.caption(f"**DATA SOURCE:** {data_source_label}" + ("" if provider_name != "synthetic" else " — set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env for real data."))
if provider_name == "synthetic":
    st.caption(
        "Synthetic demo data is continuous calendar time (24/7), not restricted to real trading "
        "sessions — so Time of Day / Opening Range results reflect whatever the most recent bar's "
        "clock time happens to be, including nights and weekends. This is expected on demo data; "
        "see engine/data_provider/synthetic_provider.py."
    )
st.caption("All times below are shown in **America/New_York (ET)**.")

watchlist = wl_repo.get_or_create_default_watchlist(client, user.id)
symbols = wl_repo.list_watchlist_items(client, watchlist["id"])
if not symbols:
    md_repo.upsert_symbol(client, "SPY", name="SPDR S&P 500 ETF Trust", exchange="ARCA", asset_type="ETF")
    wl_repo.add_watchlist_item(client, watchlist["id"], "SPY")
    symbols = ["SPY"]

# ---------------------------------------------------------------------------
# Top controls
# ---------------------------------------------------------------------------
row1 = st.columns([2, 1, 1, 1])
with row1[0]:
    symbol = st.selectbox("Symbol", options=symbols, index=0)
    new_symbol = st.text_input("Add symbol to watchlist", key="new_symbol").strip().upper()
    if new_symbol and st.button("Add to watchlist"):
        md_repo.upsert_symbol(client, new_symbol, name=None, exchange=None)
        wl_repo.add_watchlist_item(client, watchlist["id"], new_symbol)
        st.rerun()
with row1[1]:
    timeframe = st.selectbox("Timeframe", options=list(TIMEFRAME_MINUTES), index=0)
with row1[2]:
    lookback_days = st.number_input("Lookback (days)", min_value=1, max_value=90, value=10)
with row1[3]:
    atr_period = st.number_input("ATR Period", min_value=5, max_value=50, value=14)

row2 = st.columns([1, 1, 1, 1])
with row2[0]:
    opening_range_minutes = st.selectbox("Opening Range", options=list(SUPPORTED_WINDOW_MINUTES), index=2)
with row2[1]:
    show_minor_swings = st.checkbox("Show minor swings", value=False)
with row2[2]:
    include_multi_timeframe = st.checkbox("Multi-timeframe context", value=True)
with row2[3]:
    refresh = st.button("Refresh Data", type="primary")

overlay_row = st.columns(4)
with overlay_row[0]:
    show_zones = st.checkbox("S/R zones", value=True)
with overlay_row[1]:
    show_opening_range = st.checkbox("ORH/ORL", value=True)
with overlay_row[2]:
    show_volume = st.checkbox("Volume", value=True)
with overlay_row[3]:
    show_retests = st.checkbox("Retest markers", value=True)

zone_filter_row = st.columns([1, 2])
with zone_filter_row[0]:
    show_all_zones = st.checkbox("Show all zones (advanced)", value=False)
with zone_filter_row[1]:
    zones_per_side = st.number_input(
        "Zones per side shown on chart", min_value=1, max_value=50, value=4, disabled=show_all_zones,
        help="A long lookback can produce many distinct S/R zones — this only filters what's drawn "
        "on the chart, ranked by strength/touches/recency/proximity to price. The full set is always "
        "used for analysis and persistence, and is visible in the panel counts and the data below.",
    )

# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
if refresh:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_days))

    is_real_market_data = provider_name == "alpaca"
    with st.spinner("Fetching bars..."):
        df = provider.get_ohlcv(symbol, timeframe, start, end)
        df = normalize_intraday_for_live_analysis(df, TIMEFRAME_MINUTES[timeframe], is_real_market_data)

        higher_tf_data = {}
        if include_multi_timeframe:
            for tf in MULTI_TIMEFRAME_CANDIDATES.get(timeframe, []):
                try:
                    tf_df = provider.get_ohlcv(symbol, tf, start, end)
                    higher_tf_data[tf] = normalize_intraday_for_live_analysis(
                        tf_df, TIMEFRAME_MINUTES[tf], is_real_market_data
                    )
                except Exception:  # noqa: BLE001 - multi-timeframe context is enrichment, never fatal
                    continue

    snapshot = build_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        df=df,
        data_source=provider_name,
        timeframe_minutes=TIMEFRAME_MINUTES[timeframe],
        atr_period=int(atr_period),
        opening_range_minutes=int(opening_range_minutes),
        higher_timeframe_data=higher_tf_data or None,
        now=pd.Timestamp(end),
    )

    if not snapshot.data_quality_ok:
        st.error("DATA QUALITY FAILURE — ANALYSIS UNAVAILABLE")
        for w in snapshot.warnings:
            st.write(f"- {w}")
        st.session_state.pop("market_reader_snapshot", None)
        st.stop()

    # Warning/info banners are rendered from the persisted snapshot in the Render
    # section below (not here) — that section re-executes on every rerun, so the
    # top banner and the Market State panel always read the same stored snapshot
    # and can never show conflicting CHOCH text (a real bug: this used to render
    # a one-shot banner only on the click that produced it, using a hardcoded
    # message that didn't recheck the CHOCH outcome on later reruns).

    # ---- persistence (see engine/market_state/market_intelligence.py + migration
    # 0003 for exactly what gets stored vs. computed on demand) ----
    # A persistence failure must never surface a raw traceback, and must never
    # block the analysis the user is here to see — the snapshot above was
    # already computed successfully in memory regardless of whether saving it
    # succeeds. Log the technical detail server-side; show a friendly message.
    try:
        md_repo.upsert_symbol(client, symbol, name=None, exchange=None)
        md_repo.upsert_bars(client, symbol, timeframe, df, provider=provider_name)
        md_repo.upsert_swing_points(client, symbol, timeframe, snapshot.swing_points, algorithm_version="swings-v1")

        extra_evidence_by_ts = {}
        if snapshot.latest_bos is not None:
            extra_evidence_by_ts[snapshot.latest_bos.ts] = {
                "bos_strength": snapshot.latest_bos.strength.value,
                "bos_break_distance_atr": snapshot.latest_bos.break_distance_atr,
                "bos_follow_through_bars": snapshot.latest_bos.follow_through_bars,
            }
        md_repo.upsert_market_state_events(
            client, symbol, timeframe, snapshot.events, algorithm_version="trend-v1",
            extra_evidence_by_ts=extra_evidence_by_ts,
        )

        md_repo.upsert_sr_zones(client, symbol, timeframe, snapshot.support_zones + snapshot.resistance_zones)

        if snapshot.breakout is not None:
            md_repo.upsert_breakout_event(client, symbol, timeframe, "BOS", snapshot.breakout, algorithm_version="breakouts-v1")

        if snapshot.opening_range is not None and snapshot.opening_range.session_date:
            md_repo.upsert_opening_range_event(
                client, symbol, timeframe,
                session_date=pd.Timestamp(snapshot.opening_range.session_date).date(),
                opening_range=snapshot.opening_range, algorithm_version="opening-range-v1",
            )
        persisted = True
    except Exception:  # noqa: BLE001 - never show a raw traceback to the user
        logger.exception("Failed to persist market analysis for %s/%s", symbol, timeframe)
        st.warning("Unable to persist market analysis. Please retry.")
        persisted = False

    st.session_state["market_reader_snapshot"] = {
        "snapshot": snapshot, "df": df, "symbol": symbol, "timeframe": timeframe, "fetch_now": end,
    }
    if persisted:
        st.success(f"Loaded {len(df)} bars, {len(snapshot.swing_points)} swing points, {len(snapshot.events)} structural events.")

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
state = st.session_state.get("market_reader_snapshot")
if not state or state["symbol"] != symbol or state["timeframe"] != timeframe:
    st.info("Click **Refresh Data** to load and analyze this symbol.")
    st.stop()

snapshot = state["snapshot"]
df = state["df"]
majors = [p for p in snapshot.swing_points if p.significance.value == "MAJOR"]
minors = [p for p in snapshot.swing_points if p.significance.value == "MINOR"]

# Display-only ET-indexed copy for the chart — all engine computation above
# already happened in the original (UTC) df; this never feeds back into it.
df_et = df.copy()
df_et.index = df_et.index.tz_convert(MARKET_TZ)

st.caption(f"**AS OF:** {_et(snapshot.as_of)} ET" if snapshot.as_of else "**AS OF:** —")

# Application acceptance hardening: an informative, non-alarming data-status
# banner reflecting exchange session state — never a red failure merely
# because the regular session has closed for the day (see
# engine.data_integrity.checks._staleness_reference_timestamp for the
# underlying session-aware freshness fix this banner explains to the user).
fetch_now = state.get("fetch_now")
if fetch_now is not None:
    latest_bar_str = f"{_et(snapshot.as_of).strftime('%I:%M %p')} ET" if snapshot.as_of else "—"
    session_status = classify_session_status(pd.Timestamp(fetch_now))
    if session_status == SessionStatus.ACTIVE_SESSION:
        st.success(f"DATA CURRENT  \nLatest bar: {latest_bar_str}")
    elif session_status == SessionStatus.MARKET_CLOSED_TODAY:
        st.info(f"MARKET CLOSED — DATA CURRENT THROUGH LAST SESSION  \nLatest bar: {latest_bar_str}")
    elif session_status == SessionStatus.MARKET_NOT_YET_OPEN:
        st.info(f"MARKET NOT YET OPEN — USING PREVIOUS COMPLETED SESSION  \nLatest bar: {latest_bar_str}")
    else:
        st.info(f"MARKET CLOSED — USING MOST RECENT COMPLETED SESSION  \nLatest bar: {latest_bar_str}")

for w in snapshot.warnings:
    st.warning(w)

# Same shared function drives this top banner AND the Market State panel's
# CHOCH line below — they read the identical, current outcome and can never
# disagree (see engine.market_state.bos_choch.choch_banner_message).
if snapshot.latest_choch is not None:
    _choch_message, _choch_severity = choch_banner_message(snapshot.latest_choch)
    if _choch_severity == "info":  # "warning" severity is already in snapshot.warnings above
        st.info(_choch_message)

chart_col, panel_col = st.columns([3, 1])

with chart_col:
    rows = 2 if show_volume else 1
    row_heights = [0.75, 0.25] if show_volume else [1.0]
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, row_heights=row_heights, vertical_spacing=0.03)

    fig.add_trace(
        go.Candlestick(x=df_et.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=symbol),
        row=1, col=1,
    )

    if majors:
        fig.add_trace(
            go.Scatter(
                x=[_et(p.ts) for p in majors], y=[p.price for p in majors], mode="markers+text",
                text=[p.label.value if p.label.value != "NONE" else "" for p in majors],
                textposition="top center", marker=dict(size=9, symbol="diamond", color="#1f77b4"),
                name="Major swing",
            ),
            row=1, col=1,
        )
    if show_minor_swings and minors:
        fig.add_trace(
            go.Scatter(
                x=[_et(p.ts) for p in minors], y=[p.price for p in minors], mode="markers",
                marker=dict(size=5, symbol="circle-open", color="#888888"), name="Minor swing",
            ),
            row=1, col=1,
        )

    if show_zones:
        current_price = float(df["close"].iloc[-1])
        if show_all_zones:
            zones_to_draw = snapshot.support_zones + snapshot.resistance_zones
        else:
            zones_to_draw = (
                rank_zones_by_relevance(snapshot.support_zones, current_price)[: int(zones_per_side)]
                + rank_zones_by_relevance(snapshot.resistance_zones, current_price)[: int(zones_per_side)]
            )
        for zone in zones_to_draw:
            fig.add_hrect(
                y0=zone.lower_boundary, y1=zone.upper_boundary,
                fillcolor="green" if zone.zone_type == "SUPPORT" else "red",
                opacity=0.08, line_width=0, row=1, col=1,
            )
            if show_retests and zone.retest_count > 0 and zone.last_touch_time is not None:
                fig.add_trace(
                    go.Scatter(
                        x=[_et(zone.last_touch_time)], y=[(zone.upper_boundary + zone.lower_boundary) / 2],
                        mode="markers", marker=dict(size=10, symbol="star", color="orange"),
                        name=f"Retest ({zone.zone_type})", showlegend=False,
                    ),
                    row=1, col=1,
                )

    if show_opening_range and snapshot.opening_range is not None and snapshot.opening_range.orh is not None:
        fig.add_hline(y=snapshot.opening_range.orh, line_dash="dash", line_color="purple", annotation_text="ORH", row=1, col=1)
        fig.add_hline(y=snapshot.opening_range.orl, line_dash="dash", line_color="purple", annotation_text="ORL", row=1, col=1)

    if show_volume:
        fig.add_trace(
            go.Bar(x=df_et.index, y=df["volume"], name="Volume", marker_color="rgba(100,100,100,0.5)"),
            row=2, col=1,
        )

    fig.update_layout(height=650, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

with panel_col:
    st.subheader("Market State")
    st.write(f"**SYMBOL:** {snapshot.symbol}")
    st.write(f"**TIMEFRAME:** {snapshot.timeframe}")
    # st.metric truncates long values in a narrow column (e.g. "DOWNTREND
    # CONFIRMED" clipped to "DOWNTREND CONFI...") — plain markdown wraps instead.
    st.markdown(f"**MARKET STATE:**  \n### {(snapshot.market_state or '—').replace('_', ' ')}")

    tq = snapshot.trend_quality
    st.write(f"**TREND QUALITY:** {tq.quality if tq else '—'}")

    lse = snapshot.latest_structural_event
    if lse:
        label = lse.evidence.get("event", lse.state.value)
        st.write(f"**LATEST STRUCTURAL EVENT:** {label.replace('_', ' ')} → {lse.state.value.replace('_', ' ')}")
        st.caption(f"as of {_et(lse.ts)} ET")
    else:
        st.write("**LATEST STRUCTURAL EVENT:** —")

    # Distinct from the above: the most recent BOS-type event specifically and
    # its strength — this can be chronologically OLDER than the latest
    # structural event above (e.g. if a CHOCH-driven reversal has happened
    # since), so its own timestamp is shown to make that visible rather than
    # implying it's current. See MarketIntelligenceSnapshot.latest_structural_event.
    if snapshot.latest_bos:
        st.write(f"**LATEST BOS:** {snapshot.latest_bos.direction} ({snapshot.latest_bos.strength.value})")
        st.caption(f"as of {_et(snapshot.latest_bos.ts)} ET")
    else:
        st.write("**LATEST BOS:** —")

    if snapshot.latest_choch:
        choch = snapshot.latest_choch
        panel_message, panel_severity = choch_banner_message(choch)
        if panel_severity == "warning":
            st.error(f"**CHOCH:** {panel_message}")
        else:
            st.write(f"**CHOCH:** {panel_message}")
        st.caption(f"outcome: {choch.outcome} · as of {_et(choch.ts)} ET")
    else:
        st.write("**CHOCH:** NONE")

    shown_note = "" if show_all_zones else f" ({min(int(zones_per_side), len(snapshot.support_zones))} shown on chart)"
    st.write(f"**SUPPORT ZONES:** {len(snapshot.support_zones)}{shown_note}")
    shown_note_r = "" if show_all_zones else f" ({min(int(zones_per_side), len(snapshot.resistance_zones))} shown on chart)"
    st.write(f"**RESISTANCE ZONES:** {len(snapshot.resistance_zones)}{shown_note_r}")

    # "Recent"/"local" in the label is deliberate, not decoration: this reflects
    # only the last `lookback_bars` bars (see evidence below), independent of
    # the overall MARKET STATE above — a symbol can be in a confirmed uptrend
    # while its most recent bars are locally consolidating (a pause inside a
    # larger trend). That is not a contradiction; see consolidation.py's
    # module docstring.
    _cons = snapshot.consolidation
    _lookback = _cons.evidence.get("lookback_bars") if _cons else None
    _lookback_note = f" (last {_lookback} bars)" if _lookback else ""
    st.write(f"**RECENT CONSOLIDATION:** {'YES' if _cons and _cons.state == 'CONSOLIDATION' else 'NO'}{_lookback_note}")
    st.write(f"**RECENT COMPRESSION:** {'YES' if _cons and _cons.state == 'COMPRESSION' else 'NO'}{_lookback_note}")

    if snapshot.breakout:
        st.write(f"**BREAKOUT STATE:** {snapshot.breakout.state.value}")
    else:
        st.write("**BREAKOUT STATE:** —")

    st.write(f"**VOLUME:** {snapshot.volume_level}")
    if snapshot.rvol and snapshot.rvol.value is not None:
        st.write(f"**RVOL:** {snapshot.rvol.value} ({snapshot.rvol.method})")
    else:
        st.write("**RVOL:** INSUFFICIENT_DATA")

    vol = snapshot.volatility
    st.write(f"**VOLATILITY:** {vol.level if vol else '—'}" + (f" ({vol.transition})" if vol else ""))
    st.write(f"**ATR:** {round(snapshot.atr_value, 4) if snapshot.atr_value else '—'}")

    orr = snapshot.opening_range
    st.write(f"**OPENING RANGE:** {orr.status if orr else '—'}")

    st.write(f"**TIME OF DAY:** {snapshot.time_of_day or '—'} (ET, as of latest bar)")

    if snapshot.multi_timeframe_alignment:
        mtf = snapshot.multi_timeframe_alignment
        label = mtf.level.replace("_", " ")
        if mtf.level == "CONFLICTED":
            st.error(f"**MULTI-TIMEFRAME:** {label}")
        else:
            st.write(f"**MULTI-TIMEFRAME:** {label}")
        with st.expander("Timeframe states"):
            for tf, s in mtf.timeframe_states.items():
                st.write(f"- {tf}: {s or 'insufficient data'}")
    else:
        st.write("**MULTI-TIMEFRAME:** not requested")

with st.expander("Why? — explainability (WHAT / WHY / EVIDENCE)", expanded=False):
    st.markdown(f"**MARKET STATE: {snapshot.market_state}**")
    st.write("WHY:")
    for e in snapshot.events[-5:]:
        st.write(f"- {_et(e.ts)} ET: {e.evidence.get('event')} → {e.state.value} ({e.structure_label.value})")
    if not snapshot.events:
        st.write("- no structural events yet in this window")

    if snapshot.latest_bos:
        st.markdown(f"**BOS STRENGTH: {snapshot.latest_bos.strength.value}**")
        st.write("EVIDENCE:")
        st.json(snapshot.latest_bos.evidence)

    if tq and tq.evidence:
        st.markdown(f"**TREND QUALITY: {tq.quality}**")
        st.write("EVIDENCE:")
        st.json(tq.evidence)

    if snapshot.consolidation:
        cons = snapshot.consolidation
        label = f"RECENT {cons.state}" if cons.state != "NONE" else "NO RECENT CONSOLIDATION/COMPRESSION"
        st.markdown(f"**{label}**")
        st.caption(
            "Based only on the local window below — independent of the overall MARKET STATE above. "
            "A confirmed trend with a locally consolidating recent window is expected, not contradictory."
        )
        st.write("EVIDENCE (analysis window):")
        st.json(cons.evidence)

with st.expander("Structural event log"):
    if snapshot.events:
        ev_df = pd.DataFrame(
            [
                {"ts (ET)": _et(e.ts), "state": e.state.value, "label": e.structure_label.value, "event": e.evidence.get("event")}
                for e in snapshot.events
            ]
        )
        st.dataframe(ev_df, use_container_width=True)
    else:
        st.write("No events yet.")
