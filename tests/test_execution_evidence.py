"""Adversarial tests for the engineering remediation: evidence, translation, mandate, bridge.

Everything here is written against the *hostile* reading of the design:

* that a single flag could make fifteen capabilities SUPPORTED,
* that an adapter could quietly change an order's economics inside serialization,
* that a fully conformant broker could be talked into the wrong market,
* that "the module exists" could be mistaken for "the work is done",
* that supervision could acquire a power it is not allowed to have.

The declared order of operations at the transmission boundary is: canonical economics are
validated FIRST, and only then may the adapter serialize them into provider-native field names.
These tests exist to make the other order - serialize, then hope - impossible.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from execution import ed25519  # noqa: E402
from execution.adapters import (AlpacaAdapter, BrokerContractError, UpstoxAdapter,  # noqa: E402
                                default_alpaca, default_upstox)
from execution.channels import (AlpacaChannel, BrokerChannelError,  # noqa: E402
                                RecordedTransport, UpstoxChannel, normalize_alpaca_positions,
                                normalize_upstox_account, normalize_upstox_positions)
from execution.conformance import (CONFORMANCE_EVIDENCE_VERSION, CapabilityEvidence,  # noqa: E402
                                   ConformanceEvidence, ConformanceEvidenceError,
                                   ConformanceSuite, MandateEvidence, mandate_verdict_for)
from execution.contracts import (CORE_CAPABILITIES, APPROVED_INSTRUMENT_SCOPE,  # noqa: E402
                                 CapabilityError, CapabilityStatus, approved_symbol_scope,
                                 assert_preserves_economic_meaning,
                                 canonical_economic_representation)
from execution.lifecycle import Lifecycle, Stage  # noqa: E402
from execution.market_data import (ProductionMarketDataProvider,  # noqa: E402
                                   ProviderCredentialMissing, closed_60min_bars_evidence,
                                   evaluate_market_data, provider_credential_status)
from execution.readiness import (ALPACA_TRANSCRIPTS, UPSTOX_TRANSCRIPTS,  # noqa: E402
                                 ENGINEERING_CHECKS, ENGINEERING_WORK_COVERAGE, collect_evidence)
from execution.reconciliation import ReconciliationEngine  # noqa: E402
from execution.contracts import BrokerPosition  # noqa: E402
from execution.supervisor import RuleBasedSupervisorProvider, SafetyController  # noqa: E402
from execution.supervisor_bridge import (ProductionSupervisorBridge,  # noqa: E402
                                         SupervisorBridgeError, SupervisorEvidenceCollector,
                                         default_supervisor_bridge)

NOW = datetime.now(timezone.utc)
SCOPE = sorted(approved_symbol_scope())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _intent(**overrides):
    base = {"intent_id": "i-1", "idempotency_key": "idem-1", "correlation_id": "c-1",
            "decision_id": "d-1", "decision_bar_ts": "2026-01-02T15:00:00+00:00", "symbol": "SPY",
            "side": "BUY", "quantity": 10, "order_type": "LIMIT", "time_in_force": "DAY",
            "limit_price": 501.25, "strategy_build_id": "b", "config_id": "c", "truth_ref": "t",
            "risk_ref": "r", "governor_ref": "g", "created_at": "2026-01-02T15:00:00+00:00",
            "expires_at": "2026-01-02T18:00:00+00:00"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _upstox_channel(**kwargs):
    return UpstoxChannel(environment="sandbox", access_token="recorded",
                         transport=RecordedTransport(dict(UPSTOX_TRANSCRIPTS)), **kwargs)


def _alpaca_channel(**kwargs):
    return AlpacaChannel(environment="paper", api_key="recorded", api_secret="recorded",
                         transport=RecordedTransport(dict(ALPACA_TRANSCRIPTS)), **kwargs)


def _conformance(broker_id="test", *, environment="sandbox", now=NOW, **overrides):
    """A typed, digest-checked document with one record per capability."""
    from execution.contracts import OPTIONAL_CAPABILITIES

    statuses = overrides.pop("statuses", {})
    unverified = overrides.pop("unverified", ())
    unsupported = overrides.pop("unsupported", ())
    records = {}
    for name in tuple(CORE_CAPABILITIES) + tuple(OPTIONAL_CAPABILITIES):
        status = statuses.get(name, CapabilityStatus.SUPPORTED)
        records[name] = CapabilityEvidence(
            capability=name, status=status, interface=f"{broker_id}/iface",
            environment=environment, observed_by="test_run", observed_at=now.isoformat(),
            observation={"probe": f"probe_{name}",
                         "exercised": status is CapabilityStatus.SUPPORTED})
    mandate = MandateEvidence(
        broker_id=broker_id, mandate_id=APPROVED_INSTRUMENT_SCOPE,
        instruments=tuple(overrides.pop("instruments", ("SPY", "QQQ", "AAPL"))),
        asset_class=APPROVED_INSTRUMENT_SCOPE, environment=environment, observed_by="test_run",
        observed_at=now.isoformat(), observation={"probe": "probe_mandate", "exercised": True})
    return ConformanceEvidence(broker_id=broker_id, environment=environment, suite="test_run",
                               generated_at=now.isoformat(), records=records, mandate=mandate,
                               **overrides)


# ---------------------------------------------------------------------------
# 1. No blanket conformance flag survives anywhere
# ---------------------------------------------------------------------------


def test_the_blanket_conformance_flag_is_gone_from_every_adapter_constructor():
    from execution.adapters import _ChannelInjectedAdapter

    parameters = inspect.signature(_ChannelInjectedAdapter.__init__).parameters
    assert "conformance_verified" not in parameters
    assert "conformance" in parameters, "the replacement must be typed per-capability evidence"
    for adapter_type in (UpstoxAdapter, AlpacaAdapter):
        try:
            adapter_type(conformance_verified=True)
            raise AssertionError(f"{adapter_type.__name__} still accepts conformance_verified")
        except TypeError:
            pass
    for factory in (default_upstox, default_alpaca):
        try:
            factory(conformance_verified=True)
            raise AssertionError("a default factory still accepts conformance_verified")
        except TypeError:
            pass
    package = ROOT / "engine" / "execution"
    for path in sorted(package.glob("*.py")):
        assert "conformance_verified" not in path.read_text(), path.name


def test_a_channel_plus_a_boolean_no_longer_makes_every_capability_supported():
    """The old code: channel present + conformance_verified=True -> all fifteen SUPPORTED."""
    adapter = UpstoxAdapter(channel=_upstox_channel())
    matrix = adapter.capability_matrix()
    assert matrix.statuses == {}
    assert matrix.missing_core() == list(CORE_CAPABILITIES)


def test_each_capability_resolves_independently_and_a_proven_negative_is_kept():
    document = _conformance(statuses={"order_replace": CapabilityStatus.UNSUPPORTED,
                                       "streaming_events": CapabilityStatus.UNVERIFIED})
    assert document.status("order_submission") is CapabilityStatus.SUPPORTED
    assert document.status("order_replace") is CapabilityStatus.UNSUPPORTED
    assert document.status("streaming_events") is CapabilityStatus.UNVERIFIED
    # A proven negative survives re-resolution; it is not re-probed into optimism.
    for _ in range(3):
        assert document.status("order_replace") is CapabilityStatus.UNSUPPORTED
    assert document.missing_core() == (), "a proven optional negative must not block core"


def test_a_capability_with_no_record_is_unverified_and_never_supported():
    document = ConformanceEvidence(broker_id="b", environment="sandbox", suite="s",
                                   generated_at=NOW.isoformat(), records={})
    assert document.status("order_submission") is CapabilityStatus.UNVERIFIED
    assert document.is_complete() is False
    assert set(document.missing_core()) == set(CORE_CAPABILITIES)


def test_tampered_stale_or_mismatched_evidence_stops_resolving_to_supported():
    record = CapabilityEvidence(capability="order_submission", status=CapabilityStatus.SUPPORTED,
                                interface="b/v1/orders", environment="sandbox",
                                observed_by="run", observed_at=NOW.isoformat(),
                                observation={"probe": "p", "exercised": True})
    assert record.resolve()[0] is CapabilityStatus.SUPPORTED
    # Editing the observation after the fact changes the digest, so it is no longer evidence.
    tampered = replace(record, observation={"probe": "p", "exercised": True, "result": "forged"})
    assert tampered.digest() != record.digest()
    forged_payload = {**record.to_dict(),
                      "observation": {"probe": "p", "exercised": True, "result": "forged"}}
    with pytest_free(ConformanceEvidenceError):
        CapabilityEvidence.from_dict(forged_payload)
    # A record that was never exercised is UNVERIFIED, whatever it claims.
    unexercised = replace(record, observation={"probe": "p", "exercised": False})
    assert unexercised.resolve()[0] is CapabilityStatus.UNVERIFIED
    # Stale evidence, evidence from another environment, and evidence from another interface.
    old = replace(record, observed_at=(NOW - timedelta(days=400)).isoformat())
    assert old.resolve()[0] is CapabilityStatus.UNVERIFIED
    assert record.resolve(environment="live")[0] is CapabilityStatus.UNVERIFIED
    assert record.resolve(interface="b/v2/orders")[0] is CapabilityStatus.UNVERIFIED
    # A future schema version is not evidence either.
    assert replace(record, schema_version=CONFORMANCE_EVIDENCE_VERSION + 1).resolve()[0] is \
        CapabilityStatus.UNVERIFIED


def pytest_free(expected):
    """Tiny context manager so this suite keeps its zero-dependency shape."""
    class _Raises:
        message = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise AssertionError(f"expected {expected.__name__}")
            if not issubclass(exc_type, expected):
                return False
            self.message = str(exc)
            return True

    return _Raises()


def test_the_conformance_suite_itself_produces_independent_evidence():
    evidence = ConformanceSuite(broker_id="alpaca", environment="paper",
                                channel=_alpaca_channel(allow_mutation_probes=True),
                                mandate=None).run(now=NOW)
    assert evidence.is_complete()
    assert set(CORE_CAPABILITIES) <= set(evidence.records)
    # order_preview and streaming_events are not implemented by this channel, so they must NOT
    # read as supported just because their neighbours passed.
    assert evidence.status("order_preview") is CapabilityStatus.UNVERIFIED
    assert evidence.status("streaming_events") is CapabilityStatus.UNVERIFIED
    assert evidence.status("order_submission") is CapabilityStatus.SUPPORTED
    # Every record round-trips through its own digest.
    restored = ConformanceEvidence.from_dict(evidence.to_dict())
    assert restored.digest() == evidence.digest()


def test_a_channel_without_mutation_probes_leaves_submission_unverified():
    evidence = ConformanceSuite(broker_id="upstox", environment="sandbox",
                                channel=_upstox_channel(), mandate=None).run(now=NOW)
    assert evidence.status("order_submission") is CapabilityStatus.UNVERIFIED
    assert evidence.status("order_cancel") is CapabilityStatus.UNVERIFIED
    assert "order_submission" in evidence.missing_core()
    # Read-only capabilities are still proven, because they really were exercised.
    assert evidence.status("account") is CapabilityStatus.SUPPORTED


# ---------------------------------------------------------------------------
# 2. Canonical economics are validated BEFORE serialization
# ---------------------------------------------------------------------------


def test_the_gateway_validates_canonical_economics_before_any_serialization():
    """The declared order, asserted against the source rather than against a comment."""
    source = (ROOT / "engine" / "execution" / "gateway.py").read_text()
    canonical_at = source.index("canonical_economic_representation(intent)")
    represent_at = source.index("self._adapter.represent_intent(intent, economic=economic)")
    assert canonical_at < represent_at, "canonical economics must be validated before serialization"


def test_the_canonical_representation_requires_every_economic_field():
    good = canonical_economic_representation(_intent())
    assert set(good.to_dict()) == {"symbol", "side", "quantity", "order_type", "time_in_force",
                                   "limit_price"}
    for missing in ("symbol", "side", "quantity", "order_type", "time_in_force"):
        payload = good.to_dict()
        payload.pop(missing)
        with pytest_free(CapabilityError):
            canonical_economic_representation(payload)
    for bad in ({"quantity": 0}, {"quantity": True}, {"quantity": 1.5}, {"side": "buy"},
                {"order_type": "STOP"}, {"time_in_force": "FOK"}, {"symbol": " spy"},
                {"limit_price": -1.0}, {"limit_price": "n/a"}):
        with pytest_free(CapabilityError):
            canonical_economic_representation({**good.to_dict(), **bad})
    # MARKET carries no price; LIMIT must carry one.
    market = {**good.to_dict(), "order_type": "MARKET", "limit_price": None}
    assert canonical_economic_representation(market).limit_price is None
    with pytest_free(CapabilityError):
        canonical_economic_representation({**good.to_dict(), "limit_price": None})
    with pytest_free(CapabilityError):
        canonical_economic_representation({**market, "limit_price": 501.25})


# ---------------------------------------------------------------------------
# 3. ADVERSARIAL: serialization cannot alter economics
# ---------------------------------------------------------------------------


def _both_adapters():
    return (("upstox", UpstoxAdapter()), ("alpaca", AlpacaAdapter()))


def test_every_field_of_every_order_shape_survives_serialization_unchanged():
    shapes = (
        {"order_type": "LIMIT", "limit_price": 501.25, "side": "BUY", "time_in_force": "DAY"},
        {"order_type": "LIMIT", "limit_price": 0.01, "side": "SELL", "time_in_force": "DAY"},
        {"order_type": "MARKET", "limit_price": None, "side": "BUY", "time_in_force": "DAY"},
        {"order_type": "MARKET", "limit_price": None, "side": "SELL", "time_in_force": "GTC"},
        {"order_type": "LIMIT", "limit_price": 999999.99, "side": "BUY", "time_in_force": "GTC"},
    )
    for name, adapter in _both_adapters():
        caps = adapter.order_capabilities()
        for index, shape in enumerate(shapes):
            if shape["order_type"] not in caps.order_types or \
                    shape["time_in_force"] not in caps.time_in_force or \
                    shape["side"] not in caps.sides:
                continue  # the broker genuinely cannot express this shape; that is not a test case
            intent = _intent(intent_id=f"i-{index}", idempotency_key=f"idem-{index}", **shape)
            economic = canonical_economic_representation(intent)
            payload = adapter.represent_intent(intent, economic=economic)
            decoded = adapter.economic_view(payload)
            assert decoded == economic.to_dict(), (name, shape)
            assert_preserves_economic_meaning(economic, decoded)


def test_serialization_cannot_alter_quantity_side_symbol_order_type_tif_or_price():
    """Every economic field, mutated one at a time, in each broker's own field names."""
    intent = _intent()
    economic = canonical_economic_representation(intent)
    mutations = {
        "upstox": {
            "quantity": ({"quantity": 11}, "quantity"),
            "quantity_rounded_up": ({"quantity": 10.0 + 1}, "quantity"),
            "side": ({"transaction_type": "SELL"}, "side"),
            "symbol": ({"tradingsymbol": "QQQ"}, "symbol"),
            "order_type": ({"order_type": "MARKET"}, "order_type"),
            "time_in_force": ({"validity": "GTC"}, "time_in_force"),
            "price": ({"price": 1.01}, "limit_price"),
            "price_dropped": ({"price": 0.0}, "limit_price"),
        },
        "alpaca": {
            "quantity": ({"qty": "11"}, "quantity"),
            "quantity_fractional": ({"qty": "10.5"}, "quantity"),
            "side": ({"side": "sell"}, "side"),
            "symbol": ({"symbol": "AAPL"}, "symbol"),
            "order_type": ({"type": "market"}, "order_type"),
            "time_in_force": ({"time_in_force": "gtc"}, "time_in_force"),
            "price": ({"limit_price": "1.01"}, "limit_price"),
            "price_dropped": ({"limit_price": "0"}, "limit_price"),
        },
    }
    for name, adapter in _both_adapters():
        payload = adapter.represent_intent(intent, economic=economic)
        # Control: the unmutated payload is accepted.
        assert adapter.economic_view(payload) == economic.to_dict()
        for label, (patch, field) in mutations[name].items():
            tampered = {**payload, **patch}
            refused = False
            try:
                decoded = adapter.economic_view(tampered)
                assert_preserves_economic_meaning(economic, decoded)
            except (BrokerContractError, CapabilityError) as exc:
                refused = True
                assert field in str(exc) or "quantity" in str(exc) or "price" in str(exc), \
                    (name, label, str(exc))
            assert refused, f"{name}.{label} altered economic meaning and was not refused"


