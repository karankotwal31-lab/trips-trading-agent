"""Concrete broker adapters, ported from the ``live_execution/`` draft (PR #1).

PR #1 was reviewed and **closed unmerged**. Four things in it were genuinely good and are carried
over here, in the shapes this repository's architecture actually wants:

1. **Validate before the network.** Every field is checked before a request could be made, so a
   malformed intent can never reach a broker or consume a submission reservation. Ported from
   ``upstox_adapter.py`` and its tests (``request.assert_not_called()``).
2. **Integer quantity discipline.** A quantity must be a positive whole number; ``bool``,
   ``nan``, ``inf``, fractional and non-numeric values are all refused. Silent rounding is a
   change of economic meaning, which this repository forbids outright.
3. **A split acknowledgement fails closed.** A broker that answers with more than one order id
   means one intent became several orders. The adapter refuses and hands the case to
   reconciliation rather than guessing which order was "the" one.
4. **Snapshot shape is validated, and identity-less rows are never dropped.** An account or order
   snapshot that is malformed, or an order row with no id, is an error, not an empty result.

Deliberately **not** ported:

* ``require_live_opt_in()`` and its ``TRIPS_LIVE_EXECUTION=ENABLED`` flag. That is an
  operator-settable boolean carrying financial authority — the exact pattern that made the owner
  signature forgeable (see AMENDMENT-01 §20.1). Authority here comes from the frozen boundary plus
  an owner signature, and an adapter cannot grant either.
* ``LiveLimits``' invented numbers. Capital values are an owner input; none is invented here.
* Its own gates, executor, journal, kill switch and readiness module. This repository already has
  a two-permission authority gate, a durable idempotency ledger and an owner-gated lifecycle;
  a second parallel set would create two authorities, and two authorities means one is weak.

These adapters build and validate requests and interpret responses. They hold **no transport**: the
transport is injected, so shipping this module ships no network capability and no credentials.
Without an injected transport and configured credentials an adapter cannot submit, and it says so.
"""

from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Mapping, Optional, Sequence

from .contracts import (CORE_CAPABILITIES, BrokerAccount, BrokerAdapter, BrokerHealth,
                        BrokerPosition, CapabilityMatrix, CapabilityStatus, EconomicRepresentation,
                        ExecutionState, OrderCapabilities, canonical_json,
                        canonical_economic_representation)

#: A broker-side order tag is a fixed-width field. Silently truncating an idempotency key would
#: destroy deduplication, so the bound is enforced rather than adapted around.
MAX_TAG_LENGTH = 40

UPSTOX_LIVE_TRADE_BASE = "https://api-hft.upstox.com/v3"
UPSTOX_SANDBOX_TRADE_BASE = "https://api-sandbox.upstox.com/v3"
UPSTOX_DATA_BASE = "https://api.upstox.com"
ALPACA_LIVE_BASE = "https://api.alpaca.markets"
ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"


class BrokerContractError(Exception):
    """A broker contract was violated. Never a substitute for evidence of execution."""


