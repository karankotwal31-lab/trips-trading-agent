from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Mapping


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self):
        return asdict(self)


def correlated_group(config: dict, symbol: str) -> tuple[str, ...]:
    symbol = str(symbol).strip().upper()
    for raw_group in config["risk"]["correlated_symbol_groups"]:
        group = tuple(str(item).strip().upper() for item in raw_group)
        if symbol in group:
            return group
    return ()


def correlated_exposure(*, config: dict, positions: Mapping[str, object], symbol: str,
                        prices: Mapping[str, float] | None = None) -> float:
    """Current long notional in the candidate's approved correlation bucket."""
    group = set(correlated_group(config, symbol))
    if not group:
        return 0.0
    prices = prices or {}
    exposure = 0.0
    for held_symbol, raw in positions.items():
        if str(held_symbol).strip().upper() not in group:
            continue
        if isinstance(raw, Mapping):
            qty = raw.get("qty", raw.get("quantity", 0))
            entry = raw.get("entry", 0)
        else:
            qty = getattr(raw, "qty", getattr(raw, "quantity", 0))
            entry = getattr(raw, "entry", 0)
        try:
            quantity = max(0.0, float(qty))
            fallback_price = float(entry)
            mark = float(prices.get(held_symbol, fallback_price))
        except (TypeError, ValueError):
            # Unknown position economics must not create fake headroom.
            return float("inf")
        if mark <= 0:
            return float("inf")
        exposure += quantity * mark
    return exposure


def correlated_position_size_cap(*, equity: float, entry: float, config: dict,
                                 positions: Mapping[str, object], symbol: str,
                                 prices: Mapping[str, float] | None = None) -> int:
    group = correlated_group(config, symbol)
    if not group:
        return 2 ** 31 - 1
    if equity <= 0 or entry <= 0:
        return 0
    used = correlated_exposure(config=config, positions=positions, symbol=symbol, prices=prices)
    limit = float(equity) * float(config["risk"]["max_correlated_exposure_pct"])
    remaining = max(0.0, limit - used)
    return max(0, int(remaining / float(entry)))


def forge_gate(*, config: dict, provider_name: str, bars_count: int, signal_score: float,
               conflict: bool, stale: bool, positions: Dict[str, dict], pending_entries: Dict[str, dict], symbol: str,
               daily_pnl: float, equity: float, drawdown_pct: float, assumed_spread_bps: float,
               cooldown_remaining: int, global_halt: bool, agreement_count: int,
               candle_context: str) -> dict:
    r = config["risk"]
    f = config["forge"]
    checks: List[GateCheck] = []

    checks.append(GateCheck("paper_mode", config.get("mode") == "paper", "Live execution is disabled by design."))
    checks.append(GateCheck("global_halt", not global_halt, "No active risk/data halt" if not global_halt else "System halt blocks new entries"))
    checks.append(GateCheck("history", bars_count >= f["require_history_bars"], f"{bars_count} bars available"))
    checks.append(GateCheck("fresh_data", not stale, "Data passed freshness check" if not stale else "Data is stale/undated"))
    checks.append(GateCheck("signal_score", signal_score >= r["min_signal_score"], f"signal_score={signal_score:.2f}, threshold={r['min_signal_score']:.2f}"))
    checks.append(GateCheck("strategy_conflict", not conflict, "No material strategy conflict" if not conflict else "Strategy conflict requires escalation"))
    checks.append(GateCheck("strategy_agreement", agreement_count >= f["require_strategy_agreement"], f"agreement={agreement_count}/{f['require_strategy_agreement']}"))
    candle_ok = not (f.get("reject_bearish_candle_context_for_long", True) and candle_context in {"BEARISH_CONTEXT", "CONFLICTING_CONTEXT"})
    checks.append(GateCheck("candle_context", candle_ok, f"candle_context={candle_context}"))
    reserved_slots = len(positions) + len(pending_entries)
    checks.append(GateCheck("position_limit", reserved_slots < r["max_open_positions"], f"{reserved_slots}/{r['max_open_positions']} occupied+reserved slots"))
    group = correlated_group(config, symbol)
    correlated_used = correlated_exposure(config=config, positions=positions, symbol=symbol)
    correlated_limit = float(equity) * float(r["max_correlated_exposure_pct"])
    correlated_ok = (not group) or correlated_used < correlated_limit
    checks.append(GateCheck(
        "correlated_exposure",
        correlated_ok,
        (f"group={list(group)} used={correlated_used:.2f} limit={correlated_limit:.2f}"
         if group else "symbol has no configured correlated exposure group")))
    duplicate = symbol in positions or symbol in pending_entries
    checks.append(GateCheck("duplicate_position", not duplicate, "No existing/pending position" if not duplicate else "Existing/pending position: no duplicate or averaging down"))
    checks.append(GateCheck("daily_loss", daily_pnl > -(equity * r["max_daily_loss_pct"]), f"daily_pnl={daily_pnl:.2f}"))
    checks.append(GateCheck("drawdown_halt", drawdown_pct < f["halt_on_drawdown_pct"], f"drawdown={drawdown_pct:.2%}"))
    checks.append(GateCheck("spread_assumption", assumed_spread_bps <= r["max_assumed_spread_bps"], f"simulation assumption={assumed_spread_bps:.1f} bps; not observed market spread"))
    checks.append(GateCheck("cooldown", cooldown_remaining <= 0, f"cooldown_remaining={cooldown_remaining}"))

    return {"passed": all(c.passed for c in checks), "checks": [c.to_dict() for c in checks], "provider": provider_name}


def position_size(equity: float, entry: float, stop: float, risk_pct: float, max_exposure_pct: float,
                  existing_exposure: float) -> int:
    if equity <= 0 or entry <= 0:
        return 0
    per_share_risk = max(entry - stop, 0.0)
    if per_share_risk <= 0:
        return 0
    risk_budget = equity * risk_pct
    by_risk = int(risk_budget / per_share_risk)
    remaining_exposure = max(0.0, equity * max_exposure_pct - existing_exposure)
    by_exposure = int(remaining_exposure / entry)
    return max(0, min(by_risk, by_exposure))
