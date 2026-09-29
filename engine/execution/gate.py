"""Execution Authority Gate and the frozen live boundary.

The gate contains NO trading intelligence. It cannot create, resize, reprice, retime or
re-route anything. It performs a deterministic final aggregation of already-computed authority
and answers only EXECUTION_AUTHORIZED = TRUE/FALSE, recording why. It may not override either
permission.

``FrozenLiveBoundary`` is the piece that makes live capital release impossible while the frozen
Constitution forbids it. It reads the frozen core (read-only) and refuses release, which is why
this whole layer provably terminates at LIVE_LOCKED.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .contracts import ExecutionLayerError

AUTHORIZED = "EXECUTION_AUTHORIZED"
BLOCKED = "EXECUTION_BLOCKED"

TRADE_VALID_FIELDS: Tuple[str, ...] = (
    "truth_valid",
    "strategy_actionable",
    "risk_approved",
    "constitution_compliant",
    "scope_valid",
    "intent_current",
)

CAPITAL_RELEASE_FIELDS: Tuple[str, ...] = (
    "governor_permits",
    "broker_state_reconciled",
    "broker_healthy",
    "correct_account",
    "live_environment_verified",
    "build_config_integrity_verified",
    "no_unresolved_execution_ambiguity",
    "no_applicable_halt",
)

# Rule ids that would have to exist in the frozen Constitution before live release is expressible.
LIVE_AUTHORIZATION_RULE_IDS: Tuple[str, ...] = ("LIVE_GATE",)
FROZEN_PROHIBITION_RULE_IDS: Tuple[str, ...] = ("PAPER_FIRST",)

_ENGINE_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _ENGINE_DIR.parent


@dataclass(frozen=True)
class Permission:
    name: str
    granted: bool
    checks: Tuple[Dict[str, Any], ...]

    @property
    def failing(self) -> Tuple[str, ...]:
        return tuple(c["name"] for c in self.checks if not c["passed"])

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "granted": self.granted, "checks": list(self.checks),
                "failing": list(self.failing)}


def _build_permission(name: str, evidence: Mapping[str, Any], fields: Sequence[str]) -> Permission:
    checks: List[Dict[str, Any]] = []
    for field_name in fields:
        value = evidence.get(field_name)
        if not isinstance(value, bool):
            # Unknown is not permission. Fail closed.
            checks.append({"name": field_name, "passed": False,
                           "detail": "evidence absent or not boolean; unknown state is not permission"})
        else:
            checks.append({"name": field_name, "passed": value,
                           "detail": "satisfied" if value else "not satisfied"})
    return Permission(name=name, granted=all(c["passed"] for c in checks), checks=tuple(checks))


def trade_valid(evidence: Mapping[str, Any]) -> Permission:
    return _build_permission("TRADE_VALID", evidence, TRADE_VALID_FIELDS)


def capital_release(evidence: Mapping[str, Any]) -> Permission:
    return _build_permission("CAPITAL_RELEASE", evidence, CAPITAL_RELEASE_FIELDS)


@dataclass(frozen=True)
class GateDecision:
    decision: str
    reasons: Tuple[str, ...]
    trade_valid: Permission
    capital_release: Permission

    @property
    def authorized(self) -> bool:
        return self.decision == AUTHORIZED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "TRADE_VALID": self.trade_valid.to_dict(),
            "CAPITAL_RELEASE": self.capital_release.to_dict(),
            "note": "Deterministic aggregation only. The gate never overrides either permission.",
        }


class AuthorityGate:
    """Final deterministic aggregation. No intelligence, no mutation, no override."""

    def evaluate(self, *, trade: Permission, capital: Permission) -> GateDecision:
        reasons: List[str] = []
        if not trade.granted:
            reasons.append("TRADE_VALID_FALSE:" + ",".join(trade.failing))
        if not capital.granted:
            reasons.append("CAPITAL_RELEASE_FALSE:" + ",".join(capital.failing))
        decision = AUTHORIZED if (trade.granted and capital.granted) else BLOCKED
        return GateDecision(decision=decision, reasons=tuple(reasons), trade_valid=trade,
                            capital_release=capital)


# ---------------------------------------------------------------------------
# Frozen live boundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveBoundaryVerdict:
    released: bool
    code: str
    reasons: Tuple[str, ...]
    frozen_core_verified: bool
    constitution_rule_ids: Tuple[str, ...]
    mode: str = "unknown"
    permitted_modes: Tuple[str, ...] = ()
    prohibition_rules_present: Tuple[str, ...] = ()
    authorization_rules_present: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "released": self.released,
            "code": self.code,
            "reasons": list(self.reasons),
            "frozen_core_verified": self.frozen_core_verified,
            "constitution_rule_ids": list(self.constitution_rule_ids),
            "mode": self.mode,
            "permitted_modes": list(self.permitted_modes),
            "prohibition_rules_present": list(self.prohibition_rules_present),
            "authorization_rules_present": list(self.authorization_rules_present),
            "blockers": self.blockers(),
        }

    def blockers(self) -> List[str]:
        """The independent frozen facts that are withholding release, named by category."""
        found: List[str] = []
        if self.prohibition_rules_present and not self.authorization_rules_present:
            found.append("CONSTITUTION_PROHIBITION_RULE")
        if self.mode not in self.permitted_modes:
            found.append("CONFIG_GUARD_MODE_RESTRICTION")
        if not self.frozen_core_verified:
            found.append("FROZEN_CORE_DIGEST_UNVERIFIED")
        return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_core_digest(core_manifest: Path | None = None) -> Dict[str, Any]:
    """Prove the live Constitution file still matches the reviewed frozen core digest."""
    manifest = core_manifest or (_PROJECT_ROOT / "infra" / "core_v06.sha256")
    if not manifest.exists():
        return {"verified": False, "reason": "frozen core manifest missing"}
    recorded = None
    for line in manifest.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].replace("\\", "/").endswith("engine/constitution.py"):
            recorded = parts[0]
    if not recorded:
        return {"verified": False, "reason": "constitution.py not present in frozen core manifest"}
    live = _sha256(_ENGINE_DIR / "constitution.py")
    return {"verified": live == recorded, "recorded": recorded, "live": live}


def frozen_constitution_rule_ids() -> Tuple[str, ...]:
    from constitution import NON_NEGOTIABLES

    return tuple(rule_id for rule_id, _ in NON_NEGOTIABLES)


#: Candidate modes probed against the frozen config_guard to derive what it permits.
MODE_CANDIDATES: Tuple[str, ...] = ("paper", "live", "shadow", "research")


def frozen_config_guard_permits(mode: str) -> Dict[str, Any]:
    """Ask the FROZEN config_guard whether a mode is permitted, by actually running it.

    This is a probe of the real frozen validator rather than a hardcoded mirror, so it cannot
    silently drift from the frozen rule. Note that the frozen config_guard refuses every mode
    other than ``paper``: that is a SECOND, independent blocker alongside the Constitution's
    ``PAPER_FIRST`` rule, and both must be amended before live release is possible.
    """
    import copy
    import json
    from config_guard import ConfigError, validate_config

    try:
        probe = json.loads((_ENGINE_DIR / "config.json").read_text())
    except Exception as exc:  # pragma: no cover - unreadable config must not read as permissive
        return {"permitted": False, "mode": mode,
                "reason": f"approved config is unreadable: {type(exc).__name__}"}
    probe = copy.deepcopy(probe)
    probe["mode"] = mode
    try:
        validate_config(probe)
    except ConfigError as exc:
        return {"permitted": False, "mode": mode, "reason": str(exc)}
    except Exception as exc:
        return {"permitted": False, "mode": mode, "reason": type(exc).__name__}
    return {"permitted": True, "mode": mode, "reason": None}


def frozen_permitted_modes(candidates: Sequence[str] = MODE_CANDIDATES) -> Tuple[str, ...]:
    """Derive the mode set the frozen config_guard actually permits. No hardcoding."""
    return tuple(mode for mode in candidates if frozen_config_guard_permits(mode)["permitted"])


class FrozenLiveBoundary:
    """Read-only bridge to the frozen core. Never edits a frozen file.

    Release requires ALL of: the frozen Constitution to have dropped its prohibition and gained the
    authorising rule, the frozen config_guard to permit the running mode, and the frozen core
    digest to verify. ``permitted_modes`` may only be supplied by a verified amendment basis.
    """

    def evaluate(self, *, mode: str, rule_ids: Sequence[str] | None = None,
                 permitted_modes: Sequence[str] | None = None) -> LiveBoundaryVerdict:
        ids = tuple(rule_ids) if rule_ids is not None else frozen_constitution_rule_ids()
        modes = tuple(permitted_modes) if permitted_modes is not None else frozen_permitted_modes()
        digest = verify_frozen_core_digest()
        reasons: List[str] = []

        prohibition = [rid for rid in FROZEN_PROHIBITION_RULE_IDS if rid in ids]
        authorization = [rid for rid in LIVE_AUTHORIZATION_RULE_IDS if rid in ids]

        if prohibition and not authorization:
            reasons.append(
                f"frozen Constitution rule {prohibition[0]} still forbids live-money order submission"
            )
        if mode not in modes:
            probe = frozen_config_guard_permits(mode) if permitted_modes is None else {}
            reasons.append(
                f"frozen config mode is {mode!r}; the frozen config_guard permits {list(modes)}"
                + (f" ({probe.get('reason')})" if probe.get("reason") else ""))
        if not digest.get("verified"):
            reasons.append("frozen core digest could not be verified against infra/core_v06.sha256")

        released = not reasons
        code = "LIVE_RELEASE_PERMITTED" if released else "REFUSED_BY_FROZEN_LIVE_BOUNDARY"
        return LiveBoundaryVerdict(
            released=released, code=code, reasons=tuple(reasons),
            frozen_core_verified=bool(digest.get("verified")), constitution_rule_ids=ids,
            mode=mode, permitted_modes=modes,
            prohibition_rules_present=tuple(prohibition),
            authorization_rules_present=tuple(authorization))


def frozen_config_mode(config_path: Path | None = None) -> str:
    import json

    path = config_path or (_ENGINE_DIR / "config.json")
    try:
        return str(json.loads(path.read_text()).get("mode"))
    except Exception as exc:  # pragma: no cover - unreadable config must not read as permissive
        raise ExecutionLayerError("cannot read engine/config.json; refusing to assume a mode") from exc
