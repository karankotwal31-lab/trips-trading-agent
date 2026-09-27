from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from build_guard import verify_build_integrity
from config_guard import fingerprint_config, validate_config
from health_engine import run_health_check
from evolution_engine import run_evolution_review
from store import DATA, cycle_lock, read_json, read_runtime, write_json, write_runtime
from decision_mirror import append_decision_event
from supervisor_relay import build_outbox_from_runtime, relay_health

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
APPROVED_CONFIG_PATH = HERE / "approved_config.sha256"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def cfg():
    c = validate_config(json.loads(CONFIG_PATH.read_text()))
    if fingerprint_config(c) != APPROVED_CONFIG_PATH.read_text().strip():
        raise RuntimeError("approved config mismatch")
    return c


def build_supervisor_packet() -> dict:
    c = cfg()
    health = run_health_check("daily", apply_repairs=True, persist=True)
    evolution = run_evolution_review()
    runtime = read_runtime(c["initial_equity"])
    open_escalations = [x for x in runtime["escalation_queue"] if x.get("status") == "OPEN"]
    relay = relay_health(runtime, c)
    decision_journal = runtime.get("decision_journal", [])
    recent_decisions = [{k: e.get(k) for k in ("ts","seq","event_id","event_kind","subsystem","symbol","severity","action","requires_supervisor_review","event_hash")}
                        for e in decision_journal[-25:]]
    state_mirror = read_json("state.json", {}, strict=False) or {}
    backtest = read_json("backtest.json", {}, strict=False) or {}
    deep = read_json("deep_diagnostics_latest.json", {}, strict=False) or {}
    security = deep.get("security", {}) if isinstance(deep, dict) else {}
    capability = json.loads((HERE / "capability_registry.json").read_text())
    backtest_summary = {k: backtest.get(k) for k in ("notice", "all_causal", "all_small_sample", "worst_net_pnl", "best_net_pnl", "worst_drawdown_pct") if k in backtest}
    packet = {
        "schema_version": 1,
        "project": "Trip's",
        "generated_at": now_iso(),
        "recipient_role": "ChatGPT supervisor + user",
        "plain_language_priority": True,
        "facts": {
            "health_status": health["status"],
            "health_gate_passed": health["health_gate_passed"],
            "trade_authority": "DETERMINED_ONLY_BY_DATA_TRUTH + FORGE_GATE + CONSTITUTION",
            "open_escalations": len(open_escalations),
            "paper_only": c["mode"] == "paper",
            "approved_build_hash": verify_build_integrity()["manifest_hash"],
            "approved_config_hash": fingerprint_config(c),
            "cycle_count": runtime["portfolio"].get("cycle_count", 0),
            "positions": len(runtime["portfolio"].get("positions", {})),
            "pending_entries": len(runtime["portfolio"].get("pending_entries", {})),
            "decision_events": len(decision_journal),
            "supervisor_undelivered_events": relay["undelivered_events"],
        },
        "health": health,
        "supervisor_relay": relay,
        "recent_decisions": recent_decisions,
        "open_escalations": open_escalations,
        "evolution": evolution,
        "deep_diagnostics_summary": {
            "passed": deep.get("passed"), "action": deep.get("action"),
            "chaos_passed": (deep.get("chaos") or {}).get("passed"),
            "security_passed": security.get("passed"),
        },
        "backtest_summary": backtest_summary,
        "capability_registry": capability,
        "latest_market_summary": state_mirror.get("market", {}),
        "research_requests": [
            "Verify current market-data provider terms/entitlements before treating any feed as real-time.",
            "Verify material regulatory/exchange-session changes before changing runtime assumptions.",
            "For any market escalation, independently consult current real data before forming a view."
        ],
        "consultation_contract": [
            "Supervisor must independently verify current external market facts before forming a market view.",
            "Supervisor must distinguish observed facts, inference, uncertainty and user policy decisions.",
            "Supervisor may recommend; it cannot bypass Trip's Constitution or deterministic risk gates.",
            "Any material strategy/risk/model change is explained in plain language to the user before approval.",
            "If current facts cannot be verified, the guidance is NO_CHANGE/NO_TRADE rather than invention."
        ],
        "requires_immediate_attention": health["status"] == "HALT" or bool(open_escalations) or not relay["passed"],
        "plain_language_summary": (
            f"Guardian={health['status']}; paper-only={c['mode']=='paper'}; open escalations={len(open_escalations)}; "
            f"Evolution applied no champion changes; strategy evidence remains "
            f"{'unproven' if backtest.get('all_small_sample') else 'under review'}."
        ),
    }
    # Record packet generation as a substantive supervisory action. A failure here is surfaced in
    # the packet itself; it is never silently treated as a successful supervisor handoff.
    try:
        with cycle_lock():
            current = read_runtime(c["initial_equity"])
            append_decision_event(current, event_kind="SUPERVISOR_PACKET_BUILT", subsystem="SUPERVISOR_BRIDGE",
                                  action="PREPARE_DAILY_EVIDENCE_FOR_CHATGPT_AND_USER",
                                  observed_facts={"health_status": health["status"],
                                                  "open_escalations": len(open_escalations),
                                                  "relay_health_at_snapshot": relay,
                                                  "packet_generated_at": packet["generated_at"]},
                                  severity="REVIEW" if packet["requires_immediate_attention"] else "INFO",
                                  requires_supervisor_review=True)
            write_runtime(current)
            outbox = build_outbox_from_runtime(current, c, packet["facts"]["approved_build_hash"], persist=True)
            packet["self_record_status"] = "RECORDED"
            packet["post_record_relay"] = outbox["relay_health"]
            packet["facts"]["decision_events_after_self_record"] = len(current.get("decision_journal", []))
            packet["facts"]["supervisor_undelivered_events_after_self_record"] = outbox["relay_health"]["undelivered_events"]
    except Exception as exc:
        packet["self_record_status"] = "FAILED"
        packet["self_record_error_type"] = type(exc).__name__
        packet["requires_immediate_attention"] = True
    write_json("supervisor_packet.json", packet)
    return packet


def main():
    argparse.ArgumentParser().parse_args()
    p = build_supervisor_packet()
    print(json.dumps({"generated_at": p["generated_at"], "health": p["facts"]["health_status"],
                      "open_escalations": p["facts"]["open_escalations"],
                      "requires_immediate_attention": p["requires_immediate_attention"]}, indent=2))


if __name__ == "__main__":
    main()
