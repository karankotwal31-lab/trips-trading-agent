from __future__ import annotations

from typing import Dict, List


NON_NEGOTIABLES = [
    ("TRUTH_OVER_ACTION", "No sourced data, no market claim, no trade."),
    ("FAIL_CLOSED", "Missing, stale, malformed, contradictory or unverifiable inputs force NO_TRADE or HALT."),
    ("EVIDENCE_BEFORE_INFERENCE", "Observed data/provenance are stored separately from interpretation."),
    ("NO_FAKE_CERTAINTY", "Heuristic scores are never represented as calibrated probabilities or guarantees."),
    ("RISK_BEFORE_RETURN", "Hard risk limits run before return-seeking logic and cannot be overridden by AI."),
    ("NO_MARTINGALE", "No averaging down, loss chasing, or size escalation because prior trades lost."),
    ("INDEPENDENT_VERIFICATION", "Current-market trade eligibility requires independent source confirmation when configured."),
    ("CONSERVATIVE_SIMULATION", "Ambiguous fills are resolved against the strategy, never in its favor."),
    ("AUDIT_EVERYTHING", "Inputs, hashes, proposals, rejections, configuration and escalations are auditable."),
    ("SEPARATE_ANALYSIS_FROM_EXECUTION", "AI interprets and challenges; deterministic gates own execution authority."),
    ("NO_SILENT_MODEL_DRIFT", "Strategy/risk changes require versioning, tests, review and a recorded configuration fingerprint."),
    ("ESCALATE_UNKNOWN_UNKNOWNS", "Novel/conflicting conditions are escalated instead of guessed."),
    ("PAPER_FIRST", "This build cannot submit live-money orders."),
    ("CLOSED_BARS_ONLY", "Signals may use only fully closed bars; incomplete candles cannot authorize entries."),
    ("CAUSAL_EXECUTION", "A signal formed at a bar close cannot be filled retroactively at that same close."),
    ("STATE_INTEGRITY", "Corrupt or inconsistent critical state fails closed; it is never silently reset."),
    ("RISK_DATA_SEPARATION", "Observed market facts and simulation assumptions such as spread/slippage are explicitly separated."),
    ("INSTRUMENT_SCOPE_LOCK", "This build may operate only on the explicitly validated instrument class and bar interval."),
    ("ATOMIC_STATE_COMMIT", "Authoritative portfolio, ledger, audit and escalation state commit as one integrity-checked snapshot; partial mirrors are never authoritative."),
    ("EXECUTABLE_BUILD_LOCK", "Safety-critical executable code must match the reviewed build manifest before any cycle may run."),
    ("DURABLE_ESCALATION", "An unresolved escalation remains open until explicitly acknowledged; a later quiet cycle cannot erase it."),
    ("VALIDATED_SYMBOL_SCOPE", "This release can analyze/execute only the explicitly validated symbol allowlist; widening it requires code review and hard diagnostics."),
    ("HEALTH_BEFORE_TRADING", "A critical health failure blocks new trading activity until the condition is resolved and verified."),
    ("SELF_HEALING_BOUNDARY", "Automatic repair is limited to pre-approved reversible operational faults; it cannot rewrite strategy, risk, truth, execution, symbol scope, or constitutional policy."),
    ("NO_AUTONOMOUS_POLICY_MUTATION", "No autonomous process may loosen risk limits, change the Constitution, approve a new data source/symbol, or promote trading logic."),
    ("EVOLUTION_IN_QUARANTINE", "Adaptive research runs as a challenger in shadow/replay mode; promotion requires independent evidence, tests, explicit human approval, and a new approved build/config fingerprint."),
    ("SUPERVISOR_EVIDENCE_PACKET", "Escalations and daily supervisor updates must contain provenance, timestamps, hashes, observed facts, inference, uncertainty, health status, and exact requested decision."),
    ("RECOVERY_WITHOUT_ROLLBACK", "Self-healing must never restore an older trading state merely to make the system run; uncertain recovery halts and escalates."),
    ("DECISION_MIRROR", "Every substantive analysis, rejection, order-state change, trade, repair, escalation and evolution proposal is mirrored into an integrity-checked supervisor-visible journal."),
    ("FACT_INFERENCE_SEPARATION", "Supervisor evidence must store observed facts separately from interpretation; missing facts are never filled by inference."),
    ("SUPERVISOR_ADVISORY_ONLY", "Supervisor guidance has no order authority and cannot bypass data truth, Forge Gate, risk controls, execution policy or the Constitution."),
    ("SUPERVISOR_EXTERNAL_VERIFICATION", "Any supervisor guidance using current market facts must independently verify those facts and preserve source/time provenance."),
    ("RELAY_BACKPRESSURE", "If supervisor evidence cannot be delivered/acknowledged within the approved backlog bound, Trip's blocks new entries rather than operating invisibly."),
    ("AUTHENTICATED_SUPERVISOR_ACK", "Trip's never marks supervisor evidence delivered without a cryptographically authenticated receipt from the trusted relay."),
    ("OPERATOR_SURFACE_TRUTH", "Operator dashboards are read-only, integrity-verified, staleness-labelled, and may never upgrade health, queued evidence or synthetic analysis into trade authority."),
]


def constitution_gate(*, truth: dict, mode: str, gate_passed: bool, model_conflict: bool,
                      anomaly: bool, requires_verified_trade_data: bool = True) -> Dict[str, object]:
    checks: List[dict] = []
    checks.append({"name": "paper_first", "passed": mode == "paper", "detail": "mode must remain paper"})
    checks.append({"name": "truth_for_analysis", "passed": bool(truth.get("trusted_for_analysis")), "detail": "data must pass truth validation"})
    if requires_verified_trade_data:
        checks.append({"name": "truth_for_trade", "passed": bool(truth.get("trusted_for_trade")), "detail": "current-market paper execution requires verified real-data eligibility"})
    checks.append({"name": "risk_gate", "passed": bool(gate_passed), "detail": "deterministic Trip's risk gate must pass"})
    checks.append({"name": "model_conflict", "passed": not model_conflict, "detail": "conflicting models cannot force a trade"})
    checks.append({"name": "data_anomaly", "passed": not anomaly, "detail": "unresolved data anomaly forces no-trade"})
    return {"passed": all(x["passed"] for x in checks), "checks": checks, "rules": [r[0] for r in NON_NEGOTIABLES]}
