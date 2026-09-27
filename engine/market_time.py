from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List

from providers import Bar


class IntervalError(ValueError):
    pass


def interval_to_timedelta(interval: str) -> timedelta:
    text = interval.strip().lower()
    aliases = {"1h": 60, "60m": 60, "60min": 60}
    if text in aliases:
        return timedelta(minutes=aliases[text])
    if text.endswith("min"):
        value = int(text[:-3])
        if value <= 0:
            raise IntervalError("interval must be positive")
        return timedelta(minutes=value)
    if text.endswith("m"):
        value = int(text[:-1])
        if value <= 0:
            raise IntervalError("interval must be positive")
        return timedelta(minutes=value)
    if text.endswith("h"):
        value = int(text[:-1])
        if value <= 0:
            raise IntervalError("interval must be positive")
        return timedelta(hours=value)
    raise IntervalError(f"unsupported interval: {interval}")


def closed_bars_only(bars: Iterable[Bar], interval: str, *, now: datetime | None = None,
                     close_lag_seconds: int = 20) -> List[Bar]:
    """Return bars whose full interval is already in the past.

    Provider timestamps are treated as bar-start timestamps. This intentionally fails closed:
    if a provider uses another timestamp convention, its adapter must normalize it before here.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = now.astimezone(timezone.utc) - timedelta(seconds=close_lag_seconds)
    duration = interval_to_timedelta(interval)
    result: List[Bar] = []
    for bar in bars:
        dt = datetime.fromisoformat(bar.ts)
        if dt.tzinfo is None:
            continue
        if dt.astimezone(timezone.utc) + duration <= cutoff:
            result.append(bar)
    return result
