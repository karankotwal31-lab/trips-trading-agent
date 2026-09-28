from __future__ import annotations
import os
import math
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
    def finite(value, name):
        if isinstance(value, bool):
            raise LiveGateError(f"invalid {name}")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise LiveGateError(f"invalid {name}") from None
        if not math.isfinite(number):
            raise LiveGateError(f"invalid {name}")
        return number

    notional_limit = finite(limits.max_order_notional, "notional limit")
    loss_limit = finite(limits.max_daily_loss, "loss limit")
    daily_pnl = finite(daily_pnl, "daily PnL")
    if notional_limit <= 0 or loss_limit <= 0:
        raise LiveGateError("limits must be positive")
    if type(limits.max_open_positions) is not int or limits.max_open_positions <= 0:
        raise LiveGateError("invalid position limit")
    if type(open_positions) is not int or open_positions < 0:
        raise LiveGateError("invalid open position count")
    if intent.get("side") not in {"BUY", "SELL"}:
        raise LiveGateError("unsupported side")
    qty=finite(intent.get("qty",0), "quantity")
    px=finite(intent.get("reference_price",0), "reference price")
    if qty <= 0 or px <= 0:
        raise LiveGateError("invalid quantity/reference price")
    if not math.isfinite(qty * px) or qty * px > notional_limit:
        raise LiveGateError("max order notional exceeded")
    if daily_pnl <= -loss_limit:
        raise LiveGateError("daily loss kill-switch active")
    if open_positions >= limits.max_open_positions and intent["side"]=="BUY":
        raise LiveGateError("max open positions reached")
    if not isinstance(intent.get("idempotency_key"), str) or not intent["idempotency_key"].strip():
        raise LiveGateError("idempotency key required")
    if intent.get("data_fresh") is not True:
        raise LiveGateError("fresh trusted market data required")
    if intent.get("approved_core") is not True:
        raise LiveGateError("approved frozen core verification required")
