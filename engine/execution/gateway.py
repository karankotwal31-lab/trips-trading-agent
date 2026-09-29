"""Universal Broker Gateway — broker-neutral execution infrastructure.

Owns adapter discovery, capability enforcement, canonical intent translation, authorised
submission, event normalisation, reconciliation-aware state, durable idempotency, execution-state
persistence and health reporting. It contains NO trading intelligence and cannot decide whether
Trip's should trade.

The gateway no longer accepts permission booleans. Authority comes from a ``PreflightReport``
produced by ``preflight.PreflightEvaluator``, which invokes the frozen Truth, Risk and
Constitution gates itself. The gateway's own job is the transmission boundary:

* prove the report is bound to THIS intent and is not stale,
* independently re-verify the broker-observable facts (they can change after preflight),
* independently re-enforce scope and long-only,
* enforce the capability contract,
* enforce durable idempotency,
* refuse to transmit while the frozen live boundary withholds release,
* never blindly resubmit after an unknown outcome.

No real broker adapter ships in this repository. Where no authorized programmable interface
exists the answer is BROKER_AUTOMATION_UNSUPPORTED, never a fake implementation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from . import durable_store
from .capital_governor import CapitalGovernor, PortfolioSnapshot
from .contracts import (
    CAPABILITY_UNSUPPORTED,
    ORDER_INCOMPATIBLE,
    CapabilityError,
    ExecutionState,
    IntentExpired,
    LedgerError,
    assert_preserves_economic_meaning,
    assert_transition_allowed,
    approved_symbol_scope,
    check_representable,
    decision_bar_is_closed,
)
from .gate import AuthorityGate, capital_release, trade_valid
from .lifecycle import Lifecycle
from .reconciliation import ReconciliationEngine
from .supervisor import SafetyController

LEDGER_FILE = "intent_ledger.json"
LEDGER_SCHEMA_VERSION = 1

DEFAULT_MAX_PREFLIGHT_AGE_SECONDS = 120.0

TERMINAL_STATES = frozenset(state.value for state in ExecutionState if state.terminal)


def _frozen_bar_semantics() -> Tuple[str, float]:
    """Read the approved bar interval and close lag from the frozen core (read-only)."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        raw = json.loads(path.read_text())
        return str(raw["bar_interval"]), float(raw["truth"]["bar_close_lag_seconds"])
    except Exception as exc:  # pragma: no cover - unreadable config must not read as permissive
        raise LedgerError("cannot read frozen bar semantics; refusing to assume a bar interval") from exc


class SubmissionOutcome(str, Enum):
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    PREFLIGHT_NOT_BOUND = "PREFLIGHT_NOT_BOUND"
    INTENT_EXPIRED = "INTENT_EXPIRED"
    DECISION_BAR_NOT_CLOSED = "DECISION_BAR_NOT_CLOSED"
    REFUSED_BY_SCOPE = "REFUSED_BY_SCOPE"
    REFUSED_BY_CAPABILITY = "REFUSED_BY_CAPABILITY"
    ORDER_INCOMPATIBLE = "ORDER_INCOMPATIBLE"
    SUPERVISOR_BLOCKED_NEW_EXPOSURE = "SUPERVISOR_BLOCKED_NEW_EXPOSURE"
    BROKER_STATE_CHANGED = "BROKER_STATE_CHANGED"
    REFUSED_BY_AUTHORITY = "REFUSED_BY_AUTHORITY"
    LIVE_LOCKED_REFUSAL = "LIVE_LOCKED_REFUSAL"
    TRANSMITTED = "TRANSMITTED"
    UNKNOWN_PENDING_RECONCILIATION = "UNKNOWN_PENDING_RECONCILIATION"


def client_order_id_for(idempotency_key: str) -> str:
    """Deterministic client order id so broker evidence can be matched back to an intent."""
    return "trips-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class SubmissionResult:
    outcome: str
    state: str
    intent_id: str
    idempotency_key: str
    client_order_id: str
    transmitted: bool
    reasons: Tuple[str, ...] = ()
    gate: Mapping[str, Any] | None = None
    boundary: Mapping[str, Any] | None = None
    reconciliation: Mapping[str, Any] | None = None
    preflight_hash: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "state": self.state,
            "intent_id": self.intent_id,
            "idempotency_key": self.idempotency_key,
            "client_order_id": self.client_order_id,
            "transmitted": self.transmitted,
            "reasons": list(self.reasons),
            "gate": self.gate,
            "boundary": self.boundary,
            "reconciliation": self.reconciliation,
            "preflight_hash": self.preflight_hash,
        }


