"""CLI for the preregistered Trend Harness."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from research.trend.core import build_backtest_report, canonical_json, load_owner_data, sha256_file
from research.trend.gate import evaluate_report


ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.exists() and path.is_file() else None


def run_once(
    *,
    data_dir: Path,
    output: Path,
    trial_log: Path,
    generated_at: str | None = None,
) -> dict:
    here = Path(__file__).resolve().parent
    spec_path = here / "strategy_spec.json"
    universe_path = here / "universe.json"
    cost_path = here / "cost_model.json"
    criteria_path = here / "pass_criteria.json"
    spec = _read_json(spec_path)
    universe = _read_json(universe_path)
    costs = _read_json(cost_path)
    criteria = _read_json(criteria_path)

    loaded = load_owner_data(data_dir, universe)
    metadata = {
        "sufficient": bool(loaded["sufficient"]),
        "reasons": list(loaded["reasons"]),
        "valid_assets": list(loaded["valid_assets"]),
        "valid_asset_count": len(loaded["valid_assets"]),
        "invalid_assets": dict(loaded["invalid_assets"]),
        "common_start": loaded["common_start"],
        "common_end": loaded["common_end"],
        "history_years": loaded["history_years"],
        "required_periods_present": loaded["required_periods_present"],
    }
    if loaded["sufficient"]:
        report = build_backtest_report(
            loaded["data"],
            universe,
            spec,
            costs,
            data_metadata=metadata,
        )
    else:
        report = {
            "schema_version": 1,
            "strategy_id": spec["strategy_id"],
            "data": metadata,
            "strategy": {},
            "benchmarks": {},
            "robustness_grid": {},
            "bootstrap": {},
            "stability_windows_2x": [],
            "execution_audit": {"one_day_causal_lag": True, "executions": []},
        }

    gate = evaluate_report(report, criteria)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    hashes = {
        "strategy_spec": sha256_file(spec_path),
        "universe": sha256_file(universe_path),
        "cost_model": sha256_file(cost_path),
        "pass_criteria": sha256_file(criteria_path),
        "data_manifest": _hash_or_none(data_dir / "manifest.sha256"),
    }
    report["generated_at"] = timestamp
    report["hashes"] = hashes
    report["verdict"] = gate["verdict"]
    report["gate_checks"] = gate["checks"]
    body = dict(report)
    report["report_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    trial = {
        "event": "BACKTEST_TRIAL",
        "generated_at": timestamp,
        "strategy_id": spec["strategy_id"],
        "verdict": report["verdict"],
        "report_hash": report["report_hash"],
        "hashes": hashes,
        "criteria_mutated_after_result": False,
        "output": str(output),
    }
    trial_log.parent.mkdir(parents=True, exist_ok=True)
    with trial_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(trial, sort_keys=True, allow_nan=False) + "\n")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=ROOT / "research" / "data" / "daily")
    ap.add_argument("--output", type=Path, default=ROOT / "research" / "results" / "latest_backtest_report.json")
    ap.add_argument("--trial-log", type=Path, default=ROOT / "research" / "trial_log.jsonl")
    args = ap.parse_args()
    report = run_once(data_dir=args.data_dir, output=args.output, trial_log=args.trial_log)
    print(json.dumps({
        "verdict": report["verdict"],
        "report_hash": report["report_hash"],
        "data_reasons": report["data"].get("reasons", []),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
