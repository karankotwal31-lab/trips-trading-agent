"""Adapter discovery, conformance and plugin classification.

Spec: a broker is automatable only when an authorized programmable interface exists, and a
ChatGPT/plugin connector is an OPTIONAL transport that must be classified by actual capability.
A natural-language claim such as "Order placed" is never evidence.

Only ``MACHINE_CALLABLE_EXECUTION`` over an authorized transport may reach the execution path.
Every other classification is refused, which is what stops an interactive assistant or a
read-only connector from becoming an architectural dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .contracts import (
    CORE_CAPABILITIES,
    BrokerAutomationUnsupported,
    CapabilityError,
    CapabilityMatrix,
    CapabilityStatus,
    TransportKind,
)


class PluginClassification(str, Enum):
    READ_ONLY = "READ_ONLY"
    INTERACTIVE_ASSISTANCE_ONLY = "INTERACTIVE_ASSISTANCE_ONLY"
    MACHINE_CALLABLE_EXECUTION = "MACHINE_CALLABLE_EXECUTION"
    UNSUPPORTED = "UNSUPPORTED"


#: Classifications that may never reach the execution path, with the reason recorded.
NON_EXECUTING_CLASSIFICATIONS: Mapping[PluginClassification, str] = {
    PluginClassification.READ_ONLY:
        "a read-only connector cannot transmit orders",
    PluginClassification.INTERACTIVE_ASSISTANCE_ONLY:
        "an interactive-assistance connector can present information but is not machine-callable",
    PluginClassification.UNSUPPORTED:
        "no authorized programmable interface exists for this integration",
}

#: Transports that are authorized because a documented, machine-callable interface exists.
AUTHORIZED_TRANSPORTS: Tuple[TransportKind, ...] = (
    TransportKind.REST,
    TransportKind.WEBSOCKET,
    TransportKind.OAUTH,
    TransportKind.TOKEN_SESSION,
    TransportKind.OFFICIAL_GATEWAY,
    TransportKind.FIX,
    TransportKind.MCP_CONNECTOR,
)

#: Techniques that are never acceptable evidence of execution or automation.
FORBIDDEN_AUTOMATION_TECHNIQUES: Tuple[str, ...] = (
    "screen_coordinate_automation",
    "dom_clicking_on_order_tickets",
    "captcha_bypass",
    "mfa_bypass",
    "stolen_session",
    "undocumented_private_api",
    "reverse_engineered_endpoint",
    "credential_replay",
    "natural_language_order_confirmation",
)


@dataclass(frozen=True)
class BrokerRegistration:
    broker_id: str
    adapter: Any
    transport: TransportKind
    classification: PluginClassification
    authorized_interface: bool
    authorized_interface_evidence: str = ""
    environment: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "broker_id": self.broker_id,
            "transport": self.transport.value,
            "classification": self.classification.value,
            "authorized_interface": self.authorized_interface,
            "authorized_interface_evidence": self.authorized_interface_evidence,
            "environment": self.environment,
        }


class UnauthorizedInterface(RuntimeError):
    """A registration is not permitted to reach the execution path."""


@dataclass
class AdapterRegistry:
    _registrations: Dict[str, BrokerRegistration] = field(default_factory=dict)

    def register(self, *, broker_id: str, adapter: Any, transport: TransportKind,
                 classification: PluginClassification, authorized_interface: bool = False,
                 authorized_interface_evidence: str = "", environment: str = "unknown",
                 allow_duplicate: bool = False) -> BrokerRegistration:
        if not broker_id or not str(broker_id).strip():
            raise UnauthorizedInterface("broker_id is required")
        if broker_id in self._registrations and not allow_duplicate:
            raise UnauthorizedInterface(f"broker {broker_id!r} is already registered")
        if classification is PluginClassification.MACHINE_CALLABLE_EXECUTION and not authorized_interface:
            raise UnauthorizedInterface(
                "MACHINE_CALLABLE_EXECUTION requires an authorized programmable interface")
        registration = BrokerRegistration(
            broker_id=broker_id, adapter=adapter, transport=TransportKind(transport),
            classification=PluginClassification(classification),
            authorized_interface=bool(authorized_interface),
            authorized_interface_evidence=str(authorized_interface_evidence or ""),
            environment=str(environment))
        self._registrations[broker_id] = registration
        return registration

    def get(self, broker_id: str) -> Optional[BrokerRegistration]:
        return self._registrations.get(broker_id)

    def discover(self) -> List[Dict[str, Any]]:
        """Discovery result. Reports support honestly; never invents a working adapter."""
        return [self.conformance_report(broker_id) for broker_id in sorted(self._registrations)]

    def summarize(self) -> Dict[str, Any]:
        return {
            "registered": len(self._registrations),
            "executable": [b for b, r in sorted(self._registrations.items())
                           if self.execution_verdict(r)["permitted"]],
            "non_executing": [b for b, r in sorted(self._registrations.items())
                              if not self.execution_verdict(r)["permitted"]],
            "note": ("A registration is executable only when it is classified "
                     "MACHINE_CALLABLE_EXECUTION over an authorized transport AND its adapter "
                     "declares every core capability SUPPORTED."),
        }

    def execution_verdict(self, registration: BrokerRegistration,
                          scope: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        reasons: List[str] = []
        if registration.classification in NON_EXECUTING_CLASSIFICATIONS:
            reasons.append(NON_EXECUTING_CLASSIFICATIONS[registration.classification])
        if registration.transport not in AUTHORIZED_TRANSPORTS:
            reasons.append(f"transport {registration.transport.value} is not authorized")
        if not registration.authorized_interface:
            reasons.append("no authorized programmable interface is recorded for this broker")

        missing: Sequence[str] = ()
        mandate: Dict[str, Any] = {}
        if not reasons:
            missing = registration.adapter.capability_matrix().missing_core()
            if missing:
                reasons.append(f"BROKER_AUTOMATION_UNSUPPORTED: missing core capabilities {list(missing)}")
        if not reasons and scope is not None:
            # A broker can be perfectly conformant and still be the wrong broker. Mandate
            # compatibility is decided ONLY by mandate evidence, and it is never derived from a
            # capability matrix, so interface conformance can never override it.
            verdict_fn = getattr(registration.adapter, "mandate_verdict", None)
            if not callable(verdict_fn):
                raise UnauthorizedInterface(
                    f"adapter for {registration.broker_id!r} cannot answer mandate compatibility")
            mandate = dict(verdict_fn(list(scope)))
            if not mandate.get("permitted", False):
                reasons.append("BROKER_MANDATE_INCOMPATIBLE: "
                               + "; ".join(mandate.get("reasons") or []))

        permitted = not reasons
        return {
            "permitted": permitted,
            "broker_id": registration.broker_id,
            "code": ("SUPPORTED" if permitted
                     else ("BROKER_MANDATE_INCOMPATIBLE"
                           if mandate and not mandate.get("permitted", False)
                           else "BROKER_AUTOMATION_UNSUPPORTED")),
            "reasons": reasons,
            "missing_core": list(missing),
            "mandate": mandate,
        }

    def require_executable(self, broker_id: str,
                            scope: Optional[Sequence[str]] = None) -> BrokerRegistration:
        registration = self.get(broker_id)
        if registration is None:
            raise BrokerAutomationUnsupported(f"broker {broker_id!r} is not registered")
        verdict = self.execution_verdict(registration, scope=scope)
        if not verdict["permitted"]:
            raise BrokerAutomationUnsupported("; ".join(verdict["reasons"]) or "interface not authorized")
        return registration

    def conformance_report(self, broker_id: str) -> Dict[str, Any]:
        registration = self.get(broker_id)
        if registration is None:
            return {"broker_id": broker_id, "registered": False,
                    "code": "BROKER_AUTOMATION_UNSUPPORTED",
                    "reasons": ["broker is not registered"]}
        matrix: CapabilityMatrix = registration.adapter.capability_matrix()
        verdict = self.execution_verdict(registration)
        return {
            "broker_id": broker_id,
            "registered": True,
            "registration": registration.to_dict(),
            "capabilities": matrix.to_dict(),
            "unverified_capabilities": [name for name in CORE_CAPABILITIES
                                        if matrix.status(name) is CapabilityStatus.UNVERIFIED],
            "unsupported_capabilities": [name for name in CORE_CAPABILITIES
                                         if matrix.status(name) is CapabilityStatus.UNSUPPORTED],
            "supported_capabilities": [name for name in CORE_CAPABILITIES
                                       if matrix.status(name) is CapabilityStatus.SUPPORTED],
            "verdict": verdict,
            "forbidden_techniques_not_used": list(FORBIDDEN_AUTOMATION_TECHNIQUES),
            "note": ("Capabilities are resolved independently from per-capability evidence. There "
                     "is no blanket conformance flag, and mandate compatibility is decided only by "
                     "mandate evidence."),
        }


def assert_not_natural_language_evidence(payload: Mapping[str, Any], *,
                                         required_fields: Sequence[str] = ("broker_order_id",)) -> None:
    """Reject a natural-language claim in place of structured broker evidence."""
    if not isinstance(payload, Mapping):
        raise CapabilityError("broker evidence must be a structured mapping")
    missing = [field_name for field_name in required_fields if not payload.get(field_name)]
    if missing:
        raise CapabilityError(
            f"structured broker identifiers are required for execution evidence; missing {missing}. "
            "A natural-language statement such as 'Order placed' is never evidence.")
