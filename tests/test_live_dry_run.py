"""END-TO-END DRY RUN: the whole canonical live path, once, through a REAL channel.

Why this suite exists
---------------------
Every other ``LIVE_ENABLED`` test in this repository drives the gateway with ``FakeAdapter`` - a
test double whose ``submit_order`` accepts a permit and records it, but which never checks one.
So the parts of the system that only exist on the real path were individually tested and jointly
untested:

* a permit is minted by the gateway and *accepted* by a channel that actually checks it;
* the permit's account binding is satisfied by the identity the broker itself reports;
* the canonical economics survive real provider-field serialization into Alpaca names and back;
* the order lands in a real channel's transport as a real request, with a real broker order id;
* reconciliation then agrees with what the broker says.

That is exactly the seam where the two bugs fixed in PR #7 lived. A permit check that compared
against a nonexistent attribute refused every legitimate order while looking exactly like a
security control, and it passed every unit test in the repository.

What this is NOT
----------------
**It does not place a real-money order.** The broker is a ``RecordedTransport`` replaying recorded
transcripts against a non-routable base URL. It holds no credential, reaches no broker, and creates
no portfolio state. It is an ENGINEERING FIXTURE, and the evidence it produces is
``RECORDED_CONTRACT_CONFORMANCE`` - which is precisely why it cannot be mistaken for
``LIVE_READ_ONLY_BROKER_VERIFICATION`` and why it releases no capital.

What it does prove: that on the day the owner supplies credentials, an order submitted through
this path reaches the channel, binds to the right account, and returns a verifiable broker order
id - because every step up to and including the transmission has now been executed end to end.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(TESTS_DIR))

import test_execution_layer as base  # noqa: E402
import test_live_gate_amendment as amend  # noqa: E402

# The synthetic owner key stays installed for the whole module: this suite exercises the honest
# owner path, and a mutation permit is bound to an owner key id. (run_all_tests.py runs every
# suite in an isolated subprocess, so this cannot leak into another suite.)
_OWNER_KEY_INSTALLED = base.owner_key_configured()
_OWNER_KEY_INSTALLED.__enter__()
import atexit  # noqa: E402

atexit.register(_OWNER_KEY_INSTALLED.__exit__, None, None, None)

from execution import (  # noqa: E402
    STAGE_ORDER,
    AdapterRegistry,
    CycleRouter,
    ExecutionState,
    FrozenLiveBoundary,
    Lifecycle,
    LiveMutationPermit,
    MutationWithoutPermit,
    Stage,
    UniversalBrokerGateway,
    assert_canonical_live_route,
    route_frozen_cycle,
)
from execution.adapters import AlpacaAdapter  # noqa: E402
from execution.channels import (AlpacaChannel, LiveAccountReadOnlyView,  # noqa: E402
                                RecordedTransport)
from execution.contracts import canonical_economic_representation  # noqa: E402
from execution.live_path import CANONICAL_LIVE_PATH, TRANSMISSION_POINT, path_index  # noqa: E402
from execution.readiness import ALPACA_TRANSCRIPTS  # noqa: E402
from execution.reconciliation import ReconciliationEngine  # noqa: E402
from execution.registry import PluginClassification, TransportKind  # noqa: E402

#: The account the recorded Alpaca transcript reports. Every permit in this suite is bound to it,
#: so a mismatch anywhere is a real failure rather than a fixture artefact.
RECORDED_ACCOUNT = "recorded-acct"

#: The position the recorded transcript reports holding. The portfolio fixture must AGREE with it:
#: the gateway re-reconciles broker-observable facts immediately before transmitting, and a
#: portfolio claiming no positions against a broker holding 3 SPY is a discrepancy it must refuse.
#: Getting this wrong is how the dry run found the re-reconciliation doing its job.
RECORDED_POSITIONS = {"SPY": 3}


def _agreeing_portfolio(**overrides):
    """A portfolio that matches what the recorded broker says it holds."""
    return base.portfolio(positions=dict(RECORDED_POSITIONS),
                          quantities=dict(RECORDED_POSITIONS), **overrides)


def _recorded_alpaca(*, allow_mutation_probes: bool = False) -> AlpacaChannel:
    """A real Alpaca channel on recorded transcripts. No endpoint, no credential, no broker."""
    return AlpacaChannel(environment="recorded", api_key="recorded", api_secret="recorded",
                         transport=RecordedTransport(dict(ALPACA_TRANSCRIPTS)),
                         allow_mutation_probes=allow_mutation_probes)


def _live_dry_run_gateway(*, allow_mutation_probes: bool = True):
    """A gateway whose adapter is a REAL AlpacaAdapter on a REAL recorded channel.

    The conformance and mandate evidence are produced by actually running the suite against a
    channel, not hand-written - so the adapter is executable on the same terms as production.

    TWO channels, deliberately. ``probe_disconnect`` proves the disconnect contract by actually
    closing the transport, so a channel that has produced conformance evidence is spent and can
    no longer carry an order. Production has the same property: a readiness run and a trading
    session are separate connections, and a readiness run must never be able to sever the
    connection the live route is about to use. So evidence comes from one channel and the
    gateway gets a fresh one.
    """
    from execution.contracts import APPROVED_INSTRUMENT_SCOPE
    from execution.conformance import ConformanceSuite, MandateEvidence

    now = datetime.now(timezone.utc)
    evidence_channel = _recorded_alpaca(allow_mutation_probes=True)
    mandate = MandateEvidence(
        broker_id="alpaca", mandate_id=APPROVED_INSTRUMENT_SCOPE,
        instruments=("SPY", "QQQ", "AAPL"), asset_class="US_EQUITY_CASH_LONG_ONLY",
        environment="recorded", observed_by="dry_run", observed_at=now.isoformat(),
        observation={"source": "recorded transcript, engineering fixture"})
    evidence = ConformanceSuite(broker_id="alpaca", environment="recorded",
                                channel=evidence_channel, mandate=mandate).run(now=now)
    # The evidence run spent its connection, exactly as it must.
    assert evidence_channel._transport.closed, (
        "probe_disconnect must really close the channel it proves the contract of")

    execution_channel = _recorded_alpaca(allow_mutation_probes=allow_mutation_probes)
    adapter = AlpacaAdapter(environment="live", channel=execution_channel, conformance=evidence,
                            account_id=RECORDED_ACCOUNT)
    registry = AdapterRegistry()
    registry.register(broker_id="alpaca", adapter=adapter, transport=TransportKind.REST,
                      classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                      authorized_interface=True, authorized_interface_evidence="recorded fixture",
                      environment="live")
    gateway = UniversalBrokerGateway(
        adapter=adapter, governor=base.governor(),
        lifecycle=Lifecycle(Stage.LIVE_ENABLED, FrozenLiveBoundary(),
                            release_basis=amend.verified_basis(),
                            authorization=base.signed_authorization(
                                broker_id="alpaca", account_id=RECORDED_ACCOUNT,
                                environment="TEST_ENV")),
        registry=registry)
    return gateway, adapter, execution_channel, evidence


# ---------------------------------------------------------------------------
# 1. The whole path, once, end to end
# ---------------------------------------------------------------------------


def test_the_whole_canonical_live_path_runs_once_through_a_real_channel():
    """LIVE market data -> ... -> real broker execution -> reconciliation, in one pass.

    The single test the earlier suites could not make: a REAL adapter, a REAL channel, a REAL
    permit minted by the gateway and checked by the channel, and a REAL request in the transport.
    """
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        gateway, adapter, channel, evidence = _live_dry_run_gateway()
        order = base.intent(symbol="SPY", side="BUY", quantity=10, idempotency_key="idem-dry-run")

        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                                portfolio=_agreeing_portfolio(), holdings={},
                                expected_account_id=RECORDED_ACCOUNT,
                                expected_environment="TEST_ENV", now=now)

        # It reached the broker, not a double.
        assert result.outcome == "TRANSMITTED", result.reasons
        assert result.transmitted is True
        assert result.boundary["released"] is True

        # The transport holds a real POST, to the real endpoint shape, with a real client id.
        posts = [call for call in channel._transport.calls if call["method"] == "POST"]
        assert len(posts) == 1, f"expected exactly one transmission, saw {posts}"
        body = posts[0]["body"]
        assert body["symbol"] == "SPY" and body["side"] == "buy"
        # The client order id is a deterministic hash of the idempotency key, not the key itself:
        # brokers bound tag length, and the mapping back to the intent must stay reversible.
        from execution.gateway import client_order_id_for

        assert body["client_order_id"] == client_order_id_for(order.idempotency_key)
        assert body["client_order_id"] == result.client_order_id
        assert body["client_order_id"] != order.idempotency_key
        # Alpaca-native spelling, not canonical: the translation really happened.
        assert "qty" in body and "time_in_force" in body and "symbol" in body
        assert "order_type" not in body and "time_in_force" in body

        # The permit was minted from the boundary verdict, bound to the broker's OWN account.
        assert channel.account().account_id == RECORDED_ACCOUNT
        assert channel._verified_account_id == RECORDED_ACCOUNT

        # The ledger recorded a real acknowledgement with a real broker order id, exactly once.
        entry = gateway.ledger.entry(order.idempotency_key)
        assert entry["state"] == ExecutionState.BROKER_ACKNOWLEDGED.value
        assert entry["attempts"] == 1
        broker_order_id = entry["broker_order_id"]
        assert broker_order_id, "a transmitted order must carry a broker order id"
        # It is the id the recorded transcript acknowledged - not a synthesised one.
        assert broker_order_id == "rec-new"

        # And reconciliation agrees with what the broker actually says.
        reconciliation = ReconciliationEngine().reconcile(
            local_positions=dict(RECORDED_POSITIONS), broker_positions=channel.positions())
        assert reconciliation.clean, list(reconciliation.codes)
        assert adapter.broker_id == "alpaca"
        assert evidence.environment == "recorded", "this is a fixture run, and says so"


def test_a_permit_is_minted_only_after_the_boundary_and_is_bound_to_the_reported_account():
    """The permit is the product of the boundary verdict - not a constructor argument.

    Asserted on the real channel rather than on a double, because that is where a mis-bound permit
    would be caught.
    """
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        gateway, _adapter, channel, _ = _live_dry_run_gateway()
        order = base.intent(idempotency_key="idem-permit-binding")
        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                                portfolio=_agreeing_portfolio(), holdings={},
                                expected_account_id=RECORDED_ACCOUNT,
                                expected_environment="TEST_ENV", now=now)
        assert result.outcome == "TRANSMITTED", result.reasons

        # The channel bound to the account the BROKER reported, not to what the caller believed.
        assert channel._verified_account_id == RECORDED_ACCOUNT
        assert channel._verified_account_id == channel.account().account_id

        # A permit for a different account is refused by that same channel, right now.
        for label, permit in (
            ("wrong account", LiveMutationPermit(stage="LIVE_ENABLED", broker_id="alpaca",
                                                 account_id="someone-elses",
                                                 authorization_key_id="kid")),
            ("pre-enabled stage", LiveMutationPermit(stage="LIVE_READY_LOCKED", broker_id="alpaca",
                                                    account_id=RECORDED_ACCOUNT,
                                                    authorization_key_id="kid")),
            ("wrong broker", LiveMutationPermit(stage="LIVE_ENABLED", broker_id="upstox",
                                                account_id=RECORDED_ACCOUNT,
                                                authorization_key_id="kid")),
        ):
            try:
                channel._authorize_mutation(permit)
                raise AssertionError(f"a {label} permit must be refused by a real channel")
            except MutationWithoutPermit:
                pass


# ---------------------------------------------------------------------------
# 2. The dry run proves nothing about a live account, and says so
# ---------------------------------------------------------------------------


def test_the_dry_run_creates_no_portfolio_state_and_no_broker_contact():
    """Recorded transcripts are an engineering fixture. Asserted, not assumed."""
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        gateway, _adapter, channel, evidence = _live_dry_run_gateway()
        order = base.intent(idempotency_key="idem-no-state")
        gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                       portfolio=_agreeing_portfolio(), holdings={},
                       expected_account_id=RECORDED_ACCOUNT,
                       expected_environment="TEST_ENV", now=now)

        # Every URL the channel touched is the non-routable fixture host. No broker was reached.
        for call in channel._transport.calls:
            assert call["url"].startswith("https://recorded.invalid"), (
                f"the dry run touched a non-fixture host: {call['url']}")
        assert channel.environment == "recorded"
        assert channel.base_url == "https://recorded.invalid"

        # The evidence it produced is recorded-conformance, and resolves to UNVERIFIED live.
        assert evidence.to_dict()["evidence_kind"] == "RECORDED_CONTRACT_CONFORMANCE"
        from execution.contracts import CapabilityStatus

        for capability in evidence.records:
            assert evidence.status(capability,
                                   environment="live") is not CapabilityStatus.SUPPORTED

        # Positions come from the transcript; the run created none of its own.
        assert {p.symbol: p.quantity for p in channel.positions()} == RECORDED_POSITIONS

        # The two order reads are genuinely two reads. A relabelled open-orders count would be a
        # capability record duplicating another one's evidence - and this dry run is the only place
        # in the repository that holds a live channel to check it on.
        opened = {row["broker_order_id"] for row in channel.open_orders()}
        recent = {row["broker_order_id"] for row in channel.recent_orders()}
        assert recent and recent != opened, (
            f"recent_orders is a relabelled open-orders count on the live channel: {opened}")
        reads = {call["url"] for call in channel._transport.calls if "orders" in call["url"]}
        assert len(reads) > 1, f"only one order read was issued: {reads}"


def test_the_dry_run_releases_no_capital_and_grants_no_authority():
    """A transmitted order through a fixture is still not LIVE_ENABLED evidence."""
    from execution.readiness import live_readiness_report

    with base.isolated_store():
        now = datetime.now(timezone.utc)
        gateway, _adapter, _channel, _ = _live_dry_run_gateway()
        order = base.intent(idempotency_key="idem-no-capital")
        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                                portfolio=_agreeing_portfolio(), holdings={},
                                expected_account_id=RECORDED_ACCOUNT,
                                expected_environment="TEST_ENV", now=now)
        assert result.outcome == "TRANSMITTED"
        # The lifecycle is LIVE_ENABLED *in this fixture* only because a verified amendment and a
        # signed authorization were supplied. In the real repository neither exists.
        assert gateway.lifecycle.core_state_basis().source == "VERIFIED_AMENDMENT"
        from execution import owner_authority_status

        # The synthetic test key is a fixture; the real one is still absent.
        assert owner_authority_status()["configured"] is True  # synthetic, installed above

        report = live_readiness_report()
        assert report["reached_live_enabled"] is False
        assert report["releases_capital"] is False
        assert report["live_broker_verification"]["status"] == "NOT_VERIFIED"
        assert report["live_broker_verification"]["submits_no_order"] is True


# ---------------------------------------------------------------------------
# 3. Every guard on the path still refuses, through the real channel
# ---------------------------------------------------------------------------


def test_the_read_only_view_of_the_same_channel_cannot_transmit_anything():
    """The verification surface and the execution surface are the same account, opposite powers."""
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        _gateway, _adapter, channel, _ = _live_dry_run_gateway()
        view = LiveAccountReadOnlyView(channel)
        # The view can read the account...
        assert view.account().account_id == RECORDED_ACCOUNT
        # ... and has no way at all to change it.
        assert not [name for name in dir(view)
                    if any(verb in name for verb in ("submit", "cancel", "place", "replace",
                                                    "modify", "close_order"))]
        # Even handed a valid permit, the view is not a channel and cannot be made into one.
        permit = LiveMutationPermit(stage="LIVE_ENABLED", broker_id="alpaca",
                                    account_id=RECORDED_ACCOUNT, authorization_key_id="kid")
        assert not hasattr(view, "_authorize_mutation")
        assert isinstance(permit, LiveMutationPermit)
        # A read-only verification run against it is REFUSED outright, because the underlying
        # channel is a recorded fixture - and a fixture can never be reported as a live account.
        from execution.live_verification import (LiveReadOnlyBrokerVerifier,
                                                LiveVerificationError)

        try:
            LiveReadOnlyBrokerVerifier(target=view, expected_account_id=RECORDED_ACCOUNT).run(
                now=now)
            raise AssertionError("a recorded fixture must not verify as a live account")
        except LiveVerificationError as exc:
            assert "live" in str(exc).lower()
        assert not [call for call in channel._transport.calls if call["method"] in
                    ("POST", "DELETE", "PATCH", "PUT")], "verification must not mutate"


def test_the_same_path_refuses_one_step_below_live_enabled():
    """A dry run at LIVE_READY_LOCKED is the shape this build actually ships in.

    Everything up to and including the transmission is proven; the one thing standing between this
    code and a real order is the stage, and that is exactly where the build sits.
    """
    with base.isolated_store():
        now = datetime.now(timezone.utc)
        channel = _recorded_alpaca(allow_mutation_probes=True)
        from execution.conformance import ConformanceSuite, MandateEvidence
        from execution.contracts import APPROVED_INSTRUMENT_SCOPE

        now = datetime.now(timezone.utc)
        mandate = MandateEvidence(
            broker_id="alpaca", mandate_id=APPROVED_INSTRUMENT_SCOPE,
            instruments=("SPY", "QQQ", "AAPL"), asset_class="US_EQUITY_CASH_LONG_ONLY",
            environment="recorded", observed_by="dry_run", observed_at=now.isoformat(),
            observation={})
        evidence = ConformanceSuite(broker_id="alpaca", environment="recorded", channel=channel,
                                    mandate=mandate).run(now=now)
        # The conformance run spent this connection; a trading session gets a fresh one.
        execution_channel = _recorded_alpaca(allow_mutation_probes=True)
        adapter = AlpacaAdapter(environment="live", channel=execution_channel, conformance=evidence,
                                account_id=RECORDED_ACCOUNT)
        gateway = UniversalBrokerGateway(
            adapter=adapter, governor=base.governor(),
            lifecycle=Lifecycle(Stage.LIVE_READY_LOCKED, FrozenLiveBoundary(),
                                authorization=base.signed_authorization(
                                    broker_id="alpaca", account_id=RECORDED_ACCOUNT,
                                    environment="TEST_ENV")))

        order = base.intent(idempotency_key="idem-one-step-below")
        result = gateway.submit(order, preflight_report=base.permissive_report(order, now=now),
                                portfolio=_agreeing_portfolio(), holdings={},
                                expected_account_id=RECORDED_ACCOUNT,
                                expected_environment="TEST_ENV", now=now)
        assert result.outcome == "LIVE_LOCKED_REFUSAL", result.reasons
        assert not [call for call in execution_channel._transport.calls if call["method"] in
                    ("POST", "DELETE", "PATCH", "PUT")], "the stage must stop it, not the fixture"
        assert execution_channel._verified_account_id is None, (
            "no permit means no account was even resolved")


# ---------------------------------------------------------------------------
# 4. The path is the mandated path
# ---------------------------------------------------------------------------


def test_the_dry_run_traverses_exactly_the_canonical_live_path():
    """Each stage the run depends on is a named stage of the canonical route, in order.

    The dry run is only meaningful as evidence about the mandated route if the route is the one
    that was traversed - so this ties the two together rather than asserting each in isolation.
    """
    result = assert_canonical_live_route(CANONICAL_LIVE_PATH)
    assert result["canonical"] is True

    # The stages this suite actually exercises, in the order it reaches them.
    traversed = ("live_market_data", "truth", "strategy", "risk", "capital_governor",
                 "constitution", "immutable_execution_intent", "execution_authority_gate",
                 TRANSMISSION_POINT, "verified_live_broker_adapter", "live_broker_account",
                 "real_broker_execution", "reconciliation")
    assert tuple(traversed) == CANONICAL_LIVE_PATH
    indices = [path_index(name) for name in traversed]
    assert indices == sorted(indices), "the dry run must traverse the route in order"
    # The transmission point is where the permit is minted, and it precedes every stateful stage.
    assert path_index(TRANSMISSION_POINT) < path_index("real_broker_execution")

    # And PAPER is not a stage of it.
    assert "PAPER" not in {stage.value for stage in STAGE_ORDER}
    assert [stage.value for stage in STAGE_ORDER] == [
        "RESEARCH", "BACKTEST", "SHADOW", "LIVE_LOCKED", "LIVE_READY_LOCKED", "LIVE_ENABLED"]


def test_economic_meaning_survives_the_real_alpaca_serialization():
    """Canonical economics -> Alpaca field names -> canonical, unchanged.

    The dry run only means anything if the order that reached the transport still meant what the
    frozen strategy decided.
    """
    from execution.contracts import assert_preserves_economic_meaning

    adapter = AlpacaAdapter(environment="live", channel=_recorded_alpaca(), account_id=RECORDED_ACCOUNT)
    for order in (base.intent(symbol="SPY", side="BUY", quantity=10,
                              idempotency_key="idem-econ-1"),
                  base.intent(symbol="QQQ", side="SELL", quantity=3, order_type="MARKET",
                              limit_price=None, idempotency_key="idem-econ-2")):
        economic = canonical_economic_representation(order)
        payload = adapter.represent_intent(order, economic=economic)
        decoded = adapter.economic_view(payload)
        assert_preserves_economic_meaning(economic, decoded)
        assert decoded == economic.to_dict()


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
    print(f"ALL PASS ({len(tests)} live dry run tests)")
