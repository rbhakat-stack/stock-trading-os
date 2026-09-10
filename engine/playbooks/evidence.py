"""Structured evidence primitives (§29 of the Phase 4 report) — every
prerequisite, trigger condition, disqualifier, and soft concern is one of
these, never a bare boolean or a raw unstructured string. The UI converts
these to readable text; nothing here IS the UI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceItem:
    """One prerequisite/trigger-condition check result against a specific
    market snapshot. `passed=False` on a REQUIRED item means the setup
    cannot be TRIGGERED (see engine/playbooks/evaluation.py); `passed=False`
    on an OPTIONAL item only means that piece of corroborating evidence
    wasn't available/favorable — never itself a disqualifier."""
    code: str
    label: str
    observed_value: str | None
    expected_value: str | None
    passed: bool
    source: str  # which Phase 2 module/field this evidence came from
    explanation: str = ""


@dataclass(frozen=True)
class SoftConcern:
    """Reduces quality / becomes a CONDITIONAL soft condition — never a hard
    reject by itself (§9). `severity` is a 0..1 weight, not a probability."""
    code: str
    description: str
    severity: float
    evidence_value: str | None = None


@dataclass(frozen=True)
class Disqualifier:
    """A setup-level hard rejection reason (§8) — deliberately a DIFFERENT
    concept from SoftConcern and from an account/risk-policy blocker (which
    stays exclusively in engine/risk + engine/trade/decision.py; a
    Disqualifier here can NEVER be a risk-policy reason code)."""
    code: str
    description: str
    evidence_value: str | None = None
