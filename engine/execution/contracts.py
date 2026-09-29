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

import hashlib
import json
import math
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


class MandateIncompatible(ExecutionLayerError):
    """A broker is not permitted to execute under this deployment's instrument mandate."""


class MutationWithoutPermit(ExecutionLayerError):
    """Something tried to change a brokerage account without owner-signed authority to do so."""


#: The only stage at which a brokerage account may be mutated. Everything before this - including
#: broker readiness verification - is strictly non-mutating by construction, not by policy.
MUTATION_REQUIRES_STAGE = "LIVE_ENABLED"


@dataclass(frozen=True)
class LiveMutationPermit:
    """The one artifact that lets a channel place or cancel a REAL order.

    Readiness verification, conformance runs, reconciliation and every read path operate without
    this object, so none of them can mutate an account even by mistake. It is minted by the gateway
    only after ``Lifecycle.may_transmit_live()`` has permitted, which requires the frozen boundary to
    release, the stage to be ``LIVE_ENABLED``, and a verified owner-signed release basis - never a
    caller-asserted core state.

    It is bound to one broker and one account. A permit for the right stage but the wrong account is
    refused, so a permit cannot be widened by reuse.
    """

    stage: str
    broker_id: str
    account_id: str
    authorization_key_id: str

    def __post_init__(self) -> None:
        for name in ("stage", "broker_id", "account_id", "authorization_key_id"):
            if not str(getattr(self, name)).strip():
                raise MutationWithoutPermit(f"a mutation permit requires {name}")

    def check(self, *, broker_id: str, account_id: str) -> None:
        """Raise unless this permit authorises mutating THIS broker's THIS account."""
        if self.stage != MUTATION_REQUIRES_STAGE:
            raise MutationWithoutPermit(
                f"a brokerage account may only be mutated at stage {MUTATION_REQUIRES_STAGE}; "
                f"this permit carries {self.stage!r}")
        if str(broker_id) != self.broker_id or str(account_id) != self.account_id:
            raise MutationWithoutPermit(
                "a mutation permit is bound to one broker account and may not be reused for another")

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "broker_id": self.broker_id,
                "account_id": self.account_id, "authorization_key_id": self.authorization_key_id}


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
    def represent_intent(self, intent: Any, economic: "EconomicRepresentation | None" = None
                         ) -> Dict[str, Any]:
        """Translate a canonical intent into this broker's request representation.

        ``economic`` is the already-validated canonical economics the gateway resolved. An adapter
        must serialize FROM it, never re-derive economics from the raw intent.
        """

    @abstractmethod
    def economic_view(self, representation: Mapping[str, Any]) -> Dict[str, Any]:
        """Decode this broker's own payload back into canonical economics.

        The gateway proves that ``economic_view(represent_intent(i, e)) == e`` for every order.
        Without a faithful round trip, serialization is unverified and the order does not go.
        """

    def mandate_verdict(self, scope: Sequence[str]) -> Dict[str, Any]:
        """Whether this broker may execute under ``scope``. UNKNOWN fails closed."""
        from .conformance import mandate_verdict_for

        return mandate_verdict_for(self, scope)

    def require_mandate_compatible(self, scope: Sequence[str]) -> None:
        verdict = self.mandate_verdict(scope)
        if not verdict["permitted"]:
            raise MandateIncompatible(
                f"{self.broker_id} is not permitted to execute under "
                f"{verdict['mandate_id']}: " + "; ".join(verdict["reasons"]))

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

#: The six fields that carry economic meaning. Everything else a broker needs is representation.
ECONOMIC_FIELDS: Tuple[str, ...] = (
    "symbol", "side", "quantity", "order_type", "time_in_force", "limit_price")
_MEANING_FIELDS = ECONOMIC_FIELDS

#: Approved order vocabulary. An adapter may not invent a shape outside it.
SIDES: Tuple[str, ...] = ("BUY", "SELL")
ORDER_TYPES: Tuple[str, ...] = ("LIMIT", "MARKET")
TIME_IN_FORCE: Tuple[str, ...] = ("DAY", "GTC")


