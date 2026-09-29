"""Five-scenario $40,000 Trip's full-engine mock "empire audit".

TEST-ONLY. No credentials, broker endpoint or live authority.

Every scenario uses the approved production strategy/candle/risk/state components plus TASK,
Student, shadow evolution, decision mirror, ChatGPT supervisor relay/counsel boundary and the
one-way production supervisor bridge. P&L is observed, never a safety pass condition.

The phrase "empire audit" means capital-preservation / learning / regime-discipline testing. It
does NOT assert that any system can guarantee profits or eliminate trading losses.
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from build_guard import verify_build_integrity  # noqa: E402
from candle_intelligence import interpret as interpret_candles  # noqa: E402
from chaos_diagnostics import run_chaos  # noqa: E402
from config_guard import fingerprint_config, validate_config  # noqa: E402
from constitution import constitution_gate  # noqa: E402
from decision_mirror import append_decision_event  # noqa: E402
from forge_agent import advance_cooldown_on_market_bar, apply_position_management, mark_to_market  # noqa: E402
from providers import Bar  # noqa: E402
from risk import forge_gate, position_size  # noqa: E402
from store import append_hash_chained_event, initial_runtime, initial_state, validate_state, verify_hash_chain  # noqa: E402
from strategies import consensus, evaluate, features  # noqa: E402
from student_engine import (  # noqa: E402
    create_lesson, examiner, evolution_handoff, initial_student_state, observe_decision,
    pre_trade_recall, student_summary, verify_student_chain,
)
from supervisor_counsel import SupervisorCounselError, validate_counsel  # noqa: E402
from supervisor_relay import build_outbox_from_runtime  # noqa: E402
from execution.supervisor_bridge import default_supervisor_bridge  # noqa: E402
from execution.task_safety_kernel import (  # noqa: E402
    TASKContext, TASKKernel, TASKPolicy, TradeProposal, fingerprint_policy,
)
from execution.commodity_readiness import CommodityReadinessGate, CommodityUniversePolicy, fingerprint_universe_policy  # noqa: E402
from execution.commodity_truth import CommodityTruthGate, CommodityTruthPolicy, MarketSourceEvidence, fingerprint_truth_policy  # noqa: E402
from execution.contract_master import ContractMasterSnapshot, ContractSpec  # noqa: E402

STARTING_EQUITY = 40_000.0
WARMUP = 80
CYCLES = 240
SYMBOLS = ("SPY", "QQQ", "AAPL")
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _cfg():
    return validate_config(json.loads((ROOT / "engine" / "config.json").read_text()))


def _task():
    # Explicit stress-test envelope. These values are test inputs, never production defaults.
    p = TASKPolicy(
        policy_version="empire-audit-test-v1",
        max_price_deviation_bps=150.0,
        max_spread_bps=50.0,
        max_adv_participation_pct=0.01,
        max_messages_per_second=8,
        max_repeated_executions=3,
        repeated_execution_window_seconds=60,
        first_notice_buffer_days=5,
        last_trade_buffer_days=3,
        require_cancel_on_disconnect=True,
        require_self_match_prevention=True,
        allow_unbounded_market_orders=True,
        permitted_order_types=("MARKET", "LIMIT"),
        allowed_venue_states=("OPEN",),
        required_compliance_checks=("strong_auth", "venue_tag"),
    )
    return TASKKernel(p, approved_policy_hash=fingerprint_policy(p))


def _ohlcv(prev: float, close: float, rng: random.Random, volume: float, ts: datetime, *, shock_range: float = 0.0025) -> Bar:
    open_px = prev
    wiggle = abs(rng.gauss(0, shock_range))
    high = max(open_px, close) * (1.0 + wiggle)
    low = min(open_px, close) * (1.0 - wiggle)
    return Bar(ts.isoformat(), round(open_px, 4), round(high, 4), round(low, 4), round(close, 4), round(max(volume, 0.0), 2))


def _series(symbol: str, scenario: str) -> list[Bar]:
    seed = sum(ord(c) for c in f"{symbol}:{scenario}") + 40000
    rng = random.Random(seed)
    price = 90.0 + (sum(ord(c) for c in symbol) % 70)
    bars = []
    total = WARMUP + CYCLES + 1
    for i in range(total):
        t = START + timedelta(hours=i)
        # Warmup has mild mixed behaviour so indicators become fully initialized.
        if i < WARMUP:
            ret = 0.00025 + 0.0016 * math.sin(i / 9.0) + rng.gauss(0, 0.0022)
            vol = 1_200_000 * (1 + abs(rng.gauss(0, 0.15)))
        else:
            j = i - WARMUP
            if scenario == "TREND_COMPOUND":
                ret = 0.0012 + 0.0015 * math.sin(j / 12.0) + rng.gauss(0, 0.0020)
                vol = 1_600_000 * (1 + abs(rng.gauss(0, 0.20)))
            elif scenario == "RANGE_WHIPSAW":
                target = (100.0 + (sum(ord(c) for c in symbol) % 30)) * (1 + 0.025 * math.sin(j / 4.0))
                ret = (target / price - 1.0) * 0.25 + rng.gauss(0, 0.0038)
                vol = 1_100_000 * (1 + abs(rng.gauss(0, 0.35)))
            elif scenario == "CRASH_GAP_RECOVERY":
                if j < 70:
                    ret = 0.0008 + rng.gauss(0, 0.0022)
                elif j == 70:
                    ret = -0.115
                elif j < 110:
                    ret = -0.0025 + rng.gauss(0, 0.009)
                elif j < 180:
                    ret = 0.0028 + rng.gauss(0, 0.005)
                else:
                    ret = 0.0007 + rng.gauss(0, 0.0025)
                vol = 2_000_000 * (1 + abs(rng.gauss(0, 0.45)))
            elif scenario == "LIQUIDITY_TRUTH_ATTACK":
                ret = 0.00055 + 0.001 * math.sin(j / 10.0) + rng.gauss(0, 0.0028)
                # Repeated dry-liquidity windows.
                if 45 <= j < 70 or 135 <= j < 155:
                    vol = 120.0 + abs(rng.gauss(0, 30))
                else:
                    vol = 900_000 * (1 + abs(rng.gauss(0, 0.20)))
            elif scenario == "REGIME_ROTATION":
                if j < 55:
                    ret = 0.0015 + rng.gauss(0, 0.0020)
                elif j < 105:
                    ret = -0.0017 + rng.gauss(0, 0.0030)
                elif j < 160:
                    ret = rng.gauss(0, 0.010)
                elif j < 205:
                    ret = 0.0022 + rng.gauss(0, 0.0040)
                else:
                    ret = rng.gauss(0, 0.0022)
                vol = 1_500_000 * (1 + abs(rng.gauss(0, 0.50)))
            else:
                raise AssertionError(scenario)
        close = max(1.0, price * (1.0 + ret))
        bars.append(_ohlcv(price, close, rng, vol, t, shock_range=0.004 if scenario in {"CRASH_GAP_RECOVERY", "REGIME_ROTATION"} else 0.0025))
        price = close
    return bars


def _truth(symbol: str, cycle: int, scenario: str, *, trusted: bool = True) -> dict:
    return {
        "trusted_for_analysis": trusted,
        "trusted_for_trade": trusted,
        "source": f"synthetic-{scenario.lower()}",
        "source_family": "empire-audit-independent-pair",
        "integrity_hash": f"{scenario}-{symbol}-{cycle:04d}",
        "checks": [
            {"name": "declared_source_kind", "passed": True},
            {"name": "provider_kind_binding", "passed": True},
            {"name": "nonempty_source", "passed": True},
            {"name": "nonempty_source_family", "passed": True},
            {"name": "timezone_aware_timestamps", "passed": True},
            {"name": "strict_time_order", "passed": True},
            {"name": "finite_positive_prices", "passed": True},
            {"name": "ohlc_invariants", "passed": True},
            {"name": "nonnegative_volume", "passed": True},
            {"name": "extreme_move_review", "passed": True},
            {"name": "freshness", "passed": trusted},
        ],
        "cross_source": {"passed": trusted},
    }


def _task_context(bar: Bar, scenario: str, cycle: int):
    fault = None
    if scenario == "LIQUIDITY_TRUTH_ATTACK":
        fault_map = {
            50: "WIDE_SPREAD",
            65: "STALE_DATA",
            90: "BROKER_DISCONNECT",
            120: "VENUE_HALT",
            150: "COMPLIANCE_GAP",
            185: "MESSAGE_STORM",
        }
        fault = fault_map.get(cycle)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc) + timedelta(minutes=cycle)
    values = dict(
        market_data_healthy=True,
        venue_state="OPEN",
        observed_price=float(bar.close),
        spread_bps=10.0,
        average_daily_volume=max(float(bar.volume), 1.0),
        broker_connected=True,
        cancel_on_disconnect_active=True,
        instrument_kind="CASH",
        working_orders=(),
        recent_message_times=(),
        recent_execution_times=(),
        compliance={"strong_auth": True, "venue_tag": True},
        contract_lifecycle=None,
    )
    if fault == "WIDE_SPREAD":
        values["spread_bps"] = 90.0
    elif fault == "STALE_DATA":
        values["market_data_healthy"] = False
    elif fault == "BROKER_DISCONNECT":
        values["broker_connected"] = False
    elif fault == "VENUE_HALT":
        values["venue_state"] = "HALTED"
    elif fault == "COMPLIANCE_GAP":
        values["compliance"] = {"strong_auth": True}
    elif fault == "MESSAGE_STORM":
        values["recent_message_times"] = tuple((now - timedelta(milliseconds=50 * x)).isoformat() for x in range(8))
    return TASKContext(**values), now, fault



def _guaranteed_task_fault_probe(task: TASKKernel, scenario: str, bar: Bar) -> dict:
    """Force one or more execution-safety faults through TASK regardless of strategy timing."""
    base = dict(
        market_data_healthy=True, venue_state="OPEN", observed_price=float(bar.close),
        spread_bps=10.0, average_daily_volume=max(float(bar.volume), 10_000.0),
        broker_connected=True, cancel_on_disconnect_active=True, instrument_kind="CASH",
        working_orders=(), recent_message_times=(), recent_execution_times=(),
        compliance={"strong_auth": True, "venue_tag": True}, contract_lifecycle=None,
    )
    now = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    probes = {
        "TREND_COMPOUND": [("REPEATED_EXECUTION", {"recent_execution_times": tuple((now - timedelta(seconds=i + 1)).isoformat() for i in range(3))}, "REPEATED_EXECUTION_LIMIT")],
        "RANGE_WHIPSAW": [("SELF_MATCH", {"working_orders": ({"symbol": "SPY", "side": "SELL", "state": "OPEN"},)}, "SELF_MATCH_RISK")],
        "CRASH_GAP_RECOVERY": [("BROKER_DISCONNECT", {"broker_connected": False}, "BROKER_DISCONNECTED")],
        "LIQUIDITY_TRUTH_ATTACK": [
            ("WIDE_SPREAD", {"spread_bps": 90.0}, "SPREAD_TOO_WIDE"),
            ("STALE_DATA", {"market_data_healthy": False}, "MARKET_DATA_UNHEALTHY"),
            ("COMPLIANCE_GAP", {"compliance": {"strong_auth": True}}, "VENUE_COMPLIANCE_UNVERIFIED"),
        ],
        "REGIME_ROTATION": [
            ("VENUE_HALT", {"venue_state": "HALTED"}, "VENUE_STATE_NOT_ALLOWED"),
            ("MESSAGE_STORM", {"recent_message_times": tuple((now - timedelta(milliseconds=50 * i)).isoformat() for i in range(8))}, "MESSAGE_RATE_LIMIT"),
        ],
    }[scenario]
    outcomes = []
    for name, changes, expected in probes:
        values = dict(base)
        values.update(changes)
        ctx = TASKContext(**values)
        proposal = TradeProposal(
            symbol="SPY", side="BUY", desired_quantity=5, order_type="MARKET",
            limit_price=None, reference_price=float(bar.close), strategy_id="guaranteed-fault-probe",
        )
        decision = task.evaluate(proposal, context=ctx, now=now)
        passed = (not decision.allowed) and expected in decision.blocks
        outcomes.append({"probe": name, "expected": expected, "blocks": list(decision.blocks), "passed": passed})
    return {"cases": len(outcomes), "passed": sum(int(x["passed"]) for x in outcomes), "outcomes": outcomes}


def _record_student_exit(student: dict, scenario: str, symbol: str, entry_ctx: dict, exit_event: dict, build_hash: str, config_hash: str):
    return observe_decision(
        student,
        decision_event_ids=[entry_ctx["decision_id"]],
        symbol=symbol,
        interval="60min",
        provider_provenance={"source": entry_ctx["source"], "synthetic_test_only": True},
        features_known_at_decision=entry_ctx["features"],
        gate_outcomes={"forge": True, "constitution": True, "task": True},
        outcome={"pnl": float(exit_event.get("pnl", 0.0)), "process_violation": False},
        costs={"execution_cost_assumption_bps": entry_ctx["cost_bps"]},
        regime_labels=[entry_ctx["regime"], scenario],
        data_quality_flags=[],
        build_hash=build_hash,
        config_hash=config_hash,
        decision_ts=entry_ctx["decision_ts"],
    )


def _supervisor_record(cfg: dict, build_hash: str, scenario_report: dict):
    runtime = initial_runtime(STARTING_EQUITY)
    event = append_decision_event(
        runtime,
        event_kind="EMPIRE_AUDIT_SCENARIO_SUMMARY",
        subsystem="MOCK_STRESS_CAMPAIGN",
        action="REQUEST_CHATGPT_ADVISORY_REVIEW",
        observed_facts={
            "scenario": scenario_report["scenario"],
            "starting_equity": STARTING_EQUITY,
            "ending_equity": scenario_report["ending_equity"],
            "return_pct": scenario_report["return_pct"],
            "max_drawdown_pct": scenario_report["max_drawdown_pct"],
            "closed_trades": scenario_report["closed_trades"],
            "risk_rejections": scenario_report["risk_rejections"],
            "task_blocks": scenario_report["task_blocks"],
        },
        inference={"test_interpretation": "Synthetic evidence only; not proof of live profitability."},
        uncertainty="Mock/synthetic regime. Current live-market claims are intentionally absent.",
        requires_supervisor_review=True,
    )
    outbox = build_outbox_from_runtime(runtime, cfg, build_hash, persist=False)
    assert len(outbox["packets"]) == 1
    packet = outbox["packets"][0]
    assert packet["recipient_role"] == "ChatGPT supervisor"
    contract = packet["supervisor_contract"]
    assert contract["advice_is_advisory_only"] is True
    assert contract["cannot_submit_or_authorize_an_order"] is True
    assert contract["cannot_bypass_forge_or_constitution"] is True

    # The safe ChatGPT-side shape: evidence-linked, no current-market claims, no direct order.
    safe = {
        "recommendation": "REQUEST_MORE_EVIDENCE",
        "packet_hashes": [packet["packet_hash"]],
        "verified_facts": [
            f"synthetic ending_equity={scenario_report['ending_equity']}",
            f"synthetic max_drawdown_pct={scenario_report['max_drawdown_pct']}",
        ],
        "inference": ["one synthetic scenario cannot establish durable edge"],
        "uses_current_market_claims": False,
        "direct_order_instruction": None,
    }
    accepted = validate_counsel(safe, known_packet_hashes={packet["packet_hash"]})

    # A compromised/overreaching assistant instruction must be rejected.
    malicious = dict(safe)
    malicious["direct_order_instruction"] = "BUY SPY NOW"
    malicious_rejected = False
    try:
        validate_counsel(malicious, known_packet_hashes={packet["packet_hash"]})
    except SupervisorCounselError:
        malicious_rejected = True

    bridge = default_supervisor_bridge(
        truth=_truth("SPY", 9999, scenario_report["scenario"]),
        build_and_config_verified=True,
        risk_utilization={"used_pct": scenario_report["peak_capital_utilization_pct"] / 100.0},
        capital_utilization={"used_pct": scenario_report["peak_capital_utilization_pct"] / 100.0},
        recent_decisions=[scenario_report["scenario"]],
        system_health="OK",
        unexpected_behaviour_patterns=[],
        performance_deterioration=("OK" if scenario_report["return_pct"] >= 0 else "NEGATIVE_SYNTHETIC_PNL"),
        rejections=[f"risk:{scenario_report['risk_rejections']}", f"task:{scenario_report['task_blocks']}"],
    )
    bridge_result = bridge.run_once(now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert bridge_result["authority"]["execution_authority"] == "NONE"

    return {
        "decision_event_hash": event["event_hash"],
        "packet_id": packet["packet_id"],
        "packet_hash": packet["packet_hash"],
        "transport_state": outbox["transport_state"],
        "relay_health": outbox["relay_health"],
        "safe_counsel": accepted,
        "malicious_direct_order_rejected": malicious_rejected,
        "bridge": {
            "provider_id": bridge_result["provider_id"],
            "finding": bridge_result["outcome"]["finding"],
            "new_exposure": bridge_result["new_exposure"],
            "safety_action": bridge_result["safety_action"],
            "execution_authority": bridge_result["authority"]["execution_authority"],
        },
    }


def _run_one(scenario: str, cfg: dict, build_hash: str, config_hash: str) -> dict:
    barsets = {s: _series(s, scenario) for s in SYMBOLS}
    state = initial_state(STARTING_EQUITY)
    state["risk_day_start_equity"] = STARTING_EQUITY
    task = _task()
    student = initial_student_state()
    pending = {}
    ledger = []
    entry_context = {}
    processed_exit_count = 0
    equity_curve = [STARTING_EQUITY]
    signal_candidates = queued = risk_rejections = constitution_rejections = 0
    task_blocks = task_reductions = 0
    recalls = 0
    fault_attempts = fault_blocks = 0
    peak_capital_utilization = 0.0
    max_positions = 0

    for cycle in range(CYCLES):
        i = WARMUP + cycle
        current = {s: barsets[s][i] for s in SYMBOLS}
        advance_cooldown_on_market_bar(state, max(b.ts for b in current.values()))

        for symbol in list(state["positions"]):
            apply_position_management(symbol, current[symbol], state, ledger, cfg)

        # Teach Student only from completed outcomes, never from unrealized assumptions.
        exits = [x for x in ledger if x.get("type") == "PAPER_EXIT"]
        for exit_event in exits[processed_exit_count:]:
            symbol = exit_event["symbol"]
            ctx = entry_context.pop(symbol, None)
            if ctx:
                _record_student_exit(student, scenario, symbol, ctx, exit_event, build_hash, config_hash)
        processed_exit_count = len(exits)

        latest = {s: current[s].close for s in SYMBOLS}
        _, exposure, _ = mark_to_market(state, latest)
        peak_capital_utilization = max(peak_capital_utilization, exposure / STARTING_EQUITY * 100.0)
        drawdown = 1.0 - state["equity"] / max(state["peak_equity"], 1e-9)

        # Previous-close proposals may fill only at the new bar open.
        for symbol in sorted(list(pending)):
            p = pending.pop(symbol)
            if symbol in state["positions"]:
                continue
            truth = _truth(symbol, cycle, scenario)
            gate = forge_gate(
                config=cfg, provider_name=truth["source"], bars_count=i + 1,
                signal_score=p["signal_score"], conflict=p["conflict"], stale=False,
                positions=state["positions"], pending_entries=pending, symbol=symbol,
                daily_pnl=min(state.get("daily_pnl", 0.0), state["equity"] - state["risk_day_start_equity"]),
                equity=state["equity"], drawdown_pct=drawdown,
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"],
                cooldown_remaining=state.get("cooldown_remaining", 0),
                global_halt=state.get("halted", False),
                agreement_count=p["agreement_count"], candle_context=p["candle_context"],
            )
            if not gate["passed"]:
                risk_rejections += 1
                continue
            constitution = constitution_gate(
                truth=truth, mode=cfg["mode"], gate_passed=True, model_conflict=p["conflict"],
                anomaly=False, requires_verified_trade_data=True,
            )
            if not constitution["passed"]:
                constitution_rejections += 1
                continue

            bar = current[symbol]
            cost_bps = cfg["risk"]["assumed_slippage_bps"] + cfg["risk"]["assumed_spread_bps"] / 2.0
            entry = bar.open * (1 + cost_bps / 10000.0)
            stop = entry - cfg["trade"]["atr_stop_multiple"] * p["atr"]
            target = entry + cfg["trade"]["target_r_multiple"] * (entry - stop)
            desired = position_size(
                state["equity"], entry, stop, cfg["risk"]["max_risk_per_trade_pct"],
                cfg["risk"]["max_total_exposure_pct"], exposure,
            )
            desired = min(desired, int(state["cash"] / entry))
            if desired <= 0:
                risk_rejections += 1
                continue

            proposal = TradeProposal(
                symbol=symbol, side="BUY", desired_quantity=desired, order_type="MARKET",
                limit_price=None, reference_price=p["signal_close"], strategy_id=p["strategy"],
            )
            task_ctx, now, injected_fault = _task_context(bar, scenario, cycle)
            if injected_fault:
                fault_attempts += 1
            decision = task.evaluate(proposal, context=task_ctx, now=now)
            if not decision.allowed:
                task_blocks += 1
                if injected_fault:
                    fault_blocks += 1
                continue
            if decision.approved_quantity < desired:
                task_reductions += 1
            qty = decision.approved_quantity
            assert 0 < qty <= desired

            # Student recall is advisory only and cannot alter qty or gates.
            recall = pre_trade_recall(student, {
                "symbol": symbol, "interval": "60min", "regime_labels": [p["regime"], scenario],
                "features_known_at_decision": p["features"],
            })
            recalls += 1
            assert recall["authority"] == "RESEARCH_ONLY"
            before_qty = qty

            notional = entry * qty
            assert notional <= state["cash"] + 1e-9
            state["cash"] -= notional
            state["positions"][symbol] = {
                "entry": round(entry, 6), "qty": qty, "initial_stop": round(stop, 6), "stop": round(stop, 6),
                "target": round(target, 6), "atr_at_entry": p["atr"], "strategy": p["strategy"],
                "signal_score": p["signal_score"], "opened_at": now.isoformat(),
                "signal_bar_ts": p["signal_ts"], "fill_bar_ts": bar.ts, "last_processed_bar_ts": None,
                "protected": False, "trailing": False, "data_integrity_hash": truth["integrity_hash"],
                "source": truth["source"], "execution_cost_assumption_bps": cost_bps,
            }
            assert qty == before_qty
            decision_id = f"{scenario}:{symbol}:{cycle}:{len(ledger)}"
            entry_context[symbol] = {
                "decision_id": decision_id, "source": truth["source"], "cost_bps": cost_bps,
                "decision_ts": p["signal_ts"], "regime": p["regime"], "features": p["features"],
            }
            append_hash_chained_event(ledger, {
                "ts": now.isoformat(), "market_bar_ts": bar.ts, "symbol": symbol, "type": "PAPER_ENTRY",
                "entry": round(entry, 6), "qty": qty, "stop": round(stop, 6), "target": round(target, 6),
                "strategy": p["strategy"], "signal_score": round(p["signal_score"], 4),
                "provider": truth["source"], "execution_cost_assumption_bps": cost_bps,
                "causal_fill": "next_bar_open_after_signal", "decision_id": decision_id,
            })
            exposure += notional
            apply_position_management(symbol, bar, state, ledger, cfg)
            _, exposure, _ = mark_to_market(state, latest)

        validate_state(state)
        max_positions = max(max_positions, len(state["positions"]))
        peak_capital_utilization = max(peak_capital_utilization, exposure / STARTING_EQUITY * 100.0)

        # New analysis at the CLOSED bar.
        for symbol in SYMBOLS:
            if symbol in state["positions"] or symbol in pending:
                continue
            hist = barsets[symbol][: i + 1]
            f = features(hist)
            c = consensus(evaluate(f))
            candle = interpret_candles(hist)
            if c["direction"] != "LONG":
                continue
            signal_candidates += 1
            gate = forge_gate(
                config=cfg, provider_name=f"synthetic-{scenario.lower()}", bars_count=len(hist),
                signal_score=float(c["signal_score"]), conflict=bool(c["conflict"]), stale=False,
                positions=state["positions"], pending_entries=pending, symbol=symbol,
                daily_pnl=min(state.get("daily_pnl", 0.0), state["equity"] - state["risk_day_start_equity"]),
                equity=state["equity"],
                drawdown_pct=1.0 - state["equity"] / max(state["peak_equity"], 1e-9),
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"],
                cooldown_remaining=state.get("cooldown_remaining", 0), global_halt=state.get("halted", False),
                agreement_count=int(c["agreement_count"]), candle_context=candle.label,
            )
            if not gate["passed"]:
                risk_rejections += 1
                continue
            con = constitution_gate(
                truth=_truth(symbol, cycle, scenario), mode=cfg["mode"], gate_passed=True,
                model_conflict=bool(c["conflict"]), anomaly=False, requires_verified_trade_data=True,
            )
            if not con["passed"]:
                constitution_rejections += 1
                continue
            pending[symbol] = {
                "signal_ts": current[symbol].ts, "signal_close": current[symbol].close,
                "signal_score": float(c["signal_score"]), "conflict": bool(c["conflict"]),
                "agreement_count": int(c["agreement_count"]), "strategy": c["strategy"],
                "atr": float(f.atr14), "candle_context": candle.label, "regime": f.regime,
                "features": {"regime": f.regime, "rsi14": round(f.rsi14, 3), "z20": round(f.z20, 3),
                             "volume_ratio": round(f.volume_ratio, 3), "signal_score": round(float(c["signal_score"]), 4)},
            }
            queued += 1

        mark_to_market(state, latest)
        validate_state(state)
        equity_curve.append(float(state["equity"]))

    # Process any final exits produced in last cycle.
    exits = [x for x in ledger if x.get("type") == "PAPER_EXIT"]
    for exit_event in exits[processed_exit_count:]:
        symbol = exit_event["symbol"]
        ctx = entry_context.pop(symbol, None)
        if ctx:
            _record_student_exit(student, scenario, symbol, ctx, exit_event, build_hash, config_hash)

    validate_state(state)
    assert verify_hash_chain(ledger)
    assert verify_student_chain(student)

    entries = [x for x in ledger if x.get("type") == "PAPER_ENTRY"]
    exits = [x for x in ledger if x.get("type") == "PAPER_EXIT"]
    wins = [x for x in exits if float(x["pnl"]) > 0]
    realized = sum(float(x["pnl"]) for x in exits)
    ending = float(state["equity"])
    curve_peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        curve_peak = max(curve_peak, value)
        max_dd = max(max_dd, 1.0 - value / curve_peak)

    student_report = student_summary(student)
    lesson = create_lesson(
        episode_ids=[e["episode_id"] for e in student["episodes"]],
        hypothesis=f"{scenario} observations may inform a challenger, never the champion directly",
        baseline="approved champion",
        sample_size=len(exits),
        oos=False,
        walk_forward_windows=0,
        cost_sensitivity={"configured_costs_applied": True},
        failure_modes=[],
    )
    exam = examiner(lesson)
    promotion_blocked = False
    try:
        evolution_handoff(lesson, exam)
    except ValueError:
        promotion_blocked = True

    fault_probe = _guaranteed_task_fault_probe(task, scenario, barsets["SPY"][WARMUP + CYCLES - 1])
    assert fault_probe["passed"] == fault_probe["cases"]

    report = {
        "scenario": scenario,
        "starting_equity": STARTING_EQUITY,
        "ending_equity": round(ending, 2),
        "net_pnl": round(ending - STARTING_EQUITY, 2),
        "return_pct": round((ending / STARTING_EQUITY - 1.0) * 100.0, 4),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(ending - STARTING_EQUITY - realized, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "signal_candidates": signal_candidates,
        "queued_orders": queued,
        "entries": len(entries),
        "closed_trades": len(exits),
        "wins": len(wins),
        "losses": len(exits) - len(wins),
        "win_rate_pct": round((len(wins) / len(exits) * 100.0) if exits else 0.0, 4),
        "open_positions_at_end": len(state["positions"]),
        "max_concurrent_positions": max_positions,
        "risk_rejections": risk_rejections,
        "constitution_rejections": constitution_rejections,
        "task_blocks": task_blocks,
        "task_reductions": task_reductions,
        "guaranteed_task_fault_probe": fault_probe,
        "fault_attempts_reaching_task": fault_attempts,
        "fault_blocks": fault_blocks,
        "student": student_report,
        "student_recalls_before_execution": recalls,
        "shadow_evolution_exam": exam,
        "shadow_promotion_blocked": promotion_blocked,
        "ledger_hash_chain_valid": True,
        "student_hash_chain_valid": True,
        "peak_capital_utilization_pct": round(peak_capital_utilization, 4),
        "live_orders_submitted": 0,
        "real_money_used": 0,
    }
    report["chatgpt_supervisor"] = _supervisor_record(cfg, build_hash, report)
    return report


def _commodity_probe():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    spec = ContractSpec(
        contract_symbol="ZXZ26", root_symbol="ZX", series_kind="EXECUTABLE_CONTRACT",
        venue="XTEST", sector="METALS", currency="USD", contract_multiplier=100.0,
        tick_size=0.1, settlement_type="PHYSICAL", first_notice_date="2026-12-20",
        last_trade_date="2026-12-27", expiration_date="2026-12-28",
        source_id="mock-exchange-master", observed_at=now.isoformat(),
    )
    snap = ContractMasterSnapshot(source_name="mock-exchange", as_of=now.isoformat(), contracts=(spec,))
    tp = CommodityTruthPolicy("empire-truth", True, True, 30, 20.0)
    truth = CommodityTruthGate(tp, approved_policy_hash=fingerprint_truth_policy(tp))
    up = CommodityUniversePolicy("empire-universe", ("ZX",), ("XTEST",), ("METALS",), 24)
    gate = CommodityReadinessGate(
        up, approved_policy_hash=fingerprint_universe_policy(up),
        approved_contract_master_hash=snap.fingerprint, truth_gate=truth,
    )
    primary = MarketSourceEvidence(
        "p", "family-a", "REALTIME", "ZXZ26", now.isoformat(), "OPEN",
        99.9, 100.1, 100.0, 10_000.0, 25_000.0,
    )
    secondary = MarketSourceEvidence(
        "s", "family-b", "REALTIME", "ZXZ26", now.isoformat(), "OPEN",
        99.91, 100.09, 100.0, 9_800.0, 24_000.0,
    )
    d = gate.evaluate("ZXZ26", snapshot=snap, primary=primary, secondary=secondary, now=now)
    assert d.eligible_for_analysis
    assert d.to_dict()["execution_authority"] is False
    return {"eligible_for_analysis": True, "execution_authority": False, "contract_master_hash": snap.fingerprint}


def run_audit():
    cfg = _cfg()
    build = verify_build_integrity()
    build_hash = build["manifest_hash"]
    config_hash = fingerprint_config(cfg)
    scenarios = [
        "TREND_COMPOUND",
        "RANGE_WHIPSAW",
        "CRASH_GAP_RECOVERY",
        "LIQUIDITY_TRUTH_ATTACK",
        "REGIME_ROTATION",
    ]
    results = [_run_one(name, cfg, build_hash, config_hash) for name in scenarios]

    # Brute-force engine-wide adversarial campaign in addition to scenario-specific faults.
    chaos = run_chaos(seed=40000, malformed_cases=5000, sizing_cases=100000, counsel_cases=5000)
    commodity = _commodity_probe()

    profitable = sum(1 for x in results if x["net_pnl"] > 0)
    total_end = sum(x["ending_equity"] for x in results)
    aggregate = {
        "campaign": "TRIPS_EMPIRE_AUDIT_40000_X5",
        "scenarios": len(results),
        "starting_equity_each": STARTING_EQUITY,
        "total_mock_capital_across_independent_runs": STARTING_EQUITY * len(results),
        "total_ending_equity_across_independent_runs": round(total_end, 2),
        "aggregate_net_pnl": round(total_end - STARTING_EQUITY * len(results), 2),
        "profitable_scenarios": profitable,
        "loss_scenarios": len(results) - profitable,
        "worst_scenario_return_pct": min(x["return_pct"] for x in results),
        "best_scenario_return_pct": max(x["return_pct"] for x in results),
        "worst_max_drawdown_pct": max(x["max_drawdown_pct"] for x in results),
        "total_closed_trades": sum(x["closed_trades"] for x in results),
        "total_risk_rejections": sum(x["risk_rejections"] for x in results),
        "total_task_blocks": sum(x["task_blocks"] for x in results),
        "all_ledgers_valid": all(x["ledger_hash_chain_valid"] for x in results),
        "all_student_chains_valid": all(x["student_hash_chain_valid"] for x in results),
        "all_shadow_promotions_blocked_without_evidence": all(x["shadow_promotion_blocked"] for x in results),
        "all_guaranteed_task_fault_probes_passed": all(x["guaranteed_task_fault_probe"]["passed"] == x["guaranteed_task_fault_probe"]["cases"] for x in results),
        "all_chatgpt_packets_advisory_only": all(
            x["chatgpt_supervisor"]["safe_counsel"]["execution_authority"] == "NONE"
            and x["chatgpt_supervisor"]["malicious_direct_order_rejected"]
            for x in results
        ),
        "chaos": chaos,
        "commodity_readiness_probe": commodity,
        "live_orders_submitted": 0,
        "real_money_used": 0,
        "profitability_conclusion": "UNPROVEN_SYNTHETIC_ONLY",
        "empire_claim": "NO_GUARANTEE_POSSIBLE; evaluate preservation, selectivity, adaptability and evidence quality instead.",
    }
    out = {
        "schema_version": 1,
        "generated_for": "Trip's five-scenario mock empire audit",
        "build_hash": build_hash,
        "config_hash": config_hash,
        "engine_coverage": {
            "strategy_features_votes_consensus": True,
            "candle_intelligence": True,
            "forge_risk_gate": True,
            "constitution": True,
            "position_sizing": True,
            "paper_execution_stop_target_trailing": True,
            "TASK": True,
            "state_and_hash_chain": True,
            "Student_recall_and_post_trade_autopsy": True,
            "shadow_evolution_examiner": True,
            "decision_mirror": True,
            "ChatGPT_supervisor_relay": True,
            "supervisor_counsel_validation": True,
            "one_way_production_supervisor_bridge": True,
            "commodity_readiness": True,
            "chaos_diagnostics": True,
        },
        "aggregate": aggregate,
        "scenario_results": results,
        "notice": "Synthetic accelerated engineering evidence only; not evidence or guarantee of live-market profitability.",
    }
    print("EMPIRE_AUDIT_FINAL=" + json.dumps(out, sort_keys=True))

    assert chaos["passed"] is True
    assert aggregate["all_ledgers_valid"]
    assert aggregate["all_student_chains_valid"]
    assert aggregate["all_shadow_promotions_blocked_without_evidence"]
    assert aggregate["all_guaranteed_task_fault_probes_passed"]
    assert aggregate["all_chatgpt_packets_advisory_only"]
    assert aggregate["live_orders_submitted"] == 0
    assert aggregate["real_money_used"] == 0
    return out


def test_empire_audit_40000_five_scenarios():
    out = run_audit()
    assert out["aggregate"]["scenarios"] == 5
    assert out["aggregate"]["starting_equity_each"] == 40_000.0


if __name__ == "__main__":
    run_audit()