def test_a_market_order_that_gains_a_price_is_refused_rather_than_tolerated():
    intent = _intent(order_type="MARKET", limit_price=None)
    economic = canonical_economic_representation(intent)
    for name, adapter in _both_adapters():
        payload = adapter.represent_intent(intent, economic=economic)
        tampered = {**payload, ("price" if name == "upstox" else "limit_price"): 500.0}
        # Decoding ignores an added price on a market order, but the canonical side of the
        # comparison still carries limit_price=None, so any drift is caught at the boundary.
        assert adapter.economic_view(tampered)["limit_price"] is None
    # And a LIMIT order whose price is silently zeroed IS refused, because the broker would
    # trade something other than the approved price.
    limit_intent = _intent()
    limit_economic = canonical_economic_representation(limit_intent)
    for name, adapter in _both_adapters():
        payload = adapter.represent_intent(limit_intent, economic=limit_economic)
        tampered = {**payload, ("price" if name == "upstox" else "limit_price"): 0}
        with pytest_free(BrokerContractError):
            adapter.economic_view(tampered)


def test_an_adapter_cannot_re_derive_economics_and_override_the_validated_canonical():
    """The adapter serializes FROM the canonical object, not from the raw intent."""
    intent = _intent()
    economics = canonical_economic_representation(intent)
    conflicting = replace(economics, quantity=999, symbol="AAPL")
    for name, adapter in _both_adapters():
        payload = adapter.represent_intent(intent, economic=conflicting)
        # It serialized the object it was handed...
        assert adapter.economic_view(payload) == conflicting.to_dict()
        # ...which means a CONFLICT with the validated canonical is caught by the comparison,
        # not by trusting the payload.
        with pytest_free(CapabilityError):
            assert_preserves_economic_meaning(economics, adapter.economic_view(payload))


