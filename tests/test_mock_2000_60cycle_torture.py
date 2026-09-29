"""Deterministic $2,000 / 60-cycle mock performance + safety torture campaign.

This is test-only. It uses synthetic market bars, no credentials, no broker endpoint and no live
authority. Each accelerated cycle represents one validated 60-minute Trip's bar.

The campaign deliberately combines:
- production strategy features/votes/consensus,
- candle intelligence,
- Forge risk gate + Constitution gate,
- production position sizing,
- production paper position management / stops / targets / costs,
- TASK at the execution boundary,
- state validation and hash-chained ledger,
- explicit TASK fault injections,
- high-volume built-in chaos diagnostics.

No profitability threshold is asserted. Safety/invariant violations fail the test; P&L is an
observation, not a pass condition.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from candle_intelligence import interpret as interpret_candles  # noqa: E402
from chaos_diagnostics import run_chaos  # noqa: E402
from config_guard import validate_config  # noqa: E402
from constitution import constitution_gate  # noqa: E402
from forge_agent import (  # noqa: E402
    advance_cooldown_on_market_bar,
    apply_position_management,
    mark_to_market,
)
from providers import DemoProvider  # noqa: E402
from risk import forge_gate, position_size  # noqa: E402
from store import append_hash_chained_event, initial_state, validate_state, verify_hash_chain  # noqa: E402
from strategies import consensus, evaluate, features  # noqa: E402
from execution.task_safety_kernel import (  # noqa: E402
    BLOCKED,
    TASKContext,
    TASKKernel,
    TASKPolicy,
    TradeProposal,
    fingerprint_policy,
)

STARTING_EQUITY = 2000.0
CYCLES = 60
WARMUP_BARS = 60
SYMBOLS = ("SPY", "QQQ", "AAPL")


def _config():
    return validate_config(json.loads((ROOT / "engine" / "config.json").read_text()))


def _task():
    # Explicit synthetic-test policy only. These are not production thresholds.
    policy = TASKPolicy(
        policy_version="mock-2000-60cycle-v1",
        max_price_deviation_bps=100.0,
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
    return TASKKernel(policy, approved_policy_hash=fingerprint_policy(policy))


def _truth_stub(symbol: str, cycle: int) -> dict:
    # Synthetic test evidence with every anomaly-sensitive check explicitly represented.
    checks = [
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
        {"name": "freshness", "passed": True},
    ]
    return {
        "trusted_for_analysis": True,
        "trusted_for_trade": True,
        "source": "mock-dual-source",
        "source_family": "mock-independent-pair",
        "integrity_hash": f"mock-{symbol}-{cycle:02d}",
        "checks": checks,
        "cross_source": {"passed": True},
    }


def _task_context(bar, *, cycle: int, fault: str | None = None):
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
    now = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=cycle)
    if fault == "STALE_DATA":
        values["market_data_healthy"] = False
    elif fault == "WIDE_SPREAD":
        values["spread_bps"] = 75.0
    elif fault == "BROKER_DISCONNECT":
        values["broker_connected"] = False
    elif fault == "COD_MISSING":
        values["cancel_on_disconnect_active"] = False
    elif fault == "VENUE_HALT":
        values["venue_state"] = "HALTED"
    elif fault == "MESSAGE_STORM":
        values["recent_message_times"] = tuple(
            (now - timedelta(milliseconds=i * 50)).isoformat() for i in range(8)
        )
    elif fault == "REPEAT_STORM":
        values["recent_execution_times"] = tuple(
            (now - timedelta(seconds=i + 1)).isoformat() for i in range(3)
        )
    elif fault == "SELF_MATCH":
        values["working_orders"] = ({"symbol": "SPY", "side": "SELL", "state": "OPEN"},)
    elif fault == "COMPLIANCE_GAP":
        values["compliance"] = {"strong_auth": True}
    return TASKContext(**values), now


def _task_fault_campaign(kernel, sample_bar):
    faults = (
        ("STALE_DATA", "MARKET_DATA_UNHEALTHY"),
        ("WIDE_SPREAD", "SPREAD_TOO_WIDE"),
        ("BROKER_DISCONNECT", "BROKER_DISCONNECTED"),
        ("COD_MISSING", "CANCEL_ON_DISCONNECT_UNVERIFIED"),
        ("VENUE_HALT", "VENUE_STATE_NOT_ALLOWED"),
        ("MESSAGE_STORM", "MESSAGE_RATE_LIMIT"),
        ("REPEAT_STORM", "REPEATED_EXECUTION_LIMIT"),
        ("SELF_MATCH", "SELF_MATCH_RISK"),
        ("COMPLIANCE_GAP", "VENUE_COMPLIANCE_UNVERIFIED"),
    )
    passed = 0
    results = []
    for idx, (fault, expected) in enumerate(faults):
        ctx, now = _task_context(sample_bar, cycle=idx, fault=fault)
        proposal = TradeProposal(
            symbol="SPY", side="BUY", desired_quantity=5,
            order_type="MARKET", limit_price=None, reference_price=float(sample_bar.close),
            strategy_id="fault-probe",
        )
        decision = kernel.evaluate(proposal, context=ctx, now=now)
        ok = decision.status == BLOCKED and expected in decision.blocks
        passed += int(ok)
        results.append({"fault": fault, "expected": expected, "blocks": list(decision.blocks), "passed": ok})
    return {"cases": len(faults), "passed": passed, "results": results}


def run_campaign() -> dict:
    cfg = _config()
    provider = DemoProvider()
    bars = {symbol: provider.bars(symbol, WARMUP_BARS + CYCLES + 1) for symbol in SYMBOLS}
    state = initial_state(STARTING_EQUITY)
    state["risk_day_start_equity"] = STARTING_EQUITY
    ledger = []
    task = _task()

    pending = {}
    peak = STARTING_EQUITY
    equity_curve = [STARTING_EQUITY]
    signal_candidates = 0
    queued = 0
    risk_rejections = 0
    constitution_rejections = 0
    task_blocks = 0
    task_reductions = 0
    causal_fill_violations = 0
    max_position_count = 0

    for cycle in range(CYCLES):
        i = WARMUP_BARS + cycle
        current = {symbol: bars[symbol][i] for symbol in SYMBOLS}

        # Cooldown advances on market evidence, never on wall-clock loops.
        advance_cooldown_on_market_bar(state, max(bar.ts for bar in current.values()))

        # First manage positions that existed before this bar.
        for symbol in list(state["positions"]):
            apply_position_management(symbol, current[symbol], state, ledger, cfg)

        latest = {symbol: current[symbol].close for symbol in SYMBOLS}
        _, exposure, _ = mark_to_market(state, latest)
        drawdown = 1.0 - state["equity"] / max(state["peak_equity"], 1e-9)

        # Prior-close proposals may fill only at this bar's open.
        for symbol in sorted(list(pending)):
            p = pending.pop(symbol)
            if symbol in state["positions"]:
                continue

            truth = _truth_stub(symbol, cycle)
            gate = forge_gate(
                config=cfg,
                provider_name="mock-dual-source",
                bars_count=i + 1,
                signal_score=float(p["signal_score"]),
                conflict=bool(p["conflict"]),
                stale=False,
                positions=state["positions"],
                pending_entries={k: v for k, v in pending.items() if k != symbol},
                symbol=symbol,
                daily_pnl=min(
                    state.get("daily_pnl", 0.0),
                    state["equity"] - state.get("risk_day_start_equity", state["equity"]),
                ),
                equity=state["equity"],
                drawdown_pct=drawdown,
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"],
                cooldown_remaining=state.get("cooldown_remaining", 0),
                global_halt=state.get("halted", False),
                agreement_count=int(p["agreement_count"]),
                candle_context=p["candle_context"],
            )
            if not gate["passed"]:
                risk_rejections += 1
                continue

            constitution = constitution_gate(
                truth=truth,
                mode=cfg["mode"],
                gate_passed=gate["passed"],
                model_conflict=bool(p["conflict"]),
                anomaly=False,
                requires_verified_trade_data=True,
            )
            if not constitution["passed"]:
                constitution_rejections += 1
                continue

            bar = current[symbol]
            cost_bps = cfg["risk"]["assumed_slippage_bps"] + cfg["risk"]["assumed_spread_bps"] / 2.0
            entry = float(bar.open) * (1.0 + cost_bps / 10000.0)
            stop = entry - cfg["trade"]["atr_stop_multiple"] * float(p["atr"])
            target = entry + cfg["trade"]["target_r_multiple"] * (entry - stop)
            desired = position_size(
                state["equity"], entry, stop,
                cfg["risk"]["max_risk_per_trade_pct"],
                cfg["risk"]["max_total_exposure_pct"],
                exposure,
            )
            desired = min(desired, int(state["cash"] / entry) if entry > 0 else 0)
            if desired <= 0:
                risk_rejections += 1
                continue

            proposal = TradeProposal(
                symbol=symbol, side="BUY", desired_quantity=desired,
                order_type="MARKET", limit_price=None,
                reference_price=float(p["signal_close"]), strategy_id=str(p["strategy"]),
            )
            ctx, now = _task_context(bar, cycle=cycle)
            decision = task.evaluate(proposal, context=ctx, now=now)
            if not decision.allowed:
                task_blocks += 1
                continue
            if decision.approved_quantity < desired:
                task_reductions += 1
            qty = decision.approved_quantity
            assert qty <= desired

            # Causality: queued on previous close, filled at this bar open.
            if p["signal_ts"] == bar.ts:
                causal_fill_violations += 1

            notional = entry * qty
            if notional > state["cash"] + 1e-9:
                raise AssertionError("mock fill exceeds available cash")
            state["cash"] -= notional
            state["positions"][symbol] = {
                "entry": round(entry, 6),
                "qty": qty,
                "initial_stop": round(stop, 6),
                "stop": round(stop, 6),
                "target": round(target, 6),
                "atr_at_entry": float(p["atr"]),
                "strategy": p["strategy"],
                "signal_score": float(p["signal_score"]),
                "opened_at": now.isoformat(),
                "signal_bar_ts": p["signal_ts"],
                "fill_bar_ts": bar.ts,
                "last_processed_bar_ts": None,
                "protected": False,
                "trailing": False,
                "data_integrity_hash": truth["integrity_hash"],
                "source": truth["source"],
                "execution_cost_assumption_bps": cost_bps,
            }
            append_hash_chained_event(ledger, {
                "ts": now.isoformat(), "market_bar_ts": bar.ts, "symbol": symbol,
                "type": "PAPER_ENTRY", "entry": round(entry, 6), "qty": qty,
                "stop": round(stop, 6), "target": round(target, 6),
                "strategy": p["strategy"], "signal_score": round(float(p["signal_score"]), 4),
                "provider": truth["source"], "execution_cost_assumption_bps": cost_bps,
                "causal_fill": "next_bar_open_after_signal",
            })
            exposure += notional
            # Same bar may hit a stop/target after the opening fill.
            apply_position_management(symbol, bar, state, ledger, cfg)
            _, exposure, _ = mark_to_market(state, latest)

        validate_state(state)
        max_position_count = max(max_position_count, len(state["positions"]))

        # New decisions are created only from this bar after it has closed.
        for symbol in SYMBOLS:
            if symbol in state["positions"] or symbol in pending:
                continue
            hist = bars[symbol][: i + 1]
            f = features(hist)
            c = consensus(evaluate(f))
            candle = interpret_candles(hist)
            if c["direction"] != "LONG":
                continue
            signal_candidates += 1

            drawdown = 1.0 - state["equity"] / max(state["peak_equity"], 1e-9)
            gate = forge_gate(
                config=cfg,
                provider_name="mock-dual-source",
                bars_count=len(hist),
                signal_score=float(c["signal_score"]),
                conflict=bool(c["conflict"]),
                stale=False,
                positions=state["positions"],
                pending_entries=pending,
                symbol=symbol,
                daily_pnl=min(
                    state.get("daily_pnl", 0.0),
                    state["equity"] - state.get("risk_day_start_equity", state["equity"]),
                ),
                equity=state["equity"],
                drawdown_pct=drawdown,
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"],
                cooldown_remaining=state.get("cooldown_remaining", 0),
                global_halt=state.get("halted", False),
                agreement_count=int(c["agreement_count"]),
                candle_context=candle.label,
            )
            if not gate["passed"]:
                risk_rejections += 1
                continue
            constitution = constitution_gate(
                truth=_truth_stub(symbol, cycle),
                mode=cfg["mode"],
                gate_passed=True,
                model_conflict=bool(c["conflict"]),
                anomaly=False,
                requires_verified_trade_data=True,
            )
            if not constitution["passed"]:
                constitution_rejections += 1
                continue
            pending[symbol] = {
                "signal_ts": current[symbol].ts,
                "signal_close": current[symbol].close,
                "signal_score": float(c["signal_score"]),
                "conflict": bool(c["conflict"]),
                "agreement_count": int(c["agreement_count"]),
                "strategy": c["strategy"],
                "atr": float(f.atr14),
                "candle_context": candle.label,
            }
            queued += 1

        _, _, _ = mark_to_market(state, latest)
        validate_state(state)
        peak = max(peak, state["equity"])
        equity_curve.append(state["equity"])

    # Mark surviving positions to the final close; do not manufacture a liquidation.
    final_latest = {symbol: bars[symbol][WARMUP_BARS + CYCLES - 1].close for symbol in SYMBOLS}
    _, _, _ = mark_to_market(state, final_latest)
    validate_state(state)

    entries = [x for x in ledger if x.get("type") == "PAPER_ENTRY"]
    exits = [x for x in ledger if x.get("type") == "PAPER_EXIT"]
    wins = [x for x in exits if float(x.get("pnl", 0)) > 0]
    losses = [x for x in exits if float(x.get("pnl", 0)) < 0]
    realized_pnl = sum(float(x.get("pnl", 0)) for x in exits)
    final_equity = float(state["equity"])
    unrealized = final_equity - STARTING_EQUITY - realized_pnl
    max_dd = 0.0
    curve_peak = equity_curve[0]
    for value in equity_curve:
        curve_peak = max(curve_peak, value)
        max_dd = max(max_dd, 1.0 - value / curve_peak)

    fault_campaign = _task_fault_campaign(task, bars["SPY"][-1])
    chaos = run_chaos(seed=6060, malformed_cases=5000, sizing_cases=100000, counsel_cases=5000)

    report = {
        "campaign": "TRIPS_MOCK_2000_60_CYCLE_TORTURE",
        "accelerated_cycles": CYCLES,
        "bar_interval": cfg["bar_interval"],
        "starting_equity": STARTING_EQUITY,
        "ending_equity": round(final_equity, 2),
        "net_mark_to_market_pnl": round(final_equity - STARTING_EQUITY, 2),
        "return_pct": round((final_equity / STARTING_EQUITY - 1.0) * 100.0, 4),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "signals": signal_candidates,
        "queued_orders": queued,
        "entries": len(entries),
        "closed_trades": len(exits),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / len(exits) * 100.0) if exits else 0.0, 4),
        "open_positions_at_end": len(state["positions"]),
        "max_concurrent_positions": max_position_count,
        "risk_rejections": risk_rejections,
        "constitution_rejections": constitution_rejections,
        "task_blocks_on_strategy_orders": task_blocks,
        "task_size_reductions": task_reductions,
        "task_fault_campaign": {
            "cases": fault_campaign["cases"],
            "passed": fault_campaign["passed"],
        },
        "chaos": chaos,
        "causal_fill_violations": causal_fill_violations,
        "ledger_hash_chain_valid": verify_hash_chain(ledger),
        "live_orders_submitted": 0,
        "real_money_used": 0,
        "note": "Synthetic accelerated engineering simulation; not evidence of live profitability.",
    }

    print("TRIPS_60_CYCLE_REPORT=" + json.dumps(report, sort_keys=True))

    assert fault_campaign["passed"] == fault_campaign["cases"]
    assert chaos["passed"] is True
    assert causal_fill_violations == 0
    assert verify_hash_chain(ledger)
    assert state["cash"] >= -1e-9
    assert state["equity"] >= 0
    assert max_position_count <= cfg["risk"]["max_open_positions"]
    assert all(x["qty"] > 0 for x in entries)
    assert report["live_orders_submitted"] == 0
    assert report["real_money_used"] == 0
    return report


def test_mock_2000_60cycle_torture():
    report = run_campaign()
    # Profit/loss is deliberately NOT a safety pass criterion.
    assert report["accelerated_cycles"] == 60
    assert report["starting_equity"] == 2000.0


if __name__ == "__main__":
    report = run_campaign()
    assert report["accelerated_cycles"] == 60
    assert report["starting_equity"] == 2000.0
    print("TRIPS_60_CYCLE_FINAL=" + json.dumps(report, sort_keys=True))
