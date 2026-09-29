"""Safety tests for the live-gate amendment mechanism and frozen-cycle routing.

These two concerns live in their own suite because they answer a different question from the rest
of the execution layer:

1. **Amendment mechanism.** ``PAPER_FIRST`` is NOT the only frozen blocker. The frozen
   ``config_guard`` independently refuses every mode but ``paper``. This suite proves both blockers
   are named, that no caller-asserted argument can talk either one away, that a *verified*
   owner-signed amendment makes the PRODUCTION boundary release, and that applying the amendment is
   refused because it is an owner act.

2. **Frozen-cycle routing.** Every actionable decision the frozen ``forge_agent`` cycle produces is
   routed through the frozen Truth/Risk/Constitution gates, the Capital Governor, the Execution
   Authority Gate and the broker gateway. With the unamended core that route terminates at
   ``LIVE_LOCKED_REFUSAL`` and nothing reaches the broker; with a verified amendment it transmits.

``FakeAdapter`` and friends are imported from ``test_execution_layer`` - they are TEST DOUBLES
local to the suite, never shipped by the library.
"""

from __future__ import annotations

import atexit
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import test_execution_layer as base  # noqa: E402

# This suite exercises the honest owner path end to end, so the synthetic test public key stays
# installed for the whole module. (run_all_tests.py runs every suite in an isolated subprocess.)
# Tests that assert fail-closed behaviour install their own key file for the duration.
_OWNER_KEY_INSTALLED = base.owner_key_configured()
_OWNER_KEY_INSTALLED.__enter__()
atexit.register(_OWNER_KEY_INSTALLED.__exit__, None, None, None)

from execution import (  # noqa: E402
    AMENDMENT_APPLICABLE,
    JOURNAL_FILE,
    PURPOSE_AMENDMENT,
    AdapterRegistry,
    AmendmentApplicationRefused,
    AmendmentError,
    AmendmentProposal,
    CapitalGovernor,
    CycleRouter,
    ExecutionLayerError,
    ExecutionState,
    FrozenLiveBoundary,
    HealthGateEvidence,
    IntentPolicy,
    Lifecycle,
    LiveAuthorization,
    LiveEnvironmentAttestation,
    OrderCapabilities,
    PluginClassification,
    SafetyController,
    Stage,
    TransportKind,
    UniversalBrokerGateway,
    apply_amendment,
    blockers_to_live_release,
    describe_frozen_runtime_decisions,
    durable_store,
    fingerprint_profile,
    frozen_config_guard_permits,
    frozen_constitution_rule_ids,
    frozen_core_basis,
    frozen_permitted_modes,
    frozen_risk_permits,
    frozen_risk_permitted_modes,
    interpret_supervisor_output,
    live_release_requirements,
    release_basis_from_verdict,
    route_frozen_cycle,
    verify_amendment,
)
from providers import DemoProvider  # noqa: E402

LIVE_TEST_PROFILE = base.LIVE_TEST_PROFILE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def live_governor(**overrides):
    profile = {**LIVE_TEST_PROFILE, **overrides}
    return CapitalGovernor(profile, profile_version="live-test-1",
                           approved_profile_hash=fingerprint_profile(profile))


def amendment_proposal(**overrides):
    """A properly owner-SIGNED proposal. Pass ``signature=`` to build a deliberately bad one."""
    now = datetime.now(timezone.utc)
    payload = {
        "amendment_id": "AMENDMENT-01-LIVE-GATE",
        "adds_rules": ("LIVE_GATE",),
        "removes_rules": ("PAPER_FIRST",),
        "target_mode": "live",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=365)).isoformat(),
        "rationale": "replace PAPER_FIRST with the deterministic LIVE_GATE",
    }
    payload.update(overrides)
    unsigned = AmendmentProposal(**payload)
    if "signature" in overrides:
        return unsigned
    return replace(unsigned, signature=base.sign_for_tests(PURPOSE_AMENDMENT,
                                                           unsigned.signed_payload()))


