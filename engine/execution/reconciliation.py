"""Reconciliation Engine.

The broker is authoritative for what externally happened. This engine compares canonical local
expectations against broker evidence; any unresolved discrepancy means NO NEW EXPOSURE. Its
result feeds CAPITAL_RELEASE's ``broker_state_reconciled`` and
``no_unresolved_execution_ambiguity`` checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class ReconciliationResult:
    clean: bool
    discrepancies: Tuple[Discrepancy, ...]

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(d.kind for d in self.discrepancies)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clean": self.clean,
            "discrepancy_count": len(self.discrepancies),
            "codes": list(self.codes),
            "discrepancies": [d.to_dict() for d in self.discrepancies],
        }


class ReconciliationEngine:
    def reconcile(
        self,
        *,
        local_positions: Mapping[str, int],
        broker_positions: Sequence[Any],
        broker_open_client_order_ids: Iterable[str] = (),
        pending_intent_client_order_ids: Iterable[str] = (),
        expected_account_id: str | None = None,
        broker_account_id: str | None = None,
    ) -> ReconciliationResult:
        discrepancies = []

        broker_qty: Dict[str, int] = {}
        for position in broker_positions:
            symbol = getattr(position, "symbol", None)
            quantity = getattr(position, "quantity", None)
            if not isinstance(symbol, str) or not symbol:
                discrepancies.append(Discrepancy("MALFORMED_BROKER_POSITION", "broker position without symbol"))
                continue
            if isinstance(quantity, bool) or not isinstance(quantity, int):
                discrepancies.append(Discrepancy("MALFORMED_BROKER_POSITION", f"{symbol} quantity not an integer"))
                continue
            if quantity == 0:
                continue
            broker_qty[symbol] = broker_qty.get(symbol, 0) + quantity

        for symbol, quantity in sorted(local_positions.items()):
            theirs = broker_qty.get(symbol, 0)
            if theirs != quantity:
                discrepancies.append(Discrepancy(
                    "POSITION_QUANTITY_MISMATCH", f"{symbol}: local={quantity} broker={theirs}"))

        for symbol in sorted(set(broker_qty) - set(local_positions)):
            discrepancies.append(Discrepancy(
                "UNKNOWN_BROKER_POSITION", f"{symbol}: broker holds {broker_qty[symbol]} with no local record"))

        open_ids = {str(value) for value in broker_open_client_order_ids}
        pending_ids = {str(value) for value in pending_intent_client_order_ids}
        for client_id in sorted(open_ids & pending_ids):
            discrepancies.append(Discrepancy(
                "UNRESOLVED_ORDER", f"{client_id} is still open at the broker"))

        if expected_account_id is not None and broker_account_id != expected_account_id:
            discrepancies.append(Discrepancy(
                "WRONG_ACCOUNT", f"expected={expected_account_id} broker={broker_account_id}"))

        return ReconciliationResult(clean=not discrepancies, discrepancies=tuple(discrepancies))
