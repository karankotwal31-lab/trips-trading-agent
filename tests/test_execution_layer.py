"""Safety tests for Trip's additive execution layer.

Two decisive claims are under test.

1. ``test_real_chain_truth_vetoes_demo_data`` exercises the REAL frozen pipeline
   (closed bars -> Truth -> strategy -> forge_gate -> constitution_gate) over the approved
   config. Trade eligibility fails, so the gateway refuses on authority, not on policy. That is
   the frozen Truth Engine vetoing, not this layer.

2. ``test_permissive_report_still_terminates_live_locked`` hands the gateway a maximally
   permissive, correctly-bound authority report. The gate reaches EXECUTION_AUTHORIZED and the
   adapter is still never called, because the frozen live boundary withholds release.

No broker is shipped by the library. ``FakeAdapter`` is a TEST DOUBLE local to this file, and
``ReleasingBoundary`` exists only to prove the transmission path is real code rather than a
placeholder.
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from config_guard import ConfigError, fingerprint_config, validate_config  # noqa: E402
from providers import Bar, DemoProvider  # noqa: E402
from execution import (  # noqa: E402
    AMENDMENT_APPLICABLE,
    ANOMALY_CHECKS,
    AUTHORIZED_TRANSPORTS,
    CAPITAL_RELEASE_FIELDS,
    CORE_CAPABILITIES,
    EVIDENCE_ALLOWLIST,
    JOURNAL_FILE,
    PRECONDITIONS,
    TRADE_VALID_FIELDS,
    AdapterRegistry,
    AmendmentApplicationRefused,
    AmendmentError,
    AmendmentProposal,
    CycleRouter,
    IntentPolicy,
    OrderCapabilities,
    RuleBasedSupervisorProvider,
    SupervisorPolicy,
    SupervisorRunner,
    UnavailableSupervisorProvider,
    apply_amendment,
    blockers_to_live_release,
    check_representable,
    decision_bar_is_closed,
    describe_frozen_runtime_decisions,
    frozen_config_guard_permits,
    frozen_core_basis,
    frozen_permitted_modes,
    live_release_requirements,
    release_basis_from_verdict,
    route_frozen_cycle,
    verify_amendment,
    Actor,
    AuthorityGate,
    AutonomousAuthorityIncrease,
    BrokerAccount,
    BrokerAdapter,
    BrokerHealth,
    CapabilityError,
    CapabilityMatrix,
    CapabilityStatus,
    CapitalGovernor,
    DataPurpose,
    DataSourceGuard,
    DataSourceRecord,
    ExecutionIntent,
    ExecutionState,
    FrozenLiveBoundary,
    GovernorError,
    HealthGateEvidence,
    IdempotencyLedger,
    IllegalStateTransition,
    IntentExpired,
    Lifecycle,
    LiveAuthorization,
    LiveBoundaryVerdict,
    LiveEnvironmentAttestation,
    PluginClassification,
    PortfolioSnapshot,
    PreflightEvaluator,
    PreflightReport,
    ProvenanceViolation,
    ReconciliationEngine,
    SafetyController,
    SanitizedEvidencePacket,
    SessionCalendar,
    SessionCalendarError,
    SessionStatus,
    Stage,
    SupervisorAuthorityViolation,
    SupervisorFinding,
    TransportKind,
    UnauthorizedInterface,
    UniversalBrokerGateway,
    assert_not_natural_language_evidence,
    assert_preserves_economic_meaning,
    assert_transition_allowed,
    authorization_drift,
    availability_outcome,
    capital_release,
    current_identity,
    durable_store,
    fingerprint_profile,
    frozen_constitution_rule_ids,
    interpret_supervisor_output,
    trade_valid,
    verify_frozen_core_digest,
)

# Explicit TEST values. The library ships NO financial limits; the owner supplies them.
TEST_PROFILE = {
    "max_deployable_capital": 50_000.0,
    "max_order_value": 20_000.0,
    "max_position_exposure_pct": 0.10,
    "max_portfolio_exposure_pct": 0.30,
    "max_concentration_pct": 0.50,
    "max_daily_loss_pct": 0.015,
    "max_drawdown_pct": 0.05,
    "max_positions": 3,
    "max_new_exposure_per_period": 30_000.0,
}

TEST_HOLIDAYS = {"2099-01-01": "far-future test holiday"}
SESSION_TZ = "America/New_York"

# Generous but still frozen-ceiling-respecting test values, so a route can complete once the
# frozen boundary releases. Every value is an explicit test input, never a library default.
LIVE_TEST_PROFILE = {
    "max_deployable_capital": 60_000.0,
    "max_order_value": 40_000.0,
    "max_position_exposure_pct": 0.50,
    "max_portfolio_exposure_pct": 0.50,
    "max_concentration_pct": 0.80,
    "max_daily_loss_pct": 0.03,
    "max_drawdown_pct": 0.10,
    "max_positions": 5,
    "max_new_exposure_per_period": 60_000.0,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@contextmanager
def isolated_store():
    old = durable_store.ROOT_DIR
    with tempfile.TemporaryDirectory() as td:
        durable_store.ROOT_DIR = Path(td)
        try:
            yield Path(td)
        finally:
            durable_store.ROOT_DIR = old


class TestProvider:
    """TEST DOUBLE market-data provider. Deterministic bars, fresh relative to real now."""

    def __init__(self, name: str, family: str, *, realtime: bool = True, bars=None):
        self.identity = type("Identity", (), {
            "name": name, "source_family": family, "fixed_source_kind": None,
            "can_request_realtime_entitlement": realtime,
        })()
        self.name = name
        self._bars = bars

    def bars(self, symbol, count=240):
        return list(self._bars if self._bars is not None else DemoProvider().bars(symbol, count))


class FakeAdapter(BrokerAdapter):
    """TEST DOUBLE ONLY — not shipped by the library."""

    broker_id = "test-double"

    def __init__(self, *, core_status=CapabilityStatus.SUPPORTED, unverified=(), health_ok=True,
                 account_id="ACCT-1", environment="TEST_ENV", positions=None, open_orders=None,
                 submit_exception=None, alter_meaning=False, order_caps=None,
                 represent_exception=None):
        self._core_status = core_status
        self._unverified = set(unverified)
        self._health_ok = health_ok
        self.account_id = account_id
        self.environment = environment
        self._positions = list(positions or [])
        self._open_orders = list(open_orders or [])
        self._submit_exception = submit_exception
        self._alter_meaning = alter_meaning
        self._order_caps = order_caps if order_caps is not None else FULL_ORDER_CAPS
        self._represent_exception = represent_exception
        self.submitted = []
        self.cancelled = []

    def order_capabilities(self):
        return self._order_caps

    def capability_matrix(self):
        statuses = {name: self._core_status for name in CORE_CAPABILITIES}
        for name in self._unverified:
            statuses[name] = CapabilityStatus.UNVERIFIED
        return CapabilityMatrix(statuses=statuses, source="test-double")

    def health(self):
        return BrokerHealth(connected=self._health_ok, authenticated=self._health_ok, clock_skew_seconds=0.5)

    def account(self):
        return BrokerAccount(account_id=self.account_id, environment=self.environment,
                             cash=100_000.0, equity=100_000.0, buying_power=100_000.0)

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        return list(self._open_orders)

    def order_status(self, *, client_order_id):
        return next((o for o in self._open_orders if o.get("client_order_id") == client_order_id), None)

    def represent_intent(self, intent):
        if self._represent_exception is not None:
            raise self._represent_exception
        representation = {
            "symbol": intent.symbol, "side": intent.side, "quantity": intent.quantity,
            "order_type": intent.order_type, "time_in_force": intent.time_in_force,
            "limit_price": intent.limit_price,
        }
        if self._alter_meaning:
            representation["order_type"] = "MARKET"
            representation["limit_price"] = None
        return representation

    def submit_order(self, *, client_order_id, representation):
        if self._submit_exception is not None:
            self.submitted.append(client_order_id)
            raise self._submit_exception
        self.submitted.append(client_order_id)
        return {"broker_order_id": "BO-1", "client_order_id": client_order_id,
                "state": ExecutionState.BROKER_ACKNOWLEDGED.value}

    def cancel_order(self, *, broker_order_id, reason):
        self.cancelled.append((broker_order_id, reason))
        return {"broker_order_id": broker_order_id, "state": ExecutionState.CANCELLED.value}


FULL_ORDER_CAPS = OrderCapabilities(order_types=frozenset({"LIMIT", "MARKET"}),
                                    time_in_force=frozenset({"DAY", "GTC"}),
                                    sides=frozenset({"BUY", "SELL"}), declared=True,
                                    source="test-double")


class UndeclaredOrderCapsAdapter(FakeAdapter):
    """TEST DOUBLE — mimics an adapter that never declared order support."""

    def order_capabilities(self):
        return OrderCapabilities()


class ReleasingBoundary(FrozenLiveBoundary):
    """TEST DOUBLE ONLY — proves the transmission path is real, never used in production.

    Production releases through ``FrozenLiveBoundary`` with a verified amendment basis. This
    double bypasses the boundary logic entirely, so it only ever proves that the transmission
    code path exists.
    """

    def evaluate(self, *, mode, rule_ids=None, permitted_modes=None):
        return LiveBoundaryVerdict(released=True, code="LIVE_RELEASE_PERMITTED", reasons=(),
                                   frozen_core_verified=True, constitution_rule_ids=(),
                                   mode=mode,
                                   permitted_modes=tuple(permitted_modes or (mode,)))


def approved_config():
    return validate_config(json.loads((ROOT / "engine" / "config.json").read_text()))


def trade_config():
    """A config that can produce trade-eligible data. config_guard permits real-kind providers."""
    cfg = approved_config()
    cfg["provider"] = "twelve_data"
    cfg["provider_source_kind"] = "real"
    cfg["secondary_provider"] = "alpha_vantage"
    cfg["secondary_source_kind"] = "real"
    return validate_config(cfg)


def governor(**overrides):
    profile = {**TEST_PROFILE, **overrides}
    return CapitalGovernor(profile, profile_version="test-1",
                           approved_profile_hash=fingerprint_profile(profile))


def intent(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "intent_id": "int-1", "idempotency_key": "idem-1", "correlation_id": "corr-1",
        "decision_id": "dec-1", "decision_bar_ts": (now - timedelta(hours=2)).isoformat(),
        "symbol": "SPY", "side": "BUY", "quantity": 10, "order_type": "LIMIT",
        "time_in_force": "DAY", "limit_price": 100.0, "strategy_build_id": "build-1",
        "config_id": "cfg-1", "truth_ref": "truth-1", "risk_ref": "risk-1",
        "governor_ref": "gov-1", "created_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    base.update(overrides)
    return ExecutionIntent(**base)


def portfolio(**overrides):
    base = {"equity": 100_000.0, "cash": 100_000.0, "peak_equity": 100_000.0, "daily_pnl": 0.0,
            "deployable_capital": 50_000.0, "new_exposure_this_period": 0.0,
            "positions": {}, "quantities": {}, "exposure": 0.0}
    base.update(overrides)
    return PortfolioSnapshot(**base)


def in_session_now() -> datetime:
    """A UTC instant inside a weekday US session, so session truth is deterministic."""
    tz = ZoneInfo(SESSION_TZ)
    day = datetime.now(timezone.utc).astimezone(tz).date()
    while day.weekday() >= 5 or day.isoformat() in TEST_HOLIDAYS:
        day -= timedelta(days=1)
    return datetime.combine(day, time(11, 0), tzinfo=tz).astimezone(timezone.utc)


def session_calendar(**overrides) -> SessionCalendar:
    tz = ZoneInfo(SESSION_TZ)
    today = datetime.now(timezone.utc).astimezone(tz).date()
    kwargs = {
        "provenance": "test fixture calendar, not an exchange source",
        "holidays": TEST_HOLIDAYS,
        "valid_from": (today - timedelta(days=30)).isoformat(),
        "valid_through": (today + timedelta(days=30)).isoformat(),
    }
    kwargs.update(overrides)
    return SessionCalendar(**kwargs)


def permissive_report(order, *, now, authorized_quantity=100) -> PreflightReport:
    """A maximally permissive, correctly-bound authority report. Used to prove the boundary."""
    binding = {
        "intent_content_hash": order.content_hash(), "symbol": order.symbol, "side": order.side,
        "quantity": int(order.quantity), "idempotency_key": order.idempotency_key,
        "evaluated_at": now.isoformat(),
    }
    return PreflightReport(
        checks=tuple({"name": name, "passed": True, "detail": "fixture"} for name in PRECONDITIONS),
        trade_valid={name: True for name in TRADE_VALID_FIELDS},
        capital_release={name: True for name in CAPITAL_RELEASE_FIELDS},
        artifacts={"intent_binding": binding, "authorized_quantity": authorized_quantity},
    )


def build_gateway(*, adapter=None, gov=None, stage=Stage.LIVE_LOCKED, boundary=None, registry=None,
                  safety=None):
    adapter = adapter or FakeAdapter()
    gateway = UniversalBrokerGateway(adapter=adapter, governor=gov or governor(),
                                     lifecycle=Lifecycle(stage, boundary), registry=registry,
                                     safety=safety)
    return gateway, adapter


def submit(gateway, report, *, order=None, pf=None, holdings=None, adapter_account="ACCT-1"):
    return gateway.submit(order or intent(), preflight_report=report,
                          portfolio=pf or portfolio(), holdings=holdings,
                          expected_account_id=adapter_account, expected_environment="TEST_ENV")


# ---------------------------------------------------------------------------
# Capability contract and registry
# ---------------------------------------------------------------------------


def test_capability_unverified_fails_closed():
    assert CapabilityStatus.UNVERIFIED.permits_execution is False
    assert CapabilityStatus.SUPPORTED.permits_execution is True
    matrix = CapabilityMatrix(statuses={})
    assert matrix.status("order_submission") is CapabilityStatus.UNVERIFIED
    assert "order_submission" in matrix.missing_core()


def test_registry_reports_automation_unsupported_when_core_capability_missing():
    registry = AdapterRegistry()
    registry.register(broker_id="test-double", adapter=FakeAdapter(unverified=("order_submission",)),
                      transport=TransportKind.REST,
                      classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                      authorized_interface=True, authorized_interface_evidence="official REST API")
    report = registry.conformance_report("test-double")
    assert report["verdict"]["permitted"] is False
    assert report["verdict"]["code"] == "BROKER_AUTOMATION_UNSUPPORTED"
    assert "order_submission" in report["unverified_capabilities"]


def test_registry_requires_an_authorized_interface_for_machine_callable_execution():
    registry = AdapterRegistry()
    try:
        registry.register(broker_id="x", adapter=FakeAdapter(), transport=TransportKind.REST,
                          classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                          authorized_interface=False)
        assert False, "expected UnauthorizedInterface"
    except UnauthorizedInterface:
        pass


def test_non_executing_plugin_classifications_cannot_reach_execution():
    for classification in (PluginClassification.READ_ONLY,
                           PluginClassification.INTERACTIVE_ASSISTANCE_ONLY,
                           PluginClassification.UNSUPPORTED):
        registry = AdapterRegistry()
        registry.register(broker_id="p", adapter=FakeAdapter(), transport=TransportKind.MCP_CONNECTOR,
                          classification=classification, authorized_interface=True,
                          allow_duplicate=True)
        verdict = registry.execution_verdict(registry.get("p"))
        assert verdict["permitted"] is False, classification
        assert verdict["code"] == "BROKER_AUTOMATION_UNSUPPORTED"
        try:
            registry.require_executable("p")
            assert False, "expected BrokerAutomationUnsupported"
        except Exception as exc:
            assert "BROKER_AUTOMATION_UNSUPPORTED" in str(exc) or "cannot transmit" in str(exc) \
                   or "not machine-callable" in str(exc) or "no authorized" in str(exc)


def test_executable_registry_entry_is_permitted_and_discoverable():
    registry = AdapterRegistry()
    registry.register(broker_id="test-double", adapter=FakeAdapter(), transport=TransportKind.TOKEN_SESSION,
                      classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                      authorized_interface=True, authorized_interface_evidence="official SDK")
    summary = registry.summarize()
    assert summary["executable"] == ["test-double"]
    assert registry.execution_verdict(registry.get("test-double"))["permitted"] is True
    assert registry.conformance_report("test-double")["unverified_capabilities"] == []


def test_natural_language_execution_evidence_is_rejected():
    try:
        assert_not_natural_language_evidence({"message": "Order placed"})
        assert False, "expected CapabilityError"
    except CapabilityError:
        pass
    assert_not_natural_language_evidence({"broker_order_id": "BO-1"})


# ---------------------------------------------------------------------------
# Capital Governor
# ---------------------------------------------------------------------------


def test_governor_requires_an_explicit_owner_profile():
    try:
        CapitalGovernor({"max_order_value": 1.0}, profile_version="v", approved_profile_hash="0" * 64)
        assert False, "expected GovernorError"
    except GovernorError:
        pass


def test_governor_rejects_unapproved_profile_fingerprint():
    try:
        CapitalGovernor(TEST_PROFILE, profile_version="test-1", approved_profile_hash="f" * 64)
        assert False, "expected GovernorError"
    except GovernorError:
        pass


def test_governor_rejects_profile_above_frozen_ceilings():
    for key, value in (("max_daily_loss_pct", 0.5), ("max_portfolio_exposure_pct", 0.9),
                       ("max_positions", 99), ("max_drawdown_pct", 0.9)):
        profile = {**TEST_PROFILE, key: value}
        try:
            CapitalGovernor(profile, profile_version="v",
                            approved_profile_hash=fingerprint_profile(profile))
            assert False, f"expected GovernorError for {key}"
        except GovernorError:
            pass


def test_autonomous_may_reduce_but_never_increase_authority():
    gov = governor()
    gov.apply_reduction({**TEST_PROFILE, "max_order_value": 5_000.0}, actor="AUTONOMOUS")
    try:
        gov.apply_reduction({**TEST_PROFILE, "max_order_value": 15_000.0}, actor="AUTONOMOUS")
        assert False, "expected AutonomousAuthorityIncrease"
    except AutonomousAuthorityIncrease:
        pass


def test_governor_blocks_oversized_order_with_explicit_reason():
    decision = governor(max_order_value=500.0).evaluate(
        intent=intent(quantity=10), portfolio=portfolio(), price=100.0)
    assert decision.allowed is False
    assert "max_order_value" in decision.reasons


def test_portfolio_snapshot_requires_owner_supplied_deployable_capital():
    state = {"equity": 1000.0, "cash": 1000.0, "peak_equity": 1000.0, "positions": {}}
    import inspect

    signature = inspect.signature(PortfolioSnapshot.from_runtime_state)
    assert "deployable_capital" in signature.parameters
    assert signature.parameters["deployable_capital"].default is inspect.Parameter.empty
    snapshot = PortfolioSnapshot.from_runtime_state(state, deployable_capital=500.0)
    assert snapshot.deployable_capital == 500.0
    assert snapshot.drawdown_pct == 0.0


def test_portfolio_snapshot_derives_exposure_and_drawdown_from_positions():
    state = {
        "equity": 90_000.0, "cash": 10_000.0, "peak_equity": 100_000.0, "daily_pnl": -100.0,
        "last_prices": {"SPY": 100.0},
        "positions": {"SPY": {"entry": 90.0, "qty": 800}},
        "pending_entries": {"QQQ": {"signal_bar_ts": "t"}}, "cooldown_remaining": 2,
        "halted": True, "halt_reason": "test",
    }
    snapshot = PortfolioSnapshot.from_runtime_state(state, deployable_capital=50_000.0)
    assert snapshot.exposure == 80_000.0
    assert snapshot.quantities == {"SPY": 800}
    assert abs(snapshot.drawdown_pct - 0.10) < 1e-9
    assert snapshot.halted is True and snapshot.cooldown_remaining == 2
    assert set(snapshot.pending_entries) == {"QQQ"}


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


def test_expired_intent_is_refused_and_never_auto_extended():
    now = datetime.now(timezone.utc)
    stale = intent(created_at=(now - timedelta(hours=5)).isoformat(),
                   expires_at=(now - timedelta(hours=4)).isoformat())
    try:
        stale.require_fresh(now)
        assert False, "expected IntentExpired"
    except IntentExpired:
        pass


def test_market_intent_may_not_carry_a_limit_price():
    try:
        intent(order_type="MARKET", limit_price=100.0)
        assert False, "expected IntentError"
    except Exception as exc:
        assert type(exc).__name__ == "IntentError"


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_gate_authorizes_only_when_both_permissions_are_true():
    decision = AuthorityGate().evaluate(trade=trade_valid({n: True for n in TRADE_VALID_FIELDS}),
                                       capital=capital_release({n: True for n in CAPITAL_RELEASE_FIELDS}))
    assert decision.authorized is True


def test_gate_blocks_when_either_permission_is_false():
    for tweak in ({"risk_approved": False}, {"truth_valid": False}):
        trade_ev = {n: True for n in TRADE_VALID_FIELDS}
        trade_ev.update(tweak)
        decision = AuthorityGate().evaluate(
            trade=trade_valid(trade_ev),
            capital=capital_release({n: True for n in CAPITAL_RELEASE_FIELDS}))
        assert decision.authorized is False
        assert decision.decision == "EXECUTION_BLOCKED"


def test_gate_treats_missing_evidence_as_denied():
    decision = AuthorityGate().evaluate(trade=trade_valid({"truth_valid": True}),
                                       capital=capital_release({"governor_permits": True}))
    assert decision.authorized is False
    assert "unknown state is not permission" in decision.trade_valid.checks[1]["detail"]


# ---------------------------------------------------------------------------
# Preflight — the real frozen chain
# ---------------------------------------------------------------------------


def make_evaluator(*, config=None, approved_hash=None, provider=None, secondary=None,
                   adapter=None, registry=None, ledger=None, gov=None, calendar=None,
                   safety=None, authorization=None, data_guard=None):
    config = config or approved_config()
    return PreflightEvaluator(
        config=config, approved_config_hash=approved_hash or fingerprint_config(config),
        governor=gov or governor(), adapter=adapter, registry=registry or AdapterRegistry(),
        lifecycle=Lifecycle(), ledger=ledger or IdempotencyLedger(), provider=provider,
        secondary_provider=secondary, session_calendar=calendar or session_calendar(),
        expected_account_id="ACCT-1", expected_environment="TEST_ENV",
        authorization=authorization, data_guard=data_guard,
        reconciliation=ReconciliationEngine(), safety=safety or SafetyController())


def test_preflight_rejects_unapproved_configuration():
    try:
        make_evaluator(approved_hash="f" * 64)
        assert False, "expected ConfigError"
    except ConfigError:
        pass


def test_preflight_reports_exactly_the_twenty_preconditions_in_spec_order():
    report = make_evaluator().evaluate(intent=intent(), portfolio=portfolio(), price=100.0)
    assert [c["name"] for c in report.checks] == list(PRECONDITIONS)
    assert len(report.checks) == 20


def test_real_chain_truth_vetoes_demo_data():
    """The frozen Truth Engine, not this layer, refuses trade eligibility for demo data."""
    report = make_evaluator(provider=DemoProvider()).evaluate(
        intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.trade_valid["truth_valid"] is False
    assert "healthy_market_data" not in report.failing  # demo data IS analysis-eligible
    assert "valid_truth_state" in report.failing        # but NOT trade-eligible
    assert "not eligible for current-market trade decisions" in str(report.artifacts["truth"]["reasons"])


def test_real_chain_truth_vetoes_when_independent_sources_disagree():
    primary_bars = DemoProvider().bars("SPY", 240)
    shifted = [replace(b, close=b.close * 1.05, high=b.high * 1.05) for b in primary_bars]
    evaluator = make_evaluator(config=trade_config(), provider=TestProvider("a", "fam_a"),
                               secondary=TestProvider("b", "fam_b", bars=shifted))
    evaluator.provider._bars = primary_bars
    report = evaluator.evaluate(intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.trade_valid["truth_valid"] is False
    assert "valid_truth_state" in report.failing
    assert report.artifacts["truth"]["cross_source"]["reason"] != "sources_agree"


def test_real_chain_reaches_trade_eligible_truth_and_still_refuses_on_session_and_broker():
    """Full pipeline with agreeing independent real-kind sources: truth passes, policy refuses."""
    bars = DemoProvider().bars("SPY", 240)
    evaluator = make_evaluator(config=trade_config(), provider=TestProvider("a", "fam_a", bars=bars),
                               secondary=TestProvider("b", "fam_b", bars=bars))
    report = evaluator.evaluate(intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.artifacts["truth"]["trusted_for_trade"] is True
    assert report.artifacts["truth"]["cross_source"]["passed"] is True
    assert report.trade_valid["truth_valid"] is True
    # No adapter, no registry, no attestation, no health gate: capital release still refuses.
    assert report.capital_release["broker_healthy"] is False
    assert report.capital_release["live_environment_verified"] is False
    assert report.capital_release["no_applicable_halt"] is False
    assert "correct_authorized_broker_adapter" in report.failing


def test_preflight_computes_authorized_quantity_from_the_frozen_risk_engine():
    bars = DemoProvider().bars("SPY", 240)
    evaluator = make_evaluator(config=trade_config(), provider=TestProvider("a", "fam_a", bars=bars),
                               secondary=TestProvider("b", "fam_b", bars=bars))
    report = evaluator.evaluate(intent=intent(), portfolio=portfolio(), price=100.0)
    authorized = report.artifacts["authorized_quantity"]
    assert authorized > 0
    assert report.artifacts["size_within_risk_authorization"] is True


def test_preflight_flags_oversized_intent_against_the_frozen_authorization():
    bars = DemoProvider().bars("SPY", 240)
    evaluator = make_evaluator(config=trade_config(), provider=TestProvider("a", "fam_a", bars=bars),
                               secondary=TestProvider("b", "fam_b", bars=bars))
    report = evaluator.evaluate(intent=intent(quantity=100_000), portfolio=portfolio(), price=100.0)
    assert report.artifacts["size_within_risk_authorization"] is False
    assert report.trade_valid["scope_valid"] is False


def test_preflight_session_truth_blocks_when_no_exchange_calendar_is_supplied():
    report = make_evaluator(calendar=SessionCalendar(), provider=DemoProvider()).evaluate(
        intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.artifacts["session"]["status"] == SessionStatus.UNKNOWN.value
    assert "valid_market_session" in report.failing


def test_preflight_requires_a_fresh_health_gate():
    now = in_session_now()
    evaluator = make_evaluator(provider=DemoProvider())
    stale = HealthGateEvidence(passed=True, checks=(), generated_at=(now - timedelta(hours=4)).isoformat(),
                               max_age_minutes=30)
    report = evaluator.evaluate(intent=intent(), portfolio=portfolio(), price=100.0, now=now,
                                health_gate=stale)
    assert "global_halt_not_active" in report.failing
    assert report.artifacts["health_gate_valid"] is False


def test_preflight_requires_a_live_environment_attestation():
    now = in_session_now()
    evaluator = make_evaluator(provider=DemoProvider())
    report = evaluator.evaluate(intent=intent(), portfolio=portfolio(), price=100.0, now=now)
    assert report.capital_release["live_environment_verified"] is False
    assert report.artifacts["live_environment"]["verified"] is False


def test_preflight_detects_authorization_drift():
    auth = LiveAuthorization(strategy_build_id="x" * 64, config_id="y" * 64, risk_profile_id="z" * 64,
                             governor_profile_id="w" * 64, broker_id="test-double",
                             account_id="ACCT-1", environment="TEST_ENV",
                             issued_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                             expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                             owner_signed=True)
    report = make_evaluator(authorization=auth, provider=DemoProvider()).evaluate(
        intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.artifacts["authorization_drift"]
    assert "approved_capital_governor_profile" in report.failing


def test_preflight_authorization_without_drift_passes_that_precondition():
    current = current_identity(config=approved_config(), governor_profile_hash=governor().profile_hash)
    auth = LiveAuthorization(strategy_build_id=current["build_hash"], config_id=current["config_hash"],
                             risk_profile_id=current["risk_profile_hash"],
                             governor_profile_id=current["governor_profile_hash"],
                             broker_id="test-double", account_id="ACCT-1", environment="TEST_ENV",
                             issued_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                             expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                             owner_signed=True)
    report = make_evaluator(authorization=auth, provider=DemoProvider()).evaluate(
        intent=intent(), portfolio=portfolio(), price=100.0)
    assert report.artifacts["authorization_drift"] == []
    assert "approved_capital_governor_profile" not in report.failing


# ---------------------------------------------------------------------------
# THE DECISIVE TEST: LIVE_LOCKED is terminal
# ---------------------------------------------------------------------------


def test_permissive_report_still_terminates_live_locked():
    """A maximally permissive, correctly-bound report reaches AUTHORIZED and still cannot send."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        registry = AdapterRegistry()
        adapter = FakeAdapter()
        registry.register(broker_id="test-double", adapter=adapter, transport=TransportKind.REST,
                          classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                          authorized_interface=True, authorized_interface_evidence="official REST API")
        gateway, _ = build_gateway(adapter=adapter, registry=registry)
        result = submit(gateway, permissive_report(order, now=now), order=order)

        assert result.gate["decision"] == "EXECUTION_AUTHORIZED"
        assert result.outcome == "LIVE_LOCKED_REFUSAL"
        assert result.state == ExecutionState.REFUSED.value
        assert result.transmitted is False
        assert adapter.submitted == [], "adapter must never receive an order while LIVE_LOCKED"
        assert any("PAPER_FIRST" in reason for reason in result.reasons)
        assert gateway.lifecycle.may_transmit_live()["permitted"] is False