@dataclass(frozen=True)
class EconomicRepresentation:
    """A validated, broker-neutral statement of what an order economically IS.

    This is the object a broker adapter is allowed to serialize, and the object the gateway
    re-proves the serialization against. It exists because an adapter that is handed a raw intent
    and asked to "translate it" can alter economics inside the translation: Alpaca spells quantity
    ``qty`` as a *string*, Upstox spells side ``transaction_type`` and time-in-force ``validity``,
    and neither carries the canonical names. Comparing a provider-native payload field-by-field
    against the intent is therefore not possible in general - it either misses the fields or, worse,
    has to be weakened until it passes. So the canonical economics are validated FIRST, in one
    place, and the adapter is then required to be able to decode its own payload back into exactly
    that object.
    """

    symbol: str
    side: str
    quantity: int
    order_type: str
    time_in_force: str
    limit_price: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "limit_price": self.limit_price,
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def as_mapping(self) -> Dict[str, Any]:
        return self.to_dict()


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityError(f"canonical {name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CapabilityError(f"canonical {name} must be finite and positive, got {value!r}")
    return number


def canonical_economic_representation(source: Any) -> EconomicRepresentation:
    """Read and fully validate the canonical economics of an intent or a payload.

    ``source`` may be an :class:`~execution.intent.ExecutionIntent`, any object exposing the six
    attributes, or a mapping. This is the gate the Universal Broker Gateway runs BEFORE any
    adapter is asked to serialize anything, so a malformed or ambiguous order is rejected while it
    is still broker-neutral - no provider-native field has been produced and no submission
    reservation has been consumed.
    """
    if isinstance(source, EconomicRepresentation):
        return source
    if isinstance(source, Mapping):
        get = source.get
    else:
        def get(name, default=None):  # noqa: E306 - local adapter over attribute access
            return getattr(source, name, default)

    for name in ECONOMIC_FIELDS:
        if name != "limit_price" and get(name) is None:
            raise CapabilityError(f"canonical economic representation is missing {name!r}")

    symbol = get("symbol")
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip().upper():
        raise CapabilityError("canonical symbol must be a canonical uppercase token")
    side = get("side")
    if side not in SIDES:
        raise CapabilityError(f"canonical side must be one of {SIDES}, got {side!r}")
    quantity = get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise CapabilityError(f"canonical quantity must be a positive integer, got {quantity!r}")
    order_type = get("order_type")
    if order_type not in ORDER_TYPES:
        raise CapabilityError(f"canonical order_type must be one of {ORDER_TYPES}, got {order_type!r}")
    time_in_force = get("time_in_force")
    if time_in_force not in TIME_IN_FORCE:
        raise CapabilityError(
            f"canonical time_in_force must be one of {TIME_IN_FORCE}, got {time_in_force!r}")

    limit_price = get("limit_price")
    if order_type == "LIMIT":
        if limit_price is None:
            raise CapabilityError("a canonical LIMIT order requires a limit_price")
        limit_price = _finite_positive(limit_price, "limit_price")
    else:
        if limit_price is not None:
            raise CapabilityError("a canonical MARKET order must not carry a limit_price")
        limit_price = None

    return EconomicRepresentation(symbol=symbol, side=side, quantity=int(quantity),
                                  order_type=order_type, time_in_force=time_in_force,
                                  limit_price=limit_price)


def assert_preserves_economic_meaning(reference: Any, representation: Mapping[str, Any]) -> None:
    """Prove a translation preserved economic meaning, field for field.

    Rejects the silent conversions the specification forbids: limit->market, quantity rounding,
    time-in-force substitution, price modification, side change, symbol substitution.

    ``reference`` is the validated canonical economics (an :class:`EconomicRepresentation`, an
    intent, or a mapping) and ``representation`` is what the adapter claims the broker will read
    back. Both sides are compared in canonical terms; this function is deliberately NOT weakened
    to accommodate a provider's field names.
    """
    if not isinstance(representation, Mapping):
        raise CapabilityError("adapter representation must be a mapping")
    for name in _MEANING_FIELDS:
        if name not in representation:
            raise CapabilityError(f"adapter representation omitted economic field {name!r}")
    expected = canonical_economic_representation(reference).to_dict()
    for name, want in expected.items():
        got = representation[name]
        if name == "quantity":
            if isinstance(got, bool) or not isinstance(got, int):
                raise CapabilityError("adapter quantity must be an integer")
        if name == "limit_price" and want is None and got is None:
            continue
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
