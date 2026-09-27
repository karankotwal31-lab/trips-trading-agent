from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from typing import Any, Dict
ROOT=Path(__file__).resolve().parent.parent
ENGINE=ROOT/"engine"; DATA=ROOT/"runtime_data"
sys.path.insert(0,str(ENGINE))
from config_guard import fingerprint_config, validate_config
from build_guard import verify_build_integrity
from store import cycle_lock, read_runtime, write_runtime, read_json, write_json
from student_engine import ensure_runtime_student, pre_trade_recall, observe_decision, student_summary
from evolution_engine import run_evolution_review

def _cfg():
    c=validate_config(json.loads((ENGINE/"config.json").read_text()))
    return c

def _market_contexts(state_doc:dict) -> list:
    out=[]
    for symbol,m in (state_doc.get("market") or {}).items():
        if not isinstance(m,dict) or m.get("status")!="OK": continue
        out.append((symbol,m))
    return out

def recall_before_cycle() -> dict:
    """Research recall snapshot. It cannot alter the frozen core's inputs or config."""
    cfg=_cfg()
    with cycle_lock():
        runtime=read_runtime(cfg["initial_equity"])
        student=ensure_runtime_student(runtime)
        prior=read_json("state.json",{},strict=False) or {}
        recalls={}
        for symbol,m in _market_contexts(prior):
            recalls[symbol]=pre_trade_recall(student,{
                "symbol":symbol,"interval":cfg["bar_interval"],
                "regime_labels":[(m.get("candle_interpretation") or {}).get("label","UNKNOWN")],
                "features_known_at_decision":m.get("features") or {},
            },limit=5)
        write_runtime(runtime)
        write_json("student_recall_latest.json",{"authority":"RESEARCH_ONLY","recalls":recalls})
        return recalls

def learn_after_cycle() -> dict:
    """Observe frozen-core outputs after they are committed; never feeds authority back into that cycle."""
    cfg=_cfg(); build=verify_build_integrity()["manifest_hash"]; ch=fingerprint_config(cfg)
    state_doc=read_json("state.json",{},strict=False) or {}
    with cycle_lock():
        runtime=read_runtime(cfg["initial_equity"])
        student=ensure_runtime_student(runtime)
        existing={e.get("episode_id") for e in student["episodes"]}
        learned=0
        for proposal in state_doc.get("proposals",[]):
            symbol=proposal.get("symbol")
            m=(state_doc.get("market") or {}).get(symbol,{})
            if not symbol or not m: continue
            truth=m.get("truth") or {}
            candle=(m.get("candle_interpretation") or {}).get("label","UNKNOWN")
            eid_seed=[f"decision:{state_doc.get('portfolio',{}).get('cycle_count')}:{symbol}:{proposal.get('signal_bar_ts')}"]
            # build once to determine id and avoid duplicate observations across retries
            obs=observe_decision(student,decision_event_ids=eid_seed,symbol=symbol,interval=cfg["bar_interval"],
                provider_provenance={"source":truth.get("source"),"source_kind":truth.get("source_kind"),
                                     "integrity_hash":truth.get("integrity_hash")},
                features_known_at_decision=m.get("features") or {},
                gate_outcomes={"forge":bool((proposal.get("forge_gate") or {}).get("passed")),
                               "constitution":bool((proposal.get("constitution_gate") or {}).get("passed")),
                               "truth_for_analysis":bool(truth.get("trusted_for_analysis")),
                               "truth_for_trade":bool(truth.get("trusted_for_trade"))},
                outcome={"status":"DECISION_OBSERVED","process_violation":False},
                costs=m.get("assumptions") or {},regime_labels=[candle],
                data_quality_flags=list(truth.get("reasons") or []) if not truth.get("trusted_for_analysis") else [],
                build_hash=build,config_hash=ch,decision_ts=proposal.get("signal_bar_ts") or state_doc.get("generated_at","UNKNOWN"))
            ep=obs["episode"]
            if ep["episode_id"] in existing:
                # remove duplicate append just created; wrapper retries must be idempotent
                student["episodes"].pop()
                student["last_event_hash"]=student["episodes"][-1]["event_hash"] if student["episodes"] else "GENESIS"
                continue
            existing.add(ep["episode_id"]); learned+=1
        write_runtime(runtime)
        summary=student_summary(student); summary["learned_this_cycle"]=learned
        write_json("student_summary.json",summary)
        return summary

def run_integrated_cycle() -> dict:
    """v0.8 wrapper: Student recall -> frozen v0.6 cycle -> Student observation."""
    verify_build_integrity()
    recalls=recall_before_cycle()
    p=subprocess.run([sys.executable,str(ENGINE/"forge_agent.py")],cwd=ROOT,text=True,capture_output=True)
    if p.returncode!=0:
        raise RuntimeError("frozen core cycle failed; Student did not override it")
    summary=learn_after_cycle()
    return {"ok":True,"student":summary,"pre_cycle_recall_symbols":sorted(recalls)}

def student_evolution_review() -> dict:
    """Run frozen Evolution then append Student-qualified shadow proposals as a separate research artifact."""
    base=run_evolution_review()
    cfg=_cfg()
    with cycle_lock():
        runtime=read_runtime(cfg["initial_equity"]); student=ensure_runtime_student(runtime)
        from student_engine import examiner,evolution_handoff
        candidates=[]
        for lesson in student["lessons"]:
            ex=examiner(lesson)
            if ex["passed"]: candidates.append(evolution_handoff(lesson,ex))
        write_runtime(runtime)
    result={"base_evolution":base,"student_challengers":candidates,"authority":"RESEARCH_ONLY",
            "champion_mutated":False,"auto_promotion_allowed":False}
    write_json("student_evolution_latest.json",result)
    return result

if __name__=="__main__":
    print(json.dumps(run_integrated_cycle(),sort_keys=True))
