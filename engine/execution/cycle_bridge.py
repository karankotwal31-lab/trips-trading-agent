"""Frozen-cycle → execution-layer bridge.

This closes the gap the spec calls "the sequence must not be bypassable". Before this module the
execution layer was a complete but *parallel* subsystem: the frozen ``engine/forge_agent.py`` cycle
produced paper decisions and this layer was never consulted. Now every actionable decision the
frozen cycle generates is routed through:

    frozen Truth -> frozen Risk (forge_gate / position_size) -> frozen Constitution
      -> Capital Governor -> Execution Authority Gate -> Universal Broker Gateway

and each route ends in a recorded outcome.

Two deliberate design choices:

* **No frozen file is touched.** ``forge_agent`` stays byte-identical (it is pinned by
  ``infra/core_v06.sha256``). The bridge reads the AUTHORITATIVE decision object the frozen cycle
  already produced - the pending-entry record in Trip's runtime state - instead of inventing a
  second strategy record. Spec section 18 forbids duplicating the existing decision object.
* **The frozen Risk engine remains the sizing authority.** The bridge does not reimplement
  ``position_size``. It runs preflight once to discover the frozen authorization, then builds the
  real intent at exactly that quantity and runs preflight again so the report is bound to the
  intent that is actually submitted.

Every route in this repository terminates at ``LIVE_LOCKED_REFUSAL``, because the frozen
Constitution still forbids live-money order submission. That is the point: the wiring is real and
the refusal is deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import durable_store
from .capital_governor import CapitalGovernor, PortfolioSnapshot
from .contracts import ExecutionLayerError, ExecutionState, canonical_json
from .intent import ExecutionIntent
from .gateway import IdempotencyLedger, SubmissionOutcome, UniversalBrokerGateway
from .preflight import HealthGateEvidence, LiveEnvironmentAttestation, PreflightEvaluator
from .reconciliation import ReconciliationEngine
from .supervisor import SafetyController
from .task_safety_kernel import TradeProposal

JOURNAL_FILE = "cycle_route_journal.json"
JOURNAL_SCHEMA_VERSION = 1
JOURNAL_MAX_ENTRIES = 500

#: Router-level outcomes that exist before the gateway is consulted.
NO_DECISION_PRICE = "NO_DECISION_PRICE"
STALE_DECISION = "STALE_DECISION"
NOT_SIZED = "NOT_SIZED"
NOT_ACTIONABLE = "NOT_ACTIONABLE"
TASK_BLOCKED = "TASK_BLOCKED"


@dataclass(frozen=True)
class IntentPolicy:
    """Order shape for intents derived from the frozen cycle.

    Defaults translate the frozen paper simulation faithfully: the frozen engine fills at the
    NEXT closed bar's open, which is exactly a ``MARKET`` order in DAY time in force. No price is
    invented, and no limit is fabricated to make a fill more likely.
    """

    order_type: str = "MARKET"
    time_in_force: str = "DAY"
    intent_ttl_seconds: int = 3600
    max_signal_bar_age_seconds: int = 7200

    def to_dict(self) -> Dict[str, Any]:
        return {"order_type": self.order_type, "time_in_force": self.time_in_force,
                "intent_ttl_seconds": self.intent_ttl_seconds,
                "max_signal_bar_age_seconds": self.max_signal_bar_age_seconds,
                "note": ("MARKET/DAY mirrors the frozen 'fill at next closed bar open' paper policy. "
                         "The bridge never invents a limit price.")}


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class CycleDecision:
    """The frozen cycle's authoritative decision, adapted - never replaced.

    ``decision_id`` is DERIVED from the frozen decision object (symbol + signal bar + strategy +
    data-integrity hash). It is a stable reference to an existing record, not a new one.
    """

    symbol: str
    side: str
    strategy: str
    signal_bar_ts: str
    atr_at_signal: float
    data_integrity_hash: str
    source: str
    decision_price: float

    @property
    def decision_id(self) -> str:
        return "frozen-" + _digest({
            "symbol": self.symbol, "signal_bar_ts": self.signal_bar_ts,
            "strategy": self.strategy, "data_integrity_hash": self.data_integrity_hash,
        })[:24]

    def to_dict(self) -> Dict[str, Any]:
        return {"decision_id": self.decision_id, "symbol": self.symbol, "side": self.side,
                "strategy": self.strategy, "signal_bar_ts": self.signal_bar_ts,
                "atr_at_signal": self.atr_at_signal,
                "data_integrity_hash": self.data_integrity_hash, "source": self.source,
                "decision_price": self.decision_price}


def decisions_from_frozen_runtime(runtime: Mapping[str, Any], *,
                                  prices: Mapping[str, float]) -> Tuple[CycleDecision, ...]:
    """Extract actionable decisions from Trip's authoritative runtime state.

    Transparently reports what it could not convert rather than dropping it silently; see
    ``describe_frozen_runtime_decisions``.
    """
    decisions, _skipped = _frozen_runtime_decisions(runtime, prices=prices)
    return tuple(decisions)


def describe_frozen_runtime_decisions(runtime: Mapping[str, Any], *,
                                      prices: Mapping[str, float]) -> Dict[str, Any]:
    """Same extraction, but with the unconvertible entries named and explained."""
    decisions, skipped = _frozen_runtime_decisions(runtime, prices=prices)
    return {"decisions": [d.to_dict() for d in decisions], "skipped": skipped,
            "pending_entry_count": len((runtime.get("portfolio") or {}).get("pending_entries") or {})}


def _frozen_runtime_decisions(runtime: Mapping[str, Any],
                              *, prices: Mapping[str, float]) -> Tuple[List[CycleDecision], List[Dict[str, Any]]]:
    portfolio = dict(runtime.get("portfolio") or {})
    pending = dict(portfolio.get("pending_entries") or {})
    positions = dict(portfolio.get("positions") or {})
    decisions: List[CycleDecision] = []
    skipped: List[Dict[str, Any]] = []
    for symbol in sorted(pending):
        entry = dict(pending[symbol] or {})
        if symbol in positions:
            skipped.append({"symbol": symbol, "code": "ALREADY_IN_POSITION",
                            "detail": "a pending entry competes with an open position"})
            continue
        signal_bar_ts = str(entry.get("signal_bar_ts") or "")
        if not signal_bar_ts:
            skipped.append({"symbol": symbol, "code": "NO_SIGNAL_BAR",
                            "detail": "pending entry has no signal bar timestamp"})
            continue
        price = prices.get(symbol)
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not float(price) > 0:
            skipped.append({"symbol": symbol, "code": "NO_DECISION_PRICE",
                            "detail": "no positive decision-bar price was supplied for this symbol"})
            continue
        decisions.append(CycleDecision(
            symbol=symbol, side="BUY", strategy=str(entry.get("strategy") or "unknown"),
            signal_bar_ts=signal_bar_ts, atr_at_signal=float(entry.get("atr_at_signal") or 0.0),
            data_integrity_hash=str(entry.get("data_integrity_hash") or ""),
            source=str(entry.get("source") or "unknown"), decision_price=float(price)))
    return decisions, skipped


@dataclass(frozen=True)
class RouteResult:
    symbol: str
    decision_id: str
    intent_id: Optional[str]
    quantity: int
    outcome: str
    state: str
    transmitted: bool
    preflight_passed: bool
    failing_preconditions: Tuple[str, ...] = ()
    failing_permissions: Tuple[str, ...] = ()
    gate_decision: Optional[str] = None
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "decision_id": self.decision_id, "intent_id": self.intent_id,
                "quantity": self.quantity, "outcome": self.outcome, "state": self.state,
                "transmitted": self.transmitted, "preflight_passed": self.preflight_passed,
                "failing_preconditions": list(self.failing_preconditions),
                "failing_permissions": list(self.failing_permissions),
                "gate_decision": self.gate_decision, "notes": list(self.notes)}


class CycleRouter:
    """Routes frozen-cycle decisions through preflight, the gate and the broker gateway."""

    def __init__(self, *, config: Mapping[str, Any], approved_config_hash: str,
                 governor: CapitalGovernor, adapter: Any, registry: Any, lifecycle: Any,
                 provider: Any, session_calendar: Any, expected_account_id: str,
                 expected_environment: str, policy: Optional[IntentPolicy] = None,
                 ledger: Optional[IdempotencyLedger] = None,
                 safety: Optional[SafetyController] = None,
                 task_kernel: Any = None, task_context_provider: Any = None,
                 secondary_provider: Any = None, data_guard: Any = None,
                 reconciliation: Optional[ReconciliationEngine] = None,
                 requested_bars: int = 240) -> None:
        self._config = dict(config)
        self._approved_config_hash = approved_config_hash
        self._governor = governor
        self._adapter = adapter
        self._registry = registry
        self._lifecycle = lifecycle
        self._provider = provider
        self._secondary_provider = secondary_provider
        self._session_calendar = session_calendar
        self._expected_account_id = expected_account_id
        self._expected_environment = expected_environment
        self._data_guard = data_guard
        self._reconciliation = reconciliation or ReconciliationEngine()
        self._policy = policy or IntentPolicy()
        self._safety = safety or SafetyController()
        self._task_kernel = task_kernel
        self._task_context_provider = task_context_provider
        self._ledger = ledger or IdempotencyLedger()
        self._gateway = UniversalBrokerGateway(
            adapter=adapter, governor=governor, lifecycle=lifecycle, ledger=self._ledger,
            reconciliation=self._reconciliation, safety=self._safety, registry=registry,
            bar_interval=str(config["bar_interval"]),
            bar_close_lag_seconds=float(config["truth"]["bar_close_lag_seconds"]))
        self._evaluator = PreflightEvaluator(
            config=self._config, approved_config_hash=approved_config_hash, governor=governor,
            adapter=adapter, registry=registry, lifecycle=lifecycle, ledger=self._ledger,
            provider=provider, session_calendar=session_calendar,
            expected_account_id=expected_account_id, expected_environment=expected_environment,
            secondary_provider=secondary_provider, data_guard=data_guard,
            reconciliation=self._reconciliation, safety=self._safety,
            requested_bars=requested_bars)

    @property
    def policy(self) -> IntentPolicy:
        return self._policy

    @property
    def gateway(self) -> UniversalBrokerGateway:
        return self._gateway

    @property
    def safety(self) -> SafetyController:
        return self._safety

    # -- intent construction ---------------------------------------------

    def _build_intent(self, decision: CycleDecision, *, quantity: int, cycle_number: int,
                      now: datetime) -> ExecutionIntent:
        if quantity <= 0:
            raise ExecutionLayerError("cannot build an intent with a non-positive quantity")
        from .identity import approved_build_hash

        return ExecutionIntent(
            intent_id="intent-" + _digest({"decision_id": decision.decision_id,
                                           "quantity": int(quantity),
                                           "order_type": self._policy.order_type})[:24],
            idempotency_key="trips-cycle-" + _digest({
                "symbol": decision.symbol, "signal_bar_ts": decision.signal_bar_ts,
                "strategy": decision.strategy, "build": approved_build_hash()})[:32],
            correlation_id=f"frozen-cycle-{int(cycle_number)}",
            decision_id=decision.decision_id, decision_bar_ts=decision.signal_bar_ts,
            symbol=decision.symbol, side=decision.side, quantity=int(quantity),
            order_type=self._policy.order_type, time_in_force=self._policy.time_in_force,
            limit_price=None,
            strategy_build_id=approved_build_hash(),
            config_id=self._approved_config_hash,
            truth_ref=decision.data_integrity_hash or "truth-unavailable",
            risk_ref=f"forge_gate:{decision.signal_bar_ts}",
            governor_ref=self._governor.profile_hash,
            created_at=now.astimezone(timezone.utc).isoformat(),
            expires_at=(now.astimezone(timezone.utc)
                        + timedelta(seconds=self._policy.intent_ttl_seconds)).isoformat())

    def _portfolio(self, runtime: Mapping[str, Any], *, new_exposure_this_period: float,
                   exclude_symbol: Optional[str] = None) -> PortfolioSnapshot:
        """Build the portfolio view for one decision.

        ``exclude_symbol`` mirrors the frozen cycle exactly: ``forge_agent._execute_pending``
        passes ``pending_entries={k: v for k, v in ... if k != symbol}``, because the entry being
        acted on is cleared by that same step. Without the same exclusion the frozen
        ``forge_gate`` would see this decision's own pending entry and block every decision with
        "already pending" - a real behaviour, wrongly applied.
        """
        state = dict(runtime.get("portfolio") or {})
        if exclude_symbol is not None:
            state["pending_entries"] = {
                symbol: entry for symbol, entry in (state.get("pending_entries") or {}).items()
                if symbol != exclude_symbol}
        profile_cap = float(self._governor.profile["max_deployable_capital"])
        return PortfolioSnapshot.from_runtime_state(
            state, deployable_capital=min(profile_cap, float(state.get("equity", 0.0))),
            new_exposure_this_period=float(new_exposure_this_period))

    # -- routing ----------------------------------------------------------

    def route(self, decisions: Sequence[CycleDecision], *, runtime: Mapping[str, Any],
              new_exposure_this_period: float, now: Optional[datetime] = None,
              health_gate: Optional[HealthGateEvidence] = None,
              environment_attestation: Optional[LiveEnvironmentAttestation] = None,
              supervisor_available: bool = True,
              holdings: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
        """Route every decision. Records an outcome per decision; never raises for a refusal."""
        now = now or datetime.now(timezone.utc)
        portfolio = dict(runtime.get("portfolio") or {})
        cycle_number = int(portfolio.get("cycle_count") or 0)
        holdings = dict(holdings) if holdings is not None else None
        results: List[RouteResult] = []

        for decision in decisions:
            # One snapshot per decision so the decision's own pending entry is excluded, exactly
            # as the frozen cycle does when it evaluates that symbol.
            snapshot = self._portfolio(runtime, new_exposure_this_period=new_exposure_this_period,
                                       exclude_symbol=decision.symbol)
            results.append(self._route_one(
                decision, snapshot=snapshot,
                holdings=holdings if holdings is not None else snapshot.quantities,
                cycle_number=cycle_number, now=now, health_gate=health_gate,
                environment_attestation=environment_attestation,
                supervisor_available=supervisor_available))

        report = {
            "schema_version": 2,
            "generated_at": now.astimezone(timezone.utc).isoformat(),
            "cycle_number": cycle_number,
            "policy": self._policy.to_dict(),
            "decision_count": len(decisions),
            "transmitted": sum(1 for r in results if r.transmitted),
            "refused": sum(1 for r in results if not r.transmitted),
            "results": [r.to_dict() for r in results],
            "lifecycle_stage": getattr(self._lifecycle, "stage", None).value
            if getattr(self._lifecycle, "stage", None) is not None else None,
            "core_state_source": self._lifecycle.core_state_basis().source,
            "note": ("Every frozen-cycle decision was routed through the frozen Truth, Risk and "
                     "Constitution gates, the Capital Governor, the Execution Authority Gate and "
                     "the Universal Broker Gateway. A refusal is recorded, never suppressed."),
        }
        self._journal(report)
        return report

    def _route_one(self, decision: CycleDecision, *, snapshot: PortfolioSnapshot,
                   holdings: Mapping[str, int], cycle_number: int, now: datetime,
                   health_gate: Optional[HealthGateEvidence],
                   environment_attestation: Optional[LiveEnvironmentAttestation],
                   supervisor_available: bool) -> RouteResult:
        def record(outcome: str, state: str, *, intent_id: Optional[str] = None, quantity: int = 0,
                   preflight: Any = None, notes: Sequence[str] = (),
                   failing_permissions: Sequence[str] = (),
                   gate_decision: Optional[str] = None) -> RouteResult:
            return RouteResult(
                symbol=decision.symbol, decision_id=decision.decision_id, intent_id=intent_id,
                quantity=int(quantity), outcome=outcome, state=state, transmitted=False,
                preflight_passed=bool(preflight.passed) if preflight is not None else False,
                failing_preconditions=tuple(preflight.failing) if preflight is not None else (),
                failing_permissions=tuple(failing_permissions), gate_decision=gate_decision,
                notes=tuple(notes))

        age = (now.astimezone(timezone.utc)
               - datetime.fromisoformat(decision.signal_bar_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
               ).total_seconds()
        if age > self._policy.max_signal_bar_age_seconds:
            return record(STALE_DECISION, ExecutionState.REFUSED.value,
                          notes=(f"signal bar is {age:.0f}s old; limit "
                                 f"{self._policy.max_signal_bar_age_seconds}s",))

        def evaluate(quantity: int):
            probe = self._build_intent(decision, quantity=quantity, cycle_number=cycle_number, now=now)
            report = self._evaluator.evaluate(
                intent=probe, portfolio=snapshot, price=decision.decision_price,
                holdings=holdings, now=now, health_gate=health_gate,
                environment_attestation=environment_attestation,
                supervisor_available=supervisor_available)
            return probe, report

        # A live-money route may never bypass TASK. Existing locked/research routes remain
        # runnable without an owner-approved TASK policy so the safety kernel can be developed and
        # tested before it is configured. The moment lifecycle reaches LIVE_ENABLED, absence of
        # TASK itself is a deterministic refusal.
        stage = getattr(getattr(self._lifecycle, "stage", None), "value", None)
        if stage == "LIVE_ENABLED" and self._task_kernel is None:
            return record(
                TASK_BLOCKED, ExecutionState.REFUSED.value,
                notes=("LIVE_ENABLED requires an owner-approved TASK safety kernel; none is configured",))

        # Phase 1: let the FROZEN risk engine state the authorized size. No reimplementation.
        probe_intent, probe_report = evaluate(1)
        authorized = int((probe_report.artifacts or {}).get("authorized_quantity", 0))
        if authorized <= 0:
            return record(NOT_SIZED, ExecutionState.REFUSED.value, intent_id=probe_intent.intent_id,
                          preflight=probe_report,
                          notes=("the frozen risk engine authorized zero size for this decision",))

        # TASK is deliberately between sizing and immutable intent creation. It may preserve the
        # frozen Risk quantity, reduce it, or block it; it may never increase it. This placement
        # keeps adaptive guardrails out of strategy logic without allowing an adapter/gateway to
        # mutate an already-authorized economic intent.
        task_notes: List[str] = []
        if self._task_kernel is not None:
            if self._task_context_provider is None:
                return record(
                    TASK_BLOCKED, ExecutionState.REFUSED.value,
                    intent_id=probe_intent.intent_id, preflight=probe_report,
                    notes=("TASK is configured but no execution-context provider is configured",))
            try:
                task_context = self._task_context_provider(decision=decision, now=now)
                task_proposal = TradeProposal(
                    symbol=decision.symbol,
                    side=decision.side,
                    desired_quantity=authorized,
                    order_type=self._policy.order_type,
                    limit_price=None,
                    reference_price=decision.decision_price,
                    strategy_id=decision.strategy,
                )
                task_decision = self._task_kernel.evaluate(
                    task_proposal, context=task_context, now=now)
            except Exception as exc:
                return record(
                    TASK_BLOCKED, ExecutionState.REFUSED.value,
                    intent_id=probe_intent.intent_id, preflight=probe_report,
                    notes=(f"TASK evaluation failed closed: {type(exc).__name__}",))

            if (not task_decision.allowed) or task_decision.approved_quantity <= 0:
                return record(
                    TASK_BLOCKED, ExecutionState.REFUSED.value,
                    intent_id=probe_intent.intent_id, preflight=probe_report,
                    notes=tuple(task_decision.blocks) or ("TASK refused new exposure",))
            if task_decision.approved_quantity > authorized:
                return record(
                    TASK_BLOCKED, ExecutionState.REFUSED.value,
                    intent_id=probe_intent.intent_id, preflight=probe_report,
                    notes=("TASK attempted to increase frozen Risk authority; refused",))
            if task_decision.approved_quantity < authorized:
                task_notes.append(
                    f"TASK reduced quantity {authorized}->{task_decision.approved_quantity}")
            task_notes.extend(task_decision.advisories)
            authorized = int(task_decision.approved_quantity)

        # Phase 2: build the real intent at exactly the safe authorization and re-run preflight so
        # the report is bound to the intent that is actually submitted.
        intent, report = evaluate(authorized)
        submission = self._gateway.submit(
            intent, preflight_report=report, portfolio=snapshot, holdings=holdings,
            expected_account_id=self._expected_account_id,
            expected_environment=self._expected_environment, now=now)
        decision_map = submission.gate or {}
        failing_permissions: Tuple[str, ...] = ()
        gate_decision = decision_map.get("decision") if isinstance(decision_map, Mapping) else None
        if isinstance(decision_map, Mapping):
            failing = []
            for permission_name in ("TRADE_VALID", "CAPITAL_RELEASE"):
                permission = decision_map.get(permission_name) or {}
                failing.extend(permission.get("failing") or [])
            failing_permissions = tuple(failing)
        return RouteResult(
            symbol=decision.symbol, decision_id=decision.decision_id, intent_id=intent.intent_id,
            quantity=intent.quantity, outcome=submission.outcome, state=submission.state,
            transmitted=submission.transmitted, preflight_passed=bool(report.passed),
            failing_preconditions=tuple(report.failing), failing_permissions=failing_permissions,
            gate_decision=gate_decision, notes=tuple(task_notes) + tuple(submission.reasons))

    # -- durable journal --------------------------------------------------

    def _journal(self, report: Mapping[str, Any]) -> None:
        data = durable_store.read_json(JOURNAL_FILE, None, strict=False)
        if not isinstance(data, dict) or data.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            data = {"schema_version": JOURNAL_SCHEMA_VERSION, "entries": []}
        entries = list(data.get("entries") or [])
        entries.append({
            "generated_at": report.get("generated_at"), "cycle_number": report.get("cycle_number"),
            "decision_count": report.get("decision_count"),
            "transmitted": report.get("transmitted"), "refused": report.get("refused"),
            "core_state_source": report.get("core_state_source"),
            "results": [{k: r[k] for k in ("symbol", "decision_id", "intent_id", "quantity",
                                          "outcome", "state", "transmitted")}
                        for r in report.get("results") or []],
        })
        data["entries"] = entries[-JOURNAL_MAX_ENTRIES:]
        durable_store.write_json(JOURNAL_FILE, data)

    def journal(self) -> Dict[str, Any]:
        data = durable_store.read_json(JOURNAL_FILE, None, strict=False)
        if not isinstance(data, dict):
            return {"schema_version": JOURNAL_SCHEMA_VERSION, "entries": []}
        return data


def route_frozen_cycle(router: CycleRouter, *, runtime: Mapping[str, Any],
                       prices: Mapping[str, float], new_exposure_this_period: float,
                       **kwargs: Any) -> Dict[str, Any]:
    """Convenience entry point: extract decisions from a frozen runtime and route them."""
    found = describe_frozen_runtime_decisions(runtime, prices=prices)
    report = router.route(decisions_from_frozen_runtime(runtime, prices=prices),
                          runtime=runtime, new_exposure_this_period=new_exposure_this_period, **kwargs)
    report["extraction"] = found
    return report
