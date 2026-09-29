"""WP5 documentation truth-pass regression tests."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

D1 = (
    "Current enforced state: mode=paper; live transmission locked "
    "(LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. "
    "The execution layer is designed live-money-only with no simulated-broker stage; forward "
    "evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN."
)

MARKDOWN_TARGETS = (
    "README.md",
    "HARDENING_REPORT.md",
    "INFRASTRUCTURE_STATUS.md",
    "NEON_DRY_RUN_REPORT.md",
    "NEON_PRODUCTION_STATUS.md",
    "STUDENT_ENGINE_ARCHITECTURE.md",
    "STUDENT_HARD_STRESS_AUDIT.md",
    "STUDENT_INTEGRATION_STATUS.md",
    "STUDENT_V08_INTEGRATED_HARD_TEST.md",
    "infra/README.md",
)


def test_d1_is_verbatim_in_every_wp5_markdown_target():
    for rel in MARKDOWN_TARGETS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert D1 in text, f"D1 missing or changed in {rel}"


def test_discrepancy_inventory_covers_required_protected_sources_and_p004():
    text = (ROOT / "research" / "DOC_DISCREPANCIES.md").read_text(encoding="utf-8")
    required = (
        "engine/capability_registry.json",
        "engine/config.json",
        "docs/TRIPS_CONSTITUTION.md",
        "engine/execution/lifecycle.py",
        "engine/execution/status.py",
        "engine/execution/readiness.py",
        ".github/workflows/trips-agent.yml",
        ".github/workflows/trips-live-readiness.yml",
        "P004",
    )
    for token in required:
        assert token in text, f"missing discrepancy inventory token: {token}"


def test_current_truth_replaces_unqualified_false_present_tense_claims():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    infra_status = (ROOT / "INFRASTRUCTURE_STATUS.md").read_text(encoding="utf-8")
    student = (ROOT / "STUDENT_INTEGRATION_STATUS.md").read_text(encoding="utf-8")
    hard = (ROOT / "STUDENT_HARD_STRESS_AUDIT.md").read_text(encoding="utf-8")

    assert "\nThere is **no live-money execution path**." not in readme
    assert "\nTrip's is a **paper-only** market-analysis" not in readme
    assert "\nNo live-money broker path is pending or enabled." not in infra_status
    assert "\n- No live-money execution exists or is authorized." not in student
    assert "\nPaper-only authority remains unchanged." not in hard


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
    print(f"ALL PASS ({len(tests)} docs-truth tests)")