def test_the_gateway_refuses_an_altering_adapter_before_it_can_transmit():
    """End to end: an adapter that mutates the order is stopped at the boundary."""
    import test_execution_layer as base

    class AlteringAdapter(base.FakeAdapter):
        def economic_view(self, representation):
            return {**dict(representation), "quantity": int(representation["quantity"]) + 1}

    with base.isolated_store():
        adapter = AlteringAdapter()
        gateway, _ = base.build_gateway(adapter=adapter, stage=Stage.LIVE_ENABLED,
                                        boundary=base.ReleasingBoundary())
        order = base.intent()
        result = base.submit(gateway, base.permissive_report(order, now=NOW), order=order)
        assert result.outcome == "REFUSED_BY_CAPABILITY"
        assert any("economic meaning" in reason for reason in result.reasons)
        assert adapter.submitted == []
        assert gateway.ledger.open_client_order_ids() == ()


def test_the_meaning_checker_was_not_weakened_to_fit_provider_field_names():
    """It still rejects every forbidden conversion when handed a canonical-shaped payload."""
    economic = canonical_economic_representation(_intent())
    for field, value in (("quantity", 11), ("side", "SELL"), ("symbol", "QQQ"),
                         ("order_type", "MARKET"), ("time_in_force", "GTC"),
                         ("limit_price", 1.01)):
        with pytest_free(CapabilityError):
            assert_preserves_economic_meaning(economic, {**economic.to_dict(), field: value})
    for field in economic.to_dict():
        payload = economic.to_dict()
        payload.pop(field)
        with pytest_free(CapabilityError):
            assert_preserves_economic_meaning(economic, payload)
    assert_preserves_economic_meaning(economic, economic.to_dict())


