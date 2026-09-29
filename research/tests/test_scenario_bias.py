"""WP2 scenario-bias control tests."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scenario_bias_check import FAMILIES, WARNING, run_scenario_bias  # noqa: E402


def test_scenario_bias_runs_200_seeds_per_family_and_keeps_null_adjacent():
    report = run_scenario_bias(seeds=200)
    assert report["seeds_per_family"] == 200
    assert set(report["families"]) == set(FAMILIES)
    assert report["warning"] == WARNING
    null = report["families"]["driftless_random_walk"]
    for family, item in report["families"].items():
        assert item["runs"] == 200
        assert item["synthetic_warning"] == WARNING
        assert item["driftless_null_reference"]["runs"] == 200
        assert 0 <= item["profitable_pct"] <= 1
        assert item["trade_count_mean"] >= 0
    print("SCENARIO_BIAS_REPORT=" + json.dumps(report, sort_keys=True))


def test_scenario_bias_rejects_less_than_200_seeds():
    try:
        run_scenario_bias(seeds=199)
        assert False
    except ValueError as exc:
        assert "at least 200 seeds" in str(exc)


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
    print(f"ALL PASS ({len(tests)} scenario-bias tests)")
