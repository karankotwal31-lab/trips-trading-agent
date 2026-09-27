from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from candle_intelligence import interpret as interpret_candles
from build_guard import verify_build_integrity
from config_guard import ConfigError, fingerprint_config, validate_config
from constitution import NON_NEGOTIABLES, constitution_gate
from market_time import closed_bars_only
from providers import Bar, MarketDataError, get_provider
from risk import forge_gate, position_size
from store import (StateStoreError, append_hash_chained_event, cycle_lock, read_runtime,
                   validate_state, write_json, write_runtime)
from strategies import consensus, evaluate, features
from truth_guard import apply_cross_source_verification, cross_validate, validate_bars
from health_engine import preflight_health_gate
from decision_mirror import append_decision_event, mirror_trade_cycle
from supervisor_relay import build_outbox_from_runtime, relay_health
from redaction import sanitize

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).parent / "config.json"
APPROVED_CONFIG_PATH = Path(__file__).parent / "approved_config.sha256"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict:
    raw = json.loads(CONFIG_PATH.read_text())
    cfg = validate_config(raw)
    current = fingerprint_config(cfg)
    if not APPROVED_CONFIG_PATH.exists():
        raise ConfigError("approved_config.sha256 missing; configuration has not been reviewed")
    approved = APPROVED_CONFIG_PATH.read_text().strip()
    if current != approved:
        raise ConfigError("configuration fingerprint changed; review/tests and explicit re-approval are required")
    return cfg


def _risk_day(ts: datetime, tz_name: str) -> str:
    return ts.astimezone(ZoneInfo(tz_name)).date().isoformat()


def roll_risk_day(state: dict, cfg: dict):
    key = _risk_day(datetime.now(timezone.utc), cfg["risk"]["risk_day_timezone"])
    if state.get("risk_day") != key:
        state["risk_day"] = key
        state["daily_pnl"] = 0.0
        state["risk_day_start_equity"] = state.get("equity", state.get("cash", 0.0))


def advance_cooldown_on_market_bar(state: dict, market_bar_ts: Optional[str]):
    """Advance loss cooldown at most once per newly observed trusted market bar."""
    if state.get("cooldown_remaining", 0) <= 0 or not market_bar_ts:
        return
    last = state.get("cooldown_last_bar_ts")
    if last is None:
        state["cooldown_last_bar_ts"] = market_bar_ts
        return
    try:
        current_dt = datetime.fromisoformat(market_bar_ts).astimezone(timezone.utc)
        last_dt = datetime.fromisoformat(last).astimezone(timezone.utc)
    except Exception:
        return
    if current_dt > last_dt:
        state["cooldown_remaining"] = max(0, int(state["cooldown_remaining"]) - 1)
        state["cooldown_last_bar_ts"] = market_bar_ts if state["cooldown_remaining"] > 0 else None


def mark_to_market(state: dict, latest: Dict[str, float]):
    unrealized = 0.0
    exposure = 0.0
    unpriced = []
    for symbol, p in state["positions"].items():
        if symbol in latest:
            px = latest[symbol]
            state.setdefault("last_prices", {})[symbol] = px
        elif symbol in state.get("last_prices", {}):
            px = state["last_prices"][symbol]
            unpriced.append(symbol)
        else:
            px = p["entry"]
            unpriced.append(symbol)
        unrealized += (px - p["entry"]) * p["qty"]
        exposure += px * p["qty"]
    state["equity"] = state["cash"] + exposure
    state["peak_equity"] = max(state.get("peak_equity", state["equity"]), state["equity"])
    return unrealized, exposure, unpriced


def _append(ledger: List[dict], event: dict):
    return append_hash_chained_event(ledger, event)


