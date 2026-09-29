"""Production bootstrap for live-only Trip's, deliberately read-only before activation.

This module is the reviewed bridge from external credentials to typed readiness evidence. It never
constructs UniversalBrokerGateway, never submits/cancels/replaces an order, and never invents owner
capital values or signatures.

Secrets enter only through explicit environment variables and are passed directly to provider /
broker constructors. Status/report methods expose presence booleans and variable names only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from .channels import AlpacaChannel, LiveAccountReadOnlyView
from .live_verification import LiveReadOnlyBrokerVerifier, LiveReadOnlyVerification
from .market_data import ProductionMarketDataProvider, provider_credential_status
from .production_data import (ProductionDataVerification,
                              verify_dual_source_production_data)
from .realtime_providers import (TWELVE_DATA_CREDENTIAL_ENV,
                                 TwelveDataRealtimeProvider)

BROKER_ENV = "TRIPS_LIVE_BROKER"
ACCOUNT_ENV = "TRIPS_LIVE_ACCOUNT_ID"
ALPACA_KEY_ENV = "APCA_API_KEY_ID"
ALPACA_SECRET_ENV = "APCA_API_SECRET_KEY"
PRIMARY_PROVIDER_ENV = "TRIPS_PRIMARY_DATA_PROVIDER"
SECONDARY_PROVIDER_ENV = "TRIPS_SECONDARY_DATA_PROVIDER"

SUPPORTED_LIVE_BROKERS: Tuple[str, ...] = ("alpaca",)


class ProductionBootstrapError(RuntimeError):
    """External production prerequisites are missing or contradict the live-only architecture."""


def _value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def external_dependency_status(*, environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Report only presence/identity metadata. Secret values are never returned."""
    env = os.environ if environ is None else environ
    broker = _value(env, BROKER_ENV)
    account_present = bool(_value(env, ACCOUNT_ENV))
    primary = _value(env, PRIMARY_PROVIDER_ENV)
    secondary = _value(env, SECONDARY_PROVIDER_ENV)

    broker_status = {
        "broker": broker or None,
        "supported": broker in SUPPORTED_LIVE_BROKERS,
        "account_id_present": account_present,
        "credentials": {
            ALPACA_KEY_ENV: bool(_value(env, ALPACA_KEY_ENV)),
            ALPACA_SECRET_ENV: bool(_value(env, ALPACA_SECRET_ENV)),
        } if broker == "alpaca" else {},
    }
    data_status = {}
    for label, name in (("primary", primary), ("secondary", secondary)):
        data_status[label] = (
            provider_credential_status(name, environ=env)
            if name else {
                "provider": None, "credential_present": False, "trade_eligible": False,
                "note": f"{label} provider is not explicitly configured",
            }
        )
    return {
        "broker": broker_status,
        "market_data": data_status,
        "mutation_surface_constructed": False,
        "releases_capital": False,
    }


def build_read_only_broker_verifier(*, environ: Optional[Mapping[str, str]] = None
                                    ) -> LiveReadOnlyBrokerVerifier:
    """Construct the structurally read-only verifier for the intended live account."""
    env = os.environ if environ is None else environ
    broker = _value(env, BROKER_ENV)
    if broker not in SUPPORTED_LIVE_BROKERS:
        raise ProductionBootstrapError(
            f"{BROKER_ENV} must explicitly name one of {list(SUPPORTED_LIVE_BROKERS)}")
    account_id = _value(env, ACCOUNT_ENV)
    if not account_id:
        raise ProductionBootstrapError(f"{ACCOUNT_ENV} is required")
    if broker == "alpaca":
        api_key = _value(env, ALPACA_KEY_ENV)
        api_secret = _value(env, ALPACA_SECRET_ENV)
        if not api_key or not api_secret:
            raise ProductionBootstrapError(
                f"Alpaca live readiness requires {ALPACA_KEY_ENV} and {ALPACA_SECRET_ENV}")
        channel = AlpacaChannel(
            environment="live", api_key=api_key, api_secret=api_secret,
            allow_mutation_probes=False)
        view = LiveAccountReadOnlyView(channel)
        return LiveReadOnlyBrokerVerifier(
            target=view, expected_account_id=account_id)
    raise ProductionBootstrapError(f"no reviewed live bootstrap exists for broker {broker!r}")


