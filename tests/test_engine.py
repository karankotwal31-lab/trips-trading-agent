import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from providers import Bar, DemoProvider
from strategies import consensus, evaluate, features
from risk import forge_gate, position_size


def base_cfg():
    return json.loads((ROOT / "engine" / "config.json").read_text())


def test_demo_provider_deterministic_shape():
    bars = DemoProvider().bars("TEST", 100)
    assert len(bars) == 100
    assert all(b.high >= max(b.open, b.close) for b in bars)
    assert all(b.low <= min(b.open, b.close) for b in bars)


def test_config_guard_prevents_demo_relabeling():
    from config_guard import ConfigError, validate_config
    cfg = base_cfg(); cfg["provider_source_kind"] = "real"
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_config_guard_enforces_hard_risk_ceiling():
    from config_guard import ConfigError, validate_config
    cfg = base_cfg(); cfg["risk"]["max_risk_per_trade_pct"] = 0.5
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_closed_bars_excludes_incomplete_candle():
    from market_time import closed_bars_only
    now = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    bars = [
        Bar("2026-01-01T10:00:00+00:00", 1, 2, 1, 2, 1),
        Bar("2026-01-01T11:00:00+00:00", 2, 3, 2, 3, 1),
        Bar("2026-01-01T12:00:00+00:00", 3, 4, 3, 4, 1),
    ]
    closed = closed_bars_only(bars, "60min", now=now, close_lag_seconds=0)
    assert closed == bars[:2] and len(closed) == 2


def test_features_and_votes_use_signal_score_not_probability():
    bars = DemoProvider().bars("TEST", 100)
    f = features(bars)
    votes = evaluate(f)
    c = consensus(votes)
    assert len(votes) == 3
    assert c["direction"] in ("LONG", "FLAT")
    assert "signal_score" in c and "confidence" not in c
    assert 0 <= c["signal_score"] <= 1


def test_position_size_respects_risk_and_exposure():
    qty = position_size(100000, 100, 98, 0.005, 0.30, 0)
    assert qty <= 250
    assert qty * 100 <= 30000


def test_gate_fails_conflict_live_and_global_halt():
    cfg = base_cfg(); cfg["mode"] = "live"
    gate = forge_gate(config=cfg, provider_name="x", bars_count=100, signal_score=0.9, conflict=True,
                      stale=False, positions={}, pending_entries={}, symbol="TEST", daily_pnl=0, equity=100000,
                      drawdown_pct=0, assumed_spread_bps=5, cooldown_remaining=0, global_halt=True,
                      agreement_count=2, candle_context="BULLISH_CONTEXT")
    assert not gate["passed"]
    failed = {x["name"] for x in gate["checks"] if not x["passed"]}
    assert {"paper_mode", "strategy_conflict", "global_halt"} <= failed


def test_same_bar_close_cannot_retroactively_move_stop():
    from forge_agent import apply_position_management
    cfg = base_cfg()
    state = {"positions": {"X": {"entry": 100.0, "qty": 1, "initial_stop": 98.0, "stop": 98.0,
                                      "target": 110.0, "atr_at_entry": 2.0, "protected": False,
                                      "trailing": False, "last_processed_bar_ts": None}},
             "cash": 0.0, "daily_pnl": 0.0, "consecutive_losses": 0, "cooldown_remaining": 0}
    ledger = []
    # Low 99 is below the NEW break-even stop but above the OLD 98 stop. No retroactive exit is allowed.
    bar = Bar("2026-01-01T00:00:00+00:00", 100, 103, 99, 102.5, 1)
    apply_position_management("X", bar, state, ledger, cfg)
    assert "X" in state["positions"]
    assert state["positions"]["X"]["stop"] >= 100.0
    assert ledger == []


def test_gap_through_stop_fills_at_worse_open():
    from forge_agent import apply_position_management
    cfg = base_cfg(); cfg["risk"]["assumed_spread_bps"] = 0; cfg["risk"]["assumed_slippage_bps"] = 0
    state = {"positions": {"X": {"entry": 100.0, "qty": 1, "initial_stop": 98.0, "stop": 98.0,
                                      "target": 110.0, "atr_at_entry": 2.0, "protected": False,
                                      "trailing": False, "last_processed_bar_ts": None}},
             "cash": 0.0, "daily_pnl": 0.0, "consecutive_losses": 0, "cooldown_remaining": 0}
    ledger = []
    apply_position_management("X", Bar("2026-01-01T00:00:00+00:00", 95, 96, 94, 95, 1), state, ledger, cfg)
    assert ledger[-1]["reason"] == "GAP_STOP_OR_PROTECTIVE_EXIT"
    assert ledger[-1]["exit"] == 95.0


def test_position_management_is_idempotent_per_bar():
    from forge_agent import apply_position_management
    cfg = base_cfg(); cfg["risk"]["assumed_spread_bps"] = 0; cfg["risk"]["assumed_slippage_bps"] = 0
    bar = Bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100.5, 1)
    state = {"positions": {"X": {"entry": 100.0, "qty": 1, "initial_stop": 98.0, "stop": 98.0,
                                      "target": 110.0, "atr_at_entry": 2.0, "protected": False,
                                      "trailing": False, "last_processed_bar_ts": None}},
             "cash": 0.0, "daily_pnl": 0.0, "consecutive_losses": 0, "cooldown_remaining": 0}
    ledger=[]
    apply_position_management("X", bar, state, ledger, cfg)
    first = copy.deepcopy(state)
    apply_position_management("X", bar, state, ledger, cfg)
    assert state == first and ledger == []


def test_daily_pnl_updates_and_cooldown_requires_new_market_bars():
    from forge_agent import advance_cooldown_on_market_bar, apply_position_management
    cfg = base_cfg(); cfg["risk"]["assumed_spread_bps"] = 0; cfg["risk"]["assumed_slippage_bps"] = 0
    cfg["risk"]["cooldown_after_losses"] = 1; cfg["risk"]["cooldown_cycles"] = 2
    loss_ts="2026-01-01T00:00:00+00:00"
    state = {"positions": {"X": {"entry": 100.0, "qty": 1, "initial_stop": 98.0, "stop": 98.0,
                                      "target": 110.0, "atr_at_entry": 2.0, "protected": False,
                                      "trailing": False, "last_processed_bar_ts": None}},
             "cash": 0.0, "daily_pnl": 0.0, "consecutive_losses": 0, "cooldown_remaining": 0,
             "cooldown_last_bar_ts": None}
    ledger=[]
    apply_position_management("X", Bar(loss_ts,100,100,97,98,1), state, ledger, cfg)
    assert state["daily_pnl"] == -2.0
    assert state["cooldown_remaining"] == 2
    assert state["consecutive_losses"] == 0
    # Re-running on the same market bar cannot burn cooldown.
    advance_cooldown_on_market_bar(state, loss_ts); advance_cooldown_on_market_bar(state, loss_ts)
    assert state["cooldown_remaining"] == 2
    advance_cooldown_on_market_bar(state, "2026-01-01T01:00:00+00:00")
    assert state["cooldown_remaining"] == 1
    advance_cooldown_on_market_bar(state, "2026-01-01T02:00:00+00:00")
    assert state["cooldown_remaining"] == 0