def owner_authorization():
    """Owner decision B. Kept distinct from decision A: a basis alone is not authority."""
    return base.signed_authorization(broker_id="test-double", account_id="ACCT-1",
                                     environment="TEST_ENV")


def verified_basis():
    verdict = verify_amendment(amendment_proposal())
    assert verdict["code"] == AMENDMENT_APPLICABLE, verdict
    return release_basis_from_verdict(verdict)


def live_ready_router(*, basis=None, stage=Stage.LIVE_LOCKED, requested_bars=240,
                      authorization=None):
    """Everything a live route needs, so the ONLY thing left to refuse is the frozen boundary."""
    config = base.trade_config()
    bars = DemoProvider().bars("SPY", requested_bars)
    adapter = base.FakeAdapter()
    registry = AdapterRegistry()
    registry.register(broker_id="test-double", adapter=adapter, transport=TransportKind.REST,
                      classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                      authorized_interface=True, authorized_interface_evidence="test",
                      environment="TEST_ENV")
    router = CycleRouter(
        config=config, approved_config_hash=base.fingerprint_config(config),
        governor=live_governor(), adapter=adapter, registry=registry,
        lifecycle=Lifecycle(stage, FrozenLiveBoundary(), release_basis=basis,
                            authorization=authorization),
        provider=base.TestProvider("a", "fam_a", bars=bars),
        secondary_provider=base.TestProvider("b", "fam_b", bars=bars),
        session_calendar=base.session_calendar(), expected_account_id="ACCT-1",
        expected_environment="TEST_ENV", requested_bars=requested_bars)
    return router, adapter


def frozen_cycle_runtime(*, signal_bar_ts, price=100.0, cycle_number=7, **state_overrides):
    """A runtime shaped exactly like the one the frozen forge_agent cycle commits."""
    state = {"equity": 100_000.0, "cash": 100_000.0, "peak_equity": 100_000.0, "daily_pnl": 0.0,
             "cycle_count": cycle_number, "positions": {}, "last_prices": {"SPY": price},
             "pending_entries": {"SPY": {
                 "created_at": signal_bar_ts, "signal_bar_ts": signal_bar_ts,
                 "strategy": "trend_follow", "signal_score": 0.87, "agreement_count": 3,
                 "candle_context": "BULLISH", "atr_at_signal": 1.5,
                 "data_integrity_hash": "deadbeef", "source": "provider-a"}}}
    state.update(state_overrides)
    return {"portfolio": state, "ledger": []}


def route_kwargs(now, **overrides):
    kwargs = {"now": now, "new_exposure_this_period": 0.0,
              "health_gate": HealthGateEvidence(passed=True, checks=(),
                                                generated_at=now.isoformat()),
              "environment_attestation": LiveEnvironmentAttestation(
                  environment="TEST_ENV", attested_by="owner",
                  attested_at=(now - timedelta(minutes=5)).isoformat(),
                  expires_at=(now + timedelta(hours=1)).isoformat())}
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# The full set of frozen blockers
# ---------------------------------------------------------------------------


def test_every_frozen_blocker_is_named_not_just_paper_first():
    """PAPER_FIRST was never the only frozen blocker. THREE separate sites are reported."""
    requirements = live_release_requirements()
    blockers = requirements["blockers_to_live_release"]
    assert "CONSTITUTION_PROHIBITION_RULE" in blockers
    assert "CONFIG_GUARD_MODE_RESTRICTION" in blockers
    assert "RISK_MODE_RESTRICTION" in blockers
    assert frozen_config_guard_permits("paper")["permitted"] is True
    live_probe = frozen_config_guard_permits("live")
    assert live_probe["permitted"] is False
    assert "paper mode only" in str(live_probe["reason"])
    # The third blocker is the frozen Risk engine's own paper_mode check, probed not mirrored.
    assert frozen_risk_permits("paper")["permitted"] is True
    risk_probe = frozen_risk_permits("live")
    assert risk_probe["permitted"] is False
    assert "Live execution is disabled by design" in str(risk_probe["reason"])
    assert frozen_permitted_modes() == ("paper",)
    assert frozen_risk_permitted_modes() == ("paper",)
    assert requirements["frozen_mode_restrictions"] == {"config_guard": ["paper"],
                                                        "risk": ["paper"]}
    assert requirements["frozen_config_guard_permitted_modes"] == ["paper"]
    assert "engine/config_guard.py" in requirements["artifacts_requiring_owner_review_and_reapproval"]
    assert "engine/risk.py" in requirements["artifacts_requiring_owner_review_and_reapproval"]


