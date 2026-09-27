from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from build_guard import verify_build_integrity
from config_guard import fingerprint_config, validate_config
from store import (DATA, StateStoreError, append_hash_chained_event, cycle_lock,
                   read_runtime, validate_runtime, write_json, write_runtime)
from decision_mirror import append_decision_event
from supervisor_relay import relay_health, build_outbox_from_runtime

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).parent / "config.json"
APPROVED_CONFIG_PATH = Path(__file__).parent / "approved_config.sha256"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, severity: str, detail: str, repairable: bool = False) -> dict:
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail,
            "repairable": bool(repairable)}


def _load_approved_config() -> dict:
    cfg = validate_config(json.loads(CONFIG_PATH.read_text()))
    if not APPROVED_CONFIG_PATH.exists():
        raise RuntimeError("approved_config.sha256 missing")
    if fingerprint_config(cfg) != APPROVED_CONFIG_PATH.read_text().strip():
        raise RuntimeError("approved configuration fingerprint mismatch")
    return cfg


def _runtime_integrity(cfg: dict) -> tuple[bool, str, dict | None]:
    try:
        runtime = read_runtime(cfg["initial_equity"])
        validate_runtime(runtime)
        return True, "authoritative runtime snapshot valid", runtime
    except StateStoreError as e:
        return False, str(e), None


def _safe_repairs(cfg: dict, runtime: dict | None) -> List[dict]:
    """Only reversible, non-authoritative repairs. Never repairs trading state by inference."""
    repairs: List[dict] = []
    policy = cfg.get("health", {})
    if not policy.get("safe_auto_repair", False):
        return repairs

    # Remove abandoned temporary files only when old enough. Never delete the authoritative snapshot.
    grace = max(5, int(policy.get("stale_temp_minutes", 60))) * 60
    now = time.time()
    for p in DATA.glob("*.tmp"):
        try:
            age = now - p.stat().st_mtime
            if age >= grace:
                p.unlink()
                repairs.append({"type": "REMOVE_STALE_TEMP", "path": p.name, "age_seconds": round(age, 1)})
        except FileNotFoundError:
            pass

    # Escalations JSON is an observability mirror only; rebuilding it cannot alter trading authority.
    authoritative_snapshot_exists = (DATA / "runtime_snapshot.json").exists()
    if runtime is not None and authoritative_snapshot_exists:
        mirror = DATA / "escalations.json"
        try:
            mirror_ok = mirror.exists() and json.loads(mirror.read_text()) is not None
        except Exception:
            mirror_ok = False
        if not mirror_ok:
            open_items = [x for x in runtime["escalation_queue"] if x.get("status") == "OPEN"]
            write_json("escalations.json", {"required": bool(open_items), "open_items": open_items,
                                             "history": runtime["escalation_queue"], "repaired_at": now_iso()})
            repairs.append({"type": "REBUILD_DERIVED_ESCALATION_MIRROR", "path": "escalations.json"})
    return repairs


