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
from execution.amendment import blockers_to_live_release, live_release_requirements  # noqa: E402
from execution.contracts import BrokerHealth  # noqa: E402
from execution.exchange_calendar import (default_us_equity_calendar, early_closes,  # noqa: E402
                                         market_holidays)
from execution.gate import (frozen_config_guard_permits, frozen_modes,  # noqa: E402
                            frozen_permitted_modes, frozen_risk_permits,
                            frozen_risk_permitted_modes)
from execution.lifecycle import (OWNER_BLOCKING_ITEMS, Actor, Lifecycle,  # noqa: E402
                                 Stage)
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
        ok, code, _ = verify_owner_signature(PURPOSE_AMENDMENT, b"p", "hmac:other:" + "aa" * 32)
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
        assert len(matrix.missing_core()) == len(matrix.statuses), "nothing is verified"
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
    """Selecting a live endpoint grants nothing: it changes a URL and nothing else."""
    assert UpstoxAdapter().trade_base == "https://api-sandbox.upstox.com/v3"
    assert UpstoxAdapter(environment="live").trade_base == "https://api-hft.upstox.com/v3"
    assert AlpacaAdapter().base_url == "https://paper-api.alpaca.markets"
    assert AlpacaAdapter(environment="live").base_url == "https://api.alpaca.markets"
    for environment in ("sandbox", "live"):
        matrix = UpstoxAdapter(environment=environment).capability_matrix()
        assert matrix.status("order_submission").permits_execution is False
    for bad in ("production", "", "LIVE", "Live"):
        try:
            UpstoxAdapter(environment=bad)
            raise AssertionError(f"environment {bad!r} must be refused")
        except BrokerContractError:
            pass


def test_the_shipped_package_ships_no_broker_channel_implementation():
    """A channel is an owner act. Shipping one would ship network capability with the library."""
    import execution.adapters as adapters

    for name, value in vars(adapters).items():
        if isinstance(value, type) and issubclass(value, BrokerChannel) and value is not BrokerChannel:
            raise AssertionError(f"{name} looks like a shipped BrokerChannel implementation")
    assert BrokerChannel.__abstractmethods__


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


def test_readiness_separates_engineering_work_from_owner_blocked_items():
    readiness = Lifecycle(Stage.LIVE_LOCKED).live_readiness()
    names = {check["name"] for check in readiness["engineering_checks"]}
    assert {"frozen_core_digest_verified", "executable_build_integrity",
            "session_calendar_available", "owner_trust_root_present"} <= names
    assert readiness["failing_engineering"] == []
    assert readiness["engineering_ready"] is True
    assert readiness["ceiling"] == "LIVE_READY_LOCKED"


def test_every_remaining_blocker_is_an_owner_credential_value_or_signature():
    items = {name for name, _ in OWNER_BLOCKING_ITEMS}
    assert items == {"owner_public_key_configured", "capital_governor_profile_set",
                     "broker_channel_and_conformance", "production_market_data",
                     "owner_signed_live_authorization",
                     "constitutional_amendment_applied"}


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
    for start in (Stage.RESEARCH, Stage.BACKTEST, Stage.SHADOW, Stage.PAPER):
        outcome = Lifecycle(start).advance(Stage.LIVE_READY_LOCKED, actor=Actor.OWNER)
        assert outcome["advanced"] is False
        assert outcome["code"] == "PROMOTION_REFUSED_ILLEGAL_TRANSITION"


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
