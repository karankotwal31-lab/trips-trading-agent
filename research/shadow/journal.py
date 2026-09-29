"""Append-only hash-chained journal for Trip's research shadow runner.

Research-only. No engine, broker, credential, order, or network imports.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping


ZERO_HASH = "0" * 64


class JournalError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def event_hash(event_without_hash: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(dict(event_without_hash))).hexdigest()


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except Exception as exc:
            raise JournalError(f"journal line {line_no} is not valid JSON") from exc
        if not isinstance(event, dict):
            raise JournalError(f"journal line {line_no} is not a JSON object")
        events.append(event)
    return events


def verify_chain(events: Iterable[Mapping[str, object]]) -> dict:
    previous = ZERO_HASH
    expected_seq = 1
    count = 0
    for count, raw in enumerate(events, 1):
        event = dict(raw)
        actual_hash = event.pop("event_hash", None)
        if event.get("seq") != expected_seq:
            return {
                "valid": False,
                "count": count,
                "reason": f"sequence mismatch at {count}: {event.get('seq')!r} != {expected_seq}",
            }
        if event.get("prev_hash") != previous:
            return {
                "valid": False,
                "count": count,
                "reason": f"prev_hash mismatch at seq {expected_seq}",
            }
        calculated = event_hash(event)
        if actual_hash != calculated:
            return {
                "valid": False,
                "count": count,
                "reason": f"event_hash mismatch at seq {expected_seq}",
            }
        previous = str(actual_hash)
        expected_seq += 1
    return {
        "valid": True,
        "count": count,
        "last_hash": previous,
        "reason": None,
    }


def verify_file(path: Path) -> dict:
    try:
        return verify_chain(load_events(path))
    except JournalError as exc:
        return {"valid": False, "count": 0, "last_hash": ZERO_HASH, "reason": str(exc)}


def append_event(path: Path, payload: Mapping[str, object]) -> dict:
    """Append one immutable event after verifying the full existing chain."""
    events = load_events(path)
    status = verify_chain(events)
    if not status["valid"]:
        raise JournalError(f"refusing append to invalid journal: {status['reason']}")
    seq = len(events) + 1
    event = {
        "seq": seq,
        "prev_hash": status.get("last_hash", ZERO_HASH),
        **dict(payload),
    }
    if "event_hash" in payload:
        raise JournalError("payload may not supply event_hash")
    event["event_hash"] = event_hash(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        fh.flush()
    return event


def latest_event(events: Iterable[Mapping[str, object]], kind: str) -> dict | None:
    for event in reversed(list(events)):
        if event.get("kind") == kind:
            return dict(event)
    return None
