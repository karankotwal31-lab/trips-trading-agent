"""Constitutional amendment verification — the ONLY supported way past ``PAPER_FIRST``.

``PAPER_FIRST`` is a frozen invariant. It lives in ``engine/constitution.py``, a file pinned by
``infra/core_v06.sha256``, and it is enforced twice over:

* ``constitution.constitution_gate()`` returns ``passed`` only while ``mode == "paper"``;
* ``config_guard.validate_config()`` refuses any mode other than ``paper``.

This module does not weaken either one. What it does is make the release path **concrete,
auditable and executable** so that the amendment is a reviewed owner act rather than a code edit
somebody makes in a hurry:

1. ``live_release_requirements()`` states, from the frozen core itself, exactly which frozen facts
   currently forbid release and exactly which artifacts would have to be re-approved.
2. ``AmendmentProposal`` is an owner-**signed**, versioned, expiring description of the intended
   rule change and target mode. The signature is an HMAC-SHA256 tag over the proposal's canonical
   payload, produced with the owner key held outside this repository. There is no ``owner_signed``
   boolean: a boolean is settable by any caller and would make this whole mechanism forgeable.
3. ``verify_amendment()`` proves the gate OPENS by running the **production**
   ``FrozenLiveBoundary`` over the amended rule set and mode. It enforces a strict-superset
   invariant: the amendment may remove only the prohibition and add only the authorizing rule.
   It refuses when the owner key is unconfigured, absent, or the signature does not match.
4. ``apply_amendment()`` deliberately refuses. Editing a hash-pinned frozen file is an owner act
   performed through the approved change process, never something this deterministic code does on
   its own initiative.

So the override mechanism exists, is demonstrated and is tested — and it cannot be triggered by
the Strategy, Risk, Governor, Student, Evolution, Guardian or AI Supervisor, or by a caller
passing a convenient argument: a caller who sets every argument still cannot produce a signature
that verifies against a key it does not hold.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import ExecutionLayerError, canonical_json
from .gate import (
    FROZEN_PROHIBITION_RULE_IDS,
    LIVE_AUTHORIZATION_RULE_IDS,
    FrozenLiveBoundary,
    frozen_config_guard_permits,
    frozen_config_mode,
    frozen_constitution_rule_ids,
    frozen_permitted_modes,
    verify_frozen_core_digest,
)
from .owner_authority import OwnerAuthorityError, owner_authority_status, verify_owner_signature
from . import owner_authority

#: Rules this amendment mechanism is permitted to touch. Nothing else may be added or removed.
AMENDABLE_ADD_RULES: Tuple[str, ...] = LIVE_AUTHORIZATION_RULE_IDS
AMENDABLE_REMOVE_RULES: Tuple[str, ...] = FROZEN_PROHIBITION_RULE_IDS

#: Frozen artifacts whose review and re-approval an amendment requires, in order.
AMENDMENT_ARTIFACTS: Tuple[str, ...] = (
    "engine/constitution.py",
    "engine/config_guard.py",
    "infra/core_v06.sha256",
    "engine/approved_config.sha256",
    "engine/approved_build.json",
    "infra/approved_infra.json",
)

#: Verdict codes. The owner-signature codes come from the trust root so the two cannot drift.
OWNER_SIGNATURE_REQUIRED = owner_authority.SIGNATURE_REQUIRED
OWNER_AUTHORITY_KEY_NOT_CONFIGURED = owner_authority.KEY_NOT_CONFIGURED
OWNER_SIGNATURE_INVALID = owner_authority.SIGNATURE_INVALID
OWNER_SIGNATURE_VALID = owner_authority.SIGNATURE_VALID
AMENDMENT_EXPIRED = "AMENDMENT_EXPIRED"
AMENDMENT_ADD_NOT_PERMITTED = "AMENDMENT_ADD_NOT_PERMITTED"
AMENDMENT_REMOVE_NOT_PERMITTED = "AMENDMENT_REMOVE_NOT_PERMITTED"
AMENDMENT_REMOVES_PROTECTED_RULE = "AMENDMENT_REMOVES_PROTECTED_RULE"
AMENDMENT_DOES_NOT_OPEN_THE_GATE = "AMENDMENT_DOES_NOT_OPEN_THE_GATE"
AMENDMENT_APPLICABLE = "AMENDMENT_APPLICABLE"

APPLICATION_REFUSED = "AMENDMENT_APPLICATION_IS_AN_OWNER_ACT"


class AmendmentError(ExecutionLayerError):
    """An amendment proposal is structurally unusable."""


class AmendmentApplicationRefused(ExecutionLayerError):
    """Applying an amendment is an owner act; deterministic code refuses to self-apply it."""


def _aware(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise AmendmentError(f"amendment {field} is not parseable ISO-8601") from exc
    if parsed.tzinfo is None:
        raise AmendmentError(f"amendment {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AmendmentProposal:
    """An owner-SIGNED description of the intended constitutional change.

    There is deliberately no ``owner_signed`` boolean. Authority is the ``signature`` field: an
    HMAC-SHA256 tag over ``signed_payload()`` produced with the owner key. A boolean can be set by
    any caller, so it would make the whole amendment mechanism forgeable.
    """

    amendment_id: str
    adds_rules: Tuple[str, ...]
    removes_rules: Tuple[str, ...]
    target_mode: str
    issued_at: str
    expires_at: str
    rationale: str = ""
    signature: str = ""

    def __post_init__(self) -> None:
        if not str(self.amendment_id).strip():
            raise AmendmentError("amendment_id is required")
        for name in ("adds_rules", "removes_rules"):
            value = getattr(self, name)
            if isinstance(value, str) or not isinstance(value, (tuple, list, set, frozenset)):
                raise AmendmentError(f"{name} must be a collection of rule ids")
            object.__setattr__(self, name, tuple(str(item) for item in value))
        if not str(self.target_mode).strip():
            raise AmendmentError("target_mode is required")
        if not isinstance(self.signature, str):
            raise AmendmentError("amendment signature must be a string")
        issued = _aware(self.issued_at, "issued_at")
        expires = _aware(self.expires_at, "expires_at")
        if expires <= issued:
            raise AmendmentError("amendment expires_at must be after issued_at")

    def signed_payload(self) -> bytes:
        """The canonical bytes the owner signs. The signature itself is excluded."""
        body = asdict(self)
        body.pop("signature", None)
        return canonical_json(body)

    def claim_owner_signed(self) -> bool:
        """Whether this proposal carries a signature that verifies against the owner key."""
        return verify_owner_signature(self.signed_payload(), self.signature)[0]

    def content_hash(self) -> str:
        return hashlib.sha256(self.signed_payload()).hexdigest()

    def is_current(self, now: datetime) -> bool:
        return _aware(self.issued_at, "issued_at") <= now.astimezone(timezone.utc) \
            < _aware(self.expires_at, "expires_at")

    def describe(self) -> Dict[str, Any]:
        return {**asdict(self), "content_hash": self.content_hash()}


@dataclass(frozen=True)
class CoreStateBasis:
    """A verified statement of what the frozen core says about live release.

    A ``Lifecycle`` may carry one of these so the transmission boundary evaluates against a core
    state that was *proven* by ``verify_amendment``, rather than against whatever argument a
    caller happens to pass. ``source`` records which of the two it is.
    """

    mode: str
    rule_ids: Tuple[str, ...]
    permitted_modes: Tuple[str, ...] = ()
    source: str = "FROZEN_FILES"
    amendment_id: Optional[str] = None
    amendment_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "rule_ids": list(self.rule_ids),
                "permitted_modes": list(self.permitted_modes), "source": self.source,
                "amendment_id": self.amendment_id, "amendment_hash": self.amendment_hash}


def frozen_core_basis() -> CoreStateBasis:
    """Read the live basis straight from the frozen core. No overrides are possible here."""
    return CoreStateBasis(mode=frozen_config_mode(), rule_ids=frozen_constitution_rule_ids(),
                          permitted_modes=frozen_permitted_modes(), source="FROZEN_FILES")


def blockers_to_live_release(*, boundary: Optional[FrozenLiveBoundary] = None,
                             target_mode: str = "live") -> Dict[str, Any]:
    """What withholds LIVE release, measured in the intended live state against the FROZEN rules.

    Evaluating the current paper configuration would understate the problem: the running mode is
    legitimately permitted, so only the Constitution rule would show up. This instead asks the
    question a live deployment asks, and reports every independent blocker it hits.
    """
    boundary = boundary or FrozenLiveBoundary()
    basis = frozen_core_basis()
    verdict = boundary.evaluate(mode=target_mode, rule_ids=basis.rule_ids,
                                permitted_modes=basis.permitted_modes)
    return {"target_mode": target_mode, "blockers": verdict.blockers(),
            "reasons": list(verdict.reasons), "boundary": verdict.to_dict()}


def live_release_requirements(*, boundary: Optional[FrozenLiveBoundary] = None) -> Dict[str, Any]:
    """State exactly what the frozen core currently requires, and what would have to change."""
    boundary = boundary or FrozenLiveBoundary()
    basis = frozen_core_basis()
    verdict = boundary.evaluate(mode=basis.mode, rule_ids=basis.rule_ids,
                                permitted_modes=basis.permitted_modes)
    live = blockers_to_live_release(boundary=boundary)
    digest = verify_frozen_core_digest()
    guard = frozen_config_guard_permits("live")
    return {
        "released": verdict.released,
        "code": verdict.code,
        "blocking_reasons": list(verdict.reasons),
        "independent_blockers_current_mode": verdict.blockers(),
        "blockers_to_live_release": live["blockers"],
        "blockers_to_live_release_reasons": live["reasons"],
        "frozen_core_verified": bool(digest.get("verified")),
        "current_mode": basis.mode,
        "current_rule_count": len(basis.rule_ids),
        "frozen_config_guard_permitted_modes": list(basis.permitted_modes),
        "owner_authority": owner_authority_status(),
        "prohibition_rule_present": sorted(set(FROZEN_PROHIBITION_RULE_IDS) & set(basis.rule_ids)),
        "authorization_rule_present": sorted(set(LIVE_AUTHORIZATION_RULE_IDS) & set(basis.rule_ids)),
        "required_change": {
            "add_rules": list(AMENDABLE_ADD_RULES),
            "remove_rules": list(AMENDABLE_REMOVE_RULES),
            "target_mode": "live",
            "config_guard_probe": {
                "mode": "live", "permitted_by_frozen_core": bool(guard.get("permitted")),
                "reason": guard.get("reason"),
                "detail": ("The frozen config_guard refuses every mode other than paper, so the "
                           "PAPER_FIRST rule is NOT the only frozen blocker. Both the Constitution "
                           "rule and the config_guard mode restriction must be amended together."),
            },
        },
        "artifacts_requiring_owner_review_and_reapproval": list(AMENDMENT_ARTIFACTS),
        "note": ("The amendment mechanism is implemented and tested. It refuses to self-apply: "
                 "editing a hash-pinned frozen file and re-freezing the core manifest is an owner "
                 "act performed through the approved change process."),
    }


def amended_rule_ids(proposal: AmendmentProposal,
                     current_rule_ids: Sequence[str]) -> Tuple[str, ...]:
    """The rule set the frozen Constitution would contain after the amendment. Order preserved."""
    remaining = [rule for rule in current_rule_ids if rule not in set(proposal.removes_rules)]
    for rule in proposal.adds_rules:
        if rule not in remaining:
            remaining.append(rule)
    return tuple(remaining)


def verify_amendment(proposal: AmendmentProposal, *, current_rule_ids: Optional[Sequence[str]] = None,
                     boundary: Optional[FrozenLiveBoundary] = None,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Prove an amendment opens the live gate, using the PRODUCTION boundary.

    Returns a verdict dict. ``AMENDMENT_APPLICABLE`` means the gate provably releases with the
    amended facts; it does not mean anything was changed.
    """
    boundary = boundary or FrozenLiveBoundary()
    now = now or datetime.now(timezone.utc)
    current = tuple(current_rule_ids) if current_rule_ids is not None \
        else frozen_constitution_rule_ids()

    def refuse(code: str, reason: str) -> Dict[str, Any]:
        return {"applicable": False, "code": code, "reasons": [reason],
                "proposal_hash": proposal.content_hash(),
                "current_rule_count": len(current)}

    signed_ok, signed_code, signed_detail = verify_owner_signature(
        proposal.signed_payload(), proposal.signature)
    if not signed_ok:
        return refuse(signed_code,
                      f"{signed_detail}; only the owner may amend the Constitution")
    if not proposal.is_current(now):
        return refuse(AMENDMENT_EXPIRED, "amendment is outside its validity window")

    illegal_adds = sorted(set(proposal.adds_rules) - set(AMENDABLE_ADD_RULES))
    if illegal_adds:
        return refuse(AMENDMENT_ADD_NOT_PERMITTED,
                      f"amendment may only add {list(AMENDABLE_ADD_RULES)}; refused {illegal_adds}")
    illegal_removes = sorted(set(proposal.removes_rules) - set(AMENDABLE_REMOVE_RULES))
    if illegal_removes:
        return refuse(AMENDMENT_REMOVE_NOT_PERMITTED,
                      f"amendment may only remove {list(AMENDABLE_REMOVE_RULES)}; "
                      f"refused {illegal_removes}")
    if not set(proposal.removes_rules):
        return refuse(AMENDMENT_DOES_NOT_OPEN_THE_GATE,
                      "amendment removes nothing, so the prohibition would remain in force")

    rule_ids = amended_rule_ids(proposal, current)
    # Strict superset: every rule except the amended ones must survive untouched.
    protected = [rule for rule in current if rule not in set(proposal.removes_rules)
                 and rule not in set(rule_ids)]
    if protected:
        return refuse(AMENDMENT_REMOVES_PROTECTED_RULE,
                      f"amendment would drop protected rules {protected}")

    # The amendment also amends the frozen config_guard, so the post-amendment mode set is the
    # frozen one plus the proposed target mode. This models the SECOND frozen blocker: the
    # Constitution rule alone would not have been enough.
    current_modes = frozen_permitted_modes()
    if proposal.target_mode in current_modes:
        return refuse(AMENDMENT_DOES_NOT_OPEN_THE_GATE,
                      f"the frozen config_guard already permits mode {proposal.target_mode!r}, "
                      "so this amendment does not change the mode restriction")
    permitted_modes = tuple(current_modes) + (proposal.target_mode,)

    verdict = boundary.evaluate(mode=proposal.target_mode, rule_ids=rule_ids,
                                permitted_modes=permitted_modes)
    if not verdict.released:
        return {"applicable": False, "code": AMENDMENT_DOES_NOT_OPEN_THE_GATE,
                "reasons": list(verdict.reasons), "proposal_hash": proposal.content_hash(),
                "amended_rule_ids": list(rule_ids), "target_mode": proposal.target_mode,
                "permitted_modes": list(permitted_modes), "boundary": verdict.to_dict(),
                "note": "the production boundary still refuses under the amended facts"}

    basis = CoreStateBasis(mode=proposal.target_mode, rule_ids=rule_ids,
                           permitted_modes=permitted_modes, source="VERIFIED_AMENDMENT",
                           amendment_id=proposal.amendment_id,
                           amendment_hash=proposal.content_hash())
    return {
        "applicable": True,
        "code": AMENDMENT_APPLICABLE,
        "reasons": [],
        "proposal_hash": proposal.content_hash(),
        "amendment_id": proposal.amendment_id,
        "target_mode": proposal.target_mode,
        "added_rules": list(proposal.adds_rules),
        "removed_rules": list(proposal.removes_rules),
        "amended_rule_count": len(rule_ids),
        "preserved_rule_count": len([r for r in current if r not in set(proposal.removes_rules)]),
        "permitted_modes": list(permitted_modes),
        "blockers_resolved": blockers_to_live_release(boundary=boundary)["blockers"],
        "blockers_after_amendment": verdict.blockers(),
        "boundary_code": verdict.code,
        "boundary": verdict.to_dict(),
        "release_basis": basis.to_dict(),
        "artifacts_requiring_owner_review_and_reapproval": list(AMENDMENT_ARTIFACTS),
        "note": ("The production FrozenLiveBoundary releases under these facts, so PAPER_FIRST is "
                 "no longer the sole refusal reason. Nothing was written: applying the change to "
                 "the hash-pinned frozen files is an owner act."),
    }