def test_the_risk_engine_blocker_is_independent_of_the_config_guard():
    """Two frozen sites refuse live for two different reasons; lifting one is not enough."""
    guard, risk = frozen_config_guard_permits("live"), frozen_risk_permits("live")
    assert guard["permitted"] is False and risk["permitted"] is False
    assert "paper mode only" in str(guard["reason"])
    assert "Live execution is disabled by design" in str(risk["reason"])
    assert guard["reason"] != risk["reason"]


def test_caller_asserted_core_state_can_never_release_capital():
    """A caller passing convenient arguments must not be able to open the gate."""
    lifecycle = Lifecycle(Stage.LIVE_ENABLED)
    result = lifecycle.may_transmit_live(rule_ids=("LIVE_GATE",), mode="live")
    assert result["permitted"] is False
    assert result["code"] == "CORE_STATE_CALLER_ASSERTED_REFUSED"
    assert result["core_state_source"] == "CALLER_ASSERTED"
    assert result["release_basis"] is None
    # The mode restriction is read from the frozen validator even when asserting a rule set.
    assert result["boundary"]["permitted_modes"] == ["paper"]
    assert "cannot release capital" in " ".join(result["reasons"])
    assert Lifecycle(Stage.LIVE_ENABLED).may_transmit_live()["permitted"] is False
    assert frozen_core_basis().source == "FROZEN_FILES"
    assert frozen_core_basis().permitted_modes == ("paper",)


# ---------------------------------------------------------------------------
# Amendment verification
# ---------------------------------------------------------------------------


def test_verified_owner_amendment_opens_the_production_boundary():
    verdict = verify_amendment(amendment_proposal())
    assert verdict["code"] == AMENDMENT_APPLICABLE
    assert verdict["boundary_code"] == "LIVE_RELEASE_PERMITTED"
    assert verdict["blockers_after_amendment"] == []
    assert sorted(verdict["blockers_resolved"]) == ["CONFIG_GUARD_MODE_RESTRICTION",
                                                    "CONSTITUTION_PROHIBITION_RULE",
                                                    "RISK_MODE_RESTRICTION"]
    assert verdict["permitted_modes"] == ["paper", "live"]
    assert verdict["amended_rule_count"] == 35 and verdict["preserved_rule_count"] == 34
    assert verdict["boundary"]["released"] is True
    basis = release_basis_from_verdict(verdict)
    assert basis.source == "VERIFIED_AMENDMENT" and basis.mode == "live"
    released = Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                         authorization=owner_authorization())
    assert released.may_transmit_live()["permitted"] is True
    assert released.core_state_basis().source == "VERIFIED_AMENDMENT"
    # The same production boundary still refuses the unamended core.
    assert Lifecycle(Stage.LIVE_ENABLED).may_transmit_live()["permitted"] is False


def test_unsigned_amendment_is_refused():
    verdict = verify_amendment(amendment_proposal(signature=""))
    assert verdict["code"] == "OWNER_SIGNATURE_REQUIRED"
    assert verdict["applicable"] is False
    assert "no owner signature" in verdict["reasons"][0]


def test_amendment_may_only_touch_the_prohibition_and_the_authorization_rule():
    widened = verify_amendment(amendment_proposal(adds_rules=("LIVE_GATE", "SKIP_RISK")))
    assert widened["code"] == "AMENDMENT_ADD_NOT_PERMITTED"
    greedy = verify_amendment(amendment_proposal(
        removes_rules=("PAPER_FIRST", "CLOSED_BARS_ONLY", "FAIL_CLOSED")))
    assert greedy["code"] == "AMENDMENT_REMOVE_NOT_PERMITTED"
    assert "CLOSED_BARS_ONLY" in greedy["reasons"][0]


