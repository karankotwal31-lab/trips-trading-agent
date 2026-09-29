"""Durable, integrity-checked state for the additive execution layer.

Deliberately independent of ``engine/store.py`` (frozen). The frozen store owns the
authoritative trading snapshot and uses an evidence-file heuristic to detect a lost runtime;
execution-layer state must not be able to influence that decision. This module therefore keeps
its own root under ``runtime_data/execution/`` and mirrors the frozen durability pattern
(atomic temp+fsync+rename, SHA-256 sidecar, fail-closed reads) without editing it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from .contracts import LedgerError

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent

# Overridable so tests can point at a temporary directory (same technique the existing suite
# uses for the frozen store). No directory is created at import time.
ROOT_DIR: Path = PROJECT_ROOT / "runtime_data" / "execution"


def _checksum(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_path(name: str) -> Path:
    if not isinstance(name, str) or not name or name.startswith("/") or ".." in Path(name).parts:
        raise LedgerError("illegal execution state path")
    return ROOT_DIR / name


def read_json(name: str, default: Any = None, *, strict: bool = False) -> Any:
    path = _safe_path(name)
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        if strict:
            raise LedgerError(f"cannot parse critical execution state {name}: {type(exc).__name__}") from exc
        return default

    sidecar = path.with_suffix(path.suffix + ".sha256")
    if strict and not sidecar.exists():
        raise LedgerError(f"checksum sidecar missing for critical execution state {name}")
    if sidecar.exists():
        if sidecar.read_text().strip() != _checksum(payload):
            if strict:
                raise LedgerError(f"checksum mismatch for critical execution state {name}")
            return default
    return payload


def write_json(name: str, payload: Any) -> None:
    path = _safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    side_tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")

    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False, allow_nan=False))
    with tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    tmp.replace(path)

    side_tmp.write_text(_checksum(payload) + "\n")
    with side_tmp.open("rb") as fh:
        os.fsync(fh.fileno())
    side_tmp.replace(sidecar)

    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        pass
