"""Approved-identity hashing and live-authorization drift detection.

The spec requires that changing material strategy code, Risk rules, Capital Governor rules,
Truth rules, symbol scope or execution semantics invalidates a previous live approval. Binding
an authorization document is not enough on its own: something must recompute the CURRENT
identity hashes and compare them. That is what this module does.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, FrozenSet, List, Mapping, Sequence, Tuple

from .contracts import canonical_json


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def approved_build_hash() -> str:
    """Frozen executable build identity. Read-only use of the existing build guard."""
    from build_guard import verify_build_integrity

    return verify_build_integrity()["manifest_hash"]


def approved_config_hash() -> str:
    from config_guard import fingerprint_config, validate_config

    return fingerprint_config(validate_config(dict(_raw_config())))


def _raw_config() -> Mapping[str, Any]:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "config.json"
    return json.loads(path.read_text())


def current_identity(*, config: Mapping[str, Any], governor_profile_hash: str,
                     engine_root: Any = None) -> Dict[str, str]:
    """Every identity component a live authorization must be bound to.

    ``engine_root`` is accepted so tests can compute identity without touching the live config.
    """
    if engine_root is not None:
        import json
        from pathlib import Path

        config = json.loads((Path(engine_root) / "config.json").read_text())
    return {
        "build_hash": approved_build_hash(),
        "config_hash": _digest(dict(config)),
        "risk_profile_hash": _digest(dict(config.get("risk", {}))),
        "truth_rules_hash": _digest(dict(config.get("truth", {}))),
        "governor_profile_hash": governor_profile_hash,
        "symbol_scope_hash": _digest(sorted(config.get("symbols", []))),
        "instrument_scope_hash": _digest(str(config.get("instrument_scope", ""))),
        "bar_interval_hash": _digest(str(config.get("bar_interval", ""))),
    }


# Authorization field -> identity key. A mismatch means the approval no longer describes reality.
_BINDINGS: Tuple[Tuple[str, str], ...] = (
    ("strategy_build_id", "build_hash"),
    ("config_id", "config_hash"),
    ("risk_profile_id", "risk_profile_hash"),
    ("governor_profile_id", "governor_profile_hash"),
)


def authorization_drift(authorization: Any, current: Mapping[str, str]) -> Tuple[bool, List[str]]:
    """Return (drifted, reasons). Drift means the authorization no longer applies."""
    reasons: List[str] = []
    for field_name, identity_key in _BINDINGS:
        bound = str(getattr(authorization, field_name, "") or "")
        actual = str(current.get(identity_key, ""))
        if bound != actual:
            reasons.append(
                f"{field_name} no longer matches the current {identity_key}: "
                f"authorized={bound[:12] or '<empty>'}, current={actual[:12]}")
    return (bool(reasons), reasons)
