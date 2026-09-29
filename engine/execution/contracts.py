"""Broker-neutral execution contracts for Trip's additive execution layer.

This package is ADDITIVE. It does not modify, monkeypatch, or shadow any file pinned by
``infra/core_v06.sha256``. Where a frozen invariant already owns a decision (approved symbol
scope, hard risk ceilings, the Constitution) this layer READS that invariant rather than
restating it, so there is never a second competing authority.

Honesty note: the authoritative canonical execution-state list comes from a specification
section that is currently truncated. ``CANONICAL_STATE_MODEL_STATUS`` records that, so nobody
mistakes this provisional model for a reviewed one.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

CANONICAL_STATE_MODEL_STATUS = "PROVISIONAL_PENDING_SPEC_SECTION_27"

MAX_EVIDENCE_BYTES = 64 * 1024

#: Representability verdict codes. Both non-SUPPORTED codes mean NO TRADE, never a substitution.
SUPPORTED = "SUPPORTED"
ORDER_INCOMPATIBLE = "ORDER_INCOMPATIBLE"
CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"


class ExecutionLayerError(RuntimeError):
    """Base error for the additive execution layer."""


class CapabilityError(ExecutionLayerError):
    """A required broker capability is absent, or its state is unknown."""


class BrokerAutomationUnsupported(ExecutionLayerError):
    """No authorized programmable interface exists for this broker."""


class IntentError(ExecutionLayerError):
    """An execution intent is structurally invalid."""


class IntentExpired(IntentError):
    """The intent's validity boundary has passed. Never extended automatically."""


class GovernorError(ExecutionLayerError):
    """The Capital Governor profile is missing, malformed, or unapproved."""


class AutonomousAuthorityIncrease(GovernorError):
    """An autonomous mechanism attempted to raise owner-approved financial authority."""


class LedgerError(ExecutionLayerError):
    """Durable execution ledger is missing, corrupt, or in an illegal state."""


class IllegalStateTransition(LedgerError):
    """A canonical execution state transition that the model does not permit."""


# ---------------------------------------------------------------------------
# Capability contract (spec: SUPPORTED / UNSUPPORTED / UNVERIFIED, UNVERIFIED fails closed)
# ---------------------------------------------------------------------------


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"

    @property
    def permits_execution(self) -> bool:
        """Unknown capability is NOT supported capability. Only SUPPORTED permits execution."""
        return self is CapabilityStatus.SUPPORTED


CORE_CAPABILITIES: Tuple[str, ...] = (
    "broker_identity",
    "capability_discovery",
    "auth_state",
    "connection_health",
    "broker_clock",
    "account",
    "buying_power",
    "positions",
    "open_orders",
    "order_status",
    "recent_orders",
    "order_submission",
    "order_cancel",
    "reconciliation",
    "disconnect",
)

OPTIONAL_CAPABILITIES: Tuple[str, ...] = (
    "order_replace",
    "order_preview",
    "streaming_events",
)


@dataclass(frozen=True)
class CapabilityMatrix:
    """Declared capability state for one broker adapter.

    A capability that is absent from ``statuses`` is treated as UNVERIFIED, not as supported.
    """

    statuses: Mapping[str, CapabilityStatus]
    source: str = "adapter_declaration"

    def status(self, name: str) -> CapabilityStatus:
        value = self.statuses.get(name)
        if value is None:
            return CapabilityStatus.UNVERIFIED
        return value if isinstance(value, CapabilityStatus) else CapabilityStatus(str(value))

    def missing_core(self) -> List[str]:
        return [name for name in CORE_CAPABILITIES if not self.status(name).permits_execution]

    def require(self, name: str) -> None:
        status = self.status(name)
        if not status.permits_execution:
            raise CapabilityError(f"broker capability {name!r} is {status.value}; refusing to proceed")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "core": {name: self.status(name).value for name in CORE_CAPABILITIES},
            "optional": {name: self.status(name).value for name in OPTIONAL_CAPABILITIES},
            "missing_core": self.missing_core(),
        }


# ---------------------------------------------------------------------------
# Intent representability (spec section 19: an adapter may translate representation,
# never economic meaning. If it cannot represent the approved intent there is NO TRADE.)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderCapabilities:
    """What order shapes a broker adapter can actually express.

    ``declared=False`` means the adapter has not proven its order support. Unknown capability is
    not supported capability, so an undeclared adapter can never transmit a live order.
    """

    order_types: FrozenSet[str] = frozenset()
    time_in_force: FrozenSet[str] = frozenset()
    sides: FrozenSet[str] = frozenset()
    declared: bool = False
    source: str = "adapter_declaration"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "declared": self.declared,
            "source": self.source,
            "order_types": sorted(self.order_types),
            "time_in_force": sorted(self.time_in_force),
            "sides": sorted(self.sides),
        }


