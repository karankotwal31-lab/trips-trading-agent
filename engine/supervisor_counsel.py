from __future__ import annotations

import hashlib
import json
from typing import Dict


ALLOWED_RECOMMENDATIONS = {
    "NO_CHANGE",
    "KEEP_PAPER_OBSERVATION",
    "HALT_NEW_ENTRIES",
    "ESCALATE_TO_USER",
    "REQUEST_MORE_EVIDENCE",
    "PROPOSE_QUARANTINED_CHALLENGER",
}


class SupervisorCounselError(RuntimeError):
    pass


def validate_counsel(counsel: dict, *, known_packet_hashes: set[str]) -> Dict[str, object]:
    """Validate supervisor guidance before Trip's may even record it.

    Counsel is advisory. It can never itself authorize, submit, size or modify a trade.
    """
    if not isinstance(counsel, dict):
        raise SupervisorCounselError("counsel must be an object")
    rec = counsel.get("recommendation")
    if rec not in ALLOWED_RECOMMENDATIONS:
        raise SupervisorCounselError("unsupported supervisor recommendation")
    refs = counsel.get("packet_hashes")
    if not isinstance(refs, list) or not refs or any(x not in known_packet_hashes for x in refs):
        raise SupervisorCounselError("counsel must reference known evidence packets")
    if counsel.get("direct_order_instruction"):
        raise SupervisorCounselError("supervisor counsel may not contain direct order instructions")
    facts = counsel.get("verified_facts")
    inference = counsel.get("inference")
    if not isinstance(facts, list) or not isinstance(inference, list):
        raise SupervisorCounselError("verified_facts and inference must be separate lists")
    if counsel.get("uses_current_market_claims") is True:
        evidence = counsel.get("external_evidence")
        if not isinstance(evidence, list) or not evidence:
            raise SupervisorCounselError("current market claims require external evidence")
        for item in evidence:
            if not isinstance(item, dict) or not item.get("source") or not item.get("observed_at"):
                raise SupervisorCounselError("external evidence requires source and observed_at")
    if rec == "PROPOSE_QUARANTINED_CHALLENGER" and counsel.get("user_approval_status") not in {"REQUIRED", "GRANTED"}:
        raise SupervisorCounselError("material change requires explicit user-approval status")
    raw = json.dumps(counsel, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "accepted_as_advisory": True,
        "recommendation": rec,
        "counsel_hash": hashlib.sha256(raw).hexdigest(),
        "execution_authority": "NONE",
        "may_bypass_forge_or_constitution": False,
    }
