from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List

from providers import Bar


@dataclass
class CandleRead:
    label: str
    strength_score: float  # heuristic evidence strength, NOT a probability
    evidence: List[str]
    structure: str
    warnings: List[str]

    def to_dict(self):
        return asdict(self)


def _anatomy(b: Bar) -> dict:
    rng = max(b.high - b.low, 1e-12)
    body = abs(b.close - b.open)
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    return {
        "bullish": b.close > b.open,
        "bearish": b.close < b.open,
        "body_ratio": body / rng,
        "upper_wick_ratio": upper / rng,
        "lower_wick_ratio": lower / rng,
        "range": rng,
    }


def interpret(bars: List[Bar]) -> CandleRead:
    if len(bars) < 25:
        return CandleRead("INSUFFICIENT_DATA", 0.0, [], "UNKNOWN", ["Need at least 25 closed candles"])

    cur, prev = bars[-1], bars[-2]
    a = _anatomy(cur)
    evidence: List[str] = []
    warnings: List[str] = []

    recent = bars[-20:]
    swing_high = max(x.high for x in recent[:-1])
    swing_low = min(x.low for x in recent[:-1])
    closes = [x.close for x in bars[-10:]]
    up_steps = sum(closes[i] > closes[i - 1] for i in range(1, len(closes)))
    down_steps = sum(closes[i] < closes[i - 1] for i in range(1, len(closes)))

    if up_steps >= 7:
        structure = "UP_STRUCTURE"
        evidence.append("Most recent closes show persistent upward structure")
    elif down_steps >= 7:
        structure = "DOWN_STRUCTURE"
        evidence.append("Most recent closes show persistent downward structure")
    else:
        structure = "MIXED_OR_RANGE"

    bullish_engulf = cur.close > cur.open and prev.close < prev.open and cur.open <= prev.close and cur.close >= prev.open
    bearish_engulf = cur.close < cur.open and prev.close > prev.open and cur.open >= prev.close and cur.close <= prev.open
    if bullish_engulf:
        evidence.append("Bullish engulfing relationship detected")
    if bearish_engulf:
        evidence.append("Bearish engulfing relationship detected")

    if a["body_ratio"] <= 0.12:
        evidence.append("Small-body / doji-like candle")
    if a["lower_wick_ratio"] >= 0.55 and a["body_ratio"] <= 0.35:
        evidence.append("Strong lower-wick rejection")
    if a["upper_wick_ratio"] >= 0.55 and a["body_ratio"] <= 0.35:
        evidence.append("Strong upper-wick rejection")

    if cur.close > swing_high:
        evidence.append("Close broke above prior 20-candle swing high")
    if cur.close < swing_low:
        evidence.append("Close broke below prior 20-candle swing low")

    avg_range = sum(x.high - x.low for x in bars[-21:-1]) / 20
    if avg_range > 0:
        if a["range"] > 1.8 * avg_range:
            evidence.append("Range expansion candle")
            warnings.append("Expansion candle may increase slippage/gap risk")
        elif a["range"] < 0.55 * avg_range:
            evidence.append("Range compression candle")

    bull = 0.0
    bear = 0.0
    if structure == "UP_STRUCTURE": bull += 0.35
    if structure == "DOWN_STRUCTURE": bear += 0.35
    if bullish_engulf: bull += 0.30
    if bearish_engulf: bear += 0.30
    if cur.close > swing_high: bull += 0.25
    if cur.close < swing_low: bear += 0.25
    if a["lower_wick_ratio"] >= 0.55: bull += 0.10
    if a["upper_wick_ratio"] >= 0.55: bear += 0.10

    if bull >= 0.40 and bear >= 0.40:
        label = "CONFLICTING_CONTEXT"
        strength = min(0.95, max(bull, bear))
        warnings.append("Bullish and bearish candle evidence both material")
    elif bull >= 0.40 and bull > bear:
        label = "BULLISH_CONTEXT"
        strength = min(0.95, bull)
    elif bear >= 0.40 and bear > bull:
        label = "BEARISH_CONTEXT"
        strength = min(0.95, bear)
    else:
        label = "NEUTRAL_OR_AMBIGUOUS"
        strength = min(0.39, max(bull, bear))

    return CandleRead(label, strength, evidence, structure, warnings)