def apply_position_management(symbol: str, bar: Bar, state: dict, ledger: List[dict], cfg: dict):
    """Process an already-open long position causally.

    Existing stop/target are evaluated before any stop adjustment derived from this bar's close.
    A stop adjusted from the close becomes effective only for subsequent bars.
    """
    p = state["positions"].get(symbol)
    if not p or p.get("last_processed_bar_ts") == bar.ts:
        return

    stop = p["stop"]
    target = p["target"]
    exit_reason = None
    raw_exit = None

    # Open is temporally first. Handle unambiguous gap events before intrabar ambiguity.
    if bar.open <= stop:
        exit_reason = "GAP_STOP_OR_PROTECTIVE_EXIT"
        raw_exit = bar.open
    elif bar.open >= target:
        # Conservative profit fill: cap at target even if the market gaps beyond it.
        exit_reason = "TARGET"
        raw_exit = target
    else:
        stop_hit = bar.low <= stop
        target_hit = bar.high >= target
        if stop_hit and target_hit:
            # OHLC cannot reveal which occurred first; resolve against the strategy.
            exit_reason = "AMBIGUOUS_BAR_STOP_FIRST"
            raw_exit = stop
        elif stop_hit:
            exit_reason = "STOP_OR_PROTECTIVE_EXIT"
            raw_exit = stop
        elif target_hit:
            exit_reason = "TARGET"
            raw_exit = target

    if exit_reason is not None:
        cost_bps = cfg["risk"]["assumed_slippage_bps"] + cfg["risk"]["assumed_spread_bps"] / 2.0
        exit_price = raw_exit * (1.0 - cost_bps / 10000.0)
        proceeds = exit_price * p["qty"]
        state["cash"] += proceeds
        pnl = (exit_price - p["entry"]) * p["qty"]
        state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl
        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= cfg["risk"]["cooldown_after_losses"]:
                state["cooldown_remaining"] = max(state.get("cooldown_remaining", 0), cfg["risk"]["cooldown_cycles"])
                state["cooldown_last_bar_ts"] = bar.ts
                # Reset the streak once the cooldown has been triggered so it can eventually expire.
                state["consecutive_losses"] = 0
        else:
            state["consecutive_losses"] = 0
        _append(ledger, {
            "ts": now_iso(), "market_bar_ts": bar.ts, "symbol": symbol, "type": "PAPER_EXIT",
            "reason": exit_reason, "entry": p["entry"], "exit": round(exit_price, 6), "qty": p["qty"],
            "pnl": round(pnl, 2), "protected": p.get("protected", False), "trailing": p.get("trailing", False),
            "execution_cost_assumption_bps": cost_bps,
        })
        del state["positions"][symbol]
        return

    initial_r = p["entry"] - p["initial_stop"]
    if initial_r > 0:
        favorable_r = (bar.close - p["entry"]) / initial_r
        if favorable_r >= cfg["trade"]["break_even_after_r"]:
            p["stop"] = max(p["stop"], p["entry"])
            p["protected"] = True
        if favorable_r >= cfg["trade"]["trail_after_r"]:
            p["stop"] = max(p["stop"], bar.close - cfg["trade"]["trail_atr_multiple"] * p["atr_at_entry"])
            p["trailing"] = True
    p["last_processed_bar_ts"] = bar.ts


def create_escalation(reason: str, symbol: str, packet: dict) -> dict:
    seed = f"{reason}|{symbol}|{packet.get('truth', {}).get('integrity_hash', '')}|{packet.get('signal_bar_ts', '')}"
    eid = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return {
        "required": True, "id": eid, "dedupe_key": f"{reason}|{symbol}", "created_at": now_iso(),
        "last_seen_at": now_iso(), "occurrences": 1, "reason": reason, "symbol": symbol,
        "consultation_protocol": [
            "Trip's supplies observed data, provenance, timestamps, hashes, model outputs and failed rules.",
            "Supervisor independently verifies current external facts before forming a view.",
            "Observed facts, assumptions, inference and uncertainty remain explicitly separated.",
            "Material strategy/risk-policy changes are explained to the user in plain language before approval.",
            "Neither supervisor nor user instruction silently overrides hard risk/data gates."
        ],
        "question": "Review the evidence. If current facts cannot be independently confirmed, remain NO_TRADE. Any rule/model change requires explicit reviewed configuration/code change and tests.",
        "packet": packet,
    }


def _add_escalation(items: list, reason: str, symbol: str, packet: dict):
    e = create_escalation(reason, symbol, packet)
    if all(x["id"] != e["id"] for x in items):
        items.append(e)