def test_real_chain_refusal_is_authority_not_live_lock():
    """With real (non-permissive) preflight the refusal happens earlier, at the gate."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        report = make_evaluator(provider=DemoProvider()).evaluate(
            intent=order, portfolio=portfolio(), price=100.0, now=now)
        report = replace(report, artifacts={**report.artifacts,
                                            "intent_binding": {**report.artifacts["intent_binding"],
                                                               "evaluated_at": now.isoformat()}})
        gateway, adapter = build_gateway()
        result = submit(gateway, report, order=order)
        assert result.outcome == "REFUSED_BY_AUTHORITY"
        assert result.transmitted is False
        assert adapter.submitted == []


def test_frozen_boundary_verifies_the_core_digest_and_detects_the_prohibition():
    assert verify_frozen_core_digest()["verified"] is True
    assert "PAPER_FIRST" in frozen_constitution_rule_ids()
    verdict = FrozenLiveBoundary().evaluate(mode="paper")
    assert verdict.released is False
    assert verdict.code == "REFUSED_BY_FROZEN_LIVE_BOUNDARY"
    assert any("PAPER_FIRST" in reason for reason in verdict.reasons)
    hypothetical = FrozenLiveBoundary().evaluate(mode="live", rule_ids=("LIVE_GATE",))
    assert hypothetical.released is False
    assert any("config mode" in reason for reason in hypothetical.reasons)


def test_frozen_core_has_no_broker_specific_branches():
    for name in ("strategies.py", "risk.py", "truth_guard.py", "constitution.py"):
        text = (ROOT / "engine" / name).read_text().lower()
        for broker in ("alpaca", "ibkr", "interactive_brokers", "tradier", "schwab", "robinhood"):
            assert broker not in text, f"{name} must not contain broker-specific branch {broker}"


# ---------------------------------------------------------------------------
# Gateway boundary enforcement
# ---------------------------------------------------------------------------


def test_unbound_preflight_report_is_refused():
    with isolated_store():
        now = datetime.now(timezone.utc)
        report = permissive_report(intent(), now=now)
        report = replace(report, artifacts={"authorized_quantity": 100})
        result = submit(build_gateway()[0], report)
        assert result.outcome == "PREFLIGHT_NOT_BOUND"


def test_preflight_report_for_a_different_intent_is_refused():
    with isolated_store():
        now = datetime.now(timezone.utc)
        report = permissive_report(intent(), now=now)
        other = intent(intent_id="int-2", idempotency_key="idem-2", quantity=999)
        result = submit(build_gateway()[0], report, order=other)
        assert result.outcome == "PREFLIGHT_NOT_BOUND"
        assert any("different intent" in r or "quantity" in r for r in result.reasons)


def test_stale_preflight_report_is_refused():
    with isolated_store():
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=30)
        order = intent()
        report = permissive_report(order, now=old)
        result = build_gateway()[0].submit(
            order, preflight_report=report, portfolio=portfolio(),
            expected_account_id="ACCT-1", expected_environment="TEST_ENV", now=now)
        assert result.outcome == "PREFLIGHT_NOT_BOUND"
        assert any("stale" in reason for reason in result.reasons)


def test_scope_rejects_symbol_outside_the_approved_mandate():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent(symbol="TSLA")
        result = submit(build_gateway()[0], permissive_report(order, now=now), order=order)
        assert result.outcome == "REFUSED_BY_SCOPE"


def test_long_only_rejects_a_short_and_an_over_sell():
    with isolated_store():
        now = datetime.now(timezone.utc)
        gateway, adapter = build_gateway()
        # The same object must back both the report and the submission: intent() stamps
        # created_at/expires_at, so two calls produce different content hashes by design.
        bare_sell = intent(side="SELL")
        no_holding = submit(gateway, permissive_report(bare_sell, now=now), order=bare_sell)
        assert no_holding.outcome == "REFUSED_BY_SCOPE"
        over = intent(idempotency_key="idem-2", side="SELL", quantity=50)
        over_sell = submit(gateway, permissive_report(over, now=now), order=over, holdings={"SPY": 5})
        assert over_sell.outcome == "REFUSED_BY_SCOPE"
        assert adapter.submitted == []


def test_quantity_above_the_authorized_size_is_refused_at_the_boundary():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent(quantity=500)
        gateway, adapter = build_gateway()
        result = submit(gateway, permissive_report(order, now=now, authorized_quantity=100), order=order)
        assert result.outcome == "REFUSED_BY_SCOPE"
        assert any("authorized size" in reason for reason in result.reasons)
        assert adapter.submitted == []


def test_broker_state_change_after_preflight_is_caught():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        adapter = FakeAdapter()
        registry = AdapterRegistry()
        registry.register(broker_id="test-double", adapter=adapter, transport=TransportKind.REST,
                          classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                          authorized_interface=True, authorized_interface_evidence="official REST API")
        gateway, _ = build_gateway(adapter=adapter, registry=registry)
        report = permissive_report(order, now=now)
        adapter.account_id = "ACCT-OTHER"  # changes between preflight and submission
        result = submit(gateway, report, order=order)
        assert result.outcome == "BROKER_STATE_CHANGED"
        assert adapter.submitted == []


def test_duplicate_idempotency_key_is_suppressed_and_survives_restart():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        gateway, adapter = build_gateway()
        first = submit(gateway, permissive_report(order, now=now), order=order)
        second = submit(gateway, permissive_report(order, now=now), order=order)
        assert first.outcome == "LIVE_LOCKED_REFUSAL"
        assert second.outcome == "DUPLICATE_SUPPRESSED"
        assert adapter.submitted == []
        assert "idem-1" in IdempotencyLedger().all_entries()


def test_transmission_path_is_real_and_gated_only_by_the_frozen_boundary():
    """Proves step 8 is implemented, not a placeholder, while proving it is unreachable."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        blocked_order = intent()
        production, blocked_adapter = build_gateway(stage=Stage.LIVE_ENABLED)
        assert submit(production, permissive_report(blocked_order, now=now), order=blocked_order).outcome \
            == "LIVE_LOCKED_REFUSAL"
        assert blocked_adapter.submitted == []

        live_order = intent(intent_id="int-live", idempotency_key="idem-live")
        released, live_adapter = build_gateway(stage=Stage.LIVE_ENABLED, boundary=ReleasingBoundary())
        transmitted = submit(released, permissive_report(live_order, now=now), order=live_order)
        assert transmitted.outcome == "TRANSMITTED"
        assert transmitted.transmitted is True
        assert len(live_adapter.submitted) == 1
        entry = released.ledger.entry("idem-live")
        assert entry["broker_order_id"] == "BO-1" and entry["attempts"] == 1


