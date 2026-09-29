"""WP1 Trend Harness tests: causality, validation, controls, isolation and determinism."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.trend.core import (  # noqa: E402
    DailyBar,
    DataValidationError,
    bootstrap_sharpes,
    canonical_json,
    dataset_sufficiency,
    load_owner_data,
    metrics_from_equity,
    parse_csv,
    simulate_allocations,
    simulate_strategy,
    target_weights,
)
from research.trend.gate import evaluate_report  # noqa: E402


def weekdays(start: date, n: int) -> list[date]:
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_data(symbols=("SPY", "IEF", "QQQ"), n=900, seed=1, drift=0.0004):
    dates = weekdays(date(2000, 1, 3), n)
    out = {}
    for j, symbol in enumerate(symbols):
        rng = random.Random(seed + j * 101)
        px = 80.0 + j * 10
        rows = []
        for i, d in enumerate(dates):
            ret = drift + 0.002 * math.sin(i / 13.0 + j) + rng.gauss(0, 0.004)
            close = max(1.0, px * (1 + ret))
            hi = max(px, close) * 1.001
            lo = min(px, close) * 0.999
            rows.append(DailyBar(d, px, hi, lo, close, close, 1_000_000.0))
            px = close
        out[symbol] = rows
    return out


def small_spec():
    return {
        "signal": {"lookback_days": 60},
        "volatility": {"window_days": 30, "target_portfolio_volatility": 0.10},
        "sizing": {"per_asset_cap": 0.20, "per_class_cap": 0.40, "gross_cap": 1.0},
    }


def costs():
    return {"base_cost_bps": {"equity": 3, "bonds": 5}}


def classes(symbols):
    return {s: ("bonds" if s == "IEF" else "equity") for s in symbols}


def test_target_weights_have_no_lookahead():
    data = make_data(n=500)
    cls = classes(data)
    as_of = data["SPY"][350].date
    before = target_weights(data, cls, as_of=as_of, lookback=60, vol_window=30)
    mutated = {s: list(rows) for s, rows in data.items()}
    last = mutated["SPY"][-1]
    mutated["SPY"][-1] = DailyBar(last.date, last.open, last.high * 20, last.low, last.close * 15, last.adj_close * 15, last.volume)
    after = target_weights(mutated, cls, as_of=as_of, lookback=60, vol_window=30)
    assert before == after


def test_month_end_signal_executes_only_next_available_close():
    data = make_data(n=120)
    cls = classes(data)
    fixed = {s: 0.2 for s in data}
    sim = simulate_allocations(data, cls, costs(), cost_multiplier=2.0, target_fn=lambda _: fixed)
    assert sim["execution_log"]
    for event in sim["execution_log"]:
        assert date.fromisoformat(event["execution_date"]) > date.fromisoformat(event["decision_date"])


def test_costs_are_monotone():
    data = make_data(n=800, drift=0.0007)
    cls = classes(data)
    spec = small_spec()
    endings = [
        simulate_strategy(data, cls, spec, costs(), cost_multiplier=m)["ending_equity"]
        for m in (1.0, 2.0, 4.0)
    ]
    assert endings[0] >= endings[1] >= endings[2]


def test_known_answer_max_drawdown():
    ds = [date(2020, 1, 1) + timedelta(days=i) for i in range(4)]
    m = metrics_from_equity(ds, [1.0, 1.2, 0.9, 1.1])
    assert abs(m["max_drawdown"] - 0.25) < 1e-12


def test_weight_constraints():
    symbols = tuple(f"S{i:02d}" for i in range(12))
    data = make_data(symbols=symbols, n=180, seed=4, drift=0.001)
    cls = {s: ("A" if i < 6 else "B") for i, s in enumerate(symbols)}
    w = target_weights(
        data, cls, lookback=60, vol_window=30,
        target_vol=0.10, per_asset_cap=0.20, per_class_cap=0.40, gross_cap=1.0,
    )
    assert all(0 <= x <= 0.20 + 1e-12 for x in w.values())
    assert sum(w.values()) <= 1.0 + 1e-12
    for group in ("A", "B"):
        assert sum(w[s] for s in w if cls[s] == group) <= 0.40 + 1e-12


def _write_csv(path: Path, rows: list[str], header="date,open,high,low,close,adj_close,volume"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_csv_validation_rules():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "X.csv"
        good = [
            "2020-01-02,10,11,9,10.5,10.5,100",
            "2020-01-03,10.5,11,10,10.8,10.8,110",
        ]
        _write_csv(p, good)
        assert len(parse_csv(p)) == 2

        _write_csv(p, good, header="date,open,high,low,close,volume")
        try:
            parse_csv(p); assert False
        except DataValidationError:
            pass

        _write_csv(p, [good[0], good[0]])
        try:
            parse_csv(p); assert False
        except DataValidationError:
            pass

        _write_csv(p, ["2020-01-02,10,nan,9,10,10,100"])
        try:
            parse_csv(p); assert False
        except DataValidationError:
            pass

        _write_csv(p, ["2020-01-02,10,9,8,10,10,100"])
        try:
            parse_csv(p); assert False
        except DataValidationError:
            pass

        _write_csv(p, ["2020-01-02,10,11,9,10,10,-1"])
        try:
            parse_csv(p); assert False
        except DataValidationError:
            pass


def test_manifest_mismatch_fails_asset_closed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_csv(root / "SPY.csv", ["2020-01-02,10,11,9,10,10,100"])
        (root / "manifest.sha256").write_text("0" * 64 + "  SPY.csv\n", encoding="utf-8")
        universe = {"classes": {"equity": ["SPY"]}, "minimum_valid_assets": 1, "minimum_history_years": 0, "required_periods": []}
        loaded = load_owner_data(root, universe)
        assert not loaded["sufficient"]
        assert "SHA-256 mismatch" in loaded["invalid_assets"]["SPY"]


def test_sufficiency_rules():
    data = make_data(symbols=("A", "B"), n=100)
    x = dataset_sufficiency(data, minimum_valid_assets=10, minimum_history_years=15, required_periods=["2008-01-01/2009-12-31", "2022-01-01/2022-12-31"])
    assert not x["sufficient"]
    assert any("valid assets" in r for r in x["reasons"])
    assert any("common history" in r for r in x["reasons"])
    assert any("required period" in r for r in x["reasons"])


def _criteria():
    return {
        "insufficient_data": {
            "minimum_valid_assets": 10,
            "minimum_history_years": 15,
            "must_cover": ["2008-01-01/2009-12-31", "2022-01-01/2022-12-31"],
        }
    }


def _base_report(strategy_metrics: dict, spy_metrics: dict, robust: float, boot: float):
    return {
        "data": {"sufficient": True, "reasons": [], "valid_asset_count": 10, "history_years": 16.0, "required_periods_present": True},
        "strategy": {"2x": strategy_metrics},
        "benchmarks": {"SPY_BUY_AND_HOLD": spy_metrics},
        "robustness_grid": {"positive_fraction_2x": robust},
        "bootstrap": {"sharpe_5th_percentile_2x": boot},
    }


def test_positive_control_gate_passes():
    s = {"cagr": 0.12, "max_drawdown": 0.10, "sharpe": 0.9, "calmar": 1.2, "rolling_3y_positive_pct": 0.8}
    spy = {"max_drawdown": 0.20, "calmar": 0.5}
    assert evaluate_report(_base_report(s, spy, 0.9, 0.2), _criteria())["verdict"] == "EVIDENCE_PASS"


def test_driftless_random_walk_null_gate_does_not_pass():
    rng = random.Random(20260930)
    ds = weekdays(date(2000, 1, 3), 4200)
    eq = [1.0]
    rets = []
    for _ in range(len(ds) - 1):
        r = rng.gauss(0, 0.01)
        rets.append(r)
        eq.append(eq[-1] * max(0.01, 1 + r))
    m = metrics_from_equity(ds, eq)
    b = bootstrap_sharpes(rets, seed=9, block_length=21, replications=200)
    s = {"cagr": m["cagr"], "max_drawdown": m["max_drawdown"], "sharpe": m["sharpe"], "calmar": m["calmar"], "rolling_3y_positive_pct": m["rolling_3y_positive_pct"]}
    spy = {"max_drawdown": m["max_drawdown"], "calmar": m["calmar"]}
    report = _base_report(s, spy, 0.5, sorted(b)[9])
    assert evaluate_report(report, _criteria())["verdict"] != "EVIDENCE_PASS"


def test_gate_fails_closed_on_missing_or_nonfinite_metric():
    good = {"cagr": 0.1, "max_drawdown": 0.1, "sharpe": 0.8, "calmar": 1.0, "rolling_3y_positive_pct": 0.8}
    spy = {"max_drawdown": 0.2, "calmar": 0.5}
    missing = dict(good); del missing["sharpe"]
    assert evaluate_report(_base_report(missing, spy, 0.9, 0.1), _criteria())["verdict"] == "EVIDENCE_FAIL"
    bad = dict(good); bad["sharpe"] = float("nan")
    assert evaluate_report(_base_report(bad, spy, 0.9, 0.1), _criteria())["verdict"] == "EVIDENCE_FAIL"


def test_harness_is_byte_deterministic():
    data = make_data(n=700, seed=77)
    cls = classes(data)
    a = simulate_strategy(data, cls, small_spec(), costs(), cost_multiplier=2.0)
    b = simulate_strategy(data, cls, small_spec(), costs(), cost_multiplier=2.0)
    assert canonical_json(a) == canonical_json(b)


def test_research_python_isolation_static_check():
    forbidden = re.compile(r"^\s*(?:from|import)\s+(?:engine\b|forge_agent\b|store\b|execution\b)", re.M)
    for path in (ROOT / "research").rglob("*.py"):
        if path.name == "test_ed25519_vectors.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), f"forbidden engine/runtime import in {path.relative_to(ROOT)}"


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failed = []
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception:
            failed.append(test.__name__)
            print("FAIL", test.__name__)
            traceback.print_exc()
    if failed:
        raise SystemExit(f"{len(failed)} failures: {failed}")
    print(f"ALL PASS ({len(tests)} trend-harness tests)")
