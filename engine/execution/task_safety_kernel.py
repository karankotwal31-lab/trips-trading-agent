"""TASK — Trip's Autonomous Safety Kernel.

TASK is an execution-safety envelope, not trading intelligence.

The autonomous strategy may propose any trade that the frozen strategy/risk stack permits. TASK
may only (a) preserve the proposal, (b) reduce its quantity, or (c) block it because execution
conditions are unsafe or unverified. It can never increase quantity, alter side, switch symbols,
change strategy logic, or grant capital authority.

This keeps Trip's autonomous without making safety advisory. Financial authority remains owned by
the existing frozen Risk engine and CapitalGovernor. TASK covers execution/market-conduct
safeguards that should live outside the learning loop: market-data reasonability, liquidity
participation, price tolerance, message/repeated-execution throttles, self-match prevention,
disconnect safeguards, venue-compliance evidence, and derivative contract lifecycle.

No numeric safety thresholds are invented here. Every threshold is supplied by an explicit,
hash-bound owner/venue policy.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .contracts import canonical_json

APPROVED = "APPROVED"
APPROVED_REDUCED = "APPROVED_REDUCED"
BLOCKED = "BLOCKED"

_CASH = "CASH"
_FUTURE = "FUTURE"
_OPTION = "OPTION"
_ALLOWED_INSTRUMENT_KINDS = frozenset({_CASH, _FUTURE, _OPTION})
_ALLOWED_SETTLEMENT = frozenset({"NONE", "CASH", "PHYSICAL"})


class TASKError(RuntimeError):
    """TASK policy or execution-context failure."""


def _finite_number(value: Any, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TASKError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        relation = "non-negative" if allow_zero else "positive"
        raise TASKError(f"{name} must be finite and {relation}")
    return result


def _whole(value: Any, name: str, *, allow_zero: bool = False) -> int:
    number = _finite_number(value, name, allow_zero=allow_zero)
    if number != int(number):
        raise TASKError(f"{name} must be a whole number")
    return int(number)


def _aware(value: str, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TASKError(f"{name} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise TASKError(f"{name} is not parseable ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TASKError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _day(value: str, name: str) -> date:
    if not isinstance(value, str) or not value:
        raise TASKError(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except Exception as exc:
        raise TASKError(f"{name} is not parseable YYYY-MM-DD") from exc


@dataclass(frozen=True)
class TASKPolicy:
    """Owner/venue-approved execution envelope.

    Values are intentionally required. There are no "reasonable defaults" for financial-market
    safety parameters because silently choosing them would grant this program authority the owner
    did not grant.
    """

    policy_version: str
    max_price_deviation_bps: float
    max_spread_bps: float
    max_adv_participation_pct: float
    max_messages_per_second: int
    max_repeated_executions: int
    repeated_execution_window_seconds: int
    first_notice_buffer_days: int
    last_trade_buffer_days: int
    require_cancel_on_disconnect: bool
    require_self_match_prevention: bool
    allow_unbounded_market_orders: bool
    permitted_order_types: Tuple[str, ...]
    allowed_venue_states: Tuple[str, ...]
    required_compliance_checks: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise TASKError("policy_version is required")
        _finite_number(self.max_price_deviation_bps, "max_price_deviation_bps")
        _finite_number(self.max_spread_bps, "max_spread_bps")
        participation = _finite_number(self.max_adv_participation_pct, "max_adv_participation_pct")
        if participation > 1:
            raise TASKError("max_adv_participation_pct must be expressed as a fraction <= 1")
        _whole(self.max_messages_per_second, "max_messages_per_second")
        _whole(self.max_repeated_executions, "max_repeated_executions")
        _whole(self.repeated_execution_window_seconds, "repeated_execution_window_seconds")
        _whole(self.first_notice_buffer_days, "first_notice_buffer_days", allow_zero=True)
        _whole(self.last_trade_buffer_days, "last_trade_buffer_days", allow_zero=True)

        if not isinstance(self.require_cancel_on_disconnect, bool):
            raise TASKError("require_cancel_on_disconnect must be boolean")
        if not isinstance(self.require_self_match_prevention, bool):
            raise TASKError("require_self_match_prevention must be boolean")
        if not isinstance(self.allow_unbounded_market_orders, bool):
            raise TASKError("allow_unbounded_market_orders must be boolean")

        if not self.permitted_order_types:
            raise TASKError("permitted_order_types cannot be empty")
        invalid_types = sorted(set(self.permitted_order_types) - {"MARKET", "LIMIT"})
        if invalid_types:
            raise TASKError(f"unsupported permitted_order_types: {invalid_types}")
        if not self.allowed_venue_states:
            raise TASKError("allowed_venue_states cannot be empty")
        if any(not isinstance(item, str) or not item.strip() for item in self.allowed_venue_states):
            raise TASKError("allowed_venue_states must contain non-empty strings")
        if any(not isinstance(item, str) or not item.strip() for item in self.required_compliance_checks):
            raise TASKError("required_compliance_checks must contain non-empty strings")

    def to_dict(self) -> Dict[str, Any]:
        body = asdict(self)
        for name in ("permitted_order_types", "allowed_venue_states", "required_compliance_checks"):
            body[name] = list(body[name])
        return body


def fingerprint_policy(policy: TASKPolicy | Mapping[str, Any]) -> str:
    body = policy.to_dict() if isinstance(policy, TASKPolicy) else dict(policy)
    return hashlib.sha256(canonical_json(body)).hexdigest()


@dataclass(frozen=True)
class TradeProposal:
    """Flexible strategy output before the immutable broker-neutral intent is minted."""

    symbol: str
    side: str
    desired_quantity: int
    order_type: str
    limit_price: Optional[float]
    reference_price: float
    strategy_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol or self.symbol != self.symbol.strip().upper():
            raise TASKError("proposal symbol must be a canonical uppercase token")
        if self.side not in {"BUY", "SELL"}:
            raise TASKError("proposal side must be BUY or SELL")
        if isinstance(self.desired_quantity, bool) or not isinstance(self.desired_quantity, int) or self.desired_quantity <= 0:
            raise TASKError("desired_quantity must be a positive integer")
        if self.order_type not in {"MARKET", "LIMIT"}:
            raise TASKError("order_type must be MARKET or LIMIT")
        _finite_number(self.reference_price, "reference_price")
        if self.order_type == "LIMIT":
            _finite_number(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise TASKError("MARKET proposal must not carry limit_price")
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise TASKError("strategy_id is required")


@dataclass(frozen=True)
class ContractLifecycleEvidence:
    """Exchange-sourced lifecycle facts for one derivative contract."""

    settlement_type: str
    last_trade_date: str
    first_notice_date: Optional[str] = None

    def __post_init__(self) -> None:
        if self.settlement_type not in _ALLOWED_SETTLEMENT:
            raise TASKError(f"settlement_type must be one of {sorted(_ALLOWED_SETTLEMENT)}")
        _day(self.last_trade_date, "last_trade_date")
        if self.first_notice_date is not None:
            _day(self.first_notice_date, "first_notice_date")


@dataclass(frozen=True)
class TASKContext:
    """Facts known at the execution boundary.

    Recent message/execution timestamps are supplied by the execution journal, not by the strategy.
    Compliance flags are evidence results (for example static-IP, strong-auth, algo-tag or exchange
    conformance checks) whose exact required set is defined by the owner/venue policy.
    """

    market_data_healthy: bool
    venue_state: str
    observed_price: float
    spread_bps: float
    average_daily_volume: float
    broker_connected: bool
    cancel_on_disconnect_active: bool
    instrument_kind: str
    working_orders: Tuple[Mapping[str, Any], ...] = ()
    recent_message_times: Tuple[str, ...] = ()
    recent_execution_times: Tuple[str, ...] = ()
    compliance: Mapping[str, bool] = None  # type: ignore[assignment]
    contract_lifecycle: Optional[ContractLifecycleEvidence] = None

    def __post_init__(self) -> None:
        if not isinstance(self.market_data_healthy, bool):
            raise TASKError("market_data_healthy must be boolean")
        if not isinstance(self.venue_state, str) or not self.venue_state.strip():
            raise TASKError("venue_state is required")
        _finite_number(self.observed_price, "observed_price")
        _finite_number(self.spread_bps, "spread_bps", allow_zero=True)
        _finite_number(self.average_daily_volume, "average_daily_volume")
        if not isinstance(self.broker_connected, bool):
            raise TASKError("broker_connected must be boolean")
        if not isinstance(self.cancel_on_disconnect_active, bool):
            raise TASKError("cancel_on_disconnect_active must be boolean")
        if self.instrument_kind not in _ALLOWED_INSTRUMENT_KINDS:
            raise TASKError(f"instrument_kind must be one of {sorted(_ALLOWED_INSTRUMENT_KINDS)}")
        object.__setattr__(self, "compliance", dict(self.compliance or {}))
        for stamp in (*self.recent_message_times, *self.recent_execution_times):
            _aware(stamp, "recent event timestamp")


@dataclass(frozen=True)
class TASKDecision:
    status: str
    approved_quantity: int
    desired_quantity: int
    blocks: Tuple[str, ...]
    advisories: Tuple[str, ...]
    constraints: Mapping[str, Any]
    policy_version: str
    policy_hash: str

    @property
    def allowed(self) -> bool:
        return self.status in {APPROVED, APPROVED_REDUCED} and self.approved_quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "allowed": self.allowed,
            "approved_quantity": self.approved_quantity,
            "desired_quantity": self.desired_quantity,
            "blocks": list(self.blocks),
            "advisories": list(self.advisories),
            "constraints": dict(self.constraints),
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
        }


class TASKKernel:
    """Deterministic safety envelope. It has no authority to make a trade larger or riskier."""

    def __init__(self, policy: TASKPolicy, *, approved_policy_hash: str) -> None:
        if not isinstance(policy, TASKPolicy):
            raise TASKError("policy must be a TASKPolicy")
        if not isinstance(approved_policy_hash, str) or len(approved_policy_hash) != 64:
            raise TASKError("approved_policy_hash must be a SHA-256 hex digest")
        actual = fingerprint_policy(policy)
        if actual != approved_policy_hash:
            raise TASKError("TASK policy does not match the approved fingerprint")
        self._policy = policy
        self._policy_hash = actual

    @property
    def policy(self) -> TASKPolicy:
        return self._policy

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    def risk_budget(self, proposal: TradeProposal, context: TASKContext,
                    *, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Read-only budget the autonomous brain may query before proposing an order.

        This is deliberately advisory to strategy logic but binding at execution: the same kernel
        recomputes the result during evaluate. It never exposes a quantity above what the
        upstream Risk engine already authorized as desired_quantity.
        """
        decision = self.evaluate(proposal, context=context, now=now)
        return {
            "symbol": proposal.symbol,
            "desired_quantity": proposal.desired_quantity,
            "max_executable_quantity": decision.approved_quantity if decision.allowed else 0,
            "status": decision.status,
            "binding_constraints": list(decision.blocks) if decision.blocks else
                                   list(decision.constraints.get("binding", ())),
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
        }

    def evaluate(self, proposal: TradeProposal, *, context: TASKContext,
                 now: Optional[datetime] = None) -> TASKDecision:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        p = self._policy
        blocks = []
        advisories = []
        constraints: Dict[str, Any] = {"binding": []}

        if not context.market_data_healthy:
            blocks.append("MARKET_DATA_UNHEALTHY")
        if context.venue_state not in p.allowed_venue_states:
            blocks.append("VENUE_STATE_NOT_ALLOWED")
        if not context.broker_connected:
            blocks.append("BROKER_DISCONNECTED")
        if p.require_cancel_on_disconnect and not context.cancel_on_disconnect_active:
            blocks.append("CANCEL_ON_DISCONNECT_UNVERIFIED")
        if proposal.order_type not in p.permitted_order_types:
            blocks.append("ORDER_TYPE_NOT_PERMITTED")

        if context.spread_bps > p.max_spread_bps:
            blocks.append("SPREAD_TOO_WIDE")

        if proposal.order_type == "LIMIT":
            deviation = abs(float(proposal.limit_price) - float(context.observed_price)) / float(context.observed_price) * 10000.0
            constraints["price_deviation_bps"] = deviation
            if deviation > p.max_price_deviation_bps:
                blocks.append("PRICE_TOLERANCE_EXCEEDED")
        elif not p.allow_unbounded_market_orders:
            blocks.append("UNBOUNDED_MARKET_ORDER_NOT_PERMITTED")
        else:
            advisories.append("MARKET_ORDER_HAS_NO_LIMIT_PRICE")

        missing_compliance = [
            check for check in p.required_compliance_checks
            if context.compliance.get(check) is not True
        ]
        if missing_compliance:
            blocks.append("VENUE_COMPLIANCE_UNVERIFIED")
            constraints["missing_compliance_checks"] = missing_compliance

        recent_messages = self._recent_count(
            context.recent_message_times, now=now, seconds=1.0)
        constraints["messages_last_second"] = recent_messages
        if recent_messages >= p.max_messages_per_second:
            blocks.append("MESSAGE_RATE_LIMIT")

        repeats = self._recent_count(
            context.recent_execution_times, now=now,
            seconds=float(p.repeated_execution_window_seconds))
        constraints["repeated_executions_in_window"] = repeats
        if repeats >= p.max_repeated_executions:
            blocks.append("REPEATED_EXECUTION_LIMIT")

        if p.require_self_match_prevention and self._would_self_match(proposal, context.working_orders):
            blocks.append("SELF_MATCH_RISK")

        lifecycle_blocks = self._lifecycle_blocks(context, now=now)
        blocks.extend(lifecycle_blocks)

        adv_cap = int(math.floor(float(context.average_daily_volume) * p.max_adv_participation_pct))
        constraints["adv_cap_quantity"] = adv_cap
        approved = min(int(proposal.desired_quantity), max(0, adv_cap))
        if approved < proposal.desired_quantity:
            constraints["binding"].append("ADV_PARTICIPATION_CAP")
        if approved <= 0:
            blocks.append("NO_LIQUIDITY_BUDGET")

        # Absolute invariant: TASK may never increase upstream authority.
        approved = min(approved, int(proposal.desired_quantity))
        if blocks:
            approved = 0
            status = BLOCKED
        elif approved < proposal.desired_quantity:
            status = APPROVED_REDUCED
        else:
            status = APPROVED

        return TASKDecision(
            status=status, approved_quantity=approved,
            desired_quantity=int(proposal.desired_quantity),
            blocks=tuple(dict.fromkeys(blocks)),
            advisories=tuple(advisories),
            constraints=constraints,
            policy_version=p.policy_version,
            policy_hash=self._policy_hash,
        )

    @staticmethod
    def _recent_count(values: Sequence[str], *, now: datetime, seconds: float) -> int:
        count = 0
        for value in values:
            occurred = _aware(value, "recent event timestamp")
            age = (now - occurred).total_seconds()
            if 0 <= age <= seconds:
                count += 1
        return count

    @staticmethod
    def _would_self_match(proposal: TradeProposal,
                          working_orders: Sequence[Mapping[str, Any]]) -> bool:
        opposite = "SELL" if proposal.side == "BUY" else "BUY"
        for order in working_orders:
            if str(order.get("symbol") or "").upper() != proposal.symbol:
                continue
            if str(order.get("side") or "").upper() != opposite:
                continue
            state = str(order.get("state") or "OPEN").upper()
            if state not in {"CANCELLED", "FILLED", "REJECTED", "EXPIRED"}:
                return True
        return False

    def _lifecycle_blocks(self, context: TASKContext, *, now: datetime) -> list[str]:
        if context.instrument_kind == _CASH:
            return []
        lifecycle = context.contract_lifecycle
        if lifecycle is None:
            return ["CONTRACT_LIFECYCLE_UNVERIFIED"]

        p = self._policy
        today = now.date()
        last_trade = _day(lifecycle.last_trade_date, "last_trade_date")
        last_safe = last_trade - timedelta(days=p.last_trade_buffer_days)
        if today >= last_safe:
            return ["LAST_TRADE_CUTOFF_REACHED"]

        if lifecycle.settlement_type == "PHYSICAL":
            if not lifecycle.first_notice_date:
                return ["FIRST_NOTICE_DATE_UNVERIFIED"]
            first_notice = _day(lifecycle.first_notice_date, "first_notice_date")
            first_safe = first_notice - timedelta(days=p.first_notice_buffer_days)
            if today >= first_safe:
                return ["FIRST_NOTICE_CUTOFF_REACHED"]
        return []