def check_representable(intent: Any, capabilities: OrderCapabilities) -> Dict[str, Any]:
    """Decide whether the broker can express the AUTHORIZED intent unchanged.

    Returns a verdict with code SUPPORTED, ORDER_INCOMPATIBLE or CAPABILITY_UNSUPPORTED. A caller
    must never respond to a non-SUPPORTED code by substituting a different order.
    """
    if not isinstance(capabilities, OrderCapabilities):
        return {"permitted": False, "code": CAPABILITY_UNSUPPORTED,
                "reasons": ["adapter capabilities are UNVERIFIED; unknown is not supported"]}
    if not capabilities.declared:
        return {"permitted": False, "code": CAPABILITY_UNSUPPORTED,
                "reasons": ["adapter has not declared order capabilities; "
                            "UNVERIFIED must fail closed"]}

    reasons: List[str] = []
    if intent.side not in capabilities.sides:
        reasons.append(f"broker cannot represent side {intent.side}")
    if intent.order_type not in capabilities.order_types:
        reasons.append(f"broker cannot represent order_type {intent.order_type}")
    if intent.time_in_force not in capabilities.time_in_force:
        reasons.append(f"broker cannot represent time_in_force {intent.time_in_force}")

    if reasons:
        return {
            "permitted": False,
            "code": ORDER_INCOMPATIBLE,
            "reasons": reasons + ["the approved intent is not representable; substitute nothing"],
        }
    return {"permitted": True, "code": SUPPORTED, "reasons": []}


# ---------------------------------------------------------------------------
# Closed-bar guarantee at the transmission boundary (spec section 25)
# ---------------------------------------------------------------------------


def decision_bar_close_time(intent: Any, *, interval: str, close_lag_seconds: float = 20.0) -> datetime:
    """When the intent's decision bar provably completed, using the frozen bar semantics.

    Mirrors ``market_time.closed_bars_only`` exactly: provider timestamps are bar-start, and a bar
    is closed only once its whole interval plus the close lag is in the past.
    """
    from market_time import interval_to_timedelta

    start = aware_timestamp(intent.decision_bar_ts, "decision_bar_ts")
    return start + interval_to_timedelta(interval) + timedelta(seconds=float(close_lag_seconds))


def decision_bar_is_closed(intent: Any, *, interval: str, now: datetime,
                           close_lag_seconds: float = 20.0) -> Tuple[bool, str]:
    """Re-prove, at the moment of transmission, that the decision bar has closed.

    ``closed_bars_only`` guards the decision; this guards the ORDER. A signal derived from a bar
    that has not completed may never be transmitted, including after a broker outage, restart,
    reconciliation or network loss.
    """
    if now.tzinfo is None:
        return False, "transmission time must be timezone-aware"
    closes_at = decision_bar_close_time(intent, interval=interval,
                                        close_lag_seconds=close_lag_seconds)
    reference = now.astimezone(timezone.utc)
    if reference < closes_at:
        return False, (f"decision bar {intent.decision_bar_ts} does not close until "
                       f"{closes_at.isoformat()}; refusing to execute an incomplete decision bar")
    return True, "decision bar closed"


