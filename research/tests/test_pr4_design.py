"""PR-4 design/proposal artifact tests. Research-only; no engine imports."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_supervisor_bridge_design_preserves_one_way_authority():
    text = (ROOT / "research" / "design" / "supervisor_bridge.md").read_text(encoding="utf-8").lower()
    required = (
        "asynchronous",
        "advisory",
        "halt",
        "signed acknowledgement",
        "no per-trade approvals",
        "prompt injection",
        "block_new_entries_and_escalate",
        "execution_authority = none",
        "cannot resume",
    )
    for token in required:
        assert token in text, token


def test_scope_roadmap_keeps_new_markets_analysis_only_until_own_evidence():
    text = (ROOT / "research" / "design" / "scope_and_roadmap.md").read_text(encoding="utf-8").lower()
    for token in ("commodities", "upstox", "analysis-only", "market-specific", "forward shadow",
                  "owner scope", "one market at a time"):
        assert token in text, token


def test_exact_five_class_b_proposals_have_required_artifacts():
    root = ROOT / "research" / "proposals"
    proposals = sorted(path for path in root.iterdir() if path.is_dir())
    ids = []
    for path in proposals:
        meta = json.loads((path / "proposal.json").read_text(encoding="utf-8"))
        ids.append(meta["id"])
        for name in ("README.md", "patch.diff", "owner_reapproval_checklist.md", "proposal.json"):
            assert (path / name).is_file(), f"{path.name}/{name}"
        patch = (path / "patch.diff").read_text(encoding="utf-8")
        assert "engine/approved_build.json" not in patch
        assert "engine/approved_config.sha256" not in patch
        assert "tests/approved_tests.json" not in patch
        assert "infra/approved_infra.json" not in patch
        assert "infra/core_v06.sha256" not in patch
    assert ids == ["P001", "P002", "P003", "P004", "P005"]


def test_proposal_commands_never_run_owner_approval():
    forbidden = ("approve_build.py", "approve_config.py", "approve_infra.py", "--approve")
    for meta_path in (ROOT / "research" / "proposals").glob("P*/proposal.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for command in meta["test_commands"]:
            text = " ".join(command)
            assert not any(token in text for token in forbidden), (meta["id"], text)


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
    print(f"ALL PASS ({len(tests)} PR-4 design/proposal tests)")
