from __future__ import annotations
import os
from dataclasses import dataclass

class LiveGateError(RuntimeError):
    pass

@dataclass(frozen=True)
class LiveLimits:
    max_order_notional: float = 100.0
    max_daily_loss: float = 25.0
    max_open_positions: int = 1

def require_live_opt_in() -> None:
    if os.getenv("TRIPS_LIVE_EXECUTION", "").strip() != "ENABLED":
        raise LiveGateError("live execution disabled: explicit operator opt-in required")

def validate_intent(intent: dict, *, limits: LiveLimits, daily_pnl: float, open_positions: int) -> None:
    if intent.get("side") not in {"BUY", "SELL"}:
        raise LiveGateError("unsupported side")
    qty=float(intent.get("qty",0)); px=float(intent.get("reference_price",0))
    if qty <= 0 or px <= 0:
        raise LiveGateError("invalid quantity/reference price")
    if qty * px > limits.max_order_notional:
        raise LiveGateError("max order notional exceeded")
    if daily_pnl <= -abs(limits.max_daily_loss):
        raise LiveGateError("daily loss kill-switch active")
    if open_positions >= limits.max_open_positions and intent["side"]=="BUY":
        raise LiveGateError("max open positions reached")
    if not intent.get("idempotency_key"):
        raise LiveGateError("idempotency key required")
    if intent.get("data_fresh") is not True:
        raise LiveGateError("fresh trusted market data required")
    if intent.get("approved_core") is not True:
        raise LiveGateError("approved frozen core verification required")
