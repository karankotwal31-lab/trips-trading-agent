"""Production market data: provider, Truth integration, provenance, closed bars, session, health.

Market data was previously assembled ad hoc inside ``preflight``. That made it impossible to say
what "production market data" meant, or to demonstrate that any of it exists. This module makes the
whole data path a real, inspectable object with one owner per decision:

* :class:`ProductionMarketDataProvider` is the production provider. It is a thin, honest wrapper
  over the FROZEN ``providers.get_provider``; it adds no second parsing path and cannot relabel a
  source kind. Constructing it requires a configured provider name and, for any non-demo provider,
  the provider's own credential - which lives outside this repository and is therefore an owner
  act, not engineering work.
* :class:`MarketDataHealth` is the FAIL-CLOSED data-health logic. Unknown is not healthy. A stale
  feed, a gap, a missing provenance tag, a closed-bar violation or a session mismatch each produce
  a refusal, never a warning and never a substituted value.
* Truth integration calls the FROZEN ``truth_guard.validate_bars``. It does not reimplement it.
* Closed-bar enforcement calls the FROZEN ``market_time.closed_bars_only``, and the interval is the
  approved ``60min`` - so no code path can quietly trade on a partially formed hour.
* Session integration uses the computed exchange calendar and asks ``SessionCalendar``; a calendar
  that cannot prove the session answers UNKNOWN, and UNKNOWN blocks new exposure.
* Provenance passes every source through :class:`~execution.provenance.DataSourceGuard`, so a
  broker execution feed can never be promoted into a Truth-eligible Strategy source.

What is NOT here, deliberately: no invented provider key, no DEMO-to-real relabelling, and no
claim that a feed is production because a module imported successfully.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from market_time import closed_bars_only
from providers import Bar, get_provider
from truth_guard import DataVerdict, cross_validate, validate_bars

from .contracts import ExecutionLayerError, canonical_json
from .exchange_calendar import default_us_equity_calendar
from .provenance import DataPurpose, DataSourceGuard, DataSourceRecord, ProvenanceViolation
from .session import SessionCalendar, SessionStatus

#: The approved decision interval. Not configurable: this module cannot be pointed at a different
#: bar size by a caller.
APPROVED_INTERVAL = "60min"

#: Provider names this repository knows how to reach, and the source kind each one can honestly
#: declare. ``demo`` is synthetic and can never be trade-eligible; that is the frozen Truth Engine's
#: call, not this module's.
PROVIDER_SOURCE_KINDS: Dict[str, str] = {
    "demo": "demo",
    "alpha_vantage": "real",
    "twelve_data": "real",
}

#: Env var each production provider needs. The VALUES are never in this repository.
PROVIDER_CREDENTIAL_ENV: Dict[str, str] = {
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "twelve_data": "TWELVE_DATA_API_KEY",
}


class MarketDataError(ExecutionLayerError):
    """The market-data path could not be proven usable. Never a fallback to stale or demo data."""


class ProviderCredentialMissing(ExecutionLayerError):
    """A production provider needs a credential this repository deliberately does not hold."""


PRODUCTION_DATA_EVIDENCE_KIND = "PRODUCTION_MARKET_DATA_VERIFICATION"
PRODUCTION_DATA_EVIDENCE_VERSION = 1
PRODUCTION_DATA_MAX_AGE_SECONDS = 15 * 60
REQUIRED_PRODUCTION_SYMBOLS: Tuple[str, ...] = ("SPY", "QQQ", "AAPL")


@dataclass(frozen=True)
class ProductionDataRecord:
    """One symbol's read-only dual-source verification result. Never releases capital."""

    symbol: str
    passed: bool
    detail: str
    primary_integrity_hash: str = ""
    secondary_integrity_hash: str = ""
    cross_source: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = str(self.symbol).strip().upper()
        if normalized not in REQUIRED_PRODUCTION_SYMBOLS:
            raise MarketDataError(f"{normalized!r} is outside the approved data scope")
        object.__setattr__(self, "symbol", normalized)
        object.__setattr__(self, "cross_source", dict(self.cross_source))


