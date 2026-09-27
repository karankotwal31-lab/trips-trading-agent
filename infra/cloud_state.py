from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


class CloudStateError(RuntimeError):
    pass


MAX_RUNTIME_BYTES = 15_000_000


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def validate_payload_size(payload: Any) -> int:
    size = len(canonical_bytes(payload))
    if size > MAX_RUNTIME_BYTES:
        raise CloudStateError(f"cloud payload exceeds {MAX_RUNTIME_BYTES} byte safety limit")
    return size


@dataclass(frozen=True)
class RemoteRuntime:
    version: int
    payload: Dict[str, Any]
    payload_sha256: str
    updated_at: Optional[str] = None
