from __future__ import annotations

import json
import math
import os
import random
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from redaction import sanitize


@dataclass
class Bar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ProviderIdentity:
    name: str
    source_family: str
    timestamp_semantics: str = "bar_start"
    fixed_source_kind: Optional[str] = None
    can_request_realtime_entitlement: bool = False


class MarketDataError(RuntimeError):
    pass


class DemoProvider:
    """Deterministic synthetic data. It can never be relabeled as real data."""
    identity = ProviderIdentity("demo", "trips_synthetic", fixed_source_kind="demo")
    name = identity.name

    def bars(self, symbol: str, count: int = 180) -> List[Bar]:
        seed = sum(ord(c) for c in symbol)
        rng = random.Random(seed)
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=count)
        price = 100.0 + (seed % 50)
        result = []
        regime = (seed % 3) - 1
        for i in range(count):
            drift = regime * 0.00035 + 0.0006 * math.sin(i / 15.0)
            shock = rng.gauss(0, 0.004)
            prev = price
            price = max(1.0, price * (1 + drift + shock))
            hi = max(prev, price) * (1 + abs(rng.gauss(0, 0.0015)))
            lo = min(prev, price) * (1 - abs(rng.gauss(0, 0.0015)))
            result.append(Bar(
                ts=(start + timedelta(hours=i)).isoformat(),
                open=round(prev, 4), high=round(hi, 4), low=round(lo, 4), close=round(price, 4),
                volume=round(1_000_000 * (1 + abs(rng.gauss(0, 0.2))), 2),
            ))
        return result


class AlphaVantageProvider:
    identity = ProviderIdentity("alpha_vantage", "alpha_vantage", can_request_realtime_entitlement=True)
    name = identity.name

    def __init__(self, interval: str = "60min", source_kind: str = "delayed"):
        self.source_kind = source_kind
        self.key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
        if not self.key:
            raise MarketDataError("ALPHA_VANTAGE_API_KEY is missing")
        self.interval = interval

    def bars(self, symbol: str, count: int = 180) -> List[Bar]:
        params = urllib.parse.urlencode({
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": self.interval,
            "outputsize": "full",
            "adjusted": "false",
            "extended_hours": "false",
            "apikey": self.key,
            **({"entitlement": "realtime"} if self.source_kind == "real" else ({"entitlement": "delayed"} if self.source_kind == "delayed" else {})),
        })
        req = urllib.request.Request(
            f"https://www.alphavantage.co/query?{params}",
            headers={"User-Agent": "Trips/0.6", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise MarketDataError("provider response exceeded 10 MB safety limit")
                payload = json.loads(raw.decode("utf-8"))
        except MarketDataError:
            raise
        except Exception as e:
            raise MarketDataError(f"market data request failed: {type(e).__name__}: {sanitize(str(e))}") from e

        key = next((k for k in payload if k.startswith("Time Series")), None)
        if not key:
            msg = payload.get("Note") or payload.get("Information") or payload.get("Error Message") or "missing time series"
            raise MarketDataError(str(msg))

        meta = payload.get("Meta Data", {})
        returned_symbol = str(meta.get("2. Symbol") or "").strip()
        returned_interval = str(meta.get("4. Interval") or "").strip()
        if returned_symbol and returned_symbol.upper() != symbol.upper():
            raise MarketDataError(f"provider symbol mismatch: requested={symbol}, returned={returned_symbol}")
        if returned_interval and returned_interval.lower() != self.interval.lower():
            raise MarketDataError(f"provider interval mismatch: requested={self.interval}, returned={returned_interval}")
        tz_name = meta.get("6. Time Zone") or meta.get("5. Time Zone") or meta.get("7. Time Zone")
        if not tz_name:
            raise MarketDataError("provider response omitted market timezone")
        try:
            source_tz = ZoneInfo(tz_name)
        except Exception as e:
            raise MarketDataError(f"unrecognized market timezone: {tz_name}") from e

        rows = []
        try:
            for ts, row in payload[key].items():
                local_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=source_tz)
                rows.append(Bar(
                    ts=local_dt.astimezone(timezone.utc).isoformat(),
                    open=float(row["1. open"]), high=float(row["2. high"]), low=float(row["3. low"]),
                    close=float(row["4. close"]), volume=float(row["5. volume"]),
                ))
        except (KeyError, TypeError, ValueError) as e:
            raise MarketDataError(f"malformed Alpha Vantage payload: {e}") from e
        rows.sort(key=lambda b: b.ts)
        return rows[-count:]


class TwelveDataProvider:
    identity = ProviderIdentity("twelve_data", "twelve_data")
    name = identity.name

    def __init__(self, interval: str = "60min", source_kind: str = "delayed"):
        self.source_kind = source_kind
        self.key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
        if not self.key:
            raise MarketDataError("TWELVE_DATA_API_KEY is missing")
        self.interval = interval

    def bars(self, symbol: str, count: int = 180) -> List[Bar]:
        params = urllib.parse.urlencode({
            "symbol": symbol, "interval": self.interval, "outputsize": min(max(count, 1), 5000),
            "timezone": "UTC", "order": "asc", "format": "JSON", "prepost": "false", "adjust": "none", "apikey": self.key,
        })
        req = urllib.request.Request(
            f"https://api.twelvedata.com/time_series?{params}",
            headers={"User-Agent": "Trips/0.6", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise MarketDataError("provider response exceeded 10 MB safety limit")
                payload = json.loads(raw.decode("utf-8"))
        except MarketDataError:
            raise
        except Exception as e:
            raise MarketDataError(f"Twelve Data request failed: {type(e).__name__}: {sanitize(str(e))}") from e

        if payload.get("status") == "error":
            raise MarketDataError(str(payload.get("message") or "Twelve Data error"))
        meta = payload.get("meta") or {}
        returned_symbol = str(meta.get("symbol") or "").strip()
        returned_interval = str(meta.get("interval") or "").strip()
        if returned_symbol and returned_symbol.upper() != symbol.upper():
            raise MarketDataError(f"provider symbol mismatch: requested={symbol}, returned={returned_symbol}")
        if returned_interval and returned_interval.lower() != self.interval.lower():
            raise MarketDataError(f"provider interval mismatch: requested={self.interval}, returned={returned_interval}")
        values = payload.get("values")
        if not isinstance(values, list) or not values:
            raise MarketDataError("Twelve Data response missing values")

        rows = []
        try:
            for row in values:
                ts = row.get("datetime")
                if not ts:
                    raise MarketDataError("Twelve Data row missing datetime")
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                rows.append(Bar(
                    ts=dt.isoformat(), open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
                ))
        except (KeyError, TypeError, ValueError) as e:
            raise MarketDataError(f"malformed Twelve Data payload: {e}") from e
        rows.sort(key=lambda b: b.ts)
        return rows[-count:]


def get_provider(name: str, interval: str, source_kind: str = "delayed"):
    if name == "demo":
        return DemoProvider()
    if name == "alpha_vantage":
        return AlphaVantageProvider(interval, source_kind)
    if name == "twelve_data":
        return TwelveDataProvider(interval, source_kind)
    raise MarketDataError(f"unknown provider: {name}")
