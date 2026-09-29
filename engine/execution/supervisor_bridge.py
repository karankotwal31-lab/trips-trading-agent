"""The Supervisor production/provider bridge - actually wired, actually testable.

There was a provider abstraction (``RuleBasedSupervisorProvider``), a runner, a policy and a
``SafetyController``, and no bridge connecting them to the running system. Everything was
importable; nothing was connected. That is the worst state of all: it reads like supervision and
behaves like absence.

This module is that bridge, and it is deliberately boring:

* :class:`SupervisorEvidenceCollector` gathers facts from things that can actually be observed in
  this process - broker health, reconciliation, build/config integrity, Truth data anomalies,
  Guardian health, utilization - and hands them to a :class:`SanitizedEvidencePacket`, which
  refuses any field that is not on the allowlist. It reads. It never orders, cancels, resumes or
  promotes, and the object it is handed is a restricted read-only view, not the adapter itself.
* :class:`ProductionSupervisorBridge` runs the configured provider over that packet and translates
  the outcome through :class:`SafetyController`.

Two structural guarantees, not just policy statements:

* **One-way.** The bridge's return type is a supervisor outcome and a safety action. There is no
  method on this class that resumes trading, cancels an order, raises a limit or changes a broker.
  Resumption lives on ``SafetyController.resume`` and requires owner authority plus proven recovery.
* **Outside broker authority.** The collector is constructed with a ``ReadOnlyBrokerObservation``,
  which exposes exactly four read calls. It has no submit and no cancel, so the supervision path
  cannot reach broker authority even if it wanted to.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from .contracts import ExecutionLayerError, canonical_json
from .supervisor import (EVIDENCE_ALLOWLIST, SanitizedEvidencePacket, SafetyController,
                         SupervisorOutcome, SupervisorPolicy, SupervisorProvider, SupervisorRunner)


class SupervisorBridgeError(ExecutionLayerError):
    """The supervisor bridge was asked to do something outside its one-way safety authority."""


class ReadOnlyBrokerObservation(Protocol):
    """The only broker surface the supervision path is allowed to see.

    Read-only by construction: submit, cancel and every other mutating adapter method are simply
    not part of this interface, so the supervision path has nothing to escalate to.
    """

    def health(self) -> Any: ...

    def account(self) -> Any: ...

    def positions(self) -> Sequence[Any]: ...

    def open_orders(self) -> Sequence[Mapping[str, Any]]: ...


class SupervisorEvidenceCollector:
    """Builds one allowlisted evidence packet from observable, in-process facts."""

    def __init__(self, *, broker: Optional[ReadOnlyBrokerObservation] = None,
                 reconciliation: Any = None, truth: Optional[Mapping[str, Any]] = None,
                 build_and_config_verified: Optional[bool] = None,
                 guardian_status: Optional[str] = None,
                 guardian_incidents: Sequence[Any] = (),
                 risk_utilization: Any = None, capital_utilization: Any = None,
                 recent_decisions: Sequence[Any] = (),
                 system_health: Any = None,
                 unexpected_behaviour_patterns: Sequence[Any] = (),
                 performance_deterioration: Any = None,
                 rejections: Sequence[Any] = ()) -> None:
        self._broker = broker
        self._reconciliation = reconciliation
        self._truth = dict(truth) if truth else None
        self._build_and_config_verified = build_and_config_verified
        self._guardian_status = guardian_status
        self._guardian_incidents = tuple(guardian_incidents)
        self._risk_utilization = risk_utilization
        self._capital_utilization = capital_utilization
        self._recent_decisions = tuple(recent_decisions)
        self._system_health = system_health
        self._unexpected_behaviour_patterns = tuple(unexpected_behaviour_patterns)
        self._performance_deterioration = performance_deterioration
        self._rejections = tuple(rejections)

    # -- individual facts ------------------------------------------------

    def broker_health(self) -> Any:
        if self._broker is None:
            return "UNKNOWN"
        try:
            health = self._broker.health()
        except Exception as exc:
            return f"BROKER_UNREACHABLE:{type(exc).__name__}"
        if not getattr(health, "healthy", False):
            return "UNHEALTHY"
        return "OK"

    def reconciliation_discrepancies(self) -> Sequence[str]:
        """Codes from an ALREADY-COMPUTED reconciliation result.

        The bridge never performs a reconciliation itself. It is given the result the execution path
        already produced, because recomputing it here would be a second, possibly divergent, view
        of broker state.
        """
        result = self._reconciliation
        if result is None:
            return ()
        codes = getattr(result, "codes", None)
        if codes is None and isinstance(result, Mapping):
            codes = result.get("codes")
        return tuple(codes or ())

    def market_data_anomalies(self) -> Sequence[str]:
        if not self._truth:
            return ()
        anomalies = []
        if not self._truth.get("trusted_for_analysis", False):
            anomalies.append("TRUTH_NOT_ANALYSIS_ELIGIBLE")
        if not self._truth.get("trusted_for_trade", False):
            anomalies.append("TRUTH_NOT_TRADE_ELIGIBLE")
        for check in self._truth.get("checks", ()):  # type: ignore[union-attr]
            if isinstance(check, Mapping) and not check.get("passed", True):
                anomalies.append(str(check.get("name") or "UNKNOWN_CHECK"))
        return tuple(sorted(set(anomalies)))

    def build_config_integrity(self) -> Any:
        if self._build_and_config_verified is None:
            return "UNVERIFIED"
        return "VERIFIED" if self._build_and_config_verified else "TAMPERED"

    # -- packet ----------------------------------------------------------

    def collect(self, *, now: Optional[datetime] = None) -> SanitizedEvidencePacket:
        now = now or datetime.now(timezone.utc)
        fields: Dict[str, Any] = {
            "system_health": self._system_health if self._system_health is not None else "OK",
            "broker_health": self.broker_health(),
            "recent_decisions": list(self._recent_decisions),
            "risk_utilization": (self._risk_utilization if self._risk_utilization is not None
                                 else "UNKNOWN"),
            "capital_utilization": (self._capital_utilization
                                    if self._capital_utilization is not None else "UNKNOWN"),
            "order_lifecycle": {"open_orders": self._open_order_count()},
            "rejections": list(self._rejections),
            "reconciliation_discrepancies": list(self.reconciliation_discrepancies()),
            "market_data_anomalies": list(self.market_data_anomalies()),
            "unexpected_behaviour_patterns": list(self._unexpected_behaviour_patterns),
            "build_config_integrity": self.build_config_integrity(),
            "guardian_incidents": list(self._guardian_incidents),
            "performance_deterioration": (self._performance_deterioration
                                          if self._performance_deterioration is not None else "OK"),
        }
        unexpected = sorted(set(fields) - set(EVIDENCE_ALLOWLIST))
        if unexpected:  # pragma: no cover - a programming error, not a runtime condition
            raise SupervisorBridgeError(f"collector produced non-allowlisted fields: {unexpected}")
        return SanitizedEvidencePacket(fields=fields, generated_at=now.isoformat())

    def _open_order_count(self) -> int:
        if self._broker is None:
            return 0
        try:
            return len(list(self._broker.open_orders()))
        except Exception:
            return 0


class ProductionSupervisorBridge:
    """Wires collector -> provider -> safety controller, one way, with no broker authority.

    ``SafetyController`` is the only sink. It can block new exposure; it cannot resume, cannot
    cancel and cannot widen anything, so the strongest thing this bridge can ever do is stop.
    """

    def __init__(self, *, provider: SupervisorProvider, collector: SupervisorEvidenceCollector,
                 safety: Optional[SafetyController] = None,
                 policy: Optional[SupervisorPolicy] = None) -> None:
        self._runner = SupervisorRunner(provider=provider, policy=policy)
        self._collector = collector
        self._safety = safety or SafetyController()

    @property
    def runner(self) -> SupervisorRunner:
        return self._runner

    @property
    def safety(self) -> SafetyController:
        return self._safety

    @property
    def provider_id(self) -> str:
        return self._runner.provider_id

    def run_once(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        packet = self._collector.collect(now=now)
        outcome = self._runner.run(packet)
        decision = self._runner.permits_new_exposure(outcome)
        action = self._safety.apply(outcome)
        return {
            "provider_id": self.provider_id,
            "packet": packet.to_dict(),
            "outcome": outcome.to_dict(),
            "new_exposure": decision,
            "safety_action": action,
            "packet_hash": _packet_hash(packet),
            "authority": {
                "execution_authority": "NONE",
                "may_only_tighten": True,
                "note": ("This bridge can only block. It holds no submit, cancel, resume or "
                         "promotion path, and the SafetyController it writes to exposes none."),
            },
        }

    def supervise(self, *, now: Optional[datetime] = None) -> SupervisorOutcome:
        """The outcome alone, for callers that manage the safety controller themselves."""
        return self._runner.run(self._collector.collect(now=now))


def _packet_hash(packet: SanitizedEvidencePacket) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(packet.to_dict())).hexdigest()


def default_supervisor_bridge(*, provider: Optional[SupervisorProvider] = None,
                              broker: Optional[ReadOnlyBrokerObservation] = None,
                              reconciliation: Any = None,
                              truth: Optional[Mapping[str, Any]] = None,
                              build_and_config_verified: Optional[bool] = None,
                              safety: Optional[SafetyController] = None,
                              policy: Optional[SupervisorPolicy] = None,
                              **facts: Any) -> ProductionSupervisorBridge:
    """Build the production bridge from the deterministic rule provider unless told otherwise.

    The rule provider is the default on purpose: supervision must never be a required network hop
    for an order, and a deterministic provider is the only kind whose behaviour can be asserted.
    """
    from .supervisor import RuleBasedSupervisorProvider

    collector = SupervisorEvidenceCollector(broker=broker, reconciliation=reconciliation,
                                            truth=truth,
                                            build_and_config_verified=build_and_config_verified,
                                            **facts)
    return ProductionSupervisorBridge(provider=provider or RuleBasedSupervisorProvider(),
                                      collector=collector, safety=safety, policy=policy)


__all__ = [
    "ProductionSupervisorBridge",
    "ReadOnlyBrokerObservation",
    "SupervisorBridgeError",
    "SupervisorEvidenceCollector",
    "default_supervisor_bridge",
]
