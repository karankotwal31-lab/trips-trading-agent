"""Research historical-data bootstrap contract tests."""

from __future__ import annotations

import ast
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INGEST = ROOT / "research" / "data_ingest" / "yahoo_chart_daily.py"


def test_ingestion_stays_outside_engine_and_broker_paths():
    tree = ast.parse(INGEST.read_text(encoding="utf-8"))
    banned = {"engine", "socket", "alpaca", "upstox"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {(node.module or "").split(".", 1)[0]}
        else:
            continue
        assert not (roots & banned), roots


def test_adjusted_close_is_mandatory_and_never_substituted():
    source = INGEST.read_text(encoding="utf-8")
    assert '"adjclose"' in source
    assert '"adj_close"' in source
    assert "includeAdjustedClose" in source
    assert "Close will never be substituted" in source


def test_network_fetch_is_confined_to_research_ingestion():
    source = INGEST.read_text(encoding="utf-8")
    assert "urllib.request" in source
    assert "/v8/finance/chart/" in source
    assert "query1.finance.yahoo.com" in source
    assert "query2.finance.yahoo.com" in source
    shadow = (ROOT / "research" / "shadow" / "runner.py").read_text(encoding="utf-8")
    assert "finance.yahoo.com" not in shadow.lower()
    assert "urllib.request" not in shadow


def test_workflow_never_commits_market_data():
    source = (ROOT / ".github" / "workflows" / "research-market-data.yml").read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in source
    assert "git push" not in source
    assert "contents: read" in source
    assert "EVIDENCE_PASS" in source and "EVIDENCE_FAIL" in source and "INSUFFICIENT_DATA" in source


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
    print(f"ALL PASS ({len(tests)} data-ingest contract tests)")
