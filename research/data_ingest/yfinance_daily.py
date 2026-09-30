#!/usr/bin/env python3
"""Research-only historical daily data ingestion from Yahoo Finance via yfinance.

This module is intentionally outside research/shadow and engine/. It may use the network to
materialize owner-approved research CSVs, but it cannot submit orders or contact a broker.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]


class IngestError(RuntimeError):
    pass


def _load_universe(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    classes = raw.get("classes")
    if not isinstance(classes, dict):
        raise IngestError("universe classes missing")
    symbols = []
    for class_name, members in classes.items():
        if not isinstance(class_name, str) or not isinstance(members, list):
            raise IngestError("invalid universe class mapping")
        for symbol in members:
            if not isinstance(symbol, str) or not symbol:
                raise IngestError("invalid universe symbol")
            symbols.append(symbol)
    if len(symbols) != len(set(symbols)):
        raise IngestError("duplicate universe symbol")
    return {"raw": raw, "symbols": sorted(symbols)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _finite(value: object, label: str) -> float:
    try:
        x = float(value)
    except Exception as exc:
        raise IngestError(f"{label} is not numeric") from exc
    if not math.isfinite(x):
        raise IngestError(f"{label} is not finite")
    return x


def normalize_frame(symbol: str, frame) -> list[dict]:
    required = ("Open", "High", "Low", "Close", "Adj Close", "Volume")
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise IngestError(
            f"{symbol}: Yahoo/yfinance response missing required columns {missing}; "
            "Adj Close is mandatory and Close will never be substituted"
        )
    rows = []
    previous = None
    for stamp, raw in frame.iterrows():
        d = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
        if previous is not None and d <= previous:
            raise IngestError(f"{symbol}: dates are not strictly increasing")
        previous = d
        o = _finite(raw["Open"], f"{symbol}:{d}:Open")
        h = _finite(raw["High"], f"{symbol}:{d}:High")
        l = _finite(raw["Low"], f"{symbol}:{d}:Low")
        c = _finite(raw["Close"], f"{symbol}:{d}:Close")
        adj = _finite(raw["Adj Close"], f"{symbol}:{d}:Adj Close")
        vol = _finite(raw["Volume"], f"{symbol}:{d}:Volume")
        if min(o, h, l, c, adj) <= 0:
            raise IngestError(f"{symbol}:{d}: prices must be positive")
        if vol < 0:
            raise IngestError(f"{symbol}:{d}: volume must be non-negative")
        if h < max(o, c, l) or l > min(o, c, h):
            raise IngestError(f"{symbol}:{d}: OHLC geometry invalid")
        rows.append({
            "date": d.isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "adj_close": adj,
            "volume": vol,
        })
    if not rows:
        raise IngestError(f"{symbol}: no rows returned")
    return rows


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("date", "open", "high", "low", "close", "adj_close", "volume")
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


def fetch_symbol(symbol: str, *, start: str, end: str, attempts: int = 3):
    import yfinance as yf

    failures = []
    for attempt in range(1, attempts + 1):
        try:
            frame = yf.download(
                symbol,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=False,
                back_adjust=False,
                repair=True,
                keepna=False,
                actions=False,
                progress=False,
                threads=False,
                ignore_tz=True,
                timeout=30,
                multi_level_index=False,
            )
            if frame is None or frame.empty:
                raise IngestError(f"{symbol}: empty response")
            return normalize_frame(symbol, frame)
        except Exception as exc:
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if attempt < attempts:
                time.sleep(2.0 * attempt)
    raise IngestError(f"{symbol}: all download attempts failed: {' | '.join(failures)}")


def ingest(
    *,
    universe_path: Path,
    output_dir: Path,
    start: str,
    end: str,
    pause_seconds: float,
) -> dict:
    universe = _load_universe(universe_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "provider": "Yahoo Finance via yfinance",
        "provider_role": "research_historical_only",
        "production_market_data": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "start_requested": start,
        "end_requested_exclusive": end,
        "valid": {},
        "invalid": {},
    }
    manifest_lines = []
    for index, symbol in enumerate(universe["symbols"]):
        try:
            rows = fetch_symbol(symbol, start=start, end=end)
            target = output_dir / f"{symbol}.csv"
            write_csv(target, rows)
            digest = _sha256(target)
            manifest_lines.append(f"{digest}  {target.name}")
            summary["valid"][symbol] = {
                "rows": len(rows),
                "first_date": rows[0]["date"],
                "last_date": rows[-1]["date"],
                "sha256": digest,
            }
        except Exception as exc:
            summary["invalid"][symbol] = f"{type(exc).__name__}: {exc}"
        if pause_seconds > 0 and index + 1 < len(universe["symbols"]):
            time.sleep(pause_seconds)

    manifest_path = output_dir / "manifest.sha256"
    manifest_path.write_text("\n".join(sorted(manifest_lines)) + ("\n" if manifest_lines else ""), encoding="utf-8")
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
    ap.add_argument("--pause-seconds", type=float, default=0.75)
    args = ap.parse_args()
    summary = ingest(
        universe_path=args.universe,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        pause_seconds=args.pause_seconds,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    # Ingestion itself is honest about partial availability. The preregistered harness decides
    # whether the resulting valid universe/history is sufficient for evidence.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
