from __future__ import annotations
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable, Sequence


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("insufficient data for EMA")
    alpha = 2.0 / (period + 1.0)
    value = mean(values[:period])
    for x in values[period:]:
        value = alpha * x + (1.0 - alpha) * value
    return value


def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("insufficient data for RSI")
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas[-period:]]
    losses = [max(-d, 0.0) for d in deltas[-period:]]
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float:
    if min(len(highs), len(lows), len(closes)) < period + 1:
        raise ValueError("insufficient data for ATR")
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return mean(trs[-period:])


def realized_volatility(values: Sequence[float], periods_per_year: int = 252 * 6) -> float:
    if len(values) < 3:
        return 0.0
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] != 0:
            returns.append(values[i] / values[i - 1] - 1.0)
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * sqrt(periods_per_year)


def zscore(values: Sequence[float], window: int = 20) -> float:
    if len(values) < window:
        raise ValueError("insufficient data for z-score")
    xs = list(values[-window:])
    mu = mean(xs)
    sd = pstdev(xs)
    return 0.0 if sd == 0 else (xs[-1] - mu) / sd


def percentile_rank(values: Iterable[float], x: float) -> float:
    xs = list(values)
    if not xs:
        return 0.0
    return sum(1 for v in xs if v <= x) / len(xs)