def _whole_quantity(value: Any, *, field: str = "quantity") -> int:
    """Ported: a positive whole number or nothing. Rejects bool, nan, inf and fractions."""
    if isinstance(value, bool):
        raise BrokerContractError(f"{field} must be a whole number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise BrokerContractError(f"{field} is not numeric: {value!r}") from None
    if not math.isfinite(number):
        raise BrokerContractError(f"{field} must be finite, got {value!r}")
    if number <= 0 or not number.is_integer():
        raise BrokerContractError(f"{field} must be a positive whole number, got {value!r}")
    return int(number)


def _non_negative(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise BrokerContractError(f"{field} must be a number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise BrokerContractError(f"{field} is not numeric: {value!r}") from None
    if not math.isfinite(number) or number < 0:
        raise BrokerContractError(f"{field} must be finite and non-negative, got {value!r}")
    return number


def _require_tag(value: Any) -> str:
    """Ported: the broker tag is never silently truncated, so an over-long key is refused."""
    if not isinstance(value, str):
        raise BrokerContractError("order tag must be a string")
    if not value.strip() or len(value) > MAX_TAG_LENGTH:
        raise BrokerContractError(
            f"order tag must be 1 to {MAX_TAG_LENGTH} characters; refusing to truncate")
    return value


def _rows(response: Any, *, what: str) -> list:
    """Ported: a broker snapshot is validated, and a malformed one is an error, not an empty list."""
    if not isinstance(response, Mapping) or response.get("status") != "success":
        raise BrokerContractError(f"{what} snapshot unavailable")
    rows = response.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BrokerContractError(f"malformed {what} snapshot")
    return rows


def _single_order_id(data: Any) -> str:
    if not isinstance(data, Mapping):
        raise BrokerContractError("malformed order acknowledgement; reconcile before retry")
    ids = data.get("order_ids")
    if ids is None:
        legacy = data.get("order_id")
        ids = [legacy] if legacy else []
    if not isinstance(ids, list) or not ids:
        raise BrokerContractError("broker returned no verifiable order id; reconcile before retry")
    if len(ids) != 1:
        raise BrokerContractError(
            f"broker acknowledged {len(ids)} orders for one intent; refusing to guess which. "
            "This is a split acknowledgement and requires explicit reconciliation")
    candidate = ids[0]
    if not isinstance(candidate, str) or not candidate.strip():
        raise BrokerContractError("invalid broker order identity; reconcile before retry")
    return candidate


def _canonical(intent: Any, economic: Optional[EconomicRepresentation]) -> EconomicRepresentation:
    """Resolve the canonical economics an adapter may serialize from.

    The gateway always passes them, already validated. A direct caller may omit them, in which case
    they are validated here - the same validation, so there is one authority, not two.
    """
    try:
        return canonical_economic_representation(economic if economic is not None else intent)
    except Exception as exc:
        raise BrokerContractError(f"canonical economic representation is invalid: {exc}") from exc


class BrokerChannel(ABC):
    """A configured, authorized connection to a broker.

    Deliberately not shipped. An adapter with no channel validates and translates but cannot reach
    a broker, so importing this module ships no network capability and no credentials. An
    implementation is an owner act: it needs an authorized programmable interface and credentials
    that never enter this repository. The adapter never handles credentials itself; the channel
    does, and it is injected.
    """

    @abstractmethod
    def health(self) -> BrokerHealth: ...

    @abstractmethod
    def submit(self, *, client_order_id: str, representation: Mapping[str, Any]) -> Mapping[str, Any]: ...

    @abstractmethod
    def account(self) -> BrokerAccount: ...

    @abstractmethod
    def positions(self) -> Sequence[BrokerPosition]: ...

    @abstractmethod
    def open_orders(self) -> Sequence[Mapping[str, Any]]: ...

    @abstractmethod
    def order_status(self, *, client_order_id: str) -> Optional[Mapping[str, Any]]: ...

    @abstractmethod
    def cancel(self, *, broker_order_id: str, reason: str) -> Mapping[str, Any]: ...


class _ChannelInjectedAdapter(BrokerAdapter):
    """Shared shape: per-capability capabilities, and a refusal at every boundary without a channel.

    There is deliberately no blanket conformance flag argument. It used to mean "flip all fifteen
    core capabilities to SUPPORTED", which is a claim with no content: nothing recorded which
    capability was exercised, against what interface, in what environment, by what run. The
    replacement is :class:`~execution.conformance.ConformanceEvidence`, attached per capability, and
    an adapter with no evidence attached answers UNVERIFIED for every capability.
    """

    def __init__(self, *, channel: Optional[BrokerChannel] = None,
                 conformance: Optional[Any] = None,
                 environment: str = "TEST_ENV", account_id: Optional[str] = None) -> None:
        self._channel = channel
        self._conformance = conformance
        self._environment = environment
        self._account_id = account_id

    # -- capabilities -----------------------------------------------------
    @property
    def conformance_evidence(self) -> Optional[Any]:
        return self._conformance

    def capability_matrix(self) -> CapabilityMatrix:
        """Each capability resolves from its own record. Absent evidence is UNVERIFIED.

        A channel alone is not permission, and neither is a boolean. Verification is a recorded,
        digest-checked observation from a real conformance run against a real interface.
        """
        if self._conformance is None:
            return CapabilityMatrix(statuses={}, source=f"{self.broker_id}:no_conformance_evidence")
        return self._conformance.matrix()

    def health(self) -> BrokerHealth:
        if self._channel is None:
            return BrokerHealth(connected=False, authenticated=False, clock_skew_seconds=0.0)
        try:
            return self._channel.health()
        except Exception as exc:
            return BrokerHealth(connected=False, authenticated=False, clock_skew_seconds=0.0)

    # -- refusal paths ----------------------------------------------------
    def _require_channel(self) -> "BrokerChannel":
        if self._channel is None:
            raise BrokerContractError(
                f"{self.broker_id}: no broker channel is configured, so this adapter cannot reach "
                "a broker. It can validate and translate, but it cannot submit")
        return self._channel

    def submit_order(self, *, client_order_id: str, representation: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._require_channel().submit(client_order_id=client_order_id,
                                              representation=representation)

    def account(self) -> BrokerAccount:
        return self._require_channel().account()

    def positions(self) -> Sequence[BrokerPosition]:
        return tuple(self._require_channel().positions())

    def open_orders(self) -> Sequence[Mapping[str, Any]]:
        return tuple(self._require_channel().open_orders())

    def order_status(self, *, client_order_id: str) -> Optional[Mapping[str, Any]]:
        return self._require_channel().order_status(client_order_id=client_order_id)

    def cancel_order(self, *, broker_order_id: str, reason: str) -> Mapping[str, Any]:
        return self._require_channel().cancel(broker_order_id=broker_order_id, reason=reason)


class UpstoxAdapter(_ChannelInjectedAdapter):
    """Upstox V3 adapter. Sandbox by default; the live base must be selected explicitly.

    Environment selection is a configuration value, not an authority. Whether capital may move is
    decided by the frozen boundary and an owner signature, never by which endpoint is configured.
    """

    broker_id = "upstox"

    def __init__(self, *, environment: str = "sandbox", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if environment not in {"sandbox", "live"}:
            raise BrokerContractError("Upstox environment must be sandbox or live")
        self._upstox_environment = environment

    @property
    def trade_base(self) -> str:
        return (UPSTOX_LIVE_TRADE_BASE if self._upstox_environment == "live"
                else UPSTOX_SANDBOX_TRADE_BASE)

    def order_capabilities(self) -> OrderCapabilities:
        return OrderCapabilities(order_types=frozenset({"LIMIT", "MARKET"}),
                                 time_in_force=frozenset({"DAY"}),
                                 sides=frozenset({"BUY", "SELL"}), declared=True,
                                 source=f"{self.broker_id}:{self._upstox_environment}")

    def represent_intent(self, intent: Any, economic: Optional[EconomicRepresentation] = None
                         ) -> Dict[str, Any]:
        """Serialize ALREADY-VALIDATED canonical economics into Upstox's field names.

        Ported: every check still happens here, before any request could be built. The economics
        themselves were validated by the gateway first, so this step cannot re-derive them from the
        raw intent and therefore cannot quietly change one.
        """
        if getattr(intent, "slice", False) is not False:
            raise BrokerContractError("sliced orders are not supported by this adapter")
        tag = _require_tag(getattr(intent, "idempotency_key", None))
        economics = _canonical(intent, economic)
        if economics.order_type == "MARKET" and economics.limit_price is not None:
            raise BrokerContractError("a market order may not carry a limit price")
        return {
            "tradingsymbol": economics.symbol,
            "quantity": _whole_quantity(economics.quantity),
            "product": "D",
            "validity": economics.time_in_force,
            "price": 0.0 if economics.limit_price is None
                     else _non_negative(economics.limit_price, field="price"),
            "tag": tag,
            "order_type": economics.order_type,
            "transaction_type": economics.side,
            "disclosed_quantity": 0,
            "trigger_price": 0.0,
            "is_amo": False,
            "slice": False,
            "market_protection": -1,
        }

    def economic_view(self, representation: Mapping[str, Any]) -> Dict[str, Any]:
        """Decode the Upstox payload back into canonical economics. The gateway proves equality.

        Nothing here is forgiving. ``quantity`` must be an exact integer, ``validity`` and
        ``order_type`` must be exactly as canonical, and a LIMIT order carrying a zero price is a
        price alteration rather than a formatting detail.
        """
        if not isinstance(representation, Mapping):
            raise BrokerContractError("upstox representation must be a mapping")
        symbol = representation.get("tradingsymbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise BrokerContractError("upstox representation carries no tradingsymbol")
        order_type = representation.get("order_type")
        price = representation.get("price")
        if order_type == "MARKET":
            limit_price = None
        else:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise BrokerContractError("upstox limit price is not numeric")
            if float(price) <= 0:
                raise BrokerContractError(
                    "upstox dropped the limit price of a non-market order; that is a price "
                    "alteration, not a formatting difference")
            limit_price = float(price)
        return {
            "symbol": symbol,
            "side": str(representation.get("transaction_type") or "").upper(),
            "quantity": _whole_quantity(representation.get("quantity")),
            "order_type": str(order_type or "").upper(),
            "time_in_force": str(representation.get("validity") or "").upper(),
            "limit_price": limit_price,
        }

    def interpret_acknowledgement(self, response: Any) -> Dict[str, Any]:
        """Ported: exactly one order id, or refuse and reconcile."""
        if not isinstance(response, Mapping) or response.get("status") != "success":
            raise BrokerContractError("broker did not acknowledge the order; reconcile before retry")
        order_id = _single_order_id(response.get("data"))
        return {"broker_order_id": order_id, "state": ExecutionState.BROKER_ACKNOWLEDGED.value}

    def normalize_orders(self, response: Any) -> Sequence[BrokerPosition]:
        """Ported: an order row with no identity is an error, never silently dropped."""
        normalized = []
        for row in _rows(response, what="order"):
            order_id = row.get("order_id")
            if not order_id:
                raise BrokerContractError("broker order row is missing its identity")
            normalized.append(BrokerPosition(symbol=str(row.get("trading_symbol") or
                                                         row.get("tradingsymbol") or ""),
                                            quantity=_non_negative(row.get("quantity", 0),
                                                                 field="quantity"),
                                            average_price=_non_negative(
                                                row.get("filled_quantity", 0),
                                                field="filled_quantity")))
        return tuple(normalized)


class AlpacaAdapter(_ChannelInjectedAdapter):
    """Alpaca Trading API adapter. Paper by default; live requires explicit endpoint selection.

    As with Upstox, choosing a live endpoint grants nothing. The frozen boundary and an owner
    signature are what release capital, and neither can be bypassed by configuration.
    """

    broker_id = "alpaca"

    def __init__(self, *, environment: str = "paper", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if environment not in {"paper", "live"}:
            raise BrokerContractError("Alpaca environment must be paper or live")
        self._alpaca_environment = environment

    @property
    def base_url(self) -> str:
        return (ALPACA_LIVE_BASE if self._alpaca_environment == "live" else ALPACA_PAPER_BASE)

    def order_capabilities(self) -> OrderCapabilities:
        return OrderCapabilities(order_types=frozenset({"LIMIT", "MARKET"}),
                                 time_in_force=frozenset({"DAY", "GTC"}),
                                 sides=frozenset({"BUY", "SELL"}), declared=True,
                                 source=f"{self.broker_id}:{self._alpaca_environment}")

    def represent_intent(self, intent: Any, economic: Optional[EconomicRepresentation] = None
                         ) -> Dict[str, Any]:
        """Serialize ALREADY-VALIDATED canonical economics into Alpaca's field names.

        Alpaca spells quantity ``qty`` as a *string* and lowercases the enums, which is precisely
        why translation used to be unverifiable: the canonical field names simply are not there.
        ``economic_view`` is the counterpart that decodes them back, and the gateway proves the
        round trip for every order.
        """
        tag = _require_tag(getattr(intent, "idempotency_key", None))
        economics = _canonical(intent, economic)
        if economics.order_type == "LIMIT" and economics.limit_price is None:
            raise BrokerContractError("a limit order requires a limit price")
        representation: Dict[str, Any] = {
            "symbol": economics.symbol,
            "qty": str(_whole_quantity(economics.quantity)),
            "side": economics.side.lower(),
            "type": economics.order_type.lower(),
            "time_in_force": economics.time_in_force.lower(),
            "client_order_id": tag,
        }
        if economics.order_type == "LIMIT":
            representation["limit_price"] = str(
                _non_negative(economics.limit_price, field="limit_price"))
        return representation

    def economic_view(self, representation: Mapping[str, Any]) -> Dict[str, Any]:
        """Decode the Alpaca payload back into canonical economics, refusing anything lossy.

        A fractional ``qty`` string, a missing or non-numeric ``limit_price`` on a LIMIT order, or a
        blank symbol are all refusals. They are not rounding and they are not defaults.
        """
        if not isinstance(representation, Mapping):
            raise BrokerContractError("alpaca representation must be a mapping")
        symbol = representation.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise BrokerContractError("alpaca representation carries no symbol")
        order_type = str(representation.get("type") or "").upper()
        if order_type == "MARKET":
            limit_price = None
        else:
            raw_price = representation.get("limit_price")
            if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
                raise BrokerContractError("alpaca limit order carries no numeric limit price")
            try:
                limit_price = float(raw_price)
            except (TypeError, ValueError):
                raise BrokerContractError("alpaca limit price is not numeric") from None
            if not math.isfinite(limit_price) or limit_price <= 0:
                raise BrokerContractError(
                    "alpaca dropped the limit price of a non-market order; that is a price "
                    "alteration, not a formatting difference")
        return {
            "symbol": symbol,
            "side": str(representation.get("side") or "").upper(),
            "quantity": _whole_quantity(representation.get("qty")),
            "order_type": order_type,
            "time_in_force": str(representation.get("time_in_force") or "").upper(),
            "limit_price": limit_price,
        }

    def interpret_acknowledgement(self, response: Any) -> Dict[str, Any]:
        if not isinstance(response, Mapping) or not response.get("id"):
            raise BrokerContractError("broker did not return a verifiable order id")
        return {"broker_order_id": str(response["id"]),
                "state": ExecutionState.BROKER_ACKNOWLEDGED.value}


def default_upstox(**kwargs: Any) -> UpstoxAdapter:
    """Upstox configured from the environment, with no transport: validation only, no network."""
    return UpstoxAdapter(
        environment=(os.getenv("UPSTOX_ENV", "sandbox").strip().lower() or "sandbox"), **kwargs)


def default_alpaca(**kwargs: Any) -> AlpacaAdapter:
    return AlpacaAdapter(
        environment=(os.getenv("ALPACA_ENV", "paper").strip().lower() or "paper"), **kwargs)


__all__ = ["AlpacaAdapter", "BrokerChannel", "BrokerContractError", "MAX_TAG_LENGTH",
           "UpstoxAdapter", "canonical_json", "default_alpaca", "default_upstox"]