def _as_dict(value: Any) -> Any:
    return value.to_dict() if hasattr(value, "to_dict") else value


class IdempotencyLedger:
    """Durable at-most-once submission ledger.

    Entries are never deleted, so a replayed intent is always detectable. A refusal also
    consumes its idempotency key: retrying requires a fresh intent, which re-runs preflight.
    """

    def __init__(self, store=durable_store) -> None:
        self._store = store

    def _load(self) -> Dict[str, Any]:
        data = self._store.read_json(LEDGER_FILE, None, strict=True)
        if data is None:
            return {"schema_version": LEDGER_SCHEMA_VERSION, "entries": {}}
        if not isinstance(data, dict) or data.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerError("unsupported execution ledger schema")
        if not isinstance(data.get("entries"), dict):
            raise LedgerError("execution ledger entries must be an object")
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self._store.write_json(LEDGER_FILE, data)

    def entry(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        return self._load()["entries"].get(idempotency_key)

    def all_entries(self) -> Dict[str, Any]:
        return dict(self._load()["entries"])

    def open_client_order_ids(self) -> Tuple[str, ...]:
        return tuple(
            entry["client_order_id"] for entry in self._load()["entries"].values()
            if entry.get("state") not in TERMINAL_STATES
        )

    def unknown_pending(self) -> Tuple[str, ...]:
        return tuple(
            key for key, entry in self._load()["entries"].items()
            if entry.get("state") == ExecutionState.UNKNOWN_PENDING_RECONCILIATION.value
        )

    def _advance(self, key: str, *, create_from: Any, state: ExecutionState, outcome: str,
                 reasons: Sequence[str] = (), extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        data = self._load()
        entry = data["entries"].get(key)
        if entry is None:
            if create_from is None:
                raise LedgerError(f"cannot create ledger entry for unknown key {key}")
            entry = {
                "intent_id": create_from.intent_id,
                "idempotency_key": key,
                "client_order_id": client_order_id_for(key),
                "broker_order_id": None,
                "state": ExecutionState.READY.value,
                "outcome": None,
                "attempts": 0,
                "broker_id": None,
                "account_id": None,
                "correlation_id": create_from.correlation_id,
                "intent_content_hash": create_from.content_hash(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
                "history": [],
            }
        previous = ExecutionState(entry["state"])
        if previous is not state:
            assert_transition_allowed(previous, state)
        stamp = datetime.now(timezone.utc).isoformat()
        entry["state"] = state.value
        entry["outcome"] = outcome
        entry["updated_at"] = stamp
        entry.setdefault("history", []).append(
            {"state": state.value, "outcome": outcome, "reasons": list(reasons), "at": stamp})
        for field_name, value in dict(extra or {}).items():
            entry[field_name] = value
        data["entries"][key] = entry
        self._save(data)
        return entry

    def record(self, intent: Any, *, state: ExecutionState, outcome: str,
               reasons: Sequence[str] = (), **extra: Any) -> Dict[str, Any]:
        return self._advance(intent.idempotency_key, create_from=intent, state=state, outcome=outcome,
                             reasons=reasons, extra=extra)

    def reserve(self, intent: Any, *, broker_id: Optional[str], account_id: Optional[str]) -> Dict[str, Any]:
        """Atomic submission reservation taken before any transmission is attempted."""
        self._advance(intent.idempotency_key, create_from=intent,
                      state=ExecutionState.SUBMISSION_RESERVED, outcome="SUBMISSION_RESERVED",
                      extra={"broker_id": broker_id, "account_id": account_id})
        data = self._load()
        data["entries"][intent.idempotency_key]["attempts"] = \
            int(data["entries"][intent.idempotency_key].get("attempts", 0)) + 1
        self._save(data)
        return data["entries"][intent.idempotency_key]

    def advance_from_evidence(self, idempotency_key: str, *, state: ExecutionState,
                              outcome: str, extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        return self._advance(idempotency_key, create_from=None, state=state, outcome=outcome,
                             extra=extra)


class UniversalBrokerGateway:
    def __init__(self, *, adapter: Any, governor: CapitalGovernor, lifecycle: Lifecycle,
                 ledger: Optional[IdempotencyLedger] = None,
                 gate: Optional[AuthorityGate] = None,
                 reconciliation: Optional[ReconciliationEngine] = None,
                 safety: Optional[SafetyController] = None,
                 registry: Any = None,
                 bar_interval: Optional[str] = None,
                 bar_close_lag_seconds: Optional[float] = None) -> None:
        self._adapter = adapter
        self._governor = governor
        self._lifecycle = lifecycle
        self._ledger = ledger or IdempotencyLedger()
        self._gate = gate or AuthorityGate()
        self._reconciliation = reconciliation or ReconciliationEngine()
        self._safety = safety or SafetyController()
        self._registry = registry
        frozen_interval, frozen_lag = _frozen_bar_semantics() if bar_interval is None or bar_close_lag_seconds is None else (bar_interval, bar_close_lag_seconds)
        self._bar_interval = str(frozen_interval)
        self._bar_close_lag_seconds = float(frozen_lag)

    @property
    def ledger(self) -> IdempotencyLedger:
        return self._ledger

    @property
    def governor(self) -> CapitalGovernor:
        return self._governor

    @property
    def lifecycle(self) -> Lifecycle:
        return self._lifecycle

    @property
    def registry(self) -> Any:
        return self._registry

    # -- capability / automation support ---------------------------------

    def automation_support(self) -> Dict[str, Any]:
        broker_id = getattr(self._adapter, "broker_id", "unknown")
        if self._registry is not None and self._registry.get(broker_id) is not None:
            report = self._registry.conformance_report(broker_id)
            verdict = report["verdict"]
            return {
                "broker_id": broker_id,
                "capabilities": report["capabilities"],
                "registration": report["registration"],
                "missing_core": verdict.get("missing_core", []),
                "automation_supported": bool(verdict["permitted"]),
                "code": verdict["code"],
                "reasons": verdict["reasons"],
                "note": ("A broker is automatable only when an authorized programmable interface exists. "
                         "Screen-coordinate automation, DOM clicking, CAPTCHA/MFA bypass, stolen sessions, "
                         "undocumented or reverse-engineered endpoints and credential replay are never used."),
            }
        matrix = self._adapter.capability_matrix()
        missing = matrix.missing_core()
        return {
            "broker_id": broker_id,
            "capabilities": matrix.to_dict(),
            "missing_core": missing,
            "automation_supported": not missing,
            "code": "SUPPORTED" if not missing else "BROKER_AUTOMATION_UNSUPPORTED",
            "reasons": [] if not missing else [f"missing core capabilities {missing}"],
            "note": "No authorized registry entry: capability declaration only.",
        }

    # -- scope defense in depth ------------------------------------------

    def _scope_violation(self, intent: Any, holdings: Mapping[str, int],
                         authorized_quantity: Optional[int]) -> Optional[str]:
        if intent.symbol not in approved_symbol_scope():
            return f"symbol {intent.symbol} is outside the approved mandate"
        if intent.side == "SELL":
            held = int(holdings.get(intent.symbol, 0))
            if held <= 0:
                return f"SELL of {intent.symbol} with no broker-confirmed holding would create a short"
            if intent.quantity > held:
                return (f"SELL quantity {intent.quantity} exceeds broker-confirmed holdings "
                        f"{held} for {intent.symbol}")
        if authorized_quantity is not None and intent.quantity > authorized_quantity:
            return (f"quantity {intent.quantity} exceeds the frozen risk engine's authorized size "
                    f"{authorized_quantity}")
        return None

    # -- broker observables re-check --------------------------------------

    def _broker_observables(self, portfolio: PortfolioSnapshot,
                            expected_account_id: Optional[str]) -> Dict[str, Any]:
        try:
            health = self._adapter.health()
            account = self._adapter.account()
            positions = list(self._adapter.positions())
            open_orders = list(self._adapter.open_orders())
        except Exception as exc:
            return {"ok": False, "reason": f"broker evidence unavailable: {type(exc).__name__}"}
        recon = self._reconciliation.reconcile(
            local_positions=dict(portfolio.quantities),
            broker_positions=positions,
            broker_open_client_order_ids=[str(o.get("client_order_id")) for o in open_orders
                                          if isinstance(o, Mapping)],
            pending_intent_client_order_ids=self._ledger.open_client_order_ids(),
            expected_account_id=expected_account_id,
            broker_account_id=getattr(account, "account_id", None),
        )
        return {"ok": True, "healthy": bool(health.healthy),
                "account_id": getattr(account, "account_id", None),
                "environment": getattr(account, "environment", None), "reconciliation": recon,
                "reconciliation_ok": recon.clean, "open_orders": len(open_orders)}

    # -- submission -------------------------------------------------------

    def submit(self, intent: Any, *, preflight_report: Any, portfolio: PortfolioSnapshot,
               holdings: Mapping[str, int] | None = None,
               expected_account_id: Optional[str] = None,
               expected_environment: Optional[str] = None,
               now: datetime | None = None,
               max_preflight_age_seconds: float = DEFAULT_MAX_PREFLIGHT_AGE_SECONDS) -> SubmissionResult:
        now = now or datetime.now(timezone.utc)
        holdings = dict(holdings or {})
        cid = client_order_id_for(intent.idempotency_key)
        preflight_hash = getattr(preflight_report, "content_hash", lambda: None)()
        authorized_quantity = int((preflight_report.artifacts or {}).get("authorized_quantity", 0))

        def result(outcome: SubmissionOutcome, state: ExecutionState, *, reasons: Sequence[str] = (),
                   transmitted: bool = False, gate: Any = None, boundary: Any = None,
                   reconciliation: Any = None, persist: bool = True) -> SubmissionResult:
            if persist:
                self._ledger.record(intent, state=state, outcome=outcome.value, reasons=reasons,
                                    preflight_hash=preflight_hash)
            return SubmissionResult(
                outcome=outcome.value, state=state.value, intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key, client_order_id=cid, transmitted=transmitted,
                reasons=tuple(reasons), gate=_as_dict(gate), boundary=_as_dict(boundary),
                reconciliation=_as_dict(reconciliation), preflight_hash=preflight_hash,
            )

        # 0. Durable idempotency. An existing key is never transmitted again.
        existing = self._ledger.entry(intent.idempotency_key)
        if existing is not None:
            return result(SubmissionOutcome.DUPLICATE_SUPPRESSED, ExecutionState(existing["state"]),
                          reasons=(f"idempotency key already recorded in state {existing['state']}; "
                                   "a retry requires a fresh intent"),
                          persist=False)

        # 1. The authority report must describe THIS intent and be recent.
        bound, binding_reasons = preflight_report.binding_matches(
            intent, now=now, max_age_seconds=max_preflight_age_seconds)
        if not bound:
            return result(SubmissionOutcome.PREFLIGHT_NOT_BOUND, ExecutionState.REFUSED,
                          reasons=binding_reasons)

        # 2. Intent freshness.
        try:
            intent.require_fresh(now)
        except IntentExpired as exc:
            return result(SubmissionOutcome.INTENT_EXPIRED, ExecutionState.EXPIRED, reasons=(str(exc),))

        # 3. Execution-layer scope and size defense in depth.
        violation = self._scope_violation(intent, holdings, authorized_quantity or None)
        if violation:
            return result(SubmissionOutcome.REFUSED_BY_SCOPE, ExecutionState.REFUSED, reasons=(violation,))

        # 4. Capability contract. UNVERIFIED fails closed.
        missing = self._adapter.capability_matrix().missing_core()
        if missing:
            return result(SubmissionOutcome.REFUSED_BY_CAPABILITY, ExecutionState.REFUSED,
                          reasons=(f"{CAPABILITY_UNSUPPORTED}: missing core capabilities {missing}",))

        # 4b. Decision-bar closure, re-proven at the moment of transmission. A signal derived from
        #     an incomplete bar may never be sent, including after an outage or restart.
        try:
            bar_ok, bar_detail = decision_bar_is_closed(
                intent, interval=self._bar_interval, now=now,
                close_lag_seconds=self._bar_close_lag_seconds)
        except Exception as exc:
            return result(SubmissionOutcome.DECISION_BAR_NOT_CLOSED, ExecutionState.REFUSED,
                          reasons=(f"decision-bar closure cannot be proven: {type(exc).__name__}",))
        if not bar_ok:
            return result(SubmissionOutcome.DECISION_BAR_NOT_CLOSED, ExecutionState.REFUSED,
                          reasons=(bar_detail,))

        # 4c. The approved intent must be representable by THIS broker, unchanged. If it is not,
        #     the answer is a code and no trade - never a substituted order.
        representability = check_representable(intent, self._adapter.order_capabilities())
        if not representability["permitted"]:
            outcome = (SubmissionOutcome.ORDER_INCOMPATIBLE
                       if representability["code"] == ORDER_INCOMPATIBLE
                       else SubmissionOutcome.REFUSED_BY_CAPABILITY)
            return result(outcome, ExecutionState.REFUSED,
                          reasons=tuple(representability["reasons"]))

        # 5. Independently re-verify broker-observable facts; they can change after preflight.
        observables = self._broker_observables(portfolio, expected_account_id)
        if not observables["ok"]:
            return result(SubmissionOutcome.REFUSED_BY_CAPABILITY, ExecutionState.REFUSED,
                          reasons=(observables["reason"],))
        release_map = dict(preflight_report.capital_release)
        changed = []
        if bool(release_map.get("broker_healthy")) and not observables["healthy"]:
            changed.append("broker health regressed after preflight")
        if bool(release_map.get("correct_account")) and observables["account_id"] != expected_account_id:
            changed.append("broker account changed after preflight")
        if bool(release_map.get("broker_state_reconciled")) and not observables["reconciliation_ok"]:
            changed.append("reconciliation was clean at preflight but is not clean now")
        if changed:
            return result(SubmissionOutcome.BROKER_STATE_CHANGED, ExecutionState.REFUSED,
                          reasons=tuple(changed), reconciliation=observables["reconciliation"])

        # 5b. A supervisor halt raised AFTER preflight still blocks new exposure, and it can only
        #     ever tighten. The supervisor never gains the power to release anything.
        if self._safety.halt_requested:
            return result(SubmissionOutcome.SUPERVISOR_BLOCKED_NEW_EXPOSURE, ExecutionState.REFUSED,
                          reasons=("a supervisor halt request is active; new exposure is blocked",),
                          reconciliation=observables["reconciliation"])

        # 6. Deterministic authority aggregation over the computed permissions.
        decision = self._gate.evaluate(trade=trade_valid(dict(preflight_report.trade_valid)),
                                       capital=capital_release(release_map))
        if not decision.authorized:
            return result(SubmissionOutcome.REFUSED_BY_AUTHORITY, ExecutionState.REFUSED,
                          reasons=tuple(decision.reasons), gate=decision,
                          reconciliation=observables["reconciliation"])

        # 7. THE FROZEN LIVE BOUNDARY. Authorized is not released.
        boundary = self._lifecycle.may_transmit_live()
        if not boundary["permitted"]:
            return result(SubmissionOutcome.LIVE_LOCKED_REFUSAL, ExecutionState.REFUSED,
                          reasons=(boundary["code"],) + tuple(boundary["boundary"]["reasons"]),
                          gate=decision, boundary=boundary["boundary"],
                          reconciliation=observables["reconciliation"])

        # 8. Translation BEFORE reservation: a broker that cannot represent the approved intent
        #    must not consume a submission reservation. Then reserve atomically, then transmit.
        try:
            representation = self._adapter.represent_intent(intent)
        except Exception as exc:
            return result(SubmissionOutcome.REFUSED_BY_CAPABILITY, ExecutionState.REFUSED,
                          reasons=(f"{CAPABILITY_UNSUPPORTED}: adapter could not represent the "
                                   f"approved intent ({type(exc).__name__})",),
                          gate=decision, boundary=boundary["boundary"])
        try:
            assert_preserves_economic_meaning(intent, representation)
        except CapabilityError as exc:
            return result(SubmissionOutcome.REFUSED_BY_CAPABILITY, ExecutionState.REFUSED,
                          reasons=(str(exc),), gate=decision, boundary=boundary["boundary"])

        self._ledger.reserve(intent, broker_id=getattr(self._adapter, "broker_id", None),
                             account_id=observables["account_id"])
        self._ledger.record(intent, state=ExecutionState.SUBMITTING, outcome="SUBMITTING")
        try:
            ack = self._adapter.submit_order(client_order_id=cid, representation=representation)
        except Exception as exc:
            # A lost response is NEVER a reason to resubmit. Resolve from broker evidence.
            self._ledger.record(intent, state=ExecutionState.UNKNOWN_PENDING_RECONCILIATION,
                                outcome=SubmissionOutcome.UNKNOWN_PENDING_RECONCILIATION.value,
                                reasons=[f"submission outcome unknown: {type(exc).__name__}"])
            return result(SubmissionOutcome.UNKNOWN_PENDING_RECONCILIATION,
                          ExecutionState.UNKNOWN_PENDING_RECONCILIATION,
                          reasons=("response lost; reconciliation required before any further submission",),
                          gate=decision, boundary=boundary["boundary"], persist=False)

        broker_order_id = ack.get("broker_order_id") if isinstance(ack, Mapping) else None
        self._ledger.record(intent, state=ExecutionState.BROKER_ACKNOWLEDGED, outcome="TRANSMITTED",
                            broker_order_id=broker_order_id)
        return result(SubmissionOutcome.TRANSMITTED, ExecutionState.BROKER_ACKNOWLEDGED,
                      transmitted=True, gate=decision, boundary=boundary["boundary"], persist=False)

    # -- uncertainty resolution ------------------------------------------

    def resolve_unknown(self, idempotency_key: str, *,
                        broker_orders: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        """Resolve an unknown submission from broker evidence. Never blind-resubmit."""
        entry = self._ledger.entry(idempotency_key)
        if entry is None:
            raise LedgerError("unknown submission is not in the ledger")
        if entry["state"] not in {ExecutionState.UNKNOWN_PENDING_RECONCILIATION.value,
                                  ExecutionState.SUBMITTING.value}:
            return {"resolved": False, "code": "NOT_IN_UNKNOWN_STATE", "state": entry["state"]}
        match = next((o for o in broker_orders
                      if str(o.get("client_order_id")) == entry["client_order_id"]), None)
        if match is None:
            return {
                "resolved": False,
                "code": "UNRESOLVED_PENDING_RECONCILIATION",
                "state": entry["state"],
                "note": ("Broker evidence does not yet prove acceptance or rejection. Absence of a match "
                         "is not proof of non-acceptance while the broker view may be incomplete. "
                         "No further submission is permitted."),
            }
        state = ExecutionState(str(match.get("state", "BROKER_ACKNOWLEDGED")))
        updated = self._ledger.advance_from_evidence(
            idempotency_key, state=state, outcome="RESOLVED_FROM_BROKER_EVIDENCE",
            extra={"broker_order_id": match.get("broker_order_id")})
        return {"resolved": True, "code": "RESOLVED_FROM_BROKER_EVIDENCE", "state": updated["state"]}

    # -- cancellation -----------------------------------------------------

    def request_cancel(self, *, broker_order_id: str, reason: str, actor: str,
                       policy: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        """Cancellation follows approved policy. A safety halt blocks NEW exposure; it does not
        silently acquire the authority to cancel arbitrary working orders."""
        policy = dict(policy or {})
        if actor == "SAFETY_CONTROLLER" and not policy.get("allow_autonomous_cancel_all", False):
            return {"cancelled": False, "code": "CANCELLATION_NOT_APPROVED_BY_POLICY", "actor": actor,
                    "broker_order_id": broker_order_id, "reason": reason}
        try:
            ack = self._adapter.cancel_order(broker_order_id=broker_order_id, reason=reason)
        except Exception as exc:
            return {"cancelled": False, "code": "CANCEL_OUTCOME_UNKNOWN", "actor": actor,
                    "broker_order_id": broker_order_id, "error_type": type(exc).__name__}
        return {"cancelled": True, "code": "CANCEL_ACKNOWLEDGED", "actor": actor,
                "broker_order_id": broker_order_id, "reason": reason, "ack": dict(ack)}

    # -- health / observability ------------------------------------------

    def health(self) -> Dict[str, Any]:
        support = self.automation_support()
        try:
            health = self._adapter.health()
            account = self._adapter.account()
            account_view = {"account_id": getattr(account, "account_id", None),
                            "environment": getattr(account, "environment", None)}
        except Exception as exc:
            return {**support, "broker_health": {"healthy": False, "error_type": type(exc).__name__},
                    "code": "BROKER_UNREACHABLE"}
        return {
            **support,
            "broker_health": {"connected": health.connected, "authenticated": health.authenticated,
                              "clock_skew_seconds": health.clock_skew_seconds, "healthy": health.healthy},
            "account": account_view,
            "unknown_pending_reconciliation": list(self._ledger.unknown_pending()),
            "governor": self._governor.describe(),
            "supervisor_halt_requested": self._safety.halt_requested,
            "code": "OK" if health.healthy else "BROKER_UNHEALTHY",
        }
