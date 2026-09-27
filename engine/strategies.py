from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List

from indicators import atr, ema, realized_volatility, rsi, zscore
from providers import Bar


@dataclass
class StrategyVote:
    name: str
    direction: str  # LONG or FLAT
    score: float    # heuristic signal score, NOT a probability
    evidence: List[str]

    def to_dict(self):
        return asdict(self)


@dataclass
class MarketFeatures:
    price: float
    ema20: float
    ema50: float
    rsi14: float
    atr14: float
    vol: float
    z20: float
    breakout20: bool
    volume_ratio: float
    regime: str

    def to_dict(self):
        return asdict(self)


def features(bars: List[Bar]) -> MarketFeatures:
    if len(bars) < 60:
        raise ValueError("at least 60 closed bars required for feature computation")
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r = rsi(closes, 14)
    a = atr(highs, lows, closes, 14)
    rv = realized_volatility(closes[-60:])
    z = zscore(closes, 20)
    prev_high = max(closes[-21:-1])
    breakout = closes[-1] > prev_high
    prior_vols = vols[-21:-1]
    avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols and sum(prior_vols) > 0 else 0.0
    volume_ratio = (vols[-1] / avg_vol) if avg_vol else 1.0

    slope = (e20 / e50 - 1.0) if e50 else 0.0
    if slope > 0.006:
        regime = "TREND_UP"
    elif slope < -0.006:
        regime = "TREND_DOWN"
    elif rv > 0.45:
        regime = "HIGH_VOL"
    else:
        regime = "RANGE"

    return MarketFeatures(closes[-1], e20, e50, r, a, rv, z, breakout, volume_ratio, regime)


def evaluate(f: MarketFeatures) -> List[StrategyVote]:
    votes: List[StrategyVote] = []

    trend_score = 0.0; ev = []
    if f.ema20 > f.ema50: trend_score += 0.35; ev.append("EMA20 above EMA50")
    if f.price > f.ema20: trend_score += 0.25; ev.append("price above EMA20")
    if 50 <= f.rsi14 <= 70: trend_score += 0.20; ev.append("RSI confirms trend without extreme extension")
    if f.regime == "TREND_UP": trend_score += 0.20; ev.append("regime classified TREND_UP")
    votes.append(StrategyVote("trend", "LONG" if trend_score >= 0.70 else "FLAT", min(trend_score, 1.0), ev))

    breakout_score = 0.0; ev = []
    if f.breakout20: breakout_score += 0.55; ev.append("20-bar closing breakout")
    if f.volume_ratio >= 1.15: breakout_score += 0.20; ev.append("volume expansion")
    if f.ema20 > f.ema50: breakout_score += 0.15; ev.append("trend alignment")
    if f.rsi14 < 75: breakout_score += 0.10; ev.append("RSI not extremely extended")
    votes.append(StrategyVote("breakout", "LONG" if breakout_score >= 0.70 else "FLAT", min(breakout_score, 1.0), ev))

    mr_score = 0.0; ev = []
    if f.z20 <= -1.75: mr_score += 0.45; ev.append("price deeply below 20-bar mean")
    if f.rsi14 <= 32: mr_score += 0.35; ev.append("RSI oversold")
    if f.regime in ("RANGE", "TREND_UP"): mr_score += 0.20; ev.append("regime permits mean reversion")
    votes.append(StrategyVote("mean_reversion", "LONG" if mr_score >= 0.75 else "FLAT", min(mr_score, 1.0), ev))
    return votes


def consensus(votes: List[StrategyVote]) -> Dict[str, object]:
    long_votes = [v for v in votes if v.direction == "LONG"]
    if not long_votes:
        return {"direction": "FLAT", "signal_score": 0.0, "strategy": None, "conflict": False, "agreement_count": 0}

    best = max(long_votes, key=lambda v: v.score)
    nonconfirm = [v for v in votes if v.direction == "FLAT" and v.score < 0.35]
    conflict = len(long_votes) == 1 and len(nonconfirm) >= 1
    agreement_bonus = 0.08 * (len(long_votes) - 1)
    score = min(0.99, best.score + agreement_bonus)
    return {"direction": "LONG", "signal_score": score, "strategy": best.name,
            "conflict": conflict, "agreement_count": len(long_votes)}