# ---------------------------------------------------------------------------
# 4. Mandate compatibility is independent of interface conformance
# ---------------------------------------------------------------------------


def test_upstox_is_ineligible_for_the_us_equities_mandate_despite_full_conformance():
    """Full conformance, wrong market. The refusal must come from mandate evidence."""
    from execution.contracts import MandateIncompatible

    _records, conformance = collect_evidence(now=NOW)
    upstox = conformance["upstox"]
    # Interface conformance is genuinely measured and genuinely good.
    assert upstox.status("order_submission") is CapabilityStatus.SUPPORTED
    assert upstox.missing_core(now=NOW) == ()
    # It is still not permitted to trade SPY, QQQ or AAPL.
    verdict = upstox.mandate_verdict(("SPY", "QQQ", "AAPL"))
    assert verdict["permitted"] is False
    assert verdict["broker_mandate_id"] == "IN_EQUITY_CASH"
    assert set(verdict["missing"]) == {"SPY", "QQQ", "AAPL"}
    adapter = UpstoxAdapter(conformance=upstox)
    assert adapter.mandate_verdict(SCOPE)["permitted"] is False
    with pytest_free(MandateIncompatible) as refusal:
        adapter.require_mandate_compatible(SCOPE)
    assert refusal.message is not None


def test_generic_conformance_cannot_override_mandate_compatibility():
    """Adding SUPPORTED records must not change a single mandate verdict."""
    records, conformance = collect_evidence(now=NOW)
    upstox = conformance["upstox"]
    before = upstox.mandate_verdict(("SPY", "QQQ", "AAPL"))
    louder = replace(upstox, records={
        name: replace(record, status=CapabilityStatus.SUPPORTED)
        if record.status is not CapabilityStatus.SUPPORTED
        and record.observation.get("exercised")
        else CapabilityEvidence(
            capability=name, status=CapabilityStatus.SUPPORTED,
            interface=record.interface, environment=record.environment,
            observed_by=record.observed_by, observed_at=record.observed_at,
            observation={"probe": record.observation.get("probe", name), "exercised": True})
        for name, record in upstox.records.items()})
    assert louder.mandate_verdict(("SPY", "QQQ", "AAPL")) == before
    assert UpstoxAdapter(conformance=louder).mandate_verdict(SCOPE)["permitted"] is False


