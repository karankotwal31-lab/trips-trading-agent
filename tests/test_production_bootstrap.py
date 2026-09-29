"""Adversarial tests for the production bootstrap and dual-source Truth evidence."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from providers import Bar, ProviderIdentity  # noqa: E402
from execution.live_verification import assert_non_mutating  # noqa: E402
from execution.production_data import (  # noqa: E402
    REQUIRED_SYMBOLS,
    ProductionDataVerificationError,
    verify_dual_source_production_data,
)
from execution.production_runtime import (  # noqa: E402
    ACCOUNT_ENV,
    ALPACA_KEY_ENV,
    ALPACA_SECRET_ENV,
    BROKER_ENV,
    PRIMARY_PROVIDER_ENV,
    SECONDARY_PROVIDER_ENV,
    build_read_only_broker_verifier,
    external_dependency_status,
    external_readiness_report,
)


class FakeRealtimeProvider:
    def __init__(self, *, name: str, family: str, price_offset: float = 0.0,
                 realtime_attested: bool = True):
        self.identity = ProviderIdentity(
            name, family, can_request_realtime_entitlement=realtime_attested)
        self.source_kind = "real"
        self.interval = "60min"
        self.provider_name = name
        self._offset = float(price_offset)

    def bars(self, symbol: str, count: int = 240):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = now - timedelta(hours=1)
        start = end - timedelta(hours=69)
        seed = {"SPY": 500.0, "QQQ": 450.0, "AAPL": 220.0}[symbol] + self._offset
        rows = []
        for index in range(70):
            ts = start + timedelta(hours=index)
            base = seed + index * 0.03
            rows.append(Bar(
                ts=ts.isoformat(),
                open=base,
                high=base + 0.30,
                low=base - 0.30,
                close=base + 0.05,
                volume=1_000_000 + index,
            ))
        return rows[-count:]


class NeverCalledProvider(FakeRealtimeProvider):
    def bars(self, symbol: str, count: int = 240):
        raise AssertionError("provider bars must not be called when realtime attestation is absent")


def test_dual_source_verification_passes_only_for_independent_realtime_families():
    primary = FakeRealtimeProvider(name="alpha", family="family-alpha")
    secondary = FakeRealtimeProvider(
        name="secondary", family="family-secondary", price_offset=0.10)
    # Keep this structural test independent of the wall-clock's first 20 seconds after the hour.
    # Production keeps the frozen 120-minute freshness limit; here 180 minutes prevents the
    # previous fully closed bar from becoming a one-second boundary race when the newest bar is
    # correctly dropped by the close-lag rule.
    evidence = verify_dual_source_production_data(
        primary, secondary, max_age_minutes=180)
    assert evidence.verified is True
    assert evidence.missing_symbols == ()
    assert evidence.failed_symbols == ()
    assert tuple(sorted(evidence.records)) == tuple(sorted(REQUIRED_SYMBOLS))
    for record in evidence.records.values():
        assert record.passed is True
        assert record.cross_source["passed"] is True
        assert record.primary["source_kind"] == "real"
        assert record.secondary["source_kind"] == "real"


def test_same_provider_family_is_refused_before_data_is_treated_as_independent():
    primary = FakeRealtimeProvider(name="a", family="same")
    secondary = FakeRealtimeProvider(name="b", family="same")
    try:
        verify_dual_source_production_data(primary, secondary)
        raise AssertionError("same-family providers must not satisfy independent-source Truth")
    except ProductionDataVerificationError as exc:
        assert "independent" in str(exc).lower()


def test_provider_without_machine_realtime_attestation_is_refused_before_fetch():
    primary = FakeRealtimeProvider(name="alpha", family="family-alpha")
    secondary = NeverCalledProvider(
        name="twelve_data", family="twelve_data", realtime_attested=False)
    try:
        verify_dual_source_production_data(primary, secondary)
        raise AssertionError("a provider that cannot attest realtime must not be promoted to live")
    except ProductionDataVerificationError as exc:
        assert "realtime" in str(exc).lower()


def test_production_data_scope_cannot_expand_beyond_spy_qqq_aapl():
    primary = FakeRealtimeProvider(name="a", family="fa")
    secondary = FakeRealtimeProvider(name="b", family="fb")
    try:
        verify_dual_source_production_data(
            primary, secondary, symbols=("SPY", "QQQ", "AAPL", "MSFT"))
        raise AssertionError("production-data scope expansion must fail")
    except ProductionDataVerificationError as exc:
        assert "scope" in str(exc).lower()


def test_dependency_status_never_returns_secret_values():
    env = {
        BROKER_ENV: "alpaca",
        ACCOUNT_ENV: "ACCT-123",
        ALPACA_KEY_ENV: "TOP-SECRET-KEY",
        ALPACA_SECRET_ENV: "TOP-SECRET-SECRET",
        PRIMARY_PROVIDER_ENV: "alpha_vantage",
        SECONDARY_PROVIDER_ENV: "twelve_data",
        "ALPHA_VANTAGE_API_KEY": "AV-SECRET",
        "TWELVE_DATA_API_KEY": "TD-SECRET",
    }
    payload = external_dependency_status(environ=env)
    encoded = json.dumps(payload, sort_keys=True)
    for secret in ("TOP-SECRET-KEY", "TOP-SECRET-SECRET", "AV-SECRET", "TD-SECRET"):
        assert secret not in encoded
    assert payload["broker"]["credentials"][ALPACA_KEY_ENV] is True
    assert payload["broker"]["credentials"][ALPACA_SECRET_ENV] is True
    assert payload["mutation_surface_constructed"] is False


def test_missing_external_inputs_fail_closed_without_constructing_mutation_surface():
    report = external_readiness_report(environ={})
    assert report["verified"] is False
    assert report["status"] == "NOT_VERIFIED"
    assert report["submits_orders"] is False
    assert report["cancels_orders"] is False
    assert report["replaces_orders"] is False
    assert report["releases_capital"] is False


def test_live_broker_readiness_surface_is_structurally_non_mutating():
    env = {
        BROKER_ENV: "alpaca",
        ACCOUNT_ENV: "ACCT-123",
        ALPACA_KEY_ENV: "synthetic-key",
        ALPACA_SECRET_ENV: "synthetic-secret",
    }
    verifier = build_read_only_broker_verifier(environ=env)
    target = verifier._target
    assert_non_mutating(target)
    exposed = {name for name in dir(target) if not name.startswith("_")}
    assert not any(name in exposed for name in (
        "submit", "submit_order", "cancel", "cancel_order", "replace", "modify", "place_order"))


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
    print(f"ALL PASS ({len(tests)} production-bootstrap tests)")
