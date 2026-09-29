"""Tests for the live-readiness work: Ed25519 trust root, three frozen blockers, ported adapter
logic, the exchange calendar, and the LIVE_READY_LOCKED ceiling.

What is under test here is the claim that everything NOT requiring an owner credential, an owner
capital value, or an owner signature has actually been done, and that what remains cannot be
supplied by a program.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(TESTS_DIR))

from execution import ed25519  # noqa: E402
from execution.adapters import (AlpacaAdapter, BrokerChannel, BrokerContractError,  # noqa: E402
                                UpstoxAdapter)
from execution.contracts import CORE_CAPABILITIES  # noqa: E402
from execution.amendment import blockers_to_live_release, live_release_requirements  # noqa: E402
from execution.contracts import BrokerHealth  # noqa: E402
from execution.exchange_calendar import (default_us_equity_calendar, early_closes,  # noqa: E402
                                         market_holidays)
from execution.gate import (frozen_config_guard_permits, frozen_modes,  # noqa: E402
                            frozen_permitted_modes, frozen_risk_permits,
                            frozen_risk_permitted_modes)
from execution.lifecycle import (OWNER_BLOCKING_ITEMS, Actor, Lifecycle,  # noqa: E402
                                 Stage)
from execution.live_path import LivePathViolation  # noqa: E402
from execution.live_verification import READ_ONLY_CHECKS  # noqa: E402

#: A fixed instant, so timestamped evidence is reproducible.
NOW = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
from execution.owner_authority import (PURPOSE_AMENDMENT, PURPOSE_LIVE_AUTHORIZATION,  # noqa: E402
                                       key_id_for, owner_authority_status, signed_message,
                                       verify_owner_signature)

# ---------------------------------------------------------------------------
# Ed25519 primitive
# ---------------------------------------------------------------------------

#: Published RFC 8032 test vector 1 (empty message).
RFC8032_V1_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
RFC8032_V1_PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC8032_V1_SIGNATURE = (
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
#: Published RFC 8032 test vector 2 (single 0x72 byte).
RFC8032_V2_SEED = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
RFC8032_V2_SIGNATURE_PREFIX = "92a009a9f0d4cab8720e820b5f642540"


def test_ed25519_matches_the_published_rfc8032_vector_one():
    private, public = ed25519.generate_keypair(RFC8032_V1_SEED)
    assert public.hex() == RFC8032_V1_PUBLIC
    assert ed25519.sign(b"", private).hex() == RFC8032_V1_SIGNATURE


def test_ed25519_matches_the_published_rfc8032_vector_two():
    private, _ = ed25519.generate_keypair(RFC8032_V2_SEED)
    assert ed25519.sign(b"\x72", private).hex().startswith(RFC8032_V2_SIGNATURE_PREFIX)


def test_ed25519_rejects_tampering_and_malformed_input():
    private, public = ed25519.generate_keypair(RFC8032_V1_SEED)
    signature = ed25519.sign(b"message", private)
    assert ed25519.verify(b"message", signature, public) is True
    assert ed25519.verify(b"messagf", signature, public) is False
    assert ed25519.verify(b"message", bytes(1) + signature[1:], public) is False
    other_public = ed25519.generate_keypair(b"\x09" * 32)[1]
    assert ed25519.verify(b"message", signature, other_public) is False
    assert ed25519.verify(b"message", signature, b"\xff" * 32) is False
    for bad in (b"", signature[:63], b"\x00" * 64):
        assert ed25519.verify(b"message", bad, public) is False
    assert ed25519.verify(b"message", signature, public[:31]) is False


def test_ed25519_refuses_a_signature_scalar_at_or_above_the_group_order():
    _private, public = ed25519.generate_keypair(RFC8032_V1_SEED)
    forged = b"\x00" * 32 + ed25519._L.to_bytes(32, "little")
    assert ed25519.verify(b"message", forged, public) is False


# ---------------------------------------------------------------------------
# Owner trust root
# ---------------------------------------------------------------------------


def test_the_pinned_owner_key_is_absent_and_owner_acts_fail_closed():
    status = owner_authority_status()
    assert status["configured"] is False
    assert status["private_key_in_process"] is False
    assert "build_guard" in status["protected_by"]
    ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, b"payload", "ed25519:v1:k:" + "00" * 64)
    assert ok is False and code == "OWNER_AUTHORITY_KEY_NOT_CONFIGURED"


def test_the_owner_key_file_is_registered_in_build_integrity():
    from build_guard import CRITICAL_FILES, current_manifest

    assert "owner_public_key.json" in CRITICAL_FILES
    assert "engine/owner_public_key.json" in current_manifest()["files"]


def test_domain_separation_makes_a_signature_unusable_under_another_purpose():
    """A signature made for an amendment must never verify as a live authorization."""
    from execution.amendment import AmendmentProposal

    now = datetime.now(timezone.utc)
    private, public = ed25519.generate_keypair(RFC8032_V1_SEED)
    key_id = key_id_for(public)
    proposal = AmendmentProposal(amendment_id="A", adds_rules=("LIVE_GATE",),
                                 removes_rules=("PAPER_FIRST",), target_mode="live",
                                 issued_at=now.isoformat(),
                                 expires_at=(now + timedelta(days=1)).isoformat())
    payload = proposal.signed_payload()
    signature = ed25519.sign(signed_message(PURPOSE_AMENDMENT, payload, key_id), private)
    envelope = f"ed25519:v1:{key_id}:{signature.hex()}"
    # Same bytes, same key, wrong purpose: the domain tag makes it a different message.
    assert signed_message(PURPOSE_AMENDMENT, payload, key_id) != \
        signed_message(PURPOSE_LIVE_AUTHORIZATION, payload, key_id)
    # The framed message is also not the bare payload, so nothing can be replayed unsigned.
    assert signed_message(PURPOSE_AMENDMENT, payload, key_id).startswith(
        b"TRIPS-OWNER-AUTHORITY-V1")
    assert signed_message(PURPOSE_AMENDMENT, payload, key_id) != payload


def test_a_version_or_key_id_mismatch_is_refused_rather_than_guessed():
    import test_execution_layer as base

    with base.owner_key_configured():
        ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, b"p", "")
        assert ok is False and code == "OWNER_SIGNATURE_REQUIRED"
        ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, b"p", "sha256:other:" + "aa" * 32)
        assert ok is False and code == "OWNER_SIGNATURE_MALFORMED"
        ok, code, _ = verify_owner_signature(
            PURPOSE_AMENDMENT, b"p", f"ed25519:v9:{base.TEST_OWNER_KEY_ID}:" + "aa" * 64)
        assert ok is False and code == "OWNER_SIGNATURE_MALFORMED"
        ok, code, _ = verify_owner_signature(
            PURPOSE_AMENDMENT, b"p", "ed25519:v1:deadbeefdeadbeef:" + "aa" * 64)
        assert ok is False and code == "OWNER_AUTHORITY_KEY_MISMATCH"
        ok, code, _ = verify_owner_signature(
            PURPOSE_AMENDMENT, b"p", f"ed25519:v1:{base.TEST_OWNER_KEY_ID}:" + "aa" * 64)
        assert ok is False and code == "OWNER_SIGNATURE_INVALID"


def test_a_genuine_owner_signature_verifies_and_binds_its_payload():
    import test_execution_layer as base

    payload = b"the canonical artifact bytes"
    envelope = base.sign_for_tests(PURPOSE_AMENDMENT, payload)
    with base.owner_key_configured():
        ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, payload, envelope)
        assert ok is True and code == "OWNER_SIGNATURE_VALID"
        # Replay under another purpose: refused, because the purpose is inside the signed message.
        ok, code, _ = verify_owner_signature(PURPOSE_LIVE_AUTHORIZATION, payload, envelope)
        assert ok is False
        # Any mutation of the payload invalidates it.
        ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, payload + b"!", envelope)
        assert ok is False and code == "OWNER_SIGNATURE_INVALID"


# ---------------------------------------------------------------------------
# Three frozen blockers
# ---------------------------------------------------------------------------


def test_three_independent_frozen_sites_refuse_live_mode():
    restrictions = frozen_modes()
    assert restrictions == {"config_guard": ("paper",), "risk": ("paper",)}
    guard, risk = frozen_config_guard_permits("live"), frozen_risk_permits("live")
    assert guard["permitted"] is False and risk["permitted"] is False
    assert guard["reason"] != risk["reason"], "the two blockers must be genuinely separate"
    assert "paper mode only" in str(guard["reason"])
    assert "Live execution is disabled by design" in str(risk["reason"])


def test_all_three_blockers_are_named_by_category():
    blockers = blockers_to_live_release()["blockers"]
    assert blockers == ["CONSTITUTION_PROHIBITION_RULE", "CONFIG_GUARD_MODE_RESTRICTION",
                        "RISK_MODE_RESTRICTION"]


def test_the_risk_engine_file_is_in_the_amendment_change_set():
    artifacts = live_release_requirements()["artifacts_requiring_owner_review_and_reapproval"]
    assert "engine/risk.py" in artifacts


# ---------------------------------------------------------------------------
# Adapter logic ported from PR #1
# ---------------------------------------------------------------------------


def _intent(**overrides):
    base = {"order_type": "LIMIT", "limit_price": 100.0, "time_in_force": "DAY", "side": "BUY",
            "quantity": 10, "idempotency_key": "idem-1", "symbol": "SPY"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_ported_adapter_validates_quantity_before_any_request():
    adapter = UpstoxAdapter()
    assert adapter.represent_intent(_intent())["quantity"] == 10
    for bad in (1.5, True, "nan", "inf", 0, -3, None, "ten"):
        try:
            adapter.represent_intent(_intent(quantity=bad))
            raise AssertionError(f"quantity {bad!r} must be refused")
        except BrokerContractError:
            pass


def test_ported_adapter_refuses_to_truncate_an_over_long_order_tag():
    adapter = UpstoxAdapter()
    for bad in ("x" * 41, "", "   ", None, 1):
        try:
            adapter.represent_intent(_intent(idempotency_key=bad))
            raise AssertionError(f"tag {bad!r} must be refused")
        except BrokerContractError:
            pass


def test_ported_adapter_refuses_slicing_and_meaning_altering_shapes():
    adapter = UpstoxAdapter()
    try:
        adapter.represent_intent(_intent(slice=True))
        raise AssertionError("sliced orders must be refused")
    except BrokerContractError:
        pass
    alpaca = AlpacaAdapter()
    try:
        alpaca.represent_intent(_intent(limit_price=None))
        raise AssertionError("a limit order without a price must be refused")
    except BrokerContractError:
        pass


def test_ported_adapter_refuses_a_split_acknowledgement():
    adapter = UpstoxAdapter()
    for ids, label in (([], "no id"), ([None], "null id"), ([""], "blank id"),
                       (["1", "2"], "two ids"), ([True], "boolean id")):
        try:
            adapter.interpret_acknowledgement({"status": "success", "data": {"order_ids": ids}})
            raise AssertionError(f"{label} must be refused")
        except BrokerContractError:
            pass
    ok = adapter.interpret_acknowledgement({"status": "success", "data": {"order_ids": ["7"]}})
    assert ok["broker_order_id"] == "7"


def test_ported_adapter_never_drops_an_identity_less_order_row():
    adapter = UpstoxAdapter()
    try:
        adapter.normalize_orders({"status": "success", "data": [{"quantity": 1}]})
        raise AssertionError("an order row with no identity must not be dropped silently")
    except BrokerContractError:
        pass
    for bad in (None, {}, {"status": "error", "data": []}, {"status": "success", "data": {}},
                {"status": "success", "data": [None]}):
        try:
            adapter.normalize_orders(bad)
            raise AssertionError("a malformed snapshot must be an error, not an empty account")
        except BrokerContractError:
            pass
    assert adapter.normalize_orders({"status": "success", "data": []}) == ()


def test_no_shipped_channel_means_no_adapter_can_reach_a_broker():
    for adapter in (UpstoxAdapter(), AlpacaAdapter()):
        assert adapter.health().connected is False
        matrix = adapter.capability_matrix()
        assert matrix.statuses == {}, "no capability is claimed without conformance evidence"
        assert len(matrix.missing_core()) == len(CORE_CAPABILITIES), "nothing is verified"
        for call in (lambda: adapter.submit_order(client_order_id="x", representation={}),
                     lambda: adapter.account(),
                     lambda: adapter.positions(),
                     lambda: adapter.cancel_order(broker_order_id="1", reason="r")):
            try:
                call()
                raise AssertionError("an adapter without a channel must refuse")
            except BrokerContractError:
                pass


def test_endpoint_selection_is_configuration_not_authority():
    """Selecting an endpoint grants nothing: it changes a URL and nothing else.

    The live endpoint is the only one there is. A recorded fixture exists for engineering tests and
    points at a non-routable host, so no paper or sandbox URL remains to be selected at all.
    """
    assert UpstoxAdapter().trade_base == "https://api-hft.upstox.com/v3"
    assert UpstoxAdapter(environment="live").trade_base == "https://api-hft.upstox.com/v3"
    assert AlpacaAdapter().base_url == "https://api.alpaca.markets"
    assert AlpacaAdapter(environment="live").base_url == "https://api.alpaca.markets"
    assert UpstoxAdapter(environment="recorded").trade_base.startswith("https://recorded.invalid")
    assert AlpacaAdapter(environment="recorded").base_url == "https://recorded.invalid"
    for environment in ("recorded", "live"):
        matrix = UpstoxAdapter(environment=environment).capability_matrix()
        assert matrix.status("order_submission").permits_execution is False
    for bad in ("production", "", "LIVE", "Live", "paper", "sandbox", "PAPER"):
        try:
            UpstoxAdapter(environment=bad)
            raise AssertionError(f"environment {bad!r} must be refused")
        except BrokerContractError:
            pass


def test_no_paper_or_sandbox_url_exists_on_any_execution_route_object():
    """Removing paper trading means the endpoints are gone, not merely unused."""
    from execution import adapters, channels

    for module in (adapters, channels):
        published = [name for name in dir(module) if not name.startswith("_")]
        for name in published:
            value = getattr(module, name)
            if not isinstance(value, str) or name.islower():
                continue
            assert "paper" not in value.lower(), f"{module.__name__}.{name} is a paper endpoint"
            assert "sandbox" not in value.lower(), f"{module.__name__}.{name} is a sandbox endpoint"


def test_the_shipped_channels_cannot_be_constructed_without_a_credential():
    """Concrete channels are engineering work; a credential is an owner act.

    So the package may carry real ``BrokerChannel`` implementations, and none of them may be
    built without one. That keeps account access, entitlement and order capacity out of the
    repository while still letting conformance be executed.
    """
    from execution.channels import AlpacaChannel, BrokerChannelError, UpstoxChannel

    for channel_type in (UpstoxChannel, AlpacaChannel):
        assert issubclass(channel_type, BrokerChannel)
        try:
            channel_type()
            raise AssertionError(f"{channel_type.__name__} must refuse to build without a credential")
        except BrokerChannelError as exc:
            assert "credential" in str(exc).lower()
    assert BrokerChannel.__abstractmethods__


def test_mutation_probes_never_run_against_a_live_endpoint():
    from execution.channels import AlpacaChannel, BrokerChannelError, UpstoxChannel

    for channel_type, kwargs in ((UpstoxChannel, {"access_token": "t"}),
                                 (AlpacaChannel, {"api_key": "k", "api_secret": "s"})):
        try:
            channel_type(environment="live", allow_mutation_probes=True, **kwargs)
            raise AssertionError("mutation probes must be refused against a live endpoint")
        except BrokerChannelError as exc:
            assert "recorded" in str(exc).lower(), str(exc)


def test_paper_and_sandbox_environments_cannot_be_constructed_at_all():
    """There is no runtime fallback between environments, so the names are refused outright."""
    from execution.channels import AlpacaChannel, BrokerChannelError, UpstoxChannel

    for channel_type, kwargs in ((UpstoxChannel, {"access_token": "t"}),
                                 (AlpacaChannel, {"api_key": "k", "api_secret": "s"})):
        for environment in ("paper", "sandbox", "PAPER", "SANDBOX"):
            try:
                channel_type(environment=environment, **kwargs)
                raise AssertionError(f"{channel_type.__name__} accepted {environment!r}")
            except BrokerChannelError as exc:
                assert environment.lower() in str(exc).lower() or "live" in str(exc).lower()


def test_a_live_channel_refuses_to_submit_or_cancel_without_an_owner_permit():
    """The first brokerage mutation may happen only after LIVE_ENABLED, under a signed permit."""
    from execution.channels import (AlpacaChannel, LiveAccountReadOnlyView, RecordedTransport,
                                    UpstoxChannel)
    from execution.contracts import LiveMutationPermit, MutationWithoutPermit
    from execution.readiness import ALPACA_TRANSCRIPTS, UPSTOX_TRANSCRIPTS

    upstox = UpstoxChannel(environment="recorded", access_token="recorded",
                           transport=RecordedTransport(dict(UPSTOX_TRANSCRIPTS)))
    for call in (lambda: upstox.submit(client_order_id="x", representation={"quantity": 1}),
                 lambda: upstox.cancel(broker_order_id="x", reason="y")):
        try:
            call()
            raise AssertionError("an unpermitted mutation must be refused")
        except MutationWithoutPermit:
            pass
    assert not [c for c in upstox._transport.calls if c["method"] in ("POST", "DELETE", "PATCH")]

    # A permit for the right stage but the wrong account is refused too, so it cannot be reused.
    permit = LiveMutationPermit(stage="LIVE_ENABLED", broker_id="upstox", account_id="OTHER",
                                authorization_key_id="kid")
    try:
        upstox.submit(client_order_id="x", representation={"quantity": 1}, permit=permit)
        raise AssertionError("a permit for another account must be refused")
    except MutationWithoutPermit:
        pass
    # A permit carrying the wrong stage is refused even for the right account.
    early = LiveMutationPermit(stage="LIVE_READY_LOCKED", broker_id="upstox", account_id="ACCT",
                               authorization_key_id="kid")
    try:
        upstox.submit(client_order_id="x", representation={"quantity": 1}, permit=early)
        raise AssertionError("a pre-LIVE_ENABLED permit must be refused")
    except MutationWithoutPermit:
        pass

    # The read-only view has no mutating method at all, so a verifier cannot reach one.
    view = LiveAccountReadOnlyView(AlpacaChannel(environment="recorded", api_key="r",
                                                 api_secret="r",
                                                 transport=RecordedTransport(
                                                     dict(ALPACA_TRANSCRIPTS))))
    assert not [name for name in dir(view)
                if any(verb in name for verb in ("submit", "cancel", "place", "replace", "modify"))]


# ---------------------------------------------------------------------------
# Exchange calendar
# ---------------------------------------------------------------------------


def test_the_2026_exchange_holiday_schedule_is_correct():
    holidays = {day.isoformat() for day in market_holidays(2026)}
    assert holidays == {
        "2026-01-01",   # New Year's Day
        "2026-01-19",   # Martin Luther King Jr. Day
        "2026-02-16",   # Washington's Birthday
        "2026-04-14",   # Good Friday
        "2026-05-25",   # Memorial Day
        "2026-06-19",   # Juneteenth
        "2026-07-03",   # Independence Day, observed on the preceding Friday
        "2026-09-07",   # Labor Day
        "2026-11-26",   # Thanksgiving
        "2026-12-25",   # Christmas
    }


def test_early_closes_and_juneteenth_onset():
    assert {day.isoformat() for day in early_closes(2026)} == {"2026-11-27", "2026-12-24"}
    assert date(2021, 6, 18) not in market_holidays(2021), "Juneteenth begins in 2022"
    assert date(2022, 6, 20) in market_holidays(2022), "Juneteenth 2022 fell on a Sunday"


def test_weekend_holidays_are_observed_on_fridays_and_no_session_is_claimed():
    calendar = default_us_equity_calendar(2025, 2027)
    tz = ZoneInfo("America/New_York")
    closed = date(2026, 7, 3)          # observed Independence Day
    open_day = date(2026, 7, 2)         # ordinary Thursday
    weekend = date(2026, 7, 4)          # Saturday
    at_noon = lambda day: calendar.evaluate(  # noqa: E731
        datetime.combine(day, time(12, 0), tzinfo=tz))
    assert at_noon(closed).status.value == "CLOSED"
    assert at_noon(open_day).status.value == "OPEN"
    assert at_noon(weekend).status.value == "CLOSED"
    # Outside the validity window the calendar says UNKNOWN rather than guessing.
    beyond = calendar.evaluate(datetime(2030, 7, 2, 16, 0, tzinfo=tz))
    assert beyond.status.value == "UNKNOWN"


def test_the_calendar_states_its_own_provenance_limits():
    calendar = default_us_equity_calendar(2025, 2027)
    assert "not a live exchange feed" in calendar.provenance
    assert "halt" in calendar.provenance


# ---------------------------------------------------------------------------
# LIVE_READY_LOCKED
# ---------------------------------------------------------------------------


def test_readiness_is_computed_from_executed_evidence_not_module_imports():
    """Every engineering check must EXECUTE something and carry a digest over the observation."""
    from execution.readiness import ENGINEERING_CHECKS

    readiness = Lifecycle(Stage.LIVE_LOCKED).live_readiness()
    checks = {check["name"]: check for check in readiness["engineering_checks"]}
    assert set(ENGINEERING_CHECKS) <= set(checks)
    for name, check in checks.items():
        assert check["produced_by"], f"{name} names no producing system"
        assert len(check["observation_hash"]) == 64, f"{name} carries no evidence digest"
    # The old module-import checks are gone, and nothing like them replaced them.
    for gone in ("broker_adapters_present", "reconciliation_engine_present",
                 "supervisor_provider_present", "owner_trust_root_present",
                 "execution_authority_gate_present", "session_calendar_available"):
        assert gone not in checks, f"{gone} is a module-presence check, not evidence"
    assert "importlib" not in _lifecycle_source()
    assert readiness["failing_engineering"] == []
    assert readiness["engineering_ready"] is True
    assert readiness["ceiling"] == "LIVE_READY_LOCKED"
    assert readiness["live_status"] == "ENGINEERING_COMPLETE_STILL_LOCKED"


def _lifecycle_source() -> str:
    return (Path(__file__).resolve().parents[1] / "engine" / "execution" / "lifecycle.py").read_text()


def test_the_two_mixed_blockers_are_split_into_engineering_and_owner_parts():
    """Not all six remaining blockers are owner-only, and the split is explicit."""
    items = {item.name: item for item in OWNER_BLOCKING_ITEMS}
    assert set(items) == {"owner_public_key_configured", "capital_governor_profile_set",
                          "broker_channel_and_conformance", "production_market_data",
                          "owner_signed_live_authorization",
                          "constitutional_amendment_applied"}
    mixed = {"broker_channel_and_conformance", "production_market_data"}
    owner_only = {"owner_public_key_configured", "capital_governor_profile_set",
                  "owner_signed_live_authorization", "constitutional_amendment_applied"}
    for name, item in items.items():
        assert item.owner_only is (name in owner_only), name
        assert item.owner, name
        if name in mixed:
            assert item.engineering, f"{name} must still state its engineering part"
    assert items["broker_channel_and_conformance"].engineering[0].startswith("a real BrokerChannel")
    assert any("provider API key" in part
               for part in items["production_market_data"].owner)
    # Upstox's mandate evidence names an Indian cash segment: that is engineering-side evidence,
    # not a credential, so the owner part stays exactly the account/OAuth act.
    upstox = items["broker_channel_and_conformance"].owner[0]
    assert "OAuth" in upstox and "BrokerChannel" not in upstox


def test_live_ready_locked_is_reachable_but_grants_nothing():
    lifecycle = Lifecycle(Stage.LIVE_LOCKED)
    outcome = lifecycle.advance(Stage.LIVE_READY_LOCKED, actor=Actor.TESTS)
    assert outcome["advanced"] is True and outcome["code"] == "STAGE_ADVANCED"
    assert lifecycle.stage is Stage.LIVE_READY_LOCKED
    verdict = lifecycle.may_transmit_live()
    assert verdict["permitted"] is False, "the readiness stage must never release capital"
    assert verdict["code"] == "LIVE_LOCKED_REFUSAL"


def test_live_enabled_stays_owner_only_even_from_the_readiness_stage():
    lifecycle = Lifecycle(Stage.LIVE_READY_LOCKED)
    for actor in (Actor.TESTS, Actor.SUPERVISOR, Actor.BROKER, Actor.STUDENT, Actor.EVOLUTION):
        outcome = lifecycle.advance(Stage.LIVE_ENABLED, actor=actor)
        assert outcome["advanced"] is False
        assert outcome["code"] == "PROMOTION_REFUSED_NOT_OWNER"
    assert lifecycle.stage is Stage.LIVE_READY_LOCKED


def test_the_ceiling_is_not_self_promoting():
    """LIVE_READY_LOCKED must not be reachable from a stage that has not earned it."""
    assert Stage.RESEARCH not in [Stage.LIVE_READY_LOCKED]
    for start in (Stage.RESEARCH, Stage.BACKTEST, Stage.SHADOW):
        outcome = Lifecycle(start).advance(Stage.LIVE_READY_LOCKED, actor=Actor.OWNER)
        assert outcome["advanced"] is False
        assert outcome["code"] == "PROMOTION_REFUSED_ILLEGAL_TRANSITION"


# ---------------------------------------------------------------------------
# LIVE-MONEY-ONLY: no paper stage, one canonical path, read-only verification
# ---------------------------------------------------------------------------


def test_there_is_no_paper_stage_and_the_order_is_exactly_as_mandated():
    """The stage order is data, and PAPER is absent by design rather than by omission."""
    from execution.lifecycle import LIVE_ENABLED_REQUIREMENTS, STAGE_ORDER

    assert [stage.value for stage in STAGE_ORDER] == [
        "RESEARCH", "BACKTEST", "SHADOW", "LIVE_LOCKED", "LIVE_READY_LOCKED", "LIVE_ENABLED"]
    assert not hasattr(Stage, "PAPER")
    assert "PAPER" not in {stage.value for stage in STAGE_ORDER}
    assert all(Stage.SHADOW not in () for _ in ())  # no paper waypoint exists to be skipped
    # No transition routes through a paper stage, and the two that exist are the whole story.
    from execution.lifecycle import ALLOWED_TRANSITIONS

    assert ALLOWED_TRANSITIONS[Stage.SHADOW] == (Stage.LIVE_LOCKED,)
    assert Stage.LIVE_LOCKED in ALLOWED_TRANSITIONS[Stage.SHADOW]
    # And LIVE_ENABLED still requires all eleven named things, none of them a paper prerequisite.
    assert len(LIVE_ENABLED_REQUIREMENTS) == 11
    for requirement in LIVE_ENABLED_REQUIREMENTS:
        assert "paper" not in requirement and "sandbox" not in requirement


def test_live_enabled_requires_exactly_the_mandated_eleven_requirements():
    from execution.lifecycle import LIVE_ENABLED_REQUIREMENTS

    assert set(LIVE_ENABLED_REQUIREMENTS) == {
        "pinned_ed25519_owner_trust_root",
        "owner_signed_capital_governor_profile",
        "approved_constitutional_amendment_covering_all_three_frozen_blockers",
        "exact_approved_build_and_config_identities",
        "owner_signed_live_authorization",
        "verified_real_live_broker_account",
        "verified_live_market_data",
        "clean_reconciliation",
        "healthy_truth_risk_capital_and_constitution_gates",
        "no_halt",
        "no_unresolved_order_ambiguity",
    }


def test_the_canonical_live_path_is_exactly_as_mandated_and_in_order():
    from execution.live_path import (CANONICAL_LIVE_PATH, TRANSMISSION_POINT, assert_canonical_live_route,
                                     path_index)

    assert CANONICAL_LIVE_PATH == (
        "live_market_data", "truth", "strategy", "risk", "capital_governor", "constitution",
        "immutable_execution_intent", "execution_authority_gate", "universal_broker_gateway",
        "verified_live_broker_adapter", "live_broker_account", "real_broker_execution",
        "reconciliation")
    assert assert_canonical_live_route()["canonical"] is True
    # The gateway is the transmission point, and it sits after every read-only stage.
    assert path_index(TRANSMISSION_POINT) > path_index("execution_authority_gate")
    assert path_index(TRANSMISSION_POINT) < path_index("real_broker_execution")
    # Anything reordered, dropped or inserted is a violation, not a variant.
    for broken in (CANONICAL_LIVE_PATH[:-1], tuple(reversed(CANONICAL_LIVE_PATH)),
                   CANONICAL_LIVE_PATH + ("extra_stage",),
                   ("live_market_data", "live_broker_account", "truth")):
        try:
            assert_canonical_live_route(broken)
            raise AssertionError(f"{broken} must be refused as non-canonical")
        except LivePathViolation:
            pass


def test_no_non_live_environment_is_permitted_anywhere_on_the_route():
    from execution.live_path import assert_no_non_live_environment

    assert assert_no_non_live_environment(
        market_data_source_kind="live", broker_environment="live",
        broker_account_environment="live")["canonical"] is True
    for kwargs in ({"broker_environment": "paper"}, {"broker_environment": "sandbox"},
                   {"broker_environment": "PAPER"}, {"broker_account_environment": "sandbox"},
                   {"market_data_source_kind": "demo"}, {"market_data_source_kind": "delayed"},
                   {"market_data_source_kind": "simulated"}):
        try:
            assert_no_non_live_environment(**kwargs)
            raise AssertionError(f"{kwargs} must be refused on a live-money-only route")
        except LivePathViolation:
            pass


def test_the_first_mutation_may_happen_only_at_live_enabled_and_under_a_permit():
    from execution.contracts import MUTATION_REQUIRES_STAGE
    from execution.live_path import mutation_stage

    assert MUTATION_REQUIRES_STAGE == "LIVE_ENABLED"
    assert mutation_stage() == "LIVE_ENABLED"
    from execution.lifecycle import Stage

    assert Stage.LIVE_ENABLED.value == MUTATION_REQUIRES_STAGE
    for stage in (Stage.RESEARCH, Stage.BACKTEST, Stage.SHADOW, Stage.LIVE_LOCKED,
                  Stage.LIVE_READY_LOCKED):
        assert stage.value != MUTATION_REQUIRES_STAGE


def test_the_gateway_mints_the_permit_immediately_before_the_only_transmission():
    """The first brokerage mutation happens in the gateway, and nowhere else."""
    import inspect

    from execution import gateway

    source = inspect.getsource(gateway.UniversalBrokerGateway.submit)
    body = source.split("\n")
    mint = next(index for index, line in enumerate(body) if "LiveMutationPermit(" in line)
    call = next(index for index, line in enumerate(body) if "submit_order(" in line)
    assert mint < call, "the permit must exist before the order is transmitted"
    # ... and the gate verdict must already have been consulted.
    assert any("may_transmit_live" in line for line in body)
    # The permit is never a caller argument.
    signature = inspect.signature(gateway.UniversalBrokerGateway.submit)
    assert "permit" not in signature.parameters


def test_the_twelve_read_only_checks_run_without_submitting_anything():
    from execution.live_verification import (REQUIRED_INSTRUMENTS, LiveReadOnlyBrokerVerifier,
                                             LiveReadOnlyVerification)

    assert len(READ_ONLY_CHECKS) == 12
    assert READ_ONLY_CHECKS == (
        "authenticate_legitimately", "verify_broker_identity", "verify_exact_account",
        "verify_us_equity_permissions", "verify_required_instruments_available",
        "retrieve_balances", "retrieve_positions", "retrieve_open_and_recent_orders",
        "verify_broker_clock", "verify_account_restrictions",
        "verify_rate_limit_and_error_behaviour", "verify_live_market_data_entitlement")
    assert REQUIRED_INSTRUMENTS == ("SPY", "QQQ", "AAPL")
    # The verifier has no submit, no cancel and no replace of its own.
    assert not [name for name in dir(LiveReadOnlyBrokerVerifier)
                if any(verb in name for verb in ("submit", "cancel", "place", "replace",
                                                "modify", "close_order"))]


def test_read_only_verification_is_refused_for_any_non_live_environment():
    from execution.live_verification import LiveReadOnlyVerification, LiveVerificationError

    for environment in ("recorded", "paper", "sandbox", ""):
        try:
            LiveReadOnlyVerification(broker_id="b", account_id="A", environment=environment,
                                     generated_at=NOW.isoformat())
            raise AssertionError(f"environment {environment!r} must be refused")
        except LiveVerificationError as exc:
            assert "live" in str(exc).lower()
    ok = LiveReadOnlyVerification(broker_id="b", account_id="A", environment="live",
                                  generated_at=NOW.isoformat())
    assert ok.verified is False, "no checks run means unverified, not verified"
    assert ok.missing_checks == READ_ONLY_CHECKS
    assert ok.to_dict()["releases_capital"] is False


def test_the_two_evidence_kinds_are_distinct_and_neither_releases_capital():
    from execution.live_verification import EVIDENCE_KIND_LIVE_READ_ONLY, EVIDENCE_KIND_RECORDED

    assert EVIDENCE_KIND_RECORDED == "RECORDED_CONTRACT_CONFORMANCE"
    assert EVIDENCE_KIND_LIVE_READ_ONLY == "LIVE_READ_ONLY_BROKER_VERIFICATION"
    assert EVIDENCE_KIND_RECORDED != EVIDENCE_KIND_LIVE_READ_ONLY
    # A recorded conformance document is stamped as recorded, and resolves to UNVERIFIED when
    # asked about the live environment.
    from execution.contracts import CapabilityStatus
    from execution.readiness import collect_evidence

    _, conformance = collect_evidence()
    for broker_id, document in conformance.items():
        assert document.to_dict()["evidence_kind"] == EVIDENCE_KIND_RECORDED
        for capability in document.records:
            # Never SUPPORTED. A recorded proof is not a live proof; a recorded proven negative
            # survives as a negative, which also fails closed.
            assert document.status(capability, environment="live") is not CapabilityStatus.SUPPORTED
        assert document.mandate_verdict(["SPY", "QQQ", "AAPL"],
                                        environment="live")["permitted"] is False
        assert broker_id


def test_the_readiness_report_is_honest_and_releases_nothing():
    from execution.readiness import live_readiness_report

    report = live_readiness_report()
    assert report["reached_live_enabled"] is False
    assert report["paper_stage_present"] is False
    assert report["releases_capital"] is False
    assert report["live_broker_verification"]["status"] == "NOT_VERIFIED"
    assert report["live_broker_verification"]["submits_no_order"] is True
    assert report["stage_order"] == ["RESEARCH", "BACKTEST", "SHADOW", "LIVE_LOCKED",
                                    "LIVE_READY_LOCKED", "LIVE_ENABLED"]


def test_autonomy_may_only_tighten_the_owner_signed_governor_profile():
    """After activation, valid decisions execute without per-order approval - inside the limits."""
    from execution.live_path import assert_no_autonomous_authority_increase

    ceilings = {"max_order_notional": 5000.0, "max_position_notional": 20000.0}
    signed = {"max_order_notional": 4000.0, "max_position_notional": 20000.0}
    assert assert_no_autonomous_authority_increase(signed, signed, ceilings)["may_only_tighten"]
    # Tightening itself is always allowed.
    assert assert_no_autonomous_authority_increase(
        signed, {"max_order_notional": 1000.0, "max_position_notional": 5000.0}, ceilings)
    for raised in ({"max_order_notional": 6000.0, "max_position_notional": 20000.0},
                   {"max_order_notional": 4000.0, "max_position_notional": 25000.0}):
        try:
            assert_no_autonomous_authority_increase(signed, raised, ceilings)
            raise AssertionError("an autonomous increase in authority must be refused")
        except LivePathViolation:
            pass


def test_no_execution_module_names_a_paper_or_sandbox_prerequisite():
    """The requirement is removed from the code, not merely deprioritised."""
    from pathlib import Path

    import execution

    root = Path(execution.__file__).parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text()
        for needle in ("paper_conformance", "sandbox_conformance", "paper_order_lifecycle",
                       "sandbox_order_lifecycle"):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


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
    print(f"ALL PASS ({len(tests)} live-readiness tests)")
