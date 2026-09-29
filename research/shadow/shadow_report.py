"""Forward shadow report and D3 evaluator for Trip's research evidence.

No broker/order/network access. The report is derived from the append-only shadow journal and the
same owner-supplied CSV data/specifications used by the preregistered harness.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.shadow.journal import load_events, verify_file  # noqa: E402
from research.shadow.runner import common_dates, research_hashes  # noqa: E402
from research.trend.core import (  # noqa: E402
    DailyBar,
    _buy_and_hold,
    _fixed_monthly,
    asset_class_map,
    load_owner_data,
    metrics_from_equity,
    target_weights,
)


D3 = {
    "minimum_trading_days": 126,
    "minimum_monthly_rebalances": 6,
    "maximum_data_gap_trading_days": 3,
    "maximum_forward_drawdown": 0.20,
    "maximum_cost_drag_multiple": 1.5,
    "required_weight_parity": 1.0,
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _slice_data(
    data: Mapping[str, Sequence[DailyBar]],
    start: date,
    end: date,
) -> dict[str, list[DailyBar]]:
    return {
        symbol: [row for row in rows if start <= row.date <= end]
        for symbol, rows in data.items()
    }


def _finite_or_none(value: object) -> object:
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return 1e12 if value > 0 else -1e12
    return value


def _metrics(dates: Sequence[str], equity: Sequence[float]) -> dict:
    raw = metrics_from_equity(dates, equity)
    return {k: _finite_or_none(v) for k, v in raw.items()}


def replay_weight_parity(
    events: Sequence[Mapping[str, object]],
    data: Mapping[str, Sequence[DailyBar]],
    universe: Mapping[str, object],
    strategy_spec: Mapping[str, object],
) -> dict:
    classes = asset_class_map(universe)
    sizing = dict(strategy_spec["sizing"])
    signal = dict(strategy_spec["signal"])
    volatility = dict(strategy_spec["volatility"])
    decisions = [dict(x) for x in events if x.get("kind") == "DECISION"]
    if not decisions:
        return {
            "weight_parity": False,
            "parity_fraction": 0.0,
            "checked_decisions": 0,
            "mismatches": ["no decisions available for parity replay"],
        }

    passed = 0
    mismatches: list[dict] = []
    for event in decisions:
        d = date.fromisoformat(str(event["decision_market_date"]))
        expected = target_weights(
            data,
            {s: classes[s] for s in data},
            as_of=d,
            lookback=int(signal["lookback_days"]),
            vol_window=int(volatility["window_days"]),
            target_vol=float(volatility["target_portfolio_volatility"]),
            per_asset_cap=float(sizing["per_asset_cap"]),
            per_class_cap=float(sizing["per_class_cap"]),
            gross_cap=float(sizing["gross_cap"]),
        )
        recorded = {str(k): float(v) for k, v in dict(event["target_weights"]).items()}
        symbols = sorted(set(expected) | set(recorded))
        deltas = {s: abs(float(expected.get(s, 0.0)) - float(recorded.get(s, 0.0))) for s in symbols}
        if all(delta <= 1e-12 for delta in deltas.values()):
            passed += 1
        else:
            mismatches.append({
                "decision_market_date": d.isoformat(),
                "max_abs_weight_delta": max(deltas.values()) if deltas else 0.0,
            })
    fraction = passed / len(decisions)
    return {
        "weight_parity": fraction == 1.0,
        "parity_fraction": fraction,
        "checked_decisions": len(decisions),
        "mismatches": mismatches,
    }


def benchmark_report(
    data: Mapping[str, Sequence[DailyBar]],
    universe: Mapping[str, object],
    costs: Mapping[str, object],
    start: date,
    end: date,
) -> dict:
    sliced = _slice_data(data, start, end)
    valid = {s: rows for s, rows in sliced.items() if rows}
    if "SPY" not in valid or "IEF" not in valid:
        return {"available": False, "reason": "SPY and IEF are required for forward benchmarks"}
    dates = common_dates(valid)
    if len(dates) < 2:
        return {"available": False, "reason": "fewer than two common forward benchmark dates"}
    trimmed = {
        s: [row for row in rows if row.date in set(dates)]
        for s, rows in valid.items()
    }
    classes_all = asset_class_map(universe)
    classes = {s: classes_all[s] for s in trimmed}
    equal = {s: 1.0 / len(trimmed) for s in trimmed}
    spy = _buy_and_hold(trimmed, "SPY", costs, classes, cost_multiplier=1.0)
    ew = _fixed_monthly(trimmed, classes, costs, equal, cost_multiplier=1.0)
    sixty = _fixed_monthly(
        trimmed,
        classes,
        costs,
        {"SPY": 0.60, "IEF": 0.40},
        cost_multiplier=1.0,
    )
    return {
        "available": True,
        "SPY_BUY_AND_HOLD": _metrics(spy["dates"], spy["equity"]),
        "EQUAL_WEIGHT_UNIVERSE": _metrics(ew["dates"], ew["equity"]),
        "60_40_SPY_IEF": _metrics(sixty["dates"], sixty["equity"]),
    }


def build_forward_report(
    *,
    journal_path: Path,
    data_dir: Path,
    root: Path = ROOT,
    generated_at: datetime | None = None,
) -> dict:
    generated_at = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    chain = verify_file(journal_path)
    if not chain["valid"]:
        return {
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "verdict": "FORWARD_FAIL",
            "reasons": [f"journal chain invalid: {chain['reason']}"],
            "weight_parity": False,
            "hashes": research_hashes(root, data_dir),
        }

    events = load_events(journal_path)
    states = [dict(x) for x in events if x.get("kind") == "STATE"]
    trend = root / "research" / "trend"
    universe = _json(trend / "universe.json")
    spec = _json(trend / "strategy_spec.json")
    costs = _json(trend / "cost_model.json")
    hashes = research_hashes(root, data_dir)
    loaded = load_owner_data(data_dir, universe)

    if not loaded["sufficient"]:
        return {
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "verdict": "FORWARD_INSUFFICIENT",
            "reasons": list(loaded["reasons"]),
            "trading_days": len(states),
            "monthly_rebalances": sum(1 for x in events if x.get("kind") == "HYPOTHETICAL_REBALANCE"),
            "weight_parity": False,
            "hashes": hashes,
            "journal_chain": chain,
        }
    if len(states) < 2:
        return {
            "schema_version": 1,
            "generated_at": generated_at.isoformat(),
            "verdict": "FORWARD_INSUFFICIENT",
            "reasons": ["fewer than two processed shadow market dates"],
            "trading_days": len(states),
            "monthly_rebalances": 0,
            "weight_parity": False,
            "hashes": hashes,
            "journal_chain": chain,
        }

    data = loaded["data"]
    dates = [str(x["market_date"]) for x in states]
    equity = [float(x["equity"]) for x in states]
    metrics = _metrics(dates, equity)
    rebalances = [x for x in events if x.get("kind") == "HYPOTHETICAL_REBALANCE"]
    last = states[-1]
    observed_cost = float(last.get("cumulative_cost_fraction", 0.0))
    modeled_cost = float(last.get("modeled_cost_fraction", 0.0))
    cost_multiple = observed_cost / modeled_cost if modeled_cost > 0 else 0.0
    parity = replay_weight_parity(events, data, universe, spec)
    benchmark = benchmark_report(
        data,
        universe,
        costs,
        date.fromisoformat(dates[0]),
        date.fromisoformat(dates[-1]),
    )

    trading_days = len(states)
    rebalance_count = len(rebalances)
    max_gap = int(last.get("max_missing_trading_days", 0))
    max_dd = float(metrics["max_drawdown"])

    sufficiency_reasons = []
    if trading_days < D3["minimum_trading_days"]:
        sufficiency_reasons.append(
            f"trading days {trading_days} < required {D3['minimum_trading_days']}"
        )
    if rebalance_count < D3["minimum_monthly_rebalances"]:
        sufficiency_reasons.append(
            f"monthly rebalances {rebalance_count} < required {D3['minimum_monthly_rebalances']}"
        )

    criteria = [
        {
            "name": "no_data_gap_gt_3_trading_days",
            "passed": max_gap <= D3["maximum_data_gap_trading_days"],
            "observed": max_gap,
            "required": f"<= {D3['maximum_data_gap_trading_days']}",
        },
        {
            "name": "forward_max_drawdown",
            "passed": max_dd <= D3["maximum_forward_drawdown"],
            "observed": max_dd,
            "required": f"<= {D3['maximum_forward_drawdown']}",
        },
        {
            "name": "forward_cost_drag_multiple",
            "passed": cost_multiple <= D3["maximum_cost_drag_multiple"],
            "observed": cost_multiple,
            "required": f"<= {D3['maximum_cost_drag_multiple']}",
        },
        {
            "name": "weight_parity",
            "passed": parity["weight_parity"] is True,
            "observed": parity["parity_fraction"],
            "required": D3["required_weight_parity"],
        },
    ]

    if sufficiency_reasons:
        verdict = "FORWARD_INSUFFICIENT"
        reasons = sufficiency_reasons
    elif all(x["passed"] for x in criteria):
        verdict = "FORWARD_PASS"
        reasons = []
    else:
        verdict = "FORWARD_FAIL"
        reasons = [x["name"] for x in criteria if not x["passed"]]

    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "verdict": verdict,
        "reasons": reasons,
        "d3": D3,
        "trading_days": trading_days,
        "monthly_rebalances": rebalance_count,
        "metrics": metrics,
        "max_data_gap_trading_days": max_gap,
        "forward_cost_drag_multiple": cost_multiple,
        "weight_parity": parity["weight_parity"],
        "parity": parity,
        "benchmarks": benchmark,
        "criteria": criteria,
        "hashes": hashes,
        "journal_chain": chain,
        "execution_authority": "NONE",
        "live_orders_submitted": 0,
        "real_money_used": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", type=Path, default=ROOT / "research" / "shadow" / "journal.jsonl")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "research" / "data" / "daily")
    ap.add_argument("--output", type=Path, default=ROOT / "research" / "shadow" / "forward_report.json")
    args = ap.parse_args()
    report = build_forward_report(journal_path=args.journal, data_dir=args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "reasons": report.get("reasons", []),
        "trading_days": report.get("trading_days", 0),
        "monthly_rebalances": report.get("monthly_rebalances", 0),
        "weight_parity": report.get("weight_parity", False),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