def test_lost_response_becomes_unknown_pending_and_is_never_resubmitted():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        adapter = FakeAdapter(submit_exception=TimeoutError("response lost"))
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED, boundary=ReleasingBoundary())
        first = submit(gateway, permissive_report(order, now=now), order=order)
        assert first.outcome == "UNKNOWN_PENDING_RECONCILIATION"
        assert first.transmitted is False
        assert len(adapter.submitted) == 1
        again = submit(gateway, permissive_report(order, now=now), order=order)
        assert again.outcome == "DUPLICATE_SUPPRESSED"
        assert len(adapter.submitted) == 1, "a lost response must never trigger a resubmission"


def test_resolve_unknown_requires_broker_evidence_and_never_infers_rejection():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        gateway, _ = build_gateway(adapter=FakeAdapter(submit_exception=TimeoutError("lost")),
                                   stage=Stage.LIVE_ENABLED, boundary=ReleasingBoundary())
        submit(gateway, permissive_report(order, now=now), order=order)
        unresolved = gateway.resolve_unknown("idem-1", broker_orders=[])
        assert unresolved["resolved"] is False
        assert unresolved["code"] == "UNRESOLVED_PENDING_RECONCILIATION"
        client_id = next(iter(gateway.ledger.all_entries().values()))["client_order_id"]
        resolved = gateway.resolve_unknown(
            "idem-1", broker_orders=[{"client_order_id": client_id, "broker_order_id": "BO-9",
                                      "state": ExecutionState.BROKER_ACKNOWLEDGED.value}])
        assert resolved["resolved"] is True


