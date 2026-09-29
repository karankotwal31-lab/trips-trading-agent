"""Evidence verifier for Trip's research path.

Exit 0 only when backtest and forward evidence both pass, hashes match the committed research
artifacts/data manifest, and both reports are fresh. Nothing in engine/ imports this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORT))

from research.trend.core import sha256_file


DEFAULT_MAX_AGE_DAYS = 30


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _expected_hashes(root: Path, backtest: Mapping[str, object]) -> dict[str, str | None]:
    trend = root / "research" / "trend"
    manifest = root / "research" / "data" / "daily" / "manifest.sha256"
    return {
        "strategy_spec": sha256_file(trend / "strategy_spec.json"),
        "universe": sha256_file(trend / "universe.json"),
        "cost_model": sha256_file(trend / "cost_model.json"),
        "pass_criteria": sha256_file(trend / "pass_criteria.json"),
        "data_manifest": sha256_file(manifest) if manifest.exists() else None,
    }


def verify_evidence(
    root: Path,
    *,
    backtest_path: Path | None = None,
    forward_path: Path | None = None,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    backtest_path = backtest_path or root / "research" / "results" / "latest_backtest_report.json"
    forward_path = forward_path or root / "research" / "shadow" / "forward_report.json"
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons: list[str] = []

    if not backtest_path.exists():
        return {"passed": False, "reasons": [f"backtest report missing: {backtest_path}"]}
    if not forward_path.exists():
        return {"passed": False, "reasons": [f"forward report missing: {forward_path}"]}

    try:
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        forward = json.loads(forward_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"passed": False, "reasons": [f"report JSON unreadable: {type(exc).__name__}"]}

    if backtest.get("verdict") != "EVIDENCE_PASS":
        reasons.append(f"backtest verdict is {backtest.get('verdict')!r}, not EVIDENCE_PASS")
    if forward.get("verdict") != "FORWARD_PASS":
        reasons.append(f"forward verdict is {forward.get('verdict')!r}, not FORWARD_PASS")

    try:
        expected = _expected_hashes(root, backtest)
    except Exception as exc:
        reasons.append(f"committed evidence inputs unreadable: {type(exc).__name__}: {exc}")
        expected = {}

    for report_name, report in (("backtest", backtest), ("forward", forward)):
        hashes = report.get("hashes")
        if not isinstance(hashes, dict):
            reasons.append(f"{report_name} hashes missing")
            continue
        for key, digest in expected.items():
            if hashes.get(key) != digest:
                reasons.append(
                    f"{report_name} hash mismatch for {key}: recorded={hashes.get(key)!r} expected={digest!r}"
                )

    for report_name, report in (("backtest", backtest), ("forward", forward)):
        stamp = report.get("generated_at")
        if not isinstance(stamp, str):
            reasons.append(f"{report_name} generated_at missing")
            continue
        try:
            age = now - _parse_time(stamp)
        except Exception as exc:
            reasons.append(f"{report_name} generated_at invalid: {exc}")
            continue
        if age.total_seconds() < 0:
            reasons.append(f"{report_name} generated_at is in the future")
        elif age.total_seconds() > max_age_days * 86400:
            reasons.append(f"{report_name} is older than {max_age_days} days")

    parity = forward.get("weight_parity")
    if parity is not True:
        reasons.append("forward weight_parity is not true")

    return {"passed": not reasons, "reasons": reasons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--backtest", type=Path, default=None)
    ap.add_argument("--forward", type=Path, default=None)
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    args = ap.parse_args()
    result = verify_evidence(
        args.root,
        backtest_path=args.backtest,
        forward_path=args.forward,
        max_age_days=args.max_age_days,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