def aware_timestamp(value: Any, field_name: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp or fail. Naive timestamps are refused."""
    if not isinstance(value, str) or not value:
        raise IntentError(f"{field_name} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise IntentError(f"{field_name} is not parseable ISO-8601") from exc
    if parsed.tzinfo is None:
        raise IntentError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Transports (communication mechanics) vs adapters (broker semantics)
# ---------------------------------------------------------------------------


class TransportKind(str, Enum):
    REST = "REST"
    WEBSOCKET = "WEBSOCKET"
    OAUTH = "OAUTH"
    TOKEN_SESSION = "TOKEN_SESSION"
    OFFICIAL_GATEWAY = "OFFICIAL_GATEWAY"
    FIX = "FIX"
    MCP_CONNECTOR = "MCP_CONNECTOR"


class BrokerTransport(ABC):
    """Communication mechanics only. No broker semantics, no trading intelligence."""

    kind: TransportKind

    @abstractmethod
    def describe(self) -> Dict[str, Any]:
        """Return non-secret transport metadata. Never return credentials."""


class BrokerAdapter(ABC):
    """Broker semantics. Translates canonical intents and normalises broker events.

    Adapters may translate REPRESENTATION. They may never alter ECONOMIC MEANING; the gateway
    independently asserts that with ``assert_preserves_economic_meaning``.
    """

    broker_id: str

    @abstractmethod
    def capability_matrix(self) -> CapabilityMatrix: ...

    @abstractmethod
    def health(self) -> "BrokerHealth": ...

    @abstractmethod
    def account(self) -> "BrokerAccount": ...

    @abstractmethod
    def positions(self) -> Sequence["BrokerPosition"]: ...

    @abstractmethod
    def open_orders(self) -> Sequence[Mapping[str, Any]]: ...

    @abstractmethod
    def order_status(self, *, client_order_id: str) -> Optional[Mapping[str, Any]]: ...

    def order_capabilities(self) -> OrderCapabilities:
        """Declare which order shapes this broker can express. Default is UNDECLARED, which fails
        closed through ``check_representable``. An adapter that ships without implementing this
        cannot transmit an order, by construction."""
        return OrderCapabilities()

    @abstractmethod
    def represent_intent(self, intent: Any) -> Dict[str, Any]:
        """Translate a canonical intent into this broker's request representation."""

    @abstractmethod
    def submit_order(self, *, client_order_id: str, representation: Mapping[str, Any]) -> Mapping[str, Any]: ...

    @abstractmethod
    def cancel_order(self, *, broker_order_id: str, reason: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BrokerHealth:
    connected: bool
    authenticated: bool
    clock_skew_seconds: float

    @property
    def healthy(self) -> bool:
        return bool(self.connected and self.authenticated)


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    environment: str
    cash: float
    equity: float
    buying_power: float


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: int


# ---------------------------------------------------------------------------
# Canonical execution states
# ---------------------------------------------------------------------------


class ExecutionState(str, Enum):
    READY = "READY"
    SUBMISSION_RESERVED = "SUBMISSION_RESERVED"
    SUBMITTING = "SUBMITTING"
    UNKNOWN_PENDING_RECONCILIATION = "UNKNOWN_PENDING_RECONCILIATION"
    BROKER_ACKNOWLEDGED = "BROKER_ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUSED = "REFUSED"

    @property
    def terminal(self) -> bool:
        return self in {ExecutionState.FILLED, ExecutionState.REJECTED, ExecutionState.CANCELLED,
                        ExecutionState.EXPIRED, ExecutionState.REFUSED}


_STATE_TRANSITIONS: Dict[ExecutionState, Tuple[ExecutionState, ...]] = {
    ExecutionState.READY: (ExecutionState.SUBMISSION_RESERVED, ExecutionState.EXPIRED, ExecutionState.REFUSED),
    ExecutionState.SUBMISSION_RESERVED: (ExecutionState.SUBMITTING, ExecutionState.REFUSED,
                                         ExecutionState.UNKNOWN_PENDING_RECONCILIATION),
    ExecutionState.SUBMITTING: (ExecutionState.BROKER_ACKNOWLEDGED, ExecutionState.REJECTED,
                                ExecutionState.UNKNOWN_PENDING_RECONCILIATION, ExecutionState.FILLED,
                                ExecutionState.PARTIALLY_FILLED),
    ExecutionState.UNKNOWN_PENDING_RECONCILIATION: (ExecutionState.BROKER_ACKNOWLEDGED, ExecutionState.REJECTED,
                                                    ExecutionState.FILLED, ExecutionState.PARTIALLY_FILLED,
                                                    ExecutionState.CANCELLED, ExecutionState.REFUSED),
    ExecutionState.BROKER_ACKNOWLEDGED: (ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED,
                                         ExecutionState.CANCEL_PENDING, ExecutionState.REJECTED,
                                         ExecutionState.EXPIRED),
    ExecutionState.PARTIALLY_FILLED: (ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED,
                                      ExecutionState.CANCEL_PENDING),
    ExecutionState.CANCEL_PENDING: (ExecutionState.CANCELLED, ExecutionState.PARTIALLY_FILLED,
                                    ExecutionState.FILLED),
    ExecutionState.FILLED: (),
    ExecutionState.REJECTED: (),
    ExecutionState.CANCELLED: (),
    ExecutionState.EXPIRED: (),
    ExecutionState.REFUSED: (),
}


def assert_transition_allowed(current: ExecutionState, nxt: ExecutionState) -> None:
    allowed = _STATE_TRANSITIONS.get(current, ())
    if nxt not in allowed:
        raise IllegalStateTransition(f"illegal canonical transition {current.value} -> {nxt.value}")


# ---------------------------------------------------------------------------
# Economic-meaning preservation
# ---------------------------------------------------------------------------

_MEANING_FIELDS = ("symbol", "side", "quantity", "order_type", "time_in_force", "limit_price")


def assert_preserves_economic_meaning(intent: Any, representation: Mapping[str, Any]) -> None:
    """Prove an adapter translated representation WITHOUT changing economic meaning.

    Rejects the silent conversions the specification forbids: limit->market, quantity rounding,
    time-in-force substitution, price modification, side change, symbol substitution.
    """
    if not isinstance(representation, Mapping):
        raise CapabilityError("adapter representation must be a mapping")
    for name in _MEANING_FIELDS:
        if name not in representation:
            raise CapabilityError(f"adapter representation omitted economic field {name!r}")
    expected = {
        "symbol": intent.symbol,
        "side": intent.side,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "time_in_force": intent.time_in_force,
        "limit_price": intent.limit_price,
    }
    for name, want in expected.items():
        got = representation[name]
        if name == "quantity":
            if isinstance(got, bool) or not isinstance(got, int):
                raise CapabilityError("adapter quantity must be an integer")
        if got != want:
            raise CapabilityError(
                f"adapter altered economic meaning for {name!r}: authorized {want!r}, represented {got!r}"
            )


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def approved_symbol_scope() -> frozenset:
    """Reuse the frozen symbol lock instead of maintaining a second, drifting scope list."""
    from config_guard import VALIDATED_SYMBOLS

    return frozenset(VALIDATED_SYMBOLS)


APPROVED_INSTRUMENT_SCOPE = "US_EQUITY_CASH_LONG_ONLY"
APPROVED_BAR_INTERVAL = "60min"
