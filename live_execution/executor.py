from __future__ import annotations
from .broker import Broker
from .gates import LiveGateError, LiveLimits, require_live_opt_in, validate_intent
from .readiness import evaluate


def execute(intent: dict, broker: Broker, *, daily_pnl: float, open_positions: int,
            limits: LiveLimits | None = None, readiness_checks=None,
            kill_switch=None, journal=None) -> dict:
    """Operator boundary. Missing readiness evidence or durable state blocks submission.

    Readiness evidence must be supplied by trusted host code, never by a strategy
    or an AI-generated intent. This function does not establish broker readiness.
    """
    require_live_opt_in()
    if not isinstance(readiness_checks, dict) or not evaluate(readiness_checks)['ready']:
        raise LiveGateError('complete readiness evidence required')
    if kill_switch is None or journal is None:
        raise LiveGateError('kill switch and durable journal required')
    kill_switch.require_clear()
    validate_intent(intent, limits=limits or LiveLimits(), daily_pnl=daily_pnl, open_positions=open_positions)
    journal.reserve(intent)
    try:
        result = broker.submit_order(dict(intent))
        if not isinstance(result, dict) or not isinstance(result.get('broker_order_id'), str) or not result['broker_order_id'].strip():
            raise RuntimeError('broker did not return a verifiable order id')
        journal.finish(intent['idempotency_key'], result)
    except BaseException:
        # A timeout/interrupt can occur after broker acceptance. Never auto-retry.
        # The durable 'submitting' row also blocks restart if this write fails.
        kill_switch.engage('uncertain broker submission: reconciliation required')
        journal.mark_unknown(intent['idempotency_key'])
        raise
    return result