def test_adapter_cannot_alter_economic_meaning():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        adapter = FakeAdapter(alter_meaning=True)
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED, boundary=ReleasingBoundary())
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "REFUSED_BY_CAPABILITY"
        assert any("economic meaning" in reason for reason in result.reasons)
        assert adapter.submitted == []


def test_undeclared_order_capabilities_fail_closed():
    """An adapter that never declared order support can never transmit. Unknown is not supported."""
    verdict = check_representable(intent(), OrderCapabilities())
    assert verdict["permitted"] is False
    assert verdict["code"] == "CAPABILITY_UNSUPPORTED"
    assert check_representable(intent(), "not-declared")["code"] == "CAPABILITY_UNSUPPORTED"


def test_order_incompatible_when_the_broker_cannot_represent_the_intent():
    """A broker that cannot express the APPROVED order shape gets a code and no trade."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent(order_type="MARKET", limit_price=None)
        caps = OrderCapabilities(order_types=frozenset({"LIMIT"}),
                                 time_in_force=frozenset({"DAY", "GTC"}),
                                 sides=frozenset({"BUY", "SELL"}), declared=True)
        adapter = FakeAdapter(order_caps=caps)
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED,
                                   boundary=ReleasingBoundary())
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "ORDER_INCOMPATIBLE"
        assert result.state == ExecutionState.REFUSED.value
        assert adapter.submitted == []
        assert any("order_type MARKET" in reason for reason in result.reasons)
        assert any("substitute nothing" in reason for reason in result.reasons)


def test_unrepresentable_intent_consumes_no_submission_reservation():
    """Translation is proven BEFORE a reservation is taken, so nothing is left in flight."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent(order_type="MARKET", limit_price=None)
        caps = OrderCapabilities(order_types=frozenset({"LIMIT"}), time_in_force=frozenset({"DAY"}),
                                 sides=frozenset({"BUY"}), declared=True)
        gateway, _ = build_gateway(adapter=FakeAdapter(order_caps=caps), stage=Stage.LIVE_ENABLED,
                                   boundary=ReleasingBoundary())
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "ORDER_INCOMPATIBLE"
        entry = gateway.ledger.entry("idem-1")
        assert entry["state"] == ExecutionState.REFUSED.value, "a refusal is terminal, never in flight"
        assert entry["attempts"] == 0
        assert gateway.ledger.open_client_order_ids() == ()
        assert all(s["state"] not in {"SUBMISSION_RESERVED", "SUBMITTING"}
                   for s in entry["history"]), "an unrepresentable intent reserves no submission"


