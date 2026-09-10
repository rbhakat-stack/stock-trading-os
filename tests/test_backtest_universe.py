"""Phase 5.0 §1 — universe abstraction: ALL DISCOVERED SYMBOLS vs. the
ELIGIBLE TRADING UNIVERSE, with no Phase 6 production filter implemented."""
from datetime import datetime, timezone

import pytest

from engine.backtest.universe import (
    AllowListEligibilityFilter, AllSymbolsEligible, TradingUniverse, build_universe,
    discover_symbols_from_repository,
)

_AS_OF = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_all_symbols_eligible_filter_passes_everything():
    universe = build_universe(["SPY", "AAPL", "QQQ"], AllSymbolsEligible(), _AS_OF)
    assert universe.discovered == ("AAPL", "QQQ", "SPY")  # sorted, deduplicated
    assert universe.eligible == ("AAPL", "QQQ", "SPY")
    assert universe.eligibility_filter_name == "ALL_DISCOVERED_ELIGIBLE"


def test_allow_list_filter_restricts_to_configured_symbols():
    filt = AllowListEligibilityFilter(allowed=frozenset({"SPY", "AAPL"}))
    universe = build_universe(["SPY", "AAPL", "QQQ", "TSLA"], filt, _AS_OF)
    assert universe.discovered == ("AAPL", "QQQ", "SPY", "TSLA")
    assert universe.eligible == ("AAPL", "SPY")  # QQQ/TSLA discovered but not eligible
    assert set(universe.eligible).issubset(set(universe.discovered))


def test_discovered_symbols_deduplicated_and_sorted():
    universe = build_universe(["TSLA", "AAPL", "AAPL", "SPY"], AllSymbolsEligible(), _AS_OF)
    assert universe.discovered == ("AAPL", "SPY", "TSLA")


def test_universe_construction_rejects_eligible_symbol_not_in_discovered():
    # Directly constructing an inconsistent TradingUniverse must fail —
    # eligible can never be a superset of discovered.
    with pytest.raises(ValueError):
        TradingUniverse(discovered=("SPY",), eligible=("SPY", "AAPL"), eligibility_filter_name="X", as_of=_AS_OF)


def test_empty_discovered_universe_produces_empty_eligible():
    universe = build_universe([], AllSymbolsEligible(), _AS_OF)
    assert universe.discovered == ()
    assert universe.eligible == ()


# ---- repository-backed discovery (fake client, no live DB) ----


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return type("Res", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "symbols"
        return _FakeQuery(self._rows)


def test_discover_symbols_from_repository_reads_the_symbols_table():
    client = _FakeClient([{"symbol": "SPY"}, {"symbol": "AAPL"}])
    discovered = discover_symbols_from_repository(client)
    assert discovered == ["SPY", "AAPL"]


def test_discover_symbols_from_repository_handles_empty_table():
    client = _FakeClient([])
    assert discover_symbols_from_repository(client) == []