def run_health_check(mode: str = "heartbeat", apply_repairs: bool = True, persist: bool = True) -> Dict[str, Any]:
    checks: List[dict] = []
    runtime = None
    cfg = None
    manifest = None
    try:
        manifest = verify_build_integrity()
        checks.append(_check("approved_build_integrity", True, "CRITICAL", manifest["manifest_hash"]))
    except Exception as e:
        checks.append(_check("approved_build_integrity", False, "CRITICAL", str(e)))

    try:
        cfg = _load_approved_config()
        checks.append(_check("approved_config_integrity", True, "CRITICAL", fingerprint_config(cfg)))
    except Exception as e:
        checks.append(_check("approved_config_integrity", False, "CRITICAL", str(e)))

    if cfg is not None:
        ok, detail, runtime = _runtime_integrity(cfg)
        checks.append(_check("authoritative_runtime_integrity", ok, "CRITICAL", detail))

        try:
            usage = shutil.disk_usage(DATA)
            free_mb = usage.free / (1024 * 1024)
            minimum = float(cfg["health"]["disk_free_min_mb"])
            checks.append(_check("runtime_disk_headroom", free_mb >= minimum, "CRITICAL" if free_mb < minimum else "INFO",
                                 f"free_mb={free_mb:.1f}, minimum_mb={minimum:.1f}"))
        except Exception as e:
            checks.append(_check("runtime_disk_headroom", False, "CRITICAL", str(e)))

        if runtime is not None:
            open_items = [x for x in runtime["escalation_queue"] if x.get("status") == "OPEN"]
            checks.append(_check("escalation_queue_valid", True, "INFO", f"open={len(open_items)}"))
            relay = relay_health(runtime, cfg)
            checks.append(_check("supervisor_relay_backlog", relay["passed"], "WARNING" if not relay["passed"] else "INFO",
                                 f"undelivered={relay['undelivered_events']}, limit={relay['max_undelivered_events']}"))
            halted = bool(runtime["portfolio"].get("halted"))
            checks.append(_check("portfolio_not_halted", not halted, "CRITICAL" if halted else "INFO",
                                 runtime["portfolio"].get("halt_reason") or "not halted"))

    repairs: List[dict] = []
    critical_before = [x for x in checks if x["severity"] == "CRITICAL" and not x["passed"]]
    # Never attempt repair of build/config/runtime integrity failures. Those require review.
    if apply_repairs and cfg is not None and not critical_before:
        repairs = _safe_repairs(cfg, runtime)
        if repairs and runtime is not None:
            try:
                with cycle_lock():
                    current = read_runtime(cfg["initial_equity"])
                    for repair in repairs:
                        audit_event = append_hash_chained_event(current["audit_log"], {
                            "ts": now_iso(), "event": "SELF_HEAL_REPAIR", "repair": repair,
                            "policy": "PREAPPROVED_REVERSIBLE_OPERATIONAL_ONLY"
                        })
                        append_decision_event(current, event_kind="SELF_HEAL_REPAIR", subsystem="GUARDIAN",
                                              action="APPLY_PREAPPROVED_REVERSIBLE_REPAIR",
                                              observed_facts={"repair": repair, "policy": "PREAPPROVED_REVERSIBLE_OPERATIONAL_ONLY"},
                                              evidence_refs={"source_event_hash": audit_event.get("event_hash")},
                                              severity="REVIEW", requires_supervisor_review=True,
                                              source_event_hash=audit_event.get("event_hash"))
                    write_runtime(current)
                    if manifest is not None:
                        build_outbox_from_runtime(current, cfg, manifest["manifest_hash"], persist=True)
            except Exception as e:
                checks.append(_check("repair_audit_commit", False, "CRITICAL", str(e)))

    critical = [x for x in checks if x["severity"] == "CRITICAL" and not x["passed"]]
    warnings = [x for x in checks if x["severity"] == "WARNING" and not x["passed"]]
    status = "HALT" if critical else ("DEGRADED" if warnings else "HEALTHY")
    report = {
        "schema_version": 1,
        "project": "Trip's",
        "engine": "Trip's Guardian",
        "generated_at": now_iso(),
        "mode": mode,
        "status": status,
        "health_gate_passed": status != "HALT",
        "trade_authority": "NOT_EVALUATED_BY_GUARDIAN",
        "checks": checks,
        "repairs": repairs,
        "critical_failures": critical,
        "warnings": warnings,
        "policy": {
            "auto_repair_scope": "reversible non-authoritative operational faults only",
            "forbidden": ["strategy edits", "risk-limit changes", "Constitution edits", "symbol expansion",
                          "data-source approval", "state rollback", "live-order enablement"]
        }
    }
    if persist:
        write_json("health_latest.json", report)
        # Routine healthy heartbeats remain telemetry. Any degraded/halt result is a substantive
        # supervisory decision and is mirrored if authoritative runtime state exists.
        if cfg is not None and runtime is not None and status != "HEALTHY":
            try:
                with cycle_lock():
                    current = read_runtime(cfg["initial_equity"])
                    append_decision_event(current, event_kind="GUARDIAN_HEALTH_ALERT", subsystem="GUARDIAN",
                                          action="DEGRADE_OR_HALT_NEW_RISK",
                                          observed_facts={"status": status, "critical_failures": critical, "warnings": warnings},
                                          severity="CRITICAL" if status == "HALT" else "REVIEW",
                                          requires_supervisor_review=True)
                    write_runtime(current)
                    if manifest is not None:
                        build_outbox_from_runtime(current, cfg, manifest["manifest_hash"], persist=True)
            except Exception:
                # Never let a failed observability write turn a health report into a fabricated success.
                pass
    return report


def preflight_health_gate(cfg: dict, runtime: dict) -> dict:
    """Fast in-cycle checks only. Heavy diagnostics run out-of-band."""
    checks = []
    try:
        validate_runtime(runtime)
        checks.append(_check("runtime_schema_and_hash_chains", True, "CRITICAL", "valid"))
    except Exception as e:
        checks.append(_check("runtime_schema_and_hash_chains", False, "CRITICAL", str(e)))
    free_mb = shutil.disk_usage(DATA).free / (1024 * 1024)
    minimum = float(cfg["health"]["disk_free_min_mb"])
    checks.append(_check("disk_headroom", free_mb >= minimum, "CRITICAL", f"free_mb={free_mb:.1f}"))
    passed = all(x["passed"] for x in checks)
    return {"passed": passed, "checks": checks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["heartbeat", "hourly", "daily"], default="heartbeat")
    ap.add_argument("--no-repair", action="store_true")
    args = ap.parse_args()
    report = run_health_check(args.mode, apply_repairs=not args.no_repair)
    print(json.dumps({"status": report["status"], "health_gate_passed": report["health_gate_passed"],
                      "repairs": len(report["repairs"]), "critical": len(report["critical_failures"])}, indent=2))
    raise SystemExit(2 if report["status"] == "HALT" else 0)


if __name__ == "__main__":
    main()
