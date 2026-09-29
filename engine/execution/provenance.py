"""Data provenance separation.

Enabling broker execution must never silently promote the broker's own feed into a trusted
Strategy data source. Broker execution authority and market-data Truth authority are separate,
so this guard makes the boundary explicit and testable instead of relying on the absence of a
code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple


class DataPurpose(str, Enum):
    STRATEGY_ANALYSIS = "STRATEGY_ANALYSIS"
    TRADE_ELIGIBILITY = "TRADE_ELIGIBILITY"
    EXECUTION_RECONCILIATION = "EXECUTION_RECONCILIATION"


class ProvenanceViolation(RuntimeError):
    """A broker execution feed was offered as a Strategy/Truth data source."""


@dataclass(frozen=True)
class DataSourceRecord:
    source: str
    source_family: str
    origin: str  # "market_data_provider" | "broker_execution_feed"
    approved_for_truth: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "source_family": self.source_family, "origin": self.origin,
                "approved_for_truth": self.approved_for_truth}


class DataSourceGuard:
    """Allowlist of sources that may feed Truth and Strategy.

    A broker execution feed is only ever acceptable for EXECUTION_RECONCILIATION. It can never
    satisfy STRATEGY_ANALYSIS or TRADE_ELIGIBILITY, even when the broker connection is healthy
    and convenient.
    """

    def __init__(self, *, approved_sources: Iterable[str] = (),
                 approved_families: Iterable[str] = ()) -> None:
        self._sources: FrozenSet[str] = frozenset(str(s) for s in approved_sources)
        self._families: FrozenSet[str] = frozenset(str(s) for s in approved_families)

    @property
    def approved_sources(self) -> Tuple[str, ...]:
        return tuple(sorted(self._sources))

    def admit(self, record: DataSourceRecord, *, purpose: DataPurpose) -> None:
        if purpose is DataPurpose.EXECUTION_RECONCILIATION:
            return
        if record.origin == "broker_execution_feed":
            raise ProvenanceViolation(
                f"broker execution feed {record.source!r} may not supply {purpose.value}")
        if record.origin != "market_data_provider":
            raise ProvenanceViolation(
                f"unrecognized data origin {record.origin!r} for {purpose.value}")
        if not record.approved_for_truth:
            raise ProvenanceViolation(
                f"source {record.source!r} is not approved for Truth eligibility")
        if self._sources and record.source not in self._sources:
            raise ProvenanceViolation(f"source {record.source!r} is not on the approved source list")
        if self._families and record.source_family not in self._families:
            raise ProvenanceViolation(
                f"source family {record.source_family!r} is not on the approved family list")

    def check(self, record: DataSourceRecord, *, purpose: DataPurpose) -> Dict[str, Any]:
        try:
            self.admit(record, purpose=purpose)
        except ProvenanceViolation as exc:
            return {"permitted": False, "purpose": purpose.value, "reason": str(exc)}
        return {"permitted": True, "purpose": purpose.value, "reason": "approved"}