def test_amendment_strict_superset_preserves_every_other_rule():
    verdict = verify_amendment(amendment_proposal())
    surviving = [rule for rule in frozen_constitution_rule_ids() if rule != "PAPER_FIRST"]
    amended = list(verdict["boundary"]["constitution_rule_ids"])
    assert set(surviving) <= set(amended)
    assert "PAPER_FIRST" not in amended and "LIVE_GATE" in amended
    assert len(amended) == len(frozen_constitution_rule_ids())


def test_amendment_must_also_resolve_the_config_guard_mode_restriction():
    """An amendment that only renames the rule leaves the second and third blockers in place."""
    verdict = verify_amendment(amendment_proposal(adds_rules=(), removes_rules=()))
    assert verdict["code"] == "AMENDMENT_DOES_NOT_OPEN_THE_GATE"
    already = verify_amendment(amendment_proposal(target_mode="paper"))
    assert already["code"] == "AMENDMENT_DOES_NOT_OPEN_THE_GATE"
    assert "already permits mode" in already["reasons"][0]


def test_expired_amendment_is_refused():
    now = datetime.now(timezone.utc)
    verdict = verify_amendment(amendment_proposal(
        issued_at=(now - timedelta(days=30)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat()))
    assert verdict["code"] == "AMENDMENT_EXPIRED"
    try:
        AmendmentProposal(amendment_id="x", adds_rules=("LIVE_GATE",), removes_rules=(),
                          target_mode="live", issued_at="2026-01-01T00:00:00",
                          expires_at="2026-01-02T00:00:00")
        assert False, "expected AmendmentError for a naive timestamp"
    except AmendmentError:
        pass


def test_amendment_application_is_refused_because_it_is_an_owner_act():
    try:
        apply_amendment(amendment_proposal())
        assert False, "expected AmendmentApplicationRefused"
    except AmendmentApplicationRefused as exc:
        assert "AMENDMENT_APPLICATION_IS_AN_OWNER_ACT" in str(exc)
        assert "engine/constitution.py" in str(exc)


def test_release_basis_is_refused_from_a_non_applicable_amendment():
    try:
        release_basis_from_verdict(verify_amendment(amendment_proposal(signature="")))
        assert False, "expected AmendmentApplicationRefused"
    except AmendmentApplicationRefused:
        pass
    assert blockers_to_live_release()["blockers"] == ["CONSTITUTION_PROHIBITION_RULE",
                                                      "CONFIG_GUARD_MODE_RESTRICTION",
                                                      "RISK_MODE_RESTRICTION"]


# ---------------------------------------------------------------------------
# Frozen-cycle routing
# ---------------------------------------------------------------------------


def test_router_derives_decision_identity_from_the_frozen_pending_entry():
    """A stable reference to the frozen decision - not a second strategy record."""
    now = base.in_session_now()
    runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
    described = describe_frozen_runtime_decisions(runtime, prices={"SPY": 100.0})
    assert described["pending_entry_count"] == 1 and described["skipped"] == []
    decision = described["decisions"][0]
    assert decision["symbol"] == "SPY" and decision["side"] == "BUY"
    assert decision["strategy"] == "trend_follow"
    assert decision["decision_id"].startswith("frozen-")
    again = describe_frozen_runtime_decisions(runtime, prices={"SPY": 100.0})["decisions"][0]
    assert again["decision_id"] == decision["decision_id"], "decision identity must be deterministic"
    moved = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=3)).isoformat())
    other = describe_frozen_runtime_decisions(moved, prices={"SPY": 100.0})["decisions"][0]
    assert other["decision_id"] != decision["decision_id"]


