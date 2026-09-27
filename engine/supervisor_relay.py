from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from build_guard import verify_build_integrity
from config_guard import fingerprint_config, validate_config
from store import DATA, StateStoreError, append_hash_chained_event, cycle_lock, read_runtime, write_json, write_runtime

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
APPROVED_CONFIG_PATH = HERE / "approved_config.sha256"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg() -> dict:
    cfg = validate_config(json.loads(CONFIG_PATH.read_text()))
    if fingerprint_config(cfg) != APPROVED_CONFIG_PATH.read_text().strip():
        raise RuntimeError("approved configuration fingerprint mismatch")
    return cfg


def _packet(event: dict, manifest_hash: str, config_hash: str) -> dict:
    # Packet identity must be deterministic so a relay retry cannot masquerade as new evidence.
    stable = {
        "schema_version": 1,
        "project": "Trip's",
        "recipient_role": "ChatGPT supervisor",
        "decision_seq": event.get("seq"),
        "decision_event_id": event.get("event_id"),
        "decision_event_hash": event.get("event_hash"),
        "event": event,
        "approved_build_hash": manifest_hash,
        "approved_config_hash": config_hash,
        "supervisor_contract": {
            "independently_verify_current_market_facts": True,
            "facts_must_be_separated_from_inference": True,
            "missing_or_unverifiable_facts": "UNVERIFIED_NO_CHANGE",
            "advice_is_advisory_only": True,
            "cannot_submit_or_authorize_an_order": True,
            "cannot_bypass_forge_or_constitution": True,
            "material_strategy_risk_or_policy_change_requires_user_approval": True,
            "delivery_ack_requires_hmac_receipt": True,
            "deduplicate_by_packet_hash": True,
        },
    }
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    packet_hash = hashlib.sha256(raw).hexdigest()
    body = dict(stable)
    body["packet_id"] = packet_hash[:20]
    body["packet_hash"] = packet_hash
    body["prepared_at"] = now_iso()
    return body


def relay_health(runtime: dict, cfg: dict) -> dict:
    journal = runtime.get("decision_journal", [])
    delivery = runtime.get("supervisor_delivery", {})
    ack = int(delivery.get("last_ack_seq", 0))
    last_seq = int(journal[-1].get("seq", 0)) if journal else 0
    backlog = max(0, last_seq - ack)
    limit = int(cfg["supervisor"]["max_undelivered_events"])
    passed = backlog <= limit
    return {
        "passed": passed,
        "last_decision_seq": last_seq,
        "last_ack_seq": ack,
        "undelivered_events": backlog,
        "max_undelivered_events": limit,
        "action": "OK" if passed else "BLOCK_NEW_ENTRIES_AND_ESCALATE",
    }


def build_outbox_from_runtime(runtime: dict, cfg: dict, manifest_hash: str, *, persist: bool = True) -> dict:
    health = relay_health(runtime, cfg)
    ack = health["last_ack_seq"]
    pending = [e for e in runtime.get("decision_journal", []) if int(e.get("seq", 0)) > ack]
    # Never silently truncate evidence. If the backlog is over policy, write every event and let the health gate stop new entries.
    packets = [_packet(e, manifest_hash, fingerprint_config(cfg)) for e in pending]
    out = {
        "schema_version": 1,
        "project": "Trip's",
        "transport_state": "OUTBOX_READY_NOT_DELIVERED",
        "transport_note": "This build prepares every decision for the ChatGPT supervisor. A durable hosted bridge is still required to transmit/acknowledge packets automatically.",
        "generated_at": now_iso(),
        "relay_health": health,
        "packets": packets,
    }
    if persist:
        write_json("supervisor_outbox.json", out)
    return out


def build_supervisor_outbox(*, persist: bool = True) -> dict:
    cfg = _cfg()
    verify = verify_build_integrity()
    with cycle_lock():
        runtime = read_runtime(cfg["initial_equity"])
        return build_outbox_from_runtime(runtime, cfg, verify["manifest_hash"], persist=persist)


