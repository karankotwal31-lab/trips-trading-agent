"""Non-mutating production bootstrap for Trip's.

This module prepares and verifies the external live environment without creating execution
authority. It deliberately does NOT construct a UniversalBrokerGateway, does NOT transition the
Lifecycle to LIVE_ENABLED, and exposes no order submit/cancel/replace operation.

External verification is opt-in. With verify_external=False it performs only local integrity
and configuration inspection. With verify_external=True it may make read-only requests to the
configured broker account and market-data providers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from build_guard import verify_build_integrity
from config_guard import validate_config

from .capital_governor import CapitalGovernor, fingerprint_profile
from .channels import AlpacaChannel, LiveAccountReadOnlyView
from .live_verification import LiveReadOnlyBrokerVerifier
from .market_data import (APPROVED_INTERVAL, ProductionMarketDataProvider,
                          provider_credential_status, verify_production_market_data)
from .owner_authority import owner_authority_status
from .provenance import DataSourceGuard
from .readiness import verify_frozen_core_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "engine" / "config.json"

SUPPORTED_LIVE_BROKERS: Tuple[str, ...] = ("alpaca",)
REQUIRED_BROKER_ENV = ("TRIPS_LIVE_BROKER", "TRIPS_LIVE_ACCOUNT_ID",
                       "ALPACA_API_KEY", "ALPACA_API_SECRET")
REQUIRED_DATA_ENV = ("TRIPS_PRIMARY_DATA_PROVIDER", "TRIPS_SECONDARY_DATA_PROVIDER")
GOVERNOR_ENV = "TRIPS_GOVERNOR_PROFILE_JSON"


class ProductionBootstrapError(RuntimeError):
    """Production bootstrap input is malformed. Never converts uncertainty into readiness."""


def _present(environ: Mapping[str, str], name: str) -> bool:
    return bool(str(environ.get(name, "") or "").strip())


def _missing(environ: Mapping[str, str], names: Tuple[str, ...]) -> list[str]:
    return [name for name in names if not _present(environ, name)]


def _approved_config() -> Dict[str, Any]:
    return validate_config(json.loads(CONFIG_PATH.read_text()))


def governor_profile_status(environ: Mapping[str, str]) -> Dict[str, Any]:
    """Validate owner-supplied Governor JSON without inventing a single financial value."""
    raw = str(environ.get(GOVERNOR_ENV, "") or "").strip()
    if not raw:
        return {
            "configured": False,
            "valid": False,
            "missing": [GOVERNOR_ENV],
            "reason": "owner Capital Governor profile is not supplied",
        }
    try:
        payload = json.loads(raw)
    except Exception as exc:
        return {"configured": True, "valid": False,
                "reason": f"Governor JSON is invalid: {type(exc).__name__}"}
    if not isinstance(payload, dict):
        return {"configured": True, "valid": False,
                "reason": "Governor JSON must be an object"}
    profile = payload.get("profile")
    version = str(payload.get("profile_version") or "")
    approved_hash = str(payload.get("approved_profile_hash") or "")
    if not isinstance(profile, dict):
        return {"configured": True, "valid": False,
                "reason": "Governor JSON has no profile object"}
    if not approved_hash:
        computed = fingerprint_profile(profile)
        return {
            "configured": True,
            "valid": False,
            "reason": ("approved_profile_hash is missing; the bootstrap will not self-approve "
                       "owner financial authority"),
            "computed_profile_hash": computed,
        }
    try:
        governor = CapitalGovernor(profile, profile_version=version,
                                   approved_profile_hash=approved_hash)
    except Exception as exc:
        return {"configured": True, "valid": False,
                "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "configured": True,
        "valid": True,
        "profile_version": governor.profile_version,
        "profile_hash": governor.profile_hash,
        "financial_values_echoed": False,
    }


def frozen_config_status(*, primary_provider: str = "", secondary_provider: str = "") -> Dict[str, Any]:
    """Report exact frozen-config changes still requiring owner-reviewed amendment."""
    cfg = _approved_config()
    required = {
        "mode": "live",
        "provider": primary_provider or "<owner-selected-primary>",
        "secondary_provider": secondary_provider or "<owner-selected-secondary>",
        "provider_source_kind": "real",
        "secondary_source_kind": "real",
        "symbols": ["SPY", "QQQ", "AAPL"],
        "bar_interval": APPROVED_INTERVAL,
        "require_independent_source_for_trade": True,
    }
    observed = {
        "mode": cfg.get("mode"),
        "provider": cfg.get("provider"),
        "secondary_provider": cfg.get("secondary_provider"),
        "provider_source_kind": cfg.get("provider_source_kind"),
        "secondary_source_kind": cfg.get("secondary_source_kind"),
        "symbols": list(cfg.get("symbols") or ()),
        "bar_interval": cfg.get("bar_interval"),
        "require_independent_source_for_trade":
            bool((cfg.get("truth") or {}).get("require_independent_source_for_trade")),
    }
    mismatches = []
    if observed["mode"] != "live":
        mismatches.append("mode")
    if primary_provider and observed["provider"] != primary_provider:
        mismatches.append("provider")
    if secondary_provider and observed["secondary_provider"] != secondary_provider:
        mismatches.append("secondary_provider")
    if observed["provider_source_kind"] != "real":
        mismatches.append("provider_source_kind")
    if observed["secondary_source_kind"] != "real":
        mismatches.append("secondary_source_kind")
    if set(observed["symbols"]) != {"SPY", "QQQ", "AAPL"}:
        mismatches.append("symbols")
    if observed["bar_interval"] != APPROVED_INTERVAL:
        mismatches.append("bar_interval")
    if observed["require_independent_source_for_trade"] is not True:
        mismatches.append("require_independent_source_for_trade")
    return {
        "matches_live_target": not mismatches,
        "observed": observed,
        "required": required,
        "mismatches": mismatches,
        "note": "frozen configuration is never changed by this bootstrap",
    }


def broker_status(environ: Mapping[str, str], *, verify_external: bool) -> Dict[str, Any]:
    broker_id = str(environ.get("TRIPS_LIVE_BROKER", "") or "").strip().lower()
    missing = _missing(environ, REQUIRED_BROKER_ENV)
    if missing:
        return {"configured": False, "verified": False, "broker_id": broker_id or None,
                "missing": missing, "external_request_made": False}
    if broker_id not in SUPPORTED_LIVE_BROKERS:
        return {"configured": True, "verified": False, "broker_id": broker_id,
                "reason": f"unsupported production broker {broker_id!r}",
                "external_request_made": False}
    if not verify_external:
        return {"configured": True, "verified": False, "broker_id": broker_id,
                "reason": "external verification was not requested",
                "external_request_made": False}

    channel = None
    try:
        channel = AlpacaChannel(
            environment="live",
            api_key=str(environ["ALPACA_API_KEY"]).strip(),
            api_secret=str(environ["ALPACA_API_SECRET"]).strip())
        view = LiveAccountReadOnlyView(channel)
        verifier = LiveReadOnlyBrokerVerifier(
            target=view,
            expected_account_id=str(environ["TRIPS_LIVE_ACCOUNT_ID"]).strip())
        verification = verifier.run()
        return {
            "configured": True,
            "verified": bool(verification.verified),
            "broker_id": broker_id,
            "evidence": verification.to_dict(),
            "summary": verifier.summarize(verification),
            "external_request_made": True,
            "mutating_request_made": False,
        }
    except Exception as exc:
        return {"configured": True, "verified": False, "broker_id": broker_id,
                "reason": f"{type(exc).__name__}: {exc}",
                "external_request_made": True, "mutating_request_made": False}
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass


def market_data_status(environ: Mapping[str, str], *, verify_external: bool) -> Dict[str, Any]:
    primary = str(environ.get("TRIPS_PRIMARY_DATA_PROVIDER", "") or "").strip()
    secondary = str(environ.get("TRIPS_SECONDARY_DATA_PROVIDER", "") or "").strip()
    if not primary or not secondary:
        return {
            "configured": False, "verified": False,
            "missing": _missing(environ, REQUIRED_DATA_ENV),
            "primary_provider": primary or None,
            "secondary_provider": secondary or None,
            "external_request_made": False,
        }
    if primary == secondary:
        return {"configured": True, "verified": False,
                "primary_provider": primary, "secondary_provider": secondary,
                "reason": "primary and secondary providers must be independent",
                "external_request_made": False}

    primary_credential = provider_credential_status(primary, environ=environ)
    secondary_credential = provider_credential_status(secondary, environ=environ)
    credentials_ready = (primary_credential.get("credential_present") is True
                         and secondary_credential.get("credential_present") is True)
    base = {
        "configured": True,
        "primary_provider": primary,
        "secondary_provider": secondary,
        "primary_credential": primary_credential,
        "secondary_credential": secondary_credential,
    }
    if not credentials_ready:
        return {**base, "verified": False,
                "reason": "one or both production provider credentials are missing",
                "external_request_made": False}
    if not verify_external:
        return {**base, "verified": False,
                "reason": "external verification was not requested",
                "external_request_made": False}

    try:
        p = ProductionMarketDataProvider(
            provider_name=primary, source_kind="real", interval=APPROVED_INTERVAL)
        s = ProductionMarketDataProvider(
            provider_name=secondary, source_kind="real", interval=APPROVED_INTERVAL)
        guard = DataSourceGuard(
            approved_sources=(p.identity.name, s.identity.name),
            approved_families=(p.identity.source_family, s.identity.source_family))
        verification = verify_production_market_data(p, s, data_guard=guard)
        return {
            **base,
            "verified": bool(verification.verified),
            "evidence": verification.to_dict(),
            "external_request_made": True,
            "mutating_request_made": False,
        }
    except Exception as exc:
        return {**base, "verified": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "external_request_made": True,
                "mutating_request_made": False}


def production_bootstrap_status(*, verify_external: bool = False,
                                environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Return one sanitized readiness document. It cannot create execution authority."""
    env = os.environ if environ is None else environ
    build = verify_build_integrity()
    frozen = verify_frozen_core_digest()
    owner = owner_authority_status()
    governor = governor_profile_status(env)
    primary = str(env.get("TRIPS_PRIMARY_DATA_PROVIDER", "") or "").strip()
    secondary = str(env.get("TRIPS_SECONDARY_DATA_PROVIDER", "") or "").strip()
    config = frozen_config_status(primary_provider=primary, secondary_provider=secondary)
    broker = broker_status(env, verify_external=verify_external)
    data = market_data_status(env, verify_external=verify_external)

    external_complete = bool(broker.get("verified") and data.get("verified"))
    owner_inputs_complete = bool(owner.get("configured") and governor.get("valid"))
    ready_for_owner_amendment_review = bool(
        build and frozen.get("verified") and external_complete and owner_inputs_complete)

    return {
        "schema_version": 1,
        "mode": "PRODUCTION_BOOTSTRAP_READ_ONLY",
        "verify_external_requested": bool(verify_external),
        "build_integrity": {"verified": True, "manifest_hash": build["manifest_hash"]},
        "frozen_core": frozen,
        "owner_authority": owner,
        "capital_governor": governor,
        "frozen_config": config,
        "broker": broker,
        "market_data": data,
        "external_verification_complete": external_complete,
        "owner_inputs_complete": owner_inputs_complete,
        "ready_for_owner_amendment_review": ready_for_owner_amendment_review,
        "constructs_execution_gateway": False,
        "transitions_live_enabled": False,
        "releases_capital": False,
        "places_orders": False,
        "next_stage_ceiling": "LIVE_READY_LOCKED",
        "note": ("This bootstrap verifies external dependencies only. It never constructs the "
                 "execution gateway, never transitions LIVE_ENABLED, and never places/cancels/"
                 "replaces an order. Frozen config remains unchanged until an explicit owner "
                 "amendment is reviewed, signed, applied and re-frozen."),
    }


__all__ = [
    "GOVERNOR_ENV",
    "REQUIRED_BROKER_ENV",
    "REQUIRED_DATA_ENV",
    "SUPPORTED_LIVE_BROKERS",
    "ProductionBootstrapError",
    "broker_status",
    "frozen_config_status",
    "governor_profile_status",
    "market_data_status",
    "production_bootstrap_status",
]