def test_router_reports_unconvertible_pending_entries_instead_of_dropping_them():
    now = base.in_session_now()
    runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
    no_price = describe_frozen_runtime_decisions(runtime, prices={})
    assert no_price["decisions"] == []
    assert no_price["skipped"][0]["code"] == "NO_DECISION_PRICE"
    held = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat(),
                                positions={"SPY": {"qty": 10, "entry": 99.0}})
    in_position = describe_frozen_runtime_decisions(held, prices={"SPY": 100.0})
    assert in_position["decisions"] == []
    assert in_position["skipped"][0]["code"] == "ALREADY_IN_POSITION"


def test_router_routes_the_frozen_cycle_and_refuses_only_at_the_frozen_boundary():
    """The frozen decision reaches the boundary and is refused only by the frozen core."""
    with base.isolated_store():
        now = base.in_session_now()
        router, adapter = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        assert report["decision_count"] == 1 and report["transmitted"] == 0
        assert report["core_state_source"] == "FROZEN_FILES"
        result = report["results"][0]
        assert result["outcome"] == "LIVE_LOCKED_REFUSAL"
        assert result["state"] == ExecutionState.REFUSED.value
        assert result["preflight_passed"] is True, result["failing_preconditions"]
        assert result["failing_permissions"] == []
        assert result["gate_decision"] == "EXECUTION_AUTHORIZED"
        assert result["quantity"] > 0
        assert any("PAPER_FIRST" in note for note in result["notes"])
        assert adapter.submitted == [], "nothing may reach the broker while the core forbids it"


def test_router_sizes_only_from_the_frozen_risk_authorization():
    with base.isolated_store():
        now = base.in_session_now()
        router, _ = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        assert report["results"][0]["quantity"] == 300, "size must come from frozen position_size"


def test_router_refuses_a_stale_signal_bar_before_any_request():
    with base.isolated_store():
        now = base.in_session_now()
        router, adapter = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=9)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        assert report["results"][0]["outcome"] == "STALE_DECISION"
        assert adapter.submitted == []
        assert router.gateway.ledger.all_entries() == {}, "a stale decision buys no reservation"


def test_router_refuses_when_the_frozen_risk_engine_authorizes_zero_size():
    with base.isolated_store():
        now = base.in_session_now()
        router, adapter = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat(), cash=0.0)
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        result = report["results"][0]
        assert result["outcome"] == "NOT_SIZED"
        assert "authorized zero size" in result["notes"][0]
        assert adapter.submitted == []


def test_router_journals_every_route_with_its_core_state_source():
    with base.isolated_store():
        now = base.in_session_now()
        router, _ = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
        route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0}, **route_kwargs(now))
        journal = router.journal()
        assert journal["schema_version"] == 1 and len(journal["entries"]) == 1
        entry = journal["entries"][0]
        assert entry["cycle_number"] == 7 and entry["refused"] == 1
        assert entry["core_state_source"] == "FROZEN_FILES"
        assert entry["results"][0]["outcome"] == "LIVE_LOCKED_REFUSAL"
        assert durable_store.read_json(JOURNAL_FILE, None) is not None


def test_router_policy_translates_the_frozen_fill_policy_without_inventing_prices():
    policy = IntentPolicy()
    assert policy.order_type == "MARKET" and policy.time_in_force == "DAY"
    described = policy.to_dict()
    assert "never invents a limit price" in described["note"]
    with base.isolated_store():
        now = base.in_session_now()
        router, _ = live_ready_router()
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        assert report["policy"] == described


# ---------------------------------------------------------------------------
# The live-money demonstration (test environment, test broker only)
# ---------------------------------------------------------------------------


