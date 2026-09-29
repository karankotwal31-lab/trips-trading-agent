"""AI Supervisor — one-way safety authority.

The supervisor observes and explains. It may only ever make the system MORE conservative.

Allowed findings: NORMAL / CAUTION / INVESTIGATE / SUPERVISOR_HALT_REQUEST.
A SUPERVISOR_HALT_REQUEST is translated by the deterministic Safety Controller into blocking
NEW exposure and nothing else. The supervisor can never resume trading, increase risk, create
exposure, modify an order, change a strategy, promote a model, change a limit, or switch broker
or account.

AI availability is never a required network hop for an order. If the provider is unavailable the
system records SUPERVISOR_UNAVAILABLE and follows configured policy; absence is never silently
read as approval.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Sequence, Tuple

from .contracts import MAX_EVIDENCE_BYTES, ExecutionLayerError, canonical_json

SUPERVISOR_UNAVAILABLE = "SUPERVISOR_UNAVAILABLE"


class SupervisorFinding(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    INVESTIGATE = "INVESTIGATE"
    SUPERVISOR_HALT_REQUEST = "SUPERVISOR_HALT_REQUEST"


class SupervisorAuthorityViolation(ExecutionLayerError):
    """The supervisor produced something outside its advisory/safety-only authority."""


# Fields the supervisor must never be able to influence. Presence of any of these in supervisor
# output is a hard violation, not a warning.
FORBIDDEN_OUTPUT_FIELDS: Tuple[str, ...] = (
    "symbol", "direction", "side", "quantity", "price", "limit_price", "stop", "stop_loss",
    "time_in_force", "order_type", "risk_limit", "capital_limit", "leverage", "strategy",
    "broker", "account", "account_id", "live_authorization", "resume", "promote",
)

# Explicit allowlist. Evidence not on this list is never forwarded to the provider.
EVIDENCE_ALLOWLIST: Tuple[str, ...] = (
    "system_health",
    "broker_health",
    "recent_decisions",
    "risk_utilization",
    "capital_utilization",
    "order_lifecycle",
    "rejections",
    "reconciliation_discrepancies",
    "market_data_anomalies",
    "student_findings",
    "performance_deterioration",
    "unexpected_behaviour_patterns",
    "build_config_integrity",
    "guardian_incidents",
)


@dataclass(frozen=True)
class SanitizedEvidencePacket:
    """Structured evidence for the supervisor. Fact fields only; no credentials, no raw payloads."""

    fields: Mapping[str, Any]
    generated_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        unexpected = sorted(set(self.fields) - set(EVIDENCE_ALLOWLIST))
        if unexpected:
            raise SupervisorAuthorityViolation(
                f"evidence packet contains non-allowlisted fields: {unexpected}")
        raw = canonical_json({"fields": dict(self.fields), "generated_at": self.generated_at})
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ExecutionLayerError(
                f"evidence packet exceeds {MAX_EVIDENCE_BYTES} bytes; refusing to relay a partial view")

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": self.schema_version, "generated_at": self.generated_at,
                "fields": dict(self.fields)}


@dataclass(frozen=True)
class SupervisorOutcome:
    finding: SupervisorFinding
    evidence_refs: Tuple[str, ...]
    explanation: str
    availability: str = "AVAILABLE"
    execution_authority: str = "NONE"
    may_only_tighten: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding": self.finding.value,
            "evidence_refs": list(self.evidence_refs),
            "explanation": self.explanation,
            "availability": self.availability,
            "execution_authority": self.execution_authority,
            "may_only_tighten": self.may_only_tighten,
        }


def availability_outcome(reason: str) -> SupervisorOutcome:
    """Record unavailability. Never interpret absence of AI as approval."""
    return SupervisorOutcome(finding=SupervisorFinding.INVESTIGATE, evidence_refs=(),
                             explanation=f"{SUPERVISOR_UNAVAILABLE}: {reason}",
                             availability=SUPERVISOR_UNAVAILABLE)


def interpret_supervisor_output(payload: Mapping[str, Any]) -> SupervisorOutcome:
    """Validate supervisor output. Rejects anything order-shaped or authority-expanding."""
    if not isinstance(payload, Mapping):
        raise SupervisorAuthorityViolation("supervisor output must be a structured mapping")

    lowered = {str(key).strip().lower() for key in payload}
    forbidden = sorted(lowered & set(FORBIDDEN_OUTPUT_FIELDS))
    if forbidden:
        raise SupervisorAuthorityViolation(
            f"supervisor output attempted to influence protected fields: {forbidden}")

    raw_finding = payload.get("finding")
    try:
        finding = SupervisorFinding(str(raw_finding).strip().upper())
    except Exception as exc:
        raise SupervisorAuthorityViolation(
            f"supervisor finding {raw_finding!r} is outside the permitted vocabulary") from exc

    refs = payload.get("evidence_refs") or ()
    if isinstance(refs, str) or not isinstance(refs, (list, tuple)):
        raise SupervisorAuthorityViolation("evidence_refs must be a list of references")
    refs = tuple(str(value) for value in refs)
    if finding in (SupervisorFinding.CAUTION, SupervisorFinding.INVESTIGATE,
                   SupervisorFinding.SUPERVISOR_HALT_REQUEST) and not refs:
        raise SupervisorAuthorityViolation("a non-NORMAL finding must cite evidence references")

    explanation = str(payload.get("explanation") or "")[:2000]
    return SupervisorOutcome(finding=finding, evidence_refs=refs, explanation=explanation)


# ---------------------------------------------------------------------------
# Provider abstraction (spec section 10/12)
#
# AI supervision is a STRUCTURED optional provider, never a required network hop for an order.
# Whatever the provider is - a hosted model, a local model, or a deterministic rule engine - it
# receives only a sanitized packet and returns only a structured finding. Execution integrity does
# not depend on it being reachable.
# ---------------------------------------------------------------------------


class SupervisorProvider(ABC):
    """Structured supervision provider. Implementations must not be able to order anything."""

    provider_id: str = "abstract"

    @abstractmethod
    def evaluate(self, packet: SanitizedEvidencePacket) -> Mapping[str, Any]:
        """Return a structured finding payload: finding, evidence_refs, explanation."""


class RuleBasedSupervisorProvider(SupervisorProvider):
    """Deterministic, network-free supervision.

    It exists so the supervision PIPELINE is real and testable without making an LLM a required
    hop for every order. It reads only whitelisted evidence fields and can only ever tighten:
    the worst finding it can emit is SUPERVISOR_HALT_REQUEST, which blocks new exposure.

    Field convention: every field is a PROBLEM INDICATOR. ``False``, ``0``, an empty collection, or
    ``"OK"``/``"CLEAN"``/``"NONE"``/``"PASS"``/``"VERIFIED"`` means no problem. Anything else means
    a problem. An ABSENT field is missing evidence, which escalates to INVESTIGATE - never to a
    quiet NORMAL, and never treated as clean.
    """

    provider_id = "rule_based_v1"

    #: Evidence field -> (finding when non-empty, explanation). Ordered worst-first.
    _RULES: Tuple[Tuple[str, SupervisorFinding, str], ...] = (
        ("broker_health", SupervisorFinding.SUPERVISOR_HALT_REQUEST,
         "broker health is not clean"),
        ("reconciliation_discrepancies", SupervisorFinding.SUPERVISOR_HALT_REQUEST,
         "unresolved reconciliation discrepancy between local and broker state"),
        ("build_config_integrity", SupervisorFinding.SUPERVISOR_HALT_REQUEST,
         "build or configuration integrity is not verified"),
        ("market_data_anomalies", SupervisorFinding.INVESTIGATE,
         "market-data anomaly reported by the Truth Engine"),
        ("guardian_incidents", SupervisorFinding.INVESTIGATE,
         "Guardian recorded an incident requiring review"),
        ("unexpected_behaviour_patterns", SupervisorFinding.INVESTIGATE,
         "unexpected behavioural pattern detected"),
        ("performance_deterioration", SupervisorFinding.CAUTION,
         "performance is deteriorating against expectation"),
        ("rejections", SupervisorFinding.CAUTION, "broker rejections were recorded"),
    )

    def evaluate(self, packet: SanitizedEvidencePacket) -> Mapping[str, Any]:
        fields = dict(packet.fields)
        refs = [f"generated_at={packet.generated_at}"]
        missing: list = []

        for field_name, finding, explanation in self._RULES:
            if field_name not in fields:
                missing.append(field_name)
                continue
            if not _tripped(fields[field_name]):
                continue
            return {
                "finding": finding.value,
                "evidence_refs": [f"evidence:{field_name}", *refs],
                "explanation": f"{explanation} (field={field_name}, provider={self.provider_id})",
            }
        for field_name in ("risk_utilization", "capital_utilization"):
            if field_name not in fields:
                missing.append(field_name)
            elif _utilization_elevated(fields[field_name]):
                return {
                    "finding": SupervisorFinding.CAUTION.value,
                    "evidence_refs": [f"evidence:{field_name}", *refs],
                    "explanation": (f"owner-approved utilization is close to its ceiling "
                                    f"(field={field_name}, provider={self.provider_id})"),
                }
        if missing:
            return {
                "finding": SupervisorFinding.INVESTIGATE.value,
                "evidence_refs": [f"missing_evidence:{name}" for name in missing] + refs,
                "explanation": (f"supervision is operating on partial evidence; missing "
                                f"{missing}. Missing evidence is not clean evidence."),
            }
        return {
            "finding": SupervisorFinding.NORMAL.value,
            "evidence_refs": [],
            "explanation": f"no rule tripped over {len(fields)} whitelisted evidence field(s)",
        }


class UnavailableSupervisorProvider(SupervisorProvider):
    """Explicitly unavailable provider, for exercising the outage path honestly."""

    provider_id = "unavailable"

    def __init__(self, reason: str = "provider not configured") -> None:
        self._reason = reason

    def evaluate(self, packet: SanitizedEvidencePacket) -> Mapping[str, Any]:
        raise ExecutionLayerError(self._reason)


def _tripped(value: Any) -> bool:
    """Is this problem indicator reporting a problem? Only an explicit clean value is clean."""
    if value is None or value is True:
        return True
    if value is False:
        return False
    if isinstance(value, str):
        return value.strip().upper() not in {"OK", "CLEAN", "NONE", "PASS", "VERIFIED"}
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return bool(value)
    return True


def _utilization_elevated(value: Any) -> bool:
    if isinstance(value, Mapping):
        used = value.get("used_pct", value.get("utilization_pct"))
    else:
        used = value
    return isinstance(used, (int, float)) and not isinstance(used, bool) and used >= 0.8


@dataclass(frozen=True)
class SupervisorPolicy:
    """Explicit outage policy. It is never inferred and never defaults to permissive."""

    block_new_exposure_when_unavailable: bool = True
    block_new_exposure_on_investigate: bool = False

    def describe(self) -> Dict[str, Any]:
        return {
            "block_new_exposure_when_unavailable": self.block_new_exposure_when_unavailable,
            "block_new_exposure_on_investigate": self.block_new_exposure_on_investigate,
            "note": ("AI availability is never a required hop for an order. The configured policy "
                     "decides during an outage, and absence of supervision is never read as approval."),
        }


class SupervisorRunner:
    """Runs supervision without ever letting it influence execution except by tightening."""

    def __init__(self, *, provider: SupervisorProvider,
                 policy: SupervisorPolicy | None = None) -> None:
        self._provider = provider
        self._policy = policy or SupervisorPolicy()

    @property
    def policy(self) -> SupervisorPolicy:
        return self._policy

    @property
    def provider_id(self) -> str:
        return getattr(self._provider, "provider_id", "unknown")

    def run(self, packet: SanitizedEvidencePacket) -> SupervisorOutcome:
        """Evaluate supervision. A provider fault becomes SUPERVISOR_UNAVAILABLE, never approval."""
        try:
            payload = self._provider.evaluate(packet)
        except SupervisorAuthorityViolation:
            raise
        except Exception as exc:
            return availability_outcome(f"{self.provider_id} failed: {type(exc).__name__}")
        try:
            return interpret_supervisor_output(payload)
        except SupervisorAuthorityViolation:
            raise
        except Exception as exc:
            return availability_outcome(f"{self.provider_id} returned unusable output: {type(exc).__name__}")

    def permits_new_exposure(self, outcome: SupervisorOutcome) -> Dict[str, Any]:
        """One-way safety decision. Only ever tightens, never releases."""
        if outcome.availability == SUPERVISOR_UNAVAILABLE:
            blocked = self._policy.block_new_exposure_when_unavailable
            return {"permitted": not blocked, "code": SUPERVISOR_UNAVAILABLE, "blocked": blocked,
                    "reasons": ["supervisor unavailable and policy blocks new exposure"] if blocked else []}
        blocking_findings = {SupervisorFinding.SUPERVISOR_HALT_REQUEST}
        if self._policy.block_new_exposure_on_investigate:
            blocking_findings.add(SupervisorFinding.INVESTIGATE)
        if outcome.finding in blocking_findings:
            return {"permitted": False, "code": outcome.finding.value, "blocked": True,
                    "reasons": [outcome.explanation or outcome.finding.value]}
        return {"permitted": True, "code": outcome.finding.value, "blocked": False, "reasons": []}


class SafetyController:
    """Deterministic translation of supervisor findings. Blocks NEW exposure only."""

    def __init__(self) -> None:
        self._halt_requested = False
        self._history: list[Dict[str, Any]] = []

    @property
    def halt_requested(self) -> bool:
        return self._halt_requested

    def apply(self, outcome: SupervisorOutcome) -> Dict[str, Any]:
        if outcome.availability == SUPERVISOR_UNAVAILABLE:
            self._history.append({"availability": SUPERVISOR_UNAVAILABLE,
                                  "new_exposure_blocked": False,
                                  "note": "configured policy decides; absence is not approval"})
            return {"action": SUPERVISOR_UNAVAILABLE, "new_exposure_blocked": False,
                    "cancels_orders": False, "resumes_trading": False}

        if outcome.finding is SupervisorFinding.SUPERVISOR_HALT_REQUEST:
            self._halt_requested = True
            self._history.append({"finding": outcome.finding.value, "new_exposure_blocked": True})
            return {"action": "BLOCK_NEW_EXPOSURE", "new_exposure_blocked": True,
                    "cancels_orders": False, "resumes_trading": False}

        self._history.append({"finding": outcome.finding.value, "new_exposure_blocked": False})
        return {"action": "ADVISORY_ONLY", "new_exposure_blocked": False,
                "cancels_orders": False, "resumes_trading": False}

    def resume(self, *, actor: str, deterministic_recovery_verified: bool,
               owner_authorized: bool) -> Dict[str, Any]:
        """Resumption is NOT a supervisor power. It needs recovery evidence and owner authority."""
        if actor == "SUPERVISOR":
            return {"resumed": False, "code": "SUPERVISOR_CANNOT_RESUME"}
        if not deterministic_recovery_verified:
            return {"resumed": False, "code": "RECOVERY_NOT_PROVEN"}
        if not owner_authorized:
            return {"resumed": False, "code": "OWNER_AUTHORIZATION_REQUIRED"}
        self._halt_requested = False
        return {"resumed": True, "code": "RESUMED_UNDER_OWNER_AUTHORITY"}

    def history(self) -> Sequence[Dict[str, Any]]:
        return tuple(self._history)
