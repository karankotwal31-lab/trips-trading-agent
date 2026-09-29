"""Two-source exact-contract market truth for commodity futures.

Read-only.  This module decides whether market evidence is trustworthy for analysis; it has no
broker, capital, order, or strategy authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .contract_master import aware, nonempty, positive, token
from .contracts import canonical_json

TRUSTED = "TRUSTED"
BLOCKED = "BLOCKED"


class CommodityTruthError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommodityTruthPolicy:
    policy_version: str
    require_realtime_sources: bool
    require_independent_sources: bool
    max_data_age_seconds: int
    max_cross_source_price_deviation_bps: float

    def __post_init__(self) -> None:
        nonempty(self.policy_version, "policy_version")
        if not isinstance(self.require_realtime_sources, bool):
            raise CommodityTruthError("require_realtime_sources must be boolean")
        if not isinstance(self.require_independent_sources, bool):
            raise CommodityTruthError("require_independent_sources must be boolean")
        age = positive(self.max_data_age_seconds, "max_data_age_seconds")
        if age != int(age):
            raise CommodityTruthError("max_data_age_seconds must be a whole number")
        positive(self.max_cross_source_price_deviation_bps,
                 "max_cross_source_price_deviation_bps", allow_zero=True)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fingerprint_truth_policy(policy: CommodityTruthPolicy) -> str:
    return hashlib.sha256(canonical_json(policy.to_dict())).hexdigest()


@dataclass(frozen=True)
class MarketSourceEvidence:
    source_id: str
    source_family: str
    source_kind: str
    contract_symbol: str
    observed_at: str
    venue_state: str
    bid: float
    ask: float
    last: float
    daily_volume: float
    open_interest: float

    def __post_init__(self) -> None:
        nonempty(self.source_id, "source_id")
        nonempty(self.source_family, "source_family")
        nonempty(self.source_kind, "source_kind")
        object.__setattr__(self, "contract_symbol", token(self.contract_symbol, "contract_symbol"))
        aware(self.observed_at, "observed_at")
        object.__setattr__(self, "venue_state", token(self.venue_state, "venue_state"))
        bid = positive(self.bid, "bid")
        ask = positive(self.ask, "ask")
        positive(self.last, "last")
        positive(self.daily_volume, "daily_volume", allow_zero=True)
        positive(self.open_interest, "open_interest", allow_zero=True)
        if ask < bid:
            raise CommodityTruthError("ask cannot be below bid")

    @property
    def mid(self) -> float:
        return (float(self.bid) + float(self.ask)) / 2.0

    @property
    def spread_bps(self) -> float:
        return ((float(self.ask) - float(self.bid)) / self.mid) * 10_000.0


@dataclass(frozen=True)
class CommodityTruthDecision:
    status: str
    contract_symbol: str
    blocks: Tuple[str, ...]
    observed_price: float
    secondary_price: Optional[float]
    spread_bps: float
    average_daily_volume: float
    open_interest: float
    venue_state: str
    source_ids: Tuple[str, ...]
    source_families: Tuple[str, ...]

    @property
    def trusted_for_analysis(self) -> bool:
        return self.status == TRUSTED and not self.blocks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "trusted_for_analysis": self.trusted_for_analysis,
            "contract_symbol": self.contract_symbol,
            "blocks": list(self.blocks),
            "observed_price": self.observed_price,
            "secondary_price": self.secondary_price,
            "spread_bps": self.spread_bps,
            "average_daily_volume": self.average_daily_volume,
            "open_interest": self.open_interest,
            "venue_state": self.venue_state,
            "source_ids": list(self.source_ids),
            "source_families": list(self.source_families),
        }


class CommodityTruthGate:
    def __init__(self, policy: CommodityTruthPolicy, *, approved_policy_hash: str) -> None:
        if not isinstance(policy, CommodityTruthPolicy):
            raise CommodityTruthError("policy must be CommodityTruthPolicy")
        actual = fingerprint_truth_policy(policy)
        if actual != approved_policy_hash:
            raise CommodityTruthError("commodity truth policy fingerprint mismatch")
        self.policy = policy
        self.policy_hash = actual

    def evaluate(self, contract_symbol: str, *,
                 primary: MarketSourceEvidence,
                 secondary: Optional[MarketSourceEvidence],
                 now: Optional[datetime] = None) -> CommodityTruthDecision:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbol = token(contract_symbol, "contract_symbol")
        blocks = []
        sources = [primary]

        if primary.contract_symbol != symbol:
            blocks.append("PRIMARY_SYMBOL_MISMATCH")

        if self.policy.require_independent_sources:
            if secondary is None:
                blocks.append("SECONDARY_SOURCE_REQUIRED")
            else:
                sources.append(secondary)
                if secondary.contract_symbol != symbol:
                    blocks.append("SECONDARY_SYMBOL_MISMATCH")
                if secondary.source_family == primary.source_family:
                    blocks.append("MARKET_SOURCES_NOT_INDEPENDENT")
        elif secondary is not None:
            sources.append(secondary)
            if secondary.contract_symbol != symbol:
                blocks.append("SECONDARY_SYMBOL_MISMATCH")

        for label, source in (("PRIMARY", primary), ("SECONDARY", secondary)):
            if source is None:
                continue
            age = (now - aware(source.observed_at, "observed_at")).total_seconds()
            if age < 0:
                blocks.append(f"{label}_MARKET_DATA_FROM_FUTURE")
            elif age > self.policy.max_data_age_seconds:
                blocks.append(f"{label}_MARKET_DATA_STALE")
            if self.policy.require_realtime_sources and source.source_kind != "REALTIME":
                blocks.append(f"{label}_SOURCE_NOT_REALTIME")

        secondary_price = None
        if secondary is not None:
            secondary_price = secondary.mid
            if secondary.venue_state != primary.venue_state:
                blocks.append("VENUE_STATE_DISAGREEMENT")
            denominator = max(primary.mid, secondary.mid)
            deviation = abs(primary.mid - secondary.mid) / denominator * 10_000.0
            if deviation > self.policy.max_cross_source_price_deviation_bps:
                blocks.append("CROSS_SOURCE_PRICE_DEVIATION")

        volume = min(source.daily_volume for source in sources)
        open_interest = min(source.open_interest for source in sources)
        if volume <= 0:
            blocks.append("NO_REPORTED_VOLUME")
        if open_interest <= 0:
            blocks.append("NO_REPORTED_OPEN_INTEREST")

        return CommodityTruthDecision(
            status=BLOCKED if blocks else TRUSTED,
            contract_symbol=symbol,
            blocks=tuple(sorted(set(blocks))),
            observed_price=primary.mid,
            secondary_price=secondary_price,
            spread_bps=max(source.spread_bps for source in sources),
            average_daily_volume=volume,
            open_interest=open_interest,
            venue_state=primary.venue_state,
            source_ids=tuple(source.source_id for source in sources),
            source_families=tuple(source.source_family for source in sources),
        )