def test_amended_core_transmits_a_live_money_order_to_the_test_broker():
    """With a verified owner amendment the PRODUCTION boundary releases and an order is sent."""
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        order = base.intent()
        basis = verified_basis()

        blocked, blocked_adapter = base.build_gateway(stage=Stage.LIVE_ENABLED)
        assert base.submit(blocked, base.permissive_report(order, now=now),
                           order=order).outcome == "LIVE_LOCKED_REFUSAL"
        assert blocked_adapter.submitted == []

        live_order = base.intent(intent_id="int-live", idempotency_key="idem-live")
        adapter = base.FakeAdapter()
        gateway = UniversalBrokerGateway(
            adapter=adapter, governor=base.governor(),
            lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                                authorization=owner_authorization()))
        result = gateway.submit(live_order, preflight_report=base.permissive_report(live_order, now=now),
                               portfolio=base.portfolio(), holdings={}, expected_account_id="ACCT-1",
                               expected_environment="TEST_ENV", now=now)
        assert result.outcome == "TRANSMITTED", result.reasons
        assert result.transmitted is True
        assert result.boundary["released"] is True
        assert gateway.lifecycle.core_state_basis().source == "VERIFIED_AMENDMENT"
        entry = gateway.ledger.entry("idem-live")
        assert entry["state"] == ExecutionState.BROKER_ACKNOWLEDGED.value
        assert entry["broker_order_id"] == "BO-1"
        assert len(adapter.submitted) == 1


def test_frozen_cycle_routes_a_live_money_order_once_the_amendment_is_verified():
    """End to end: frozen decision -> frozen gates -> governor -> gate -> broker."""
    with base.isolated_store():
        now = base.in_session_now()
        router, adapter = live_ready_router(basis=verified_basis(), stage=Stage.LIVE_ENABLED,
                                            authorization=owner_authorization())
        runtime = frozen_cycle_runtime(signal_bar_ts=(now - timedelta(hours=2)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(now))
        assert report["transmitted"] == 1 and report["refused"] == 0
        assert report["core_state_source"] == "VERIFIED_AMENDMENT"
        result = report["results"][0]
        assert result["outcome"] == "TRANSMITTED"
        assert result["transmitted"] is True
        assert result["gate_decision"] == "EXECUTION_AUTHORIZED"
        assert result["quantity"] == 300
        assert len(adapter.submitted) == 1
        entry = next(iter(router.gateway.ledger.all_entries().values()))
        assert entry["state"] == ExecutionState.BROKER_ACKNOWLEDGED.value
        assert entry["attempts"] == 1


def test_amended_core_still_refuses_a_short_or_out_of_scope_order():
    """The amendment releases capital; it does not relax the mandate."""
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        basis = verified_basis()
        for bad in (base.intent(side="SELL", idempotency_key="idem-s"),
                    base.intent(symbol="TSLA", idempotency_key="idem-t")):
            adapter = base.FakeAdapter()
            gateway = UniversalBrokerGateway(
                adapter=adapter, governor=base.governor(),
                lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                                authorization=owner_authorization()))
            result = gateway.submit(bad, preflight_report=base.permissive_report(bad, now=now),
                                   portfolio=base.portfolio(), holdings={},
                                   expected_account_id="ACCT-1", expected_environment="TEST_ENV",
                                   now=now)
            assert result.outcome == "REFUSED_BY_SCOPE"
            assert adapter.submitted == []


def test_amended_core_still_cannot_bypass_a_supervisor_halt():
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        safety = SafetyController()
        adapter = base.FakeAdapter()
        gateway = UniversalBrokerGateway(
            adapter=adapter, governor=base.governor(), safety=safety,
            lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(),
                                release_basis=verified_basis(),
                                authorization=owner_authorization()))
        safety.apply(interpret_supervisor_output(
            {"finding": "SUPERVISOR_HALT_REQUEST", "evidence_refs": ["e:1"],
             "explanation": "unresolved ambiguity"}))
        order = base.intent()
        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                               portfolio=base.portfolio(), holdings={}, expected_account_id="ACCT-1",
                               expected_environment="TEST_ENV", now=now)
        assert result.outcome == "SUPERVISOR_BLOCKED_NEW_EXPOSURE"
        assert adapter.submitted == []


