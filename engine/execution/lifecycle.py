"""Trading-environment lifecycle.

RESEARCH -> BACKTEST -> SHADOW -> PAPER -> LIVE_LOCKED -> LIVE_ENABLED

Nothing promotes itself. Student, Evolution, Strategy, Supervisor, Guardian, broker
connectivity and passing tests are NOT promotion authorities. LIVE_ENABLED requires an explicit
owner authorization artifact AND a deterministic boundary verdict. This build parks at
LIVE_LOCKED and cannot leave it while the frozen Constitution forbids live-money orders.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Sequence, Tuple

from .amendment import CoreStateBasis, frozen_core_basis  # noqa: E402
from .contracts import ExecutionLayerError, canonical_json
from .gate import (FrozenLiveBoundary, LiveBoundaryVerdict, frozen_config_mode,
                   frozen_constitution_rule_ids, frozen_permitted_modes)
from typing import Mapping  # noqa: E402

LIVE_LOCKED_REFUSAL = "LIVE_LOCKED_REFUSAL"
CORE_STATE_CALLER_ASSERTED_REFUSED = "CORE_STATE_CALLER_ASSERTED_REFUSED"
CORE_STATE_SOURCES = ("FROZEN_FILES", "VERIFIED_AMENDMENT", "CALLER_ASSERTED")


class Stage(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE_LOCKED = "LIVE_LOCKED"
    LIVE_ENABLED = "LIVE_ENABLED"


class Actor(str, Enum):
    OWNER = "OWNER"
    STUDENT = "STUDENT"
    EVOLUTION = "EVOLUTION"
    STRATEGY = "STRATEGY"
    GUARDIAN = "GUARDIAN"
    SUPERVISOR = "SUPERVISOR"
    BROKER = "BROKER"
    TESTS = "TESTS"


ALLOWED_TRANSITIONS: Dict[Stage, Tuple[Stage, ...]] = {
    Stage.RESEARCH: (Stage.BACKTEST,),
    Stage.BACKTEST: (Stage.SHADOW,),
    Stage.SHADOW: (Stage.PAPER,),
    Stage.PAPER: (Stage.LIVE_LOCKED,),
    Stage.LIVE_LOCKED: (Stage.LIVE_ENABLED,),
    Stage.LIVE_ENABLED: (Stage.LIVE_LOCKED,),
}

# Stage the additive execution layer ships in: fully built, verified, holding at the lock.
DEFAULT_STAGE = Stage.LIVE_LOCKED


@dataclass(frozen=True)
class LiveAuthorization:
    """Owner-controlled artifact binding one approved live identity.

    Any material change to strategy code, Risk rules, Capital Governor rules, Truth rules,
    symbol scope or execution semantics must invalidate this and return the runtime to
    LIVE_LOCKED until verification completes again.
    """

    strategy_build_id: str
    config_id: str
    risk_profile_id: str
    governor_profile_id: str
    broker_id: str
    account_id: str
    environment: str
    issued_at: str
    expires_at: str
    owner_signed: bool = False

    def _aware(self, value: str, name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception as exc:
            raise ExecutionLayerError(f"live authorization {name} is not parseable ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ExecutionLayerError(f"live authorization {name} must be timezone-aware")
        return parsed

    def identity_complete(self) -> bool:
        return all(str(getattr(self, name)).strip() for name in (
            "strategy_build_id", "config_id", "risk_profile_id", "governor_profile_id",
            "broker_id", "account_id", "environment",
        ))

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json({**asdict(self), "content_hash": None})).hexdigest()

    def is_valid(self, now: datetime | None = None) -> Tuple[bool, Tuple[str, ...]]:
        now = now or datetime.now(timezone.utc)
        reasons = []
        if not self.owner_signed:
            reasons.append("authorization is not owner-signed")
        if not self.identity_complete():
            reasons.append("authorization identity is incomplete")
        try:
            if not (self._aware(self.issued_at, "issued_at") <= now.astimezone(timezone.utc)
                    < self._aware(self.expires_at, "expires_at")):
                reasons.append("authorization is outside its validity window")
        except ExecutionLayerError as exc:
            reasons.append(str(exc))
        return (not reasons), tuple(reasons)


class Lifecycle:
    """Stage machine plus the single transmission gate.

    ``release_basis`` is how a deployment carries a *verified* statement of what the frozen core
    says. Supplying one is the only way to release capital without the frozen files themselves
    changing, and it can only be produced by ``amendment.verify_amendment`` returning
    AMENDMENT_APPLICABLE. Caller-asserted mode/rule arguments are still accepted for inspection,
    but they can NEVER release capital - see ``may_transmit_live``.
    """

    def __init__(self, stage: Stage = DEFAULT_STAGE, boundary: Optional[FrozenLiveBoundary] = None,
                 release_basis: Optional[CoreStateBasis] = None) -> None:
        self._stage = Stage(stage)
        self._boundary = boundary or FrozenLiveBoundary()
        if release_basis is not None and release_basis.source not in CORE_STATE_SOURCES:
            raise ExecutionLayerError(f"unrecognized release basis source {release_basis.source!r}")
        self._release_basis = release_basis

    @property
    def stage(self) -> Stage:
        return self._stage

    @property
    def boundary(self) -> FrozenLiveBoundary:
        return self._boundary

    @property
    def release_basis(self) -> Optional[CoreStateBasis]:
        return self._release_basis

    def core_state_basis(self) -> CoreStateBasis:
        """The basis this lifecycle actually evaluates against, with its provenance recorded."""
        return self._release_basis if self._release_basis is not None else frozen_core_basis()

    def _basis(self, mode: Optional[str], rule_ids: Optional[Sequence[str]]) -> CoreStateBasis:
        """Resolve the core state to evaluate against.

        A caller may assert the Constitution rule set and the mode, but never what modes the frozen
        config_guard permits: that is always read from the frozen validator. So a caller cannot
        talk the mode restriction away, and the asserted basis cannot release capital regardless.
        """
        if mode is None and rule_ids is None:
            return self.core_state_basis()
        return CoreStateBasis(
            mode=mode if mode is not None else frozen_config_mode(),
            rule_ids=tuple(rule_ids) if rule_ids is not None else frozen_constitution_rule_ids(),
            permitted_modes=frozen_permitted_modes(), source="CALLER_ASSERTED")

    def advance(self, to: Stage, *, actor: Actor, authorization: Optional[LiveAuthorization] = None,
                mode: Optional[str] = None, rule_ids: Optional[Sequence[str]] = None,
                now: datetime | None = None, config: Optional[Mapping[str, Any]] = None,
                governor_profile_hash: Optional[str] = None) -> Dict[str, Any]:
        """Promote only on explicit owner authority. Every other actor is refused."""
        to = Stage(to)
        if actor is not Actor.OWNER:
            return {"advanced": False, "code": "PROMOTION_REFUSED_NOT_OWNER", "stage": self._stage.value,
                    "reason": f"{actor.value} is not a promotion authority"}
        if to not in ALLOWED_TRANSITIONS.get(self._stage, ()):
            return {"advanced": False, "code": "PROMOTION_REFUSED_ILLEGAL_TRANSITION",
                    "stage": self._stage.value,
                    "reason": f"{self._stage.value} -> {to.value} is not a permitted transition"}

        if to is Stage.LIVE_ENABLED:
            verdict = self.release_verdict(mode=mode, rule_ids=rule_ids)
            if authorization is None:
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_NO_AUTHORIZATION",
                        "stage": self._stage.value, "reason": "no owner live authorization artifact"}
            valid, reasons = authorization.is_valid(now)
            if not valid:
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_INVALID_AUTHORIZATION",
                        "stage": self._stage.value, "reason": "; ".join(reasons)}
            if not verdict.released:
                return {"advanced": False, "code": LIVE_LOCKED_REFUSAL, "stage": self._stage.value,
                        "reason": "; ".join(verdict.reasons), "boundary": verdict.to_dict()}
            # A bound approval is worthless if the thing it approved has since changed.
            if config is not None and governor_profile_hash is not None:
                from .identity import authorization_drift, current_identity

                current = current_identity(config=dict(config),
                                           governor_profile_hash=governor_profile_hash)
                drifted, drift_reasons = authorization_drift(authorization, current)
                if drifted:
                    return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_AUTHORIZATION_DRIFT",
                            "stage": self._stage.value, "reason": "; ".join(drift_reasons)}

        self._stage = to
        return {"advanced": True, "code": "STAGE_ADVANCED", "stage": self._stage.value}

    def release_verdict(self, *, mode: Optional[str] = None,
                        rule_ids: Optional[Sequence[str]] = None) -> LiveBoundaryVerdict:
        """Pure inspection of what the boundary says for a given core state.

        This answers a question; it does not authorise anything. Only ``may_transmit_live``
        authorises, and it refuses a caller-asserted state regardless of what this returns.
        """
        basis = self._basis(mode, rule_ids)
        return self._boundary.evaluate(mode=basis.mode, rule_ids=basis.rule_ids,
                                       permitted_modes=basis.permitted_modes)

    def may_transmit_live(self, *, mode: Optional[str] = None,
                          rule_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """The single question that matters: may a live order leave this process?

        A caller may assert a core state to *ask* whether it would release, but an asserted state
        can never actually release capital. Only the frozen files on disk, or a basis produced by
        a verified owner-signed amendment, can.
        """
        basis = self._basis(mode, rule_ids)
        verdict = self._boundary.evaluate(mode=basis.mode, rule_ids=basis.rule_ids,
                                          permitted_modes=basis.permitted_modes)
        asserted = basis.source == "CALLER_ASSERTED"
        permitted = bool(verdict.released and self._stage is Stage.LIVE_ENABLED and not asserted)
        reasons = list(verdict.reasons)
        code = "LIVE_TRANSMISSION_PERMITTED" if permitted else LIVE_LOCKED_REFUSAL
        if asserted:
            code = CORE_STATE_CALLER_ASSERTED_REFUSED
            reasons.append("a caller-asserted core state cannot release capital; a release basis "
                           "must come from a verified owner-signed amendment")
        return {
            "permitted": permitted,
            "code": code,
            "stage": self._stage.value,
            "boundary": verdict.to_dict(),
            "core_state_source": basis.source,
            "release_basis": basis.to_dict() if not asserted else None,
            "reasons": reasons,
        }