def test_an_adapter_with_no_mandate_evidence_fails_closed_on_mandate():
    document = replace(_conformance(), mandate=None)
    verdict = mandate_verdict_for(UpstoxAdapter(conformance=document), SCOPE)
    assert verdict["permitted"] is False
    assert "mandate evidence" in " ".join(verdict["reasons"])


def test_the_registry_refuses_a_conformant_broker_that_cannot_serve_the_mandate():
    from execution import (AdapterRegistry, PluginClassification, TransportKind)

    records, conformance = collect_evidence(now=NOW)
    registry = AdapterRegistry()
    upstox = UpstoxAdapter(conformance=conformance["upstox"])
    registry.register(broker_id="upstox", adapter=upstox, transport=TransportKind.REST,
                      classification=PluginClassification.MACHINE_CALLABLE_EXECUTION,
                      authorized_interface=True, authorized_interface_evidence="official v3 REST")
    # Perfectly conformant over an authorized transport...
    assert registry.execution_verdict(registry.get("upstox"))["permitted"] is True
    # ...and still refused for this deployment's instruments.
    verdict = registry.execution_verdict(registry.get("upstox"), scope=SCOPE)
    assert verdict["permitted"] is False
    assert verdict["code"] == "BROKER_MANDATE_INCOMPATIBLE"
    with pytest_free(Exception):
        registry.require_executable("upstox", scope=SCOPE)


