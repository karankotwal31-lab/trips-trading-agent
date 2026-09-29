"""Trading-environment lifecycle.

RESEARCH -> BACKTEST -> SHADOW -> LIVE_LOCKED -> LIVE_READY_LOCKED -> LIVE_ENABLED

There is no PAPER stage. Trip's is a live-money-only execution system: paper and sandbox broker
execution are not prerequisites, are not on the path to LIVE_ENABLED, and are not represented as
anywhere on it. A recorded fixture, mock or simulation is an engineering test and never a trading
environment; it creates no portfolio state and satisfies no readiness requirement.

Nothing promotes itself. Student, Evolution, Strategy, Supervisor, Guardian, broker
connectivity and passing tests are NOT promotion authorities. LIVE_ENABLED requires an explicit
owner authorization artifact AND a deterministic boundary verdict. This build parks at
LIVE_READY_LOCKED and cannot reach LIVE_ENABLED while the frozen Constitution forbids live-money
orders.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .amendment import CoreStateBasis, frozen_core_basis  # noqa: E402
from .contracts import (CORE_CAPABILITIES, ExecutionLayerError,  # noqa: E402
                        approved_symbol_scope, canonical_json)
from .gate import (FrozenLiveBoundary, LiveBoundaryVerdict, frozen_config_mode,  # noqa: E402
                   frozen_constitution_rule_ids, frozen_permitted_modes,
                   frozen_risk_permitted_modes)
from .owner_authority import (PURPOSE_LIVE_AUTHORIZATION, owner_authority_status,  # noqa: E402
                             verify_owner_signature)
from typing import Mapping  # noqa: E402


def _outstanding_engineering(conformance: Mapping[str, Any]) -> List[str]:
    """Engineering work still outstanding, derived from the executed evidence."""
    outstanding: List[str] = []
    for broker_id, document in sorted(conformance.items()):
        missing = document.missing_core()
        if missing:
            outstanding.append(f"{broker_id}: core capabilities not proven: {list(missing)}")
    return outstanding


def _outstanding_parts(failing: Sequence[str]) -> List[str]:
    """Engineering parts of the classified blockers that the executed evidence does not cover."""
    from .readiness import ENGINEERING_WORK_COVERAGE

    outstanding = []
    for item in OWNER_BLOCKING_ITEMS:
        for part in item.engineering:
            covering = ENGINEERING_WORK_COVERAGE.get(part)
            if covering is None or covering not in failing:
                continue
            outstanding.append(f"{item.name}: {part} (unproven by {covering})")
    return outstanding

LIVE_LOCKED_REFUSAL = "LIVE_LOCKED_REFUSAL"
CORE_STATE_CALLER_ASSERTED_REFUSED = "CORE_STATE_CALLER_ASSERTED_REFUSED"
CORE_STATE_SOURCES = ("FROZEN_FILES", "VERIFIED_AMENDMENT", "CALLER_ASSERTED")
LIVE_VERIFICATION_MAX_AGE_SECONDS = 15 * 60


class Stage(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    SHADOW = "SHADOW"
    LIVE_LOCKED = "LIVE_LOCKED"
    #: Everything an autonomous process can complete is done, and it has been verified READ-ONLY
    #: against the intended real live account where credentials allowed. Still locked: the
    #: remaining blockers are owner credentials, owner capital values, or the owner's signature,
    #: and no component may supply any of them. This is the ceiling autonomous work may reach.
    LIVE_READY_LOCKED = "LIVE_READY_LOCKED"
    LIVE_ENABLED = "LIVE_ENABLED"


#: The stage order, as data. PAPER is absent by design, not by omission.
STAGE_ORDER: Tuple[Stage, ...] = (Stage.RESEARCH, Stage.BACKTEST, Stage.SHADOW, Stage.LIVE_LOCKED,
                                  Stage.LIVE_READY_LOCKED, Stage.LIVE_ENABLED)

#: Everything LIVE_ENABLED requires. Each entry is checked by name; none is a paper or sandbox
#: substitute, and none may be satisfied by a recorded fixture.
LIVE_ENABLED_REQUIREMENTS: Tuple[str, ...] = (
    "pinned_ed25519_owner_trust_root",
    "owner_signed_capital_governor_profile",
    "approved_constitutional_amendment_covering_all_three_frozen_blockers",
    "exact_approved_build_and_config_identities",
    "owner_signed_live_authorization",
    "verified_real_live_broker_account",
    "verified_live_market_data",
    "clean_reconciliation",
    "healthy_truth_risk_capital_and_constitution_gates",
    "no_halt",
    "no_unresolved_order_ambiguity",
)


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
    # SHADOW goes straight to the lock. There is no paper waypoint, because a simulated broker
    # order is not evidence about a real brokerage account.
    Stage.SHADOW: (Stage.LIVE_LOCKED,),
    Stage.LIVE_LOCKED: (Stage.LIVE_READY_LOCKED,),
    Stage.LIVE_READY_LOCKED: (Stage.LIVE_ENABLED, Stage.LIVE_LOCKED),
    Stage.LIVE_ENABLED: (Stage.LIVE_READY_LOCKED,),
}

@dataclass(frozen=True)
class BlockerItem:
    """One remaining blocker, with its engineering part and its owner part stated separately."""

    name: str
    engineering: Tuple[str, ...] = ()
    owner: Tuple[str, ...] = ()

    @property
    def owner_only(self) -> bool:
        """True when nothing an autonomous process can build remains on this item."""
        return not self.engineering

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "owner_only": self.owner_only,
                "engineering": list(self.engineering), "owner": list(self.owner)}


#: What still stands between this build and live capital, split by WHO can clear it.
#:
#: Calling all of it owner work was wrong, and it was wrong in the direction that flatters the
#: build: it let a program declare "the only thing left is the owner's signature" while real
#: engineering - a channel that actually talks to a broker, normalization of what that broker
#: returns, a conformance suite that records per-capability facts, a reconciliation engine that
#: has been run, a production data provider, Truth and provenance integration, closed 60-minute
#: bars, calendar and session integration and fail-closed data health - had never been written or
#: run.
#:
#: So each blocker now carries two lists. ``engineering`` is what an autonomous process can finish
#: and prove. ``owner`` is what no program can supply: a credential, an entitlement, a capital
#: value, or a signature. ``LIVE_READY_LOCKED`` asserts that every remaining item is an owner one.
#:
#: Note what is NOT in either list: paper or sandbox broker execution. It is not a prerequisite
#: and not an achievement, so it can appear in neither column.
OWNER_BLOCKING_ITEMS: Tuple["BlockerItem", ...] = (
    BlockerItem(
        name="owner_public_key_configured",
        owner=("the owner must install their Ed25519 public key; no owner act is possible "
               "without it",)),
    BlockerItem(
        name="capital_governor_profile_set",
        owner=("maximum capital, loss, exposure and position values are an owner input; none has "
               "been invented",)),
    BlockerItem(
        name="broker_channel_and_conformance",
        engineering=("a real BrokerChannel implementation per broker",
                     "broker-specific response normalization",
                     "a per-capability conformance suite producing typed, versioned evidence",
                     "a reconciliation engine executed against known-agreeing and "
                     "known-disagreeing fixtures",
                     "RECORDED_CONTRACT_CONFORMANCE evidence for implementation behaviour"),
        owner=("LIVE_READ_ONLY_BROKER_VERIFICATION against the intended real live brokerage "
               "account: credentials and the owner's OAuth approval for that account",)),
    BlockerItem(
        name="production_market_data",
        engineering=("a production market-data provider implementation",
                     "integration with the frozen Truth Engine",
                     "a data-provenance guard separating market data from broker execution feeds",
                     "closed 60-minute bars enforced through the frozen closure rule",
                     "exchange-calendar and session integration",
                     "fail-closed data-health logic"),
        owner=("the provider API key, subscription and real-time entitlement where required",)),
    BlockerItem(
        name="owner_signed_live_authorization",
        owner=("decision A (amendment) and decision B (live authorization), separately and "
               "explicitly signed",)),
    BlockerItem(
        name="constitutional_amendment_applied",
        owner=("re-freezing the frozen core is an owner act through the approved change process",)),
)

# Stage the additive execution layer ships in: fully built, verified, holding at the lock.
DEFAULT_STAGE = Stage.LIVE_LOCKED


@dataclass(frozen=True)
class LiveAuthorization:
    """Owner-controlled artifact binding one approved live identity.

    Any material change to strategy code, Risk rules, Capital Governor rules, Truth rules,
    symbol scope or execution semantics must invalidate this and return the runtime to
    LIVE_LOCKED until verification completes again.

    Authority is the ``signature`` field — an Ed25519 signature (RFC 8032) over
    ``signed_payload()``, domain-separated by purpose, made with the owner key — and deliberately
    NOT a boolean. A boolean is settable by any caller, which would make this artifact
    self-mintable and the two owner acts collapse into one forgery.
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
    signature: str = ""

    def signed_payload(self) -> bytes:
        """The canonical bytes the owner signs. The signature itself is excluded."""
        body = asdict(self)
        body.pop("signature", None)
        return canonical_json(body)

    def signature_valid(self) -> Tuple[bool, str, str]:
        """Verify this artifact against the owner key. Returns (ok, code, detail)."""
        return verify_owner_signature(PURPOSE_LIVE_AUTHORIZATION, self.signed_payload(),
                                      self.signature)

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
        return hashlib.sha256(self.signed_payload()).hexdigest()

    def is_valid(self, now: datetime | None = None) -> Tuple[bool, Tuple[str, ...]]:
        now = now or datetime.now(timezone.utc)
        reasons = []
        signed_ok, signed_code, signed_detail = self.signature_valid()
        if not signed_ok:
            reasons.append(f"{signed_code}: {signed_detail}")
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
                 release_basis: Optional[CoreStateBasis] = None,
                 authorization: Optional[LiveAuthorization] = None,
                 live_verification: Optional[Mapping[str, Any]] = None,
                 market_data_verification: Optional[Any] = None) -> None:
        self._stage = Stage(stage)
        self._boundary = boundary or FrozenLiveBoundary()
        if release_basis is not None and release_basis.source not in CORE_STATE_SOURCES:
            raise ExecutionLayerError(f"unrecognized release basis source {release_basis.source!r}")
        if release_basis is not None and release_basis.source == "VERIFIED_AMENDMENT":
            # A release basis proves what the AMENDED core would say. That is evidence, not
            # authority: without the owner's signed authorization, a basis alone would release
            # capital and owner decision A would be skippable entirely.
            if authorization is None:
                raise ExecutionLayerError(
                    "a release basis requires the owner's signed live authorization; "
                    "a basis is evidence, not authority")
            valid, reasons = authorization.is_valid()
            if not valid:
                raise ExecutionLayerError("live authorization is not valid: " + "; ".join(reasons))
        self._release_basis = release_basis
        self._authorization = authorization
        # Keep the complete typed verification envelope. Bare record maps discard the broker,
        # account and environment binding and can never authorize LIVE_ENABLED.
        self._live_verification = live_verification
        # Independent market-data Truth evidence is deliberately separate from broker/account
        # verification. A healthy broker feed never becomes Strategy/Truth authority by convenience.
        self._market_data_verification = market_data_verification

    @property
    def authorization(self) -> Optional[LiveAuthorization]:
        return self._authorization

    def authorization_status(self) -> dict:
        """Whether owner-signed acts are even possible in this runtime. Never returns key material."""
        status = owner_authority_status()
        status["authorization_present"] = self._authorization is not None
        if self._authorization is not None:
            ok, code, detail = self._authorization.signature_valid()
            status["authorization_signature"] = {"ok": ok, "code": code, "detail": detail}
        return status

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
            permitted_modes=frozen_permitted_modes(),
            risk_permitted_modes=frozen_risk_permitted_modes(), source="CALLER_ASSERTED")

    def live_readiness(self) -> Dict[str, Any]:
        """What still stands between this build and live capital, classified by who can clear it.

        Engineering items are computed from EVIDENCE: every check below actually executes the
        subsystem it names and reports a digest over what it observed. Nothing here is satisfied
        by a file existing or a module importing. Owner items are credentials, capital values and
        signatures, and this method never reports one as satisfied.

        Reaching ``LIVE_READY_LOCKED`` requires that no engineering item is outstanding AND that
        every remaining item is an owner one. It grants nothing: capital release still requires a
        separately signed live authorization.
        """
        from .readiness import collect_evidence

        records, conformance = collect_evidence()
        checks = [record.to_dict() for record in records]
        failing = [record.name for record in records if not record.passed]

        owner_items = [item.to_dict() for item in OWNER_BLOCKING_ITEMS]
        engineering_remaining = _outstanding_parts(failing) + _outstanding_engineering(conformance)

        return {
            "stage": self._stage.value,
            "stage_order": [stage.value for stage in STAGE_ORDER],
            "live_enabled_requirements": list(LIVE_ENABLED_REQUIREMENTS),
            "engineering_ready": not failing,
            "engineering_checks": checks,
            "failing_engineering": failing,
            "engineering_evidence_model": (
                "every check executes the subsystem it names and reports a digest over the "
                "observation; module presence is not engineering completion"),
            "live_read_only_broker_verification": self.live_verification_status(),
            "production_market_data_verification": self.market_data_verification_status(),
            "conformance_evidence": {
                broker_id: {
                    "digest": document.digest(),
                    "complete": document.is_complete(),
                    "capabilities": {name: document.status(name).value for name in CORE_CAPABILITIES},
                    "mandate": (document.mandate_verdict(sorted(approved_symbol_scope()))
                                if document.mandate else None),
                } for broker_id, document in sorted(conformance.items())
            },
            "remaining_engineering_work": engineering_remaining,
            "owner_blocking_items": owner_items,
            "owner_only_items": [item["name"] for item in owner_items if item["owner_only"]],
            "live_status": "NOT_COMPLETE" if failing else "ENGINEERING_COMPLETE_STILL_LOCKED",
            "ceiling": Stage.LIVE_READY_LOCKED.value,
            "note": ("LIVE_READY_LOCKED would mean every remaining blocker is an owner credential, "
                     "an owner capital value, or the owner's signature. It grants nothing: capital "
                     "release still requires a separately signed live authorization. While any "
                     "engineering evidence is missing the honest report is NOT_COMPLETE. There is "
                     "no paper stage and no paper prerequisite anywhere on this path."),
        }

    def live_verification_status(self, *, authorization: Optional[LiveAuthorization] = None,
                                 now: datetime | None = None) -> Dict[str, Any]:
        """Validate current real-account evidence and optionally bind it to owner authority.

        Activation accepts only the complete typed verification envelope. A dictionary of
        individual records is insufficient because it loses the broker/account identity that
        those records are supposed to prove. Evidence also expires.
        """
        from .live_verification import (EVIDENCE_KIND_LIVE_READ_ONLY, READ_ONLY_CHECKS,
                                        REQUIRED_INSTRUMENTS, LiveReadOnlyVerification)

        verification = self._live_verification
        if verification is None:
            return {
                "evidence_kind": EVIDENCE_KIND_LIVE_READ_ONLY,
                "status": "NOT_VERIFIED", "verified": False,
                "activation_verified": False, "authorization_bound": False,
                "observed": {}, "missing_checks": list(READ_ONLY_CHECKS),
                "reasons": ["no live read-only verification envelope is installed"],
                "submits_no_order": True, "releases_capital": False,
                "max_age_seconds": LIVE_VERIFICATION_MAX_AGE_SECONDS,
            }
        if not isinstance(verification, LiveReadOnlyVerification):
            return {
                "evidence_kind": EVIDENCE_KIND_LIVE_READ_ONLY,
                "status": "NOT_VERIFIED", "verified": False,
                "activation_verified": False, "authorization_bound": False,
                "observed": {}, "missing_checks": list(READ_ONLY_CHECKS),
                "reasons": ["bare verification records are refused; the typed broker/account "
                            "verification envelope is required"],
                "submits_no_order": True, "releases_capital": False,
                "max_age_seconds": LIVE_VERIFICATION_MAX_AGE_SECONDS,
            }

        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        records = verification.records
        observed: Dict[str, Any] = {}
        evidence_reasons: List[str] = []

        try:
            generated = datetime.fromisoformat(
                str(verification.generated_at).replace("Z", "+00:00"))
            if generated.tzinfo is None:
                raise ValueError("generated_at is naive")
            generated = generated.astimezone(timezone.utc)
            age = (now - generated).total_seconds()
            if age < -300:
                evidence_reasons.append("live verification timestamp is implausibly in the future")
            elif age > LIVE_VERIFICATION_MAX_AGE_SECONDS:
                evidence_reasons.append(
                    f"live verification is stale ({age:.0f}s > "
                    f"{LIVE_VERIFICATION_MAX_AGE_SECONDS}s)")
        except Exception:
            evidence_reasons.append("live verification generated_at is unusable")

        for name in READ_ONLY_CHECKS:
            record = records.get(name)
            if record is None:
                observed[name] = "MISSING"
                continue
            if record.evidence_kind != EVIDENCE_KIND_LIVE_READ_ONLY:
                observed[name] = "REFUSED: wrong evidence kind"
                evidence_reasons.append(f"{name}: wrong evidence kind")
                continue
            try:
                verified_at = datetime.fromisoformat(
                    str(record.verified_at).replace("Z", "+00:00"))
                if verified_at.tzinfo is None:
                    raise ValueError("verified_at is naive")
                verified_at = verified_at.astimezone(timezone.utc)
                record_age = (now - verified_at).total_seconds()
                if record_age < -300:
                    observed[name] = "REFUSED: timestamp is in the future"
                    evidence_reasons.append(f"{name}: timestamp is implausibly in the future")
                    continue
                if record_age > LIVE_VERIFICATION_MAX_AGE_SECONDS:
                    observed[name] = "REFUSED: stale"
                    evidence_reasons.append(f"{name}: evidence is stale ({record_age:.0f}s)")
                    continue
            except Exception:
                observed[name] = "REFUSED: timestamp unusable"
                evidence_reasons.append(f"{name}: verified_at is unusable")
                continue
            observed[name] = "PASSED" if record.passed else f"FAILED: {record.detail[:160]}"
            if not record.passed:
                evidence_reasons.append(f"{name}: {record.detail[:160]}")

        missing = [name for name in READ_ONLY_CHECKS if name not in records]
        evidence_reasons.extend(f"{name}: missing" for name in missing)
        if verification.evidence_kind != EVIDENCE_KIND_LIVE_READ_ONLY:
            evidence_reasons.append("verification envelope is not live read-only evidence")
        if verification.environment != "live":
            evidence_reasons.append(
                f"verification environment {verification.environment!r} is not live")

        evidence_verified = not evidence_reasons and not missing and verification.verified
        binding_reasons: List[str] = []
        authorization_bound = False
        if authorization is None:
            binding_reasons.append("no owner live authorization supplied for evidence binding")
        else:
            if authorization.environment != "live":
                binding_reasons.append(
                    f"authorization environment {authorization.environment!r} is not live")
            if verification.broker_id != authorization.broker_id:
                binding_reasons.append(
                    f"verified broker {verification.broker_id!r} does not match authorized "
                    f"broker {authorization.broker_id!r}")
            if verification.account_id != authorization.account_id:
                binding_reasons.append(
                    f"verified account {verification.account_id!r} does not match authorized "
                    f"account {authorization.account_id!r}")

            broker_record = records.get("verify_broker_identity")
            broker_obs = dict(getattr(broker_record, "observation", {}) or {})
            if str(broker_obs.get("broker_id") or "") != authorization.broker_id:
                binding_reasons.append("broker-identity observation does not match authorization")
            if str(broker_obs.get("environment") or "") != "live":
                binding_reasons.append("broker-identity observation did not prove the live environment")

            account_record = records.get("verify_exact_account")
            account_obs = dict(getattr(account_record, "observation", {}) or {})
            if str(account_obs.get("account_id") or "") != authorization.account_id:
                binding_reasons.append("account observation does not match authorization")

            instruments_record = records.get("verify_required_instruments_available")
            instruments_obs = dict(getattr(instruments_record, "observation", {}) or {})
            available = {str(s).upper() for s in instruments_obs.get("available", ())}
            required = set(REQUIRED_INSTRUMENTS)
            if not required.issubset(available):
                binding_reasons.append(
                    f"live broker verification did not prove all required instruments: "
                    f"{sorted(required - available)}")

            authorization_bound = not binding_reasons

        activation_verified = bool(evidence_verified and authorization_bound)
        return {
            "evidence_kind": EVIDENCE_KIND_LIVE_READ_ONLY,
            "status": "VERIFIED" if evidence_verified else "NOT_VERIFIED",
            "verified": bool(evidence_verified),
            "activation_verified": activation_verified,
            "authorization_bound": authorization_bound,
            "broker_id": verification.broker_id,
            "account_id": verification.account_id,
            "environment": verification.environment,
            "observed": observed,
            "missing_checks": missing,
            "reasons": evidence_reasons + binding_reasons,
            "submits_no_order": True,
            "releases_capital": False,
            "max_age_seconds": LIVE_VERIFICATION_MAX_AGE_SECONDS,
            "note": ("Eleven non-mutating broker/account checks against the intended real live "
                     "account. Market-data Truth evidence is verified independently and cannot be "
                     "satisfied by a broker execution feed."),
        }

    def advance(self, to: Stage, *, actor: Actor, authorization: Optional[LiveAuthorization] = None,
                mode: Optional[str] = None, rule_ids: Optional[Sequence[str]] = None,
                now: datetime | None = None, config: Optional[Mapping[str, Any]] = None,
                governor_profile_hash: Optional[str] = None) -> Dict[str, Any]:
        """Promote only on explicit owner authority, except into the locked readiness stage.

        ``LIVE_READY_LOCKED`` is reachable by any actor once the engineering checks pass, because
        it asserts completeness and grants nothing. ``LIVE_ENABLED`` remains owner-only.
        """
        to = Stage(to)
        if to not in ALLOWED_TRANSITIONS.get(self._stage, ()):
            return {"advanced": False, "code": "PROMOTION_REFUSED_ILLEGAL_TRANSITION",
                    "stage": self._stage.value,
                    "reason": f"{self._stage.value} -> {to.value} is not a permitted transition"}
        if actor is not Actor.OWNER:
            if to is not Stage.LIVE_READY_LOCKED:
                return {"advanced": False, "code": "PROMOTION_REFUSED_NOT_OWNER",
                        "stage": self._stage.value,
                        "reason": f"{actor.value} is not a promotion authority"}
            readiness = self.live_readiness()
            if not readiness["engineering_ready"]:
                return {"advanced": False, "code": "LIVE_READY_REFUSED_ENGINEERING_INCOMPLETE",
                        "stage": self._stage.value,
                        "reason": "; ".join(readiness["failing_engineering"]),
                        "readiness": readiness}
            self._stage = to
            return {"advanced": True, "code": "STAGE_ADVANCED", "stage": self._stage.value,
                    "note": "still locked: no capital release is possible from this stage",
                    "readiness": readiness}

        if to is Stage.LIVE_ENABLED:
            effective_authorization = authorization or self._authorization
            if authorization is not None and self._authorization is not None:
                if authorization.content_hash() != self._authorization.content_hash():
                    return {
                        "advanced": False,
                        "code": "LIVE_ENABLE_REFUSED_AUTHORIZATION_MISMATCH",
                        "stage": self._stage.value,
                        "reason": "supplied live authorization differs from the lifecycle-bound artifact",
                    }
            if effective_authorization is None:
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_NO_AUTHORIZATION",
                        "stage": self._stage.value, "reason": "no owner live authorization artifact"}
            valid, reasons = effective_authorization.is_valid(now)
            if not valid:
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_INVALID_AUTHORIZATION",
                        "stage": self._stage.value, "reason": "; ".join(reasons)}
            if effective_authorization.environment != "live":
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_NON_LIVE_AUTHORIZATION",
                        "stage": self._stage.value,
                        "reason": "the owner authorization must name environment='live'"}

            live_verification = self.live_verification_status(
                authorization=effective_authorization, now=now)
            if not live_verification["activation_verified"]:
                return {
                    "advanced": False,
                    "code": "LIVE_ENABLE_REFUSED_LIVE_VERIFICATION",
                    "stage": self._stage.value,
                    "reason": "; ".join(live_verification["reasons"]),
                    "live_verification": live_verification,
                }

            missing_identity = []
            if config is None:
                missing_identity.append("config")
            if governor_profile_hash is None:
                missing_identity.append("governor_profile_hash")
            if missing_identity:
                return {
                    "advanced": False,
                    "code": "LIVE_ENABLE_REFUSED_IDENTITY_EVIDENCE_MISSING",
                    "stage": self._stage.value,
                    "reason": "missing mandatory identity evidence: " + ", ".join(missing_identity),
                }

            from .identity import authorization_drift, current_identity

            current = current_identity(config=dict(config),
                                       governor_profile_hash=str(governor_profile_hash))
            drifted, drift_reasons = authorization_drift(effective_authorization, current)
            if drifted:
                return {"advanced": False, "code": "LIVE_ENABLE_REFUSED_AUTHORIZATION_DRIFT",
                        "stage": self._stage.value, "reason": "; ".join(drift_reasons)}

            verdict = self.release_verdict(mode=mode, rule_ids=rule_ids)
            if not verdict.released:
                return {"advanced": False, "code": LIVE_LOCKED_REFUSAL, "stage": self._stage.value,
                        "reason": "; ".join(verdict.reasons), "boundary": verdict.to_dict()}

            # Persist exactly the artifact that passed activation so the gateway's mutation permit
            # remains attributable to the same owner authority.
            self._authorization = effective_authorization

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
                                       permitted_modes=basis.permitted_modes,
                                       risk_permitted_modes=basis.risk_permitted_modes)

    def may_transmit_live(self, *, mode: Optional[str] = None,
                          rule_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """The single question that matters: may a live order leave this process?

        A caller may assert a core state to *ask* whether it would release, but an asserted state
        can never actually release capital. Only the frozen files on disk, or a basis produced by
        a verified owner-signed amendment, can.
        """
        basis = self._basis(mode, rule_ids)
        verdict = self._boundary.evaluate(mode=basis.mode, rule_ids=basis.rule_ids,
                                          permitted_modes=basis.permitted_modes,
                                          risk_permitted_modes=basis.risk_permitted_modes)
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
