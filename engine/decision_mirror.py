from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from store import DATA, append_hash_chained_event, cycle_lock, read_runtime, write_runtime
from redaction import sanitize


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_copy(value: Any) -> Any:
    """Redact credentials, JSON round-trip, reject NaN/Infinity, and remove object aliases."""
    sanitized = sanitize(value)
    raw = json.dumps(sanitized, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return json.loads(raw)


def _event_id(seq: int, ts: str, event_kind: str, symbol: Optional[str], action: str) -> str:
    seed = f"{seq}|{ts}|{event_kind}|{symbol or ''}|{action}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def append_decision_event(
    runtime: dict,
    *,
    event_kind: str,
    subsystem: str,
    action: str,
    symbol: Optional[str] = None,
    observed_facts: Optional[dict] = None,
    inference: Optional[dict] = None,
    evidence_refs: Optional[dict] = None,
    guardrails: Optional[Iterable[dict | str]] = None,
    uncertainty: str = "",
    severity: str = "INFO",
    requires_supervisor_review: bool = False,
    source_event_hash: Optional[str] = None,
) -> dict:
    """Append one authoritative supervisor-visible decision event.

    Facts and inference are deliberately separated. This function never permits an
    inference to be silently copied into the observed_facts field by the caller.
    """
    journal = runtime.setdefault("decision_journal", [])
    seq = int(journal[-1].get("seq", 0)) + 1 if journal else 1
    ts = now_iso()
    event = {
        "ts": ts,
        "seq": seq,
        "event_id": _event_id(seq, ts, event_kind, symbol, action),
        "event_kind": str(event_kind),
        "subsystem": str(subsystem),
        "symbol": symbol,
        "severity": severity,
        "observed_facts": _safe_copy(observed_facts or {}),
        "inference": _safe_copy(inference or {}),
        "evidence_refs": _safe_copy(evidence_refs or {}),
        "guardrails": _safe_copy(list(guardrails or [])),
        "uncertainty": str(uncertainty)[:4000],
        "action": str(action),
        "requires_supervisor_review": bool(requires_supervisor_review),
        "source_event_hash": source_event_hash,
        "truth_contract": {
            "facts_separate_from_inference": True,
            "missing_evidence_must_not_be_invented": True,
            "current_market_claims_require_current_external_verification": True,
            "supervisor_advice_cannot_bypass_deterministic_gates": True,
        },
    }
    return append_hash_chained_event(journal, event)