@dataclass(frozen=True)
class ProductionMarketDataVerification:
    """Evidence that two independent real-time provider families serve the approved 60m scope."""

    generated_at: str
    primary_provider: str
    primary_family: str
    secondary_provider: str
    secondary_family: str
    interval: str
    records: Mapping[str, ProductionDataRecord] = field(default_factory=dict)
    evidence_kind: str = PRODUCTION_DATA_EVIDENCE_KIND
    schema_version: int = PRODUCTION_DATA_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if self.evidence_kind != PRODUCTION_DATA_EVIDENCE_KIND:
            raise MarketDataError("production market-data evidence kind is invalid")
        if self.interval != APPROVED_INTERVAL:
            raise MarketDataError(f"production data interval must be {APPROVED_INTERVAL}")
        if not self.primary_family or not self.secondary_family:
            raise MarketDataError("both provider families are required")
        if self.primary_family == self.secondary_family:
            raise MarketDataError("production providers must be independent families")
        try:
            parsed = datetime.fromisoformat(str(self.generated_at).replace("Z", "+00:00"))
        except Exception as exc:
            raise MarketDataError("production data generated_at is not ISO-8601") from exc
        if parsed.tzinfo is None:
            raise MarketDataError("production data generated_at must be timezone-aware")
        object.__setattr__(self, "records", dict(self.records))

    @property
    def missing_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol in REQUIRED_PRODUCTION_SYMBOLS if symbol not in self.records)

    @property
    def failed_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol in REQUIRED_PRODUCTION_SYMBOLS
                     if symbol in self.records and not self.records[symbol].passed)

    @property
    def verified(self) -> bool:
        return not self.missing_symbols and not self.failed_symbols

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_kind": self.evidence_kind,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "primary_provider": self.primary_provider,
            "primary_family": self.primary_family,
            "secondary_provider": self.secondary_provider,
            "secondary_family": self.secondary_family,
            "interval": self.interval,
            "verified": self.verified,
            "missing_symbols": list(self.missing_symbols),
            "failed_symbols": list(self.failed_symbols),
            "records": {
                symbol: {
                    "symbol": record.symbol,
                    "passed": record.passed,
                    "detail": record.detail,
                    "primary_integrity_hash": record.primary_integrity_hash,
                    "secondary_integrity_hash": record.secondary_integrity_hash,
                    "cross_source": dict(record.cross_source),
                }
                for symbol, record in sorted(self.records.items())
            },
            "releases_capital": False,
        }


def provider_credential_status(provider_name: str, *, environ: Optional[Mapping[str, str]] = None
                               ) -> Dict[str, Any]:
    """Whether a provider can actually be reached right now. Reports the NAME of what is missing."""
    import os

    env = os.environ if environ is None else environ
    if provider_name == "demo":
        return {"provider": "demo", "source_kind": "demo", "credential_env": "",
                "credential_present": True, "trade_eligible": False,
                "note": "synthetic; the frozen Truth Engine never treats demo data as trade-eligible"}
    if provider_name not in PROVIDER_SOURCE_KINDS:
        return {"provider": provider_name, "source_kind": None, "credential_env": "",
                "credential_present": False, "trade_eligible": False,
                "note": "unknown provider"}
    variable = PROVIDER_CREDENTIAL_ENV[provider_name]
    present = bool(str(env.get(variable, "")).strip())
    return {"provider": provider_name, "source_kind": PROVIDER_SOURCE_KINDS[provider_name],
            "credential_env": variable, "credential_present": present,
            "trade_eligible": bool(present),
            "note": ("a credential is an owner/external act; its absence fails closed and is never "
                     "worked around with demo or cached data")}


@dataclass(frozen=True)
class MarketDataHealth:
    """Fail-closed verdict on a bar series. Every field is a refusal reason when false."""

    healthy: bool
    reasons: Tuple[str, ...]
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"healthy": self.healthy, "reasons": list(self.reasons), "detail": dict(self.detail)}


class ProductionMarketDataProvider:
    """The production provider, with its credential boundary made explicit.

    The wrapper exists so that "is there a production provider?" has an answer that is not "can I
    import providers.py". It refuses to construct without a credential, so the absence of a
    credential is a construction-time fact rather than a runtime surprise on the first order.
    """

    def __init__(self, *, provider_name: str, source_kind: Optional[str] = None,
                 interval: str = APPROVED_INTERVAL, count: int = 240,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        if interval != APPROVED_INTERVAL:
            raise MarketDataError(
                f"the approved decision interval is {APPROVED_INTERVAL}; refusing {interval!r}")
        status = provider_credential_status(provider_name, environ=environ)
        if not status["credential_present"]:
            raise ProviderCredentialMissing(
                f"market-data provider {provider_name!r} needs "
                f"{status['credential_env'] or 'a known provider configuration'}; it is an "
                f"owner/external credential and is not present")
        kind = source_kind or status["source_kind"]
        if kind != status["source_kind"]:
            raise MarketDataError(
                f"provider {provider_name!r} is {status['source_kind']}; refusing to declare "
                f"{kind!r}")
        self.provider_name = provider_name
        self.source_kind = kind
        self.interval = interval
        self.count = int(count)
        self._status = status
        self._provider = get_provider(provider_name, interval, kind)

    @property
    def identity(self) -> Any:
        return self._provider.identity

    @property
    def credential_status(self) -> Dict[str, Any]:
        return dict(self._status)

    def bars(self, symbol: str, count: Optional[int] = None) -> List[Bar]:
        try:
            return list(self._provider.bars(symbol, int(count or self.count)))
        except Exception as exc:
            raise MarketDataError(
                f"market-data fetch failed for {symbol}: {type(exc).__name__}") from None

    def source_record(self) -> DataSourceRecord:
        return DataSourceRecord(source=self.identity.name, source_family=self.identity.source_family,
                                origin="market_data_provider", approved_for_truth=True)

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.provider_name, "source_kind": self.source_kind,
                "interval": self.interval, "requested_bars": self.count,
                "credential": self._status,
                "note": "no credential value is ever returned, logged or stored here"}