def _merge_escalation_queue(queue: list, new_items: list) -> list:
    """Persist/coalesce unresolved escalations and return queue IDs touched this cycle."""
    touched = []
    open_by_key = {x.get("dedupe_key"): x for x in queue
                   if isinstance(x, dict) and x.get("status") == "OPEN" and x.get("dedupe_key")}
    for item in new_items:
        key = item.get("dedupe_key") or f"{item.get('reason')}|{item.get('symbol')}"
        existing = open_by_key.get(key)
        if existing is not None:
            existing["last_seen_at"] = item.get("last_seen_at") or now_iso()
            existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
            existing["latest_event_id"] = item.get("id")
            existing["packet"] = item.get("packet")
            existing["question"] = item.get("question")
            touched.append(existing["id"])
            continue
        stored = dict(item)
        existing_ids = {x.get("id") for x in queue if isinstance(x, dict)}
        if stored.get("id") in existing_ids:
            seed = f"{stored.get('id')}|{now_iso()}|{len(queue)}"
            stored["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]
        stored["dedupe_key"] = key
        stored["status"] = "OPEN"
        queue.append(stored)
        open_by_key[key] = stored
        touched.append(stored["id"])
    # Keep all unresolved evidence; bound acknowledged history to avoid storage growth.
    acknowledged = [x for x in queue if x.get("status") == "ACKNOWLEDGED"]
    if len(acknowledged) > 500:
        keep_ids = {x.get("id") for x in acknowledged[-500:]}
        queue[:] = [x for x in queue if x.get("status") != "ACKNOWLEDGED" or x.get("id") in keep_ids]
    return touched


def _open_escalations(queue: list) -> list:
    return [x for x in queue if isinstance(x, dict) and x.get("status") == "OPEN"]


def _init_provider(name: Optional[str], interval: str, source_kind: Optional[str]):
    return None if not name else get_provider(name, interval, source_kind or "delayed")


def _anomaly(truth: dict) -> bool:
    critical = {"declared_source_kind", "provider_kind_binding", "nonempty_source", "nonempty_source_family",
                "timezone_aware_timestamps", "strict_time_order", "finite_positive_prices", "ohlc_invariants",
                "nonnegative_volume", "extreme_move_review"}
    return any(not x["passed"] for x in truth.get("checks", []) if x["name"] in critical) or (
        truth.get("cross_source") is not None and not truth["cross_source"].get("passed", False)
    )


def _new_bar_distance(bars: List[Bar], signal_ts: str) -> Optional[int]:
    index = {b.ts: i for i, b in enumerate(bars)}
    if signal_ts not in index:
        return None
    return len(bars) - 1 - index[signal_ts]


def process_position_history(symbol: str, bars: List[Bar], truth: dict, state: dict, ledger: list, cfg: dict) -> str:
    """Apply every unseen validated closed bar to an existing position in chronological order."""
    position = state["positions"].get(symbol)
    if not position:
        return "NO_POSITION"
    if not bars or not truth.get("trusted_for_trade"):
        return "UNTRUSTED"
    last_processed = position.get("last_processed_bar_ts")
    index = {b.ts: i for i, b in enumerate(bars)}
    if last_processed not in index:
        return "HISTORY_GAP"
    for bar in bars[index[last_processed] + 1:]:
        apply_position_management(symbol, bar, state, ledger, cfg)
        if symbol not in state["positions"]:
            break
    return "OK"


def _execute_pending(symbol: str, bars: List[Bar], truth: dict, state: dict, ledger: list,
                     audit: list, cfg: dict, exposure: float, drawdown: float, cycle_halt: bool):
    pending = state.get("pending_entries", {}).get(symbol)
    if not pending:
        return exposure
    distance = _new_bar_distance(bars, pending["signal_bar_ts"])
    if distance is None or distance > cfg["trade"]["pending_entry_max_bars"]:
        audit.append({"ts": now_iso(), "event": "PENDING_EXPIRED", "symbol": symbol, "detail": "signal bar missing or too old"})
        del state["pending_entries"][symbol]
        return exposure
    if distance < 1:
        return exposure
    if symbol in state["positions"]:
        del state["pending_entries"][symbol]
        return exposure

    stale = not any(x["name"] == "freshness" and x["passed"] for x in truth.get("checks", []))
    gate = forge_gate(
        config=cfg, provider_name=truth.get("source", "unknown"), bars_count=len(bars),
        signal_score=float(pending["signal_score"]), conflict=False, stale=stale,
        positions=state["positions"], pending_entries={k:v for k,v in state.get("pending_entries", {}).items() if k != symbol}, symbol=symbol,
        daily_pnl=min(state.get("daily_pnl", 0.0), state.get("equity", 0.0) - state.get("risk_day_start_equity", state.get("equity", 0.0))),
        equity=state["equity"], drawdown_pct=drawdown,
        assumed_spread_bps=cfg["risk"]["assumed_spread_bps"], cooldown_remaining=state.get("cooldown_remaining", 0),
        global_halt=cycle_halt or state.get("halted", False), agreement_count=int(pending["agreement_count"]),
        candle_context=pending["candle_context"],
    )
    constitution = constitution_gate(truth=truth, mode=cfg["mode"], gate_passed=gate["passed"],
                                     model_conflict=False, anomaly=_anomaly(truth), requires_verified_trade_data=True)
    if not constitution["passed"]:
        audit.append({"ts": now_iso(), "event": "PENDING_REJECT", "symbol": symbol,
                      "failed_checks": [x for x in constitution["checks"] if not x["passed"]]})
        del state["pending_entries"][symbol]
        return exposure

    fill_bar = bars[-1]
    cost_bps = cfg["risk"]["assumed_slippage_bps"] + cfg["risk"]["assumed_spread_bps"] / 2.0
    entry = fill_bar.open * (1.0 + cost_bps / 10000.0)
    stop = entry - cfg["trade"]["atr_stop_multiple"] * pending["atr_at_signal"]
    target = entry + cfg["trade"]["target_r_multiple"] * (entry - stop)
    if stop <= 0 or target <= entry:
        audit.append({"ts": now_iso(), "event": "PENDING_REJECT", "symbol": symbol,
                      "detail": "invalid stop/target geometry at simulated fill"})
        del state["pending_entries"][symbol]
        return exposure
    qty = position_size(state["equity"], entry, stop, cfg["risk"]["max_risk_per_trade_pct"],
                        cfg["risk"]["max_total_exposure_pct"], exposure)
    qty = min(qty, int(state["cash"] / entry) if entry > 0 else 0)
    if qty <= 0:
        audit.append({"ts": now_iso(), "event": "PENDING_REJECT", "symbol": symbol, "detail": "position size resolved to zero"})
        del state["pending_entries"][symbol]
        return exposure

    base_exposure = exposure
    notional = entry * qty
    state["cash"] -= notional
    state["positions"][symbol] = {
        "entry": round(entry, 6), "qty": qty, "initial_stop": round(stop, 6), "stop": round(stop, 6),
        "target": round(target, 6), "atr_at_entry": pending["atr_at_signal"], "strategy": pending["strategy"],
        "signal_score": pending["signal_score"], "opened_at": now_iso(), "signal_bar_ts": pending["signal_bar_ts"],
        "fill_bar_ts": fill_bar.ts, "last_processed_bar_ts": None, "protected": False, "trailing": False,
        "data_integrity_hash": truth.get("integrity_hash"), "source": truth.get("source"),
        "execution_cost_assumption_bps": cost_bps,
    }
    del state["pending_entries"][symbol]
    exposure += notional
    _append(ledger, {
        "ts": now_iso(), "market_bar_ts": fill_bar.ts, "symbol": symbol, "type": "PAPER_ENTRY",
        "entry": round(entry, 6), "qty": qty, "stop": round(stop, 6), "target": round(target, 6),
        "strategy": pending["strategy"], "signal_score": round(pending["signal_score"], 4),
        "provider": truth.get("source"), "data_integrity_hash": truth.get("integrity_hash"),
        "execution_cost_assumption_bps": cost_bps, "causal_fill": "next_bar_open_after_signal",
    })
    audit.append({"ts": now_iso(), "event": "PAPER_ENTRY", "symbol": symbol, "detail": state["positions"][symbol]})

    # Because the order was already pending before this bar opened, its whole bar can be used to simulate
    # stop/target outcomes after the opening fill without look-ahead in the decision itself.
    apply_position_management(symbol, fill_bar, state, ledger, cfg)
    # The fill may have exited within the same simulated bar. Preserve the already mark-to-market
    # exposure of older positions; add this position at the latest closed price only if it survives.
    if symbol in state["positions"]:
        exposure = base_exposure + fill_bar.close * state["positions"][symbol]["qty"]
    else:
        exposure = base_exposure
    return exposure


def run_cycle():
    manifest = verify_build_integrity()
    cfg = load_config()
    with cycle_lock():
        runtime = read_runtime(cfg["initial_equity"])
        health_preflight = preflight_health_gate(cfg, runtime)
        if not health_preflight["passed"]:
            raise StateStoreError(f"health preflight rejected trading cycle: {health_preflight['checks']}")
        state = runtime["portfolio"]
        validate_state(state)
        ledger = runtime["ledger"]
        audit_log = runtime["audit_log"]
        escalation_queue = runtime["escalation_queue"]
        ledger_start = len(ledger)
        relay_preflight = relay_health(runtime, cfg)

        roll_risk_day(state, cfg)
        config_hash = fingerprint_config(cfg)
        if state.get("config_hash") not in (None, config_hash):
            raise StateStoreError("runtime state was created under a different approved configuration")
        state["config_hash"] = config_hash

        cycle_audit: List[dict] = []
        proposals: List[dict] = []
        market: Dict[str, dict] = {}
        escalations: List[dict] = []
        cycle_halt = bool(cfg["supervisor"]["halt_new_entries_on_relay_backlog"] and not relay_preflight["passed"])
        if cycle_halt:
            cycle_audit.append({"ts": now_iso(), "event": "SUPERVISOR_RELAY_BACKPRESSURE", "symbol": "SYSTEM",
                                "detail": relay_preflight})
            _add_escalation(escalations, "SUPERVISOR_RELAY_BACKPRESSURE", "SYSTEM", relay_preflight)

        try:
            provider = _init_provider(cfg["provider"], cfg["bar_interval"], cfg.get("provider_source_kind"))
            secondary = _init_provider(cfg.get("secondary_provider"), cfg["bar_interval"], cfg.get("secondary_source_kind"))
        except MarketDataError as e:
            safe_error = sanitize(str(e))
            _add_escalation(escalations, "PROVIDER_INIT_FAILURE", "SYSTEM", {"error": safe_error})
            touched = _merge_escalation_queue(escalation_queue, escalations)
            audit_event = append_hash_chained_event(audit_log, {"ts": now_iso(), "event": "PROVIDER_INIT_FAILURE",
                                                  "detail": safe_error, "escalation_ids": touched})
            append_decision_event(runtime, event_kind="PROVIDER_INIT_FAILURE", subsystem="MARKET_DATA",
                                  action="HALT_CYCLE_AND_ESCALATE", symbol="SYSTEM",
                                  observed_facts={"error_type": type(e).__name__, "error": safe_error, "escalation_ids": touched},
                                  evidence_refs={"source_event_hash": audit_event.get("event_hash")},
                                  severity="CRITICAL", requires_supervisor_review=True,
                                  source_event_hash=audit_event.get("event_hash"))
            runtime.update({"portfolio": state, "ledger": ledger, "audit_log": audit_log,
                            "escalation_queue": escalation_queue})
            write_runtime(runtime)
            build_outbox_from_runtime(runtime, cfg, manifest["manifest_hash"], persist=True)
            open_items = _open_escalations(escalation_queue)
            write_json("escalations.json", {"required": bool(open_items), "open_items": open_items,
                                             "history": escalation_queue})
            return

        barsets: Dict[str, List[Bar]] = {}
        truth_map: Dict[str, dict] = {}
        latest: Dict[str, float] = {}

        for symbol in cfg["symbols"]:
            try:
                raw = provider.bars(symbol, 240)
                bars = closed_bars_only(raw, cfg["bar_interval"], close_lag_seconds=cfg["truth"]["bar_close_lag_seconds"])
                barsets[symbol] = bars
                primary = validate_bars(
                    source=provider.identity.name, source_family=provider.identity.source_family,
                    source_kind=cfg["provider_source_kind"], symbol=symbol, interval=cfg["bar_interval"], bars=bars,
                    max_age_minutes=cfg["risk"]["max_data_age_minutes"], min_bars=cfg["forge"]["require_history_bars"],
                    allow_synthetic_analysis=cfg["truth"]["allow_synthetic_analysis"],
                    fixed_source_kind=provider.identity.fixed_source_kind,
                    realtime_request_attested=provider.identity.can_request_realtime_entitlement and cfg["provider_source_kind"] == "real",
                )

                cross = None
                if secondary is not None:
                    sraw = secondary.bars(symbol, 240)
                    sbars = closed_bars_only(sraw, cfg["bar_interval"], close_lag_seconds=cfg["truth"]["bar_close_lag_seconds"])
                    secondary_truth = validate_bars(
                        source=secondary.identity.name, source_family=secondary.identity.source_family,
                        source_kind=cfg["secondary_source_kind"], symbol=symbol, interval=cfg["bar_interval"], bars=sbars,
                        max_age_minutes=cfg["risk"]["max_data_age_minutes"], min_bars=cfg["forge"]["require_history_bars"],
                        allow_synthetic_analysis=False, fixed_source_kind=secondary.identity.fixed_source_kind,
                        realtime_request_attested=secondary.identity.can_request_realtime_entitlement and cfg["secondary_source_kind"] == "real",
                    )
                    cross = cross_validate(primary, bars, secondary_truth, sbars,
                                           max_ohlc_deviation_pct=cfg["truth"]["max_ohlc_deviation_pct"],
                                           max_timestamp_skew_minutes=cfg["truth"]["max_timestamp_skew_minutes"],
                                           min_cross_source_bars=cfg["truth"]["min_cross_source_bars"])
                primary = apply_cross_source_verification(primary, cross, cfg["truth"]["require_independent_source_for_trade"])
                truth = primary.to_dict()
                truth_map[symbol] = truth
                if truth.get("trusted_for_trade") and bars:
                    latest[symbol] = bars[-1].close
                else:
                    cycle_audit.append({"ts": now_iso(), "event": "DATA_TRUTH_REJECT", "symbol": symbol, "detail": truth["reasons"]})
            except MarketDataError as e:
                safe_error = sanitize(str(e))
                market[symbol] = {"status": "DATA_ERROR", "error_type": type(e).__name__, "error": safe_error}
                cycle_audit.append({"ts": now_iso(), "event": "DATA_ERROR", "symbol": symbol,
                                    "detail": f"{type(e).__name__}: {safe_error}"})
                if symbol in state["positions"]:
                    cycle_halt = True
                    _add_escalation(escalations, "OPEN_POSITION_DATA_FAILURE", symbol,
                                    {"error_type": type(e).__name__, "error": safe_error, "position": state["positions"][symbol]})
            except Exception as e:
                # Unexpected code/runtime faults are systemic uncertainty, not ordinary feed misses.
                # Halt all new risk in this cycle even if another symbol still has valid data.
                cycle_halt = True
                safe_error = sanitize(str(e))
                market[symbol] = {"status": "INTERNAL_ERROR", "error_type": type(e).__name__, "error": safe_error}
                cycle_audit.append({"ts": now_iso(), "event": "INTERNAL_ERROR", "symbol": symbol,
                                    "detail": f"{type(e).__name__}: {safe_error}"})
                _add_escalation(escalations, "UNEXPECTED_INTERNAL_DATA_PATH_FAILURE", symbol,
                                {"error_type": type(e).__name__, "error": safe_error,
                                 "position": state["positions"].get(symbol)})

        trusted_bar_times = [bars[-1].ts for sym, bars in barsets.items()
                             if bars and truth_map.get(sym, {}).get("trusted_for_trade")]
        if trusted_bar_times:
            advance_cooldown_on_market_bar(state, max(trusted_bar_times))

        # Existing positions require trade-grade truth for management. Process every unseen closed bar
        # in chronological order so downtime cannot hide an intermediate stop/target event.
        for symbol in list(state["positions"]):
            bars = barsets.get(symbol, [])
            truth = truth_map.get(symbol, {})
            position = state["positions"].get(symbol)
            result = process_position_history(symbol, bars, truth, state, ledger, cfg)
            if result == "HISTORY_GAP":
                cycle_halt = True
                _add_escalation(escalations, "POSITION_BAR_HISTORY_GAP", symbol,
                                {"truth": truth, "position": position,
                                 "detail": "last processed market bar is absent from current validated history"})
            elif result == "UNTRUSTED":
                cycle_halt = True
                _add_escalation(escalations, "POSITION_DATA_UNTRUSTED", symbol,
                                {"truth": truth, "position": position})

        unrealized, exposure, unpriced = mark_to_market(state, latest)
        if unpriced:
            cycle_halt = True
            _add_escalation(escalations, "POSITION_VALUATION_STALE", ",".join(sorted(unpriced)), {"symbols": unpriced})
        drawdown = 0.0 if state["peak_equity"] <= 0 else 1.0 - state["equity"] / state["peak_equity"]
        if drawdown >= cfg["forge"]["halt_on_drawdown_pct"]:
            state["halted"] = True
            state["halt_reason"] = f"hard drawdown halt at {drawdown:.2%}"
            _add_escalation(escalations, "HARD_DRAWDOWN_HALT", "SYSTEM", {"drawdown": drawdown, "state": state})

        # First resolve orders that were created on prior closed bars. Revalue after every result
        # so a loss/exposure change from one pending order constrains the very next order.
        for symbol in list(state.get("pending_entries", {})):
            bars = barsets.get(symbol, [])
            truth = truth_map.get(symbol, {})
            if bars:
                exposure = _execute_pending(symbol, bars, truth, state, ledger, cycle_audit, cfg, exposure, drawdown, cycle_halt)
                unrealized, exposure, unpriced = mark_to_market(state, latest)
                drawdown = 0.0 if state["peak_equity"] <= 0 else 1.0 - state["equity"] / state["peak_equity"]
                if drawdown >= cfg["forge"]["halt_on_drawdown_pct"]:
                    state["halted"] = True
                    state["halt_reason"] = f"hard drawdown halt at {drawdown:.2%}"
                    cycle_halt = True
                    _add_escalation(escalations, "HARD_DRAWDOWN_HALT", "SYSTEM",
                                    {"drawdown": drawdown, "state": state})

        unrealized, exposure, unpriced = mark_to_market(state, latest)
        drawdown = 0.0 if state["peak_equity"] <= 0 else 1.0 - state["equity"] / state["peak_equity"]

        for symbol, bars in barsets.items():
            truth = truth_map.get(symbol, {})
            if not truth.get("trusted_for_analysis"):
                if cfg["forge"]["escalate_on_data_anomaly"]:
                    _add_escalation(escalations, "DATA_TRUTH_FAILURE", symbol, {"truth": truth})
                continue
            if len(bars) < cfg["forge"]["require_history_bars"]:
                cycle_audit.append({"ts": now_iso(), "event": "REJECT", "symbol": symbol, "detail": "insufficient closed history"})
                continue

            f = features(bars)
            candle = interpret_candles(bars)
            votes = evaluate(f)
            c = consensus(votes)
            stale = not any(x["name"] == "freshness" and x["passed"] for x in truth.get("checks", []))
            anomaly = _anomaly(truth)
            gate = forge_gate(
                config=cfg, provider_name=provider.name, bars_count=len(bars), signal_score=float(c["signal_score"]),
                conflict=bool(c["conflict"]), stale=stale, positions=state["positions"], pending_entries=state.get("pending_entries", {}), symbol=symbol,
                daily_pnl=min(state.get("daily_pnl", 0.0), state.get("equity", 0.0) - state.get("risk_day_start_equity", state.get("equity", 0.0))),
                equity=state["equity"], drawdown_pct=drawdown,
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"], cooldown_remaining=state.get("cooldown_remaining", 0),
                global_halt=cycle_halt or state.get("halted", False), agreement_count=int(c["agreement_count"]),
                candle_context=candle.label,
            )
            constitution = constitution_gate(truth=truth, mode=cfg["mode"], gate_passed=gate["passed"],
                                             model_conflict=bool(c["conflict"]), anomaly=anomaly,
                                             requires_verified_trade_data=True)
            market[symbol] = {
                "status": "OK", "last_closed_bar": bars[-1].to_dict(), "truth": truth,
                # Observability-only validated closed bars for the read-only dashboard.
                # They are never read back into trading state or execution logic.
                "chart_series": [b.to_dict() for b in bars[-80:]],
                "candle_interpretation": candle.to_dict(), "features": f.to_dict(),
                "votes": [v.to_dict() for v in votes], "consensus": c,
                "forge_gate": gate, "constitution_gate": constitution,
                "assumptions": {"spread_bps": cfg["risk"]["assumed_spread_bps"],
                                "slippage_bps": cfg["risk"]["assumed_slippage_bps"],
                                "note": "simulation assumptions, not observed quotes"},
            }
            proposal = {
                "ts": now_iso(), "symbol": symbol, "direction": c["direction"], "signal_score": c["signal_score"],
                "strategy": c["strategy"], "candle_read": candle.label, "forge_gate": gate,
                "constitution_gate": constitution, "data_integrity_hash": truth.get("integrity_hash"),
                "signal_bar_ts": bars[-1].ts,
            }
            proposals.append(proposal)

            if c["conflict"] and cfg["forge"]["escalate_on_conflict"]:
                _add_escalation(escalations, "STRATEGY_CONFLICT", symbol, market[symbol])
            if anomaly and cfg["forge"]["escalate_on_data_anomaly"]:
                _add_escalation(escalations, "DATA_ANOMALY", symbol, market[symbol])
            if drawdown >= cfg["forge"]["escalate_on_drawdown_pct"]:
                _add_escalation(escalations, "DRAWDOWN_REVIEW", symbol,
                                {"drawdown": drawdown, "state": state, "market": market[symbol]})

            if c["direction"] != "LONG" or not constitution["passed"]:
                cycle_audit.append({"ts": now_iso(), "event": "NO_TRADE", "symbol": symbol,
                                    "detail": "No long signal or Constitution rejected proposal",
                                    "failed_checks": [x for x in constitution["checks"] if not x["passed"]]})
                continue
            if symbol in state["positions"] or symbol in state["pending_entries"]:
                continue
            if state.get("last_signal_bar_ts", {}).get(symbol) == bars[-1].ts:
                continue

            # CAUSAL EXECUTION: queue now; do not fill at the same bar's close.
            state["pending_entries"][symbol] = {
                "created_at": now_iso(), "signal_bar_ts": bars[-1].ts, "strategy": c["strategy"],
                "signal_score": c["signal_score"], "agreement_count": c["agreement_count"],
                "candle_context": candle.label, "atr_at_signal": f.atr14,
                "data_integrity_hash": truth.get("integrity_hash"), "source": truth.get("source"),
            }
            state.setdefault("last_signal_bar_ts", {})[symbol] = bars[-1].ts
            _append(ledger, {
                "ts": now_iso(), "market_bar_ts": bars[-1].ts, "symbol": symbol, "type": "PAPER_ORDER_QUEUED",
                "strategy": c["strategy"], "signal_score": round(c["signal_score"], 4),
                "data_integrity_hash": truth.get("integrity_hash"), "fill_policy": "next_closed_bar_open_simulation",
            })
            cycle_audit.append({"ts": now_iso(), "event": "PAPER_ORDER_QUEUED", "symbol": symbol,
                                "detail": state["pending_entries"][symbol]})

        state["cycle_count"] = state.get("cycle_count", 0) + 1
        state["last_cycle"] = now_iso()
        unrealized, exposure, unpriced = mark_to_market(state, latest)
        drawdown = 0.0 if state["peak_equity"] <= 0 else 1.0 - state["equity"] / state["peak_equity"]

        hashed_cycle_audit = []
        for event in cycle_audit:
            hashed_cycle_audit.append(append_hash_chained_event(audit_log, event))

        touched_escalations = _merge_escalation_queue(escalation_queue, escalations)
        open_items = _open_escalations(escalation_queue)
        escalation_doc = {"required": bool(open_items), "open_items": open_items,
                          "history": escalation_queue, "touched_this_cycle": touched_escalations}
        metrics = {"unrealized_pnl": round(unrealized, 2), "exposure": round(exposure, 2),
                   "drawdown_pct": drawdown, "daily_realized_pnl": round(state.get("daily_pnl", 0.0), 2),
                   "daily_total_pnl": round(state.get("equity", 0.0) - state.get("risk_day_start_equity", state.get("equity", 0.0)), 2)}
        summary = {
            "generated_at": now_iso(), "project": "Trip's", "version": cfg["version"], "mode": "PAPER ONLY",
            "instrument_scope": cfg["instrument_scope"],
            "constitution": [{"id": i, "rule": r} for i, r in NON_NEGOTIABLES],
            "health_preflight": health_preflight,
            "config_hash": config_hash, "provider": provider.name,
            "secondary_provider": secondary.name if secondary else None,
            "data_policy": "No provenance/timestamp/integrity => no market claim. Real-time entitlement is declared input, then freshness and independent-feed checks are still required.",
            "portfolio": state,
            "metrics": metrics,
            "supervisor_relay": relay_preflight,
            "market": market, "proposals": proposals, "audit": audit_log[-200:], "escalation": escalation_doc,
        }

        # Mirror every substantive decision before the atomic commit. The journal is authoritative and
        # becomes the source for the supervisor relay; transport acknowledgement never changes trade facts.
        runtime.update({"portfolio": state, "ledger": ledger, "audit_log": audit_log,
                        "escalation_queue": escalation_queue})
        mirror_trade_cycle(runtime, cycle_number=state["cycle_count"], health_preflight=health_preflight,
                           proposals=proposals, cycle_audit=hashed_cycle_audit,
                           new_ledger_events=ledger[ledger_start:], touched_escalations=touched_escalations,
                           escalation_items=[x for x in escalation_queue if x.get("id") in set(touched_escalations)],
                           portfolio={"equity": state["equity"], "cash": state["cash"],
                                      "positions": len(state["positions"]), "pending_entries": len(state["pending_entries"]),
                                      "halted": state.get("halted", False)},
                           metrics=metrics)
        # One authoritative atomic snapshot prevents torn multi-file trading state after crashes.
        write_runtime(runtime)
        # Derived relay/outbox can be rebuilt from the authoritative decision journal if lost.
        build_outbox_from_runtime(runtime, cfg, manifest["manifest_hash"], persist=True)
        # These are derived observability mirrors only; they are never used to reconstruct trading state.
        write_json("state.json", summary)
        write_json("escalations.json", escalation_doc)
        print(json.dumps({"ok": True, "project": "Trip's", "cycle": state["cycle_count"],
                          "equity": state["equity"], "halted": state.get("halted", False),
                          "escalations": len(escalations), "pending_entries": len(state["pending_entries"])}, sort_keys=True))


if __name__ == "__main__":
    run_cycle()
