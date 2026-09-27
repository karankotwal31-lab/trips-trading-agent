from __future__ import annotations
from .broker import Broker
from .gates import LiveLimits, require_live_opt_in, validate_intent

def execute(intent: dict, broker: Broker, *, daily_pnl: float, open_positions: int,
            limits: LiveLimits | None = None) -> dict:
    """Fail-closed real-money execution boundary.

    Strategy/AI components may propose an intent; only this boundary may call a broker.
    """
    require_live_opt_in()
    active_limits=limits or LiveLimits()
    validate_intent(intent, limits=active_limits, daily_pnl=daily_pnl, open_positions=open_positions)
    result=broker.submit_order(dict(intent))
    if not isinstance(result, dict) or not result.get("broker_order_id"):
        raise RuntimeError("broker did not return a verifiable order id")
    return result