def closed_bar_series(provider: Any, symbol: str, *, interval: str = APPROVED_INTERVAL,
                      close_lag_seconds: float = 20.0,
                      count: int = 240) -> Tuple[Sequence[Bar], Tuple[str, ...]]:
    """Fetch and return ONLY complete bars, using the frozen closure rule.

    A provider that hands back a partially formed hour is the normal case near the bar boundary.
    Trimming to closed bars is not a convenience: the frozen Truth Engine's cadence check and the
    decision-bar closure at transmission both assume closed input.
    """
    raw = provider.bars(symbol, count)
    closed = closed_bars_only(raw, interval, close_lag_seconds=close_lag_seconds)
    dropped = len(list(raw)) - len(list(closed))
    return tuple(closed), (f"dropped {dropped} incomplete bar(s)" if dropped else "",)


def truth_verdict(provider: Any, symbol: str, bars: Sequence[Bar], *,
                  max_age_minutes: int, min_bars: int,
                  allow_synthetic_analysis: bool = True) -> DataVerdict:
    """Run the FROZEN Truth Engine over the series. This module does not reimplement validation."""
    identity = getattr(provider, "identity", None)
    if identity is None:
        raise MarketDataError("provider has no identity; provenance cannot be established")
    return validate_bars(
        source=identity.name, source_family=identity.source_family,
        source_kind=getattr(provider, "source_kind", "demo"),
        symbol=symbol, interval=APPROVED_INTERVAL, bars=bars,
        max_age_minutes=max_age_minutes, min_bars=min_bars,
        allow_synthetic_analysis=allow_synthetic_analysis,
        fixed_source_kind=identity.fixed_source_kind,
        realtime_request_attested=bool(
            getattr(identity, "can_request_realtime_entitlement", False)
            and getattr(provider, "source_kind", "") == "real"))


