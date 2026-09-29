"""Adversarial tests for TASK, Trip's Autonomous Safety Kernel.

These tests deliberately use explicit fixture thresholds. They are not production defaults and
grant no live authority.
"""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from execution.cycle_bridge import CycleRouter  # noqa: E402
from execution.task_safety_kernel import (  # noqa: E402
    APPROVED,
    APPROVED_REDUCED,
    BLOCKED,
    ContractLifecycleEvidence,
    TASKContext,
    TASKError,
    TASKKernel,
    TASKPolicy,
    TradeProposal,
    fingerprint_policy,
)


NOW = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)


def policy(**overrides):
    values = {
        "policy_version": "test-only-v1",
        "max_price_deviation_bps": 100.0,
        "max_spread_bps": 50.0,
        "max_adv_participation_pct": 0.01,
        "max_messages_per_second": 8,
        "max_repeated_executions": 3,
        "repeated_execution_window_seconds": 60,
        "first_notice_buffer_days": 5,
        "last_trade_buffer_days": 3,
        "require_cancel_on_disconnect": True,
        "require_self_match_prevention": True,
        "allow_unbounded_market_orders": False,
        "permitted_order_types": ("LIMIT",),
        "allowed_venue_states": ("OPEN",),
        "required_compliance_checks": ("strong_auth", "venue_tag"),
    }
    values.update(overrides)
    return TASKPolicy(**values)


def kernel(**overrides):
    p = policy(**overrides)
    return TASKKernel(p, approved_policy_hash=fingerprint_policy(p))


def proposal(**overrides):
    values = {
        "symbol": "TEST",
        "side": "BUY",
        "desired_quantity": 10,
        "order_type": "LIMIT",
        "limit_price": 100.25,
        "reference_price": 100.0,
        "strategy_id": "strategy-test",
    }
    values.update(overrides)
    return TradeProposal(**values)


def context(**overrides):
    values = {
        "market_data_healthy": True,
        "venue_state": "OPEN",
        "observed_price": 100.0,
        "spread_bps": 10.0,
        "average_daily_volume": 10_000.0,
        "broker_connected": True,
        "cancel_on_disconnect_active": True,
        "instrument_kind": "CASH",
        "working_orders": (),
        "recent_message_times": (),
        "recent_execution_times": (),
        "compliance": {"strong_auth": True, "venue_tag": True},
        "contract_lifecycle": None,
    }
    values.update(overrides)
    return TASKContext(**values)


def test_policy_has_no_hidden_runtime_defaults():
    sig = inspect.signature(TASKPolicy)
    for parameter in sig.parameters.values():
        assert parameter.default is inspect.Parameter.empty


def test_policy_must_match_owner_approved_fingerprint():
    p = policy()
    try:
        TASKKernel(p, approved_policy_hash="0" * 64)
        assert False, "expected TASKError"
    except TASKError:
        pass
    assert TASKKernel(p, approved_policy_hash=fingerprint_policy(p)).policy_hash == fingerprint_policy(p)


def test_kernel_preserves_a_safe_proposal():
    decision = kernel().evaluate(proposal(), context=context(), now=NOW)
    assert decision.status == APPROVED
    assert decision.approved_quantity == 10
    assert decision.approved_quantity <= decision.desired_quantity


def test_liquidity_cap_reduces_instead_of_needlessly_rejecting():
    decision = kernel().evaluate(
        proposal(desired_quantity=20),
        context=context(average_daily_volume=1_000.0),
        now=NOW)
    assert decision.status == APPROVED_REDUCED
    assert decision.approved_quantity == 10
    assert "ADV_PARTICIPATION_CAP" in decision.constraints["binding"]


def test_task_can_never_increase_upstream_authorized_size():
    for adv in (1_000.0, 10_000.0, 1_000_000_000.0):
        desired = 7
        decision = kernel().evaluate(
            proposal(desired_quantity=desired),
            context=context(average_daily_volume=adv),
            now=NOW)
        assert decision.approved_quantity <= desired


def test_spread_and_bad_market_data_fail_closed():
    assert kernel().evaluate(
        proposal(), context=context(spread_bps=50.01), now=NOW).status == BLOCKED
    assert kernel().evaluate(
        proposal(), context=context(market_data_healthy=False), now=NOW).status == BLOCKED


def test_limit_price_tolerance_is_checked_against_current_observed_price():
    decision = kernel().evaluate(
        proposal(limit_price=103.0),
        context=context(observed_price=100.0),
        now=NOW)
    assert decision.status == BLOCKED
    assert "PRICE_TOLERANCE_EXCEEDED" in decision.blocks


