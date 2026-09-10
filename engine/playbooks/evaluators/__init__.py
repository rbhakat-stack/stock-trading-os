"""One module per playbook family. Every evaluate_*() function has the
signature (snapshot, current_price, min_rr) -> PlaybookEvaluation and never
raises for a well-formed MarketIntelligenceSnapshot — see engine/playbooks/
engine.py for how these are invoked.
"""