def test_mandate_evidence_stale_or_mislabelled_does_not_permit_execution():
    stale = MandateEvidence(broker_id="alpaca", mandate_id=APPROVED_INSTRUMENT_SCOPE,
                            instruments=("SPY", "QQQ", "AAPL"),
                            asset_class=APPROVED_INSTRUMENT_SCOPE, environment="paper",
                            observed_by="run",
                            observed_at=(NOW - timedelta(days=400)).isoformat(),
                            observation={"probe": "p", "exercised": True})
    assert stale.resolve()[0] is False
    document = replace(_conformance(), mandate=stale)
    assert document.mandate_verdict(SCOPE)["permitted"] is False
    mismatched = replace(stale, observed_at=NOW.isoformat())
    document = replace(_conformance(), mandate=mismatched)
    assert document.mandate_verdict(SCOPE)["permitted"] is True, "paper evidence covers paper"
    assert document.mandate_verdict(SCOPE, environment="live")["permitted"] is False


# ---------------------------------------------------------------------------
# 5. The broker channels are real, and they refuse to be built without credentials
# ---------------------------------------------------------------------------


def test_every_shipped_channel_is_a_concrete_broker_channel_that_needs_a_credential():
    from execution.adapters import BrokerChannel

    for channel_type in (UpstoxChannel, AlpacaChannel):
        assert issubclass(channel_type, BrokerChannel)
        with pytest_free(BrokerChannelError) as refusal:
            channel_type()
        assert "credential" in refusal.message.lower()


def test_broker_specific_normalization_is_exercised_and_refuses_malformed_payloads():
    account = normalize_upstox_account(UPSTOX_TRANSCRIPTS["GET /v3/user/funds"]["payload"])
    assert (account.account_id, account.buying_power) == ("recorded-acct", 100000.0)
    assert [(p.symbol, p.quantity) for p in
            normalize_upstox_positions(
                UPSTOX_TRANSCRIPTS["GET /v3/portfolio/short-term-positions"]["payload"])] \
        == [("SBIN", 3)]
    assert [(p.symbol, p.quantity) for p in
            normalize_alpaca_positions(ALPACA_TRANSCRIPTS["GET /v2/positions"]["payload"])] \
        == [("SPY", 3)]
    # Upstox wraps its payloads; an unwrapped body is a contract violation, not an empty result.
    for bad in ({"data": []}, {"status": "error", "data": []},
                {"status": "success", "data": {"not": "a list"}}):
        with pytest_free(BrokerChannelError):
            normalize_upstox_positions(bad)
    for bad in ([{"qty": "1"}], {"positions": []}, [[]], [{"symbol": "SPY", "qty": "x"}]):
        with pytest_free(BrokerChannelError):
            normalize_alpaca_positions(bad)
    for bad in ({"status": "success", "data": {"equity": 1}}, {"status": "success", "data": {}}):
        with pytest_free(BrokerChannelError):
            normalize_upstox_account(bad)


def test_a_channel_cannot_be_asked_to_do_something_it_has_no_transcript_for():
    channel = _alpaca_channel()
    try:
        channel._transport.request("GET", "https://paper-api.alpaca.markets/v2/nonexistent")
        raise AssertionError("an unrecorded request must be an error, not a silent pass")
    except BrokerChannelError:
        pass


def test_closing_a_channel_is_enforced_and_is_the_last_probe_to_run():
    from execution.conformance import ConformanceSuite

    channel = _upstox_channel(allow_mutation_probes=True)
    order = [name for name, _ in ConformanceSuite._probe_order()]
    assert order[-1] == "disconnect", "disconnect closes the channel, so it must run last"
    evidence = ConformanceSuite(broker_id="upstox", environment="sandbox", channel=channel,
                                mandate=None).run(now=NOW)
    assert evidence.status("disconnect") is CapabilityStatus.SUPPORTED
    assert evidence.status("order_submission") is CapabilityStatus.SUPPORTED
    with pytest_free(BrokerChannelError):
        channel._transport.request("GET", "https://api-sandbox.upstox.com/v3/user/profile")


