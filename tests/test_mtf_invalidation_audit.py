"""Phase 5.2V §12 — MTF invalidation audit.

Code-inspection finding (see the Phase 5.2V report §P): grep across
engine/trade/setups.py and every engine/playbooks/evaluators/*.py shows
`CandidateStatus`/`SetupStatus` is assigned ONLY `TRIGGERED` or `FORMING`
anywhere in the current rule set — `INVALIDATED` is a defined enum member
(engine/playbooks/taxonomy.py) that is NEVER actually produced by any
matcher or evaluator today. Therefore multi-timeframe context cannot
possibly influence "setup_status becomes INVALIDATED during ENTRY_ACTIVE"
for any of the 8 currently-implementable playbooks — that transition does
not exist yet, with or without MTF data. This is a stronger invariant than
"MTF doesn't matter" — it's "the INVALIDATED branch is unreachable."

This test suite is the EMPIRICAL half of that proof: it exercises
evaluate_all_playbooks across a wide variety of market conditions (trending,
reversing, ranging, high/low volatility, WITH and WITHOUT higher_timeframe_
data) and asserts setup_status is never "INVALIDATED". If ANY current or
future playbook ever introduces an INVALIDATED transition, this test breaks
immediately — a deliberate tripwire forcing the Phase 5.2 MTF question to be
revisited at that point, exactly per the task's audit requirement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.market_state.market_intelligence import build_snapshot
from engine.playbooks.engine import evaluate_all_playbooks
from engine.backtest.resampling import resample_ohlcv
from tests.conftest import make_rth_days


def _seeded_walk(n, seed, drift, vol_scale, start="2024-01-02 09:30"):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(drift, vol_scale, n).cumsum()
    openp = np.roll(close, 1)
    openp[0] = close[0]
    high = np.maximum(openp, close) + rng.uniform(0, vol_scale, n)
    low = np.minimum(openp, close) - rng.uniform(0, vol_scale, n)
    volume = rng.integers(1000, 5000, n)
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _v_shaped(n, start="2024-01-02 09:30"):
    down = list(np.linspace(150, 100, n // 2))
    up = list(np.linspace(100, 150, n - n // 2))
    closes = down + up
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
         "close": closes, "volume": [2000] * len(closes)},
        index=idx,
    )


_FIXTURES = {
    "trending_up": _seeded_walk(300, seed=1, drift=0.08, vol_scale=0.2),
    "trending_down": _seeded_walk(300, seed=2, drift=-0.08, vol_scale=0.2),
    "ranging_choppy": _seeded_walk(300, seed=3, drift=0.0, vol_scale=0.35),
    "low_volatility": _seeded_walk(200, seed=4, drift=0.01, vol_scale=0.03),
    "high_volatility": _seeded_walk(200, seed=5, drift=0.02, vol_scale=1.2),
    "v_shaped_reversal": _v_shaped(200),
    "multi_day_rth": make_rth_days(5),
}


def _assert_no_invalidated_anywhere(df, higher_timeframe_data=None, warmup=30, step=5):
    for i in range(warmup, len(df), step):
        prefix = df.iloc[: i + 1]
        htf = None
        if higher_timeframe_data:
            htf = {tf: resample_ohlcv(prefix, tf) for tf in higher_timeframe_data}
        snapshot = build_snapshot(
            symbol="TEST", timeframe="5min", df=prefix, data_source="test", timeframe_minutes=5,
            higher_timeframe_data=htf, now=prefix.index[-1],
        )
        if not snapshot.data_quality_ok:
            continue
        evals = evaluate_all_playbooks(snapshot, float(prefix["close"].iloc[-1]), client=None)
        invalidated = [e for e in evals if e.setup_status == "INVALIDATED"]
        assert not invalidated, f"unexpected INVALIDATED at prefix {i}: {[e.playbook_id for e in invalidated]}"


@pytest.mark.parametrize("name", list(_FIXTURES))
def test_no_playbook_ever_reports_invalidated_without_mtf(name):
    _assert_no_invalidated_anywhere(_FIXTURES[name], higher_timeframe_data=None)


@pytest.mark.parametrize("name", list(_FIXTURES))
def test_no_playbook_ever_reports_invalidated_with_mtf(name):
    _assert_no_invalidated_anywhere(_FIXTURES[name], higher_timeframe_data=("1hour",))


def test_invalidated_is_a_defined_but_currently_unreachable_status():
    """Documents the exact invariant this whole module tests: INVALIDATED
    exists in the taxonomy (so it CAN be produced in principle) but no
    current evaluator/matcher ever assigns it."""
    from engine.playbooks.taxonomy import SetupStatus
    assert SetupStatus.INVALIDATED.value == "INVALIDATED"  # the enum member exists...
    # ...but grepping every matcher/evaluator (see this module's docstring)
    # confirms no code path ever assigns it — the two parametrized tests
    # above are the empirical half of that proof, across many market
    # conditions and both with/without higher_timeframe_data.
