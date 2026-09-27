from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from build_guard import verify_build_integrity
from config_guard import fingerprint_config, validate_config
from health_engine import run_health_check
from store import DATA, cycle_lock, read_json, read_runtime, write_json, write_runtime
from decision_mirror import append_decision_event
from supervisor_relay import build_outbox_from_runtime

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).parent / "config.json"
APPROVED_CONFIG_PATH = Path(__file__).parent / "approved_config.sha256"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg() -> dict:
    cfg = validate_config(json.loads(CONFIG_PATH.read_text()))
    if fingerprint_config(cfg) != APPROVED_CONFIG_PATH.read_text().strip():
        raise RuntimeError("approved configuration fingerprint mismatch")
    return cfg


def _proposal(kind: str, observation: str, hypothesis: str, evidence_required: List[str],
              forbidden_auto_actions: List[str]) -> dict:
    seed = f"{kind}|{observation}|{hypothesis}"
    return {
        "id": hashlib.sha256(seed.encode()).hexdigest()[:12],
        "kind": kind,
        "status": "QUARANTINED_CHALLENGER",
        "observation": observation,
        "hypothesis": hypothesis,
        "evidence_required": evidence_required,
        "forbidden_auto_actions": forbidden_auto_actions,
    }


def run_evolution_review() -> Dict[str, Any]:
    cfg = _cfg()
    verify_build_integrity()
    health = run_health_check("daily", apply_repairs=False, persist=True)
    backtest = read_json("backtest.json", {}, strict=False) or {}
    proposals: List[dict] = []

    if health["status"] != "HEALTHY":
        proposals.append(_proposal(
            "RELIABILITY_FIRST",
            f"Guardian status={health['status']}",
            "Evolution must freeze while critical operational health is unresolved.",
            ["Guardian returns HEALTHY", "root cause documented", "regression test added"],
            ["strategy tuning", "risk loosening", "promotion"]
        ))

    if backtest:
        all_small = bool(backtest.get("all_small_sample", False))
        worst = backtest.get("worst_net_pnl")
        if all_small or (isinstance(worst, (int, float)) and worst < 0):
            proposals.append(_proposal(
                "STRATEGY_EVIDENCE_GAP",
                f"synthetic robustness remains insufficient; all_small_sample={all_small}, worst_net_pnl={worst}",
                "Collect genuine historical multi-regime data and test challenger ideas without changing the champion.",
                [f">={cfg['evolution']['min_candidate_trades']} closed challenger trades",
                 f">={cfg['evolution']['min_walk_forward_windows']} walk-forward windows",
                 "untouched out-of-sample period", "transaction-cost stress", "regime-separated results"],
                ["auto-changing thresholds", "auto-promoting a challenger", "editing hard risk ceilings"]
            ))

    if not proposals:
        proposals.append(_proposal(
            "NO_CHANGE",
            "No evidence currently justifies a model or policy change.",
            "Keep the approved champion unchanged and continue evidence collection.",
            ["new statistically meaningful evidence before any change"],
            ["change for novelty", "parameter drift"]
        ))

    report = {
        "schema_version": 1,
        "project": "Trip's",
        "engine": "Trip's Evolution Lab",
        "generated_at": now_iso(),
        "mode": "SHADOW_RESEARCH_ONLY",
        "health_status": health["status"],
        "approved_build_hash": verify_build_integrity()["manifest_hash"],
        "approved_config_hash": fingerprint_config(cfg),
        "champion_mutated": False,
        "auto_promotion_allowed": False,
        "proposals": proposals,
        "promotion_gate": {
            "requires_human_approval": True,
            "requires_new_approved_build_manifest": True,
            "requires_new_approved_config_fingerprint_if_config_changes": True,
            "requires_out_of_sample": bool(cfg["evolution"]["require_out_of_sample"]),
            "requires_shadow_mode": True,
        }
    }
    write_json("evolution_latest.json", report)
    # Evolution is advisory/shadow-only, but every proposal is a substantive decision artifact.
    if (DATA / "runtime_snapshot.json").exists():
        try:
            with cycle_lock():
                runtime = read_runtime(cfg["initial_equity"])
                append_decision_event(runtime, event_kind="EVOLUTION_REVIEW", subsystem="EVOLUTION_LAB",
                                      action="PROPOSE_CHALLENGERS_ONLY",
                                      observed_facts={"health_status": health["status"], "proposals": proposals,
                                                      "champion_mutated": False, "auto_promotion_allowed": False},
                                      inference={"proposal_hypotheses": [p.get("hypothesis") for p in proposals]},
                                      severity="REVIEW", requires_supervisor_review=True)
                write_runtime(runtime)
                build_outbox_from_runtime(runtime, cfg, report["approved_build_hash"], persist=True)
        except Exception:
            # The evolution report remains shadow-only; an observability failure must never mutate the champion.
            pass
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    r = run_evolution_review()
    print(json.dumps({"engine": r["engine"], "proposals": len(r["proposals"]),
                      "champion_mutated": r["champion_mutated"], "auto_promotion_allowed": r["auto_promotion_allowed"]}, indent=2))


if __name__ == "__main__":
    main()