ACK_KEY_ENV = "TRIPS_SUPERVISOR_ACK_KEY"


def acknowledgement_message(through_seq: int, through_event_hash: str, response_hash: str) -> bytes:
    return f"Trip's|supervisor-ack|{int(through_seq)}|{str(through_event_hash)}|{str(response_hash)}".encode("utf-8")


def compute_ack_signature(through_seq: int, through_event_hash: str, response_hash: str, key: str) -> str:
    if not isinstance(key, str) or len(key) < 32:
        raise StateStoreError("supervisor acknowledgement key must be at least 32 characters")
    return hmac.new(key.encode("utf-8"), acknowledgement_message(through_seq, through_event_hash, response_hash), hashlib.sha256).hexdigest()


def _verify_ack_signature(through_seq: int, through_event_hash: str, response_hash: str, receipt_signature: str) -> None:
    key = os.environ.get(ACK_KEY_ENV, "")
    if len(key) < 32:
        raise StateStoreError("signed supervisor acknowledgement unavailable: TRIPS_SUPERVISOR_ACK_KEY is not configured")
    expected = compute_ack_signature(through_seq, through_event_hash, response_hash, key)
    if not isinstance(receipt_signature, str) or not hmac.compare_digest(expected, receipt_signature):
        raise StateStoreError("invalid supervisor acknowledgement signature")


def acknowledge_supervisor_delivery(through_seq: int, through_event_hash: str, response_hash: str = "", receipt_signature: str = "") -> dict:
    cfg = _cfg()
    if through_seq < 0:
        raise StateStoreError("through_seq must be non-negative")
    with cycle_lock():
        runtime = read_runtime(cfg["initial_equity"])
        journal = runtime.get("decision_journal", [])
        last_seq = int(journal[-1].get("seq", 0)) if journal else 0
        if through_seq > last_seq:
            raise StateStoreError("cannot acknowledge a decision sequence that does not exist")
        expected_event_hash = "GENESIS" if through_seq == 0 else journal[through_seq - 1].get("event_hash")
        if through_event_hash != expected_event_hash:
            raise StateStoreError("supervisor acknowledgement does not bind to the authoritative event hash")
        _verify_ack_signature(through_seq, through_event_hash, response_hash, receipt_signature)
        delivery = runtime.setdefault("supervisor_delivery", {"last_ack_seq": 0, "last_ack_event_hash": None, "last_ack_at": None, "last_response_hash": None})
        if through_seq < int(delivery.get("last_ack_seq", 0)):
            raise StateStoreError("supervisor acknowledgement cannot move backwards")
        delivery["last_ack_seq"] = int(through_seq)
        delivery["last_ack_event_hash"] = through_event_hash if through_seq else None
        delivery["last_ack_at"] = now_iso()
        delivery["last_response_hash"] = str(response_hash)[:256] or None
        append_hash_chained_event(runtime["audit_log"], {
            "ts": now_iso(), "event": "SUPERVISOR_DELIVERY_ACK", "through_seq": through_seq,
            "response_hash": delivery["last_response_hash"],
        })
        write_runtime(runtime)
    return build_supervisor_outbox(persist=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ack-through", type=int, default=None)
    ap.add_argument("--through-event-hash", default="")
    ap.add_argument("--response-hash", default="")
    ap.add_argument("--receipt-signature", default="")
    args = ap.parse_args()
    if args.ack_through is not None:
        out = acknowledge_supervisor_delivery(args.ack_through, args.through_event_hash, args.response_hash, args.receipt_signature)
    else:
        out = build_supervisor_outbox(persist=True)
    print(json.dumps({"transport_state": out["transport_state"], "pending_packets": len(out["packets"]),
                      "relay_health": out["relay_health"]}, indent=2))


if __name__ == "__main__":
    main()
