"""Hash-bound executable futures contract master for Trip's.

Capability only: this module does not expand the approved equity scope or grant execution authority.
Continuous/back-adjusted research series are never accepted as executable contract identities.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .contracts import canonical_json
from .task_safety_kernel import ContractLifecycleEvidence

_ALLOWED_SETTLEMENT = frozenset({"CASH", "PHYSICAL"})


class ContractMasterError(RuntimeError):
    pass


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractMasterError(f"{name} must be non-empty")
    return value.strip()


def token(value: Any, name: str) -> str:
    value = nonempty(value, name)
    if value != value.upper() or any(ch.isspace() for ch in value):
        raise ContractMasterError(f"{name} must be a canonical uppercase token")
    return value


def positive(value: Any, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractMasterError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ContractMasterError(f"{name} is outside finite positive bounds")
    return number


def day(value: Any, name: str) -> date:
    if not isinstance(value, str) or not value:
        raise ContractMasterError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except Exception as exc:
        raise ContractMasterError(f"{name} must be YYYY-MM-DD") from exc


def aware(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception as exc:
            raise ContractMasterError(f"{name} must be ISO-8601") from exc
    else:
        raise ContractMasterError(f"{name} must be timezone-aware ISO-8601")
    if parsed.tzinfo is None:
        raise ContractMasterError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ContractSpec:
    contract_symbol: str
    root_symbol: str
    series_kind: str
    venue: str
    sector: str
    currency: str
    contract_multiplier: float
    tick_size: float
    settlement_type: str
    last_trade_date: str
    expiration_date: str
    first_notice_date: Optional[str]
    source_id: str
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "contract_symbol", token(self.contract_symbol, "contract_symbol"))
        object.__setattr__(self, "root_symbol", token(self.root_symbol, "root_symbol"))
        if self.series_kind != "EXECUTABLE_CONTRACT":
            raise ContractMasterError(
                "series_kind must be EXECUTABLE_CONTRACT; continuous/back-adjusted series are research-only")
        object.__setattr__(self, "venue", token(self.venue, "venue"))
        object.__setattr__(self, "sector", token(self.sector, "sector"))
        object.__setattr__(self, "currency", token(self.currency, "currency"))
        positive(self.contract_multiplier, "contract_multiplier")
        positive(self.tick_size, "tick_size")
        if self.settlement_type not in _ALLOWED_SETTLEMENT:
            raise ContractMasterError(f"invalid settlement_type: {self.settlement_type!r}")
        day(self.last_trade_date, "last_trade_date")
        day(self.expiration_date, "expiration_date")
        if self.first_notice_date is not None:
            day(self.first_notice_date, "first_notice_date")
        if self.settlement_type == "PHYSICAL" and self.first_notice_date is None:
            raise ContractMasterError("physical contract requires first_notice_date evidence")
        nonempty(self.source_id, "source_id")
        aware(self.observed_at, "observed_at")

    @property
    def tick_value(self) -> float:
        return float(self.contract_multiplier) * float(self.tick_size)

    def lifecycle_evidence(self) -> ContractLifecycleEvidence:
        return ContractLifecycleEvidence(
            settlement_type=self.settlement_type,
            first_notice_date=self.first_notice_date,
            last_trade_date=self.last_trade_date,
        )

    def to_dict(self) -> Dict[str, Any]:
        body = asdict(self)
        body["tick_value"] = self.tick_value
        return body


@dataclass(frozen=True)
class ContractMasterSnapshot:
    source_name: str
    as_of: str
    contracts: Tuple[ContractSpec, ...]

    def __post_init__(self) -> None:
        nonempty(self.source_name, "source_name")
        aware(self.as_of, "as_of")
        if not isinstance(self.contracts, tuple) or not self.contracts:
            raise ContractMasterError("contract master must not be empty")
        if any(not isinstance(item, ContractSpec) for item in self.contracts):
            raise ContractMasterError("contract master entries must be ContractSpec")
        symbols = [item.contract_symbol for item in self.contracts]
        if len(set(symbols)) != len(symbols):
            raise ContractMasterError("duplicate contract symbols in contract master")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name,
            "as_of": self.as_of,
            "contracts": [item.to_dict() for item in self.contracts],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def by_symbol(self, contract_symbol: str) -> ContractSpec:
        wanted = token(contract_symbol, "contract_symbol")
        for item in self.contracts:
            if item.contract_symbol == wanted:
                return item
        raise ContractMasterError(f"contract not present in master: {wanted}")


def load_contract_master_json(raw: str | bytes) -> ContractMasterSnapshot:
    """Parsing grants no trust; callers must compare ``snapshot.fingerprint`` to an approved hash."""
    try:
        payload = json.loads(raw)
        entries = payload["contracts"]
        if not isinstance(entries, list) or not entries:
            raise TypeError("contracts")
        contracts = tuple(ContractSpec(**entry) for entry in entries)
        return ContractMasterSnapshot(
            source_name=payload["source_name"], as_of=payload["as_of"], contracts=contracts)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ContractMasterError("invalid contract-master JSON") from exc
