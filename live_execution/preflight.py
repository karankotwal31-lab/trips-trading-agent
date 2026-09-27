from __future__ import annotations
from .readiness import evaluate
from .state import verify_reconciliation

def broker_preflight(broker, *, static_checks:dict) -> dict:
    account=broker.account()
    positions=broker.positions()
    orders=broker.orders()
    checks=dict(static_checks)
    checks["broker_configured"]=True
    checks["broker_authenticated"]=bool(account.get("id"))
    checks["account_permissions_verified"]=account.get("trading_blocked") is False and account.get("account_blocked") is False
    local_orders=static_checks.get("local_orders",[])
    rec=verify_reconciliation(local_orders,orders)
    checks["reconciliation_clean"]=rec["ok"]
    result=evaluate(checks)
    return {"ready":result["ready"],"failed":result["failed"],"account_id_present":bool(account.get("id")),
            "position_count":len(positions),"reconciliation":rec}