def _build_production_provider(name: str, env: Mapping[str, str]) -> Any:
    """Construct one reviewed realtime provider without relabeling frozen provider identity."""
    if name == "twelve_data":
        key = _value(env, TWELVE_DATA_CREDENTIAL_ENV)
        if not key:
            raise ProductionBootstrapError(
                f"Twelve Data live readiness requires {TWELVE_DATA_CREDENTIAL_ENV}")
        return TwelveDataRealtimeProvider(api_key=key)
    return ProductionMarketDataProvider(
        provider_name=name, source_kind="real", environ=env)


def build_production_data_providers(*, environ: Optional[Mapping[str, str]] = None
                                    ) -> Tuple[Any, Any]:
    """Construct two explicitly named independent realtime provider adapters.

    Alpha Vantage uses the existing production wrapper. Twelve Data uses the additive reviewed
    realtime adapter that translates Trip's 60min interval to the provider's documented 1h
    request while retaining the canonical 60-minute identity presented to frozen Truth.
    """
    env = os.environ if environ is None else environ
    primary_name = _value(env, PRIMARY_PROVIDER_ENV)
    secondary_name = _value(env, SECONDARY_PROVIDER_ENV)
    if not primary_name or not secondary_name:
        raise ProductionBootstrapError(
            f"{PRIMARY_PROVIDER_ENV} and {SECONDARY_PROVIDER_ENV} are both required")
    if primary_name == secondary_name:
        raise ProductionBootstrapError("primary and secondary market-data providers must differ")
    primary = _build_production_provider(primary_name, env)
    secondary = _build_production_provider(secondary_name, env)
    if primary.identity.source_family == secondary.identity.source_family:
        raise ProductionBootstrapError(
            "primary and secondary market data are not independent provider families")
    return primary, secondary


@dataclass(frozen=True)
class ExternalReadinessArtifacts:
    broker: LiveReadOnlyVerification
    market_data: ProductionDataVerification
    generated_at: str

    @property
    def verified(self) -> bool:
        return bool(self.broker.verified and self.market_data.verified)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "verified": self.verified,
            "broker": self.broker.to_dict(),
            "market_data": self.market_data.to_dict(),
            "submits_orders": False,
            "cancels_orders": False,
            "replaces_orders": False,
            "releases_capital": False,
        }


def collect_external_readiness_artifacts(
        *, environ: Optional[Mapping[str, str]] = None,
        now: Optional[datetime] = None) -> ExternalReadinessArtifacts:
    """Run the real external READ-ONLY checks.

    This function may make authenticated GET requests to the broker and market-data providers.
    It cannot place/cancel/replace an order because it never constructs the execution gateway and
    the broker verifier only sees LiveAccountReadOnlyView.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    broker_verifier = build_read_only_broker_verifier(environ=environ)
    broker_evidence = broker_verifier.run(now=now)
    primary, secondary = build_production_data_providers(environ=environ)
    data_evidence = verify_dual_source_production_data(
        primary, secondary, now=now)
    return ExternalReadinessArtifacts(
        broker=broker_evidence, market_data=data_evidence,
        generated_at=now.isoformat())


def external_readiness_report(*, environ: Optional[Mapping[str, str]] = None,
                              now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return a secret-free readiness report and fail closed on every missing dependency."""
    status = external_dependency_status(environ=environ)
    try:
        artifacts = collect_external_readiness_artifacts(environ=environ, now=now)
    except Exception as exc:
        return {
            "status": "NOT_VERIFIED",
            "verified": False,
            "dependency_status": status,
            "reason": f"{type(exc).__name__}: {exc}",
            "submits_orders": False,
            "cancels_orders": False,
            "replaces_orders": False,
            "releases_capital": False,
        }
    return {
        "status": "VERIFIED" if artifacts.verified else "NOT_VERIFIED",
        "verified": artifacts.verified,
        "dependency_status": status,
        "evidence": artifacts.to_dict(),
        "submits_orders": False,
        "cancels_orders": False,
        "replaces_orders": False,
        "releases_capital": False,
    }


__all__ = [
    "ACCOUNT_ENV",
    "ALPACA_KEY_ENV",
    "ALPACA_SECRET_ENV",
    "BROKER_ENV",
    "PRIMARY_PROVIDER_ENV",
    "SECONDARY_PROVIDER_ENV",
    "SUPPORTED_LIVE_BROKERS",
    "ExternalReadinessArtifacts",
    "ProductionBootstrapError",
    "build_production_data_providers",
    "build_read_only_broker_verifier",
    "collect_external_readiness_artifacts",
    "external_dependency_status",
    "external_readiness_report",
]