def test_truth_guard_rejects_synthetic_for_current_trade_but_allows_analysis():
    from truth_guard import validate_bars
    bars = DemoProvider().bars("TEST", 100)
    v = validate_bars(source="demo", source_family="trips_synthetic", source_kind="demo", symbol="TEST", interval="60min",
                      bars=bars, max_age_minutes=120, min_bars=60, fixed_source_kind="demo")
    assert v.trusted_for_analysis is True
    assert v.trusted_for_trade is False
    assert len(v.integrity_hash) == 64


def test_truth_guard_provider_binding_blocks_demo_as_real_even_if_called_directly():
    from truth_guard import validate_bars
    bars = DemoProvider().bars("TEST", 100)
    v = validate_bars(source="demo", source_family="trips_synthetic", source_kind="real", symbol="TEST", interval="60min",
                      bars=bars, max_age_minutes=999999, min_bars=60, fixed_source_kind="demo")
    assert v.trusted_for_trade is False
    failed = {c["name"] for c in v.checks if not c["passed"]}
    assert "provider_kind_binding" in failed


def test_truth_hash_binds_symbol_and_interval():
    from truth_guard import validate_bars
    bars = DemoProvider().bars("SAME", 100)
    a = validate_bars(source="x", source_family="f", source_kind="replay", symbol="AAA", interval="60min", bars=bars,
                      max_age_minutes=120, min_bars=60)
    b = validate_bars(source="x", source_family="f", source_kind="replay", symbol="BBB", interval="60min", bars=bars,
                      max_age_minutes=120, min_bars=60)
    assert a.integrity_hash != b.integrity_hash


def _real_verdict(source, family, symbol, bars):
    from truth_guard import validate_bars
    return validate_bars(source=source, source_family=family, source_kind="real", symbol=symbol, interval="60min",
                         bars=bars, max_age_minutes=999999, min_bars=3, realtime_request_attested=True)


def test_cross_source_requires_independent_families():
    from truth_guard import cross_validate
    bars = DemoProvider().bars("A", 10)
    a = _real_verdict("A1", "same_vendor", "A", bars)
    b = _real_verdict("A2", "same_vendor", "A", bars)
    x = cross_validate(a, bars, b, bars, min_cross_source_bars=3)
    assert x["passed"] is False and x["reason"] == "sources_not_independent"


def test_cross_source_rejects_timestamp_misalignment():
    from truth_guard import cross_validate
    a_bars = DemoProvider().bars("A", 10)
    b_bars = [Bar((datetime.fromisoformat(x.ts)+timedelta(minutes=30)).isoformat(), x.open,x.high,x.low,x.close,x.volume) for x in a_bars]
    a = _real_verdict("A", "vendorA", "A", a_bars)
    b = _real_verdict("B", "vendorB", "A", b_bars)
    x = cross_validate(a, a_bars, b, b_bars, max_timestamp_skew_minutes=5, min_cross_source_bars=3)
    assert x["passed"] is False


def test_cross_source_disagreement_blocks_trade():
    from truth_guard import apply_cross_source_verification, cross_validate
    a_bars = DemoProvider().bars("A", 10)
    b_bars = [Bar(x.ts, x.open*1.02, x.high*1.02, x.low*1.02, x.close*1.02, x.volume) for x in a_bars]
    a = _real_verdict("A", "vendorA", "A", a_bars)
    b = _real_verdict("B", "vendorB", "A", b_bars)
    cross = cross_validate(a, a_bars, b, b_bars, max_ohlc_deviation_pct=0.005, min_cross_source_bars=3)
    a = apply_cross_source_verification(a, cross, require_for_trade=True)
    assert cross["passed"] is False and a.trusted_for_trade is False


def test_candle_interpreter_strength_is_bounded_and_not_named_confidence():
    from candle_intelligence import interpret
    r = interpret(DemoProvider().bars("CANDLES", 100))
    d = r.to_dict()
    assert r.label in {"BULLISH_CONTEXT", "BEARISH_CONTEXT", "NEUTRAL_OR_AMBIGUOUS", "CONFLICTING_CONTEXT"}
    assert 0 <= r.strength_score <= 1
    assert "confidence" not in d


def test_constitution_blocks_unverified_trade():
    from constitution import constitution_gate
    c = constitution_gate(truth={"trusted_for_analysis": True, "trusted_for_trade": False}, mode="paper",
                          gate_passed=True, model_conflict=False, anomaly=False, requires_verified_trade_data=True)
    assert c["passed"] is False
    assert "truth_for_trade" in {x["name"] for x in c["checks"] if not x["passed"]}


def test_store_strict_read_does_not_silently_reset_corruption():
    import store
    old = store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA = Path(td)
        (store.DATA / "portfolio.json").write_text("{not-json")
        try:
            try:
                store.read_json("portfolio.json", {"reset": True}, strict=True)
                assert False, "expected StateStoreError"
            except store.StateStoreError:
                pass
        finally:
            store.DATA = old


def test_hash_chain_detects_mutation():
    from store import append_hash_chained_event, verify_hash_chain
    xs=[]
    append_hash_chained_event(xs, {"x":1}); append_hash_chained_event(xs, {"x":2})
    assert verify_hash_chain(xs)
    xs[0]["x"] = 99
    assert not verify_hash_chain(xs)


def test_state_validation_rejects_impossible_account_values():
    from store import StateStoreError, initial_state, validate_state
    s=initial_state(1000); s["cash"]=-1
    try:
        validate_state(s)
        assert False, "expected StateStoreError"
    except StateStoreError:
        pass


def test_pending_entry_waits_for_next_bar_and_never_same_close_fill():
    from forge_agent import _execute_pending
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    bars=DemoProvider().bars("X", 100)
    signal_ts=bars[-1].ts
    state={"equity":100000.0,"peak_equity":100000.0,"cash":100000.0,"positions":{},
           "pending_entries":{"X":{"signal_bar_ts":signal_ts,"strategy":"trend","signal_score":0.9,
                                    "agreement_count":2,"candle_context":"BULLISH_CONTEXT","atr_at_signal":2.0}},
           "daily_pnl":0.0,"cooldown_remaining":0,"halted":False,"consecutive_losses":0}
    truth={"trusted_for_analysis":True,"trusted_for_trade":True,"source":"A","integrity_hash":"h",
           "checks":[{"name":"freshness","passed":True}],"cross_source":{"passed":True}}
    ledger=[]; audit=[]
    exposure=_execute_pending("X", bars, truth, state, ledger, audit, cfg, 0.0, 0.0, False)
    assert "X" in state["pending_entries"] and "X" not in state["positions"]
    # Add one new closed bar. The pre-existing pending order may now simulate a next-bar-open fill.
    last=bars[-1]
    nxt=Bar((datetime.fromisoformat(last.ts)+timedelta(hours=1)).isoformat(), last.close,last.close+1,last.close-0.5,last.close+0.5,1000)
    bars2=bars+[nxt]
    exposure=_execute_pending("X", bars2, truth, state, ledger, audit, cfg, exposure, 0.0, False)
    assert "X" not in state["pending_entries"]
    assert any(x["type"]=="PAPER_ENTRY" and x["market_bar_ts"]==nxt.ts for x in ledger)


