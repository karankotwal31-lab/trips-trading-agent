"""WP4 evidence verifier tests."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.verify_evidence import verify_evidence  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_root(td: str, now: datetime):
    root = Path(td)
    trend = root / "research" / "trend"
    data = root / "research" / "data" / "daily"
    shadow = root / "research" / "shadow"
    results = root / "research" / "results"
    for p in (trend, data, shadow, results):
        p.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("strategy_spec.json", "{}\n"),
        ("universe.json", "{}\n"),
        ("cost_model.json", "{}\n"),
        ("pass_criteria.json", "{}\n"),
    ):
        (trend / name).write_text(body, encoding="utf-8")
    (data / "manifest.sha256").write_text("a" * 64 + "  X.csv\n", encoding="utf-8")
    hashes = {
        "strategy_spec": _sha(trend / "strategy_spec.json"),
        "universe": _sha(trend / "universe.json"),
        "cost_model": _sha(trend / "cost_model.json"),
        "pass_criteria": _sha(trend / "pass_criteria.json"),
        "data_manifest": _sha(data / "manifest.sha256"),
    }
    backtest = {
        "verdict": "EVIDENCE_PASS",
        "generated_at": now.isoformat(),
        "hashes": hashes,
    }
    forward = {
        "verdict": "FORWARD_PASS",
        "generated_at": now.isoformat(),
        "hashes": hashes,
        "weight_parity": True,
    }
    bp = results / "latest_backtest_report.json"
    fp = shadow / "forward_report.json"
    bp.write_text(json.dumps(backtest), encoding="utf-8")
    fp.write_text(json.dumps(forward), encoding="utf-8")
    return root, bp, fp


def test_verifier_passes_only_complete_fresh_hash_bound_evidence():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        root, bp, fp = _make_root(td, now)
        result = verify_evidence(root, backtest_path=bp, forward_path=fp, now=now)
        assert result["passed"], result


def test_verifier_fails_on_any_hash_drift():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        root, bp, fp = _make_root(td, now)
        (root / "research" / "trend" / "strategy_spec.json").write_text('{"changed":true}\n', encoding="utf-8")
        result = verify_evidence(root, backtest_path=bp, forward_path=fp, now=now)
        assert not result["passed"]
        assert any("hash mismatch" in x for x in result["reasons"])


def test_verifier_fails_if_forward_or_backtest_not_passed():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        root, bp, fp = _make_root(td, now)
        f = json.loads(fp.read_text())
        f["verdict"] = "FORWARD_INSUFFICIENT"
        fp.write_text(json.dumps(f), encoding="utf-8")
        result = verify_evidence(root, backtest_path=bp, forward_path=fp, now=now)
        assert not result["passed"]
        assert any("FORWARD_PASS" in x for x in result["reasons"])


def test_verifier_fails_on_stale_report_and_parity_failure():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        root, bp, fp = _make_root(td, now)
        f = json.loads(fp.read_text())
        f["generated_at"] = "2026-08-01T00:00:00+00:00"
        f["weight_parity"] = False
        fp.write_text(json.dumps(f), encoding="utf-8")
        result = verify_evidence(root, backtest_path=bp, forward_path=fp, now=now)
        assert not result["passed"]
        assert any("older than" in x for x in result["reasons"])
        assert any("weight_parity" in x for x in result["reasons"])


def test_verifier_fails_closed_when_forward_report_missing():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        root, bp, fp = _make_root(td, now)
        fp.unlink()
        result = verify_evidence(root, backtest_path=bp, forward_path=fp, now=now)
        assert not result["passed"]
        assert any("forward report missing" in x for x in result["reasons"])


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
    print(f"ALL PASS ({len(tests)} evidence-verifier tests)")
