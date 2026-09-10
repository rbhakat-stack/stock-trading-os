"""The static PlaybookDefinition registry (§3-5 of the Phase 4 report) — one
entry per playbook this phase considered, implemented AND deferred. This is
the ONLY place playbook metadata is declared; evaluators
(engine/playbooks/evaluators/*.py) look their definition up from here by id.

Phase 5.2V — VERSIONED STORAGE (§1-4): internally keyed by (playbook_id,
version), with a separate CURRENT-version pointer per playbook_id. This is
a storage/resolution change ONLY — no PlaybookDefinition field value for any
currently-registered v1.0 (or v0.0 deferred) playbook changed. `get_definition
(playbook_id)` — every existing live call site's exact shape — is completely
unaffected: `version=None` resolves to the CURRENT pointer, byte-identical
to pre-Phase-5.2V behavior. `all_definitions()`/`implementable_definitions()`/
`enabled_definitions()` likewise return exactly the CURRENT definitions, in
the same playbook_id insertion order as before — any additional historical
or test-only version registered alongside a playbook_id is invisible to
these three functions and to live evaluation.
"""
from __future__ import annotations

from .definition import PlaybookDefinition, QualityComponentConfig
from .taxonomy import PlaybookFamily, PlaybookId


class UnknownPlaybookError(KeyError):
    """§3 — unknown playbook_id, fails closed."""


class UnknownPlaybookVersionError(KeyError):
    """§3 — a known playbook_id but an unregistered version, fails closed.
    Never silently substituted for the current version."""


class DuplicatePlaybookVersionError(ValueError):
    """§3 — a (playbook_id, version) pair is immutable once registered.
    Re-registering the same pair (accidentally editing an already-used
    version in place) fails fast, at import time."""

_STANDARD_QUALITY_COMPONENTS = (
    QualityComponentConfig("structure", "Structure", 20),
    QualityComponentConfig("multi_timeframe", "Multi-timeframe", 15),
    QualityComponentConfig("volume", "Volume", 15),
    QualityComponentConfig("volatility", "Volatility", 15),
    QualityComponentConfig("setup_confidence", "Trigger", 15),
    QualityComponentConfig("reward_risk", "Target quality", 20),
)

# playbook_id -> version -> PlaybookDefinition. Insertion order of playbook_id
# (the outer dict) is preserved exactly as each is first registered — matches
# pre-Phase-5.2V iteration order for all_definitions()/etc.
_DEFINITIONS: dict[str, dict[str, PlaybookDefinition]] = {}
_CURRENT_VERSION: dict[str, str] = {}  # playbook_id -> the CURRENT production version string


def _register(d: PlaybookDefinition, *, current: bool = True) -> None:
    """`current=True` (every call below, for every live playbook) sets this
    version as the CURRENT production pointer — exactly one per playbook_id,
    explicit, never inferred from "most recently registered" or "highest
    version string" (§2). `current=False` is for test-only ADDITIONAL
    versions (see `register_definition_for_testing`) that must never affect
    live evaluation."""
    versions = _DEFINITIONS.setdefault(d.playbook_id, {})
    if d.version in versions:
        raise DuplicatePlaybookVersionError(
            f"{d.playbook_id} v{d.version} is already registered — a playbook_id+version pair is immutable "
            "once registered (§3 of the Phase 5.2V task). Bump the version instead of re-registering it."
        )
    versions[d.version] = d
    if current:
        _CURRENT_VERSION[d.playbook_id] = d.version


def register_definition_for_testing(d: PlaybookDefinition) -> None:
    """Phase 5.2V §6 test-only hook: registers an ADDITIONAL version for an
    already-registered playbook_id WITHOUT touching the CURRENT pointer —
    used to prove version-pinning correctness (e.g. a synthetic v1.1 that
    coexists with the real v1.0) without ever releasing a production v1.1.
    Pair with `unregister_definition_for_testing` in test teardown so no
    test leaks registry state into another test."""
    _register(d, current=False)