def test_adapter_fault_during_translation_is_a_code_not_a_crash():
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        adapter = FakeAdapter(represent_exception=NotImplementedError("no order ticket"))
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED,
                                   boundary=ReleasingBoundary())
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "REFUSED_BY_CAPABILITY"
        assert any("CAPABILITY_UNSUPPORTED" in reason for reason in result.reasons)
        assert adapter.submitted == []
        assert gateway.ledger.open_client_order_ids() == ()


def test_incomplete_decision_bar_cannot_be_transmitted():
    """The closed-bar guarantee is re-proven at the ORDER, not only at the decision."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent(decision_bar_ts=now.isoformat())
        adapter = FakeAdapter()
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED,
                                   boundary=ReleasingBoundary())
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "DECISION_BAR_NOT_CLOSED"
        assert adapter.submitted == [], "an incomplete decision bar must never reach the broker"


def test_decision_bar_closure_reuses_the_frozen_bar_semantics():
    now = datetime.now(timezone.utc)
    assert decision_bar_is_closed(intent(decision_bar_ts=(now - timedelta(hours=2)).isoformat()),
                                  interval="60min", now=now)[0] is True
    closed, detail = decision_bar_is_closed(intent(decision_bar_ts=(now - timedelta(minutes=30)).isoformat()),
                                            interval="60min", now=now)
    assert closed is False and "does not close until" in detail
    naive, detail = decision_bar_is_closed(intent(), interval="60min",
                                           now=datetime(2026, 1, 1, 0, 0))
    assert naive is False and "timezone-aware" in detail


def test_supervisor_halt_arriving_after_preflight_blocks_new_exposure():
    """A halt raised between preflight and transmission still blocks. It can only tighten."""
    with isolated_store():
        now = datetime.now(timezone.utc)
        order = intent()
        safety = SafetyController()
        adapter = FakeAdapter()
        gateway, _ = build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED,
                                   boundary=ReleasingBoundary(), safety=safety)
        safety.apply(interpret_supervisor_output(
            {"finding": "SUPERVISOR_HALT_REQUEST", "evidence_refs": ["e:1"],
             "explanation": "unresolved ambiguity"}))
        result = submit(gateway, permissive_report(order, now=now), order=order)
        assert result.outcome == "SUPERVISOR_BLOCKED_NEW_EXPOSURE"
        assert adapter.submitted == []


def test_rule_based_supervisor_is_deterministic_and_only_tightens():
    runner = SupervisorRunner(provider=RuleBasedSupervisorProvider())
    clean = SanitizedEvidencePacket(
        fields={name: ("OK" if name not in ("risk_utilization", "capital_utilization",
                                            "recent_decisions", "order_lifecycle",
                                            "student_findings")
                      else (0.1 if "utilization" in name else []))
                for name in EVIDENCE_ALLOWLIST},
        generated_at="2026-09-29T00:00:00+00:00")
    first, second = runner.run(clean), runner.run(clean)
    assert first.finding is SupervisorFinding.NORMAL
    assert first.to_dict() == second.to_dict(), "supervision must be deterministic"
    assert first.execution_authority == "NONE" and first.may_only_tighten is True
    assert runner.permits_new_exposure(first)["permitted"] is True
    bad = SanitizedEvidencePacket(fields={"broker_health": "DOWN"},
                                  generated_at="2026-09-29T00:00:00+00:00")
    escalated = runner.run(bad)
    assert escalated.finding is SupervisorFinding.SUPERVISOR_HALT_REQUEST
    assert runner.permits_new_exposure(escalated)["blocked"] is True


def test_missing_evidence_escalates_to_investigate_never_normal():
    runner = SupervisorRunner(provider=RuleBasedSupervisorProvider())
    outcome = runner.run(SanitizedEvidencePacket(fields={"recent_decisions": []},
                                                 generated_at="2026-09-29T00:00:00+00:00"))
    assert outcome.finding is SupervisorFinding.INVESTIGATE
    assert any(ref.startswith("missing_evidence:") for ref in outcome.evidence_refs)


def test_supervisor_runner_records_unavailable_and_policy_decides():
    packet = SanitizedEvidencePacket(fields={}, generated_at="2026-09-29T00:00:00+00:00")
    strict = SupervisorRunner(provider=UnavailableSupervisorProvider("no provider configured"))
    outcome = strict.run(packet)
    assert outcome.availability == "SUPERVISOR_UNAVAILABLE"
    assert strict.permits_new_exposure(outcome)["blocked"] is True, "absence is never approval"
    explicit = SupervisorRunner(provider=UnavailableSupervisorProvider(),
                                policy=SupervisorPolicy(block_new_exposure_when_unavailable=False))
    assert explicit.permits_new_exposure(explicit.run(packet))["blocked"] is False
    assert strict.policy.describe()["block_new_exposure_when_unavailable"] is True


def test_supervisor_runner_rejects_order_shaped_provider_output():
    class RogueProvider(RuleBasedSupervisorProvider):
        provider_id = "rogue"

        def evaluate(self, packet):
            return {"finding": "NORMAL", "side": "BUY", "quantity": 500}

    runner = SupervisorRunner(provider=RogueProvider())
    try:
        runner.run(SanitizedEvidencePacket(fields={}, generated_at="2026-09-29T00:00:00+00:00"))
        assert False, "expected SupervisorAuthorityViolation"
    except SupervisorAuthorityViolation as exc:
        assert "side" in str(exc) and "quantity" in str(exc)


def test_shipped_package_ships_no_concrete_broker_adapter():
    """No fake adapter may be marked complete. Only the ABC exists in the package."""
    import re

    package = ROOT / "engine" / "execution"
    found = []
    for path in sorted(package.glob("*.py")):
        for match in re.findall(r"^class\s+(\w+)\((?:[^)]*\bBrokerAdapter\b[^)]*)\)",
                                path.read_text(), flags=re.MULTILINE):
            found.append(f"{path.name}:{match}")
    assert found == [], f"concrete broker adapters must not ship in the package: {found}"


def test_economic_meaning_checker_rejects_each_forbidden_conversion():
    order = intent()
    good = {"symbol": "SPY", "side": "BUY", "quantity": 10, "order_type": "LIMIT",
            "time_in_force": "DAY", "limit_price": 100.0}
    assert_preserves_economic_meaning(order, good)
    for field, bad in (("symbol", "QQQ"), ("side", "SELL"), ("quantity", 11),
                       ("order_type", "MARKET"), ("time_in_force", "GTC"), ("limit_price", 101.0)):
        try:
            assert_preserves_economic_meaning(order, {**good, field: bad})
            assert False, f"expected CapabilityError for {field}"
        except CapabilityError:
            pass


def test_illegal_canonical_transitions_are_rejected():
    assert_transition_allowed(ExecutionState.READY, ExecutionState.SUBMISSION_RESERVED)
    for current, nxt in ((ExecutionState.FILLED, ExecutionState.SUBMITTING),
                         (ExecutionState.REFUSED, ExecutionState.SUBMITTING)):
        try:
            assert_transition_allowed(current, nxt)
            assert False, "expected IllegalStateTransition"
        except IllegalStateTransition:
            pass


# ---------------------------------------------------------------------------
# Lifecycle and identity drift
# ---------------------------------------------------------------------------


def test_no_component_other_than_the_owner_can_promote():
    for actor in (Actor.STUDENT, Actor.EVOLUTION, Actor.STRATEGY, Actor.GUARDIAN,
                  Actor.SUPERVISOR, Actor.BROKER, Actor.TESTS):
        outcome = Lifecycle(Stage.PAPER).advance(Stage.LIVE_LOCKED, actor=actor)
        assert outcome["advanced"] is False
        assert outcome["code"] == "PROMOTION_REFUSED_NOT_OWNER"


def valid_authorization():
    now = datetime.now(timezone.utc)
    return LiveAuthorization(strategy_build_id="b", config_id="c", risk_profile_id="r",
                             governor_profile_id="g", broker_id="test-double", account_id="ACCT-1",
                             environment="TEST_ENV", issued_at=(now - timedelta(minutes=1)).isoformat(),
                             expires_at=(now + timedelta(hours=1)).isoformat(), owner_signed=True)


def test_live_enabled_is_unreachable_even_with_a_valid_owner_authorization():
    outcome = Lifecycle(Stage.LIVE_LOCKED).advance(Stage.LIVE_ENABLED, actor=Actor.OWNER,
                                                   authorization=valid_authorization())
    assert outcome["advanced"] is False
    assert outcome["code"] == "LIVE_LOCKED_REFUSAL"


def test_live_enabled_requires_an_authorization_artifact_at_all():
    outcome = Lifecycle(Stage.LIVE_LOCKED).advance(Stage.LIVE_ENABLED, actor=Actor.OWNER)
    assert outcome["advanced"] is False
    assert outcome["code"] == "LIVE_ENABLE_REFUSED_NO_AUTHORIZATION"


def test_live_authorization_drift_is_refused_even_with_a_releasing_boundary():
    with isolated_store():
        outcome = Lifecycle(Stage.LIVE_LOCKED, ReleasingBoundary()).advance(
            Stage.LIVE_ENABLED, actor=Actor.OWNER, authorization=valid_authorization(),
            config=approved_config(), governor_profile_hash="a" * 64)
        assert outcome["advanced"] is False
        assert outcome["code"] == "LIVE_ENABLE_REFUSED_AUTHORIZATION_DRIFT"


def test_default_stage_is_live_locked():
    assert Lifecycle().stage is Stage.LIVE_LOCKED


def test_authorization_drift_detects_material_change():
    gov = governor()
    base = current_identity(config=approved_config(), governor_profile_hash=gov.profile_hash)
    auth = LiveAuthorization(strategy_build_id=base["build_hash"], config_id=base["config_hash"],
                             risk_profile_id=base["risk_profile_hash"],
                             governor_profile_id=base["governor_profile_hash"], broker_id="b",
                             account_id="a", environment="e", issued_at="2026-01-01T00:00:00+00:00",
                             expires_at="2026-01-01T01:00:00+00:00", owner_signed=True)
    drifted, reasons = authorization_drift(auth, base)
    assert drifted is False and reasons == []

    changed = dict(base)
    changed["symbol_scope_hash"] = "different"
    changed["config_hash"] = "different"
    drifted, reasons = authorization_drift(auth, changed)
    assert drifted is True
    assert any("config_id" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# Session truth
# ---------------------------------------------------------------------------


def test_session_truth_fails_closed_without_a_calendar():
    assert SessionCalendar().evaluate(datetime.now(timezone.utc)).status is SessionStatus.UNKNOWN


def test_session_truth_requires_a_validity_window():
    calendar = SessionCalendar(provenance="test", holidays=TEST_HOLIDAYS)
    assert calendar.evaluate(datetime.now(timezone.utc)).status is SessionStatus.UNKNOWN


def test_session_truth_weekend_and_holiday_are_closed():
    tz = ZoneInfo(SESSION_TZ)
    saturday = datetime(2026, 9, 26, 12, 0, tzinfo=tz).astimezone(timezone.utc)
    assert saturday.astimezone(tz).weekday() == 5
    calendar = session_calendar(valid_from="2026-01-01", valid_through="2026-12-31",
                                holidays={"2026-12-25": "Christmas"})
    assert calendar.evaluate(saturday).status is SessionStatus.CLOSED

    holiday_calendar = session_calendar(valid_from="2026-01-01", valid_through="2026-12-31",
                                        holidays={"2026-09-24": "test holiday"})
    thursday = datetime(2026, 9, 24, 12, 0, tzinfo=tz).astimezone(timezone.utc)
    verdict = holiday_calendar.evaluate(thursday)
    assert verdict.status is SessionStatus.CLOSED
    assert verdict.code == "SESSION_HOLIDAY"


def test_session_truth_is_open_inside_the_session_and_respects_early_close():
    tz = ZoneInfo(SESSION_TZ)
    calendar = session_calendar(valid_from="2026-01-01", valid_through="2026-12-31")
    midday = datetime(2026, 9, 24, 11, 0, tzinfo=tz).astimezone(timezone.utc)
    assert calendar.evaluate(midday).status is SessionStatus.OPEN

    early = session_calendar(valid_from="2026-01-01", valid_through="2026-12-31",
                             early_closes={"2026-11-27": "13:00"})
    after_early_close = datetime(2026, 11, 27, 14, 0, tzinfo=tz).astimezone(timezone.utc)
    assert early.evaluate(after_early_close).status is SessionStatus.CLOSED


def test_session_truth_outside_the_calendar_window_is_unknown():
    calendar = session_calendar(valid_from="2020-01-01", valid_through="2020-12-31")
    assert calendar.evaluate(datetime.now(timezone.utc)).status is SessionStatus.UNKNOWN


def test_session_calendar_rejects_malformed_times():
    try:
        SessionCalendar(provenance="x", holidays=TEST_HOLIDAYS, open_time="9:30")
        assert False, "expected SessionCalendarError"
    except SessionCalendarError:
        pass


# ---------------------------------------------------------------------------
# Data provenance separation
# ---------------------------------------------------------------------------


def test_broker_execution_feed_cannot_supply_strategy_or_truth():
    guard = DataSourceGuard(approved_sources=("twelvedata",), approved_families=("t12",))
    broker_feed = DataSourceRecord(source="broker", source_family="broker", origin="broker_execution_feed",
                                   approved_for_truth=True)
    for purpose in (DataPurpose.STRATEGY_ANALYSIS, DataPurpose.TRADE_ELIGIBILITY):
        try:
            guard.admit(broker_feed, purpose=purpose)
            assert False, f"expected ProvenanceViolation for {purpose}"
        except ProvenanceViolation:
            pass
    assert guard.check(broker_feed, purpose=DataPurpose.EXECUTION_RECONCILIATION)["permitted"] is True


def test_unapproved_market_source_is_rejected():
    guard = DataSourceGuard(approved_sources=("twelvedata",))
    record = DataSourceRecord(source="random_blog", source_family="t12", origin="market_data_provider",
                              approved_for_truth=True)
    assert guard.check(record, purpose=DataPurpose.TRADE_ELIGIBILITY)["permitted"] is False


def test_approved_market_source_is_admitted():
    guard = DataSourceGuard(approved_sources=("twelvedata",), approved_families=("t12",))
    record = DataSourceRecord(source="twelvedata", source_family="t12", origin="market_data_provider",
                              approved_for_truth=True)
    assert guard.check(record, purpose=DataPurpose.TRADE_ELIGIBILITY)["permitted"] is True


# ---------------------------------------------------------------------------
# AI Supervisor: one-way safety authority
# ---------------------------------------------------------------------------


def test_supervisor_order_shaped_output_is_rejected():
    for field in ("symbol", "quantity", "price", "side", "stop", "time_in_force", "account_id",
                  "capital_limit", "broker"):
        try:
            interpret_supervisor_output({"finding": "NORMAL", field: "anything"})
            assert False, f"expected SupervisorAuthorityViolation for {field}"
        except SupervisorAuthorityViolation:
            pass


def test_supervisor_finding_outside_the_vocabulary_is_rejected():
    try:
        interpret_supervisor_output({"finding": "BUY_NOW"})
        assert False, "expected SupervisorAuthorityViolation"
    except SupervisorAuthorityViolation:
        pass


def test_non_normal_finding_must_cite_evidence():
    try:
        interpret_supervisor_output({"finding": "SUPERVISOR_HALT_REQUEST"})
        assert False, "expected SupervisorAuthorityViolation"
    except SupervisorAuthorityViolation:
        pass


def test_supervisor_halt_blocks_new_exposure_only_and_cannot_resume():
    safety = SafetyController()
    outcome = interpret_supervisor_output({
        "finding": "SUPERVISOR_HALT_REQUEST", "evidence_refs": ["packet:abc"],
        "explanation": "reconciliation discrepancy cluster"})
    assert outcome.execution_authority == "NONE"
    action = safety.apply(outcome)
    assert action["new_exposure_blocked"] is True
    assert action["cancels_orders"] is False
    assert action["resumes_trading"] is False
    assert safety.resume(actor="SUPERVISOR", deterministic_recovery_verified=True,
                         owner_authorized=True)["resumed"] is False
    assert safety.resume(actor="OWNER", deterministic_recovery_verified=True,
                         owner_authorized=True)["resumed"] is True


def test_supervisor_unavailable_is_never_read_as_approval():
    outcome = availability_outcome("provider timeout")
    assert outcome.availability == "SUPERVISOR_UNAVAILABLE"
    action = SafetyController().apply(outcome)
    assert action["action"] == "SUPERVISOR_UNAVAILABLE"
    assert action["resumes_trading"] is False


def test_supervisor_halt_blocks_new_exposure_through_preflight():
    safety = SafetyController()
    safety.apply(interpret_supervisor_output({"finding": "SUPERVISOR_HALT_REQUEST",
                                             "evidence_refs": ["p1"]}))
    report = make_evaluator(provider=DemoProvider(), safety=safety).evaluate(
        intent=intent(), portfolio=portfolio(), price=100.0)
    assert "supervisor_not_halt_requested" in report.failing
    assert report.capital_release["no_applicable_halt"] is False


# ---------------------------------------------------------------------------
# Cancellation and observability
# ---------------------------------------------------------------------------


def test_safety_controller_cannot_cancel_orders_without_approved_policy():
    with isolated_store():
        gateway, adapter = build_gateway()
        refused = gateway.request_cancel(broker_order_id="BO-1", reason="halt", actor="SAFETY_CONTROLLER")
        assert refused["cancelled"] is False
        assert refused["code"] == "CANCELLATION_NOT_APPROVED_BY_POLICY"
        assert adapter.cancelled == []
        allowed = gateway.request_cancel(broker_order_id="BO-1", reason="owner review", actor="OWNER")
        assert allowed["cancelled"] is True


def test_gateway_health_reports_automation_state_without_credentials():
    with isolated_store():
        gateway, _ = build_gateway()
        health = gateway.health()
        assert health["automation_supported"] is True
        assert health["broker_health"]["healthy"] is True
        assert health["account"] == {"account_id": "ACCT-1", "environment": "TEST_ENV"}
        blob = json.dumps(health).lower()
        assert "token" not in blob and "secret" not in blob and "password" not in blob


def test_gateway_health_fails_closed_when_the_broker_raises():
    with isolated_store():
        class BrokenAdapter(FakeAdapter):
            def account(self):
                raise ConnectionError("broker unreachable")

        gateway, _ = build_gateway(adapter=BrokenAdapter())
        health = gateway.health()
        assert health["code"] == "BROKER_UNREACHABLE"
        assert health["broker_health"]["healthy"] is False


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
    print(f"ALL PASS ({len(tests)} execution-layer tests)")
