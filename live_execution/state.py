from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, asdict
from pathlib import Path

class ExecutionStateError(RuntimeError): pass

@dataclass
class OrderRecord:
    idempotency_key: str
    broker_order_id: str
    status: str
    symbol: str
    side: str
    qty: float
    filled_qty: float = 0.0

def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def verify_reconciliation(local_orders:list[dict], broker_orders:list[dict]) -> dict:
    local={x["broker_order_id"]:x for x in local_orders}
    remote={x["broker_order_id"]:x for x in broker_orders}
    missing_remote=sorted(set(local)-set(remote))
    unknown_remote=sorted(set(remote)-set(local))
    mismatched=[]
    for oid in sorted(set(local)&set(remote)):
        for field in ("symbol","side","qty","filled_qty","status"):
            if local[oid].get(field)!=remote[oid].get(field):
                mismatched.append({"broker_order_id":oid,"field":field,"local":local[oid].get(field),"broker":remote[oid].get(field)})
    return {"ok":not (missing_remote or unknown_remote or mismatched),
            "missing_remote":missing_remote,"unknown_remote":unknown_remote,"mismatched":mismatched}
