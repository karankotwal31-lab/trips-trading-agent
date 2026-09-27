from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self):
        return asdict(self)


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