def release_basis_from_verdict(verdict: Mapping[str, Any]) -> CoreStateBasis:
    """Turn an AMENDMENT_APPLICABLE verdict into the basis a Lifecycle may carry."""
    if not verdict.get("applicable"):
        raise AmendmentApplicationRefused(
            f"refusing a release basis from a non-applicable amendment: {verdict.get('code')}")
    basis = dict(verdict.get("release_basis") or {})
    return CoreStateBasis(mode=str(basis["mode"]), rule_ids=tuple(basis["rule_ids"]),
                          permitted_modes=tuple(basis.get("permitted_modes") or ()),
                          source=str(basis.get("source", "VERIFIED_AMENDMENT")),
                          amendment_id=basis.get("amendment_id"),
                          amendment_hash=basis.get("amendment_hash"))


def apply_amendment(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Always refuses. Applying an amendment is an owner act through the approved change process."""
    raise AmendmentApplicationRefused(
        f"{APPLICATION_REFUSED}: this module verifies and demonstrates the amendment; it does not "
        f"edit hash-pinned frozen files. To apply a verified amendment, the owner must change "
        f"{list(AMENDMENT_ARTIFACTS)} through the approved change process, re-freeze "
        f"infra/core_v06.sha256 and re-approve the derived manifests, then re-run the safety "
        f"suites. No autonomous component may perform these steps.")