def test_declared_real_without_machine_entitlement_attestation_cannot_trade():
    from truth_guard import validate_bars
    bars = DemoProvider().bars("ATTEST", 100)
    v = validate_bars(source="vendor", source_family="vendor", source_kind="real", symbol="ATTEST", interval="60min",
                      bars=bars, max_age_minutes=999999, min_bars=60, realtime_request_attested=False)
    assert v.trusted_for_analysis is True
    assert v.trusted_for_trade is False
    failed = {c["name"] for c in v.checks if not c["passed"]}
    assert "realtime_entitlement_request" in failed


def test_truth_guard_rejects_wrong_interval_cadence():
    from truth_guard import validate_bars
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars=[]
    for i in range(65):
        ts=(start+timedelta(days=i)).isoformat()
        bars.append(Bar(ts,100,101,99,100,1000))
    v=validate_bars(source="x",source_family="x",source_kind="replay",symbol="X",interval="60min",bars=bars,
                    max_age_minutes=999999,min_bars=60)
    assert not v.trusted_for_analysis
    assert "interval_cadence" in {c["name"] for c in v.checks if not c["passed"]}


def test_open_above_target_is_target_even_if_later_bar_hits_stop():
    from forge_agent import apply_position_management
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    state={"positions":{"X":{"entry":100.0,"qty":1,"initial_stop":98.0,"stop":98.0,"target":104.0,
                              "atr_at_entry":2.0,"protected":False,"trailing":False,"last_processed_bar_ts":None}},
           "cash":0.0,"daily_pnl":0.0,"consecutive_losses":0,"cooldown_remaining":0}
    ledger=[]
    apply_position_management("X", Bar("2026-01-01T00:00:00+00:00",105,106,97,99,1), state, ledger, cfg)
    assert ledger[-1]["reason"] == "TARGET"
    assert ledger[-1]["exit"] == 104.0


def test_cross_source_compares_full_ohlc_not_only_close():
    from truth_guard import cross_validate
    a_bars=DemoProvider().bars("OHLC",10)
    b_bars=[Bar(x.ts,x.open,x.high*1.02,x.low,x.close,x.volume) for x in a_bars]
    a=_real_verdict("A","vendorA","OHLC",a_bars)
    b=_real_verdict("B","vendorB","OHLC",b_bars)
    x=cross_validate(a,a_bars,b,b_bars,max_ohlc_deviation_pct=0.005,min_cross_source_bars=3)
    assert x["passed"] is False


def test_config_guard_rejects_unsupported_instrument_scope():
    from config_guard import ConfigError, validate_config
    cfg=base_cfg(); cfg["instrument_scope"]="FUTURES"
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_pending_orders_reserve_position_slots():
    cfg=base_cfg(); cfg["risk"]["max_open_positions"]=1
    gate=forge_gate(config=cfg,provider_name="x",bars_count=100,signal_score=.9,conflict=False,stale=False,
                    positions={},pending_entries={"OTHER":{"x":1}},symbol="TEST",daily_pnl=0,equity=100000,
                    drawdown_pct=0,assumed_spread_bps=5,cooldown_remaining=0,global_halt=False,
                    agreement_count=2,candle_context="BULLISH_CONTEXT")
    assert not gate["passed"]
    assert "position_limit" in {x["name"] for x in gate["checks"] if not x["passed"]}


def test_invalid_nan_data_is_rejected_with_verdict_not_validator_crash():
    from truth_guard import validate_bars
    bars=DemoProvider().bars("NAN",100)
    b=bars[-1]; bars[-1]=Bar(b.ts,float('nan'),b.high,b.low,b.close,b.volume)
    v=validate_bars(source="x",source_family="x",source_kind="replay",symbol="NAN",interval="60min",
                    bars=bars,max_age_minutes=120,min_bars=60)
    assert not v.trusted_for_analysis
    assert len(v.integrity_hash)==64
    assert "finite_positive_prices" in {c["name"] for c in v.checks if not c["passed"]}


def test_strict_state_requires_checksum_sidecar_when_file_exists():
    import store
    old = store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA = Path(td)
        (store.DATA / "portfolio.json").write_text(json.dumps({"x": 1}))
        try:
            try:
                store.read_json("portfolio.json", {}, strict=True)
                assert False, "expected StateStoreError"
            except store.StateStoreError:
                pass
        finally:
            store.DATA = old


def test_authoritative_runtime_refuses_legacy_partial_state():
    import store
    old = store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA = Path(td)
        (store.DATA / "portfolio.json").write_text("{}")
        try:
            try:
                store.read_runtime(1000.0)
                assert False, "expected StateStoreError"
            except store.StateStoreError:
                pass
        finally:
            store.DATA = old


def test_authoritative_runtime_snapshot_roundtrip_and_integrity():
    import store
    old = store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA = Path(td)
        runtime = store.initial_runtime(1000.0)
        store.write_runtime(runtime)
        restored = store.read_runtime(1000.0)
        assert restored == runtime
        # Tampering with the snapshot without the checksum must fail closed.
        p = store.DATA / "runtime_snapshot.json"
        obj = json.loads(p.read_text()); obj["portfolio"]["cash"] = 999.0; p.write_text(json.dumps(obj))
        try:
            store.read_runtime(1000.0)
            assert False, "expected StateStoreError"
        except store.StateStoreError:
            pass
        finally:
            store.DATA = old


def test_stale_delayed_data_is_not_trusted_for_analysis():
    from truth_guard import validate_bars
    start = datetime.now(timezone.utc) - timedelta(days=2)
    bars=[]
    for i in range(65):
        ts=(start+timedelta(hours=i)).isoformat()
        bars.append(Bar(ts,100,101,99,100,1000))
    v=validate_bars(source="delayed",source_family="vendor",source_kind="delayed",symbol="X",interval="60min",
                    bars=bars,max_age_minutes=120,min_bars=60)
    assert not v.trusted_for_analysis
    assert "freshness" in {c["name"] for c in v.checks if not c["passed"]}


