from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import median
from typing import List, Optional, Sequence

from providers import Bar
from market_time import interval_to_timedelta


@dataclass
class DataVerdict:
    trusted_for_analysis: bool
    trusted_for_trade: bool
    source: str
    source_family: str
    source_kind: str
    symbol: str
    interval: str
    received_at: str
    latest_bar_ts: Optional[str]
    age_minutes: Optional[float]
    bar_count: int
    integrity_hash: str
    checks: List[dict]
    reasons: List[str]
    cross_source: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _hash_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "__NONFINITE__:nan"
        return "__NONFINITE__:+inf" if value > 0 else "__NONFINITE__:-inf"
    if isinstance(value, dict):
        return {k: _hash_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hash_safe(v) for v in value]
    return value


def _hash_bars(*, source: str, source_family: str, source_kind: str, symbol: str,
               interval: str, bars: Sequence[Bar]) -> str:
    payload = {
        "source": source,
        "source_family": source_family,
        "source_kind": source_kind,
        "symbol": symbol,
        "interval": interval,
        "bars": [b.to_dict() for b in bars],
    }
    raw = json.dumps(_hash_safe(payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_bars(*, source: str, source_family: str, source_kind: str, symbol: str, interval: str,
                  bars: Sequence[Bar], max_age_minutes: int, min_bars: int,
                  allow_synthetic_analysis: bool = True, fixed_source_kind: Optional[str] = None,
                  realtime_request_attested: bool = False) -> DataVerdict:
    """Validate provenance and OHLCV invariants. Any uncertainty fails closed for trading."""
    now = datetime.now(timezone.utc)
    received_at = now.isoformat()
    checks: List[dict] = []
    reasons: List[str] = []

    known_kind = source_kind in {"real", "delayed", "demo", "replay"}
    checks.append(_check("declared_source_kind", known_kind, f"source_kind={source_kind}"))
    checks.append(_check("provider_kind_binding", fixed_source_kind is None or source_kind == fixed_source_kind,
                         f"fixed={fixed_source_kind!r}, declared={source_kind!r}"))
    realtime_attestation_ok = source_kind != "real" or realtime_request_attested
    checks.append(_check("realtime_entitlement_request", realtime_attestation_ok,
                         "provider adapter explicitly requested a realtime entitlement" if realtime_attestation_ok else "realtime entitlement is not machine-attested by this adapter"))
    checks.append(_check("nonempty_source", bool(source and source.strip()), f"source={source!r}"))
    checks.append(_check("nonempty_source_family", bool(source_family and source_family.strip()), f"source_family={source_family!r}"))
    checks.append(_check("symbol_identity", bool(symbol and symbol.strip()), f"symbol={symbol!r}"))
    checks.append(_check("interval_identity", bool(interval and interval.strip()), f"interval={interval!r}"))
    checks.append(_check("history_count", len(bars) >= min_bars, f"bars={len(bars)}, required={min_bars}"))

    timestamps_ok = True
    monotonic = True
    duplicates = False
    age_minutes: Optional[float] = None
    latest_ts: Optional[str] = None
    parsed_times: List[datetime] = []
    for b in bars:
        try:
            dt = datetime.fromisoformat(b.ts)
            if dt.tzinfo is None:
                timestamps_ok = False
                break
            parsed_times.append(dt.astimezone(timezone.utc))
        except Exception:
            timestamps_ok = False
            break
    if parsed_times:
        monotonic = all(parsed_times[i] < parsed_times[i + 1] for i in range(len(parsed_times) - 1))
        duplicates = len(set(parsed_times)) != len(parsed_times)
        latest_ts = parsed_times[-1].isoformat()
        age_minutes = (now - parsed_times[-1]).total_seconds() / 60.0

    checks.append(_check("timezone_aware_timestamps", timestamps_ok, "All bar timestamps must include a timezone"))
    checks.append(_check("strict_time_order", monotonic and not duplicates, "Bars must be strictly increasing with no duplicates"))

    cadence_ok = False
    cadence_detail = "insufficient timestamps"
    if len(parsed_times) >= 3:
        expected_seconds = interval_to_timedelta(interval).total_seconds()
        gaps = [(parsed_times[i + 1] - parsed_times[i]).total_seconds() for i in range(len(parsed_times) - 1)]
        regular = [g for g in gaps if 0 < g <= expected_seconds * 4]
        if regular:
            med_gap = median(regular)
            cadence_ok = expected_seconds * 0.8 <= med_gap <= expected_seconds * 1.2
            cadence_detail = f"median_regular_gap_seconds={med_gap}, expected={expected_seconds}"
        else:
            cadence_detail = f"no plausible intraday gaps near expected={expected_seconds}"
    checks.append(_check("interval_cadence", cadence_ok, cadence_detail))

    ohlc_ok = True
    finite_positive = True
    nonnegative_volume = True
    extreme_return = False
    prev_close: Optional[float] = None
    for b in bars:
        vals = (b.open, b.high, b.low, b.close)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in vals):
            finite_positive = False
        if not (b.high >= max(b.open, b.close) and b.low <= min(b.open, b.close) and b.high >= b.low):
            ohlc_ok = False
        if not isinstance(b.volume, (int, float)) or not math.isfinite(b.volume) or b.volume < 0:
            nonnegative_volume = False
        if prev_close is not None and prev_close > 0 and math.isfinite(prev_close) and math.isfinite(b.close):
            if abs(b.close / prev_close - 1.0) >= 0.20:
                extreme_return = True
        prev_close = b.close

    checks.append(_check("finite_positive_prices", finite_positive, "OHLC values must be finite and positive"))
    checks.append(_check("ohlc_invariants", ohlc_ok, "high>=open/close, low<=open/close, high>=low"))
    checks.append(_check("nonnegative_volume", nonnegative_volume, "Volume must be finite and non-negative"))
    checks.append(_check("extreme_move_review", not extreme_return,
                         "Single-bar close move >=20% requires corporate-action/news/data review" if extreme_return else "No >=20% one-bar close jump detected"))

    freshness_pass = True
    if source_kind == "real":
        freshness_pass = age_minutes is not None and -5 <= age_minutes <= max_age_minutes
    elif source_kind == "delayed":
        # Delayed is not timeless: stale delayed data must not be treated as current analysis.
        freshness_pass = age_minutes is not None and -5 <= age_minutes <= max_age_minutes
    elif source_kind in {"demo", "replay"}:
        freshness_pass = True
    checks.append(_check("freshness", freshness_pass, f"age_minutes={age_minutes}, max_real_age={max_age_minutes}"))

    hard_integrity = all(c["passed"] for c in checks if c["name"] not in {"freshness", "realtime_entitlement_request"})
    analysis_allowed = hard_integrity and freshness_pass and (
        source_kind in {"real", "delayed"} or (allow_synthetic_analysis and source_kind in {"demo", "replay"})
    )
    # This is local eligibility only. If cross-source verification is configured, it is applied afterward.
    trade_allowed = hard_integrity and freshness_pass and source_kind == "real" and realtime_attestation_ok

    for c in checks:
        if not c["passed"]:
            reasons.append(f"{c['name']}: {c['detail']}")
    if source_kind != "real":
        reasons.append(f"source_kind={source_kind} is not eligible for current-market trade decisions")

    return DataVerdict(
        trusted_for_analysis=analysis_allowed,
        trusted_for_trade=trade_allowed,
        source=source,
        source_family=source_family,
        source_kind=source_kind,
        symbol=symbol,
        interval=interval,
        received_at=received_at,
        latest_bar_ts=latest_ts,
        age_minutes=age_minutes,
        bar_count=len(bars),
        integrity_hash=_hash_bars(source=source, source_family=source_family, source_kind=source_kind,
                                  symbol=symbol, interval=interval, bars=bars),
        checks=checks,
        reasons=reasons,
    )


def cross_validate(primary: DataVerdict, primary_bars: Sequence[Bar], secondary: DataVerdict,
                   secondary_bars: Sequence[Bar], max_ohlc_deviation_pct: float = 0.005,
                   max_timestamp_skew_minutes: float = 5.0, min_cross_source_bars: int = 3) -> dict:
    """Cross-check multiple recent bars from independent provider families.

    Agreement reduces accidental-feed risk; it does not prove either source is objectively correct.
    """
    base = {
        "primary_source": primary.source,
        "secondary_source": secondary.source,
        "primary_family": primary.source_family,
        "secondary_family": secondary.source_family,
        "symbol": primary.symbol,
        "interval": primary.interval,
        "max_ohlc_deviation_pct": max_ohlc_deviation_pct,
        "max_timestamp_skew_minutes": max_timestamp_skew_minutes,
        "min_cross_source_bars": min_cross_source_bars,
    }
    if not primary_bars or not secondary_bars:
        return {**base, "passed": False, "reason": "missing_source_data"}
    if primary.source_family == secondary.source_family:
        return {**base, "passed": False, "reason": "sources_not_independent"}
    if primary.symbol != secondary.symbol or primary.interval != secondary.interval:
        return {**base, "passed": False, "reason": "instrument_or_interval_mismatch"}
    if not primary.trusted_for_trade or not secondary.trusted_for_trade:
        return {**base, "passed": False, "reason": "source_not_trade_eligible"}

    n = min(len(primary_bars), len(secondary_bars), max(min_cross_source_bars, 1))
    a_recent = list(primary_bars[-n:])
    b_recent = list(secondary_bars[-n:])
    comparisons = []
    passed = n >= min_cross_source_bars
    for a, b in zip(a_recent, b_recent):
        try:
            ta = datetime.fromisoformat(a.ts).astimezone(timezone.utc)
            tb = datetime.fromisoformat(b.ts).astimezone(timezone.utc)
        except Exception:
            return {**base, "passed": False, "reason": "unparseable_cross_source_timestamp"}
        skew = abs((ta - tb).total_seconds()) / 60.0
        deviations = {}
        for field in ("open", "high", "low", "close"):
            av = getattr(a, field); bv = getattr(b, field)
            midpoint = median([av, bv])
            deviations[field] = abs(av - bv) / midpoint if midpoint else 1.0
        row_ok = skew <= max_timestamp_skew_minutes and all(v <= max_ohlc_deviation_pct for v in deviations.values())
        comparisons.append({
            "primary_ts": ta.isoformat(), "secondary_ts": tb.isoformat(), "timestamp_skew_minutes": skew,
            "primary_ohlc": {k: getattr(a, k) for k in ("open", "high", "low", "close")},
            "secondary_ohlc": {k: getattr(b, k) for k in ("open", "high", "low", "close")},
            "ohlc_deviation_pct": deviations, "passed": row_ok,
        })
        passed = passed and row_ok
    return {**base, "passed": passed, "comparisons": comparisons,
            "reason": "sources_agree" if passed else "independent_sources_disagree_or_misaligned"}


def apply_cross_source_verification(primary: DataVerdict, result: Optional[dict], require_for_trade: bool) -> DataVerdict:
    primary.cross_source = result
    if require_for_trade:
        if result is None or not result.get("passed"):
            primary.trusted_for_trade = False
            primary.reasons.append("independent source confirmation required for trade eligibility")
    elif result is not None and not result.get("passed"):
        primary.trusted_for_trade = False
        primary.trusted_for_analysis = False
        primary.reasons.append("independent sources disagree or are misaligned")
    return primary
