"""Deterministic live-enablement preflight.

This is where the architecture stops being decorative. Earlier, the gateway accepted caller
booleans such as ``truth_valid`` and ``risk_approved``, which meant the "non-bypassable chain"
was only a claim. This module instead INVOKES the frozen core's own gates, in the same order
``engine/forge_agent.py`` uses:

    closed_bars_only -> validate_bars (+ optional independent cross-source verification)
      -> features -> interpret_candles -> evaluate -> consensus
      -> risk.forge_gate -> constitution.constitution_gate

Nothing here is a reimplementation. Truth, strategy, per-trade risk and the Constitution are the
frozen implementations; this module only orchestrates them and adds the portfolio, broker,
session, identity and supervisor conditions the spec requires. No LLM is asked whether live
trading is safe.

Every one of the 20 preconditions is COMPUTED. Where a fact genuinely cannot be observed from
inside this process (exchange calendar, health gate, live-environment attestation) the artifact
must be supplied as a validated, freshness-bounded, provenance-tagged record, and its absence
fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from build_guard import verify_build_integrity
from candle_intelligence import interpret as interpret_candles
from config_guard import ConfigError, fingerprint_config, validate_config
from constitution import constitution_gate
from market_time import closed_bars_only
from providers import Bar, MarketDataError
from risk import forge_gate, position_size
from strategies import consensus, evaluate, features
from truth_guard import apply_cross_source_verification, cross_validate, validate_bars

from .contracts import approved_symbol_scope, canonical_json
from .identity import authorization_drift, current_identity
from .live_path import (LivePathViolation, assert_no_forbidden_trading_environment,
                        assert_no_non_live_environment)
from .registry import AdapterRegistry
from .session import SessionCalendar, SessionStatus

#: The 20 deterministic preconditions. Order is the spec's order.
PRECONDITIONS: Tuple[str, ...] = (
    "approved_strategy_build_identity",
    "approved_configuration_identity",
    "approved_risk_profile",
    "approved_capital_governor_profile",
    "correct_live_brokerage_account",
    "correct_authorized_broker_adapter",
    "correct_environment",
    "healthy_authentication",
    "healthy_broker_connection",
    "clean_reconciliation",
    "healthy_market_data",
    "valid_market_session",
    "no_unresolved_broker_orders",
    "no_unresolved_local_broker_discrepancy",
    "valid_truth_state",
    "valid_system_clock_timezone",
    "global_halt_not_active",
    "loss_drawdown_limits_not_breached",
    "supervisor_not_halt_requested",
    "supervisor_availability_policy_satisfied",
)

#: Critical truth checks whose failure constitutes a data anomaly. Mirrors forge_agent._anomaly.
ANOMALY_CHECKS = frozenset({
    "declared_source_kind", "provider_kind_binding", "nonempty_source", "nonempty_source_family",
    "timezone_aware_timestamps", "strict_time_order", "finite_positive_prices", "ohlc_invariants",
    "nonnegative_volume", "extreme_move_review",
})

MAX_CLOCK_SKEW_SECONDS = 60.0


@dataclass(frozen=True)
class LiveEnvironmentAttestation:
    """Provenance-tagged statement that this process is pointed at a verified live environment."""

    environment: str
    attested_by: str
    attested_at: str
    expires_at: str

    def is_valid(self, now: datetime) -> Tuple[bool, Tuple[str, ...]]:
        reasons: List[str] = []
        if not str(self.attested_by).strip():
            reasons.append("live-environment attestation has no attesting authority")
        for name in ("attested_at", "expires_at"):
            try:
                parsed = datetime.fromisoformat(str(getattr(self, name)).replace("Z", "+00:00"))
            except Exception:
                reasons.append(f"{name} is not parseable ISO-8601")
                continue
            if parsed.tzinfo is None:
                reasons.append(f"{name} must be timezone-aware")
        if reasons:
            return False, tuple(reasons)
        issued = datetime.fromisoformat(str(self.attested_at).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(self.expires_at).replace("Z", "+00:00"))
        if not (issued <= now < expires):
            reasons.append("live-environment attestation is outside its validity window")
        return (not reasons), tuple(reasons)


@dataclass(frozen=True)
class HealthGateEvidence:
    """Structured Guardian health result. Must be fresh; absence fails closed."""

    passed: bool
    checks: Tuple[Mapping[str, Any], ...]
    generated_at: str
    max_age_minutes: int = 30

    def is_valid(self, now: datetime) -> Tuple[bool, Tuple[str, ...]]:
        reasons: List[str] = []
        try:
            generated = datetime.fromisoformat(str(self.generated_at).replace("Z", "+00:00"))
        except Exception:
            return False, ("health gate generated_at is not parseable ISO-8601",)
        if generated.tzinfo is None:
            return False, ("health gate generated_at must be timezone-aware",)
        age = (now - generated).total_seconds() / 60.0
        if age > self.max_age_minutes:
            reasons.append(f"health gate evidence is stale ({age:.1f}m > {self.max_age_minutes}m)")
        if not isinstance(self.checks, (list, tuple)):
            reasons.append("health gate did not report its checks")
        if not self.passed:
            reasons.append("health gate did not pass")
        return (not reasons), tuple(reasons)


@dataclass(frozen=True)
class PreflightReport:
    checks: Tuple[Dict[str, Any], ...]
    trade_valid: Mapping[str, bool]
    capital_release: Mapping[str, bool]
    artifacts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failing(self) -> Tuple[str, ...]:
        return tuple(c["name"] for c in self.checks if not c["passed"])

    @property
    def passed(self) -> bool:
        return not self.failing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "failing": list(self.failing),
            "precondition_count": len(self.checks),
            "preconditions": list(self.checks),
            "TRADE_VALID": dict(self.trade_valid),
            "CAPITAL_RELEASE": dict(self.capital_release),
            "artifacts": dict(self.artifacts),
        }

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def binding_matches(self, intent: Any, *, now: Optional[datetime] = None,
                        max_age_seconds: float = 120.0) -> Tuple[bool, Tuple[str, ...]]:
        """Prove this report describes THIS intent and is recent.

        Without binding, an authority report could be replayed against a different or larger
        intent, which would make the whole preflight decorative.
        """
        now = now or datetime.now(timezone.utc)
        binding = dict(self.artifacts.get("intent_binding") or {})
        reasons: List[str] = []
        if not binding:
            return False, ("preflight report is not bound to any intent",)
        if binding.get("intent_content_hash") != intent.content_hash():
            reasons.append("preflight report was computed for a different intent")
        if binding.get("symbol") != intent.symbol:
            reasons.append(f"preflight symbol {binding.get('symbol')!r} != intent {intent.symbol!r}")
        if int(binding.get("quantity", -1)) != int(intent.quantity):
            reasons.append(f"preflight quantity {binding.get('quantity')} != intent {intent.quantity}")
        try:
            evaluated = datetime.fromisoformat(str(binding.get("evaluated_at")).replace("Z", "+00:00"))
        except Exception:
            reasons.append("preflight evaluation timestamp is unparseable")
        else:
            age = (now - evaluated.astimezone(timezone.utc)).total_seconds()
            if age > max_age_seconds:
                reasons.append(f"preflight report is stale ({age:.0f}s > {max_age_seconds:.0f}s)")
        return (not reasons), tuple(reasons)


def _anomaly(truth: Mapping[str, Any]) -> bool:
    critical_failed = any(not c["passed"] for c in truth.get("checks", []) if c["name"] in ANOMALY_CHECKS)
    cross = truth.get("cross_source")
    return critical_failed or (cross is not None and not cross.get("passed", False))


class PreflightEvaluator:
    """Computes every precondition from the frozen gates plus directly observable state."""

    def __init__(self, *, config: Mapping[str, Any], approved_config_hash: str, governor: Any,
                 adapter: Any, registry: AdapterRegistry, lifecycle: Any, ledger: Any,
                 provider: Any, session_calendar: SessionCalendar, expected_account_id: str,
                 expected_environment: str, secondary_provider: Any = None,
                 authorization: Any = None, data_guard: Any = None, reconciliation: Any = None,
                 safety: Any = None, requested_bars: int = 240) -> None:
        self.config = validate_config(dict(config))
        if fingerprint_config(self.config) != approved_config_hash:
            raise ConfigError("preflight configuration does not match the approved fingerprint")
        self.approved_config_hash = approved_config_hash
        self.governor = governor
        self.adapter = adapter
        self.registry = registry
        self.lifecycle = lifecycle
        self.ledger = ledger
        self.provider = provider
        self.secondary_provider = secondary_provider
        self.session_calendar = session_calendar
        self.expected_account_id = expected_account_id
        self.expected_environment = expected_environment
        self.authorization = authorization
        self.data_guard = data_guard
        self.reconciliation = reconciliation
        self.safety = safety
        self.requested_bars = int(requested_bars)

    # -- truth (frozen) ---------------------------------------------------

    def _evaluate_truth(self, symbol: str) -> Tuple[Optional[Mapping[str, Any]], List[Bar], Optional[str]]:
        cfg = self.config
        if self.provider is None:
            return None, [], "no market-data provider is configured"
        try:
            raw = self.provider.bars(symbol, self.requested_bars)
            bars = closed_bars_only(raw, cfg["bar_interval"],
                                    close_lag_seconds=cfg["truth"]["bar_close_lag_seconds"])
            primary = validate_bars(
                source=self.provider.identity.name, source_family=self.provider.identity.source_family,
                source_kind=cfg["provider_source_kind"], symbol=symbol, interval=cfg["bar_interval"],
                bars=bars, max_age_minutes=cfg["risk"]["max_data_age_minutes"],
                min_bars=cfg["forge"]["require_history_bars"],
                allow_synthetic_analysis=cfg["truth"]["allow_synthetic_analysis"],
                fixed_source_kind=self.provider.identity.fixed_source_kind,
                realtime_request_attested=(self.provider.identity.can_request_realtime_entitlement
                                           and cfg["provider_source_kind"] == "real"),
            )
            cross = None
            if self.secondary_provider is not None:
                sraw = self.secondary_provider.bars(symbol, self.requested_bars)
                sbars = closed_bars_only(sraw, cfg["bar_interval"],
                                         close_lag_seconds=cfg["truth"]["bar_close_lag_seconds"])
                secondary_truth = validate_bars(
                    source=self.secondary_provider.identity.name,
                    source_family=self.secondary_provider.identity.source_family,
                    source_kind=cfg["secondary_source_kind"], symbol=symbol,
                    interval=cfg["bar_interval"], bars=sbars,
                    max_age_minutes=cfg["risk"]["max_data_age_minutes"],
                    min_bars=cfg["forge"]["require_history_bars"],
                    allow_synthetic_analysis=False,
                    fixed_source_kind=self.secondary_provider.identity.fixed_source_kind,
                    realtime_request_attested=(self.secondary_provider.identity.can_request_realtime_entitlement
                                               and cfg["secondary_source_kind"] == "real"),
                )
                cross = cross_validate(primary, bars, secondary_truth, sbars,
                                       max_ohlc_deviation_pct=cfg["truth"]["max_ohlc_deviation_pct"],
                                       max_timestamp_skew_minutes=cfg["truth"]["max_timestamp_skew_minutes"],
                                       min_cross_source_bars=cfg["truth"]["min_cross_source_bars"])
            primary = apply_cross_source_verification(
                primary, cross, cfg["truth"]["require_independent_source_for_trade"])
            return primary.to_dict(), bars, None
        except MarketDataError as exc:
            return None, [], f"market data rejected: {type(exc).__name__}"
        except Exception as exc:  # provider fault is systemic uncertainty, not a feed miss
            return None, [], f"market data path failed: {type(exc).__name__}"

    # -- evaluation -------------------------------------------------------

    def evaluate(self, *, intent: Any, portfolio: Any, price: float, holdings: Mapping[str, int] | None = None,
                 now: datetime | None = None, health_gate: Optional[HealthGateEvidence] = None,
                 environment_attestation: Optional[LiveEnvironmentAttestation] = None,
                 supervisor_available: bool = True) -> PreflightReport:
        now = now or datetime.now(timezone.utc)
        cfg = self.config
        checks: List[Dict[str, Any]] = []
        artifacts: Dict[str, Any] = {}

        def add(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        # 1-4. Identity: build, config, risk profile, governor profile.
        try:
            manifest = verify_build_integrity()
            add("approved_strategy_build_identity", True, f"build manifest {manifest['manifest_hash'][:12]}")
            artifacts["build_hash"] = manifest["manifest_hash"]
        except Exception as exc:
            add("approved_strategy_build_identity", False,
                f"executable build integrity failed: {type(exc).__name__}")
            artifacts["build_hash"] = None

        config_ok = fingerprint_config(cfg) == self.approved_config_hash
        add("approved_configuration_identity", config_ok,
            f"config fingerprint {'matches' if config_ok else 'differs from'} the approved fingerprint")
        add("approved_risk_profile", config_ok, "risk profile is covered by the approved config fingerprint")

        governor_ok = True
        governor_detail = f"profile {self.governor.profile_hash[:12]} v{self.governor.profile_version}"
        drift_reasons: Tuple[str, ...] = ()
        signed_ok = True
        signed_code = "OWNER_SIGNATURE_NOT_EVALUATED"
        if self.authorization is not None:
            # An unsigned artifact is not an authorization, so it cannot satisfy this precondition.
            # Fail closed if the object cannot even prove a signature.
            validator = getattr(self.authorization, "signature_valid", None)
            signed_ok, signed_code, signed_detail = (validator() if callable(validator)
                                                     else (False, "OWNER_SIGNATURE_REQUIRED",
                                                           "authorization artifact proves no signature"))
            current = current_identity(config=dict(cfg), governor_profile_hash=self.governor.profile_hash)
            drifted, drift_reasons = authorization_drift(self.authorization, current)
            governor_ok = bool(signed_ok and not drifted)
            if not signed_ok:
                governor_detail += f" (live authorization rejected: {signed_code} - {signed_detail})"
            elif drifted:
                governor_detail += " (live authorization drifted)"
            else:
                governor_detail += " (live authorization matches)"
        add("approved_capital_governor_profile", governor_ok, governor_detail)
        artifacts["governor"] = self.governor.describe()
        artifacts["authorization_drift"] = list(drift_reasons)
        artifacts["authorization_signature"] = {"ok": bool(signed_ok), "code": signed_code}
        artifacts["intent_binding"] = {
            "intent_content_hash": intent.content_hash(),
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": int(intent.quantity),
            "idempotency_key": intent.idempotency_key,
            "evaluated_at": now.isoformat(),
        }

        # 5-9. Broker identity, account, environment, auth, connection.
        registry_verdict: Mapping[str, Any] = {"permitted": False, "reasons": ["not evaluated"]}
        adapter = self.adapter
        try:
            registration = self.registry.require_executable(getattr(adapter, "broker_id", ""),
                                                            scope=sorted(approved_symbol_scope()))
            registry_verdict = self.registry.execution_verdict(
                registration, scope=sorted(approved_symbol_scope()))
        except Exception as exc:
            registry_verdict = {"permitted": False, "reasons": [str(exc)]}
        add("correct_authorized_broker_adapter", registry_verdict["permitted"],
            "; ".join(registry_verdict["reasons"]) or
            "registered, authorized, fully capable and mandate-compatible")
        artifacts["broker_registry"] = dict(registry_verdict)

        account: Any = None
        health: Any = None
        broker_error: Optional[str] = None
        try:
            account = adapter.account()
            health = adapter.health()
        except Exception as exc:
            broker_error = f"broker unreachable: {type(exc).__name__}"

        account_matches = (
            broker_error is None
            and getattr(account, "account_id", None) == self.expected_account_id
        )
        add("correct_live_brokerage_account", account_matches,
            broker_error or f"broker account {getattr(account, 'account_id', None)!r} "
                            f"vs expected {self.expected_account_id!r}")

        environment_matches = (
            broker_error is None
            and getattr(account, "environment", None) == self.expected_environment
        )
        add("correct_environment", environment_matches,
            broker_error or f"broker environment {getattr(account, 'environment', None)!r} "
                            f"vs expected {self.expected_environment!r}")

        # The canonical live route is LIVE end to end. A demo feed, a delayed feed, a paper
        # endpoint or a sandbox account is not a degraded version of live trading - it is a
        # different system, and there is no runtime fallback between them.
        try:
            route = assert_no_forbidden_trading_environment(
                getattr(adapter, "environment", ""), getattr(account, "environment", ""),
                self.expected_environment)
            environment_matches = environment_matches and bool(route["canonical"])
            artifacts["canonical_live_route"] = route
        except LivePathViolation as exc:
            environment_matches = False
            artifacts["canonical_live_route"] = {"canonical": False, "violation": str(exc)}
            checks[-1]["detail"] = str(exc)

        add("healthy_authentication",
            broker_error is None and bool(getattr(health, "authenticated", False)),
            broker_error or f"authenticated={getattr(health, 'authenticated', None)}")
        add("healthy_broker_connection",
            broker_error is None and bool(getattr(health, "connected", False)),
            broker_error or f"connected={getattr(health, 'connected', None)}")

        skew = getattr(health, "clock_skew_seconds", None)
        clock_ok = broker_error is None and isinstance(skew, (int, float)) and abs(skew) <= MAX_CLOCK_SKEW_SECONDS
        add("valid_system_clock_timezone", clock_ok,
            broker_error or f"broker clock skew {skew}s (limit {MAX_CLOCK_SKEW_SECONDS}s)")

        # 10, 13, 14. Reconciliation and unresolved orders.
        reconciliation = None
        recon_clean = False
        if self.reconciliation is None or broker_error is not None:
            add("clean_reconciliation", False,
                broker_error or "no reconciliation engine is configured")
            add("no_unresolved_broker_orders", False, "reconciliation unavailable")
            add("no_unresolved_local_broker_discrepancy", False, "reconciliation unavailable")
        else:
            try:
                open_orders = list(adapter.open_orders())
            except Exception:
                open_orders = []
                recon_clean = False
            pending = tuple(self.ledger.open_client_order_ids()) if self.ledger is not None else ()
            ambiguity = tuple(self.ledger.unknown_pending()) if self.ledger is not None else ()
            reconciliation = self.reconciliation.reconcile(
                local_positions=dict(getattr(portfolio, "quantities", {}) or {}),
                broker_positions=list(adapter.positions()),
                broker_open_client_order_ids=[str(o.get("client_order_id")) for o in open_orders
                                             if isinstance(o, Mapping)],
                pending_intent_client_order_ids=pending,
                expected_account_id=self.expected_account_id,
                broker_account_id=getattr(account, "account_id", None),
            )
            recon_clean = reconciliation.clean
            add("clean_reconciliation", recon_clean,
                "no discrepancies" if recon_clean else "; ".join(reconciliation.codes))
            add("no_unresolved_broker_orders", not list(open_orders),
                f"{len(open_orders)} open broker order(s)")
            add("no_unresolved_local_broker_discrepancy", recon_clean and not ambiguity,
                "clean" if (recon_clean and not ambiguity)
                else f"discrepancies={list(reconciliation.codes)} unresolved_submissions={list(ambiguity)}")
        artifacts["reconciliation"] = reconciliation.to_dict() if reconciliation is not None else None

        # 11, 15. Market data and Truth (frozen Truth Engine).
        truth, bars, truth_error = self._evaluate_truth(intent.symbol)
        if truth is None:
            add("healthy_market_data", False, truth_error or "no truth verdict")
            add("valid_truth_state", False, truth_error or "no truth verdict")
        else:
            add("healthy_market_data", bool(truth.get("trusted_for_analysis")),
                "analysis-eligible" if truth.get("trusted_for_analysis")
                else "; ".join(truth.get("reasons", [])[:4]))
            add("valid_truth_state", bool(truth.get("trusted_for_trade")),
                "trade-eligible" if truth.get("trusted_for_trade")
                else "; ".join(truth.get("reasons", [])[:4]) or "not trade-eligible")
        artifacts["truth"] = truth

        # Broker data may never be substituted for Truth-eligible market data.
        if self.data_guard is not None and truth is not None:
            from .provenance import DataPurpose, DataSourceRecord

            verdict = self.data_guard.check(
                DataSourceRecord(source=truth["source"], source_family=truth["source_family"],
                                 origin="market_data_provider",
                                 approved_for_truth=bool(truth.get("trusted_for_analysis"))),
                purpose=DataPurpose.TRADE_ELIGIBILITY)
            artifacts["provenance"] = verdict
            if not verdict["permitted"]:
                add("valid_truth_state", False, verdict["reason"])

        # 12. Session truth.
        session_verdict = self.session_calendar.evaluate(now)
        add("valid_market_session", session_verdict.permits_new_exposure,
            f"{session_verdict.status.value}: {'; '.join(session_verdict.reasons) or 'open'}")
        artifacts["session"] = session_verdict.to_dict()

        # 17-20. Halts and loss boundaries.
        portfolio_halted = bool(getattr(portfolio, "halted", False))
        supervisor_halted = bool(getattr(self.safety, "halt_requested", False)) if self.safety else False
        health_valid, health_reasons = (health_gate.is_valid(now) if health_gate is not None
                                        else (False, ("no Guardian health-gate evidence supplied",)))
        add("global_halt_not_active",
            (not portfolio_halted) and (not supervisor_halted) and health_valid,
            f"portfolio_halted={portfolio_halted} supervisor_halted={supervisor_halted} "
            f"health={'ok' if health_valid else '; '.join(health_reasons)}")

        # Loss boundaries are the FROZEN limits, not the Governor's, so an unapproved or
        # permissive Governor profile cannot widen what the Constitution already fixed.
        daily_loss_limit = float(portfolio.equity) * cfg["risk"]["max_daily_loss_pct"]
        daily_ok = float(portfolio.daily_pnl) > -daily_loss_limit
        drawdown_ok = float(portfolio.drawdown_pct) < cfg["forge"]["halt_on_drawdown_pct"]
        add("loss_drawdown_limits_not_breached", daily_ok and drawdown_ok,
            f"daily_pnl={float(portfolio.daily_pnl):.2f} limit=-{daily_loss_limit:.2f}; "
            f"drawdown={float(portfolio.drawdown_pct):.4f} "
            f"halt_at={cfg['forge']['halt_on_drawdown_pct']}")

        add("supervisor_not_halt_requested", not supervisor_halted,
            f"supervisor halt_requested={supervisor_halted}")
        add("supervisor_availability_policy_satisfied", bool(supervisor_available),
            "supervisor available" if supervisor_available else "SUPERVISOR_UNAVAILABLE")
        artifacts["health_gate_valid"] = health_valid

        # -- strategy, risk and constitution (frozen) ---------------------
        market: Dict[str, Any] = {}
        authorized_quantity = 0
        strategy_actionable = False
        risk_approved = False
        constitution_compliant = False
        if truth is not None and bars and len(bars) >= cfg["forge"]["require_history_bars"]:
            f = features(bars)
            candle = interpret_candles(bars)
            votes = evaluate(f)
            c = consensus(votes)
            strategy_actionable = c["direction"] == "LONG"

            stop = price - cfg["trade"]["atr_stop_multiple"] * f.atr14
            if stop > 0 and price > stop:
                authorized_quantity = position_size(
                    float(portfolio.equity), float(price), float(stop),
                    cfg["risk"]["max_risk_per_trade_pct"], cfg["risk"]["max_total_exposure_pct"],
                    float(portfolio.exposure))
                authorized_quantity = min(authorized_quantity,
                                          int(float(portfolio.cash) / float(price)) if price > 0 else 0)

            stale = not any(x["name"] == "freshness" and x["passed"] for x in truth.get("checks", []))
            anomaly = _anomaly(truth)
            gate = forge_gate(
                config=cfg, provider_name=self.provider.identity.name, bars_count=len(bars),
                signal_score=float(c["signal_score"]), conflict=bool(c["conflict"]), stale=stale,
                positions=dict(getattr(portfolio, "positions", {}) or {}),
                pending_entries=dict(getattr(portfolio, "pending_entries", {}) or {}),
                symbol=intent.symbol, daily_pnl=float(portfolio.daily_pnl),
                equity=float(portfolio.equity), drawdown_pct=float(portfolio.drawdown_pct),
                assumed_spread_bps=cfg["risk"]["assumed_spread_bps"],
                cooldown_remaining=int(getattr(portfolio, "cooldown_remaining", 0)),
                global_halt=portfolio_halted or supervisor_halted or not health_valid,
                agreement_count=int(c["agreement_count"]), candle_context=candle.label)
            constitution = constitution_gate(truth=truth, mode=cfg["mode"], gate_passed=gate["passed"],
                                             model_conflict=bool(c["conflict"]), anomaly=anomaly,
                                             requires_verified_trade_data=True)
            risk_approved = bool(gate["passed"])
            constitution_compliant = bool(constitution["passed"])
            market = {"features": f.to_dict(), "candle": candle.to_dict(),
                      "votes": [v.to_dict() for v in votes], "consensus": c,
                      "forge_gate": gate, "candle_label": candle.label}
            artifacts["forge_gate"] = gate
            artifacts["constitution_gate"] = constitution
            artifacts["anomaly"] = anomaly

        # -- governor (per-trade size is ALREADY the frozen risk authorization) --
        scope_ok = (intent.symbol in approved_symbol_scope() and intent.side in {"BUY", "SELL"})
        if intent.side == "SELL" and intent.quantity > int((holdings or {}).get(intent.symbol, 0)):
            scope_ok = False
        size_ok = authorized_quantity > 0 and int(intent.quantity) <= authorized_quantity
        intent_current = not intent.is_expired(now)

        governor_decision = self.governor.evaluate(intent=intent, portfolio=portfolio, price=price)
        artifacts["governor_decision"] = governor_decision.to_dict()

        loss_limits_ok = all(
            check["passed"] for check in governor_decision.checks
            if check["name"] in {"max_daily_loss_pct", "max_drawdown_pct"}
        )

        env_ok = False
        env_reasons: Tuple[str, ...] = ("no live-environment attestation supplied",)
        if environment_attestation is not None:
            env_valid, env_reasons = environment_attestation.is_valid(now)
            env_ok = (env_valid
                      and environment_attestation.environment == self.expected_environment
                      and session_verdict.status is SessionStatus.OPEN
                      and environment_matches)
        artifacts["live_environment"] = {"verified": env_ok, "reasons": list(env_reasons)}

        # -- permissions, all computed ------------------------------------
        trade_valid_map = {
            "truth_valid": bool(truth and truth.get("trusted_for_trade")),
            "strategy_actionable": bool(strategy_actionable),
            "risk_approved": bool(risk_approved),
            "constitution_compliant": bool(constitution_compliant),
            "scope_valid": bool(scope_ok and size_ok),
            "intent_current": bool(intent_current),
        }
        capital_release_map = {
            "governor_permits": bool(governor_decision.allowed),
            "broker_state_reconciled": bool(recon_clean),
            "broker_healthy": broker_error is None and bool(getattr(health, "healthy", False)),
            "correct_account": bool(account_matches),
            "live_environment_verified": bool(env_ok),
            "build_config_integrity_verified": bool(config_ok) and artifacts.get("build_hash") is not None
                                               and bool(registry_verdict["permitted"]),
            "no_unresolved_execution_ambiguity": bool(recon_clean)
                                                 and not (self.ledger.unknown_pending() if self.ledger else ()),
            "no_applicable_halt": (not portfolio_halted) and (not supervisor_halted) and health_valid
                                  and loss_limits_ok,
        }

        # Preconditions 3 and 18 gain computed detail from the frozen gates and the Governor.
        self._refine(checks, "approved_risk_profile",
                     f"frozen forge_gate passed={risk_approved}; constitution passed={constitution_compliant}")
        self._refine(checks, "loss_drawdown_limits_not_breached",
                     f"governor daily/drawdown limits within bounds={loss_limits_ok}")

        # Exactly the 20 spec preconditions, reported in spec order. Drift is a defect.
        order = {name: index for index, name in enumerate(PRECONDITIONS)}
        present = {c["name"] for c in checks}
        missing = [name for name in PRECONDITIONS if name not in present]
        extra = sorted(present - set(order))
        if missing or extra:
            raise AssertionError(f"precondition drift: missing={missing} extra={extra}")
        checks.sort(key=lambda c: order[c["name"]])

        artifacts.update({
            "authorized_quantity": authorized_quantity,
            "intent_quantity": int(intent.quantity),
            "size_within_risk_authorization": bool(size_ok),
            "market": market,
        })

        return PreflightReport(checks=tuple(checks), trade_valid=trade_valid_map,
                               capital_release=capital_release_map, artifacts=artifacts)

    @staticmethod
    def _refine(checks: List[Dict[str, Any]], name: str, detail: str) -> None:
        """Attach computed results to an identity precondition without changing its verdict."""
        for check in checks:
            if check["name"] == name:
                check["detail"] = f"{check['detail']} | {detail}"
                return