def test_same_bar_pending_fill_exit_does_not_leave_phantom_exposure():
    from forge_agent import _execute_pending
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    bars=DemoProvider().bars("X",100)
    signal=bars[-1]
    state={"equity":100000.0,"peak_equity":100000.0,"cash":100000.0,"positions":{},
           "pending_entries":{"X":{"signal_bar_ts":signal.ts,"strategy":"trend","signal_score":0.9,
                                    "agreement_count":2,"candle_context":"BULLISH_CONTEXT","atr_at_signal":2.0}},
           "daily_pnl":0.0,"risk_day_start_equity":100000.0,"cooldown_remaining":0,"halted":False,"consecutive_losses":0}
    truth={"trusted_for_analysis":True,"trusted_for_trade":True,"source":"A","integrity_hash":"h",
           "checks":[{"name":"freshness","passed":True}],"cross_source":{"passed":True}}
    # Next bar fills at 100, then falls through the 97 stop in the same bar.
    nxt=Bar((datetime.fromisoformat(signal.ts)+timedelta(hours=1)).isoformat(),100,101,96,97,1000)
    exposure=_execute_pending("X",bars+[nxt],truth,state,[],[],cfg,0.0,0.0,False)
    assert "X" not in state["positions"]
    assert exposure == 0.0


def test_build_manifest_detects_executable_drift():
    import shutil
    from build_guard import (CRITICAL_DIRECTORIES, CRITICAL_FILES, CRITICAL_PROJECT_FILES,
                             BuildIntegrityError, current_manifest, verify_build_integrity)
    with tempfile.TemporaryDirectory() as td:
        project = Path(td); root = project / "engine"; root.mkdir()
        for name in CRITICAL_FILES:
            shutil.copy2(ROOT / "engine" / name, root / name)
        for directory in CRITICAL_DIRECTORIES:
            shutil.copytree(ROOT / "engine" / directory, root / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for rel in CRITICAL_PROJECT_FILES:
            dest=project/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/rel,dest)
        manifest = current_manifest(root)
        mp = root / "approved_build.json"
        mp.write_text(json.dumps(manifest))
        verify_build_integrity(mp, root)
        with (root / "risk.py").open("a") as fh:
            fh.write("\n# drift\n")
        try:
            verify_build_integrity(mp, root)
            assert False, "expected BuildIntegrityError"
        except BuildIntegrityError:
            pass


def test_the_execution_layer_is_under_build_integrity():
    """The real-money path must trip build drift, not just the frozen core's neighbours.

    Without this, ``execution/conformance.py`` could be edited to answer SUPPORTED unconditionally
    and every downstream evidence digest would still be internally consistent.
    """
    from build_guard import CRITICAL_DIRECTORIES, current_manifest

    files = current_manifest()["files"]
    assert "execution" in CRITICAL_DIRECTORIES
    for name in ("execution/gateway.py", "execution/conformance.py", "execution/channels.py",
                 "execution/adapters.py", "execution/lifecycle.py", "execution/readiness.py"):
        assert f"engine/{name}" in files, name


def test_constitution_contains_atomic_state_build_lock_and_durable_escalation():
    from constitution import NON_NEGOTIABLES
    ids = {x[0] for x in NON_NEGOTIABLES}
    assert {"ATOMIC_STATE_COMMIT", "EXECUTABLE_BUILD_LOCK", "DURABLE_ESCALATION"} <= ids


def test_repeated_escalation_is_coalesced_not_unbounded():
    from forge_agent import create_escalation, _merge_escalation_queue
    q=[]
    a=create_escalation("DATA_ANOMALY","X",{"truth":{"integrity_hash":"a"},"signal_bar_ts":"1"})
    b=create_escalation("DATA_ANOMALY","X",{"truth":{"integrity_hash":"b"},"signal_bar_ts":"2"})
    touched1=_merge_escalation_queue(q,[a])
    touched2=_merge_escalation_queue(q,[b])
    assert len(q)==1
    assert q[0]["status"]=="OPEN" and q[0]["occurrences"]==2
    assert touched1==touched2==[q[0]["id"]]
    assert q[0]["packet"]["truth"]["integrity_hash"]=="b"


def test_escalation_acknowledgement_is_explicit_and_audited():
    import store
    old=store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA=Path(td)
        runtime=store.initial_runtime(1000.0)
        runtime["escalation_queue"].append({"id":"e1","status":"OPEN","reason":"X","symbol":"Y"})
        store.write_runtime(runtime)
        try:
            item=store.acknowledge_escalation("e1","reviewed")
            assert item["status"]=="ACKNOWLEDGED"
            restored=store.read_runtime(1000.0)
            assert restored["escalation_queue"][0]["status"]=="ACKNOWLEDGED"
            assert restored["audit_log"][-1]["event"]=="ESCALATION_ACKNOWLEDGED"
            assert store.verify_hash_chain(restored["audit_log"])
        finally:
            store.DATA=old


def test_position_history_replays_intermediate_stop_bar_after_downtime():
    from forge_agent import process_position_history
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    bars=[
        Bar("2026-01-01T00:00:00+00:00",100,101,99,100,1000),
        Bar("2026-01-01T01:00:00+00:00",100,101,97,99,1000),  # stop hit here
        Bar("2026-01-01T02:00:00+00:00",101,105,100,104,1000), # later recovery must not erase exit
    ]
    state={"positions":{"X":{"entry":100.0,"qty":1,"initial_stop":98.0,"stop":98.0,"target":110.0,
                               "atr_at_entry":2.0,"protected":False,"trailing":False,
                               "last_processed_bar_ts":bars[0].ts}},
           "cash":0.0,"daily_pnl":0.0,"consecutive_losses":0,"cooldown_remaining":0,
           "cooldown_last_bar_ts":None}
    ledger=[]
    result=process_position_history("X",bars,{"trusted_for_trade":True},state,ledger,cfg)
    assert result=="OK" and "X" not in state["positions"]
    assert ledger[-1]["market_bar_ts"]==bars[1].ts
    assert ledger[-1]["reason"]=="STOP_OR_PROTECTIVE_EXIT"


def test_position_history_gap_fails_closed_instead_of_jumping_to_latest_bar():
    from forge_agent import process_position_history
    cfg=base_cfg()
    bars=DemoProvider().bars("GAP",10)
    state={"positions":{"X":{"entry":100.0,"qty":1,"initial_stop":98.0,"stop":98.0,"target":110.0,
                               "atr_at_entry":2.0,"protected":False,"trailing":False,
                               "last_processed_bar_ts":"2020-01-01T00:00:00+00:00"}}}
    result=process_position_history("X",bars,{"trusted_for_trade":True},state,[],cfg)
    assert result=="HISTORY_GAP"
    assert "X" in state["positions"]


def test_config_guard_rejects_unvalidated_symbol_scope():
    from config_guard import ConfigError, validate_config
    cfg=base_cfg(); cfg["symbols"]=["SPY","PENNY"]
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_missing_authoritative_snapshot_after_initialization_fails_closed():
    import store
    old=store.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA=Path(td)
        runtime=store.initial_runtime(1000.0)
        store.write_runtime(runtime)
        (store.DATA / "runtime_snapshot.json").unlink()
        (store.DATA / "runtime_snapshot.json.sha256").unlink()
        try:
            store.read_runtime(1000.0)
            assert False, "expected StateStoreError"
        except store.StateStoreError:
            pass
        finally:
            store.DATA=old


