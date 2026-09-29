"""Fail-closed evidence gate for the preregistered Trend Harness."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


VERDICTS = {"EVIDENCE_PASS", "EVIDENCE_FAIL", "INSUFFICIENT_DATA"}


class GateInputError(ValueError):
    pass


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateInputError(f"{name} missing or non-numeric")
    value = float(value)
    if not math.isfinite(value):
        raise GateInputError(f"{name} must be finite")
    return value


def _path(obj: Mapping[str, Any], *keys: str) -> Any:
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, Mapping) or key not in cur:
            raise GateInputError("missing report field: " + ".".join(keys))
        cur = cur[key]
    return cur


def evaluate_report(report: Mapping[str, Any], criteria: Mapping[str, Any]) -> dict:
    data = report.get("data")
    if not isinstance(data, Mapping):
        return {"verdict": "EVIDENCE_FAIL", "checks": [{"name": "data_contract", "passed": False, "detail": "missing data metadata"}]}
    if not data.get("sufficient", False):
        return {
            "verdict": "INSUFFICIENT_DATA",
            "checks": [{"name": "data_sufficiency", "passed": False, "detail": list(data.get("reasons", []))}],
        }

    checks: list[dict] = []
    try:
        s2 = _path(report, "strategy", "2x")
        spy = _path(report, "benchmarks", "SPY_BUY_AND_HOLD")
        robust = _number(_path(report, "robustness_grid", "positive_fraction_2x"), "robustness_grid.positive_fraction_2x")
        boot5 = _number(_path(report, "bootstrap", "sharpe_5th_percentile_2x"), "bootstrap.sharpe_5th_percentile_2x")

        net_cagr = _number(_path(s2, "cagr"), "strategy.2x.cagr")
        max_dd = _number(_path(s2, "max_drawdown"), "strategy.2x.max_drawdown")
        sharpe = _number(_path(s2, "sharpe"), "strategy.2x.sharpe")
        calmar = _number(_path(s2, "calmar"), "strategy.2x.calmar")
        rolling = _number(_path(s2, "rolling_3y_positive_pct"), "strategy.2x.rolling_3y_positive_pct")
        spy_dd = _number(_path(spy, "max_drawdown"), "benchmark.SPY.max_drawdown")
        spy_calmar = _number(_path(spy, "calmar"), "benchmark.SPY.calmar")
        history_years = _number(_path(data, "history_years"), "data.history_years")
        valid_assets = int(_number(_path(data, "valid_asset_count"), "data.valid_asset_count"))
        required_periods_present = bool(_path(data, "required_periods_present"))
    except (GateInputError, TypeError, ValueError) as exc:
        return {
            "verdict": "EVIDENCE_FAIL",
            "checks": [{"name": "gate_input_integrity", "passed": False, "detail": str(exc)}],
        }

    insuff = dict(criteria["insufficient_data"])
    checks.extend(
        [
            {"name": "minimum_valid_assets", "passed": valid_assets >= int(insuff["minimum_valid_assets"]), "observed": valid_assets},
            {"name": "minimum_history_years", "passed": history_years >= float(insuff["minimum_history_years"]), "observed": history_years},
            {"name": "required_periods", "passed": required_periods_present, "observed": required_periods_present},
        ]
    )
    if not all(x["passed"] for x in checks):
        return {"verdict": "INSUFFICIENT_DATA", "checks": checks}

    checks.extend(
        [
            {"name": "net_cagr_positive_2x", "passed": net_cagr > 0, "observed": net_cagr, "required": "> 0"},
            {"name": "max_drawdown_absolute", "passed": max_dd <= 0.20, "observed": max_dd, "required": "<= 0.20"},
            {"name": "max_drawdown_vs_spy", "passed": max_dd <= spy_dd, "observed": max_dd, "benchmark": spy_dd},
            {"name": "net_sharpe_2x", "passed": sharpe >= 0.5, "observed": sharpe, "required": ">= 0.5"},
            {"name": "calmar_vs_spy", "passed": calmar > spy_calmar, "observed": calmar, "benchmark": spy_calmar},
            {"name": "rolling_3y_positive", "passed": rolling >= 0.60, "observed": rolling, "required": ">= 0.60"},
            {"name": "robustness_grid_positive", "passed": robust >= 0.75, "observed": robust, "required": ">= 0.75"},
            {"name": "bootstrap_sharpe_p5", "passed": boot5 > 0, "observed": boot5, "required": "> 0"},
        ]
    )
    return {
        "verdict": "EVIDENCE_PASS" if all(x["passed"] for x in checks) else "EVIDENCE_FAIL",
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--criteria", type=Path, default=Path(__file__).with_name("pass_criteria.json"))
    args = ap.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    criteria = json.loads(args.criteria.read_text(encoding="utf-8"))
    result = evaluate_report(report, criteria)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] in VERDICTS else 2


if __name__ == "__main__":
    raise SystemExit(main())