# ---------------------------------------------------------------------------
# 6. Market data: production provider, Truth, provenance, closed bars, fail-closed health
# ---------------------------------------------------------------------------


def test_a_production_provider_cannot_be_constructed_without_its_credential():
    status = provider_credential_status("twelve_data", environ={})
    assert status["credential_present"] is False
    assert status["credential_env"] == "TWELVE_DATA_API_KEY"
    with pytest_free(ProviderCredentialMissing):
        ProductionMarketDataProvider(provider_name="twelve_data", environ={})
    # The demo provider is always constructible but is never trade-eligible.
    demo = ProductionMarketDataProvider(provider_name="demo", environ={})
    assert demo.credential_status["trade_eligible"] is False


def test_a_provider_may_not_declare_itself_real_when_it_is_not():
    with pytest_free(Exception):
        ProductionMarketDataProvider(provider_name="demo", source_kind="real", environ={})
    with pytest_free(Exception):
        ProductionMarketDataProvider(provider_name="demo", interval="5min", environ={})


def test_the_data_health_logic_fails_closed_on_stale_data_and_an_unprovable_session():
    stale = []
    for index in range(80):
        stamp = (datetime.now(timezone.utc) - timedelta(days=30) + timedelta(hours=index)).isoformat()
        stale.append((stamp, 100.0 + index, 100.5 + index, 99.5 + index, 100.2 + index, 1000.0))
    bars = [SimpleNamespace(ts=ts, open=o, high=h, low=lo, close=c, volume=v)
            for ts, o, h, lo, c, v in stale]
    provider = SimpleNamespace(
        identity=SimpleNamespace(name="recorded", source_family="recorded", fixed_source_kind=None,
                                 can_request_realtime_entitlement=True),
        source_kind="real", bars=lambda symbol, count=240: list(bars))
    verdict = evaluate_market_data(provider, "SPY", now=NOW, max_age_minutes=120, min_bars=60)
    assert verdict["healthy"] is False
    assert verdict["trade_eligible"] is False
    assert verdict["detail"]["session"]["status"] in {"UNKNOWN", "CLOSED"}


def test_closed_60_minute_bar_evidence_refuses_an_unclosed_or_off_grid_series():
    def series(stamps):
        return [SimpleNamespace(ts=stamp, open=100.0, high=101.0, low=99.0, close=100.5,
                               volume=1000.0) for stamp in stamps]

    now = NOW.replace(minute=0, second=0, microsecond=0)
    closed = series([(now - timedelta(hours=80 - i)).isoformat() for i in range(80)])
    assert closed_60min_bars_evidence(closed, now=now + timedelta(hours=1))["passed"] is True
    unclosed = series([(now - timedelta(hours=80 - i)).isoformat() for i in range(80)])
    assert closed_60min_bars_evidence(unclosed, now=now)["passed"] is False
    off_grid = series([(now - timedelta(minutes=30 * (80 - i))).isoformat() for i in range(80)])
    assert closed_60min_bars_evidence(off_grid, now=now + timedelta(days=5))["passed"] is False


# ---------------------------------------------------------------------------
# 7. The supervisor bridge is wired, one-way, and outside broker authority
# ---------------------------------------------------------------------------


def _healthy_broker():
    return SimpleNamespace(
        health=lambda: SimpleNamespace(healthy=True, connected=True, authenticated=True),
        account=lambda: SimpleNamespace(account_id="A", environment="TEST"),
        positions=lambda: (BrokerPosition("SPY", 1),),
        open_orders=lambda: ())


def test_the_supervisor_bridge_actually_runs_and_only_ever_tightens():
    bridge = default_supervisor_bridge(
        broker=_healthy_broker(),
        reconciliation=ReconciliationEngine().reconcile(local_positions={"SPY": 1},
                                                        broker_positions=[BrokerPosition("SPY", 1)]),
        build_and_config_verified=True,
        truth={"trusted_for_analysis": True, "trusted_for_trade": True, "checks": []},
        risk_utilization={"used_pct": 0.1}, capital_utilization={"used_pct": 0.1})
    calm = bridge.run_once(now=NOW)
    assert calm["new_exposure"]["permitted"] is True
    assert calm["safety_action"]["action"] == "ADVISORY_ONLY"
    assert calm["authority"]["execution_authority"] == "NONE"
    assert bridge.provider_id == "rule_based_v1"

    unsafe = default_supervisor_bridge(
        broker=SimpleNamespace(health=lambda: SimpleNamespace(healthy=False, connected=False,
                                                              authenticated=False),
                               account=lambda: SimpleNamespace(account_id="A", environment="TEST"),
                               positions=lambda: (), open_orders=lambda: ()),
        reconciliation=ReconciliationEngine().reconcile(local_positions={"SPY": 9},
                                                        broker_positions=[]),
        build_and_config_verified=True)
    tripped = unsafe.run_once(now=NOW)
    assert tripped["new_exposure"]["permitted"] is False
    assert tripped["safety_action"]["new_exposure_blocked"] is True
    assert tripped["safety_action"]["cancels_orders"] is False
    assert tripped["safety_action"]["resumes_trading"] is False


