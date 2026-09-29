"""Canonical, immutable, broker-neutral ExecutionIntent.

Carries enough provenance to reconstruct WHY a trade exists, and an explicit validity
boundary consistent with causal execution. It is frozen: no broker layer may mutate it, and
expiry is never extended automatically.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from .contracts import IntentError, IntentExpired, canonical_json

SIDES = ("BUY", "SELL")
ORDER_TYPES = ("LIMIT", "MARKET")
TIME_IN_FORCE = ("DAY", "GTC")


def _aware(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise IntentError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise IntentError(f"{field} is not parseable ISO-8601") from exc
    if parsed.tzinfo is None:
        raise IntentError(f"{field} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    idempotency_key: str
    correlation_id: str
    decision_id: str
    decision_bar_ts: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    time_in_force: str
    limit_price: float | None
    strategy_build_id: str
    config_id: str
    truth_ref: str
    risk_ref: str
    governor_ref: str
    created_at: str
    expires_at: str

    def __post_init__(self) -> None:
        for field_name in ("intent_id", "idempotency_key", "correlation_id", "decision_id",
                           "strategy_build_id", "config_id", "truth_ref", "risk_ref", "governor_ref"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise IntentError(f"{field_name} is required")

        if not isinstance(self.symbol, str) or not self.symbol or self.symbol != self.symbol.strip().upper():
            raise IntentError("symbol must be a canonical uppercase token")

        if self.side not in SIDES:
            raise IntentError(f"side must be one of {SIDES}")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise IntentError("quantity must be a positive integer")
        if self.order_type not in ORDER_TYPES:
            raise IntentError(f"order_type must be one of {ORDER_TYPES}")
        if self.time_in_force not in TIME_IN_FORCE:
            raise IntentError(f"time_in_force must be one of {TIME_IN_FORCE}")

        if self.order_type == "LIMIT":
            price = self.limit_price
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not float(price) > 0:
                raise IntentError("LIMIT intent requires a positive limit_price")
        else:
            if self.limit_price is not None:
                raise IntentError("MARKET intent must not carry a limit_price")

        _aware(self.decision_bar_ts, "decision_bar_ts")
        created = _aware(self.created_at, "created_at")
        expires = _aware(self.expires_at, "expires_at")
        if expires <= created:
            raise IntentError("expires_at must be after created_at")

    # -- provenance -------------------------------------------------------

    def payload(self) -> Dict[str, Any]:
        return asdict(self)

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.payload())).hexdigest()

    def decision_bar_age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now.astimezone(timezone.utc) - _aware(self.decision_bar_ts, "decision_bar_ts")).total_seconds()

    # -- freshness (spec: stale-intent protection) ------------------------

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now.astimezone(timezone.utc) >= _aware(self.expires_at, "expires_at")

    def require_fresh(self, now: datetime | None = None) -> None:
        if self.is_expired(now):
            raise IntentExpired("INTENT_EXPIRED")

    def seconds_until_expiry(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (_aware(self.expires_at, "expires_at") - now.astimezone(timezone.utc)).total_seconds()