def test_state_schema_version_mismatch_fails_closed():
    from store import StateStoreError, initial_state, validate_state
    s=initial_state(1000.0); s["schema_version"]=2
    try:
        validate_state(s)
        assert False, "expected StateStoreError"
    except StateStoreError:
        pass


def test_config_guard_rejects_case_variant_duplicate_symbols():
    from config_guard import ConfigError, validate_config
    cfg=base_cfg(); cfg["symbols"]=["SPY","spy"]
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_pending_immediate_exit_preserves_existing_marked_exposure():
    from forge_agent import _execute_pending
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    bars=DemoProvider().bars("X",100); signal=bars[-1]
    state={"equity":100000.0,"peak_equity":100000.0,"cash":100000.0,"positions":{},
           "pending_entries":{"X":{"signal_bar_ts":signal.ts,"strategy":"trend","signal_score":0.9,
                                    "agreement_count":2,"candle_context":"BULLISH_CONTEXT","atr_at_signal":2.0}},
           "daily_pnl":0.0,"risk_day_start_equity":100000.0,"cooldown_remaining":0,"halted":False,
           "consecutive_losses":0,"cooldown_last_bar_ts":None}
    truth={"trusted_for_analysis":True,"trusted_for_trade":True,"source":"A","integrity_hash":"h",
           "checks":[{"name":"freshness","passed":True}],"cross_source":{"passed":True}}
    nxt=Bar((datetime.fromisoformat(signal.ts)+timedelta(hours=1)).isoformat(),100,101,96,97,1000)
    exposure=_execute_pending("X",bars+[nxt],truth,state,[],[],cfg,12345.0,0.0,False)
    assert "X" not in state["positions"]
    assert exposure == 12345.0


def test_pending_rejects_nonpositive_stop_geometry():
    from forge_agent import _execute_pending
    cfg=base_cfg(); cfg["risk"]["assumed_spread_bps"]=0; cfg["risk"]["assumed_slippage_bps"]=0
    bars=DemoProvider().bars("X",100); signal=bars[-1]
    state={"equity":100000.0,"peak_equity":100000.0,"cash":100000.0,"positions":{},
           "pending_entries":{"X":{"signal_bar_ts":signal.ts,"strategy":"trend","signal_score":0.9,
                                    "agreement_count":2,"candle_context":"BULLISH_CONTEXT","atr_at_signal":1000.0}},
           "daily_pnl":0.0,"risk_day_start_equity":100000.0,"cooldown_remaining":0,"halted":False,
           "consecutive_losses":0,"cooldown_last_bar_ts":None}
    truth={"trusted_for_analysis":True,"trusted_for_trade":True,"source":"A","integrity_hash":"h",
           "checks":[{"name":"freshness","passed":True}],"cross_source":{"passed":True}}
    last=bars[-1]
    nxt=Bar((datetime.fromisoformat(last.ts)+timedelta(hours=1)).isoformat(),100,101,99,100,1000)
    audit=[]
    _execute_pending("X",bars+[nxt],truth,state,[],audit,cfg,0.0,0.0,False)
    assert "X" not in state["positions"] and "X" not in state["pending_entries"]
    assert audit[-1]["event"]=="PENDING_REJECT"


def test_state_validation_rejects_malformed_pending_order_fields():
    from store import StateStoreError, initial_state, validate_state
    s=initial_state(1000.0)
    s["pending_entries"]["SPY"]={"signal_bar_ts":"2026-01-01T00:00:00+00:00","signal_score":2.0,
                                 "atr_at_signal":1.0,"agreement_count":1,"candle_context":"BULLISH_CONTEXT"}
    try:
        validate_state(s)
        assert False, "expected StateStoreError"
    except StateStoreError:
        pass


def test_reappearing_acknowledged_escalation_gets_unique_open_episode_id():
    from forge_agent import create_escalation, _merge_escalation_queue
    q=[]
    item=create_escalation("DATA_ANOMALY","SPY",{"truth":{"integrity_hash":"same"},"signal_bar_ts":"same"})
    _merge_escalation_queue(q,[item])
    first_id=q[0]["id"]
    q[0]["status"]="ACKNOWLEDGED"
    again=create_escalation("DATA_ANOMALY","SPY",{"truth":{"integrity_hash":"same"},"signal_bar_ts":"same"})
    _merge_escalation_queue(q,[again])
    assert len(q)==2
    assert q[1]["status"]=="OPEN"
    assert q[1]["id"] != first_id


def test_constitution_contains_guardian_and_evolution_boundaries():
    from constitution import NON_NEGOTIABLES
    ids={x[0] for x in NON_NEGOTIABLES}
    assert {"HEALTH_BEFORE_TRADING","SELF_HEALING_BOUNDARY","NO_AUTONOMOUS_POLICY_MUTATION",
            "EVOLUTION_IN_QUARANTINE","SUPERVISOR_EVIDENCE_PACKET","RECOVERY_WITHOUT_ROLLBACK"} <= ids


def test_config_rejects_autonomous_evolution_code_mutation():
    from config_guard import ConfigError, validate_config
    cfg=base_cfg()
    cfg["version"]="0.4"
    cfg["health"]={"heartbeat_minutes":5,"stale_temp_minutes":60,"disk_free_min_mb":512,
                   "safe_auto_repair":True,"daily_deep_diagnostics":True,"hourly_light_diagnostics":True}
    cfg["evolution"]={"enabled":True,"shadow_only":True,"auto_apply_code_changes":True,
                      "auto_apply_risk_changes":False,"auto_apply_constitution_changes":False,
                      "require_out_of_sample":True,"require_human_approval":True,
                      "min_candidate_trades":100,"min_walk_forward_windows":5}
    cfg["supervisor"]={"daily_packet":True,"report_timezone":"Asia/Kolkata","report_local_time":"19:00",
                       "critical_escalation_immediate":True}
    try:
        validate_config(cfg)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_guardian_preflight_fails_on_invalid_runtime_without_repairing_it():
    from health_engine import preflight_health_gate
    cfg=json.loads((ROOT/"engine"/"config.json").read_text())
    runtime={"runtime_schema_version":1,"portfolio":{},"ledger":[],"audit_log":[],"escalation_queue":[]}
    r=preflight_health_gate(cfg,runtime)
    assert r["passed"] is False
    assert any(not x["passed"] for x in r["checks"])


def test_guardian_safe_repair_only_rebuilds_derived_escalation_mirror():
    import health_engine, store
    old_store=store.DATA; old_health=health_engine.DATA
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); store.DATA=d; health_engine.DATA=d
        try:
            runtime=store.initial_runtime(1000.0)
            runtime["escalation_queue"].append({"id":"e1","status":"OPEN","reason":"X","symbol":"Y"})
            store.write_runtime(runtime)
            cfg=json.loads((ROOT/"engine"/"config.json").read_text())
            repairs=health_engine._safe_repairs(cfg,runtime)
            assert any(x["type"]=="REBUILD_DERIVED_ESCALATION_MIRROR" for x in repairs)
            assert (d/"runtime_snapshot.json").exists()
            assert store.read_runtime(1000.0)["escalation_queue"][0]["id"]=="e1"
        finally:
            store.DATA=old_store; health_engine.DATA=old_health


