"""Reviewed realtime market-data adapters for Trip's independent Truth feed.

These additive adapters do not edit the frozen provider registry. They normalize each provider's
documented realtime API into Trip's canonical 60-minute Bar model. Actual trade eligibility still
requires frozen Truth freshness/integrity checks and independent cross-source agreement.

Only market-data GET requests exist here. There is no brokerage or capital-release surface.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, List, Mapping, Optional
from zoneinfo import ZoneInfo

from providers import Bar, ProviderIdentity

from .market_data import APPROVED_INTERVAL, MarketDataError

MAX_RESPONSE_BYTES = 10_000_000

ALPHA_VANTAGE_API_BASE = "https://www.alphavantage.co/query"
ALPHA_VANTAGE_CREDENTIAL_ENV = "ALPHA_VANTAGE_API_KEY"
ALPHA_VANTAGE_PROVIDER_INTERVAL = "60min"

TWELVE_DATA_API_BASE = "https://api.twelvedata.com/time_series"
TWELVE_DATA_CREDENTIAL_ENV = "TWELVE_DATA_API_KEY"
TWELVE_DATA_PROVIDER_INTERVAL = "1h"


def _read_json_response(opener: Callable[..., Any], request: urllib.request.Request,
                        *, timeout: float, provider: str) -> Mapping[str, Any]:
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        # Do not include provider exception text: URL-bearing HTTP errors may contain API keys.
        raise MarketDataError(
            f"{provider} realtime request failed: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MarketDataError(f"{provider} realtime response exceeded 10 MB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise MarketDataError(f"{provider} realtime response was not valid JSON") from None
    if not isinstance(payload, Mapping):
        raise MarketDataError(f"{provider} realtime response was not an object")
    return payload


class AlphaVantageRealtimeProvider:
    """Alpha Vantage intraday provider that explicitly requests realtime entitlement."""

    identity = ProviderIdentity(
        "alpha_vantage_realtime",
        "alpha_vantage",
        timestamp_semantics="bar_start",
        can_request_realtime_entitlement=True,
    )
    source_kind = "real"
    interval = APPROVED_INTERVAL
    provider_name = "alpha_vantage_realtime"

    def __init__(self, *, api_key: str, opener: Optional[Callable[..., Any]] = None,
                 timeout_seconds: float = 15.0) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise MarketDataError(f"{ALPHA_VANTAGE_CREDENTIAL_ENV} is missing")
        self._api_key = key
        self._opener = opener or urllib.request.urlopen
        self._timeout = float(timeout_seconds)

    def _url(self, symbol: str) -> str:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            raise MarketDataError("Alpha Vantage symbol is empty")
        params = urllib.parse.urlencode({
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": ALPHA_VANTAGE_PROVIDER_INTERVAL,
            "outputsize": "full",
            "adjusted": "false",
            "extended_hours": "false",
            "entitlement": "realtime",
            "apikey": self._api_key,
        })
        return f"{ALPHA_VANTAGE_API_BASE}?{params}"

    def bars(self, symbol: str, count: int = 240) -> List[Bar]:
        requested_symbol = str(symbol or "").strip().upper()
        request = urllib.request.Request(
            self._url(requested_symbol),
            headers={"User-Agent": "TripsExecution/1.0", "Accept": "application/json"})
        payload = _read_json_response(
            self._opener, request, timeout=self._timeout, provider="Alpha Vantage")

        series_key = next(
            (key for key in payload if str(key).startswith("Time Series")), None)
        if not series_key:
            raise MarketDataError("Alpha Vantage realtime response contained no time series")
        meta = payload.get("Meta Data") or {}
        if not isinstance(meta, Mapping):
            raise MarketDataError("Alpha Vantage realtime response omitted metadata")
        returned_symbol = str(meta.get("2. Symbol") or "").strip().upper()
        if returned_symbol and returned_symbol != requested_symbol:
            raise MarketDataError(
                f"Alpha Vantage symbol mismatch: requested={requested_symbol}, returned={returned_symbol}")
        returned_interval = str(meta.get("4. Interval") or "").strip().lower()
        if returned_interval and returned_interval != ALPHA_VANTAGE_PROVIDER_INTERVAL:
            raise MarketDataError(
                f"Alpha Vantage interval mismatch: expected={ALPHA_VANTAGE_PROVIDER_INTERVAL}, "
                f"returned={returned_interval}")
        timezone_name = str(
            meta.get("6. Time Zone") or meta.get("5. Time Zone") or
            meta.get("7. Time Zone") or "").strip()
        if not timezone_name:
            raise MarketDataError("Alpha Vantage realtime response omitted market timezone")
        try:
            source_tz = ZoneInfo(timezone_name)
        except Exception:
            raise MarketDataError(
                f"Alpha Vantage returned unrecognized timezone {timezone_name!r}") from None

        series = payload.get(series_key)
        if not isinstance(series, Mapping) or not series:
            raise MarketDataError("Alpha Vantage realtime time series was empty")
        rows: List[Bar] = []
        try:
            for raw_ts, row in series.items():
                if not isinstance(row, Mapping):
                    raise MarketDataError("Alpha Vantage time-series row is not an object")
                local = datetime.strptime(
                    str(raw_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=source_tz)
                rows.append(Bar(
                    ts=local.astimezone(timezone.utc).isoformat(),
                    open=float(row["1. open"]),
                    high=float(row["2. high"]),
                    low=float(row["3. low"]),
                    close=float(row["4. close"]),
                    volume=float(row["5. volume"]),
                ))
        except MarketDataError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(
                f"Alpha Vantage realtime payload is malformed: {type(exc).__name__}") from None
        rows.sort(key=lambda bar: bar.ts)
        return rows[-min(max(int(count), 1), len(rows)):]


class TwelveDataRealtimeProvider:
    """Twelve Data US-equity provider normalized to Trip's canonical 60-minute bars."""

    identity = ProviderIdentity(
        "twelve_data_realtime",
        "twelve_data",
        timestamp_semantics="bar_start",
        can_request_realtime_entitlement=True,
    )
    source_kind = "real"
    interval = APPROVED_INTERVAL
    provider_name = "twelve_data_realtime"

    def __init__(self, *, api_key: str, opener: Optional[Callable[..., Any]] = None,
                 timeout_seconds: float = 15.0) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise MarketDataError(f"{TWELVE_DATA_CREDENTIAL_ENV} is missing")
        self._api_key = key
        self._opener = opener or urllib.request.urlopen
        self._timeout = float(timeout_seconds)

    def _url(self, symbol: str, count: int) -> str:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            raise MarketDataError("Twelve Data symbol is empty")
        outputsize = min(max(int(count), 1), 5000)
        params = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": TWELVE_DATA_PROVIDER_INTERVAL,
            "outputsize": outputsize,
            "timezone": "UTC",
            "order": "asc",
            "format": "JSON",
            "prepost": "false",
            "adjust": "none",
            "apikey": self._api_key,
        })
        return f"{TWELVE_DATA_API_BASE}?{params}"

    def bars(self, symbol: str, count: int = 240) -> List[Bar]:
        requested_symbol = str(symbol or "").strip().upper()
        request = urllib.request.Request(
            self._url(requested_symbol, count),
            headers={"User-Agent": "TripsExecution/1.0", "Accept": "application/json"})
        payload = _read_json_response(
            self._opener, request, timeout=self._timeout, provider="Twelve Data")

        if str(payload.get("status") or "").lower() == "error":
            code = str(payload.get("code") or "provider_error")
            raise MarketDataError(f"Twelve Data realtime provider error code={code}")
        meta = payload.get("meta") or {}
        if not isinstance(meta, Mapping):
            raise MarketDataError("Twelve Data realtime response omitted metadata")
        returned_symbol = str(meta.get("symbol") or "").strip().upper()
        if returned_symbol and returned_symbol != requested_symbol:
            raise MarketDataError(
                f"Twelve Data symbol mismatch: requested={requested_symbol}, returned={returned_symbol}")
        returned_interval = str(meta.get("interval") or "").strip().lower()
        if returned_interval and returned_interval != TWELVE_DATA_PROVIDER_INTERVAL:
            raise MarketDataError(
                f"Twelve Data interval mismatch: expected={TWELVE_DATA_PROVIDER_INTERVAL}, "
                f"returned={returned_interval}")
        instrument_type = str(meta.get("type") or "").strip()
        if instrument_type and instrument_type not in {"Common Stock", "ETF", "Exchange-Traded Fund"}:
            raise MarketDataError(
                f"Twelve Data returned unsupported US-equity instrument type {instrument_type!r}")

        values = payload.get("values")
        if not isinstance(values, list) or not values:
            raise MarketDataError("Twelve Data realtime response contained no time-series values")
        rows: List[Bar] = []
        try:
            for row in values:
                if not isinstance(row, Mapping):
                    raise MarketDataError("Twelve Data time-series row is not an object")
                raw_ts = str(row.get("datetime") or "").strip()
                if not raw_ts:
                    raise MarketDataError("Twelve Data time-series row is missing datetime")
                parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    # Request explicitly asks for UTC; never interpret naive timestamps as host local.
                    parsed = parsed.replace(tzinfo=timezone.utc)
                else:
                    parsed = parsed.astimezone(timezone.utc)
                rows.append(Bar(
                    ts=parsed.astimezone(timezone.utc).isoformat(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
        except MarketDataError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(
                f"Twelve Data realtime payload is malformed: {type(exc).__name__}") from None
        rows.sort(key=lambda bar: bar.ts)
        return rows[-min(max(int(count), 1), len(rows)):]


__all__ = [
    "ALPHA_VANTAGE_API_BASE",
    "ALPHA_VANTAGE_CREDENTIAL_ENV",
    "ALPHA_VANTAGE_PROVIDER_INTERVAL",
    "MAX_RESPONSE_BYTES",
    "TWELVE_DATA_API_BASE",
    "TWELVE_DATA_CREDENTIAL_ENV",
    "TWELVE_DATA_PROVIDER_INTERVAL",
    "AlphaVantageRealtimeProvider",
    "TwelveDataRealtimeProvider",
]
