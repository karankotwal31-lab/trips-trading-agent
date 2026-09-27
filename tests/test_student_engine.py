from __future__ import annotations
import copy, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"engine"))
from student_engine import *

def sample_episode(pnl=-100, bad=False):
    e=build_episode(decision_event_ids=["evt-1"],symbol="SPY",interval="60min",
      provider_provenance={"kind":"real","source":"test-fixture"},features_known_at_decision={"trend":"up"},
      gate_outcomes={"truth":True,"risk":True},outcome={"pnl":pnl,"process_violation":bad},
      costs={"spread_bps":10,"slippage_bps":5},regime_labels=["TREND"],
      data_quality_flags=["STALE"] if bad else [],build_hash="b",config_hash="c",decision_ts="2026-01-01T00:00:00Z")
    return e

def test_loss_is_not_automatically_mistake():
    a=post_trade_autopsy(sample_episode(-100,False))
    assert a["classification"]=="VALID_PROCESS_ADVERSE_OUTCOME"
    assert mistake_from_autopsy(sample_episode(-100,False),a) is None

def test_process_issue_becomes_mistake():
    e=sample_episode(-100,True); a=post_trade_autopsy(e)
    assert a["classification"]=="PROCESS_OR_DATA_ISSUE"
    assert mistake_from_autopsy(e,a)["status"]=="OBSERVED"

def test_recall_has_no_trade_authority():
    s=initial_student_state(); e=sample_episode(); e["autopsy_classification"]="VALID_PROCESS"
    append_student_event(s,"episodes",e)
    r=pre_trade_recall(s,{"symbol":"SPY","interval":"60min","regime_labels":["TREND"],"features_known_at_decision":{"trend":"up"}})
    assert r["authority"]=="RESEARCH_ONLY"
    assert "execute" not in r and "approve" not in r

def test_small_sample_cannot_reach_evolution():
    l=create_lesson(episode_ids=["e"],hypothesis="x",baseline="b",sample_size=12,oos=False,
                    walk_forward_windows=0,cost_sensitivity={},failure_modes=[])
    ex=examiner(l); assert not ex["passed"]
    try: evolution_handoff(l,ex); raise AssertionError("unsafe handoff")
    except ValueError: pass

def test_examined_research_can_only_create_shadow_proposal():
    l=create_lesson(episode_ids=[str(i) for i in range(100)],hypothesis="x",baseline="b",sample_size=100,oos=True,
                    walk_forward_windows=5,cost_sensitivity={"stress":"passed"},failure_modes=[])
    ex=examiner(l); assert ex["passed"]
    p=evolution_handoff(l,ex)
    assert p["shadow_only"] and p["requires_human_approval"] and not p["may_mutate_champion"]

def test_teacher_mode_quarantines_idea():
    s=initial_student_state()
    c=ingest_curriculum(s,title="idea",formal_hypothesis="test me",
      source_provenance={"source":"paper"},test_spec={"metric":"expectancy_r"})
    assert c["status"]=="QUARANTINED_HYPOTHESIS"

def test_authority_escalation_rejected():
    for bad in [
      {"authority":"EXECUTION"},
      {"execute":True},
      {"place_order":True},
      {"approve":True},
      {"mutations":{"risk":{"max_risk":1}}},
      {"mutations":{"constitution":"rewrite"}},
      {"mutations":{"provider":"fake-real"}},
    ]:
      try: assert_no_authority_escalation(bad); raise AssertionError(f"accepted {bad}")
      except ValueError: pass

def test_student_state_is_append_hash_linked():
    s=initial_student_state()
    a=append_student_event(s,"episodes",sample_episode())
    b=append_student_event(s,"lessons",{"type":"LESSON","lesson_id":"l"})
    assert a["prev_hash"]=="GENESIS" and b["prev_hash"]==a["event_hash"]

def test_student_memory_tamper_is_detected():
    s=initial_student_state()
    append_student_event(s,"episodes",sample_episode())
    s["episodes"][0]["symbol"]="TAMPERED"
    try: validate_student_state(s); raise AssertionError("tampered student memory accepted")
    except ValueError: pass

def test_nonfinite_or_noninteger_sample_size_rejected():
    for bad in [float("nan"),float("inf"),-float("inf"),1.5,-1,True]:
        try:
            create_lesson(episode_ids=["e"],hypothesis="x",baseline="b",sample_size=bad,oos=True,
                          walk_forward_windows=5,cost_sensitivity={},failure_modes=[])
            raise AssertionError(f"bad sample size accepted: {bad!r}")
        except ValueError: pass

def main():
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests: t(); print("PASS",t.__name__)
    print(f"ALL PASS ({len(tests)} student tests)")
if __name__=="__main__": main()
