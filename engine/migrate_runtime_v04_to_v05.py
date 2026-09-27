from __future__ import annotations

import json
from pathlib import Path

from store import DATA, StateStoreError, read_json, write_runtime


def migrate() -> dict:
    path = DATA / "runtime_snapshot.json"
    if not path.exists():
        return {"migrated": False, "reason": "no_runtime_snapshot"}
    old = read_json("runtime_snapshot.json", None, strict=True)
    if not isinstance(old, dict):
        raise StateStoreError("runtime snapshot unreadable")
    if old.get("runtime_schema_version") == 2:
        return {"migrated": False, "reason": "already_v0.5_schema"}
    if old.get("runtime_schema_version") != 1:
        raise StateStoreError("unsupported source runtime schema")
    required = {"portfolio", "ledger", "audit_log", "escalation_queue"}
    if not required <= set(old):
        raise StateStoreError("v0.4 runtime missing required fields")
    upgraded = dict(old)
    upgraded["runtime_schema_version"] = 2
    upgraded["decision_journal"] = []
    upgraded["supervisor_delivery"] = {"last_ack_seq": 0, "last_ack_event_hash": None, "last_ack_at": None, "last_response_hash": None}
    # Preserve all old trading state exactly. No reconstructed trade history is invented.
    write_runtime(upgraded)
    return {"migrated": True, "source_schema": 1, "target_schema": 2,
            "note": "prior audit/ledger preserved; v0.5 decision journal starts empty at migration boundary"}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))