def test_amended_core_does_not_relax_the_capability_or_representability_contract():
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        basis = verified_basis()
        order = base.intent(order_type="MARKET", limit_price=None)
        caps = OrderCapabilities(order_types=frozenset({"LIMIT"}), time_in_force=frozenset({"DAY"}),
                                 sides=frozenset({"BUY", "SELL"}), declared=True)
        adapter = base.FakeAdapter(order_caps=caps)
        gateway = UniversalBrokerGateway(
            adapter=adapter, governor=base.governor(),
            lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                                authorization=owner_authorization()))
        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                               portfolio=base.portfolio(), holdings={}, expected_account_id="ACCT-1",
                               expected_environment="TEST_ENV", now=now)
        assert result.outcome == "ORDER_INCOMPATIBLE"
        assert adapter.submitted == []

        undeclared = base.UndeclaredOrderCapsAdapter()
        gateway2 = UniversalBrokerGateway(
            adapter=undeclared, governor=base.governor(),
            lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                                authorization=owner_authorization()))
        again = base.intent(intent_id="int-2", idempotency_key="idem-2")
        result2 = gateway2.submit(again, preflight_report=base.permissive_report(again, now=now),
                                 portfolio=base.portfolio(), holdings={}, expected_account_id="ACCT-1",
                                 expected_environment="TEST_ENV", now=now)
        assert result2.outcome == "REFUSED_BY_CAPABILITY"
        assert undeclared.submitted == []


# ---------------------------------------------------------------------------
# Owner signature integrity — regression for the closed forgery hole
# ---------------------------------------------------------------------------


def test_there_is_no_owner_signed_boolean_left_to_forge():
    """Authority is a signature, not a flag. A flag is settable by any caller."""
    try:
        AmendmentProposal(amendment_id="x", adds_rules=("LIVE_GATE",),
                          removes_rules=("PAPER_FIRST",), target_mode="live",
                          issued_at="2026-01-01T00:00:00+00:00",
                          expires_at="2026-01-02T00:00:00+00:00", owner_signed=True)
        assert False, "owner_signed must not exist: a boolean is forgeable"
    except TypeError:
        pass
    assert not hasattr(AmendmentProposal, "owner_signed")
    assert not hasattr(LiveAuthorization, "owner_signed")


def test_amendment_fails_closed_when_no_owner_key_is_configured():
    signed = amendment_proposal()  # signed while the key is available
    with base.owner_key_unconfigured():
        assert live_release_requirements()["owner_authority"]["configured"] is False
        verdict = verify_amendment(signed)
        assert verdict["code"] == "OWNER_AUTHORITY_KEY_NOT_CONFIGURED"
        assert verdict["applicable"] is False
    # With the key restored the very same proposal verifies again.
    assert verify_amendment(signed)["code"] == AMENDMENT_APPLICABLE


def test_amendment_with_a_wrong_signature_is_refused():
    # Well-formed envelope, right key id, but signed over different bytes.
    forged = base.sign_for_tests(PURPOSE_AMENDMENT, b"a different payload entirely")
    verdict = verify_amendment(amendment_proposal(signature=forged))
    assert verdict["code"] == "OWNER_SIGNATURE_INVALID"
    assert verdict["applicable"] is False


def test_a_signature_from_another_key_is_refused_as_a_mismatch():
    other_private, other_public = base.ed25519.generate_keypair(b"\x42" * 32)
    other_id = base.key_id_for(other_public)
    forged = base.sign_for_tests(PURPOSE_AMENDMENT, b"payload", private=other_private,
                                 key_id=other_id)
    verdict = verify_amendment(amendment_proposal(signature=forged))
    assert verdict["code"] == "OWNER_AUTHORITY_KEY_MISMATCH"
    assert verdict["applicable"] is False


def test_an_envelope_from_a_rotated_key_cannot_be_replayed():
    """Key binding: a signature minted under a different key is refused, not accepted."""
    other_private, other_public = base.ed25519.generate_keypair(b"\x42" * 32)
    other_id = base.key_id_for(other_public)
    with base.owner_key_configured(other_public):
        # Signed while the OTHER key is the pinned one...
        signed = AmendmentProposal(**{
            "amendment_id": "AMENDMENT-01-LIVE-GATE", "adds_rules": ("LIVE_GATE",),
            "removes_rules": ("PAPER_FIRST",), "target_mode": "live",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "rationale": "r",
            "signature": base.sign_for_tests(PURPOSE_AMENDMENT, b"p", private=other_private,
                                             key_id=other_id),
        })
    with base.owner_key_configured():
        verdict = verify_amendment(signed)
    assert verdict["code"] == "OWNER_AUTHORITY_KEY_MISMATCH"