def test_market_orders_require_explicit_policy_permission_and_acknowledged_unbounded_risk():
    blocked = kernel(
        permitted_order_types=("LIMIT", "MARKET"),
        allow_unbounded_market_orders=False,
    ).evaluate(
        proposal(order_type="MARKET", limit_price=None),
        context=context(),
        now=NOW)
    assert "UNBOUNDED_MARKET_ORDER_NOT_PERMITTED" in blocked.blocks

    allowed = kernel(
        permitted_order_types=("LIMIT", "MARKET"),
        allow_unbounded_market_orders=True,
    ).evaluate(
        proposal(order_type="MARKET", limit_price=None),
        context=context(),
        now=NOW)
    assert allowed.allowed
    assert "MARKET_ORDER_HAS_NO_LIMIT_PRICE" in allowed.advisories


def test_message_throttle_blocks_new_orders_not_cancellation_paths():
    stamps = tuple((NOW - timedelta(milliseconds=i * 50)).isoformat() for i in range(8))
    decision = kernel().evaluate(
        proposal(), context=context(recent_message_times=stamps), now=NOW)
    assert decision.status == BLOCKED
    assert "MESSAGE_RATE_LIMIT" in decision.blocks
    # TASK evaluates new TradeProposal objects only; cancellation is not an input or throttled path.
    assert "cancel" not in inspect.signature(TASKKernel.evaluate).parameters


def test_repeated_execution_breaker_is_strategy_independent_at_execution_boundary():
    stamps = tuple((NOW - timedelta(seconds=i + 1)).isoformat() for i in range(3))
    decision = kernel().evaluate(
        proposal(), context=context(recent_execution_times=stamps), now=NOW)
    assert decision.status == BLOCKED
    assert "REPEATED_EXECUTION_LIMIT" in decision.blocks


def test_self_match_guard_blocks_opposite_resting_order():
    working = ({"symbol": "TEST", "side": "SELL", "state": "OPEN"},)
    decision = kernel().evaluate(
        proposal(side="BUY"), context=context(working_orders=working), now=NOW)
    assert decision.status == BLOCKED
    assert "SELF_MATCH_RISK" in decision.blocks


def test_required_venue_compliance_evidence_fails_closed_when_unknown():
    decision = kernel().evaluate(
        proposal(), context=context(compliance={"strong_auth": True}), now=NOW)
    assert decision.status == BLOCKED
    assert "VENUE_COMPLIANCE_UNVERIFIED" in decision.blocks
    assert decision.constraints["missing_compliance_checks"] == ["venue_tag"]


def test_disconnect_and_cancel_on_disconnect_are_separate_evidence():
    disconnected = kernel().evaluate(
        proposal(), context=context(broker_connected=False), now=NOW)
    assert "BROKER_DISCONNECTED" in disconnected.blocks

    no_cod = kernel().evaluate(
        proposal(), context=context(cancel_on_disconnect_active=False), now=NOW)
    assert "CANCEL_ON_DISCONNECT_UNVERIFIED" in no_cod.blocks


def test_derivatives_require_contract_lifecycle_truth():
    decision = kernel().evaluate(
        proposal(), context=context(instrument_kind="FUTURE", contract_lifecycle=None), now=NOW)
    assert decision.status == BLOCKED
    assert "CONTRACT_LIFECYCLE_UNVERIFIED" in decision.blocks


def test_physical_future_blocks_before_first_notice_cutoff():
    lifecycle = ContractLifecycleEvidence(
        settlement_type="PHYSICAL",
        first_notice_date="2026-10-04",
        last_trade_date="2026-10-20",
    )
    decision = kernel().evaluate(
        proposal(), context=context(instrument_kind="FUTURE", contract_lifecycle=lifecycle), now=NOW)
    assert decision.status == BLOCKED
    assert "FIRST_NOTICE_CUTOFF_REACHED" in decision.blocks


def test_cash_settled_future_still_obeys_last_trade_cutoff():
    lifecycle = ContractLifecycleEvidence(
        settlement_type="CASH",
        last_trade_date="2026-10-02",
    )
    decision = kernel().evaluate(
        proposal(), context=context(instrument_kind="FUTURE", contract_lifecycle=lifecycle), now=NOW)
    assert decision.status == BLOCKED
    assert "LAST_TRADE_CUTOFF_REACHED" in decision.blocks


def test_risk_budget_is_read_only_and_never_grants_more_than_frozen_risk_size():
    k = kernel()
    q = proposal(desired_quantity=11)
    budget = k.risk_budget(q, context(average_daily_volume=500.0), now=NOW)
    assert budget["max_executable_quantity"] == 5
    assert budget["max_executable_quantity"] <= q.desired_quantity
    assert k.policy_hash == fingerprint_policy(k.policy)


def test_cycle_router_wires_task_before_final_immutable_intent():
    source = inspect.getsource(CycleRouter._route_one)
    assert "self._task_kernel.evaluate" in source
    assert "TradeProposal" in source
    assert "approved_quantity" in source
    assert source.index("self._task_kernel.evaluate") < source.index("submission = self._gateway.submit")


if __name__ == "__main__":
    import traceback

    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception:
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    if failed:
        raise SystemExit(f"{len(failed)} tests failed: {failed}")
    print(f"ALL PASS ({len(tests)} TASK tests)")