def evaluate_market_data(provider: Any, symbol: str, *, now: Optional[datetime] = None,
                         max_age_minutes: int = 120, min_bars: int = 60,
                         close_lag_seconds: float = 20.0, count: int = 240,
                         calendar: Optional[SessionCalendar] = None,
                         data_guard: Optional[DataSourceGuard] = None,
                         allow_synthetic_analysis: bool = True
                         ) -> Dict[str, Any]:
    """Fetch -> close -> Truth -> provenance -> session, returning one fail-closed verdict.

    The order matters. A bar that is not closed must never reach Truth, and a session that cannot
    be proven must never be reported as open because the bars looked fine.
    """
    now = now or datetime.now(timezone.utc)
    reasons: List[str] = []
    session_calendar = calendar if calendar is not None else default_us_equity_calendar()
    session = session_calendar.evaluate(now)
    detail: Dict[str, Any] = {"interval": APPROVED_INTERVAL, "symbol": symbol,
                              "session": session.to_dict()}

    try:
        bars, closure_notes = closed_bar_series(provider, symbol, close_lag_seconds=close_lag_seconds,
                                                count=count)
    except Exception as exc:
        # No bars AND no provable session: fail closed on both counts rather than skipping either.
        return MarketDataHealth(False, (f"market data could not be fetched: {type(exc).__name__}",
                                        f"session truth is {session.status.value}: "
                                        f"{'; '.join(session.reasons)}"), detail).to_dict()
    detail["bar_count"] = len(bars)
    detail["closure"] = [note for note in closure_notes if note]
    if not bars:
        reasons.append("no closed bars were available")

    verdict: Optional[DataVerdict] = None
    if bars:
        try:
            verdict = truth_verdict(provider, symbol, bars, max_age_minutes=max_age_minutes,
                                    min_bars=min_bars,
                                    allow_synthetic_analysis=allow_synthetic_analysis)
        except Exception as exc:
            reasons.append(f"truth validation failed: {type(exc).__name__}")
    if verdict is not None:
        detail["truth"] = {"trusted_for_analysis": verdict.trusted_for_analysis,
                           "trusted_for_trade": verdict.trusted_for_trade,
                           "integrity_hash": verdict.integrity_hash,
                           "age_minutes": verdict.age_minutes,
                           "latest_bar_ts": verdict.latest_bar_ts,
                           "source_kind": verdict.source_kind}
        if not verdict.trusted_for_analysis:
            reasons.append("Truth refuses this data for analysis: "
                           + "; ".join(verdict.reasons[:3]))
        if getattr(provider, "source_kind", "") != "real":
            reasons.append(f"source kind {getattr(provider, 'source_kind', '?')!r} is never "
                           f"trade-eligible")
        if data_guard is not None:
            identity = provider.identity
            try:
                data_guard.admit(DataSourceRecord(
                    source=identity.name, source_family=identity.source_family,
                    origin="market_data_provider",
                    approved_for_truth=bool(verdict.trusted_for_analysis)),
                    purpose=DataPurpose.TRADE_ELIGIBILITY)
            except ProvenanceViolation as exc:
                reasons.append(f"provenance refused: {exc}")
        else:
            reasons.append("no provenance guard was applied")

    session_calendar = calendar if calendar is not None else default_us_equity_calendar()
    session = session_calendar.evaluate(now)
    detail["session"] = session.to_dict()
    if session.status is SessionStatus.UNKNOWN:
        reasons.append(f"session truth is UNKNOWN: {'; '.join(session.reasons)}")
    elif session.status is SessionStatus.CLOSED:
        reasons.append(f"the market session is closed: {'; '.join(session.reasons)}")

    health = MarketDataHealth(not reasons, tuple(reasons), detail)
    result = health.to_dict()
    result["bars"] = list(bars)
    result["truth"] = verdict.to_dict() if verdict is not None else None
    result["trade_eligible"] = bool(verdict is not None and verdict.trusted_for_trade
                                    and not reasons)
    return result


