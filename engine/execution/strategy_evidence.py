"""Fail-closed bridge from approved research evidence into execution readiness.

This module deliberately does not import research code. It independently verifies the exact
artifacts produced by the preregistered Trend Harness / forward-shadow path. Missing, stale,
malformed or hash-drifted evidence is a refusal, never an implicit pass.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

ROOT = Path(__file__).resolve().parents[2]
MAX_EVIDENCE_AGE_DAYS = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aware(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} generated_at missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} generated_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _read_report(path: Path, label: str) -> Mapping[str, Any]:
    if not path.exists():
        raise ValueError(f"{label} report missing: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{label} report is unreadable JSON") from exc
    if not isinstance(report, Mapping):
        raise ValueError(f"{label} report must be a JSON object")
    return report


def _expected_hashes(root: Path) -> Dict[str, str]:
    trend = root / "research" / "trend"
    manifest = root / "research" / "data" / "daily" / "manifest.sha256"
    required = {
        "strategy_spec": trend / "strategy_spec.json",
        "universe": trend / "universe.json",
        "cost_model": trend / "cost_model.json",
        "pass_criteria": trend / "pass_criteria.json",
        "data_manifest": manifest,
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise ValueError("strategy evidence inputs missing: " + ", ".join(missing))
    return {name: _sha256(path) for name, path in required.items()}


def strategy_evidence_status(
    *,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    max_age_days: int = MAX_EVIDENCE_AGE_DAYS,
) -> Dict[str, Any]:
    """Return a deterministic evidence verdict. Only passed=True can satisfy readiness."""
    root = Path(root or ROOT)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons = []
    backtest_path = root / "research" / "results" / "latest_backtest_report.json"
    forward_path = root / "research" / "shadow" / "forward_report.json"

    try:
        expected = _expected_hashes(root)
    except Exception as exc:
        return {
            "passed": False,
            "reasons": [str(exc)],
            "max_age_days": int(max_age_days),
            "backtest_report": str(backtest_path),
            "forward_report": str(forward_path),
        }

    try:
        backtest = _read_report(backtest_path, "backtest")
        forward = _read_report(forward_path, "forward")
    except Exception as exc:
        return {
            "passed": False,
            "reasons": [str(exc)],
            "expected_hashes": expected,
            "max_age_days": int(max_age_days),
            "backtest_report": str(backtest_path),
            "forward_report": str(forward_path),
        }

    if backtest.get("verdict") != "EVIDENCE_PASS":
        reasons.append(f"backtest verdict is {backtest.get('verdict')!r}, not EVIDENCE_PASS")
    if forward.get("verdict") != "FORWARD_PASS":
        reasons.append(f"forward verdict is {forward.get('verdict')!r}, not FORWARD_PASS")
    if forward.get("weight_parity") is not True:
        reasons.append("forward weight_parity is not true")

    for label, report in (("backtest", backtest), ("forward", forward)):
        hashes = report.get("hashes")
        if not isinstance(hashes, Mapping):
            reasons.append(f"{label} hashes missing")
        else:
            for name, expected_digest in expected.items():
                if hashes.get(name) != expected_digest:
                    reasons.append(
                        f"{label} hash mismatch for {name}: "
                        f"recorded={hashes.get(name)!r} expected={expected_digest!r}"
                    )
        try:
            generated = _aware(report.get("generated_at"), label)
        except Exception as exc:
            reasons.append(str(exc))
            continue
        age_seconds = (now - generated).total_seconds()
        if age_seconds < 0:
            reasons.append(f"{label} report generated_at is in the future")
        elif age_seconds > int(max_age_days) * 86400:
            reasons.append(f"{label} report is older than {max_age_days} days")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "expected_hashes": expected,
        "backtest_verdict": backtest.get("verdict"),
        "forward_verdict": forward.get("verdict"),
        "weight_parity": forward.get("weight_parity"),
        "max_age_days": int(max_age_days),
        "backtest_report": str(backtest_path),
        "forward_report": str(forward_path),
    }
