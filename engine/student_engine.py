from __future__ import annotations
import hashlib, json, math, statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

STUDENT_SCHEMA_VERSION = 1
STUDENT_AUTHORITY = "RESEARCH_ONLY"
FORBIDDEN_MUTATIONS = frozenset({
    "constitution","risk","truth","provider","provider_source_kind","symbols",
    "instrument_scope","mode","execution","champion","supervisor_delivery",
    "escalation_acknowledgement"
})

def _canon(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()

def _hash(x: Any) -> str:
    return hashlib.sha256(_canon(x)).hexdigest()

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def evidence_grade(sample_size:int, oos:bool=False, walk_forward_windows:int=0,
                   provenance_ok:bool=True, leakage_free:bool=True) -> str:
    if not provenance_ok or not leakage_free:
        return "INVALID"
    if sample_size < 20:
        return "INCONCLUSIVE"
    if sample_size < 100 or not oos or walk_forward_windows < 5:
        return "PRELIMINARY"
    return "TESTABLE"

def initial_student_state() -> Dict[str, Any]:
    return {
        "student_schema_version": STUDENT_SCHEMA_VERSION,
        "authority": STUDENT_AUTHORITY,
        "episodes": [], "lessons": [], "mistakes": [], "experiments": [],
        "curriculum": [], "last_event_hash": "GENESIS",
    }

def verify_student_chain(s: dict) -> bool:
    prev="GENESIS"
    ordered=[]
    for k in ("episodes","lessons","mistakes","experiments","curriculum"):
        ordered.extend(s.get(k,[]))
    # Events are distributed across collections, so reconstruct append order by chain linkage.
    remaining={e.get("event_hash"):e for e in ordered if isinstance(e,dict) and e.get("event_hash")}
    seen=set()
    while remaining:
        nxt=None
        for h,e in remaining.items():
            if e.get("prev_hash")==prev:
                nxt=(h,e); break
        if nxt is None: return False
        h,e=nxt
        body={k:v for k,v in e.items() if k not in {"student_authority","prev_hash","event_hash"}}
        # append_student_event hashes the caller body before adding envelope fields.
        expected=hashlib.sha256(prev.encode()+_canon(body)).hexdigest()
        if expected!=h or e.get("student_authority")!=STUDENT_AUTHORITY: return False
        seen.add(h); prev=h; del remaining[h]
    return s.get("last_event_hash","GENESIS")==prev

def validate_student_state(s: dict) -> None:
    if not isinstance(s,dict) or s.get("student_schema_version") != STUDENT_SCHEMA_VERSION:
        raise ValueError("invalid student schema")
    if s.get("authority") != STUDENT_AUTHORITY:
        raise ValueError("student authority escalation rejected")
    for k in ("episodes","lessons","mistakes","experiments","curriculum"):
        if not isinstance(s.get(k),list):
            raise ValueError(f"student {k} must be list")
    total=sum(len(s[k]) for k in ("episodes","lessons","mistakes","experiments","curriculum"))
    if total and not verify_student_chain(s):
        raise ValueError("student memory hash chain verification failed")

def append_student_event(state:dict, collection:str, body:dict) -> dict:
    # O(1) append path: structural/authority checks + chain-tip extension.
    # Full-chain verification remains in validate_student_state at trust boundaries.
    if not isinstance(state,dict) or state.get("student_schema_version") != STUDENT_SCHEMA_VERSION:
        raise ValueError("invalid student schema")
    if state.get("authority") != STUDENT_AUTHORITY:
        raise ValueError("student authority escalation rejected")
    for k in ("episodes","lessons","mistakes","experiments","curriculum"):
        if not isinstance(state.get(k),list):
            raise ValueError(f"student {k} must be list")
    if collection not in {"episodes","lessons","mistakes","experiments","curriculum"}:
        raise ValueError("unapproved student collection")
    prev=state.get("last_event_hash","GENESIS")
    event=dict(body)
    event["student_authority"]=STUDENT_AUTHORITY
    event["prev_hash"]=prev
    event["event_hash"]=hashlib.sha256(prev.encode()+_canon(body)).hexdigest()
    state[collection].append(event)
    state["last_event_hash"]=event["event_hash"]
    return event

def build_episode(*, decision_event_ids:List[str], symbol:str, interval:str,
                  provider_provenance:dict, features_known_at_decision:dict,
                  gate_outcomes:dict, outcome:dict, costs:dict,
                  regime_labels:List[str], data_quality_flags:List[str],
                  build_hash:str, config_hash:str, decision_ts:str) -> dict:
    # Caller supplies only contemporaneous features. Future bars are never accepted here.
    required=[decision_event_ids,symbol,interval,provider_provenance,build_hash,config_hash,decision_ts]
    if any(x in (None,"",[]) for x in required):
        raise ValueError("episode missing required evidence")
    return {
        "type":"EPISODE","episode_id":_hash([decision_event_ids,symbol,decision_ts,build_hash,config_hash]),
        "decision_event_ids":list(decision_event_ids),"symbol":symbol,"interval":interval,
        "provider_provenance":dict(provider_provenance),
        "features_known_at_decision":dict(features_known_at_decision),
        "gate_outcomes":dict(gate_outcomes),"outcome":dict(outcome),"costs":dict(costs),
        "regime_labels":list(regime_labels),"data_quality_flags":list(data_quality_flags),
        "build_hash":build_hash,"config_hash":config_hash,"decision_ts":decision_ts,
        "created_at":utc_now(),
    }

def post_trade_autopsy(episode:dict) -> dict:
    """Classify process evidence without equating profit with correctness."""
    flags=[]
    if episode.get("data_quality_flags"): flags.append("DATA_QUALITY")
    gates=episode.get("gate_outcomes",{})
    if any(v is False for v in gates.values()): flags.append("GATE_REJECTION_PRESENT")
    outcome=episode.get("outcome",{})
    pnl=outcome.get("pnl")
    process_ok=not episode.get("data_quality_flags") and not outcome.get("process_violation",False)
    if process_ok and isinstance(pnl,(int,float)) and pnl < 0:
        classification="VALID_PROCESS_ADVERSE_OUTCOME"
    elif not process_ok:
        classification="PROCESS_OR_DATA_ISSUE"
    else:
        classification="VALID_PROCESS"
    return {
        "type":"AUTOPSY","episode_id":episode["episode_id"],"classification":classification,
        "flags":flags,"pnl":pnl,"notes":"Profit/loss alone never defines decision quality.",
        "created_at":utc_now(),
    }

def mistake_from_autopsy(episode:dict, autopsy:dict) -> Optional[dict]:
    if autopsy["classification"] != "PROCESS_OR_DATA_ISSUE":
        return None
    signature=_hash({
        "symbol":episode["symbol"],"interval":episode["interval"],
        "flags":sorted(autopsy["flags"]),"regimes":sorted(episode.get("regime_labels",[]))
    })
    return {
        "type":"MISTAKE","mistake_id":_hash([episode["episode_id"],signature]),
        "episode_id":episode["episode_id"],"signature":signature,
        "flags":autopsy["flags"],"regime_labels":episode.get("regime_labels",[]),
        "status":"OBSERVED","created_at":utc_now(),
    }

def similar_episode_score(current:dict, past:dict) -> float:
    score=0.0
    if current.get("symbol")==past.get("symbol"): score+=0.30
    if current.get("interval")==past.get("interval"): score+=0.20
    a=set(current.get("regime_labels",[])); b=set(past.get("regime_labels",[]))
    if a or b: score+=0.30*(len(a&b)/max(1,len(a|b)))
    cf=current.get("features_known_at_decision",{}); pf=past.get("features_known_at_decision",{})
    common=set(cf)&set(pf)
    if common:
        matches=sum(1 for k in common if cf[k]==pf[k])
        score+=0.20*matches/len(common)
    return round(min(score,1.0),6)

def pre_trade_recall(state:dict, current_context:dict, limit:int=5) -> dict:
    """Advisory memory only. Never returns trade authority."""
    validate_student_state(state)
    ranked=sorted(
        ((similar_episode_score(current_context,e),e) for e in state["episodes"]),
        key=lambda x:x[0], reverse=True
    )
    similar=[{"similarity":s,"episode_id":e["episode_id"],
              "autopsy_classification":e.get("autopsy_classification"),
              "outcome":e.get("outcome",{}),"regime_labels":e.get("regime_labels",[])}
             for s,e in ranked[:limit] if s>0]
    signatures={m.get("signature") for m in state["mistakes"]}
    return {
        "authority":STUDENT_AUTHORITY,"similar_episodes":similar,
        "known_mistake_count":len(signatures),
        "warning":"Recall is evidence for analysis only; Forge/Truth/Risk remain authoritative."
    }

def create_lesson(*, episode_ids:List[str], hypothesis:str, baseline:str,
                  sample_size:int, oos:bool, walk_forward_windows:int,
                  cost_sensitivity:dict, failure_modes:List[str],
                  provenance_ok:bool=True, leakage_free:bool=True) -> dict:
    if not isinstance(sample_size,int) or isinstance(sample_size,bool) or sample_size < 0:
        raise ValueError("sample_size must be a non-negative integer")
    if not isinstance(walk_forward_windows,int) or isinstance(walk_forward_windows,bool) or walk_forward_windows < 0:
        raise ValueError("walk_forward_windows must be a non-negative integer")
    grade=evidence_grade(sample_size,oos,walk_forward_windows,provenance_ok,leakage_free)
    return {
        "type":"LESSON","lesson_id":_hash([episode_ids,hypothesis,baseline]),
        "evidence_episode_ids":list(episode_ids),"hypothesis":hypothesis,
        "comparison_baseline":baseline,"sample_size":sample_size,"oos":bool(oos),
        "walk_forward_windows":int(walk_forward_windows),
        "cost_sensitivity":dict(cost_sensitivity),"failure_modes":list(failure_modes),
        "evidence_grade":grade,"status":"INCONCLUSIVE" if grade in {"INVALID","INCONCLUSIVE"} else "SUPPORTED_FOR_RESEARCH",
        "created_at":utc_now(),
    }

def examiner(lesson:dict) -> dict:
    reasons=[]
    if lesson.get("evidence_grade")!="TESTABLE": reasons.append("INSUFFICIENT_EVIDENCE_GRADE")
    if lesson.get("sample_size",0)<100: reasons.append("MIN_100_EPISODES")
    if not lesson.get("oos"): reasons.append("OOS_REQUIRED")
    if lesson.get("walk_forward_windows",0)<5: reasons.append("MIN_5_WALK_FORWARD_WINDOWS")
    if lesson.get("failure_modes"): reasons.append("UNRESOLVED_FAILURE_MODES")
    return {
        "lesson_id":lesson["lesson_id"],"passed":not reasons,"reasons":reasons,
        "authority":STUDENT_AUTHORITY,"created_at":utc_now(),
    }

def evolution_handoff(lesson:dict, examination:dict) -> dict:
    if not examination.get("passed"):
        raise ValueError("student examiner rejected evolution handoff")
    return {
        "type":"EVOLUTION_CHALLENGER_PROPOSAL","source_lesson_id":lesson["lesson_id"],
        "authority":STUDENT_AUTHORITY,"shadow_only":True,"requires_human_approval":True,
        "may_mutate_champion":False,"created_at":utc_now(),
    }

def ingest_curriculum(state:dict, *, title:str, formal_hypothesis:str,
                      source_provenance:dict, test_spec:dict) -> dict:
    """Teacher Mode: knowledge enters as a hypothesis to test, never as truth."""
    if not title or not formal_hypothesis or not source_provenance or not test_spec:
        raise ValueError("curriculum requires provenance and formal test specification")
    return append_student_event(state,"curriculum",{
        "type":"CURRICULUM","title":title,"formal_hypothesis":formal_hypothesis,
        "source_provenance":source_provenance,"test_spec":test_spec,
        "status":"QUARANTINED_HYPOTHESIS","created_at":utc_now(),
    })

def assert_no_authority_escalation(candidate:dict) -> None:
    """Reject any Student artifact that attempts to claim execution/policy authority."""
    flat=json.dumps(candidate,sort_keys=True).lower()
    if candidate.get("authority") not in (None,STUDENT_AUTHORITY):
        raise ValueError("student authority escalation rejected")
    for key in FORBIDDEN_MUTATIONS:
        if key in candidate.get("mutations",{}):
            raise ValueError(f"student forbidden mutation: {key}")
    if candidate.get("execute") or candidate.get("place_order") or candidate.get("approve"):
        raise ValueError("student execution/approval authority rejected")


def ensure_runtime_student(runtime:dict) -> dict:
    """Embed Student in the same authoritative atomic runtime snapshot."""
    s=runtime.get("student")
    if s is None:
        s=initial_student_state()
        runtime["student"]=s
    validate_student_state(s)
    return s

def observe_decision(state:dict, *, decision_event_ids:list, symbol:str, interval:str,
                     provider_provenance:dict, features_known_at_decision:dict,
                     gate_outcomes:dict, outcome:dict, costs:dict, regime_labels:list,
                     data_quality_flags:list, build_hash:str, config_hash:str, decision_ts:str) -> dict:
    e=build_episode(decision_event_ids=decision_event_ids,symbol=symbol,interval=interval,
        provider_provenance=provider_provenance,features_known_at_decision=features_known_at_decision,
        gate_outcomes=gate_outcomes,outcome=outcome,costs=costs,regime_labels=regime_labels,
        data_quality_flags=data_quality_flags,build_hash=build_hash,config_hash=config_hash,
        decision_ts=decision_ts)
    a=post_trade_autopsy(e)
    e["autopsy_classification"]=a["classification"]
    append_student_event(state,"episodes",e)
    m=mistake_from_autopsy(e,a)
    if m: append_student_event(state,"mistakes",m)
    return {"episode":e,"autopsy":a,"mistake":m}

def student_summary(state:dict) -> dict:
    validate_student_state(state)
    return {
        "authority":STUDENT_AUTHORITY,
        "episodes":len(state["episodes"]),"lessons":len(state["lessons"]),
        "mistakes":len(state["mistakes"]),"experiments":len(state["experiments"]),
        "curriculum":len(state["curriculum"]),
        "last_event_hash":state.get("last_event_hash","GENESIS"),
        "execution_authority":False,
    }