def verify_production_market_data(
        primary: Any, secondary: Any, *, now: Optional[datetime] = None,
        count: int = 240, min_bars: int = 60,
        capability_max_age_minutes: int = 7 * 24 * 60,
        data_guard: Optional[DataSourceGuard] = None) -> ProductionMarketDataVerification:
    """Verify two independent provider families against the existing frozen Truth contract.

    This is activation/readiness evidence, not an order-time freshness verdict. The evidence object
    itself expires quickly in Lifecycle; the latest returned closed bar may be older across a
    weekend/holiday. Every actual order still runs the stricter frozen per-trade freshness/session
    checks in preflight.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    p_identity = getattr(primary, "identity", None)
    s_identity = getattr(secondary, "identity", None)
    if p_identity is None or s_identity is None:
        raise MarketDataError("both production providers must expose identity")
    p_name = str(getattr(p_identity, "name", "") or "").strip()
    s_name = str(getattr(s_identity, "name", "") or "").strip()
    p_family = str(getattr(p_identity, "source_family", "") or "").strip()
    s_family = str(getattr(s_identity, "source_family", "") or "").strip()
    if not p_name or not s_name or not p_family or not s_family:
        raise MarketDataError("both production providers must expose non-empty identity")
    if p_family == s_family:
        raise MarketDataError("production data providers must be independent source families")
    if getattr(primary, "source_kind", None) != "real":
        raise MarketDataError(f"primary provider {p_name!r} is not source_kind='real'")
    if getattr(secondary, "source_kind", None) != "real":
        raise MarketDataError(f"secondary provider {s_name!r} is not source_kind='real'")
    if getattr(primary, "interval", APPROVED_INTERVAL) != APPROVED_INTERVAL:
        raise MarketDataError(f"primary provider interval must be {APPROVED_INTERVAL}")
    if getattr(secondary, "interval", APPROVED_INTERVAL) != APPROVED_INTERVAL:
        raise MarketDataError(f"secondary provider interval must be {APPROVED_INTERVAL}")

    guard = data_guard or DataSourceGuard(
        approved_sources=(p_name, s_name), approved_families=(p_family, s_family))
    records: Dict[str, ProductionDataRecord] = {}

    for symbol in REQUIRED_PRODUCTION_SYMBOLS:
        try:
            p_bars, _ = closed_bar_series(primary, symbol, count=count)
            s_bars, _ = closed_bar_series(secondary, symbol, count=count)
            p_verdict = truth_verdict(
                primary, symbol, p_bars, max_age_minutes=capability_max_age_minutes,
                min_bars=min_bars, allow_synthetic_analysis=False)
            s_verdict = truth_verdict(
                secondary, symbol, s_bars, max_age_minutes=capability_max_age_minutes,
                min_bars=min_bars, allow_synthetic_analysis=False)

            guard.admit(primary.source_record(), purpose=DataPurpose.TRADE_ELIGIBILITY)
            guard.admit(secondary.source_record(), purpose=DataPurpose.TRADE_ELIGIBILITY)
            cross = cross_validate(
                p_verdict, p_bars, s_verdict, s_bars,
                max_ohlc_deviation_pct=0.005,
                max_timestamp_skew_minutes=5.0,
                min_cross_source_bars=3)
            passed = bool(p_verdict.trusted_for_trade and s_verdict.trusted_for_trade
                          and cross.get("passed"))
            reasons = []
            if not p_verdict.trusted_for_trade:
                reasons.append(f"{p_name}: " + "; ".join(p_verdict.reasons[:3]))
            if not s_verdict.trusted_for_trade:
                reasons.append(f"{s_name}: " + "; ".join(s_verdict.reasons[:3]))
            if not cross.get("passed"):
                reasons.append(f"cross_source: {cross.get('reason', 'failed')}")
            records[symbol] = ProductionDataRecord(
                symbol=symbol, passed=passed,
                detail="verified" if passed else " | ".join(reasons),
                primary_integrity_hash=p_verdict.integrity_hash,
                secondary_integrity_hash=s_verdict.integrity_hash,
                cross_source=cross)
        except Exception as exc:
            records[symbol] = ProductionDataRecord(
                symbol=symbol, passed=False,
                detail=f"{type(exc).__name__}: {exc}")

    return ProductionMarketDataVerification(
        generated_at=now.isoformat(),
        primary_provider=p_name, primary_family=p_family,
        secondary_provider=s_name, secondary_family=s_family,
        interval=APPROVED_INTERVAL, records=records)


def closed_60min_bars_evidence(bars: Sequence[Bar], *, now: Optional[datetime] = None,
                               close_lag_seconds: float = 20.0) -> Dict[str, Any]:
    """Deterministic proof that a series is 60-minute bars and that every one of them is closed.

    Used as an engineering evidence input, and useful on its own: it reports the interval actually
    observed and refuses anything that is not a strictly increasing, closed 60-minute series.
    """
    now = now or datetime.now(timezone.utc)
    reasons: List[str] = []
    stamps: List[datetime] = []
    for bar in bars:
        try:
            parsed = datetime.fromisoformat(str(bar.ts).replace("Z", "+00:00"))
        except Exception:
            reasons.append("a bar timestamp is not ISO-8601")
            break
        if parsed.tzinfo is None:
            reasons.append("a bar timestamp is not timezone-aware")
            break
        stamps.append(parsed.astimezone(timezone.utc))
    if reasons:
        return {"passed": False, "reasons": reasons, "bar_count": len(bars)}

    expected = timedelta(hours=1)
    gaps = [(stamps[i + 1] - stamps[i]) for i in range(len(stamps) - 1)]
    if any(gap != expected for gap in gaps):
        reasons.append("bar cadence is not a strict 60-minute grid")
    if any(stamp + expected + timedelta(seconds=close_lag_seconds) > now for stamp in stamps):
        reasons.append("the series contains a bar that has not provably closed")
    return {"passed": not reasons, "reasons": reasons, "bar_count": len(bars),
            "observed_interval_minutes": (gaps[0].total_seconds() / 60.0) if gaps else None,
            "latest_bar_ts": stamps[-1].isoformat() if stamps else None}


__all__ = [
    "APPROVED_INTERVAL",
    "PROVIDER_CREDENTIAL_ENV",
    "PROVIDER_SOURCE_KINDS",
    "PRODUCTION_DATA_EVIDENCE_KIND",
    "PRODUCTION_DATA_EVIDENCE_VERSION",
    "PRODUCTION_DATA_MAX_AGE_SECONDS",
    "REQUIRED_PRODUCTION_SYMBOLS",
    "MarketDataHealth",
    "ProductionDataRecord",
    "ProductionMarketDataProvider",
    "ProductionMarketDataVerification",
    "ProviderCredentialMissing",
    "closed_60min_bars_evidence",
    "closed_bar_series",
    "evaluate_market_data",
    "provider_credential_status",
    "truth_verdict",
    "verify_production_market_data",
]
