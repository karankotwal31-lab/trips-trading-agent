"""WP3 shadow-runner tests: journal integrity, stale fail-closed, parity, D3 and isolation."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import random
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.shadow.journal import append_event, load_events, verify_file  # noqa: E402
from research.shadow.runner import heartbeat_health, run_shadow_once  # noqa: E402
from research.shadow.shadow_report import build_forward_report  # noqa: E402
from research.trend.core import DailyBar, target_weights  # noqa: E402


def weekdays(start: date, n: int) -> list[date]:
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _write_root(root: Path, dates: list[date], *, lookback: int = 5, vol_window: int = 3) -> Path:
    trend = root / "research" / "trend"
    data_dir = root / "research" / "data" / "daily"
    trend.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    strategy = {
        "signal": {"lookback_days": lookback},
        "volatility": {"window_days": vol_window, "target_portfolio_volatility": 0.10},
        "sizing": {"per_asset_cap": 0.60, "per_class_cap": 1.0, "gross_cap": 1.0},
    }
    universe = {
        "classes": {"equity": ["SPY"], "bonds": ["IEF"]},
        "minimum_valid_assets": 2,
        "minimum_history_years": 0,
        "required_periods": [],
    }
    costs = {"base_cost_bps": {"equity": 3, "bonds": 5}}
    for name, obj in (
        ("strategy_spec.json", strategy),
        ("universe.json", universe),
        ("cost_model.json", costs),
        ("pass_criteria.json", {"schema_version": 1}),
    ):
        (trend / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    manifest = []
    for j, symbol in enumerate(("SPY", "IEF")):
        rng = random.Random(10 + j)
        px = 100.0 + 10 * j
        rows = ["date,open,high,low,close,adj_close,volume"]
        for i, d in enumerate(dates):
            ret = 0.001 + 0.002 * math.sin(i / 5 + j) + rng.gauss(0, 0.001)
            close = max(1.0, px * (1 + ret))
            high = max(px, close) * 1.001
            low = min(px, close) * 0.999
            rows.append(f"{d.isoformat()},{px:.8f},{high:.8f},{low:.8f},{close:.8f},{close:.8f},1000000")
            px = close
        p = data_dir / f"{symbol}.csv"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        manifest.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {symbol}.csv")
    (data_dir / "manifest.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return data_dir


def _rewrite_data_until(data_dir: Path, cutoff: date) -> None:
    manifest = []
    for p in sorted(data_dir.glob("*.csv")):
        lines = p.read_text(encoding="utf-8").splitlines()
        header, body = lines[0], lines[1:]
        kept = [row for row in body if date.fromisoformat(row.split(",", 1)[0]) <= cutoff]
        p.write_text(header + "\n" + "\n".join(kept) + "\n", encoding="utf-8")
        manifest.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    (data_dir / "manifest.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")


def _rows_from_csv(path: Path) -> list[DailyBar]:
    import csv
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(DailyBar(
                date.fromisoformat(row["date"]),
                float(row["open"]), float(row["high"]), float(row["low"]),
                float(row["close"]), float(row["adj_close"]), float(row["volume"]),
            ))
    return rows


def test_journal_chain_and_tamper_detection():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "journal.jsonl"
        append_event(p, {"kind": "A", "value": 1})
        append_event(p, {"kind": "B", "value": 2})
        assert verify_file(p)["valid"]
        lines = p.read_text().splitlines()
        first = json.loads(lines[0])
        first["value"] = 999
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        p.write_text("\n".join(lines) + "\n")
        status = verify_file(p)
        assert not status["valid"]
        assert "event_hash mismatch" in status["reason"]


def test_stale_data_logs_no_entry_and_never_state():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dates = weekdays(date(2026, 1, 5), 12)
        data_dir = _write_root(root, dates)
        journal = root / "state" / "journal.jsonl"
        heartbeat = root / "state" / "heartbeat.json"
        attempted = datetime(2026, 2, 10, 20, tzinfo=timezone.utc)
        result = run_shadow_once(
            data_dir=data_dir, journal_path=journal, heartbeat_path=heartbeat,
            root=root, attempted_at=attempted, stale_trading_days=3,
        )
        assert result["status"] == "NO_ENTRY_STALE_DATA"
        events = load_events(journal)
        assert [e["kind"] for e in events] == ["NO_ENTRY"]
        assert events[0]["execution_authority"] == "NONE"


def test_causal_month_transition_and_weight_parity_with_harness():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dates = weekdays(date(2026, 1, 20), 15)
        full_dir = _write_root(root, dates, lookback=5, vol_window=3)
        jan_dates = [d for d in dates if d.month == 1]
        first_feb = next(d for d in dates if d.month == 2)
        jan_last = jan_dates[-1]

        # First daily run sees only January.
        backup = {p.name: p.read_text() for p in full_dir.glob("*.csv")}
        _rewrite_data_until(full_dir, jan_last)
        journal = root / "state" / "journal.jsonl"
        heartbeat = root / "state" / "heartbeat.json"
        run_shadow_once(
            data_dir=full_dir, journal_path=journal, heartbeat_path=heartbeat, root=root,
            attempted_at=datetime.combine(jan_last, datetime.min.time(), tzinfo=timezone.utc),
            stale_trading_days=3,
        )

        # Restore full files, then trim through the first February close.
        for name, text in backup.items():
            (full_dir / name).write_text(text)
        _rewrite_data_until(full_dir, first_feb)
        run_shadow_once(
            data_dir=full_dir, journal_path=journal, heartbeat_path=heartbeat, root=root,
            attempted_at=datetime.combine(first_feb, datetime.min.time(), tzinfo=timezone.utc),
            stale_trading_days=3,
        )
        events = load_events(journal)
        decisions = [e for e in events if e["kind"] == "DECISION"]
        rebalances = [e for e in events if e["kind"] == "HYPOTHETICAL_REBALANCE"]
        assert len(decisions) == 1 and len(rebalances) == 1
        decision = decisions[0]
        assert decision["decision_market_date"] == jan_last.isoformat()
        assert decision["execution_market_date"] == first_feb.isoformat()
        assert date.fromisoformat(decision["execution_market_date"]) > date.fromisoformat(decision["decision_market_date"])
        assert rebalances[0]["broker_contacted"] is False
        assert rebalances[0]["order_submitted"] is False

        data = {"SPY": _rows_from_csv(full_dir / "SPY.csv"), "IEF": _rows_from_csv(full_dir / "IEF.csv")}
        expected = target_weights(
            data, {"SPY": "equity", "IEF": "bonds"}, as_of=jan_last,
            lookback=5, vol_window=3, target_vol=0.10,
            per_asset_cap=0.60, per_class_cap=1.0, gross_cap=1.0,
        )
        assert decision["target_weights"] == expected


def test_heartbeat_missed_run_detection():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "heartbeat.json"
        p.write_text(json.dumps({
            "last_attempt_at": "2026-09-21T23:45:00+00:00",
            "last_success_at": "2026-09-21T23:45:00+00:00",
            "status": "PROCESSED",
        }))
        result = heartbeat_health(
            p,
            now=datetime(2026, 9, 25, 23, 45, tzinfo=timezone.utc),
            max_business_days=2,
        )
        assert not result["healthy"]
        assert result["reason"] == "MISSED_SHADOW_RUN"


def test_forward_report_insufficient_before_d3_sample():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dates = weekdays(date(2026, 1, 5), 20)
        data_dir = _write_root(root, dates)
        journal = root / "state" / "journal.jsonl"
        append_event(journal, {
            "kind": "STATE", "market_date": dates[0].isoformat(), "equity": 1.0,
            "weights": {"SPY": 0.0, "IEF": 0.0}, "cash_weight": 1.0,
            "observed_days": 1, "rebalance_count": 0, "cumulative_cost_fraction": 0.0,
            "modeled_cost_fraction": 0.0, "max_missing_trading_days": 0,
            "execution_authority": "NONE",
        })
        append_event(journal, {
            "kind": "STATE", "market_date": dates[1].isoformat(), "equity": 1.001,
            "weights": {"SPY": 0.0, "IEF": 0.0}, "cash_weight": 1.0,
            "observed_days": 2, "rebalance_count": 0, "cumulative_cost_fraction": 0.0,
            "modeled_cost_fraction": 0.0, "max_missing_trading_days": 0,
            "execution_authority": "NONE",
        })
        report = build_forward_report(
            journal_path=journal, data_dir=data_dir, root=root,
            generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        assert report["verdict"] == "FORWARD_INSUFFICIENT"
        assert report["weight_parity"] is False


def test_forward_report_can_pass_only_when_all_d3_conditions_are_present():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dates = weekdays(date(2025, 1, 2), 140)
        data_dir = _write_root(root, dates, lookback=5, vol_window=3)
        journal = root / "state" / "journal.jsonl"
        data = {"SPY": _rows_from_csv(data_dir / "SPY.csv"), "IEF": _rows_from_csv(data_dir / "IEF.csv")}
        decision_indices = [20, 40, 60, 80, 100, 120]
        rebalance_count = 0
        for i, d in enumerate(dates[:130]):
            if i in decision_indices:
                target = target_weights(
                    data, {"SPY": "equity", "IEF": "bonds"}, as_of=d,
                    lookback=5, vol_window=3, target_vol=0.10,
                    per_asset_cap=0.60, per_class_cap=1.0, gross_cap=1.0,
                )
                decision = append_event(journal, {
                    "kind": "DECISION",
                    "decision_market_date": d.isoformat(),
                    "execution_market_date": dates[i + 1].isoformat(),
                    "target_weights": target,
                    "execution_authority": "NONE",
                })
                append_event(journal, {
                    "kind": "HYPOTHETICAL_REBALANCE",
                    "decision_event_hash": decision["event_hash"],
                    "decision_market_date": d.isoformat(),
                    "execution_market_date": dates[i + 1].isoformat(),
                    "cost_fraction": 0.0001,
                    "execution_authority": "NONE",
                    "broker_contacted": False,
                    "order_submitted": False,
                })
                rebalance_count += 1
            append_event(journal, {
                "kind": "STATE",
                "market_date": d.isoformat(),
                "equity": 1.0 + i * 0.001,
                "weights": {"SPY": 0.0, "IEF": 0.0},
                "cash_weight": 1.0,
                "observed_days": i + 1,
                "rebalance_count": rebalance_count,
                "cumulative_cost_fraction": 0.0001 * rebalance_count,
                "modeled_cost_fraction": 0.0001 * rebalance_count,
                "max_missing_trading_days": 0,
                "execution_authority": "NONE",
            })
        report = build_forward_report(
            journal_path=journal, data_dir=data_dir, root=root,
            generated_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
        )
        assert report["verdict"] == "FORWARD_PASS", report
        assert report["trading_days"] == 130
        assert report["monthly_rebalances"] == 6
        assert report["weight_parity"] is True
        assert report["forward_cost_drag_multiple"] == 1.0


def test_shadow_python_has_no_network_broker_or_engine_imports():
    banned_roots = {"engine", "socket", "urllib", "http", "requests", "aiohttp"}
    for path in (ROOT / "research" / "shadow").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".", 1)[0]]
            else:
                continue
            assert not (set(names) & banned_roots), f"forbidden import in {path}: {names}"
        text = path.read_text(encoding="utf-8").lower()
        assert "import engine" not in text
        assert "from engine" not in text


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
    print(f"ALL PASS ({len(tests)} shadow-runner tests)")
