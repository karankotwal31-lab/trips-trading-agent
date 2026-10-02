"""Research historical-data bootstrap contract tests."""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research.data_ingest.restore_shadow_checkpoint import (
    CheckpointError, restore_archive, select_artifact, select_run,
)
from research.shadow.journal import append_event, load_events
from research.data_ingest.yahoo_chart_daily import completed_us_session, _extract
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


def test_checkpoint_finds_history_beyond_first_page_and_ignores_pr_main():
    def run(number, branch, event="schedule"):
        return dict(id=number, head_branch=branch, event=event, conclusion="success")
    pages = [{"workflow_runs": [run(50, "other"), run(49, "main", "pull_request")]},
             {"workflow_runs": [run(10, "main"), run(11, "main")]}]
    assert select_run(pages, "main", 50, "schedule")["id"] == 11
    assert select_run(pages, "main", 49, "pull_request") is None


def test_checkpoint_missing_or_expired_artifact_is_a_hard_failure():
    for pages in ([{"artifacts": []}], [{"artifacts": [dict(
            name="trips-historical-evidence", expired=True, id=1)]}]):
        try:
            select_artifact(pages)
        except CheckpointError:
            pass
        else:
            raise AssertionError("unrecoverable history must never silently restart")


def test_checkpoint_verifies_chain_and_extracts_only_journal():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source.jsonl"
        append_event(source, {"kind": "STATE", "execution_authority": "NONE",
                              "market_date": "2026-10-01", "attempted_at": "2026-10-02T02:30:00+00:00"})
        archive = root / "prior.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("shadow/journal.jsonl", source.read_bytes())
            z.writestr("shadow/../../escape.txt", "must not extract")
            z.writestr("shadow/heartbeat.json", "stale")
        target = root / "restored"
        assert restore_archive(archive, target)["valid"]
        assert (target / "journal.jsonl").read_bytes() == source.read_bytes()
        assert list(target.iterdir()) == [target / "journal.jsonl"]
        assert not (root / "escape.txt").exists()


def test_checkpoint_rejects_corrupt_empty_missing_and_duplicate_journals():
    for entries in ([b'{"seq":1}\n'], [b""], [], [b"{}", b"{}"]):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "prior.zip"
            with zipfile.ZipFile(archive, "w") as z:
                for content in entries:
                    z.writestr("shadow/journal.jsonl", content)
            target = root / "restored"
            try:
                restore_archive(archive, target)
            except CheckpointError:
                pass
            else:
                raise AssertionError("invalid checkpoint accepted")
            assert not (target / "journal.jsonl").exists()


def test_checkpoint_never_overwrites_existing_history():
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp)
        journal = target / "journal.jsonl"
        journal.write_text("existing history")
        try:
            restore_archive(target / "missing.zip", target)
        except CheckpointError:
            pass
        else:
            raise AssertionError("existing journal overwritten")
        assert journal.read_text() == "existing history"


def test_session_cutoff_handles_dst_and_never_accepts_future_dates():
    for day, before, after in (
        (date(2026, 10, 2), "2026-10-02T20:59:59+00:00", "2026-10-02T21:00:00+00:00"),
        (date(2026, 12, 2), "2026-12-02T21:59:59+00:00", "2026-12-02T22:00:00+00:00"),
    ):
        assert not completed_us_session(day, datetime.fromisoformat(before))
        assert completed_us_session(day, datetime.fromisoformat(after))
    assert not completed_us_session(date(2026, 10, 3), datetime.fromisoformat("2026-10-02T23:00:00+00:00"))


def test_ingestion_excludes_live_daily_bar_even_when_all_values_present():
    stamps = [int(datetime(2026, 10, d, 13, 30, tzinfo=timezone.utc).timestamp()) for d in (1, 2)]
    payload = {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": -14400}, "timestamp": stamps,
        "indicators": {"quote": [{"open": [100, 100], "high": [102, 102],
            "low": [99, 99], "close": [101, 101], "volume": [1000, 1000]}],
            "adjclose": [{"adjclose": [101, 101]}]},
    }]}}
    before = _extract("SPY", payload, observed_at=datetime.fromisoformat("2026-10-02T16:16:00+00:00"))
    after = _extract("SPY", payload, observed_at=datetime.fromisoformat("2026-10-02T21:00:00+00:00"))
    assert [r["date"] for r in before] == ["2026-10-01"]
    assert [r["date"] for r in after] == ["2026-10-01", "2026-10-02"]


def test_checkpoint_quarantines_unclosed_session_without_rewriting_source():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source.jsonl"
        completed = append_event(source, {"kind": "STATE", "market_date": "2026-10-01",
                              "attempted_at": "2026-10-02T02:30:00+00:00", "execution_authority": "NONE"})
        unclosed = append_event(source, {"kind": "STATE", "market_date": "2026-10-02",
                              "attempted_at": "2026-10-02T16:16:58+00:00", "execution_authority": "NONE"})
        archive = root / "prior.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("shadow/journal.jsonl", source.read_bytes())
        original = archive.read_bytes()
        restored = root / "restored"
        chain = restore_archive(archive, restored)
        assert chain["valid"] and chain["quarantined_events"] == 1
        events = load_events(restored / "journal.jsonl")
        assert [e for e in events if e["kind"] == "STATE"] == [completed]
        assert events[-1]["kind"] == "QUARANTINED_EVENT"
        assert events[-1]["original_event"] == unclosed
        assert chain["original_last_hash"] == unclosed["event_hash"]
        assert archive.read_bytes() == original


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
