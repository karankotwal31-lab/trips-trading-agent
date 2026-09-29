"""Per-capability conformance EVIDENCE, not a conformance flag.

The previous design was one boolean per adapter: a single flag flipped every capability in
``CORE_CAPABILITIES`` to SUPPORTED. That is wrong twice over.

* It is a claim, not an observation. ``True`` said nothing about *which* capability was exercised,
  against *which* interface, in *which* environment, by *which* run, at *what* time.
* It was unfalsifiable in the dangerous direction. One boolean covering fifteen capabilities means
  a single passing probe - or a single operator flip - could mark order submission, cancel and
  reconciliation all SUPPORTED.

So each capability now carries its own **typed, versioned, self-verifying record**: a status, the
exact interface it was observed on, the environment it was observed in, the observation payload,
who observed it, when, and a SHA-256 digest over all of that. ``resolve()`` recomputes the status
from those facts every time it is asked, rather than trusting the recorded one:

* a record whose digest does not match its own content is not evidence,
* a record from a future schema version is not evidence (it may mean something else),
* a stale record is not evidence,
* a record observed against a different interface or environment is not evidence for this one,
* a recorded SUPPORTED that fails any of the above degrades to UNVERIFIED, and UNVERIFIED fails
  closed.

A recorded ``UNSUPPORTED`` is a *proven negative* and is preserved: a broker genuinely cannot do
something, and repeatedly re-probing it is not the way to find out.

MANDATE COMPATIBILITY IS SEPARATE, ON PURPOSE
---------------------------------------------
A broker can be perfectly conformant and still be the wrong broker. Upstox's v3 API is a
documented, machine-callable Indian-market interface: it can pass every conformance probe here.
None of that makes it eligible to trade SPY, QQQ and AAPL. ``MandateEvidence`` records which
instruments and which asset class a broker was actually observed to serve, and mandate
compatibility is checked against that record alone. Generic conformance can never override it -
there is no code path that turns "all fifteen capabilities SUPPORTED" into "SPY is tradeable here".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .contracts import (APPROVED_INSTRUMENT_SCOPE, CORE_CAPABILITIES,  # noqa: E402
                        OPTIONAL_CAPABILITIES, CapabilityMatrix, CapabilityStatus,
                        ExecutionLayerError, canonical_json)
from .live_verification import EVIDENCE_KIND_LIVE_READ_ONLY, EVIDENCE_KIND_RECORDED  # noqa: E402

#: Bumped whenever the shape or meaning of a record changes. An unknown version is not evidence.
CONFORMANCE_EVIDENCE_VERSION = 1

#: What a conformance record made through this suite actually proves.
#:
#: A conformance run replays the real channel, the real URL construction and the real
#: normalization against a recorded broker answer. That is genuine, valuable engineering evidence -
#: and it is evidence of exactly one thing: **implementation behaviour**. It is stamped
#: ``RECORDED_CONTRACT_CONFORMANCE`` so it can never be read back as evidence about a live
#: brokerage account, which requires ``LIVE_READ_ONLY_BROKER_VERIFICATION`` instead
#: (see :mod:`execution.live_verification`). Neither releases capital.
EVIDENCE_KIND = EVIDENCE_KIND_RECORDED

#: The deployment's instrument mandate. Taken from the approved instrument scope constant, never
#: restated here, so there is no second authority.
MANDATE_ID = APPROVED_INSTRUMENT_SCOPE

#: How long a conformance observation stays usable. Conformance facts about a broker's REST API do
#: not go stale in seconds, but they do go stale eventually.
DEFAULT_MAX_EVIDENCE_AGE = timedelta(days=30)


class ConformanceEvidenceError(ExecutionLayerError):
    """A conformance record is malformed or cannot be trusted."""


def _sha(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _aware(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise ConformanceEvidenceError(f"conformance record field {name} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ConformanceEvidenceError(f"conformance record field {name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class CapabilityEvidence:
    """Evidence about exactly one capability, on exactly one interface, in one environment."""

    capability: str
    status: CapabilityStatus
    interface: str
    environment: str
    observed_by: str
    observed_at: str
    observation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CONFORMANCE_EVIDENCE_VERSION
    evidence_kind: str = EVIDENCE_KIND

    def __post_init__(self) -> None:
        if not str(self.capability).strip():
            raise ConformanceEvidenceError("conformance evidence requires a capability name")
        for name in ("interface", "environment", "observed_by", "evidence_kind"):
            if not str(getattr(self, name)).strip():
                raise ConformanceEvidenceError(f"conformance evidence requires {name}")
        if self.evidence_kind not in (EVIDENCE_KIND, EVIDENCE_KIND_LIVE_READ_ONLY):
            raise ConformanceEvidenceError(
                f"unknown conformance evidence kind {self.evidence_kind!r}")
        _aware(self.observed_at, "observed_at")
        object.__setattr__(self, "status",
                           self.status if isinstance(self.status, CapabilityStatus)
                           else CapabilityStatus(str(self.status)))
        object.__setattr__(self, "observation", dict(self.observation))

    # -- integrity -------------------------------------------------------

    def digest(self) -> str:
        return _sha(self._body())

    def _body(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status.value,
            "interface": self.interface,
            "environment": self.environment,
            "observed_by": self.observed_by,
            "observed_at": self.observed_at,
            "observation": dict(self.observation),
            "schema_version": self.schema_version,
            "evidence_kind": EVIDENCE_KIND,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {**self._body(), "evidence_hash": self.digest()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapabilityEvidence":
        if not isinstance(payload, Mapping):
            raise ConformanceEvidenceError("conformance evidence must be a mapping")
        try:
            body = {name: payload[name] for name in (
                "capability", "status", "interface", "environment", "observed_by",
                "observed_at", "observation", "schema_version", "evidence_kind")}
        except KeyError as exc:
            raise ConformanceEvidenceError(f"conformance evidence is missing {exc}") from exc
        record = cls(**body)
        if payload.get("evidence_hash") != record.digest():
            raise ConformanceEvidenceError(
                f"conformance evidence for {body['capability']!r} does not match its own digest")
        return record

    # -- resolution -----------------------------------------------------

    def resolve(self, *, now: Optional[datetime] = None, max_age: timedelta = DEFAULT_MAX_EVIDENCE_AGE,
                interface: Optional[str] = None,
                environment: Optional[str] = None) -> Tuple[CapabilityStatus, Tuple[str, ...]]:
        """Recompute the status from the evidence itself. Never returns the recorded value on trust."""
        now = now or datetime.now(timezone.utc)
        if self.status is CapabilityStatus.UNSUPPORTED:
            return CapabilityStatus.UNSUPPORTED, ()
        if self.schema_version != CONFORMANCE_EVIDENCE_VERSION:
            return CapabilityStatus.UNVERIFIED, (
                f"evidence schema v{self.schema_version} is not the supported "
                f"v{CONFORMANCE_EVIDENCE_VERSION}")
        if self.evidence_kind != EVIDENCE_KIND:
            return CapabilityStatus.UNVERIFIED, (
                f"evidence kind {self.evidence_kind!r} is not {EVIDENCE_KIND!r}")
        if environment == "live" and EVIDENCE_KIND != EVIDENCE_KIND_LIVE_READ_ONLY:
            # The distinction is the whole point of removing paper trading: a recorded answer is
            # not evidence about a real account, and asking for it as such must fail closed.
            return CapabilityStatus.UNVERIFIED, (
                f"{EVIDENCE_KIND} proves implementation behaviour only; live compatibility "
                f"requires {EVIDENCE_KIND_LIVE_READ_ONLY}")
        if interface is not None and self.interface != interface:
            return CapabilityStatus.UNVERIFIED, (
                f"observed on interface {self.interface!r}, not {interface!r}")
        if environment is not None and self.environment != environment:
            return CapabilityStatus.UNVERIFIED, (
                f"observed in environment {self.environment!r}, not {environment!r}")
        try:
            observed = _aware(self.observed_at, "observed_at")
        except ConformanceEvidenceError as exc:
            return CapabilityStatus.UNVERIFIED, (str(exc),)
        if observed > now + timedelta(minutes=5):
            return CapabilityStatus.UNVERIFIED, ("evidence is dated in the future")
        if now - observed > max_age:
            return CapabilityStatus.UNVERIFIED, (
                f"evidence is {(now - observed).days}d old; limit is {max_age.days}d")
        if not self.observation.get("probe"):
            return CapabilityStatus.UNVERIFIED, ("evidence records no probe result")
        if self.observation.get("exercised") is not True:
            return CapabilityStatus.UNVERIFIED, (
                f"the probe was not exercised: {self.observation.get('reason', 'no reason given')}")
        return CapabilityStatus.SUPPORTED, ()


@dataclass(frozen=True)
class MandateEvidence:
    """Which instruments and asset class this broker was actually observed to serve.

    Separate from conformance on purpose. A broker can be fully conformant for the Indian cash
    segment and be entirely ineligible for a US-equities mandate; those are different facts and
    this record is the only place the second one is stated.
    """

    broker_id: str
    mandate_id: str
    instruments: Tuple[str, ...]
    asset_class: str
    environment: str
    observed_by: str
    observed_at: str
    observation: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CONFORMANCE_EVIDENCE_VERSION
    evidence_kind: str = EVIDENCE_KIND

    def __post_init__(self) -> None:
        if not str(self.broker_id).strip() or not str(self.mandate_id).strip():
            raise ConformanceEvidenceError("mandate evidence requires a broker and a mandate")
        if self.evidence_kind not in (EVIDENCE_KIND, EVIDENCE_KIND_LIVE_READ_ONLY):
            raise ConformanceEvidenceError(
                f"unknown mandate evidence kind {self.evidence_kind!r}")
        object.__setattr__(self, "instruments",
                           tuple(sorted({str(symbol).strip().upper() for symbol in self.instruments})))
        _aware(self.observed_at, "observed_at")
        object.__setattr__(self, "observation", dict(self.observation))

    def digest(self) -> str:
        return _sha(self._body())

    def _body(self) -> Dict[str, Any]:
        return {
            "broker_id": self.broker_id,
            "mandate_id": self.mandate_id,
            "instruments": list(self.instruments),
            "asset_class": self.asset_class,
            "environment": self.environment,
            "observed_by": self.observed_by,
            "observed_at": self.observed_at,
            "observation": dict(self.observation),
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {**self._body(), "evidence_hash": self.digest()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MandateEvidence":
        if not isinstance(payload, Mapping):
            raise ConformanceEvidenceError("mandate evidence must be a mapping")
        try:
            body = {name: payload[name] for name in (
                "broker_id", "mandate_id", "instruments", "asset_class", "environment",
                "observed_by", "observed_at", "observation", "schema_version", "evidence_kind")}
        except KeyError as exc:
            raise ConformanceEvidenceError(f"mandate evidence is missing {exc}") from exc
        record = cls(**body)
        if payload.get("evidence_hash") != record.digest():
            raise ConformanceEvidenceError(
                f"mandate evidence for {body['broker_id']!r} does not match its own digest")
        return record

    def resolve(self, *, now: Optional[datetime] = None, max_age: timedelta = DEFAULT_MAX_EVIDENCE_AGE,
                environment: Optional[str] = None) -> Tuple[bool, Tuple[str, ...]]:
        now = now or datetime.now(timezone.utc)
        if self.schema_version != CONFORMANCE_EVIDENCE_VERSION:
            return False, (f"mandate evidence schema v{self.schema_version} is not supported",)
        if environment == "live" and self.evidence_kind != EVIDENCE_KIND_LIVE_READ_ONLY:
            return False, (f"{self.evidence_kind} proves implementation behaviour only; which "
                           f"instruments a REAL account may trade requires "
                           f"{EVIDENCE_KIND_LIVE_READ_ONLY}",)
        if environment is not None and self.environment != environment:
            return False, (f"mandate evidence is from environment {self.environment!r}, "
                           f"not {environment!r}",)
        try:
            observed = _aware(self.observed_at, "observed_at")
        except ConformanceEvidenceError as exc:
            return False, (str(exc),)
        if now - observed > max_age:
            return False, (f"mandate evidence is {(now - observed).days}d old",)
        return True, ()


@dataclass(frozen=True)
class ConformanceEvidence:
    """A complete, independently-resolving conformance document for one broker environment."""

    broker_id: str
    environment: str
    suite: str
    generated_at: str
    records: Mapping[str, CapabilityEvidence] = field(default_factory=dict)
    mandate: Optional[MandateEvidence] = None
    schema_version: int = CONFORMANCE_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", dict(self.records))
        _aware(self.generated_at, "generated_at")

    def digest(self) -> str:
        return _sha(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": EVIDENCE_KIND,
            "broker_id": self.broker_id,
            "environment": self.environment,
            "suite": self.suite,
            "generated_at": self.generated_at,
            "records": {name: record.to_dict() for name, record in sorted(self.records.items())},
            "mandate": self.mandate.to_dict() if self.mandate is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ConformanceEvidence":
        if not isinstance(payload, Mapping):
            raise ConformanceEvidenceError("conformance evidence document must be a mapping")
        records = {}
        for name, record in dict(payload.get("records") or {}).items():
            records[name] = CapabilityEvidence.from_dict(record)
        mandate = payload.get("mandate")
        return cls(broker_id=payload["broker_id"], environment=payload["environment"],
                   suite=payload["suite"], generated_at=payload["generated_at"],
                   records=records,
                   mandate=MandateEvidence.from_dict(mandate) if mandate else None,
                   schema_version=int(payload.get("schema_version", CONFORMANCE_EVIDENCE_VERSION)))

    # -- per-capability resolution --------------------------------------

    def status(self, capability: str, *, now: Optional[datetime] = None,
               interface: Optional[str] = None,
               environment: Optional[str] = None) -> CapabilityStatus:
        record = self.records.get(capability)
        if record is None:
            return CapabilityStatus.UNVERIFIED
        if interface is None and environment is None:
            interface, environment = record.interface, record.environment
        return record.resolve(now=now, interface=interface, environment=environment)[0]

    def resolution(self, capability: str, *, now: Optional[datetime] = None,
                   interface: Optional[str] = None,
                   environment: Optional[str] = None) -> Dict[str, Any]:
        record = self.records.get(capability)
        if record is None:
            return {"capability": capability, "status": CapabilityStatus.UNVERIFIED.value,
                    "reasons": ["no conformance evidence has been recorded for this capability"],
                    "evidence": None}
        status, reasons = record.resolve(now=now, interface=interface, environment=environment)
        return {"capability": capability, "status": status.value, "reasons": list(reasons),
                "evidence": record.to_dict()}

    def matrix(self, *, now: Optional[datetime] = None) -> CapabilityMatrix:
        statuses = {name: self.status(name, now=now) for name in
                    tuple(CORE_CAPABILITIES) + tuple(OPTIONAL_CAPABILITIES)}
        return CapabilityMatrix(statuses=statuses,
                                source=f"conformance_evidence:{self.broker_id}:{self.suite}")

    def missing_core(self, *, now: Optional[datetime] = None) -> Tuple[str, ...]:
        return tuple(name for name in CORE_CAPABILITIES
                     if not self.status(name, now=now).permits_execution)

    def is_complete(self) -> bool:
        """Every core and optional capability has its own record, or it is not a complete run."""
        names = set(self.records)
        return set(CORE_CAPABILITIES) <= names and set(OPTIONAL_CAPABILITIES) <= names

    # -- mandate --------------------------------------------------------

    def mandate_verdict(self, scope: Iterable[str], *, now: Optional[datetime] = None,
                        environment: Optional[str] = None) -> Dict[str, Any]:
        wanted = tuple(sorted({str(symbol).strip().upper() for symbol in scope}))
        if self.mandate is None:
            return {"permitted": False, "mandate_id": MANDATE_ID, "missing": list(wanted),
                    "reasons": [
                        f"no mandate evidence has been recorded for {self.broker_id}; "
                        f"interface conformance does not establish instrument eligibility"]}
        reasons: list = []
        if self.mandate.mandate_id != MANDATE_ID:
            reasons.append(
                f"broker was observed serving {self.mandate.mandate_id} "
                f"({self.mandate.asset_class}), not {MANDATE_ID}")
        absent = [symbol for symbol in wanted if symbol not in self.mandate.instruments]
        if absent:
            reasons.append(f"broker was not observed serving {absent}")
        valid, stale = self.mandate.resolve(now=now, environment=environment)
        reasons.extend(stale)
        return {"permitted": not reasons, "mandate_id": MANDATE_ID,
                "broker_mandate_id": self.mandate.mandate_id, "missing": absent,
                "observed_instruments": list(self.mandate.instruments),
                "observed_asset_class": self.mandate.asset_class,
                "valid": valid, "reasons": reasons}


def mandate_verdict_for(adapter: Any, scope: Sequence[str]) -> Dict[str, Any]:
    """Mandate compatibility for any adapter, decided ONLY by mandate evidence.

    Generic conformance - even a fully SUPPORTED capability matrix - never appears in this
    verdict, by construction.
    """
    evidence = getattr(adapter, "conformance_evidence", None)
    if not isinstance(evidence, ConformanceEvidence):
        return {"permitted": False, "mandate_id": MANDATE_ID, "missing": sorted(set(scope)),
                "reasons": [
                    f"no per-capability conformance evidence is attached to "
                    f"{getattr(adapter, 'broker_id', 'unknown')}, so mandate compatibility is "
                    f"UNVERIFIED and fails closed"]}
    verdict = evidence.mandate_verdict(scope)
    verdict.setdefault("evidence_hash", evidence.mandate.digest() if evidence.mandate else "")
    return verdict


# ---------------------------------------------------------------------------
# The conformance suite
# ---------------------------------------------------------------------------

#: One probe per capability. Each probe receives the channel under test and returns a mapping of
#: what it actually observed. A probe that raises yields UNSUPPORTED - a proven negative - because
#: the interface demonstrably refused the call. A capability with no probe at all stays UNVERIFIED.
CAPABILITY_PROBES: Mapping[str, str] = {
    "broker_identity": "probe_broker_identity",
    "capability_discovery": "probe_capability_discovery",
    "auth_state": "probe_auth_state",
    "connection_health": "probe_connection_health",
    "broker_clock": "probe_broker_clock",
    "account": "probe_account",
    "buying_power": "probe_buying_power",
    "positions": "probe_positions",
    "open_orders": "probe_open_orders",
    "order_status": "probe_order_status",
    "recent_orders": "probe_recent_orders",
    "order_submission": "probe_order_submission",
    "order_cancel": "probe_order_cancel",
    "reconciliation": "probe_reconciliation",
    "disconnect": "probe_disconnect",
    "order_replace": "probe_order_replace",
    "order_preview": "probe_order_preview",
    "streaming_events": "probe_streaming_events",
}

PROBED_CAPABILITIES: Tuple[str, ...] = tuple(CAPABILITY_PROBES)


class NotExercised(Exception):
    """A probe was not run at all, because the operator did not enable it.

    Distinct from a refusal: "we were not allowed to try" is not "the broker cannot do it", and
    collapsing the two would let an unrun probe manufacture a proven negative.
    """


class ConformanceSuite:
    """Runs real probes against a real channel and records what happened, per capability.

    There is no blanket flag argument. The suite is the only thing that produces
    evidence, and it can only produce evidence about capabilities it actually exercised.
    """

    def __init__(self, *, broker_id: str, environment: str, channel: Any,
                 observed_by: Optional[str] = None,
                 mandate: Optional[MandateEvidence] = None) -> None:
        if not str(broker_id).strip():
            raise ConformanceEvidenceError("a conformance run must name its broker")
        self.broker_id = broker_id
        self.environment = environment
        self.channel = channel
        self.observed_by = observed_by or f"conformance_suite:{channel.__class__.__name__}"
        self.mandate = mandate

    def interface_for(self, capability: str) -> str:
        getter = getattr(self.channel, "interface_for", None)
        return str(getter(capability)) if callable(getter) else f"{self.broker_id}:unknown"

    def run(self, *, now: Optional[datetime] = None) -> ConformanceEvidence:
        now = now or datetime.now(timezone.utc)
        records: Dict[str, CapabilityEvidence] = {}
        for capability, probe_name in self._probe_order():
            probe: Optional[Callable[[], Mapping[str, Any]]] = getattr(self.channel, probe_name, None)
            if not callable(probe):
                # No probe means no observation. UNVERIFIED is the honest answer.
                records[capability] = CapabilityEvidence(
                    capability=capability, status=CapabilityStatus.UNVERIFIED,
                    interface=self.interface_for(capability), environment=self.environment,
                    observed_by=self.observed_by, observed_at=now.isoformat(),
                    observation={"probe": probe_name, "exercised": False,
                                 "reason": "channel exposes no probe for this capability"})
                continue
            try:
                observation = dict(probe() or {})
                observation.setdefault("probe", probe_name)
                observation["exercised"] = True
                status = CapabilityStatus.SUPPORTED
            except NotExercised as exc:
                # The operator did not let this probe run. That is not evidence the broker cannot
                # do it, so it is UNVERIFIED - never a proven negative.
                observation = {"probe": probe_name, "exercised": False,
                               "reason": str(exc)[:400]}
                status = CapabilityStatus.UNVERIFIED
            except Exception as exc:
                observation = {"probe": probe_name, "exercised": True, "outcome": "REFUSED",
                               "error_type": type(exc).__name__, "error": str(exc)[:400]}
                status = CapabilityStatus.UNSUPPORTED
            records[capability] = CapabilityEvidence(
                capability=capability, status=status, interface=self.interface_for(capability),
                environment=self.environment, observed_by=self.observed_by,
                observed_at=now.isoformat(), observation=observation)

        return ConformanceEvidence(broker_id=self.broker_id, environment=self.environment,
                                   suite=self.observed_by, generated_at=now.isoformat(),
                                   records=records, mandate=self.mandate)

    @staticmethod
    def _probe_order() -> Tuple[Tuple[str, str], ...]:
        """Deterministic probe order, with ``disconnect`` last.

        The disconnect probe closes the channel. Running it in alphabetical order would make every
        later probe fail for a reason that has nothing to do with the capability being measured.
        """
        ordered = sorted((capability, probe) for capability, probe in CAPABILITY_PROBES.items()
                         if capability != "disconnect")
        return (*ordered, ("disconnect", CAPABILITY_PROBES["disconnect"]))

    def summarize(self, evidence: ConformanceEvidence) -> Dict[str, Any]:
        supported = [name for name in sorted(CORE_CAPABILITIES)
                     if evidence.status(name) is CapabilityStatus.SUPPORTED]
        unsupported = [name for name in sorted(CORE_CAPABILITIES)
                       if evidence.status(name) is CapabilityStatus.UNSUPPORTED]
        unverified = [name for name in sorted(CORE_CAPABILITIES)
                      if evidence.status(name) is CapabilityStatus.UNVERIFIED]
        return {"supported": supported, "unsupported": unsupported, "unverified": unverified,
                "complete": evidence.is_complete(), "digest": evidence.digest(),
                "note": ("Each capability is resolved from its own record. A capability with no "
                         "record, a stale record, a mismatched interface or a mismatched "
                         "environment is UNVERIFIED, and UNVERIFIED fails closed.")}


__all__ = [
    "CAPABILITY_PROBES",
    "CONFORMANCE_EVIDENCE_VERSION",
    "DEFAULT_MAX_EVIDENCE_AGE",
    "EVIDENCE_KIND",
    "MANDATE_ID",
    "PROBED_CAPABILITIES",
    "CapabilityEvidence",
    "ConformanceEvidence",
    "ConformanceEvidenceError",
    "ConformanceSuite",
    "MandateEvidence",
    "NotExercised",
    "mandate_verdict_for",
]