def test_guardian_never_recreates_missing_authoritative_checksum():
    import health_engine, store
    old_store=store.DATA; old_health=health_engine.DATA
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); store.DATA=d; health_engine.DATA=d
        try:
            runtime=store.initial_runtime(1000.0)
            store.write_runtime(runtime)
            (d/"runtime_snapshot.json.sha256").unlink()
            cfg=json.loads((ROOT/"engine"/"config.json").read_text())
            ok, detail, loaded=health_engine._runtime_integrity(cfg)
            assert ok is False and loaded is None
            health_engine._safe_repairs(cfg,None)
            assert not (d/"runtime_snapshot.json.sha256").exists()
        finally:
            store.DATA=old_store; health_engine.DATA=old_health


def test_evolution_report_cannot_mutate_or_promote_champion():
    import evolution_engine, health_engine, store
    old_store=store.DATA; old_health=health_engine.DATA; old_evo=Path(evolution_engine.DATA) if hasattr(evolution_engine,'DATA') else None
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); store.DATA=d; health_engine.DATA=d
        # evolution_engine calls store helpers that now point at the temp runtime directory.
        try:
            store.write_runtime(store.initial_runtime(100000.0))
            store.write_json("backtest.json", {"all_small_sample":True,"worst_net_pnl":-100.0})
            r=evolution_engine.run_evolution_review()
            assert r["champion_mutated"] is False
            assert r["auto_promotion_allowed"] is False
            assert r["promotion_gate"]["requires_human_approval"] is True
            assert all(x["status"]=="QUARANTINED_CHALLENGER" for x in r["proposals"])
        finally:
            store.DATA=old_store; health_engine.DATA=old_health


def test_build_manifest_covers_guardian_evolution_and_supervisor():
    from build_guard import CRITICAL_FILES
    assert {"health_engine.py","health_daemon.py","evolution_engine.py","supervisor_bridge.py"} <= set(CRITICAL_FILES)


def test_guardian_fresh_install_does_not_create_false_prior_state_evidence():
    import health_engine, store
    old_store=store.DATA; old_health=health_engine.DATA
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); store.DATA=d; health_engine.DATA=d
        try:
            r=health_engine.run_health_check("daily", apply_repairs=True, persist=False)
            assert r["status"] == "HEALTHY"
            assert r["repairs"] == []
            assert not (d/"escalations.json").exists()
            assert not (d/"runtime_initialized.marker").exists()
        finally:
            store.DATA=old_store; health_engine.DATA=old_health


def test_capability_registry_states_locked_live_route_without_weakening_paper_first():
    registry=json.loads((ROOT/"engine"/"capability_registry.json").read_text())
    execution=registry["supported"]["execution"].lower()
    assert "mode=paper" in execution
    assert "live-money-only" in execution
    assert "locked" in execution
    text=" ".join(registry["not_supported"]).lower()
    assert "live-money" in text
    assert "guaranteed profitability" in text
    constitution=(ROOT/"docs"/"TRIPS_CONSTITUTION.md").read_text()
    paper_first=next(line for line in constitution.splitlines() if "**PAPER_FIRST**" in line)
    assert "cannot submit live-money orders while this rule is in force" in paper_first
    assert "design does not grant authority" in paper_first
    cfg=json.loads((ROOT/"engine"/"config.json").read_text())
    assert cfg["mode"]=="paper"


def test_security_diagnostics_never_emit_credential_values():
    import security_diagnostics
    r=security_diagnostics.run_security_diagnostics()
    assert "provider_credentials_present" in r
    assert all(isinstance(v,bool) for v in r["provider_credentials_present"].values())
    dumped=json.dumps(r)
    assert "secret_value_exposed\": true" not in dumped.lower()


def test_chaos_diagnostics_fail_closed_on_small_adversarial_sample():
    import chaos_diagnostics
    r=chaos_diagnostics.run_chaos(seed=1,malformed_cases=30,sizing_cases=200)
    assert r["invalid_position_sizes"]==0
    assert r["malformed_trade_eligible_accepts"]==0
    assert r["validator_crashes"]==0
    assert r["passed"] is True


def test_guardian_health_is_not_mislabeled_as_trade_authority():
    import health_engine, store
    old_store=store.DATA; old_health=health_engine.DATA
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); store.DATA=d; health_engine.DATA=d
        try:
            r=health_engine.run_health_check("heartbeat",apply_repairs=False,persist=False)
            assert r["trade_authority"]=="NOT_EVALUATED_BY_GUARDIAN"
            assert "trading_allowed" not in r
        finally:
            store.DATA=old_store; health_engine.DATA=old_health


def test_supervisor_packet_backtest_is_compact_summary_not_full_matrix():
    text=(ROOT/"engine"/"supervisor_bridge.py").read_text()
    assert '"backtest_summary"' in text
    assert '"backtest": backtest' not in text


def test_decision_mirror_is_hash_chained_and_separates_facts_from_inference():
    from decision_mirror import append_decision_event
    from store import initial_runtime, verify_hash_chain
    r=initial_runtime(100000.0)
    e=append_decision_event(r,event_kind="MARKET_DECISION",subsystem="TEST",action="NO_TRADE",
                            symbol="SPY",observed_facts={"close":100.0},
                            inference={"interpretation":"range"},evidence_refs={"hash":"abc"})
    assert e["observed_facts"]=={"close":100.0}
    assert e["inference"]=={"interpretation":"range"}
    assert e["truth_contract"]["missing_evidence_must_not_be_invented"] is True
    assert verify_hash_chain(r["decision_journal"])
    assert r["decision_journal"][0]["seq"]==1


def test_supervisor_relay_exports_every_unacknowledged_decision_without_claiming_delivery():
    from decision_mirror import append_decision_event
    from store import initial_runtime
    from supervisor_relay import build_outbox_from_runtime
    cfg=base_cfg(); r=initial_runtime(100000.0)
    append_decision_event(r,event_kind="NO_TRADE",subsystem="TEST",action="REJECT",observed_facts={"reason":"truth"})
    append_decision_event(r,event_kind="PAPER_ENTRY",subsystem="TEST",action="PAPER_ENTRY",observed_facts={"qty":1})
    out=build_outbox_from_runtime(r,cfg,"manifest-test",persist=False)
    assert out["transport_state"]=="OUTBOX_READY_NOT_DELIVERED"
    assert len(out["packets"])==2
    assert [p["decision_seq"] for p in out["packets"]]==[1,2]
    assert all(p["supervisor_contract"]["cannot_submit_or_authorize_an_order"] for p in out["packets"])


