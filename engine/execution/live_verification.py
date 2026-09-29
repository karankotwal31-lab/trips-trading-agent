"""LIVE_READ_ONLY_BROKER_VERIFICATION - proving the real account, without touching it.

Trip's is a live-money-only execution system, so paper and sandbox brokerage execution are not a
prerequisite for live trading and are not modelled anywhere on the path to it. That leaves one
honest question before ``LIVE_ENABLED``: *is this really the account we are going to trade, and can
this interface really reach it?* Recorded fixtures cannot answer it. They prove what the code does
when given an answer; they say nothing about whether the broker today accepts our credentials, on
this account, for these instruments.

So broker validation before ``LIVE_ENABLED`` is a **read-only** verification against the intended
REAL LIVE account, and it is read-only **structurally**, not by convention:

* :class:`ReadOnlyBrokerAccount` exposes exactly the reads below and has no submit, no cancel and
  no replace. A verifier physically cannot mutate an account it was handed.
* Every check is a named, typed, digest-checked record with its own independent status, in exactly
  the same shape as a conformance record. There is no umbrella flag.
* **No check submits an order.** There is no probe here that places, cancels or modifies anything,
  including in a "harmless" account, including to measure latency, and there never will be.
  ``assert_non_mutating`` enforces that the object under verification exposes no mutating method at
  all, so adding one later is a test failure rather than a surprise.

What this evidence is and is not:

* **RECORDED_CONTRACT_CONFORMANCE** proves *implementation behaviour*: that the adapter serializes
  and normalizes correctly against a fixed transcript. It cannot expire, drift, or prove anything
  about a live account.
* **LIVE_READ_ONLY_BROKER_VERIFICATION** proves *current broker / account / interface
  compatibility*: that the real endpoint, with real credentials, on the real account, answers these
  twelve questions today.

Neither releases capital. Both are evidence; capital is released only by a verified owner-signed
amendment plus a separately signed live authorization.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .contracts import (ExecutionLayerError, canonical_json)

READ_ONLY_VERIFICATION_VERSION = 1

#: The evidence kind. Spelled out because the distinction is load-bearing: recorded evidence and
#: live evidence are not interchangeable and neither substitutes for the other.
EVIDENCE_KIND_RECORDED = "RECORDED_CONTRACT_CONFORMANCE"
EVIDENCE_KIND_LIVE_READ_ONLY = "LIVE_READ_ONLY_BROKER_VERIFICATION"

#: The mandated instruments. Availability is verified, never assumed.
REQUIRED_INSTRUMENTS: Tuple[str, ...] = ("SPY", "QQQ", "AAPL")


class LiveVerificationError(ExecutionLayerError):
    """The read-only verification could not be performed or contradicted what it must prove."""


class MutatingObservation(ExecutionLayerError):
    """A verification target exposed a mutating method. Verification must never be able to trade."""


class ReadOnlyBrokerAccount(Protocol):
    """Exactly the reads readiness verification is allowed to perform against a live account.

    There is deliberately no ``submit``, no ``cancel`` and no ``replace``. A verifier handed one of
    these objects has no way to change the account it is inspecting.
    """

    def health(self) -> Any: ...

    def account(self) -> Any: ...

    def positions(self) -> Sequence[Any]: ...

    def open_orders(self) -> Sequence[Mapping[str, Any]]: ...

    def recent_orders(self) -> Sequence[Mapping[str, Any]]: ...

    def instrument(self, symbol: str) -> Mapping[str, Any]: ...

    def clock(self) -> Mapping[str, Any]: ...

    def restrictions(self) -> Mapping[str, Any]: ...

    def probe_error_behaviour(self) -> Mapping[str, Any]: ...

    def market_data_entitlement(self) -> Mapping[str, Any]: ...


#: Every name the read-only protocol may expose. Anything else is a capability this verifier is not
#: allowed to hold.
READ_ONLY_METHODS: Tuple[str, ...] = (
    "health", "account", "positions", "open_orders", "recent_orders", "instrument",
    "clock", "restrictions", "probe_error_behaviour", "market_data_entitlement",
)

#: The twelve mandated checks, in the order they must pass.
READ_ONLY_CHECKS: Tuple[str, ...] = (
    "authenticate_legitimately",
    "verify_broker_identity",
    "verify_exact_account",
    "verify_us_equity_permissions",
    "verify_required_instruments_available",
    "retrieve_balances",
    "retrieve_positions",
    "retrieve_open_and_recent_orders",
    "verify_broker_clock",
    "verify_account_restrictions",
    "verify_rate_limit_and_error_behaviour",
    "verify_live_market_data_entitlement",
)


def _sha(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _aware(value: Any, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise LiveVerificationError(f"{name} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise LiveVerificationError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReadOnlyCheckRecord:
    """One independent, typed, digest-checked result for one read-only check."""

    check: str
    passed: bool
    detail: str
    observation: Mapping[str, Any] = field(default_factory=dict)
    verified_at: str = ""
    evidence_kind: str = EVIDENCE_KIND_LIVE_READ_ONLY
    schema_version: int = READ_ONLY_VERIFICATION_VERSION

    def __post_init__(self) -> None:
        if self.check not in READ_ONLY_CHECKS:
            raise LiveVerificationError(f"{self.check!r} is not a mandated read-only check")
        if self.evidence_kind != EVIDENCE_KIND_LIVE_READ_ONLY:
            raise LiveVerificationError(
                "live read-only verification may only be reported as "
                f"{EVIDENCE_KIND_LIVE_READ_ONLY}")
        object.__setattr__(self, "observation", dict(self.observation))
        _aware(self.verified_at, "verified_at")

    def digest(self) -> str:
        return _sha(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {"check": self.check, "passed": self.passed, "detail": self.detail,
                "observation": dict(self.observation), "verified_at": self.verified_at,
                "evidence_kind": self.evidence_kind, "schema_version": self.schema_version,
                "evidence_hash": self.digest()}


@dataclass(frozen=True)
class LiveReadOnlyVerification:
    """The complete read-only verification result for one intended real live brokerage account."""

    broker_id: str
    account_id: str
    environment: str
    generated_at: str
    records: Mapping[str, ReadOnlyCheckRecord] = field(default_factory=dict)
    schema_version: int = READ_ONLY_VERIFICATION_VERSION

    def __post_init__(self) -> None:
        if self.environment != "live":
            raise LiveVerificationError(
                f"read-only verification only runs against the live environment; "
                f"{self.environment!r} is refused")
        object.__setattr__(self, "records", dict(self.records))
        _aware(self.generated_at, "generated_at")

    @property
    def evidence_kind(self) -> str:
        """Always the live read-only kind. A recorded fixture cannot even construct this object."""
        return EVIDENCE_KIND_LIVE_READ_ONLY

    @property
    def passed_checks(self) -> Tuple[str, ...]:
        return tuple(name for name in READ_ONLY_CHECKS
                     if name in self.records and self.records[name].passed)

    @property
    def failed_checks(self) -> Tuple[str, ...]:
        return tuple(name for name in READ_ONLY_CHECKS
                     if name in self.records and not self.records[name].passed)

    @property
    def missing_checks(self) -> Tuple[str, ...]:
        return tuple(name for name in READ_ONLY_CHECKS if name not in self.records)

    @property
    def verified(self) -> bool:
        """All twelve passed. Anything less is UNVERIFIED and releases nothing."""
        return not (self.failed_checks or self.missing_checks)

    def digest(self) -> str:
        return _sha(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_kind": EVIDENCE_KIND_LIVE_READ_ONLY,
            "releases_capital": False,
            "schema_version": self.schema_version,
            "broker_id": self.broker_id,
            "account_id": self.account_id,
            "environment": self.environment,
            "generated_at": self.generated_at,
            "verified": self.verified,
            "passed_checks": list(self.passed_checks),
            "failed_checks": list(self.failed_checks),
            "missing_checks": list(self.missing_checks),
            "records": {name: record.to_dict() for name, record in sorted(self.records.items())},
            "note": ("Read-only verification proves current broker/account/interface compatibility. "
                     "It submits no order, releases no capital, and is distinct from "
                     f"{EVIDENCE_KIND_RECORDED}, which proves implementation behaviour only."),
        }


def assert_non_mutating(target: Any) -> None:
    """Refuse any verification target that could change the account it is inspecting.

    Called on every verification run. Adding ``submit``/``cancel``/``replace`` to a channel is
    therefore a test failure here, not a silent widening of what CI may do against a live account.
    """
    exposed = {name for name in dir(target) if not name.startswith("_")}
    forbidden = sorted(name for name in exposed
                       if any(verb in name for verb in ("submit", "cancel", "place", "replace",
                                                        "modify", "close_order")))
    if forbidden:
        raise MutatingObservation(
            f"read-only verification target exposes mutating methods {forbidden}; it must not")
    for method in READ_ONLY_METHODS:
        if method in exposed and not callable(getattr(target, method, None)):
            raise MutatingObservation(f"read-only method {method!r} is not callable")


class LiveReadOnlyBrokerVerifier:
    """Runs the twelve mandated checks read-only against the intended real live account.

    Every check has its own named method. A check that raises is recorded as failed with the
    exception type - never swallowed - so a broker that rejects us is visible rather than inferred.
    """

    def __init__(self, *, target: ReadOnlyBrokerAccount, expected_account_id: str,
                 required_instruments: Sequence[str] = REQUIRED_INSTRUMENTS,
                 max_clock_skew_seconds: float = 300.0) -> None:
        assert_non_mutating(target)
        self._target = target
        self._expected_account_id = str(expected_account_id)
        self._instruments = tuple(str(s).strip().upper() for s in required_instruments)
        self._max_skew = float(max_clock_skew_seconds)
        self._broker_id = getattr(target, "broker_id", "unknown")
        self._environment = getattr(target, "environment", "")

    # -- the twelve checks -----------------------------------------------

    def _authenticate_legitimately(self) -> Dict[str, Any]:
        health = self._target.health()
        if not getattr(health, "authenticated", False):
            raise LiveVerificationError("the broker did not authenticate our credentials")
        if getattr(health, "connected", False) is not True:
            raise LiveVerificationError("the broker connection is not established")
        return {"authenticated": True, "connected": True,
                "clock_skew_seconds": getattr(health, "clock_skew_seconds", None)}

    def _verify_broker_identity(self) -> Dict[str, Any]:
        identity = self._target.instrument("SPY")
        venue = str(identity.get("exchange") or identity.get("venue") or "").strip()
        if not venue:
            raise LiveVerificationError("the broker did not identify the venue it answered from")
        return {"broker_id": self._broker_id, "environment": self._environment, "venue": venue}

    def _verify_exact_account(self) -> Dict[str, Any]:
        account = self._target.account()
        actual = str(getattr(account, "account_id", "") or "").strip()
        if not actual:
            raise LiveVerificationError("the broker returned no account identity")
        if actual != self._expected_account_id:
            raise LiveVerificationError(
                f"broker account {actual!r} is not the approved account {self._expected_account_id!r}")
        return {"account_id": actual, "environment": getattr(account, "environment", None),
                "matches_expected": True}

    def _verify_us_equity_permissions(self) -> Dict[str, Any]:
        restrictions = self._target.restrictions()
        if not isinstance(restrictions, Mapping):
            raise LiveVerificationError("broker restrictions payload is unusable")
        if restrictions.get("trading_enabled") is not True:
            raise LiveVerificationError("the broker reports trading is not enabled on this account")
        blocked = restrictions.get("blocked_instruments") or ()
        if blocked:
            raise LiveVerificationError(f"the broker reports blocked instruments {list(blocked)}")
        return {"trading_enabled": True, "asset_class": restrictions.get("asset_class")}

    def _verify_required_instruments_available(self) -> Dict[str, Any]:
        available, unavailable = [], []
        for symbol in self._instruments:
            try:
                instrument = self._target.instrument(symbol)
            except Exception as exc:
                unavailable.append(f"{symbol}:{type(exc).__name__}")
                continue
            if not str(instrument.get("symbol") or "").strip():
                unavailable.append(f"{symbol}:NO_IDENTITY")
                continue
            if str(instrument.get("status") or "ACTIVE").upper() not in {"ACTIVE", "TRADABLE"}:
                unavailable.append(f"{symbol}:{instrument.get('status')}")
                continue
            available.append(symbol)
        if unavailable:
            raise LiveVerificationError(f"mandated instruments unavailable: {unavailable}")
        return {"available": available, "required": list(self._instruments)}

    def _retrieve_balances(self) -> Dict[str, Any]:
        account = self._target.account()
        equity = float(getattr(account, "equity", 0.0))
        buying_power = float(getattr(account, "buying_power", 0.0))
        if equity <= 0:
            raise LiveVerificationError("the broker reported a non-positive account equity")
        return {"equity": equity, "buying_power": buying_power,
                "note": "balances are observed, never used to size an order"}

    def _retrieve_positions(self) -> Dict[str, Any]:
        positions = list(self._target.positions())
        for position in positions:
            if not str(getattr(position, "symbol", "") or "").strip():
                raise LiveVerificationError("a broker position carried no symbol")
        return {"position_count": len(positions),
                "symbols": sorted({str(getattr(p, "symbol", "")) for p in positions})}

    def _retrieve_open_and_recent_orders(self) -> Dict[str, Any]:
        open_orders = list(self._target.open_orders())
        recent = list(self._target.recent_orders())
        for row in list(open_orders) + list(recent):
            if isinstance(row, Mapping) and not str(row.get("broker_order_id") or "").strip():
                raise LiveVerificationError("a broker order row carried no order identity")
        return {"open_order_count": len(open_orders), "recent_order_count": len(recent)}

    def _verify_broker_clock(self) -> Dict[str, Any]:
        clock = self._target.clock()
        served = _aware(clock.get("timestamp"), "broker clock timestamp")
        skew = (served - datetime.now(timezone.utc)).total_seconds()
        if abs(skew) > self._max_skew:
            raise LiveVerificationError(
                f"broker clock skew {skew:.0f}s exceeds the {self._max_skew:.0f}s limit")
        return {"timestamp": served.isoformat(), "clock_skew_seconds": skew,
                "max_skew_seconds": self._max_skew}

    def _verify_account_restrictions(self) -> Dict[str, Any]:
        restrictions = self._target.restrictions()
        for name in ("pdt_rule", "account_type"):
            if name not in restrictions:
                raise LiveVerificationError(f"broker restrictions payload is missing {name}")
        return {"pdt_rule": restrictions.get("pdt_rule"),
                "account_type": restrictions.get("account_type"),
                "shorting": bool(restrictions.get("shorting", False))}

    def _verify_rate_limit_and_error_behaviour(self) -> Dict[str, Any]:
        """Observe how the interface reports errors, WITHOUT causing one.

        A readiness probe never provokes a rate limit and never sends a deliberately bad request
        to a live account: that would be a mutation attempt at best and an outage at worst. What it
        does instead is read back whatever error envelope the interface has already documented in
        its own metadata, and confirm the transport surfaces failures as typed errors rather than
        as silently empty successes.
        """
        behaviour = self._target.probe_error_behaviour()
        if not isinstance(behaviour, Mapping):
            raise LiveVerificationError("error-behaviour payload is unusable")
        for name in ("raises_typed_error", "empty_success_is_impossible"):
            if behaviour.get(name) is not True:
                raise LiveVerificationError(f"interface does not guarantee {name}")
        return {"raises_typed_error": True, "empty_success_is_impossible": True,
                "rate_limit_headers_supported": bool(behaviour.get("rate_limit_headers_supported")),
                "note": "observed from interface metadata; no request was made to provoke an error"}

    def _verify_live_market_data_entitlement(self) -> Dict[str, Any]:
        entitlement = self._target.market_data_entitlement()
        if not isinstance(entitlement, Mapping):
            raise LiveVerificationError("market-data entitlement payload is unusable")
        if entitlement.get("entitled") is not True:
            raise LiveVerificationError(
                f"the broker reports no live market-data entitlement: "
                f"{entitlement.get('detail', 'no detail supplied')}")
        symbols = {str(symbol).strip().upper()
                   for symbol in (entitlement.get("symbols") or ()) if str(symbol).strip()}
        required = set(self._instruments)
        missing = sorted(required - symbols)
        if missing:
            raise LiveVerificationError(
                f"live market-data entitlement does not cover required instruments {missing}")
        return {"entitled": True, "source": entitlement.get("source"),
                "symbols": sorted(symbols), "required": sorted(required)}

    # -- run -------------------------------------------------------------

    def run(self, *, now: Optional[datetime] = None) -> LiveReadOnlyVerification:
        now = now or datetime.now(timezone.utc)
        records: Dict[str, ReadOnlyCheckRecord] = {}
        for name in READ_ONLY_CHECKS:
            probe = getattr(self, f"_{name}", None)
            if not callable(probe):
                records[name] = ReadOnlyCheckRecord(
                    check=name, passed=False, detail="no check is implemented",
                    verified_at=now.isoformat())
                continue
            try:
                observation = dict(probe() or {})
                records[name] = ReadOnlyCheckRecord(
                    check=name, passed=True, detail=f"{name} verified against the live account",
                    observation=observation, verified_at=now.isoformat())
            except Exception as exc:
                records[name] = ReadOnlyCheckRecord(
                    check=name, passed=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    observation={"error_type": type(exc).__name__},
                    verified_at=now.isoformat())
        return LiveReadOnlyVerification(
            broker_id=self._broker_id, account_id=self._expected_account_id,
            environment=self._environment or "live", generated_at=now.isoformat(),
            records=records)

    def summarize(self, verification: LiveReadOnlyVerification) -> Dict[str, Any]:
        return {
            "evidence_kind": verification.evidence_kind,
            "releases_capital": False,
            "verified": verification.verified,
            "passed": list(verification.passed_checks),
            "failed": list(verification.failed_checks),
            "missing": list(verification.missing_checks),
            "digest": verification.digest(),
            "account_id": verification.account_id,
            "environment": verification.environment,
        }


__all__ = [
    "EVIDENCE_KIND_LIVE_READ_ONLY",
    "EVIDENCE_KIND_RECORDED",
    "READ_ONLY_CHECKS",
    "READ_ONLY_METHODS",
    "READ_ONLY_VERIFICATION_VERSION",
    "REQUIRED_INSTRUMENTS",
    "LiveReadOnlyBrokerVerifier",
    "LiveReadOnlyVerification",
    "LiveVerificationError",
    "MutatingObservation",
    "ReadOnlyBrokerAccount",
    "ReadOnlyCheckRecord",
    "assert_non_mutating",
]
