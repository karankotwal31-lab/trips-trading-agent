"""Owner-scoped commodity analysis gate for Trip's.

This is capability without activation: current config remains US_EQUITY_CASH_LONG_ONLY.  The gate
can expose exact contracts to the autonomous strategy but cannot execute, roll, resize, or replace
an order.  TASK remains the binding execution envelope.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .commodity_truth import (
    CommodityTruthDecision,
    CommodityTruthGate,
    MarketSourceEvidence,
)
from .contract_master import (
    ContractMasterError,
    ContractMasterSnapshot,
    ContractSpec,
    aware,
    day,
    nonempty,
    positive,
    token,
)
from .contracts import canonical_json

ANALYSIS_ELIGIBLE = "ANALYSIS_ELIGIBLE"
BLOCKED = "BLOCKED"


class CommodityReadinessError(RuntimeError):
    pass


def _tokens(values: Sequence[str], name: str) -> Tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values:
        raise CommodityReadinessError(f"{name} cannot be empty")
    result = tuple(token(value, name) for value in values)
    if len(set(result)) != len(result):
        raise CommodityReadinessError(f"{name} cannot contain duplicates")
    return result


@dataclass(frozen=True)
class CommodityUniversePolicy:
    policy_version: str
    approved_roots: Tuple[str, ...]
    approved_venues: Tuple[str, ...]
    approved_sectors: Tuple[str, ...]
    max_contract_metadata_age_hours: int

    def __post_init__(self) -> None:
        nonempty(self.policy_version, "policy_version")
        object.__setattr__(self, "approved_roots", _tokens(self.approved_roots, "approved_roots"))
        object.__setattr__(self, "approved_venues", _tokens(self.approved_venues, "approved_venues"))
        object.__setattr__(self, "approved_sectors", _tokens(self.approved_sectors, "approved_sectors"))
        hours = positive(self.max_contract_metadata_age_hours, "max_contract_metadata_age_hours")
        if hours != int(hours):
            raise CommodityReadinessError("max_contract_metadata_age_hours must be whole")

    def to_dict(self) -> Dict[str, Any]:
        body = asdict(self)
        for key in ("approved_roots", "approved_venues", "approved_sectors"):
            body[key] = list(body[key])
        return body


def fingerprint_universe_policy(policy: CommodityUniversePolicy) -> str:
    return hashlib.sha256(canonical_json(policy.to_dict())).hexdigest()


@dataclass(frozen=True)
class CommodityGateDecision:
    status: str
    contract_symbol: str
    blocks: Tuple[str, ...]
    contract: Optional[ContractSpec]
    truth: Optional[CommodityTruthDecision]
    universe_policy_hash: str
    contract_master_hash: str

    @property
    def eligible_for_analysis(self) -> bool:
        return self.status == ANALYSIS_ELIGIBLE and not self.blocks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "eligible_for_analysis": self.eligible_for_analysis,
            "contract_symbol": self.contract_symbol,
            "blocks": list(self.blocks),
            "contract": self.contract.to_dict() if self.contract else None,
            "truth": self.truth.to_dict() if self.truth else None,
            "universe_policy_hash": self.universe_policy_hash,
            "contract_master_hash": self.contract_master_hash,
            "execution_authority": False,
            "symbol_substitution_authority": False,
        }


class CommodityReadinessGate:
    def __init__(self, policy: CommodityUniversePolicy, *,
                 approved_policy_hash: str,
                 approved_contract_master_hash: str,
                 truth_gate: CommodityTruthGate) -> None:
        actual = fingerprint_universe_policy(policy)
        if actual != approved_policy_hash:
            raise CommodityReadinessError("commodity universe policy fingerprint mismatch")
        if not isinstance(approved_contract_master_hash, str) or len(approved_contract_master_hash) != 64:
            raise CommodityReadinessError("approved contract-master hash must be SHA-256")
        try:
            int(approved_contract_master_hash, 16)
        except ValueError as exc:
            raise CommodityReadinessError("approved contract-master hash must be hex") from exc
        if not isinstance(truth_gate, CommodityTruthGate):
            raise CommodityReadinessError("truth_gate must be CommodityTruthGate")
        self.policy = policy
        self.policy_hash = actual
        self.contract_master_hash = approved_contract_master_hash.lower()
        self.truth_gate = truth_gate

    def evaluate(self, contract_symbol: str, *,
                 snapshot: ContractMasterSnapshot,
                 primary: MarketSourceEvidence,
                 secondary: Optional[MarketSourceEvidence],
                 now: Optional[datetime] = None) -> CommodityGateDecision:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbol = token(contract_symbol, "contract_symbol")
        blocks = []

        if snapshot.fingerprint != self.contract_master_hash:
            blocks.append("CONTRACT_MASTER_FINGERPRINT_MISMATCH")
        snapshot_age = (now - aware(snapshot.as_of, "as_of")).total_seconds()
        if snapshot_age < 0:
            blocks.append("CONTRACT_MASTER_FROM_FUTURE")
        elif snapshot_age > self.policy.max_contract_metadata_age_hours * 3600:
            blocks.append("CONTRACT_MASTER_STALE")

        contract = None
        try:
            contract = snapshot.by_symbol(symbol)
        except ContractMasterError:
            blocks.append("CONTRACT_NOT_IN_MASTER")

        if contract is not None:
            if contract.root_symbol not in self.policy.approved_roots:
                blocks.append("ROOT_NOT_APPROVED")
            if contract.venue not in self.policy.approved_venues:
                blocks.append("VENUE_NOT_APPROVED")
            if contract.sector not in self.policy.approved_sectors:
                blocks.append("SECTOR_NOT_APPROVED")
            metadata_age = (now - aware(contract.observed_at, "observed_at")).total_seconds()
            if metadata_age < 0:
                blocks.append("CONTRACT_METADATA_FROM_FUTURE")
            elif metadata_age > self.policy.max_contract_metadata_age_hours * 3600:
                blocks.append("CONTRACT_METADATA_STALE")
            # TASK applies the owner-defined pre-FND/pre-LTD buffers. This gate only enforces the
            # unambiguous hard fact that a contract at/after Last Trade is no longer a new-entry
            # candidate. It invents no buffer.
            if now.date() >= day(contract.last_trade_date, "last_trade_date"):
                blocks.append("CONTRACT_LAST_TRADE_REACHED")

        truth = self.truth_gate.evaluate(
            symbol, primary=primary, secondary=secondary, now=now)
        blocks.extend(truth.blocks)

        return CommodityGateDecision(
            status=BLOCKED if blocks else ANALYSIS_ELIGIBLE,
            contract_symbol=symbol,
            blocks=tuple(sorted(set(blocks))),
            contract=contract,
            truth=truth,
            universe_policy_hash=self.policy_hash,
            contract_master_hash=self.contract_master_hash,
        )

    def scan(self, *, snapshot: ContractMasterSnapshot,
             evidence: Mapping[str, Tuple[MarketSourceEvidence, Optional[MarketSourceEvidence]]],
             now: Optional[datetime] = None) -> Tuple[CommodityGateDecision, ...]:
        """Read-only deterministic scan; it neither ranks nor chooses an order for the strategy."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        decisions = []
        for symbol in sorted(evidence):
            pair = evidence[symbol]
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise CommodityReadinessError("evidence must map symbol -> (primary, secondary)")
            decisions.append(self.evaluate(
                symbol, snapshot=snapshot, primary=pair[0], secondary=pair[1], now=now))
        return tuple(decisions)
