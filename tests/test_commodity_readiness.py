"""Adversarial tests for commodity capability. Fixtures are synthetic and grant no live authority."""

from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from execution.commodity_readiness import (  # noqa: E402
    ANALYSIS_ELIGIBLE, BLOCKED, CommodityReadinessGate, CommodityUniversePolicy,
    fingerprint_universe_policy,
)
from execution.commodity_truth import (  # noqa: E402
    CommodityTruthGate, CommodityTruthPolicy, MarketSourceEvidence, fingerprint_truth_policy,
)
from execution.contract_master import (  # noqa: E402
    ContractMasterError, ContractMasterSnapshot, ContractSpec, load_contract_master_json,
)
from execution.task_safety_kernel import (  # noqa: E402
    TASKContext, TASKKernel, TASKPolicy, TradeProposal, fingerprint_policy,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def contract(**kw):
    v = dict(contract_symbol="ZXZ26", root_symbol="ZX", series_kind="EXECUTABLE_CONTRACT",
             venue="XTEST", sector="METALS", currency="USD", contract_multiplier=100.0,
             tick_size=0.1, settlement_type="PHYSICAL", first_notice_date="2026-12-20",
             last_trade_date="2026-12-27", expiration_date="2026-12-28",
             source_id="exchange-master", observed_at=NOW.isoformat())
    v.update(kw)
    return ContractSpec(**v)


def snapshot(*items, **kw):
    v = dict(source_name="exchange-reference", as_of=NOW.isoformat(),
             contracts=tuple(items or (contract(),)))
    v.update(kw)
    return ContractMasterSnapshot(**v)


def source(source_id="p", family="A", **kw):
    v = dict(source_id=source_id, source_family=family, source_kind="REALTIME",
             contract_symbol="ZXZ26", observed_at=NOW.isoformat(), venue_state="OPEN",
             bid=99.9, ask=100.1, last=100.0, daily_volume=10000.0, open_interest=25000.0)
    v.update(kw)
    return MarketSourceEvidence(**v)


def gates(snap=None):
    snap = snap or snapshot()
    tp = CommodityTruthPolicy("test-truth", True, True, 30, 20.0)
    truth = CommodityTruthGate(tp, approved_policy_hash=fingerprint_truth_policy(tp))
    up = CommodityUniversePolicy("test-universe", ("ZX",), ("XTEST",), ("METALS",), 24)
    ready = CommodityReadinessGate(
        up, approved_policy_hash=fingerprint_universe_policy(up),
        approved_contract_master_hash=snap.fingerprint, truth_gate=truth)
    return ready


def evaluate(*, snap=None, primary=None, secondary=None, ready=None, now=NOW):
    snap = snap or snapshot()
    ready = ready or gates(snap)
    return ready.evaluate(
        "ZXZ26", snapshot=snap, primary=primary or source(),
        secondary=secondary or source("s", "B"), now=now)


def test_policies_have_no_hidden_scope_or_market_threshold_defaults():
    for cls in (CommodityTruthPolicy, CommodityUniversePolicy):
        assert all(p.default is inspect.Parameter.empty
                   for p in inspect.signature(cls).parameters.values())


def test_continuous_series_cannot_become_an_execution_identity():
    try:
        contract(series_kind="CONTINUOUS_BACK_ADJUSTED")
        assert False
    except ContractMasterError as exc:
        assert "EXECUTABLE_CONTRACT" in str(exc)


def test_physical_contract_requires_first_notice_date():
    try:
        contract(first_notice_date=None)
        assert False
    except ContractMasterError:
        pass
    assert contract(settlement_type="CASH", first_notice_date=None).first_notice_date is None


def test_contract_master_hash_tampering_fails_closed():
    original = snapshot()
    ready = gates(original)
    changed = snapshot(contract(tick_size=0.2))
    decision = evaluate(snap=changed, ready=ready)
    assert decision.status == BLOCKED
    assert "CONTRACT_MASTER_FINGERPRINT_MISMATCH" in decision.blocks


def test_contract_metadata_staleness_fails_closed():
    stamp = (NOW - timedelta(hours=25)).isoformat()
    snap = snapshot(contract(observed_at=stamp), as_of=stamp)
    decision = evaluate(snap=snap, ready=gates(snap))
    assert {"CONTRACT_MASTER_STALE", "CONTRACT_METADATA_STALE"} <= set(decision.blocks)


def test_unapproved_root_venue_and_sector_each_fail_closed():
    for field, code in (("root_symbol", "ROOT_NOT_APPROVED"),
                        ("venue", "VENUE_NOT_APPROVED"),
                        ("sector", "SECTOR_NOT_APPROVED")):
        snap = snapshot(contract(**{field: "OTHER"}))
        assert code in evaluate(snap=snap, ready=gates(snap)).blocks


def test_two_market_sources_must_be_exact_contract_and_independent():
    assert "SECONDARY_SYMBOL_MISMATCH" in evaluate(
        secondary=source("s", "B", contract_symbol="OTHER")).blocks
    assert "MARKET_SOURCES_NOT_INDEPENDENT" in evaluate(
        secondary=source("s", "A")).blocks


def test_realtime_freshness_and_cross_source_truth_fail_closed():
    assert "PRIMARY_SOURCE_NOT_REALTIME" in evaluate(
        primary=source(source_kind="DELAYED")).blocks
    assert "PRIMARY_MARKET_DATA_STALE" in evaluate(
        primary=source(observed_at=(NOW - timedelta(seconds=31)).isoformat())).blocks
    assert "CROSS_SOURCE_PRICE_DEVIATION" in evaluate(
        secondary=source("s", "B", bid=101.0, ask=101.2, last=101.1)).blocks


def test_venue_state_disagreement_fails_closed():
    assert "VENUE_STATE_DISAGREEMENT" in evaluate(
        secondary=source("s", "B", venue_state="HALTED")).blocks


def test_zero_volume_or_open_interest_fails_closed():
    assert "NO_REPORTED_VOLUME" in evaluate(primary=source(daily_volume=0.0)).blocks
    assert "NO_REPORTED_OPEN_INTEREST" in evaluate(primary=source(open_interest=0.0)).blocks


def test_last_trade_date_is_hard_stop_without_invented_buffer():
    snap = snapshot(contract(last_trade_date=NOW.date().isoformat()))
    assert "CONTRACT_LAST_TRADE_REACHED" in evaluate(snap=snap, ready=gates(snap)).blocks


def test_safe_contract_is_analysis_only_not_execution_authority():
    decision = evaluate()
    assert decision.status == ANALYSIS_ELIGIBLE and decision.eligible_for_analysis
    assert decision.to_dict()["execution_authority"] is False
    assert decision.to_dict()["symbol_substitution_authority"] is False


def test_scanner_is_deterministic_and_never_substitutes_symbols():
    second = contract(contract_symbol="ZXF27", first_notice_date="2027-01-20",
                      last_trade_date="2027-01-27", expiration_date="2027-01-28")
    snap = snapshot(contract(), second)
    ready = gates(snap)
    evidence = {
        "ZXZ26": (source(), source("s", "B")),
        "ZXF27": (source(contract_symbol="ZXF27"),
                  source("s2", "B", contract_symbol="ZXF27")),
    }
    out = ready.scan(snapshot=snap, evidence=evidence, now=NOW)
    assert [x.contract_symbol for x in out] == ["ZXF27", "ZXZ26"]
    source_text = inspect.getsource(CommodityReadinessGate).lower()
    assert "broker" not in source_text and "submit" not in source_text


def test_lifecycle_truth_flows_into_task_and_task_remains_binding():
    near = contract(first_notice_date="2026-10-03")
    snap = snapshot(near)
    readiness = evaluate(snap=snap, ready=gates(snap))
    assert readiness.eligible_for_analysis

    p = TASKPolicy(
        policy_version="test-task", max_price_deviation_bps=50.0, max_spread_bps=30.0,
        max_adv_participation_pct=0.01, max_messages_per_second=10,
        max_repeated_executions=3, repeated_execution_window_seconds=60,
        first_notice_buffer_days=5, last_trade_buffer_days=3,
        require_cancel_on_disconnect=True, require_self_match_prevention=True,
        allow_unbounded_market_orders=False, permitted_order_types=("LIMIT",),
        allowed_venue_states=("OPEN",), required_compliance_checks=("strong_auth",))
    task = TASKKernel(p, approved_policy_hash=fingerprint_policy(p))
    proposal = TradeProposal(symbol="ZXZ26", side="BUY", desired_quantity=5,
                             order_type="LIMIT", limit_price=100.0, reference_price=100.0,
                             strategy_id="synthetic")
    ctx = TASKContext(
        market_data_healthy=readiness.truth.trusted_for_analysis,
        venue_state=readiness.truth.venue_state, observed_price=readiness.truth.observed_price,
        spread_bps=readiness.truth.spread_bps,
        average_daily_volume=readiness.truth.average_daily_volume,
        broker_connected=True, cancel_on_disconnect_active=True, instrument_kind="FUTURE",
        compliance={"strong_auth": True}, contract_lifecycle=near.lifecycle_evidence())
    decision = task.evaluate(proposal, context=ctx, now=NOW)
    assert not decision.allowed and "FIRST_NOTICE_CUTOFF_REACHED" in decision.blocks
    assert proposal.symbol == readiness.contract_symbol


def test_json_loader_round_trips_exact_hash():
    snap = snapshot()
    raw = json.dumps({
        "source_name": snap.source_name, "as_of": snap.as_of,
        "contracts": [{k: v for k, v in c.to_dict().items() if k != "tick_value"}
                      for c in snap.contracts],
    })
    assert load_contract_master_json(raw).fingerprint == snap.fingerprint


def test_public_execution_api_exposes_commodity_capability():
    import execution
    for name in ("ContractMasterSnapshot", "ContractSpec", "CommodityTruthGate",
                 "CommodityReadinessGate", "CommodityUniversePolicy"):
        assert hasattr(execution, name), name


if __name__ == "__main__":
    import traceback
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failed = []
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception:
            failed.append(test.__name__)
            print("FAIL", test.__name__)
            traceback.print_exc()
    if failed:
        raise SystemExit(f"{len(failed)} tests failed: {failed}")
    print(f"ALL PASS ({len(tests)} commodity-readiness tests)")