def test_supervisor_relay_backpressure_fails_closed():
    from decision_mirror import append_decision_event
    from store import initial_runtime
    from supervisor_relay import relay_health
    cfg=base_cfg(); cfg["supervisor"]["max_undelivered_events"]=10
    r=initial_runtime(100000.0)
    for i in range(11):
        append_decision_event(r,event_kind="NO_TRADE",subsystem="TEST",action=f"A{i}")
    h=relay_health(r,cfg)
    assert h["passed"] is False
    assert h["action"]=="BLOCK_NEW_ENTRIES_AND_ESCALATE"


def test_supervisor_counsel_rejects_direct_order_instruction():
    from supervisor_counsel import SupervisorCounselError, validate_counsel
    c={"recommendation":"NO_CHANGE","packet_hashes":["p1"],"verified_facts":[],"inference":[],
       "uses_current_market_claims":False,"direct_order_instruction":"BUY SPY"}
    try:
        validate_counsel(c,known_packet_hashes={"p1"})
        assert False,"expected SupervisorCounselError"
    except SupervisorCounselError:
        pass


def test_supervisor_counsel_requires_external_evidence_for_current_market_claims():
    from supervisor_counsel import SupervisorCounselError, validate_counsel
    c={"recommendation":"REQUEST_MORE_EVIDENCE","packet_hashes":["p1"],"verified_facts":["market moved"],
       "inference":[],"uses_current_market_claims":True,"external_evidence":[],"direct_order_instruction":None}
    try:
        validate_counsel(c,known_packet_hashes={"p1"})
        assert False,"expected SupervisorCounselError"
    except SupervisorCounselError:
        pass


def test_supervisor_counsel_valid_advice_has_zero_execution_authority():
    from supervisor_counsel import validate_counsel
    c={"recommendation":"HALT_NEW_ENTRIES","packet_hashes":["p1"],"verified_facts":["feed timestamps conflict"],
       "inference":["market view is unsafe"],"uses_current_market_claims":True,
       "external_evidence":[{"source":"provider-B","observed_at":"2026-09-22T10:00:00+00:00"}],
       "direct_order_instruction":None}
    r=validate_counsel(c,known_packet_hashes={"p1"})
    assert r["accepted_as_advisory"] is True
    assert r["execution_authority"]=="NONE"
    assert r["may_bypass_forge_or_constitution"] is False


def test_config_guard_rejects_fake_direct_supervisor_transport():
    from config_guard import ConfigError, validate_config
    cfg=base_cfg(); cfg["supervisor"]["transport_mode"]="direct_chatgpt_live"
    try:
        validate_config(cfg)
        assert False,"expected ConfigError"
    except ConfigError:
        pass


def test_runtime_schema_requires_decision_journal_and_delivery_state():
    from store import StateStoreError, initial_runtime, validate_runtime
    r=initial_runtime(1000.0)
    validate_runtime(r)
    broken=copy.deepcopy(r); broken.pop("decision_journal")
    try:
        validate_runtime(broken)
        assert False,"expected StateStoreError"
    except StateStoreError:
        pass


def test_build_manifest_covers_decision_mirror_relay_and_counsel():
    from build_guard import CRITICAL_FILES
    assert {"decision_mirror.py","supervisor_relay.py","supervisor_counsel.py"} <= set(CRITICAL_FILES)


def test_constitution_contains_supervisor_truth_and_backpressure_rules():
    from constitution import NON_NEGOTIABLES
    ids={x[0] for x in NON_NEGOTIABLES}
    assert {"DECISION_MIRROR","FACT_INFERENCE_SEPARATION","SUPERVISOR_ADVISORY_ONLY",
            "SUPERVISOR_EXTERNAL_VERIFICATION","RELAY_BACKPRESSURE"} <= ids


def test_supervisor_outbox_respects_ack_cursor_without_dropping_new_events():
    from decision_mirror import append_decision_event
    from store import initial_runtime
    from supervisor_relay import build_outbox_from_runtime
    cfg=base_cfg(); r=initial_runtime(100000.0)
    append_decision_event(r,event_kind="A",subsystem="TEST",action="A")
    append_decision_event(r,event_kind="B",subsystem="TEST",action="B")
    r["supervisor_delivery"]["last_ack_seq"]=1
    out=build_outbox_from_runtime(r,cfg,"manifest-test",persist=False)
    assert [p["decision_seq"] for p in out["packets"]]==[2]


def test_escalation_mirror_contains_evidence_not_only_pointer():
    from decision_mirror import mirror_trade_cycle
    from store import initial_runtime
    r=initial_runtime(100000.0)
    escalation={"id":"e1","reason":"STRATEGY_CONFLICT","symbol":"SPY","packet":{"truth":{"integrity_hash":"abc"}}}
    created=mirror_trade_cycle(r,cycle_number=1,health_preflight={"passed":True,"checks":[]},
                               proposals=[],cycle_audit=[],new_ledger_events=[],touched_escalations=["e1"],
                               escalation_items=[escalation],portfolio={"equity":100000},metrics={})
    e=next(x for x in created if x["event_kind"]=="ESCALATION_STATE_CHANGED")
    assert e["observed_facts"]["escalations"][0]["packet"]["truth"]["integrity_hash"]=="abc"


def test_chaos_diagnostics_attacks_supervisor_counsel_gate():
    import chaos_diagnostics
    r=chaos_diagnostics.run_chaos(seed=2,malformed_cases=10,sizing_cases=20,counsel_cases=50)
    assert r["unsafe_supervisor_counsel_accepts"]==0
    assert r["supervisor_counsel_crashes"]==0
    assert r["passed"] is True


def test_decision_mirror_redacts_credentials_from_payloads_and_strings():
    from decision_mirror import append_decision_event
    from store import initial_runtime
    r=initial_runtime(100000.0)
    e=append_decision_event(r,event_kind="ERROR",subsystem="TEST",action="REJECT",
                            observed_facts={"api_key":"abc123","error":"GET https://x.test?q=1&token=secret-token Authorization: Bearer abc.def"})
    dumped=json.dumps(e)
    assert "abc123" not in dumped
    assert "secret-token" not in dumped
    assert "abc.def" not in dumped
    assert "[REDACTED]" in dumped


def test_supervisor_delivery_ack_requires_valid_hmac_receipt():
    import os
    from supervisor_relay import StateStoreError, compute_ack_signature, acknowledgement_message
    key="k"*40
    sig=compute_ack_signature(7,"eventhash","resp",key)
    assert len(sig)==64
    assert acknowledgement_message(7,"eventhash","resp").startswith(b"Trip's|supervisor-ack|")


def test_constitution_requires_authenticated_supervisor_ack():
    from constitution import NON_NEGOTIABLES
    assert "AUTHENTICATED_SUPERVISOR_ACK" in {x[0] for x in NON_NEGOTIABLES}


def test_supervisor_packet_identity_is_deterministic_across_retries():
    from decision_mirror import append_decision_event
    from store import initial_runtime
    from supervisor_relay import build_outbox_from_runtime
    cfg=base_cfg(); r=initial_runtime(100000.0)
    append_decision_event(r,event_kind="NO_TRADE",subsystem="TEST",action="REJECT")
    a=build_outbox_from_runtime(r,cfg,"manifest",persist=False)["packets"][0]
    b=build_outbox_from_runtime(r,cfg,"manifest",persist=False)["packets"][0]
    assert a["packet_hash"]==b["packet_hash"]
    assert a["packet_id"]==b["packet_id"]