def test_a_modified_proposal_does_not_verify_against_the_owner_signature():
    """The signature covers the contents, so tampering after signing is detectable."""
    signed = amendment_proposal()
    assert verify_amendment(signed)["code"] == AMENDMENT_APPLICABLE
    assert verify_amendment(replace(signed, target_mode="paper"))["code"] == \
        "OWNER_SIGNATURE_INVALID"
    assert verify_amendment(replace(signed, adds_rules=("LIVE_GATE", "SKIP_RISK")))["code"] == \
        "OWNER_SIGNATURE_INVALID"
    assert verify_amendment(replace(signed, amendment_id="forged-02"))["code"] == \
        "OWNER_SIGNATURE_INVALID"


def test_an_unsigned_live_authorization_is_not_an_authorization():
    now = datetime.now(timezone.utc)
    unsigned = LiveAuthorization(
        strategy_build_id="b" * 64, config_id="c" * 64, risk_profile_id="r" * 64,
        governor_profile_id="g" * 64, broker_id="test-double", account_id="ACCT-1",
        environment="TEST_ENV", issued_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat())
    assert unsigned.identity_complete() is True, "well-formed, but not authorized"
    valid, reasons = unsigned.is_valid(now)
    assert valid is False
    assert any("OWNER_SIGNATURE_REQUIRED" in reason for reason in reasons)


def test_release_basis_without_a_signed_owner_authorization_is_refused():
    """Owner decision A must not imply decision B: a basis is evidence, not authority."""
    try:
        Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=verified_basis())
        assert False, "expected ExecutionLayerError"
    except ExecutionLayerError as exc:
        assert "signed live authorization" in str(exc)


def test_release_basis_with_a_self_minted_unsigned_authorization_is_refused():
    now = datetime.now(timezone.utc)
    basis = verified_basis()
    unsigned = LiveAuthorization(
        strategy_build_id="b" * 64, config_id="c" * 64, risk_profile_id="r" * 64,
        governor_profile_id="g" * 64, broker_id="test-double", account_id="ACCT-1",
        environment="TEST_ENV", issued_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat())
    try:
        Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(), release_basis=basis,
                  authorization=unsigned)
        assert False, "expected ExecutionLayerError"
    except ExecutionLayerError as exc:
        assert "live authorization is not valid" in str(exc)


def test_the_demonstrated_forgery_no_longer_reaches_the_broker():
    """Regression: a caller-set boolean used to yield a live order. It now cannot.

    Before the owner signature existed, ``AmendmentProposal(owner_signed=True)`` produced an
    ``AMENDMENT_APPLICABLE`` verdict, hence a VERIFIED_AMENDMENT basis, hence TRANSMITTED.
    """
    with base.isolated_store():
        signed = amendment_proposal()
        with base.owner_key_unconfigured():
            verdict = verify_amendment(signed)
            assert verdict["code"] == "OWNER_AUTHORITY_KEY_NOT_CONFIGURED"
            assert verdict["applicable"] is False
            try:
                release_basis_from_verdict(verdict)
                assert False, "expected AmendmentApplicationRefused"
            except AmendmentApplicationRefused:
                pass
        # With no basis obtainable, the honest end-to-end path still refuses and sends nothing.
        router, adapter = live_ready_router()
        runtime = frozen_cycle_runtime(
            signal_bar_ts=(base.in_session_now() - timedelta(hours=2)).isoformat())
        report = route_frozen_cycle(router, runtime=runtime, prices={"SPY": 100.0},
                                    **route_kwargs(base.in_session_now()))
        assert report["transmitted"] == 0
        assert adapter.submitted == []
        assert report["results"][0]["outcome"] == "LIVE_LOCKED_REFUSAL"


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
    print(f"ALL PASS ({len(tests)} live-gate amendment tests)")