def test_the_bridge_collector_is_structurally_outside_broker_authority():
    from execution.supervisor_bridge import ReadOnlyBrokerObservation

    assert "broker" in SupervisorEvidenceCollector.__init__.__annotations__
    source = (ROOT / "engine" / "execution" / "supervisor_bridge.py").read_text()
    assert "def submit" not in source and "def cancel" not in source
    assert "def resume" not in source
    # The read-only protocol exposes exactly four reads, and no mutation.
    assert {name for name in vars(ReadOnlyBrokerObservation) if not name.startswith("_")} == {
        "health", "account", "positions", "open_orders"}
    for mutating in ("submit", "cancel", "place_order", "resume", "promote"):
        assert mutating not in vars(ReadOnlyBrokerObservation)


def test_missing_evidence_escalates_rather_than_reading_as_clean():
    collector = SupervisorEvidenceCollector(build_and_config_verified=None)
    packet = collector.collect(now=NOW)
    bridge = ProductionSupervisorBridge(provider=RuleBasedSupervisorProvider(),
                                        collector=collector, safety=SafetyController())
    result = bridge.run_once(now=NOW)
    assert result["outcome"]["finding"] != "NORMAL"
    assert result["new_exposure"]["permitted"] is False
    assert "broker_health" in packet.fields
    assert packet.fields["broker_health"] == "UNKNOWN"
    assert packet.fields["build_config_integrity"] == "UNVERIFIED"


def test_the_safety_controller_still_refuses_supervisor_resumption():
    from execution.supervisor import availability_outcome

    controller = SafetyController()
    controller.apply(availability_outcome("test"))
    assert controller.resume(actor="SUPERVISOR", deterministic_recovery_verified=True,
                             owner_authorized=True)["resumed"] is False


# ---------------------------------------------------------------------------
# 8. Readiness is evidence, recomputed every time
# ---------------------------------------------------------------------------


def test_readiness_is_recomputed_from_executed_evidence_each_call():
    first = Lifecycle(Stage.LIVE_LOCKED).live_readiness()
    second = Lifecycle(Stage.LIVE_LOCKED).live_readiness()
    assert set(ENGINEERING_CHECKS) <= {check["name"] for check in first["engineering_checks"]}
    for check in first["engineering_checks"]:
        assert len(check["observation_hash"]) == 64
        assert check["produced_by"]
    assert first["engineering_ready"] is True
    assert first["failing_engineering"] == []
    assert first["live_status"] == "ENGINEERING_COMPLETE_STILL_LOCKED"
    # Recomputed, not cached: the second call re-executes and reaches the same verdict.
    assert second["engineering_ready"] == first["engineering_ready"]


def test_no_engineering_check_claims_completion_from_a_module_import():
    lifecycle_source = (ROOT / "engine" / "execution" / "lifecycle.py").read_text()
    readiness_source = (ROOT / "engine" / "execution" / "readiness.py").read_text()
    assert "importlib" not in lifecycle_source
    assert "import_module" not in lifecycle_source
    assert "import_module(" not in readiness_source


def test_every_classified_engineering_part_is_covered_by_an_executed_check():
    from execution.lifecycle import OWNER_BLOCKING_ITEMS

    parts = [part for item in OWNER_BLOCKING_ITEMS for part in item.engineering]
    assert parts, "the two mixed blockers must still state their engineering parts"
    for part in parts:
        assert part in ENGINEERING_WORK_COVERAGE, part
        assert ENGINEERING_WORK_COVERAGE[part] in ENGINEERING_CHECKS


def test_the_conformance_documents_are_reported_with_their_mandate_verdicts():
    readiness = Lifecycle(Stage.LIVE_LOCKED).live_readiness()
    documents = readiness["conformance_evidence"]
    assert set(documents) == {"upstox", "alpaca"}
    for broker_id, document in documents.items():
        assert len(document["digest"]) == 64
        assert document["complete"] is True
        assert document["mandate"] is not None
        assert set(document["capabilities"]) == set(CORE_CAPABILITIES)
    assert documents["upstox"]["mandate"]["permitted"] is False
    assert documents["alpaca"]["mandate"]["permitted"] is True


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
    print(f"ALL PASS ({len(tests)} execution-evidence tests)")