def mirror_trade_cycle(
    runtime: dict,
    *,
    cycle_number: int,
    health_preflight: dict,
    proposals: list,
    cycle_audit: list,
    new_ledger_events: list,
    touched_escalations: list,
    escalation_items: list,
    portfolio: dict,
    metrics: dict,
) -> list[dict]:
    """Mirror every substantive trade-cycle decision/action into one hash chain."""
    created = []
    created.append(append_decision_event(
        runtime,
        event_kind="CYCLE_START",
        subsystem="TRADING_ENGINE",
        action="BEGIN_PAPER_CYCLE",
        observed_facts={"cycle_number": cycle_number, "health_preflight": health_preflight},
        guardrails=health_preflight.get("checks", []),
        severity="INFO",
    ))

    for proposal in proposals:
        symbol = proposal.get("symbol")
        constitution = proposal.get("constitution_gate") or {}
        forge_gate = proposal.get("forge_gate") or {}
        created.append(append_decision_event(
            runtime,
            event_kind="MARKET_DECISION",
            subsystem="ANALYSIS_AND_FORGE_GATE",
            action=("ELIGIBLE_FOR_QUEUE" if constitution.get("passed") and proposal.get("direction") == "LONG" else "REJECT_OR_NO_TRADE"),
            symbol=symbol,
            observed_facts={
                "signal_bar_ts": proposal.get("signal_bar_ts"),
                "data_integrity_hash": proposal.get("data_integrity_hash"),
                "direction": proposal.get("direction"),
                "signal_score": proposal.get("signal_score"),
                "strategy": proposal.get("strategy"),
                "candle_read": proposal.get("candle_read"),
            },
            inference={
                "strategy_interpretation": proposal.get("strategy"),
                "candle_interpretation": proposal.get("candle_read"),
            },
            evidence_refs={
                "data_integrity_hash": proposal.get("data_integrity_hash"),
                "signal_bar_ts": proposal.get("signal_bar_ts"),
            },
            guardrails=(forge_gate.get("checks", []) + constitution.get("checks", [])),
            uncertainty="Signal score is a heuristic score, not a calibrated probability.",
            severity="REVIEW" if not constitution.get("passed") else "INFO",
            requires_supervisor_review=bool((proposal.get("forge_gate") or {}).get("conflict")),
        ))

    for event in cycle_audit:
        e = dict(event)
        kind = str(e.get("event", "AUDIT_EVENT"))
        symbol = e.get("symbol")
        created.append(append_decision_event(
            runtime,
            event_kind=kind,
            subsystem="TRADING_ENGINE",
            action=kind,
            symbol=symbol,
            observed_facts=e,
            evidence_refs={"source_event_hash": e.get("event_hash")},
            severity="REVIEW" if kind in {"DATA_TRUTH_REJECT", "DATA_ERROR", "INTERNAL_ERROR", "PENDING_REJECT"} else "INFO",
            requires_supervisor_review=kind in {"DATA_ERROR", "INTERNAL_ERROR"},
            source_event_hash=e.get("event_hash"),
        ))

    for event in new_ledger_events:
        e = dict(event)
        kind = str(e.get("type", "LEDGER_EVENT"))
        symbol = e.get("symbol")
        is_trade = kind in {"PAPER_ORDER_QUEUED", "PAPER_ENTRY", "PAPER_EXIT"}
        created.append(append_decision_event(
            runtime,
            event_kind=kind,
            subsystem="EXECUTION_SIMULATOR",
            action=kind,
            symbol=symbol,
            observed_facts=e,
            evidence_refs={
                "source_event_hash": e.get("event_hash"),
                "data_integrity_hash": e.get("data_integrity_hash"),
                "market_bar_ts": e.get("market_bar_ts"),
            },
            uncertainty="Paper execution only; fill prices include configured simulation assumptions, not broker confirmations.",
            severity="TRADE" if is_trade else "INFO",
            requires_supervisor_review=is_trade,
            source_event_hash=e.get("event_hash"),
        ))

    if touched_escalations:
        created.append(append_decision_event(
            runtime,
            event_kind="ESCALATION_STATE_CHANGED",
            subsystem="ESCALATION_ENGINE",
            action="ESCALATE_FOR_REVIEW",
            observed_facts={"touched_escalation_ids": list(touched_escalations),
                            "escalations": _safe_copy(escalation_items)},
            severity="CRITICAL",
            requires_supervisor_review=True,
        ))

    created.append(append_decision_event(
        runtime,
        event_kind="CYCLE_END",
        subsystem="TRADING_ENGINE",
        action="COMMIT_PAPER_CYCLE",
        observed_facts={"cycle_number": cycle_number, "portfolio": portfolio, "metrics": metrics},
        severity="INFO",
    ))
    return created


def record_initialized_runtime_event(
    *,
    initial_equity: float,
    event_kind: str,
    subsystem: str,
    action: str,
    observed_facts: dict,
    inference: Optional[dict] = None,
    severity: str = "INFO",
    requires_supervisor_review: bool = False,
) -> Optional[dict]:
    """Record Guardian/Evolution events only after an authoritative runtime exists.

    Health checks on a brand-new installation must not create evidence of prior trading state.
    """
    if not (DATA / "runtime_snapshot.json").exists():
        return None
    with cycle_lock():
        runtime = read_runtime(initial_equity)
        event = append_decision_event(
            runtime,
            event_kind=event_kind,
            subsystem=subsystem,
            action=action,
            observed_facts=observed_facts,
            inference=inference,
            severity=severity,
            requires_supervisor_review=requires_supervisor_review,
        )
        write_runtime(runtime)
        return event
