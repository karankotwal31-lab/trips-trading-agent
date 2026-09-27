from __future__ import annotations

REQUIRED_CHECKS=(
 "frozen_core_verified","approved_infra_verified","tests_green","broker_configured",
 "broker_authenticated","account_permissions_verified","market_data_trusted",
 "reconciliation_clean","kill_switch_tested","duplicate_order_tested",
 "partial_fill_tested","cancel_replace_tested","disconnect_recovery_tested",
 "clock_skew_tested","daily_loss_halt_tested","operator_opt_in",
)

def evaluate(checks:dict[str,bool]) -> dict:
    failed=[k for k in REQUIRED_CHECKS if checks.get(k) is not True]
    return {"ready":not failed,"failed":failed,"required":list(REQUIRED_CHECKS)}