def test_signed_supervisor_ack_binds_sequence_to_authoritative_event_hash():
    import os
    import store
    from decision_mirror import append_decision_event
    from supervisor_relay import acknowledge_supervisor_delivery, compute_ack_signature
    old_data=store.DATA
    old_key=os.environ.get("TRIPS_SUPERVISOR_ACK_KEY")
    with tempfile.TemporaryDirectory() as td:
        store.DATA=Path(td)
        try:
            r=store.initial_runtime(100000.0)
            e=append_decision_event(r,event_kind="NO_TRADE",subsystem="TEST",action="REJECT")
            store.write_runtime(r)
            key="s"*40; os.environ["TRIPS_SUPERVISOR_ACK_KEY"]=key
            sig=compute_ack_signature(1,e["event_hash"],"resp",key)
            acknowledge_supervisor_delivery(1,e["event_hash"],"resp",sig)
            loaded=store.read_runtime(100000.0)
            assert loaded["supervisor_delivery"]["last_ack_seq"]==1
            assert loaded["supervisor_delivery"]["last_ack_event_hash"]==e["event_hash"]
        finally:
            store.DATA=old_data
            if old_key is None: os.environ.pop("TRIPS_SUPERVISOR_ACK_KEY",None)
            else: os.environ["TRIPS_SUPERVISOR_ACK_KEY"]=old_key


def test_daily_daemon_does_not_run_evolution_twice():
    text=(ROOT/"engine"/"health_daemon.py").read_text()
    assert "from evolution_engine import run_evolution_review" not in text
    assert "run_evolution_review()" not in text
    assert "build_supervisor_packet()" in text


def test_build_manifest_covers_dashboard_truth_surface():
    from build_guard import CRITICAL_FILES, CRITICAL_PROJECT_FILES
    assert "dashboard_export.py" in set(CRITICAL_FILES)
    assert {"docs/index.html","docs/styles.css","docs/app.js","docs/assets/trips-portrait.png","docs/TRIPS_CONSTITUTION.md"} <= set(CRITICAL_PROJECT_FILES)


def test_dashboard_export_never_claims_live_execution_or_health_trade_authority():
    import dashboard_export
    payload=dashboard_export.build_dashboard_payload()
    assert payload["system"]["live_execution_supported"] is False
    assert payload["system"]["health_is_trade_authority"] is False
    assert payload["system"]["trade_authority"]=="DATA_TRUTH + FORGE_GATE + CONSTITUTION"
    assert payload["strategy"]["live_trading_authorized"] is False


def test_dashboard_export_whitelists_decision_fields_and_redacts_secrets():
    from dashboard_export import _public_decision
    event={"ts":"x","seq":1,"event_id":"e","event_kind":"TEST","subsystem":"X","action":"LOG",
           "observed_facts":{"api_key":"supersecret"},"authorization":"Bearer abc.def","event_hash":"h"}
    public=_public_decision(event)
    dumped=json.dumps(public)
    assert "supersecret" not in dumped and "abc.def" not in dumped
    assert "observed_facts" not in public and "authorization" not in public


def test_dashboard_operator_surface_has_strict_csp_and_no_external_dependencies():
    html=(ROOT/"docs"/"index.html").read_text()
    js=(ROOT/"docs"/"app.js").read_text()
    assert "Content-Security-Policy" in html
    assert "default-src 'self'" in html and "script-src 'self'" in html and "connect-src 'self'" in html
    assert "https://" not in html and "http://" not in html
    assert "crypto.subtle.digest('SHA-256'" in js
    assert "innerHTML" not in js


def test_dashboard_background_uses_user_portrait_centered():
    css=(ROOT/"docs"/"styles.css").read_text()
    assert "trips-portrait.png" in css
    assert "background-position:center,center,54% 0%" in css


def test_security_diagnostics_scans_dashboard_js_and_css_extensions():
    text=(ROOT/"engine"/"security_diagnostics.py").read_text()
    assert '".css"' in text and '".js"' in text


def test_constitution_contains_operator_surface_truth_rule():
    from constitution import NON_NEGOTIABLES
    assert "OPERATOR_SURFACE_TRUTH" in {x[0] for x in NON_NEGOTIABLES}


def test_dashboard_server_is_local_only_by_default_and_read_only():
    text=(ROOT/"engine"/"dashboard_server.py").read_text()
    assert 'default="127.0.0.1"' in text
    assert "def do_POST" in text and "def do_PUT" in text and "def do_DELETE" in text
    assert "405" in text and "frame-ancestors 'none'" in text


def test_v05_to_v06_migration_refuses_open_or_pending_trades():
    from migrate_runtime_v05_to_v06 import LEGACY_V05_CONFIG_HASH, validate_migration_preconditions
    from store import initial_runtime
    r=initial_runtime(100000.0); r["portfolio"]["config_hash"]=LEGACY_V05_CONFIG_HASH
    r["portfolio"]["positions"]={"SPY":{"dummy":True}}
    try:
        validate_migration_preconditions(r,"new")
        assert False,"expected migration refusal"
    except RuntimeError:
        pass
    r=initial_runtime(100000.0); r["portfolio"]["config_hash"]=LEGACY_V05_CONFIG_HASH
    r["portfolio"]["pending_entries"]={"SPY":{"dummy":True}}
    try:
        validate_migration_preconditions(r,"new")
        assert False,"expected migration refusal"
    except RuntimeError:
        pass


def test_dashboard_missing_runtime_marks_supervisor_delivery_unverified():
    import dashboard_export, store
    old_store=store.DATA; old_dash=dashboard_export.DATA
    with tempfile.TemporaryDirectory() as td:
        store.DATA=Path(td); dashboard_export.DATA=Path(td)
        try:
            payload=dashboard_export.build_dashboard_payload()
            assert payload["system"]["runtime_integrity"]["present"] is False
            assert payload["supervisor"]["delivery_state"]=="UNVERIFIED"
        finally:
            store.DATA=old_store; dashboard_export.DATA=old_dash


def test_dashboard_dom_contract_has_unique_ids_and_matching_views():
    from html.parser import HTMLParser
    import re
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.ids=[]; self.nav=[]; self.panels=[]
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            if "id" in d: self.ids.append(d["id"])
            if "data-view" in d: self.nav.append(d["data-view"])
            if "data-view-panel" in d: self.panels.append(d["data-view-panel"])
    parser=Parser(); html=(ROOT/"docs"/"index.html").read_text(); parser.feed(html)
    assert len(parser.ids)==len(set(parser.ids))
    assert set(parser.nav)==set(parser.panels)
    refs=set(re.findall(r"\$\('([^']+)'\)",(ROOT/"docs"/"app.js").read_text()))
    assert refs <= set(parser.ids)
