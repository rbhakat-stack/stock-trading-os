"""Trading-universe abstraction (Phase 5.0 §1).

Distinguishes ALL DISCOVERED SYMBOLS (every symbol the system knows about at
all) from the ELIGIBLE TRADING UNIVERSE (the subset a configurable filter
allows through) — because a future Phase 6 may apply real eligibility rules
(minimum market cap, minimum price, minimum ADV, minimum RVOL, ...) that do
not exist anywhere in this codebase yet and are deliberately NOT invented
here. Phase 5.0 ships exactly one trivial filter (an explicit allow-list) so
the seam is real and testable without pretending to solve Phase 6's problem.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class EligibilityFilter(Protocol):
    """Pluggable seam for Phase 6's eventual eligibility rules. Phase 5.0
    defines the seam only — it ships no market-cap/price/ADV/RVOL filter."""

    name: str

    def is_eligible(self, symbol: str) -> bool: ...


@dataclass(frozen=True)
class AllSymbolsEligible:
    """No-op filter: every discovered symbol is eligible. The default for
    Phase 5.0's own small, manually-curated validation universes."""
    name: str = "ALL_DISCOVERED_ELIGIBLE"

    def is_eligible(self, symbol: str) -> bool:
        return True


@dataclass(frozen=True)
class AllowListEligibilityFilter:
    """The one configurable filter Phase 5.0 ships: a static, explicit
    allow-list — never a computed market-data-driven rule."""
    allowed: frozenset[str]
    name: str = "ALLOW_LIST"

    def is_eligible(self, symbol: str) -> bool:
        return symbol in self.allowed


@dataclass(frozen=True)
class TradingUniverse:
    """A resolved, timestamped snapshot — not a live query. `discovered` is
    every symbol the system knows about; `eligible` is whatever subset
    `eligibility_filter_name` allowed through. Phase 6 will introduce real
    `EligibilityFilter` implementations; this dataclass's SHAPE does not need
    to change for that — only which filter `build_universe` was called with."""
    discovered: tuple[str, ...]
    eligible: tuple[str, ...]
    eligibility_filter_name: str
    as_of: datetime

    def __post_init__(self) -> None:
        unknown = set(self.eligible) - set(self.discovered)
        if unknown:
            raise ValueError(f"eligible symbols not present in discovered: {sorted(unknown)}")


def build_universe(discovered: list[str], eligibility_filter: EligibilityFilter, as_of: datetime) -> TradingUniverse:
    """Pure function, no I/O — `discovered` is supplied by the caller (e.g.
    `discover_symbols_from_repository` below, or a hand-written list in a
    test/small Phase 5.0 validation run)."""
    discovered_sorted = tuple(sorted(set(discovered)))
    eligible = tuple(s for s in discovered_sorted if eligibility_filter.is_eligible(s))
    return TradingUniverse(
        discovered=discovered_sorted, eligible=eligible,
        eligibility_filter_name=eligibility_filter.name, as_of=as_of,
    )


def discover_symbols_from_repository(client) -> list[str]:
    """ALL DISCOVERED SYMBOLS, sourced from the existing shared `symbols`
    reference table (migration 0001) — reused unchanged, never a second,
    parallel symbol registry."""
    res = client.table("symbols").select("symbol").execute()
    return [row["symbol"] for row in (res.data or [])]