def unregister_definition_for_testing(playbook_id: str, version: str) -> None:
    versions = _DEFINITIONS.get(playbook_id)
    if versions is None or version not in versions:
        return
    if _CURRENT_VERSION.get(playbook_id) == version:
        raise ValueError(f"refusing to unregister {playbook_id} v{version} — it is the CURRENT production version")
    del versions[version]


_register(PlaybookDefinition(
    playbook_id=PlaybookId.BREAKOUT_RETEST_LONG.value, version="1.0", name="Breakout Retest Long",
    family=PlaybookFamily.BREAKOUT.value, direction="LONG",
    short_description="Enter on a successful retest of a broken resistance level, now acting as support.",
    plain_english_description=(
        "Price broke above a prior resistance level with real follow-through, then came back down to "
        "retest that level and held it as new support. This is a continuation entry, not the initial breakout."
    ),
    when_it_works="Works best in an uptrend or transitional-bullish regime, with a clean, accepted breakout and a held retest.",
    what_can_go_wrong="The retest can fail and price can close back below the old resistance, invalidating the level entirely.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("UPTREND_CONFIRMED", "TRANSITIONAL_BULLISH"),
    prerequisites=(
        "Market state is UPTREND_CONFIRMED or TRANSITIONAL_BULLISH.",
        "A breakout with direction UP exists.",
        "That breakout is classified SUCCESSFUL_BREAKOUT or SUCCESSFUL_BREAKOUT_RETEST.",
    ),
    trigger_rules=("Breakout state is SUCCESSFUL_BREAKOUT_RETEST — price has already retested the broken level and held.",),
    confirmation_rules=("Volume, volatility, and multi-timeframe alignment are reported as soft context, not hard gates.",),
    disqualifiers=("Breakout state is FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED.", "Market state is bearish."),
    entry_rules=("Enter within a small ATR-buffered zone around the broken level.",),
    stop_rules=("Structural stop below the broken level (see invalidation).",),
    target_rules=("Nearest resistance zone(s) above entry, from existing support/resistance structure.",),
    management_rules=("Consider partial profit at Target 1.", "Do not widen the stop after entry."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("market_state", "breakout"), optional_evidence=("volume_level", "volatility", "multi_timeframe_alignment"),
    contra_evidence=("multi_timeframe CONFLICTED", "volume VERY_LOW"),
    entry_type="RETEST_HOLD",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.BREAKOUT_RETEST_SHORT.value, version="1.0", name="Breakout Retest Short",
    family=PlaybookFamily.BREAKOUT.value, direction="SHORT",
    short_description="Enter on a successful retest of broken support, now acting as resistance.",
    plain_english_description=(
        "Price broke below a prior support level with real follow-through, then came back up to retest "
        "that level and held it as new resistance. A continuation entry, not the initial breakdown."
    ),
    when_it_works="Works best in a downtrend or transitional-bearish regime, with a clean, accepted breakdown and a held retest.",
    what_can_go_wrong="The retest can fail and price can close back above the old support, invalidating the level entirely.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("DOWNTREND_CONFIRMED", "TRANSITIONAL_BEARISH"),
    prerequisites=(
        "Market state is DOWNTREND_CONFIRMED or TRANSITIONAL_BEARISH.",
        "A breakout with direction DOWN exists.",
        "That breakout is classified SUCCESSFUL_BREAKOUT or SUCCESSFUL_BREAKOUT_RETEST.",
    ),
    trigger_rules=("Breakout state is SUCCESSFUL_BREAKOUT_RETEST — price has already retested the broken level and held.",),
    confirmation_rules=("Volume, volatility, and multi-timeframe alignment are reported as soft context, not hard gates.",),
    disqualifiers=("Breakout state is FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED.", "Market state is bullish."),
    entry_rules=("Enter within a small ATR-buffered zone around the broken level.",),
    stop_rules=("Structural stop above the broken level (see invalidation).",),
    target_rules=("Nearest support zone(s) below entry, from existing support/resistance structure.",),
    management_rules=("Consider partial profit at Target 1.", "Do not widen the stop after entry."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("market_state", "breakout"), optional_evidence=("volume_level", "volatility", "multi_timeframe_alignment"),
    contra_evidence=("multi_timeframe CONFLICTED", "volume VERY_LOW"),
    entry_type="RETEST_HOLD",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.TREND_PULLBACK_LONG.value, version="1.0", name="Trend Pullback Long",
    family=PlaybookFamily.TREND_CONTINUATION.value, direction="LONG",
    short_description="Buy a pullback into a support zone during a confirmed, healthy uptrend.",
    plain_english_description=(
        "The market is in a confirmed uptrend with acceptable trend quality, and price has pulled back "
        "into a recognized support zone below current price — a classic continuation entry."
    ),
    when_it_works="Best in a STRONG or HEALTHY uptrend, pulling back to a well-touched support zone.",
    what_can_go_wrong="The pullback can become a genuine reversal if the trend is already weakening or failing.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("UPTREND_CONFIRMED",),
    prerequisites=(
        "Market state is UPTREND_CONFIRMED.",
        "Trend quality is not FAILURE_RISK or REVERSAL_DEVELOPING.",
        "A support zone exists below current price.",
    ),
    trigger_rules=("Price has pulled back into the support zone, or is within pullback proximity of it.",),
    confirmation_rules=("Zone strength/touch count and trend quality are reported as evidence.",),
    disqualifiers=("Trend quality is FAILURE_RISK or REVERSAL_DEVELOPING.", "No support zone exists below price."),
    entry_rules=("Limit entry within the support zone boundaries.",),
    stop_rules=("Structural stop below the support zone.",),
    target_rules=("Nearest resistance zone(s) above entry.",),
    management_rules=("Consider partial profit at Target 1.", "Trail the stop only after a new higher low confirms.",),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("market_state", "trend_quality", "support_zones"), optional_evidence=("volume_level", "volatility", "multi_timeframe_alignment"),
    contra_evidence=("trend_quality WEAKENING", "multi_timeframe CONFLICTED"),
    entry_type="PULLBACK_RECLAIM",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.TREND_PULLBACK_SHORT.value, version="1.0", name="Trend Pullback Short",
    family=PlaybookFamily.TREND_CONTINUATION.value, direction="SHORT",
    short_description="Sell a pullback into a resistance zone during a confirmed, healthy downtrend.",
    plain_english_description=(
        "The market is in a confirmed downtrend with acceptable trend quality, and price has pulled back "
        "into a recognized resistance zone above current price — a classic continuation entry."
    ),
    when_it_works="Best in a STRONG or HEALTHY downtrend, pulling back to a well-touched resistance zone.",
    what_can_go_wrong="The pullback can become a genuine reversal if the trend is already weakening or failing.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("DOWNTREND_CONFIRMED",),
    prerequisites=(
        "Market state is DOWNTREND_CONFIRMED.",
        "Trend quality is not FAILURE_RISK or REVERSAL_DEVELOPING.",
        "A resistance zone exists above current price.",
    ),
    trigger_rules=("Price has pulled back into the resistance zone, or is within pullback proximity of it.",),
    confirmation_rules=("Zone strength/touch count and trend quality are reported as evidence.",),
    disqualifiers=("Trend quality is FAILURE_RISK or REVERSAL_DEVELOPING.", "No resistance zone exists above price."),
    entry_rules=("Limit entry within the resistance zone boundaries.",),
    stop_rules=("Structural stop above the resistance zone.",),
    target_rules=("Nearest support zone(s) below entry.",),
    management_rules=("Consider partial profit at Target 1.", "Trail the stop only after a new lower high confirms.",),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("market_state", "trend_quality", "resistance_zones"), optional_evidence=("volume_level", "volatility", "multi_timeframe_alignment"),
    contra_evidence=("trend_quality WEAKENING", "multi_timeframe CONFLICTED"),
    entry_type="PULLBACK_RECLAIM",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.FAILED_BREAKOUT_REVERSAL_LONG.value, version="1.0", name="Failed Breakout Reversal Long",
    family=PlaybookFamily.FAILED_BREAKOUT_REVERSAL.value, direction="LONG",
    short_description="Enter long when a downward breakdown attempt fails and price reclaims the level.",
    plain_english_description=(
        "Price attempted to break down through a level but the breakdown failed (FAILED_BREAKOUT/FAKEOUT/"
        "BREAKOUT_THAT_LATER_FAILED), and price has now reclaimed back above that level — a reversal entry."
    ),
    when_it_works="Works as a counter-trend or trend-agnostic reversal signal; strongest when not fighting a STRONG/HEALTHY confirmed downtrend.",
    what_can_go_wrong="Counter-trend against a strong confirmed downtrend is higher risk — flagged as a soft concern, not a hard block.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("ANY — a failed breakdown can occur in any regime",),
    prerequisites=("A breakdown (direction DOWN) exists.", "That breakdown is classified FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED."),
    trigger_rules=("Current price has reclaimed back above the failed breakdown level.",),
    confirmation_rules=("Counter-trend risk against a STRONG/HEALTHY confirmed downtrend is surfaced as a soft concern.",),
    disqualifiers=("No breakdown attempt exists.", "The breakdown attempt has not failed."),
    entry_rules=("Market-on-confirmation entry once price reclaims the level.",),
    stop_rules=("Structural stop below the failed breakdown level.",),
    target_rules=("Nearest resistance zone(s) above entry.",),
    management_rules=("Consider partial profit at Target 1.", "This is a reversal entry — respect the stop strictly."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("breakout",), optional_evidence=("market_state", "trend_quality", "volume_level", "volatility"),
    contra_evidence=("counter-trend against a STRONG/HEALTHY confirmed trend",),
    entry_type="FAILED_BREAK_REENTRY",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.FAILED_BREAKOUT_REVERSAL_SHORT.value, version="1.0", name="Failed Breakout Reversal Short",
    family=PlaybookFamily.FAILED_BREAKOUT_REVERSAL.value, direction="SHORT",
    short_description="Enter short when an upward breakout attempt fails and price breaks back below the level.",
    plain_english_description=(
        "Price attempted to break out above a level but the breakout failed (FAILED_BREAKOUT/FAKEOUT/"
        "BREAKOUT_THAT_LATER_FAILED), and price has now broken back below that level — a reversal entry."
    ),
    when_it_works="Works as a counter-trend or trend-agnostic reversal signal; strongest when not fighting a STRONG/HEALTHY confirmed uptrend.",
    what_can_go_wrong="Counter-trend against a strong confirmed uptrend is higher risk — flagged as a soft concern, not a hard block.",
    supported_timeframes=("5min", "15min", "1hour", "1day"),
    supported_regimes=("ANY — a failed breakout can occur in any regime",),
    prerequisites=("A breakout (direction UP) exists.", "That breakout is classified FAILED_BREAKOUT, FAKEOUT, or BREAKOUT_THAT_LATER_FAILED."),
    trigger_rules=("Current price has broken back below the failed breakout level.",),
    confirmation_rules=("Counter-trend risk against a STRONG/HEALTHY confirmed uptrend is surfaced as a soft concern.",),
    disqualifiers=("No breakout attempt exists.", "The breakout attempt has not failed."),
    entry_rules=("Market-on-confirmation entry once price breaks back below the level.",),
    stop_rules=("Structural stop above the failed breakout level.",),
    target_rules=("Nearest support zone(s) below entry.",),
    management_rules=("Consider partial profit at Target 1.", "This is a reversal entry — respect the stop strictly."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("breakout",), optional_evidence=("market_state", "trend_quality", "volume_level", "volatility"),
    contra_evidence=("counter-trend against a STRONG/HEALTHY confirmed trend",),
    entry_type="FAILED_BREAK_REENTRY",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.OPENING_RANGE_BREAKOUT_LONG.value, version="1.0", name="Opening Range Breakout Long",
    family=PlaybookFamily.OPENING_RANGE.value, direction="LONG",
    short_description="Enter long on a held breakout above today's opening range high.",
    plain_english_description=(
        "Price has broken above the high of today's opening range (default 30 minutes from session open) "
        "and that breakout has not already failed."
    ),
    when_it_works="Best on liquid intraday timeframes with a well-defined opening range and real follow-through volume.",
    what_can_go_wrong="Opening-range breaks are prone to fakeouts, especially on low-volume/choppy opens.",
    supported_timeframes=("5min", "15min"),
    supported_regimes=("ANY — the opening range is a session-local structure, independent of the swing-based market state",),
    prerequisites=("A valid opening range (ORH/ORL) has been computed for today's session.", "Price has broken above the ORH."),
    trigger_rules=("The breakout above ORH has not already failed (not FAILED_BREAKOUT/FAKEOUT/BREAKOUT_THAT_LATER_FAILED).",),
    confirmation_rules=("Opening volume and follow-through are reported as evidence.",),
    disqualifiers=("No opening range could be computed (e.g. outside the session window).", "The ORH breakout has already failed."),
    entry_rules=("Market-on-confirmation entry just above the ORH.",),
    stop_rules=("Structural stop below the opening range low (ORL).",),
    target_rules=("Nearest resistance zone(s) above entry, from existing support/resistance structure.",),
    management_rules=("Opening-range trades are fast-moving — do not widen the stop.", "Consider a partial exit at Target 1."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("opening_range",), optional_evidence=("volume_level", "volatility"),
    contra_evidence=("low opening volume", "wide/choppy opening range"),
    entry_type="BREAKOUT_CLOSE",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.OPENING_RANGE_BREAKOUT_SHORT.value, version="1.0", name="Opening Range Breakout Short",
    family=PlaybookFamily.OPENING_RANGE.value, direction="SHORT",
    short_description="Enter short on a held breakdown below today's opening range low.",
    plain_english_description=(
        "Price has broken below the low of today's opening range (default 30 minutes from session open) "
        "and that breakdown has not already failed."
    ),
    when_it_works="Best on liquid intraday timeframes with a well-defined opening range and real follow-through volume.",
    what_can_go_wrong="Opening-range breaks are prone to fakeouts, especially on low-volume/choppy opens.",
    supported_timeframes=("5min", "15min"),
    supported_regimes=("ANY — the opening range is a session-local structure, independent of the swing-based market state",),
    prerequisites=("A valid opening range (ORH/ORL) has been computed for today's session.", "Price has broken below the ORL."),
    trigger_rules=("The breakdown below ORL has not already failed (not FAILED_BREAKOUT/FAKEOUT/BREAKOUT_THAT_LATER_FAILED).",),
    confirmation_rules=("Opening volume and follow-through are reported as evidence.",),
    disqualifiers=("No opening range could be computed (e.g. outside the session window).", "The ORL breakdown has already failed."),
    entry_rules=("Market-on-confirmation entry just below the ORL.",),
    stop_rules=("Structural stop above the opening range high (ORH).",),
    target_rules=("Nearest support zone(s) below entry, from existing support/resistance structure.",),
    management_rules=("Opening-range trades are fast-moving — do not widen the stop.", "Consider a partial exit at Target 1."),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("opening_range",), optional_evidence=("volume_level", "volatility"),
    contra_evidence=("low opening volume", "wide/choppy opening range"),
    entry_type="BREAKOUT_CLOSE",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.RANGE_MEAN_REVERSION_LONG.value, version="1.0", name="Range Mean Reversion Long",
    family=PlaybookFamily.MEAN_REVERSION.value, direction="LONG",
    short_description="Fade a touch of the low of an active, recent consolidation range back toward its midpoint/high.",
    plain_english_description=(
        "The most recent bars are locally range-bound (CONSOLIDATION, not COMPRESSION or a trending window) "
        "and price is near the low of that range — a mean-reversion long back toward the range midpoint/high."
    ),
    when_it_works="Best in a genuinely range-bound, non-trending local window with multiple boundary touches.",
    what_can_go_wrong="A range can resolve into a breakdown instead of reverting — this playbook does not predict which.",
    supported_timeframes=("5min", "15min", "1hour"),
    supported_regimes=("Local CONSOLIDATION (see engine.market_state.consolidation — independent of the longer-term swing trend)",),
    prerequisites=("The most recent window is classified CONSOLIDATION (not COMPRESSION or NONE).",),
    trigger_rules=("Current price is within a small ATR proximity of the range low.",),
    confirmation_rules=("Boundary touch counts are reported as evidence.",),
    disqualifiers=("The window is classified COMPRESSION or NONE (trending) instead of CONSOLIDATION.",),
    entry_rules=("Limit entry at/near the range low.",),
    stop_rules=("Structural stop below the range low.",),
    target_rules=("The range midpoint, or the range high if the midpoint is not a meaningful reward.",),
    management_rules=("Exit or reassess promptly if price closes convincingly below the range low.",),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("consolidation",), optional_evidence=("volume_level", "volatility"),
    contra_evidence=("false_break_count > 0 on the low side",),
    entry_type="LIMIT_AT_ZONE",
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.RANGE_MEAN_REVERSION_SHORT.value, version="1.0", name="Range Mean Reversion Short",
    family=PlaybookFamily.MEAN_REVERSION.value, direction="SHORT",
    short_description="Fade a touch of the high of an active, recent consolidation range back toward its midpoint/low.",
    plain_english_description=(
        "The most recent bars are locally range-bound (CONSOLIDATION, not COMPRESSION or a trending window) "
        "and price is near the high of that range — a mean-reversion short back toward the range midpoint/low."
    ),
    when_it_works="Best in a genuinely range-bound, non-trending local window with multiple boundary touches.",
    what_can_go_wrong="A range can resolve into a breakout instead of reverting — this playbook does not predict which.",
    supported_timeframes=("5min", "15min", "1hour"),
    supported_regimes=("Local CONSOLIDATION (see engine.market_state.consolidation — independent of the longer-term swing trend)",),
    prerequisites=("The most recent window is classified CONSOLIDATION (not COMPRESSION or NONE).",),
    trigger_rules=("Current price is within a small ATR proximity of the range high.",),
    confirmation_rules=("Boundary touch counts are reported as evidence.",),
    disqualifiers=("The window is classified COMPRESSION or NONE (trending) instead of CONSOLIDATION.",),
    entry_rules=("Limit entry at/near the range high.",),
    stop_rules=("Structural stop above the range high.",),
    target_rules=("The range midpoint, or the range low if the midpoint is not a meaningful reward.",),
    management_rules=("Exit or reassess promptly if price closes convincingly above the range high.",),
    quality_components=_STANDARD_QUALITY_COMPONENTS,
    required_evidence=("consolidation",), optional_evidence=("volume_level", "volatility"),
    contra_evidence=("false_break_count > 0 on the high side",),
    entry_type="LIMIT_AT_ZONE",
))

_NOT_IMPLEMENTABLE_REASON = (
    "No VWAP signal exists anywhere in the current Phase 2 market-state engine "
    "(no vwap.py module, no vwap field on MarketIntelligenceSnapshot) — see the Phase 4 report. "
    "Marked NOT_IMPLEMENTABLE_YET rather than fabricating VWAP logic; this becomes implementable "
    "once a real intraday VWAP signal is added to Phase 2."
)
_EMPTY_QUALITY = ()

_register(PlaybookDefinition(
    playbook_id=PlaybookId.VWAP_RECLAIM_LONG.value, version="0.0", name="VWAP Reclaim Long",
    family=PlaybookFamily.VWAP.value, direction="LONG",
    short_description="NOT IMPLEMENTABLE YET — requires an intraday VWAP signal Phase 2 does not currently provide.",
    plain_english_description="Deferred: would enter long when price reclaims VWAP from below with confirmation.",
    when_it_works="N/A — not implementable yet.", what_can_go_wrong="N/A — not implementable yet.",
    supported_timeframes=(), supported_regimes=(), prerequisites=(), trigger_rules=(), confirmation_rules=(),
    disqualifiers=(), entry_rules=(), stop_rules=(), target_rules=(), management_rules=(),
    quality_components=_EMPTY_QUALITY, required_evidence=(), optional_evidence=(), contra_evidence=(),
    entry_type="NONE", enabled=False, implementable=False, not_implementable_reason=_NOT_IMPLEMENTABLE_REASON,
))

_register(PlaybookDefinition(
    playbook_id=PlaybookId.VWAP_REJECTION_SHORT.value, version="0.0", name="VWAP Rejection Short",
    family=PlaybookFamily.VWAP.value, direction="SHORT",
    short_description="NOT IMPLEMENTABLE YET — requires an intraday VWAP signal Phase 2 does not currently provide.",
    plain_english_description="Deferred: would enter short when price rejects VWAP from below with confirmation.",
    when_it_works="N/A — not implementable yet.", what_can_go_wrong="N/A — not implementable yet.",
    supported_timeframes=(), supported_regimes=(), prerequisites=(), trigger_rules=(), confirmation_rules=(),
    disqualifiers=(), entry_rules=(), stop_rules=(), target_rules=(), management_rules=(),
    quality_components=_EMPTY_QUALITY, required_evidence=(), optional_evidence=(), contra_evidence=(),
    entry_type="NONE", enabled=False, implementable=False, not_implementable_reason=_NOT_IMPLEMENTABLE_REASON,
))


def get_definition(playbook_id: str, version: str | None = None) -> PlaybookDefinition:
    """Backward compatible (§1/§4): `version=None` — every existing live
    call site's exact shape — resolves to the CURRENT production version,
    byte-identical to pre-Phase-5.2V behavior. An explicit `version` returns
    EXACTLY that historical version, deterministically, or fails closed
    (never inferred, never silently substituted for the current version)."""
    versions = _DEFINITIONS.get(playbook_id)
    if versions is None:
        raise UnknownPlaybookError(f"unknown playbook_id: {playbook_id!r}")
    resolved_version = version if version is not None else _CURRENT_VERSION[playbook_id]
    if resolved_version not in versions:
        raise UnknownPlaybookVersionError(
            f"{playbook_id} has no registered version {resolved_version!r}; known versions: "
            f"{sorted(versions)}"
        )
    return versions[resolved_version]


def get_current_definition(playbook_id: str) -> PlaybookDefinition:
    """§1 — explicit alias for get_definition(playbook_id) with no version,
    for call sites (like Phase 5.1 replay's pin resolution) that want to
    make "I mean CURRENT, not a pin" unambiguous in the reader's eyes."""
    return get_definition(playbook_id)


def get_current_version(playbook_id: str) -> str:
    if playbook_id not in _CURRENT_VERSION:
        raise UnknownPlaybookError(f"unknown playbook_id: {playbook_id!r}")
    return _CURRENT_VERSION[playbook_id]


def list_versions(playbook_id: str) -> tuple[str, ...]:
    versions = _DEFINITIONS.get(playbook_id)
    if versions is None:
        raise UnknownPlaybookError(f"unknown playbook_id: {playbook_id!r}")
    return tuple(sorted(versions))


def all_definitions() -> tuple[PlaybookDefinition, ...]:
    """Exactly the CURRENT definitions, one per registered playbook_id, in
    playbook_id registration order — any additional historical/test-only
    version is invisible here (§5)."""
    return tuple(_DEFINITIONS[pid][_CURRENT_VERSION[pid]] for pid in _DEFINITIONS)


def implementable_definitions() -> tuple[PlaybookDefinition, ...]:
    return tuple(d for d in all_definitions() if d.implementable)


def enabled_definitions() -> tuple[PlaybookDefinition, ...]:
    return tuple(d for d in all_definitions() if d.implementable and d.enabled)
