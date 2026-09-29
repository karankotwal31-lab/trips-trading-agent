"""Typed production-data verification for Trip's live-only runtime.

Broker execution authority and market-data Truth authority are intentionally separate. This module
proves the latter without contacting or trusting the broker for strategy data.

A valid verification requires:
- exactly the approved symbols SPY / QQQ / AAPL;
- 60-minute bars only;
- two independent provider families;
- both provider adapters explicitly able to attest a realtime entitlement request;
- both frozen Truth verdicts trade-eligible;
- recent bars from both sources cross-validated under the frozen Truth tolerances.

The resulting evidence releases no capital. It is an activation prerequisite only; the normal
per-order preflight still re-runs freshness, closure, provenance, session and cross-source checks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from truth_guard import cross_validate

from .contracts import canonical_json
from .market_data import APPROVED_INTERVAL, closed_bar_series, truth_verdict
from .provenance import DataPurpose, DataSourceGuard, DataSourceRecord

EVIDENCE_KIND_PRODUCTION_DATA = "PRODUCTION_DUAL_SOURCE_TRUTH_VERIFICATION"
PRODUCTION_DATA_SCHEMA_VERSION = 1
REQUIRED_SYMBOLS: Tuple[str, ...] = ("SPY", "QQQ", "AAPL")


class ProductionDataVerificationError(RuntimeError):
    """Production market-data verification is incomplete, inconsistent or unsafe."""


def _sha(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class ProductionDataSymbolRecord:
    symbol: str
    passed: bool
    detail: str
    primary: Mapping[str, Any]
    secondary: Mapping[str, Any]
    cross_source: Mapping[str, Any]
    verified_at: str
    schema_version: int = PRODUCTION_DATA_SCHEMA_VERSION
    evidence_kind: str = EVIDENCE_KIND_PRODUCTION_DATA

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        if symbol not in REQUIRED_SYMBOLS:
            raise ProductionDataVerificationError(
                f"{symbol!r} is outside the approved production-data scope")
        if self.evidence_kind != EVIDENCE_KIND_PRODUCTION_DATA:
            raise ProductionDataVerificationError("production-data evidence kind is invalid")
        try:
            observed = datetime.fromisoformat(str(self.verified_at).replace("Z", "+00:00"))
        except Exception as exc:
            raise ProductionDataVerificationError("verified_at is not ISO-8601") from exc
        if observed.tzinfo is None:
            raise ProductionDataVerificationError("verified_at must be timezone-aware")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "primary", dict(self.primary))
        object.__setattr__(self, "secondary", dict(self.secondary))
        object.__setattr__(self, "cross_source", dict(self.cross_source))

    def _body(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "passed": bool(self.passed),
            "detail": self.detail,
            "primary": dict(self.primary),
            "secondary": dict(self.secondary),
            "cross_source": dict(self.cross_source),
            "verified_at": self.verified_at,
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
        }

    def digest(self) -> str:
        return _sha(self._body())

    def to_dict(self) -> Dict[str, Any]:
        return {**self._body(), "evidence_hash": self.digest()}


@dataclass(frozen=True)
class ProductionDataVerification:
    primary_source: str
    primary_family: str
    secondary_source: str
    secondary_family: str
    generated_at: str
    records: Mapping[str, ProductionDataSymbolRecord] = field(default_factory=dict)
    schema_version: int = PRODUCTION_DATA_SCHEMA_VERSION
    evidence_kind: str = EVIDENCE_KIND_PRODUCTION_DATA

    def __post_init__(self) -> None:
        if self.evidence_kind != EVIDENCE_KIND_PRODUCTION_DATA:
            raise ProductionDataVerificationError("production-data evidence kind is invalid")
        if not all(str(value).strip() for value in (
                self.primary_source, self.primary_family,
                self.secondary_source, self.secondary_family)):
            raise ProductionDataVerificationError("both provider identities are required")
        if self.primary_family == self.secondary_family:
            raise ProductionDataVerificationError(
                "primary and secondary market data must come from independent provider families")
        try:
            observed = datetime.fromisoformat(str(self.generated_at).replace("Z", "+00:00"))
        except Exception as exc:
            raise ProductionDataVerificationError("generated_at is not ISO-8601") from exc
        if observed.tzinfo is None:
            raise ProductionDataVerificationError("generated_at must be timezone-aware")
        object.__setattr__(self, "records", dict(self.records))

    @property
    def missing_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol in REQUIRED_SYMBOLS if symbol not in self.records)

    @property
    def failed_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol in REQUIRED_SYMBOLS
                     if symbol in self.records and not self.records[symbol].passed)

    @property
    def verified(self) -> bool:
        return not self.missing_symbols and not self.failed_symbols

    def _body(self) -> Dict[str, Any]:
        return {
            "primary_source": self.primary_source,
            "primary_family": self.primary_family,
            "secondary_source": self.secondary_source,
            "secondary_family": self.secondary_family,
            "generated_at": self.generated_at,
            "records": {name: record.to_dict()
                        for name, record in sorted(self.records.items())},
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "verified": self.verified,
            "missing_symbols": list(self.missing_symbols),
            "failed_symbols": list(self.failed_symbols),
            "releases_capital": False,
        }

    def digest(self) -> str:
        return _sha(self._body())

    def to_dict(self) -> Dict[str, Any]:
        return {**self._body(), "evidence_hash": self.digest()}


def _provider_identity(provider: Any) -> Tuple[str, str]:
    identity = getattr(provider, "identity", None)
    if identity is None:
        raise ProductionDataVerificationError("provider has no identity")
    source = str(getattr(identity, "name", "") or "").strip()
    family = str(getattr(identity, "source_family", "") or "").strip()
    if not source or not family:
        raise ProductionDataVerificationError("provider identity is incomplete")
    if getattr(provider, "source_kind", None) != "real":
        raise ProductionDataVerificationError(
            f"provider {source!r} is not configured as source_kind='real'")
    if not bool(getattr(identity, "can_request_realtime_entitlement", False)):
        raise ProductionDataVerificationError(
            f"provider {source!r} cannot machine-attest a realtime entitlement request")
    return source, family


def _one_source(provider: Any, symbol: str, *, count: int,
                max_age_minutes: int, min_bars: int, close_lag_seconds: float):
    source, family = _provider_identity(provider)
    bars, closure = closed_bar_series(
        provider, symbol, interval=APPROVED_INTERVAL,
        close_lag_seconds=close_lag_seconds, count=count)
    verdict = truth_verdict(
        provider, symbol, bars,
        max_age_minutes=max_age_minutes,
        min_bars=min_bars,
        allow_synthetic_analysis=False)
    guard = DataSourceGuard(approved_sources=(source,), approved_families=(family,))
    guard.admit(
        DataSourceRecord(source=source, source_family=family,
                         origin="market_data_provider",
                         approved_for_truth=bool(verdict.trusted_for_trade)),
        purpose=DataPurpose.TRADE_ELIGIBILITY)
    if not verdict.trusted_for_trade:
        raise ProductionDataVerificationError(
            f"{source} failed frozen Truth for {symbol}: "
            + "; ".join(verdict.reasons[:4]))
    observation = {
        "source": source,
        "source_family": family,
        "source_kind": "real",
        "origin": "market_data_provider",
        "interval": APPROVED_INTERVAL,
        "bar_count": len(bars),
        "latest_bar_ts": verdict.latest_bar_ts,
        "age_minutes": verdict.age_minutes,
        "truth_integrity_hash": verdict.integrity_hash,
        "realtime_request_attested": True,
        "closure": [item for item in closure if item],
    }
    return tuple(bars), verdict, observation


def verify_dual_source_production_data(
        primary: Any, secondary: Any, *,
        now: Optional[datetime] = None,
        symbols: Sequence[str] = REQUIRED_SYMBOLS,
        count: int = 240,
        max_age_minutes: int = 120,
        min_bars: int = 60,
        close_lag_seconds: float = 20.0,
        max_ohlc_deviation_pct: float = 0.005,
        max_timestamp_skew_minutes: float = 5.0,
        min_cross_source_bars: int = 3) -> ProductionDataVerification:
    """Verify two independent realtime providers for the fixed Trip's symbol mandate.

    The function performs no brokerage operation. Network access, when present, is market-data
    GET traffic only through the provider adapters supplied by the caller.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized = tuple(str(symbol).strip().upper() for symbol in symbols)
    if normalized != REQUIRED_SYMBOLS:
        raise ProductionDataVerificationError(
            f"production-data scope must be exactly {list(REQUIRED_SYMBOLS)}")

    primary_source, primary_family = _provider_identity(primary)
    secondary_source, secondary_family = _provider_identity(secondary)
    if primary_family == secondary_family:
        raise ProductionDataVerificationError(
            "cross-source verification requires independent provider families")

    records: Dict[str, ProductionDataSymbolRecord] = {}
    for symbol in REQUIRED_SYMBOLS:
        try:
            p_bars, p_truth, p_obs = _one_source(
                primary, symbol, count=count, max_age_minutes=max_age_minutes,
                min_bars=min_bars, close_lag_seconds=close_lag_seconds)
            s_bars, s_truth, s_obs = _one_source(
                secondary, symbol, count=count, max_age_minutes=max_age_minutes,
                min_bars=min_bars, close_lag_seconds=close_lag_seconds)
            cross = cross_validate(
                p_truth, p_bars, s_truth, s_bars,
                max_ohlc_deviation_pct=max_ohlc_deviation_pct,
                max_timestamp_skew_minutes=max_timestamp_skew_minutes,
                min_cross_source_bars=min_cross_source_bars)
            if not cross.get("passed"):
                raise ProductionDataVerificationError(
                    f"independent sources did not agree for {symbol}: "
                    f"{cross.get('reason', 'cross-source verification failed')}")
            records[symbol] = ProductionDataSymbolRecord(
                symbol=symbol, passed=True,
                detail="both independent realtime sources passed frozen Truth and cross-validation",
                primary=p_obs, secondary=s_obs, cross_source=cross,
                verified_at=now.isoformat())
        except Exception as exc:
            records[symbol] = ProductionDataSymbolRecord(
                symbol=symbol, passed=False,
                detail=f"{type(exc).__name__}: {exc}",
                primary={"source": primary_source, "source_family": primary_family},
                secondary={"source": secondary_source, "source_family": secondary_family},
                cross_source={"passed": False},
                verified_at=now.isoformat())

    return ProductionDataVerification(
        primary_source=primary_source, primary_family=primary_family,
        secondary_source=secondary_source, secondary_family=secondary_family,
        generated_at=now.isoformat(), records=records)


__all__ = [
    "EVIDENCE_KIND_PRODUCTION_DATA",
    "PRODUCTION_DATA_SCHEMA_VERSION",
    "REQUIRED_SYMBOLS",
    "ProductionDataSymbolRecord",
    "ProductionDataVerification",
    "ProductionDataVerificationError",
    "verify_dual_source_production_data",
]
