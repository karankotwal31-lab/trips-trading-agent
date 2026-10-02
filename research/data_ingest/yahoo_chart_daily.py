#!/usr/bin/env python3
"""Research-only direct Yahoo chart ingestion with mandatory adjusted closes.

Uses Yahoo's unauthenticated chart endpoint. This is an unofficial research source, never
production Truth and never a broker path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
USER_AGENT = "Mozilla/5.0 (compatible; TripsResearchEvidence/1.0; +https://github.com/karankotwal31-lab/trips-trading-agent)"


class IngestError(RuntimeError):
    pass


def completed_us_session(day: date, observed_at: datetime) -> bool:
    """Conservative research cutoff for the fixed US-listed ETF universe.

    Wait until 17:00 New York time, including on early-close days. This is a
    completion buffer, not proof of real-time entitlement or a market calendar.
    """
    if observed_at.tzinfo is None:
        raise IngestError("observation time must be timezone-aware")
    local = observed_at.astimezone(ZoneInfo("America/New_York"))
    return day < local.date() or (day == local.date() and local.hour >= 17)


def _universe(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    classes = raw.get("classes")
    if not isinstance(classes, dict):
        raise IngestError("universe classes missing")
    out: list[str] = []
    for members in classes.values():
        if not isinstance(members, list):
            raise IngestError("invalid universe members")
        for symbol in members:
            if not isinstance(symbol, str) or not symbol.strip():
                raise IngestError("invalid universe symbol")
            out.append(symbol.strip())
    if len(out) != len(set(out)):
        raise IngestError("duplicate universe symbol")
    return sorted(out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _epoch(day: str) -> int:
    dt = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
    return int(dt.timestamp())


def _finite(value: object, label: str) -> float:
    if value is None:
        raise IngestError(f"{label} is null")
    try:
        x = float(value)
    except Exception as exc:
        raise IngestError(f"{label} is not numeric") from exc
    if not math.isfinite(x):
        raise IngestError(f"{label} is not finite")
    return x


def _request_json(url: str, *, timeout: int = 30) -> Mapping[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(32 * 1024 * 1024)
        if len(raw) >= 32 * 1024 * 1024:
            raise IngestError("Yahoo response exceeded 32 MiB safety ceiling")
        if int(getattr(response, "status", 200)) != 200:
            raise IngestError(f"Yahoo returned HTTP {response.status}")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise IngestError("Yahoo response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise IngestError("Yahoo chart response was not an object")
    return parsed


def _extract(symbol: str, payload: Mapping[str, object], *, observed_at: datetime | None = None) -> list[dict]:
    observed_at = observed_at or datetime.now(timezone.utc)
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise IngestError(f"{symbol}: chart object missing")
    error = chart.get("error")
    if error:
        raise IngestError(f"{symbol}: Yahoo chart error: {error}")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise IngestError(f"{symbol}: exactly one chart result required")
    result = results[0]
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        raise IngestError(f"{symbol}: timestamp/indicators missing")

    quotes = indicators.get("quote")
    adjs = indicators.get("adjclose")
    if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict):
        raise IngestError(f"{symbol}: quote series missing")
    if not isinstance(adjs, list) or len(adjs) != 1 or not isinstance(adjs[0], dict):
        raise IngestError(
            f"{symbol}: indicators.adjclose missing; Close will never be substituted for Adj Close"
        )
    quote = quotes[0]
    adj = adjs[0].get("adjclose")
    fields = {
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "volume": quote.get("volume"),
        "adj_close": adj,
    }
    for name, series in fields.items():
        if not isinstance(series, list) or len(series) != len(timestamps):
            raise IngestError(f"{symbol}: {name} length does not match timestamps")

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    tz_offset = int(meta.get("gmtoffset") or 0)
    rows: list[dict] = []
    previous: date | None = None
    for i, stamp in enumerate(timestamps):
        if not isinstance(stamp, (int, float)):
            raise IngestError(f"{symbol}: non-numeric timestamp at index {i}")
        # Yahoo timestamps are exchange-local bar timestamps represented in UTC seconds.
        d = datetime.fromtimestamp(int(stamp) + tz_offset, tz=timezone.utc).date()
        if not completed_us_session(d, observed_at):
            continue
        values = {name: fields[name][i] for name in fields}
        # Yahoo sometimes emits null incomplete rows. Dropping a fully unusable row is safer than
        # inventing a value; any partial row is also excluded and the harness validates history.
        if any(value is None for value in values.values()):
            continue
        o = _finite(values["open"], f"{symbol}:{d}:open")
        h = _finite(values["high"], f"{symbol}:{d}:high")
        l = _finite(values["low"], f"{symbol}:{d}:low")
        c = _finite(values["close"], f"{symbol}:{d}:close")
        a = _finite(values["adj_close"], f"{symbol}:{d}:adj_close")
        v = _finite(values["volume"], f"{symbol}:{d}:volume")
        if min(o, h, l, c, a) <= 0:
            raise IngestError(f"{symbol}:{d}: prices must be positive")
        if v < 0:
            raise IngestError(f"{symbol}:{d}: volume must be non-negative")
        if h < max(o, c, l) or l > min(o, c, h):
            raise IngestError(f"{symbol}:{d}: invalid OHLC geometry")
        if previous is not None and d <= previous:
            raise IngestError(f"{symbol}: dates not strictly increasing at {d}")
        previous = d
        rows.append({
            "date": d.isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "adj_close": a,
            "volume": v,
        })
    if not rows:
        raise IngestError(f"{symbol}: no usable daily rows")
    return rows


def fetch_symbol(symbol: str, *, start: str, end: str, attempts: int = 4) -> tuple[list[dict], str]:
    params = urllib.parse.urlencode({
        "period1": _epoch(start),
        "period2": _epoch(end),
        "interval": "1d",
        "events": "div,splits,capitalGains",
        "includeAdjustedClose": "true",
    })
    failures: list[str] = []
    for attempt in range(attempts):
        host = HOSTS[attempt % len(HOSTS)]
        url = f"{host}/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{params}"
        try:
            return _extract(symbol, _request_json(url)), host
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, IngestError) as exc:
            failures.append(f"{host} attempt {attempt + 1}: {type(exc).__name__}: {exc}")
            if attempt + 1 < attempts:
                # Deterministic base with tiny jitter only for avoiding synchronized public-endpoint bursts.
                time.sleep((2 ** attempt) + random.Random(f"{symbol}:{attempt}").random())
    raise IngestError(f"{symbol}: all direct Yahoo chart attempts failed: {' | '.join(failures)}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = ("date", "open", "high", "low", "close", "adj_close", "volume")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "date": row["date"],
                "open": format(float(row["open"]), ".12g"),
                "high": format(float(row["high"]), ".12g"),
                "low": format(float(row["low"]), ".12g"),
                "close": format(float(row["close"]), ".12g"),
                "adj_close": format(float(row["adj_close"]), ".12g"),
                "volume": str(int(round(float(row["volume"])))),
            })


def ingest(*, universe_path: Path, output_dir: Path, start: str, end: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "provider": "Yahoo Finance direct chart JSON",
        "provider_role": "research_historical_only",
        "production_market_data": False,
        "session_completion_policy": "US_LISTED_ETF_AFTER_17_NEW_YORK",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "start_requested": start,
        "end_requested_exclusive": end,
        "valid": {},
        "invalid": {},
    }
    manifest: list[str] = []
    for symbol in _universe(universe_path):
        try:
            rows, host = fetch_symbol(symbol, start=start, end=end)
            path = output_dir / f"{symbol}.csv"
            _write_csv(path, rows)
            digest = _sha256(path)
            manifest.append(f"{digest}  {path.name}")
            summary["valid"][symbol] = {
                "rows": len(rows),
                "first_date": rows[0]["date"],
                "last_date": rows[-1]["date"],
                "sha256": digest,
                "host": host,
            }
        except Exception as exc:
            summary["invalid"][symbol] = f"{type(exc).__name__}: {exc}"

    manifest_path = output_dir / "manifest.sha256"
    manifest_path.write_text("\n".join(sorted(manifest)) + ("\n" if manifest else ""), encoding="utf-8")
    summary["manifest_sha256"] = _sha256(manifest_path)
    summary["valid_asset_count"] = len(summary["valid"])
    summary["invalid_asset_count"] = len(summary["invalid"])
    summary_path = output_dir.parent / "ingest_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=Path, default=ROOT / "research" / "trend" / "universe.json")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--start", default="1990-01-01")
    ap.add_argument("--end", default=(date.today() + timedelta(days=1)).isoformat())
    args = ap.parse_args()
    summary = ingest(
        universe_path=args.universe,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
