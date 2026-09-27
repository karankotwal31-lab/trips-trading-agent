from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config_guard import fingerprint_config, validate_config
from decision_mirror import append_decision_event
from store import append_hash_chained_event, cycle_lock, read_runtime, write_runtime
from supervisor_relay import build_outbox_from_runtime
from build_guard import verify_build_integrity

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
APPROVED_CONFIG_PATH = HERE / "approved_config.sha256"

# Exact v0.5 configuration fingerprint from the reviewed predecessor release.
LEGACY_V05_CONFIG_HASH = "46051163e8f93ee464c16d3897e7fb72421369231143a40653c690dbf3c5e5f7"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_migration_preconditions(runtime: dict, current_hash: str) -> None:
    portfolio = runtime["portfolio"]
    old_hash = portfolio.get("config_hash")
    if old_hash == current_hash:
        raise RuntimeError("runtime already uses the current approved configuration")
    if old_hash != LEGACY_V05_CONFIG_HASH:
        raise RuntimeError("runtime configuration fingerprint is not the reviewed v0.5 predecessor")
    if portfolio.get("positions"):
        raise RuntimeError("migration refused while paper positions are open")
    if portfolio.get("pending_entries"):
        raise RuntimeError("migration refused while paper entries are pending")
    if portfolio.get("halted"):
        raise RuntimeError("migration refused while runtime is halted; resolve the halt first")


def migrate() -> dict:
    cfg = validate_config(json.loads(CONFIG_PATH.read_text()))
    current_hash = fingerprint_config(cfg)
    if APPROVED_CONFIG_PATH.read_text().strip() != current_hash:
        raise RuntimeError("current configuration is not approved")
    manifest = verify_build_integrity()
    with cycle_lock():
        runtime = read_runtime(float(cfg["initial_equity"]))
        validate_migration_preconditions(runtime, current_hash)
        previous_hash = runtime["portfolio"].get("config_hash")
        runtime["portfolio"]["config_hash"] = current_hash
        ts = now_iso()
        append_hash_chained_event(runtime["audit_log"], {
            "ts": ts,
            "event": "REVIEWED_RUNTIME_MIGRATION_V05_TO_V06",
            "from_config_hash": previous_hash,
            "to_config_hash": current_hash,
            "scope": "observability/dashboard release; no position/pending state present",
        })
        append_decision_event(
            runtime,
            event_kind="RUNTIME_MIGRATION",
            subsystem="RELEASE_MIGRATION",
            action="ADOPT_REVIEWED_V06_CONFIG_FINGERPRINT",
            observed_facts={
                "from_config_hash": previous_hash,
                "to_config_hash": current_hash,
                "open_positions": 0,
                "pending_entries": 0,
                "approved_build_hash": manifest["manifest_hash"],
            },
            severity="REVIEW",
            requires_supervisor_review=True,
        )
        write_runtime(runtime)
        outbox = build_outbox_from_runtime(runtime, cfg, manifest["manifest_hash"], persist=True)
    return {"migrated": True, "from": previous_hash, "to": current_hash,
            "supervisor_undelivered": outbox["relay_health"]["undelivered_events"]}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))
