from __future__ import annotations
import hashlib,json,sys,tempfile,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/"engine"
sys.path.insert(0,str(ENGINE))
from student_engine import *

def test_integral_wiring_exists():
    wrapper=(ENGINE/"student_orchestrator.py").read_text()
    for token in ("ensure_runtime_student","pre_trade_recall","observe_decision","student_summary","forge_agent.py"):
        assert token in wrapper
    assert "get_student_summary" in (ROOT/"infra/neon_schema_student_v08.sql").read_text()

def test_recall_cannot_change_gate_inputs():
    wrapper=(ENGINE/"student_orchestrator.py").read_text()
    forge=(ENGINE/"forge_agent.py").read_text()
    assert "student_engine" not in forge
    assert "pre_trade_recall" in wrapper
    assert "subprocess.run([sys.executable,str(ENGINE/\"forge_agent.py\")]" in wrapper

def test_student_observation_occurs_after_frozen_core():
    wrapper=(ENGINE/"student_orchestrator.py").read_text()
    assert wrapper.index("subprocess.run([sys.executable,str(ENGINE/\"forge_agent.py\")]") < wrapper.index("summary=learn_after_cycle()")

def test_student_is_atomic_runtime_child():
    r={"student":initial_student_state()}
    s=ensure_runtime_student(r)
    append_student_event(s,"curriculum",{"type":"CURRICULUM","title":"x"})
    assert r["student"]["curriculum"][0]["title"]=="x"

def test_neon_student_schema_is_read_only_observability():
    sql=(ROOT/"infra/neon_schema_student_v08.sql").read_text().lower()
    assert "get_student_summary" in sql
    assert "grant execute" in sql
    assert "insert into" not in sql and "update trips_cloud.runtime" not in sql

def test_student_authority_remains_research_only():
    s=initial_student_state()
    assert s["authority"]=="RESEARCH_ONLY"
    try:
        assert_no_authority_escalation({"authority":"EXECUTION"})
        raise AssertionError("authority escalation accepted")
    except ValueError: pass

def test_frozen_core_unchanged():
    for line in (ROOT/"infra/core_v06.sha256").read_text().splitlines():
        digest,rel=line.split(maxsplit=1)
        assert hashlib.sha256((ROOT/rel.strip()).read_bytes()).hexdigest()==digest

def main():
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests: t(); print("PASS",t.__name__)
    print(f"ALL PASS ({len(tests)} integration tests)")
if __name__=="__main__": main()
