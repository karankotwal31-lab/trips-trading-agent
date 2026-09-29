"""Synthetic scenario-bias control for the research trend harness.

Synthetic profit is never evidence of edge. Results depend on scenario design and the driftless
null is shown next to every family result.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from datetime import date, timedelta
from pathlib import Path

from research.trend.core import DailyBar, simulate_strategy


FAMILIES = (
    "uptrend",
    "downtrend",
    "mean_reverting_chop",
    "driftless_random_walk",
    "crash_gap",
    "liquidity_stress",
)
WARNING = (
    "Synthetic outcomes depend on scenario design; synthetic profit is never evidence of edge. "
    "Always compare each family with the driftless random-walk null."
)


def _business_dates(n: int) -> list[date]:
    out = []
    d = date(2010, 1, 4)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _make_series(family: str, seed: int, symbol_index: int, n: int = 560) -> list[DailyBar]:
    rng = random.Random(seed * 1009 + symbol_index * 9176 + 17)
    dates = _business_dates(n)
    price = 100.0 + symbol_index * 7.0
    anchor = price
    out: list[DailyBar] = []
    for i, d in enumerate(dates):
        if family == "uptrend":
            ret = 0.0008 + rng.gauss(0, 0.006)
        elif family == "downtrend":
            ret = -0.0008 + rng.gauss(0, 0.006)
        elif family == "mean_reverting_chop":
            pull = math.log(anchor / price) * 0.08
            ret = pull + rng.gauss(0, 0.008)
        elif family == "driftless_random_walk":
            ret = rng.gauss(0, 0.007)
        elif family == "crash_gap":
            ret = 0.00035 + rng.gauss(0, 0.006)
            if i in (340, 341):
                ret += -0.10 if i == 340 else -0.04
            if 390 <= i < 430:
                ret += 0.0015
        elif family == "liquidity_stress":
            ret = 0.0003 + rng.gauss(0, 0.009)
            if 320 <= i < 355:
                ret += rng.choice((-0.025, 0.025))
        else:
            raise ValueError(family)
        close = max(1.0, price * (1.0 + ret))
        wick = abs(rng.gauss(0, 0.002))
        high = max(price, close) * (1 + wick)
        low = min(price, close) * (1 - wick)
        volume = 1_000_000.0
        if family == "liquidity_stress" and 320 <= i < 355:
            volume = 100.0
        out.append(DailyBar(d, price, high, low, close, close, volume))
        price = close
    return out


def _quantile(values: list[float], q: float) -> float:
    x = sorted(values)
    if not x:
        return 0.0
    pos = q * (len(x) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return x[lo]
    frac = pos - lo
    return x[lo] * (1 - frac) + x[hi] * frac


def _summary(pnls: list[float], trades: list[int]) -> dict:
    return {
        "runs": len(pnls),
        "profitable_pct": sum(x > 0 for x in pnls) / len(pnls),
        "net_pnl_mean": statistics.mean(pnls),
        "net_pnl_median": statistics.median(pnls),
        "net_pnl_q05": _quantile(pnls, 0.05),
        "net_pnl_q25": _quantile(pnls, 0.25),
        "net_pnl_q75": _quantile(pnls, 0.75),
        "net_pnl_q95": _quantile(pnls, 0.95),
        "trade_count_mean": statistics.mean(trades),
        "trade_count_median": statistics.median(trades),
    }


def run_scenario_bias(*, seeds: int = 200) -> dict:
    if seeds < 200:
        raise ValueError("scenario-bias control requires at least 200 seeds per family")
    spec = {
        "signal": {"lookback_days": 252},
        "volatility": {"window_days": 60, "target_portfolio_volatility": 0.10},
        "sizing": {"per_asset_cap": 0.20, "per_class_cap": 0.40, "gross_cap": 1.0},
    }
    costs = {"base_cost_bps": {"equity": 3}}
    classes = {"SYN_A": "equity", "SYN_B": "equity", "SYN_C": "equity"}
    family_results: dict[str, dict] = {}
    for family in FAMILIES:
        pnls: list[float] = []
        trades: list[int] = []
        for seed in range(seeds):
            data = {
                symbol: _make_series(family, seed, idx)
                for idx, symbol in enumerate(sorted(classes))
            }
            multiplier = 4.0 if family == "liquidity_stress" else 2.0
            sim = simulate_strategy(
                data,
                classes,
                spec,
                costs,
                cost_multiplier=multiplier,
            )
            pnls.append(float(sim["ending_equity"]) - 1.0)
            trades.append(int(sim["trade_count"]))
        family_results[family] = _summary(pnls, trades)

    null = dict(family_results["driftless_random_walk"])
    for family in FAMILIES:
        family_results[family]["driftless_null_reference"] = null
        family_results[family]["synthetic_warning"] = WARNING
    return {
        "schema_version": 1,
        "seeds_per_family": seeds,
        "families": family_results,
        "warning": WARNING,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    report = run_scenario_bias(seeds=args.seeds)
    raw = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
