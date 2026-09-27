from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "runtime_data"
DATA.mkdir(parents=True, exist_ok=True)


class StateStoreError(RuntimeError):
    pass


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _checksum(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def read_json(name: str, default: Any, *, strict: bool = False):
    path = DATA / name
    if not path.exists():
        return default
    try:
        raw = path.read_text()
        payload = json.loads(raw)
    except Exception as e:
        if strict:
            raise StateStoreError(f"cannot parse critical state file {name}: {type(e).__name__}") from e
        return default

    sidecar = path.with_suffix(path.suffix + ".sha256")
    if strict and not sidecar.exists():
        raise StateStoreError(f"checksum sidecar missing for critical state file {name}")
    if sidecar.exists():
        expected = sidecar.read_text().strip()
        actual = _checksum(payload)
        if expected != actual:
            if strict:
                raise StateStoreError(f"checksum mismatch for critical state file {name}")
            return default
    return payload


def write_json(name: str, payload: Any):
    path = DATA / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    side_tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")

    encoded = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False)
    tmp.write_text(encoded)
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    tmp.replace(path)

    side_tmp.write_text(_checksum(payload) + "\n")
    with side_tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    side_tmp.replace(sidecar)
    # Best-effort directory fsync so rename metadata is durable on local POSIX filesystems.
    try:
        dir_fd = os.open(DATA, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        pass


def initial_state(equity: float) -> Dict[str, Any]:
    return {
        "schema_version": 3,
        "equity": equity,
        "peak_equity": equity,
        "cash": equity,
        "positions": {},
        "pending_entries": {},
        "last_signal_bar_ts": {},
        "last_prices": {},
        "daily_pnl": 0.0,
        "risk_day": None,
        "risk_day_start_equity": equity,
        "consecutive_losses": 0,
        "cooldown_remaining": 0,
        "cooldown_last_bar_ts": None,
        "last_cycle": None,
        "cycle_count": 0,
        "config_hash": None,
        "halted": False,
        "halt_reason": None,
    }


def initial_runtime(equity: float) -> Dict[str, Any]:
    return {
        "runtime_schema_version": 2,
        "portfolio": initial_state(equity),
        "ledger": [],
        "audit_log": [],
        "escalation_queue": [],
        "decision_journal": [],
        "supervisor_delivery": {"last_ack_seq": 0, "last_ack_event_hash": None, "last_ack_at": None, "last_response_hash": None},
    }


def validate_runtime(runtime: dict):
    if not isinstance(runtime, dict) or runtime.get("runtime_schema_version") != 2:
        raise StateStoreError("unsupported or missing runtime snapshot schema; explicit migration from older snapshots is required")
    for key in ("portfolio", "ledger", "audit_log", "escalation_queue", "decision_journal", "supervisor_delivery"):
        if key not in runtime:
            raise StateStoreError(f"runtime snapshot missing {key}")
    validate_state(runtime["portfolio"])
    if not isinstance(runtime["ledger"], list) or not isinstance(runtime["audit_log"], list):
        raise StateStoreError("runtime ledger/audit must be lists")
    if runtime["ledger"] and not verify_hash_chain(runtime["ledger"]):
        raise StateStoreError("ledger hash chain verification failed")
    if runtime["audit_log"] and not verify_hash_chain(runtime["audit_log"]):
        raise StateStoreError("audit hash chain verification failed")
    if not isinstance(runtime["escalation_queue"], list):
        raise StateStoreError("escalation_queue must be a list")
    if not isinstance(runtime["decision_journal"], list):
        raise StateStoreError("decision_journal must be a list")
    if runtime["decision_journal"] and not verify_hash_chain(runtime["decision_journal"]):
        raise StateStoreError("decision journal hash chain verification failed")
    expected_seq = 1
    for event in runtime["decision_journal"]:
        if event.get("seq") != expected_seq or not event.get("event_id"):
            raise StateStoreError("decision journal sequence/event identity invalid")
        expected_seq += 1
    delivery = runtime["supervisor_delivery"]
    if not isinstance(delivery, dict):
        raise StateStoreError("supervisor_delivery must be an object")
    ack = delivery.get("last_ack_seq", 0)
    if not isinstance(ack, int) or ack < 0 or ack > len(runtime["decision_journal"]):
        raise StateStoreError("supervisor_delivery last_ack_seq invalid")
    ack_hash = delivery.get("last_ack_event_hash")
    if ack == 0:
        if ack_hash not in (None, "GENESIS"):
            raise StateStoreError("supervisor_delivery genesis acknowledgement hash invalid")
    else:
        expected_ack_hash = runtime["decision_journal"][ack - 1].get("event_hash")
        if ack_hash != expected_ack_hash:
            raise StateStoreError("supervisor_delivery acknowledgement hash does not match decision journal")
    for item in runtime["escalation_queue"]:
        if not isinstance(item, dict) or not item.get("id") or item.get("status") not in {"OPEN", "ACKNOWLEDGED"}:
            raise StateStoreError("invalid escalation queue item")


def read_runtime(equity: float) -> Dict[str, Any]:
    """Read one authoritative atomic snapshot. Never compose trading state from partial files."""
    name = "runtime_snapshot.json"
    path = DATA / name
    if not path.exists():
        # Once Trip's has ever persisted runtime state, loss of the authoritative snapshot is a HALT,
        # never a silent fresh-account reset. Derived mirrors/legacy files also prove prior state existed.
        evidence_names = ["runtime_initialized.marker", "state.json", "escalations.json",
                          "portfolio.json", "ledger.json", "audit_log.json"]
        present = [x for x in evidence_names if (DATA / x).exists()]
        if present:
            raise StateStoreError(f"authoritative runtime snapshot missing after prior initialization: {present}")
        return initial_runtime(equity)
    runtime = read_json(name, None, strict=True)
    validate_runtime(runtime)
    return runtime


def write_runtime(runtime: Dict[str, Any]):
    validate_runtime(runtime)
    write_json("runtime_snapshot.json", runtime)
    marker = DATA / "runtime_initialized.marker"
    if not marker.exists():
        marker.write_text("Trip's runtime initialized; missing runtime_snapshot.json must fail closed.\n")


def validate_state(state: dict):
    if state.get("schema_version") != 3:
        raise StateStoreError("unsupported or missing portfolio schema_version")
    required = {"equity", "peak_equity", "cash", "positions", "pending_entries", "daily_pnl", "risk_day_start_equity", "cycle_count"}
    missing = required - set(state)
    if missing:
        raise StateStoreError(f"critical state schema missing keys: {sorted(missing)}")
    for key in ("equity", "peak_equity", "cash", "daily_pnl", "risk_day_start_equity"):
        value = state[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StateStoreError(f"critical state {key} is non-finite or non-numeric")
    if not isinstance(state["cycle_count"], int) or state["cycle_count"] < 0:
        raise StateStoreError("critical state cycle_count must be a non-negative integer")
    if not isinstance(state.get("cooldown_remaining", 0), int) or state.get("cooldown_remaining", 0) < 0:
        raise StateStoreError("cooldown_remaining must be a non-negative integer")
    if not isinstance(state.get("consecutive_losses", 0), int) or state.get("consecutive_losses", 0) < 0:
        raise StateStoreError("consecutive_losses must be a non-negative integer")
    if (state["equity"] < 0 or state["peak_equity"] <= 0 or state["cash"] < -1e-9
            or state["peak_equity"] + 1e-9 < state["equity"]
            or state["equity"] + 1e-9 < state["cash"]
            or state["risk_day_start_equity"] <= 0):
        raise StateStoreError("critical state contains impossible account values")
    if not isinstance(state["positions"], dict) or not isinstance(state["pending_entries"], dict):
        raise StateStoreError("critical state positions/pending_entries must be objects")
    if not isinstance(state.get("last_signal_bar_ts", {}), dict) or not isinstance(state.get("last_prices", {}), dict):
        raise StateStoreError("last_signal_bar_ts/last_prices must be objects")
    for symbol, px in state.get("last_prices", {}).items():
        if not isinstance(symbol, str) or not isinstance(px, (int, float)) or not math.isfinite(px) or px <= 0:
            raise StateStoreError("invalid last_prices record")
    for symbol, p in state["positions"].items():
        if not isinstance(symbol, str) or not isinstance(p, dict):
            raise StateStoreError("invalid position record")
        for key in ("entry", "initial_stop", "stop", "target", "atr_at_entry"):
            v = p.get(key)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise StateStoreError(f"invalid position {symbol} field {key}")
        qty = p.get("qty")
        if not isinstance(qty, int) or qty <= 0:
            raise StateStoreError(f"invalid position {symbol} quantity")
        for ts_key in ("signal_bar_ts", "fill_bar_ts", "last_processed_bar_ts"):
            ts = p.get(ts_key)
            if not isinstance(ts, str) or not ts:
                raise StateStoreError(f"invalid position {symbol} {ts_key}")
        score = p.get("signal_score")
        if not isinstance(score, (int, float)) or not math.isfinite(score) or not (0 <= score <= 1):
            raise StateStoreError(f"invalid position {symbol} signal_score")
        if not (p["initial_stop"] < p["entry"] < p["target"]):
            raise StateStoreError(f"invalid position {symbol} stop/entry/target ordering")
        if p["stop"] > p["target"]:
            raise StateStoreError(f"invalid position {symbol} current stop")
    for symbol, p in state["pending_entries"].items():
        if not isinstance(symbol, str) or not isinstance(p, dict) or not isinstance(p.get("signal_bar_ts"), str) or not p.get("signal_bar_ts"):
            raise StateStoreError("invalid pending entry record")
        score = p.get("signal_score")
        atr = p.get("atr_at_signal")
        agreement = p.get("agreement_count")
        if not isinstance(score, (int, float)) or not math.isfinite(score) or not (0 <= score <= 1):
            raise StateStoreError(f"invalid pending entry {symbol} signal_score")
        if not isinstance(atr, (int, float)) or not math.isfinite(atr) or atr <= 0:
            raise StateStoreError(f"invalid pending entry {symbol} atr_at_signal")
        if not isinstance(agreement, int) or agreement < 1:
            raise StateStoreError(f"invalid pending entry {symbol} agreement_count")
        if p.get("candle_context") not in {"BULLISH_CONTEXT", "BEARISH_CONTEXT", "NEUTRAL_OR_AMBIGUOUS", "CONFLICTING_CONTEXT"}:
            raise StateStoreError(f"invalid pending entry {symbol} candle_context")


def append_hash_chained_event(events: list, event: dict) -> dict:
    prev_hash = events[-1].get("event_hash", "GENESIS") if events else "GENESIS"
    body = dict(event)
    body["prev_hash"] = prev_hash
    body["event_hash"] = hashlib.sha256(prev_hash.encode("utf-8") + _canonical_bytes(event)).hexdigest()
    events.append(body)
    return body


def verify_hash_chain(events: Iterable[dict]) -> bool:
    prev = "GENESIS"
    for event in events:
        if "event_hash" not in event or "prev_hash" not in event:
            return False
        if event["prev_hash"] != prev:
            return False
        body = {k: v for k, v in event.items() if k not in {"prev_hash", "event_hash"}}
        expected = hashlib.sha256(prev.encode("utf-8") + _canonical_bytes(body)).hexdigest()
        if event["event_hash"] != expected:
            return False
        prev = event["event_hash"]
    return True


def acknowledge_escalation(escalation_id: str, note: str = "") -> dict:
    """Explicitly acknowledge one open escalation and audit the acknowledgement."""
    from datetime import datetime, timezone
    with cycle_lock():
        runtime = read_json("runtime_snapshot.json", None, strict=True)
        if runtime is None:
            raise StateStoreError("runtime snapshot does not exist")
        validate_runtime(runtime)
        target = next((x for x in runtime["escalation_queue"]
                       if x.get("id") == escalation_id and x.get("status") == "OPEN"), None)
        if target is None:
            raise StateStoreError("open escalation ID not found")
        target["status"] = "ACKNOWLEDGED"
        target["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
        target["acknowledgement_note"] = str(note)[:2000]
        append_hash_chained_event(runtime["audit_log"], {
            "ts": target["acknowledged_at"], "event": "ESCALATION_ACKNOWLEDGED",
            "escalation_id": escalation_id, "note": target["acknowledgement_note"],
        })
        write_runtime(runtime)
        return target


@contextmanager
def cycle_lock():
    """Prevent two local/scheduled cycles from mutating state concurrently."""
    lock_path = DATA / ".cycle.lock"
    fh = lock_path.open("a+")
    try:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise StateStoreError("another Trip's cycle already holds the state lock") from e
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()
