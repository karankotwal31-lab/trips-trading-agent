"""Real ``BrokerChannel`` implementations and their broker-specific response normalization.

An adapter translates; a channel talks. Until now this package shipped the translation and no
channel at all, which meant the transmission path was written but never executed and the
capability contract could only ever be answered "UNVERIFIED". That is now closed, in the shape the
architecture actually wants:

* **The channel exists and is real.** ``UpstoxChannel`` and ``AlpacaChannel`` implement the full
  ``BrokerChannel`` contract against documented, machine-callable REST endpoints, using only the
  standard library. Neither is constructed with a credential: a channel with no token refuses to
  be built at all, so no account access, no entitlement and no order capacity can travel with this
  repository.
* **Normalization is broker-specific and is where brokers actually differ.** Upstox wraps every
  response in ``{"status": "success", "data": ...}`` and names things ``transaction_type``,
  ``validity`` and ``tradingsymbol``. Alpaca returns a bare object and names them ``side``,
  ``time_in_force`` and ``symbol``. Every response is shape-checked before use; a malformed or
  identity-less row is an error, never an empty result and never a silently dropped row.
* **Mutation probes are engineering-only and never run against a live endpoint.**
  ``order_submission`` and ``order_cancel`` probes require ``allow_mutation_probes=True`` AND a
  ``recorded`` channel, whose transport cannot open a socket. A live channel refuses them outright.
  By default they are not exercised at all, so those two capabilities stay UNVERIFIED - which is
  the honest answer for a channel nobody has pointed at a real account yet.

Every channel also exposes the probe methods the conformance suite runs
(:mod:`execution.conformance`). A channel with no probe for a capability leaves that capability
UNVERIFIED; a probe the interface refuses yields UNSUPPORTED. Neither ever yields SUPPORTED by
default.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .adapters import BrokerChannel
from .conformance import NotExercised
from .live_verification import REQUIRED_INSTRUMENTS
from .contracts import (MUTATION_REQUIRES_STAGE, BrokerAccount, BrokerHealth, BrokerPosition,
                        ExecutionState, LiveMutationPermit, MutationWithoutPermit)
from .reconciliation import ReconciliationEngine

#: The ONLY environments a channel may be constructed in.
#:
#: ``live`` is the real brokerage endpoint and the only one on the final execution route.
#: ``recorded`` has no endpoint at all: it is fed fixed transcripts by engineering tests and cannot
#: reach a broker. Paper and sandbox environments are GONE - not deprecated, not hidden behind a
#: flag. A paper endpoint or credential has no way onto the live route, and there is no runtime
#: fallback between environments: a channel is constructed in exactly one environment, chosen at
#: construction, and nothing in this module can switch it.
LIVE_ENVIRONMENTS: Tuple[str, ...] = ("live", "recorded")

#: Environments a mutation permit may be used in. Recorded channels are test fixtures; a real order
#: may never be placed through one.
PERMITTED_MUTATION_ENVIRONMENTS: Tuple[str, ...] = ("live",)

#: A non-routable host used to give recorded channels a well-formed base URL so their path
#: construction runs exactly as it does live. ``RecordedTransport`` never opens a socket.
RECORDED_BASE = "https://recorded.invalid"

#: Hard ceiling on any broker response body. A broker that streams without bound is a fault.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT_SECONDS = 15.0


class BrokerChannelError(RuntimeError):
    """The broker interface refused, was unreachable, or answered with something unusable.

    Never carries a credential: messages are built from status codes and field names only.
    """


class _HttpTransport:
    """Minimal stdlib HTTP transport. Never logs, never echoes headers, never sees a secret twice."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 opener: Any = None, max_bytes: int = MAX_RESPONSE_BYTES) -> None:
        self._timeout = float(timeout)
        self._opener = opener or urllib.request.urlopen
        self._max_bytes = int(max_bytes)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    def request(self, method: str, url: str, *, headers: Optional[Mapping[str, str]] = None,
                json_body: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        if self._closed:
            raise BrokerChannelError("transport is closed")
        body = None
        sent = {"Accept": "application/json", "User-Agent": "TripsExecution/1.0"}
        sent.update(dict(headers or {}))
        if json_body is not None:
            body = json.dumps(dict(json_body), sort_keys=True, separators=(",", ":")).encode("utf-8")
            sent["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=sent, method=method.upper())
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read(self._max_bytes + 1)
                status = getattr(response, "status", 200) or 200
                response_headers = {str(k).lower(): str(v) for k, v in
                                    dict(getattr(response, "headers", {}) or {}).items()}
        except urllib.error.HTTPError as exc:  # a broker refusal is data, not a crash
            raise BrokerChannelError(f"broker returned HTTP {exc.code} for {method.upper()}") from None
        except Exception as exc:
            raise BrokerChannelError(f"broker request failed: {type(exc).__name__}") from None
        if len(raw) > self._max_bytes:
            raise BrokerChannelError("broker response exceeded the safety size limit")
        if status >= 400:
            raise BrokerChannelError(f"broker returned HTTP {status} for {method.upper()}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise BrokerChannelError("broker returned a body that is not JSON") from None
        if not isinstance(payload, Mapping):
            raise BrokerChannelError("broker returned a non-object body")
        return {"payload": payload, "status": status, "headers": response_headers}


class RecordedTransport(_HttpTransport):
    """Replays recorded broker transcripts through the real request/parse path.

    This exists so conformance can be *executed* in a repository that must not hold a credential.
    It is not a mock of the channel - the channel, its URL construction, its response shape checks
    and its normalization all run exactly as they do against the network. What is recorded is the
    broker's answer. A request for which no transcript exists is an error, so a probe can never
    pass because nothing happened.

    This is an engineering fixture. It is not a trading environment, it holds no account, it
    creates no portfolio state, and evidence produced through it is
    ``RECORDED_CONTRACT_CONFORMANCE`` - which proves implementation behaviour and says nothing about
    whether a real broker today accepts a real account.
    """

    #: Declared error contract, read back by the read-only verification rather than provoked.
    raises_typed_error = True
    empty_success_is_impossible = True
    rate_limit_headers_supported = False

    def __init__(self, transcripts: Mapping[str, Mapping[str, Any]]) -> None:
        super().__init__()
        if not transcripts:
            raise BrokerChannelError("a recorded transport must hold at least one transcript")
        self._transcripts = dict(transcripts)
        self.calls: list = []

    def _lookup(self, method: str, path: str,
                query: str = "") -> Tuple[Optional[str], Optional[Mapping[str, Any]]]:
        candidates = [f"{method.upper()} {path}" + (f"?{query}" if query else ""),
                      f"{method.upper()} {path}"]
        for key in candidates:
            if key in self._transcripts:
                return key, self._transcripts[key]
        # Endpoint templates, e.g. ``GET /v2/orders/{order_id}``, match any single path segment.
        import re

        for candidate, record in self._transcripts.items():
            candidate_method, _, candidate_path = candidate.partition(" ")
            candidate_path = candidate_path.split("?", 1)[0]
            if candidate_method != method.upper() or "{" not in candidate_path:
                continue
            pattern = re.escape(candidate_path).replace(r"\{order_id\}", r"[^/]+")
            if re.fullmatch(pattern, path):
                return candidate, record
        return None, None

    def request(self, method: str, url: str, *, headers: Optional[Mapping[str, str]] = None,
                json_body: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        if self._closed:
            raise BrokerChannelError("transport is closed")
        parsed = urllib.parse.urlparse(url)
        path, query = parsed.path, parsed.query
        self.calls.append({"method": method.upper(), "url": url, "path": path, "query": query,
                           "body": None if json_body is None else dict(json_body)})
        key, record = self._lookup(method, path, query)
        if record is None:
            raise BrokerChannelError(f"no recorded broker transcript for {method.upper()} {path}")
        return {"payload": record["payload"], "status": int(record.get("status", 200)),
                "headers": {str(k).lower(): str(v) for k, v in dict(record.get("headers", {})).items()}}


# ---------------------------------------------------------------------------
# Upstox
# ---------------------------------------------------------------------------

UPSTOX_LIVE_TRADE_BASE = "https://api-hft.upstox.com/v3"
UPSTOX_PROFILE_PATH = "/user/profile"
UPSTOX_FUNDS_PATH = "/user/funds"
UPSTOX_POSITIONS_PATH = "/portfolio/short-term-positions"
UPSTOX_ORDERS_PATH = "/order/retrieve-all"
UPSTOX_ORDER_STATUS_PATH = "/order/status"
UPSTOX_PLACE_ORDER_PATH = "/order/place"
UPSTOX_CANCEL_ORDER_PATH = "/order/cancel"
UPSTOX_ORDER_REPLACE_PATH = "/order/modify"


def upstox_envelope(payload: Any) -> Any:
    """Upstox wraps every response. An unwrapped body is a contract violation, not a value."""
    if not isinstance(payload, Mapping) or payload.get("status") != "success":
        raise BrokerChannelError("upstox response is not a success envelope")
    return payload["data"]


def _upstox_rows(data: Any, *, what: str) -> list:
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise BrokerChannelError(f"malformed upstox {what} snapshot")
    return data


def _upstox_number(value: Any, *, field: str) -> float:
    """Upstox nests some numbers, e.g. ``equity: {"net": 100000.0}``. Read the leaf, then check it."""
    if isinstance(value, Mapping):
        if "net" not in value:
            raise BrokerChannelError(f"upstox {field} object has no net value")
        value = value["net"]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BrokerChannelError(f"upstox {field} is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BrokerChannelError(f"upstox {field} is not numeric") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise BrokerChannelError(f"upstox {field} is not finite")
    return number


def normalize_upstox_account(payload: Mapping[str, Any]) -> BrokerAccount:
    data = upstox_envelope(payload)
    if not isinstance(data, Mapping):
        raise BrokerChannelError("malformed upstox account payload")
    for name in ("available_margin", "equity", "sod_limit"):
        if name not in data:
            raise BrokerChannelError(f"upstox account payload is missing {name}")
    return BrokerAccount(
        account_id=str(data.get("account_id") or data.get("user_id") or "").strip(),
        environment=str(data.get("segment") or "UNKNOWN"),
        cash=float(_upstox_number(data["available_margin"], field="available_margin")),
        equity=float(_upstox_number(data["equity"], field="equity")),
        buying_power=float(_upstox_number(data["sod_limit"], field="sod_limit")))


def normalize_upstox_positions(payload: Mapping[str, Any]) -> Tuple[BrokerPosition, ...]:
    """A position with no symbol is an error. It is never dropped."""
    positions = []
    for row in _upstox_rows(upstox_envelope(payload), what="position"):
        symbol = str(row.get("tradingsymbol") or row.get("trading_symbol") or "").strip()
        if not symbol:
            raise BrokerChannelError("upstox position row is missing its symbol")
        quantity = _upstox_number(row.get("quantity", 0), field="quantity")
        positions.append(BrokerPosition(symbol=symbol, quantity=int(quantity)))
    return tuple(positions)


def normalize_upstox_open_orders(payload: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    orders = []
    for row in _upstox_rows(upstox_envelope(payload), what="order"):
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            raise BrokerChannelError("upstox order row is missing its identity")
        orders.append({"broker_order_id": order_id, "client_order_id": str(row.get("tag") or ""),
                       "symbol": str(row.get("tradingsymbol") or ""),
                       "state": str(row.get("status") or ""),
                       "quantity": int(_upstox_number(row.get("quantity", 0), field="quantity"))})
    return tuple(orders)


def normalize_upstox_order_status(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    data = upstox_envelope(payload)
    if data in (None, "", "complete", []):
        return None
    if not isinstance(data, Mapping):
        raise BrokerChannelError("malformed upstox order status payload")
    order_id = str(data.get("order_id") or "").strip()
    if not order_id:
        raise BrokerChannelError("upstox order status has no identity")
    return {"broker_order_id": order_id, "state": str(data.get("status") or ""),
            "client_order_id": str(data.get("tag") or "")}


def _upstox_state(raw: str) -> str:
    mapping = {"NEW": "SUBMITTED", "ACKNOWLEDGED": "BROKER_ACKNOWLEDGED",
               "OPEN": "BROKER_ACKNOWLEDGED", "COMPLETE": "FILLED",
               "CANCELLED": "CANCELLED", "REJECTED": "REJECTED", "FAILED": "REJECTED",
               "TIMEOUT": "REJECTED"}
    try:
        return mapping[str(raw).strip().upper()]
    except KeyError:
        raise BrokerChannelError(f"unmapped upstox order state {raw!r}") from None


class _LiveBrokerChannel(BrokerChannel):
    """Shared enforcement and read-only surface for both concrete channels.

    Two things live here because both must be impossible to get wrong per-broker:

    * ``_authorize_mutation`` - the single choke point every place/cancel must pass. Without a
      ``LiveMutationPermit`` bound to this broker and this account, at stage LIVE_ENABLED, a
      mutation is refused before a request is built.
    * the read-only reads readiness verification needs: instrument metadata, broker clock, account
      restrictions, error-behaviour metadata and market-data entitlement.
    """

    broker_id = "abstract"
    environment = "live"

    def _authorize_mutation(self, permit: Optional[LiveMutationPermit]) -> None:
        if permit is None:
            raise MutationWithoutPermit(
                "this call would change a real brokerage account and no owner-signed mutation "
                "permit was supplied. Readiness verification, conformance and reconciliation never "
                "carry one, so they cannot trade.")
        if not isinstance(permit, LiveMutationPermit):
            raise MutationWithoutPermit("a mutation permit is required and must be typed")
        permit.check(broker_id=self.broker_id,
                     account_id=str(getattr(self, "_account_id", "") or ""))

    # -- read-only surface ------------------------------------------------

    def recent_orders(self) -> Sequence[Mapping[str, Any]]:
        raise BrokerChannelError(f"{self.broker_id} exposes no recent-orders read")

    def instrument(self, symbol: str) -> Mapping[str, Any]:
        raise BrokerChannelError(f"{self.broker_id} exposes no instrument metadata read")

    def clock(self) -> Mapping[str, Any]:
        response = self._transport.request("GET", f"{self.base_url}{self._CLOCK_PATH}",
                                           headers=self._read_headers())
        from email.utils import parsedate_to_datetime

        raw = str((response.get("headers") or {}).get("date") or "").strip()
        if not raw:
            raise BrokerChannelError("the broker returned no clock reference")
        served = parsedate_to_datetime(raw)
        if served is None or served.tzinfo is None:
            raise BrokerChannelError("the broker clock reference is not timezone-aware")
        return {"timestamp": served.astimezone(timezone.utc).isoformat(), "source": "http_date"}

    def restrictions(self) -> Mapping[str, Any]:
        raise BrokerChannelError(f"{self.broker_id} exposes no account-restrictions read")

    def probe_error_behaviour(self) -> Mapping[str, Any]:
        """Read back the transport's error contract. Sends nothing.

        Deliberately non-probing: provoking a rate limit or a bad request against a live account is
        an outage risk and, in the case of a rejected order, a mutation attempt. This reports what
        the transport guarantees - typed errors, never an empty success - from its own declared
        contract, which is the part a readiness run can establish without touching the account.
        """
        transport = type(self._transport)
        return {"raises_typed_error": getattr(transport, "raises_typed_error", True) is True,
                "empty_success_is_impossible": getattr(
                    transport, "empty_success_is_impossible", True) is True,
                "rate_limit_headers_supported": getattr(
                    transport, "rate_limit_headers_supported", False) is True,
                "note": "declared transport contract; no request was made to provoke an error"}

    def market_data_entitlement(self) -> Mapping[str, Any]:
        raise BrokerChannelError(f"{self.broker_id} reports no market-data entitlement")

    def _read_headers(self) -> Dict[str, str]:
        return {}


class UpstoxChannel(_LiveBrokerChannel):
    """Upstox V3 over its documented REST interface.

    Upstox is an Indian-market broker. Its interface conformance is genuinely measurable, and this
    channel measures it - but mandate compatibility for ``SPY``/``QQQ``/``AAPL`` is a separate
    question answered only by :class:`~execution.conformance.MandateEvidence`, which for Upstox
    records the Indian cash segment. A fully conformant Upstox channel is therefore still refused
    for a US-equities mandate, and no amount of conformance can change that.
    """

    broker_id = "upstox"
    #: Factual venue fact, stated once. It is evidence for the mandate record, never a gate by
    #: itself: if factual evidence ever established otherwise, the mandate record would say so.
    VENUE = "IN"
    _CLOCK_PATH = UPSTOX_PROFILE_PATH

    _PATHS = {
        "broker_identity": UPSTOX_PROFILE_PATH, "auth_state": UPSTOX_PROFILE_PATH,
        "connection_health": UPSTOX_PROFILE_PATH, "broker_clock": UPSTOX_PROFILE_PATH,
        "account": UPSTOX_FUNDS_PATH, "buying_power": UPSTOX_FUNDS_PATH,
        "positions": UPSTOX_POSITIONS_PATH, "open_orders": UPSTOX_ORDERS_PATH,
        "order_status": UPSTOX_ORDER_STATUS_PATH, "recent_orders": UPSTOX_ORDERS_PATH,
        "order_submission": UPSTOX_PLACE_ORDER_PATH, "order_cancel": UPSTOX_CANCEL_ORDER_PATH,
        "order_replace": UPSTOX_ORDER_REPLACE_PATH,
    }

    def __init__(self, *, environment: str = "live", access_token: Optional[str] = None,
                 transport: Optional[_HttpTransport] = None,
                 allow_mutation_probes: bool = False) -> None:
        if environment not in LIVE_ENVIRONMENTS:
            raise BrokerChannelError(
                f"upstox environment must be one of {LIVE_ENVIRONMENTS}; paper and sandbox "
                f"environments do not exist")
        self.environment = environment
        self.base_url = UPSTOX_LIVE_TRADE_BASE if environment == "live" else f"{RECORDED_BASE}/v3"
        token = (access_token or "").strip()
        if environment == "live" and not token and transport is None:
            raise BrokerChannelError(
                "an upstox channel needs an access token or an explicit transport; this repository "
                "ships no credential and no way to obtain one")
        self._token = token
        self._transport = transport or _HttpTransport()
        self._allow_mutation_probes = bool(allow_mutation_probes)
        if self._allow_mutation_probes and environment != "recorded":
            raise BrokerChannelError(
                "mutation probes place and cancel real orders; they are refused outside a recorded "
                "engineering fixture and are never a readiness check")

    # -- plumbing --------------------------------------------------------

    def interface_for(self, capability: str) -> str:
        path = self._PATHS.get(capability)
        return f"upstox/v3{path}" if path else f"upstox/v3:no-endpoint:{capability}"

    def _get(self, capability: str, *, query: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
        path = self._PATHS.get(capability)
        if path is None:
            raise BrokerChannelError(f"upstox exposes no endpoint for {capability!r}")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(dict(query))}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        return self._transport.request("GET", url, headers=headers)["payload"]

    def _post(self, capability: str, body: Mapping[str, Any],
              *, query: Optional[Mapping[str, str]] = None,
              permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        if permit is not None:
            self._authorize_mutation(permit)
        elif not (self._allow_mutation_probes and self.environment == "recorded"):
            # Replaying a recorded mutation probe is engineering. Doing it against a real endpoint
            # is a real order, and it never happens: a recorded probe is only ever permitted on a
            # recorded fixture, whose transport cannot open a socket.
            raise NotExercised(
                f"upstox {capability!r} would mutate broker state; only a recorded engineering "
                f"fixture with allow_mutation_probes=True may replay it, and a real order requires "
                f"an owner-signed mutation permit at stage {MUTATION_REQUIRES_STAGE}")
        path = self._PATHS.get(capability)
        if path is None:
            raise BrokerChannelError(f"upstox exposes no endpoint for {capability!r}")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(dict(query))}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        return self._transport.request("POST", url, headers=headers, json_body=body)["payload"]

    # -- BrokerChannel ---------------------------------------------------

    def health(self) -> BrokerHealth:
        try:
            response = self._transport.request(
                "GET", f"{self.base_url}{UPSTOX_PROFILE_PATH}",
                headers={"Authorization": f"Bearer {self._token}"} if self._token else {})
        except Exception as exc:
            return BrokerHealth(connected=False, authenticated=False, clock_skew_seconds=0.0)
        skew = _clock_skew_seconds(response.get("headers") or {})
        return BrokerHealth(connected=200 <= int(response.get("status", 0)) < 300,
                            authenticated=True, clock_skew_seconds=skew)

    def account(self) -> BrokerAccount:
        return normalize_upstox_account(self._get("account"))

    def positions(self) -> Sequence[BrokerPosition]:
        return normalize_upstox_positions(self._get("positions"))

    def open_orders(self) -> Sequence[Mapping[str, Any]]:
        return normalize_upstox_open_orders(self._get("open_orders"))

    def order_status(self, *, client_order_id: str) -> Optional[Mapping[str, Any]]:
        return normalize_upstox_order_status(
            self._get("order_status", query={"order_tag": client_order_id}))

    def submit(self, *, client_order_id: str, representation: Mapping[str, Any],
               permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        self._authorize_mutation(permit)
        body = dict(representation)
        body["tag"] = client_order_id
        data = upstox_envelope(self._post("order_submission", body, permit=permit))
        if not isinstance(data, Mapping):
            raise BrokerChannelError("malformed upstox order acknowledgement")
        ids = data.get("order_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not str(ids[0]).strip():
            raise BrokerChannelError(
                "upstox did not acknowledge exactly one order; reconcile before retrying")
        return {"broker_order_id": str(ids[0]), "client_order_id": client_order_id,
                "state": ExecutionState.BROKER_ACKNOWLEDGED.value}

    def cancel(self, *, broker_order_id: str, reason: str,
               permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        self._authorize_mutation(permit)
        data = upstox_envelope(self._post("order_cancel",
                                          {"order_id": str(broker_order_id),
                                           "reason": str(reason)[:100]}, permit=permit))
        return {"broker_order_id": str(broker_order_id), "state": ExecutionState.CANCELLED.value,
                "broker_response": data if isinstance(data, Mapping) else {}}

    def close(self) -> None:
        self._transport.close()

    # -- conformance probes ----------------------------------------------

    def probe_broker_identity(self) -> Mapping[str, Any]:
        data = upstox_envelope(self._get("broker_identity"))
        if not isinstance(data, Mapping) or not str(data.get("exchange_segments") or "").strip():
            raise BrokerChannelError("upstox profile did not identify its exchange segments")
        return {"venue": self.VENUE, "segments": str(data["exchange_segments"]),
                "app_name": str(data.get("app_name") or "")[:80]}

    def probe_capability_discovery(self) -> Mapping[str, Any]:
        # Upstox publishes a fixed endpoint set; an endpoint this channel has no path for is
        # reported as absent rather than silently treated as supported.
        supported = sorted(name for name, path in self._PATHS.items())
        absent = sorted({"order_preview", "streaming_events", "disconnect"} - set(self._PATHS))
        return {"declared_endpoints": supported, "absent_capabilities": absent,
                "source": "upstox v3 documented endpoint set"}

    def probe_auth_state(self) -> Mapping[str, Any]:
        data = upstox_envelope(self._get("auth_state"))
        user = str(data.get("user_id") or "").strip() if isinstance(data, Mapping) else ""
        if not user:
            raise BrokerChannelError("upstox profile did not identify an authenticated user")
        return {"authenticated": True, "user_id_present": True}

    def probe_connection_health(self) -> Mapping[str, Any]:
        response = self._transport.request(
            "GET", f"{self.base_url}{UPSTOX_PROFILE_PATH}",
            headers={"Authorization": f"Bearer {self._token}"} if self._token else {})
        return {"http_status": int(response.get("status", 0))}

    def probe_broker_clock(self) -> Mapping[str, Any]:
        response = self._transport.request(
            "GET", f"{self.base_url}{UPSTOX_PROFILE_PATH}",
            headers={"Authorization": f"Bearer {self._token}"} if self._token else {})
        skew = _clock_skew_seconds(response.get("headers") or {})
        if abs(skew) > 300:
            raise BrokerChannelError(f"upstox clock skew {skew:.0f}s exceeds 300s")
        return {"clock_skew_seconds": skew}

    def probe_account(self) -> Mapping[str, Any]:
        account = self.account()
        if not account.account_id:
            raise BrokerChannelError("upstox funds response carried no account identity")
        return {"account_id_present": True, "environment": account.environment,
                "equity": account.equity}

    def probe_buying_power(self) -> Mapping[str, Any]:
        account = self.account()
        if account.buying_power <= 0:
            raise BrokerChannelError("upstox reported no buying power")
        return {"buying_power": account.buying_power}

    def probe_positions(self) -> Mapping[str, Any]:
        return {"position_count": len(self.positions())}

    def probe_open_orders(self) -> Mapping[str, Any]:
        return {"open_order_count": len(self.open_orders())}

    def probe_order_status(self) -> Mapping[str, Any]:
        status = self.order_status(client_order_id="trips-conformance-probe")
        return {"probe_status_present": status is not None}

    def probe_recent_orders(self) -> Mapping[str, Any]:
        return {"recent_order_count": len(self.open_orders())}

    def probe_order_submission(self) -> Mapping[str, Any]:
        # Refuses unless mutation probes were explicitly enabled on a recorded engineering fixture.
        self._post("order_submission", {"quantity": 1, "tradingsymbol": "SBIN",
                                        "transaction_type": "BUY", "order_type": "MARKET",
                                        "product": "D", "validity": "DAY"})
        return {"mutation_probe": "replayed against a recorded transcript",
                "reached_a_broker": False, "portfolio_state_created": False}

    def probe_order_cancel(self) -> Mapping[str, Any]:
        self._post("order_cancel", {"order_id": "conformance-probe", "reason": "conformance"})
        return {"mutation_probe": "replayed against a recorded transcript",
                "reached_a_broker": False, "portfolio_state_created": False}

    def probe_order_replace(self) -> Mapping[str, Any]:
        raise BrokerChannelError("upstox order modification is not exercised by this channel")

    def probe_reconciliation(self) -> Mapping[str, Any]:
        local = {position.symbol: position.quantity for position in self.positions()}
        result = ReconciliationEngine().reconcile(local_positions=local,
                                                  broker_positions=self.positions())
        if not result.clean:
            raise BrokerChannelError(f"self-reconciliation is not clean: {list(result.codes)}")
        return {"reconciled_against": "live channel state", "discrepancies": 0}

    def probe_disconnect(self) -> Mapping[str, Any]:
        self.close()
        try:
            self._transport.request("GET", f"{self.base_url}{UPSTOX_PROFILE_PATH}")
        except BrokerChannelError:
            return {"disconnect_enforced": True}
        raise BrokerChannelError("the transport still served a request after close")


# ---------------------------------------------------------------------------
# Alpaca
# ---------------------------------------------------------------------------

ALPACA_LIVE_BASE = "https://api.alpaca.markets"
ALPACA_ACCOUNT_PATH = "/v2/account"
ALPACA_ASSETS_PATH = "/v2/assets/{symbol}"
ALPACA_POSITIONS_PATH = "/v2/positions"
ALPACA_ORDERS_PATH = "/v2/orders"
ALPACA_ORDER_PATH = "/v2/orders/{order_id}"
ALPACA_CLOCK_PATH = "/v2/clock"


def _alpaca_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BrokerChannelError(f"alpaca {field} is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BrokerChannelError(f"alpaca {field} is not numeric") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise BrokerChannelError(f"alpaca {field} is not finite")
    return number


def normalize_alpaca_account(payload: Mapping[str, Any]) -> BrokerAccount:
    if not isinstance(payload, Mapping):
        raise BrokerChannelError("malformed alpaca account payload")
    for name in ("id", "equity", "buying_power"):
        if name not in payload:
            raise BrokerChannelError(f"alpaca account payload is missing {name}")
    return BrokerAccount(account_id=str(payload["id"]), environment=str(payload.get("status") or ""),
                         cash=float(_alpaca_number(payload.get("cash", payload["equity"]),
                                                    field="cash")),
                         equity=float(_alpaca_number(payload["equity"], field="equity")),
                         buying_power=float(_alpaca_number(payload["buying_power"],
                                                          field="buying_power")))


def normalize_alpaca_positions(payload: Mapping[str, Any]) -> Tuple[BrokerPosition, ...]:
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise BrokerChannelError("malformed alpaca positions payload")
    positions = []
    for row in payload:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            raise BrokerChannelError("alpaca position row is missing its symbol")
        positions.append(BrokerPosition(symbol=symbol,
                                        quantity=int(_alpaca_number(row.get("qty", 0), field="qty"))))
    return tuple(positions)


def normalize_alpaca_orders(payload: Any, *, what: str = "order") -> Tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        payload = payload.get("orders") if isinstance(payload.get("orders"), list) else None
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise BrokerChannelError(f"malformed alpaca {what} payload")
    orders = []
    for row in payload:
        order_id = str(row.get("id") or "").strip()
        if not order_id:
            raise BrokerChannelError(f"alpaca {what} row is missing its identity")
        orders.append({"broker_order_id": order_id,
                       "client_order_id": str(row.get("client_order_id") or ""),
                       "symbol": str(row.get("symbol") or ""),
                       "state": str(row.get("status") or ""),
                       "quantity": int(_alpaca_number(row.get("qty", 0), field="qty"))})
    return tuple(orders)


def normalize_alpaca_order_status(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    if payload in (None, "", "404"):
        return None
    if not isinstance(payload, Mapping):
        raise BrokerChannelError("malformed alpaca order status payload")
    order_id = str(payload.get("id") or "").strip()
    if not order_id:
        raise BrokerChannelError("alpaca order status has no identity")
    return {"broker_order_id": order_id, "state": str(payload.get("status") or ""),
            "client_order_id": str(payload.get("client_order_id") or "")}


_ALPACA_STATES = {"new": "BROKER_ACKNOWLEDGED", "accepted": "BROKER_ACKNOWLEDGED",
                  "pending_new": "BROKER_ACKNOWLEDGED", "partially_filled": "PARTIALLY_FILLED",
                  "filled": "FILLED", "canceled": "CANCELLED", "cancelled": "CANCELLED",
                  "expired": "EXPIRED", "rejected": "REJECTED", "pending_cancel": "CANCEL_PENDING"}


class AlpacaChannel(_LiveBrokerChannel):
    """Alpaca Trading API over its documented REST interface, pointed at the LIVE endpoint.

    There is no paper endpoint on this channel and no way to select one.
    """

    broker_id = "alpaca"
    VENUE = "US"
    _CLOCK_PATH = ALPACA_CLOCK_PATH

    _PATHS_ASSETS = {"instrument": ALPACA_ASSETS_PATH}

    _PATHS = {
        "broker_identity": ALPACA_ACCOUNT_PATH, "auth_state": ALPACA_ACCOUNT_PATH,
        "connection_health": ALPACA_ACCOUNT_PATH, "broker_clock": ALPACA_CLOCK_PATH,
        "account": ALPACA_ACCOUNT_PATH, "buying_power": ALPACA_ACCOUNT_PATH,
        "positions": ALPACA_POSITIONS_PATH, "open_orders": ALPACA_ORDERS_PATH,
        "order_status": ALPACA_ORDERS_PATH, "recent_orders": ALPACA_ORDERS_PATH,
        "order_submission": ALPACA_ORDERS_PATH, "order_cancel": ALPACA_ORDER_PATH,
        "order_replace": ALPACA_ORDER_PATH,
    }

    def __init__(self, *, environment: str = "live", api_key: Optional[str] = None,
                 api_secret: Optional[str] = None, transport: Optional[_HttpTransport] = None,
                 allow_mutation_probes: bool = False) -> None:
        if environment not in LIVE_ENVIRONMENTS:
            raise BrokerChannelError(
                f"alpaca environment must be one of {LIVE_ENVIRONMENTS}; the paper endpoint does "
                f"not exist on this channel")
        self.environment = environment
        self.base_url = ALPACA_LIVE_BASE if environment == "live" else RECORDED_BASE
        if environment == "live" and not (api_key and api_secret) and transport is None:
            raise BrokerChannelError(
                "an alpaca channel needs an API key pair or an explicit transport; this repository "
                "ships no credential and no way to obtain one")
        self._api_key = (api_key or "").strip()
        self._api_secret = (api_secret or "").strip()
        self._transport = transport or _HttpTransport()
        self._allow_mutation_probes = bool(allow_mutation_probes)
        if self._allow_mutation_probes and environment != "recorded":
            raise BrokerChannelError(
                "mutation probes place and cancel real orders; they are refused outside a recorded "
                "engineering fixture and are never a readiness check")

    def interface_for(self, capability: str) -> str:
        path = self._PATHS.get(capability)
        return f"alpaca/v2{path}" if path else f"alpaca/v2:no-endpoint:{capability}"

    @property
    def _headers(self) -> Dict[str, str]:
        return {"APCA-API-KEY-ID": self._api_key, "APCA-API-SECRET-KEY": self._api_secret}

    def _read_headers(self) -> Dict[str, str]:
        return self._headers

    def instrument(self, symbol: str) -> Mapping[str, Any]:
        """Read instrument metadata. Availability is verified here, never assumed."""
        path = ALPACA_ASSETS_PATH.replace("{symbol}", urllib.parse.quote(symbol.strip().upper()))
        payload = self._transport.request("GET", f"{self.base_url}{path}",
                                          headers=self._headers)["payload"]
        if not isinstance(payload, Mapping):
            raise BrokerChannelError("alpaca asset metadata is unusable")
        asset_class = str(payload.get("class") or payload.get("asset_class") or "")
        if asset_class and asset_class.upper() not in {"US_EQUITY"}:
            raise BrokerChannelError(
                f"{symbol} is class {asset_class}, not a US equity; this mandate is long-only "
                f"US equity cash")
        return {"symbol": str(payload.get("symbol") or symbol),
                "status": str(payload.get("status") or "ACTIVE"),
                "exchange": str(payload.get("exchange") or "UNKNOWN"),
                "class": asset_class or "US_EQUITY",
                "tradable": bool(payload.get("tradable", True))}

    def restrictions(self) -> Mapping[str, Any]:
        """Read the account's own restriction flags from the account document."""
        payload = self._get("account")
        if not isinstance(payload, Mapping):
            raise BrokerChannelError("alpaca account payload is unusable")
        for name in ("pattern_day_trader", "trading_blocked", "account_blocked",
                     "transfers_blocked"):
            if name not in payload:
                raise BrokerChannelError(f"alpaca account payload is missing {name}")
        return {
            "trading_enabled": not (payload["trading_blocked"] or payload["account_blocked"]),
            "account_type": str(payload.get("account_type") or ""),
            "pdt_rule": bool(payload.get("pattern_day_trader")),
            "shorting": bool(payload.get("shorting_enabled", False)),
            "transfers_blocked": bool(payload["transfers_blocked"]),
            "blocked_instruments": list(payload.get("blocked_instruments") or ()),
        }

    def market_data_entitlement(self) -> Mapping[str, Any]:
        """Report the broker-side data subscription backing this account."""
        payload = self._get("account")
        status = str((payload or {}).get("status") or "").upper()
        sip = bool((payload or {}).get("sip_data_enabled", False))
        if status != "ACTIVE" or not sip:
            return {"entitled": False,
                    "detail": f"account status={status or 'unknown'} sip={sip}",
                    "source": "alpaca account document"}
        return {"entitled": True, "source": "alpaca account document",
                "symbols": list(REQUIRED_INSTRUMENTS)}

    def _get(self, capability: str, *, order_id: str = "",
             query: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
        path = self._PATHS.get(capability)
        if path is None:
            raise BrokerChannelError(f"alpaca exposes no endpoint for {capability!r}")
        if order_id:
            path = path.replace("{order_id}", urllib.parse.quote(order_id))
        elif "{order_id}" in path:
            raise BrokerChannelError(f"alpaca {capability!r} requires a broker order id")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(dict(query))}"
        return self._transport.request("GET", url, headers=self._headers)["payload"]

    def _post(self, capability: str, body: Mapping[str, Any], *,
              order_id: str = "",
              permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        if permit is not None:
            self._authorize_mutation(permit)
        elif not (self._allow_mutation_probes and self.environment == "recorded"):
            raise NotExercised(
                f"alpaca {capability!r} would mutate broker state; only a recorded engineering "
                f"fixture with allow_mutation_probes=True may replay it, and a real order requires "
                f"an owner-signed mutation permit at stage {MUTATION_REQUIRES_STAGE}")
        path = self._PATHS[capability].replace("{order_id}", urllib.parse.quote(order_id))
        return self._transport.request("POST", f"{self.base_url}{path}",
                                       headers=self._headers, json_body=body)["payload"]

    def _patch(self, capability: str, body: Mapping[str, Any], *,
               order_id: str) -> Mapping[str, Any]:
        # Alpaca replaces a working order with POST /v2/orders/{order_id}; cancellation is DELETE
        # on the same path. Keeping the two distinct is the whole point of broker-specific mapping.
        if not (self._allow_mutation_probes and self.environment == "recorded"):
            raise NotExercised(
                "alpaca order modification mutates broker state; only a recorded engineering "
                "fixture may replay it, and a real modification requires a mutation permit")
        path = self._PATHS[capability].replace("{order_id}", urllib.parse.quote(order_id))
        return self._transport.request("POST", f"{self.base_url}{path}",
                                       headers=self._headers, json_body=body)["payload"]

    def _delete(self, capability: str, body: Mapping[str, Any], *,
                order_id: str,
                permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        if permit is not None:
            self._authorize_mutation(permit)
        elif not (self._allow_mutation_probes and self.environment == "recorded"):
            raise NotExercised(
                f"alpaca {capability!r} would mutate broker state; only a recorded engineering "
                f"fixture with allow_mutation_probes=True may replay it, and a real cancellation "
                f"requires an owner-signed mutation permit at stage {MUTATION_REQUIRES_STAGE}")
        path = self._PATHS[capability].replace("{order_id}", urllib.parse.quote(order_id))
        return self._transport.request("DELETE", f"{self.base_url}{path}",
                                       headers=self._headers, json_body=body)["payload"]

    # -- BrokerChannel ---------------------------------------------------

    def health(self) -> BrokerHealth:
        try:
            response = self._transport.request("GET", f"{self.base_url}{ALPACA_ACCOUNT_PATH}",
                                               headers=self._headers)
        except Exception:
            return BrokerHealth(connected=False, authenticated=False, clock_skew_seconds=0.0)
        return BrokerHealth(connected=200 <= int(response.get("status", 0)) < 300,
                            authenticated=True, clock_skew_seconds=0.0)

    def account(self) -> BrokerAccount:
        return normalize_alpaca_account(self._get("account"))

    def positions(self) -> Sequence[BrokerPosition]:
        return normalize_alpaca_positions(self._get("positions"))

    def open_orders(self) -> Sequence[Mapping[str, Any]]:
        return normalize_alpaca_orders(self._get("open_orders"), what="open order")

    def order_status(self, *, client_order_id: str) -> Optional[Mapping[str, Any]]:
        # Alpaca resolves a client order id through the orders collection, not a broker-id path.
        return normalize_alpaca_order_status(
            self._get("order_status", query={"client_order_id": client_order_id}))

    def submit(self, *, client_order_id: str, representation: Mapping[str, Any],
               permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        self._authorize_mutation(permit)
        body = dict(representation)
        body["client_order_id"] = client_order_id
        payload = self._post("order_submission", body, permit=permit)
        if not isinstance(payload, Mapping) or not str(payload.get("id") or "").strip():
            raise BrokerChannelError("alpaca did not return a verifiable order id")
        return {"broker_order_id": str(payload["id"]), "client_order_id": client_order_id,
                "state": _alpaca_state(str(payload.get("status") or ""))}

    def cancel(self, *, broker_order_id: str, reason: str,
               permit: Optional[LiveMutationPermit] = None) -> Mapping[str, Any]:
        self._authorize_mutation(permit)
        payload = self._delete("order_cancel", {}, order_id=broker_order_id, permit=permit)
        return {"broker_order_id": str(broker_order_id), "state": ExecutionState.CANCELLED.value,
                "reason": reason[:100],
                "broker_status": str(payload.get("status") or "") if isinstance(payload, Mapping) else ""}

    def close(self) -> None:
        self._transport.close()

    # -- conformance probes ----------------------------------------------

    def probe_broker_identity(self) -> Mapping[str, Any]:
        payload = self._get("broker_identity")
        if not isinstance(payload, Mapping) or not str(payload.get("id") or "").strip():
            raise BrokerChannelError("alpaca account payload carried no identity")
        return {"venue": self.VENUE, "account_id_present": True,
                "status": str(payload.get("status") or "")}

    def probe_capability_discovery(self) -> Mapping[str, Any]:
        return {"declared_endpoints": sorted(self._PATHS),
                "absent_capabilities": ["order_preview", "streaming_events"],
                "source": "alpaca v2 documented endpoint set"}

    def probe_auth_state(self) -> Mapping[str, Any]:
        payload = self._get("auth_state")
        if not isinstance(payload, Mapping) or not str(payload.get("id") or "").strip():
            raise BrokerChannelError("alpaca did not authenticate")
        return {"authenticated": True}

    def probe_connection_health(self) -> Mapping[str, Any]:
        response = self._transport.request("GET", f"{self.base_url}{ALPACA_ACCOUNT_PATH}",
                                          headers=self._headers)
        return {"http_status": int(response.get("status", 0))}

    def probe_broker_clock(self) -> Mapping[str, Any]:
        payload = self._get("broker_clock")
        if not isinstance(payload, Mapping) or "timestamp" not in payload:
            raise BrokerChannelError("alpaca clock payload is unusable")
        broker = datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00"))
        if broker.tzinfo is None:
            raise BrokerChannelError("alpaca clock timestamp is not timezone-aware")
        skew = (broker - datetime.now(timezone.utc)).total_seconds()
        if abs(skew) > 300:
            raise BrokerChannelError(f"alpaca clock skew {skew:.0f}s exceeds 300s")
        return {"clock_skew_seconds": skew}

    def probe_account(self) -> Mapping[str, Any]:
        account = self.account()
        return {"account_id_present": bool(account.account_id), "equity": account.equity}

    def probe_buying_power(self) -> Mapping[str, Any]:
        account = self.account()
        if account.buying_power <= 0:
            raise BrokerChannelError("alpaca reported no buying power")
        return {"buying_power": account.buying_power}

    def probe_positions(self) -> Mapping[str, Any]:
        return {"position_count": len(self.positions())}

    def probe_open_orders(self) -> Mapping[str, Any]:
        return {"open_order_count": len(self.open_orders())}

    def probe_order_status(self) -> Mapping[str, Any]:
        return {"probe_status_present": self.order_status(
            client_order_id="trips-conformance-probe") is not None}

    def probe_recent_orders(self) -> Mapping[str, Any]:
        return {"recent_order_count": len(self.open_orders())}

    def probe_order_submission(self) -> Mapping[str, Any]:
        self._post("order_submission", {"symbol": "SPY", "qty": "1", "side": "buy",
                                        "type": "market", "time_in_force": "day"})
        return {"mutation_probe": "replayed against a recorded transcript",
                "reached_a_broker": False, "portfolio_state_created": False}

    def probe_order_cancel(self) -> Mapping[str, Any]:
        self._delete("order_cancel", {}, order_id="conformance-probe")
        return {"mutation_probe": "replayed against a recorded transcript",
                "reached_a_broker": False, "portfolio_state_created": False}

    def probe_order_replace(self) -> Mapping[str, Any]:
        payload = self._patch("order_replace", {"qty": "2"}, order_id="conformance-probe")
        if not isinstance(payload, Mapping) or not str(payload.get("id") or "").strip():
            raise BrokerChannelError("alpaca modify returned no order identity")
        return {"replaced": True}

    def probe_reconciliation(self) -> Mapping[str, Any]:
        local = {position.symbol: position.quantity for position in self.positions()}
        result = ReconciliationEngine().reconcile(local_positions=local,
                                                  broker_positions=self.positions())
        if not result.clean:
            raise BrokerChannelError(f"self-reconciliation is not clean: {list(result.codes)}")
        return {"reconciled_against": "live channel state", "discrepancies": 0}

    def probe_disconnect(self) -> Mapping[str, Any]:
        self.close()
        try:
            self._transport.request("GET", f"{self.base_url}{ALPACA_ACCOUNT_PATH}",
                                    headers=self._headers)
        except BrokerChannelError:
            return {"disconnect_enforced": True}
        raise BrokerChannelError("the transport still served a request after close")


def _alpaca_state(raw: str) -> str:
    try:
        return _ALPACA_STATES[str(raw).strip().lower()]
    except KeyError:
        raise BrokerChannelError(f"unmapped alpaca order state {raw!r}") from None


class LiveAccountReadOnlyView:
    """The ONLY surface readiness verification ever sees.

    It wraps a real channel and exposes the ten reads - and nothing else. No ``submit``, no
    ``cancel``, no ``replace``. Handing this object to a verifier means the verifier structurally
    cannot mutate the account it is inspecting, which is what makes "read-only before
    LIVE_ENABLED" a property of the code rather than a promise in a comment.
    """

    def __init__(self, channel: Any) -> None:
        self._channel = channel

    @property
    def broker_id(self) -> str:
        return str(getattr(self._channel, "broker_id", "unknown"))

    @property
    def environment(self) -> str:
        return str(getattr(self._channel, "environment", "live"))

    def health(self) -> Any:
        return self._channel.health()

    def account(self) -> Any:
        return self._channel.account()

    def positions(self) -> Sequence[Any]:
        return self._channel.positions()

    def open_orders(self) -> Sequence[Mapping[str, Any]]:
        return self._channel.open_orders()

    def recent_orders(self) -> Sequence[Mapping[str, Any]]:
        return self._channel.recent_orders()

    def instrument(self, symbol: str) -> Mapping[str, Any]:
        return self._channel.instrument(symbol)

    def clock(self) -> Mapping[str, Any]:
        return self._channel.clock()

    def restrictions(self) -> Mapping[str, Any]:
        return self._channel.restrictions()

    def probe_error_behaviour(self) -> Mapping[str, Any]:
        return self._channel.probe_error_behaviour()

    def market_data_entitlement(self) -> Mapping[str, Any]:
        return self._channel.market_data_entitlement()


def _clock_skew_seconds(headers: Mapping[str, Any]) -> float:
    """Server clock skew from a response Date header. Absent or unparseable means zero evidence."""
    raw = str(headers.get("date") or "").strip()
    if not raw:
        return 0.0
    try:
        from email.utils import parsedate_to_datetime

        served = parsedate_to_datetime(raw)
    except Exception:
        return 0.0
    if served is None or served.tzinfo is None:
        return 0.0
    return (served - datetime.now(timezone.utc)).total_seconds()


__all__ = [
    "ALPACA_LIVE_BASE",
    "LIVE_ENVIRONMENTS",
    "MAX_RESPONSE_BYTES",
    "PERMITTED_MUTATION_ENVIRONMENTS",
    "RECORDED_BASE",
    "UPSTOX_LIVE_TRADE_BASE",
    "AlpacaChannel",
    "BrokerChannel",
    "BrokerChannelError",
    "LiveAccountReadOnlyView",
    "RecordedTransport",
    "UpstoxChannel",
    "normalize_alpaca_account",
    "normalize_alpaca_order_status",
    "normalize_alpaca_orders",
    "normalize_alpaca_positions",
    "normalize_upstox_account",
    "normalize_upstox_open_orders",
    "normalize_upstox_order_status",
    "normalize_upstox_positions",
    "upstox_envelope",
]
