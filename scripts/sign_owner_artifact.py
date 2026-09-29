"""Owner-side signing tool for the two acts that can release capital.

Trip's has exactly two owner acts:

* **Decision A** — ``AmendmentProposal``: amend CONSTITUTION rule 13 ``PAPER_FIRST`` to ``LIVE_GATE``.
* **Decision B** — ``LiveAuthorization``: authorise one approved live identity.

Neither is a boolean. Both are signed with the owner authority key, which lives outside this
repository and is supplied only for the duration of this command.

Usage::

    python3 scripts/sign_owner_artifact.py --status
    TRIPS_OWNER_AUTHORITY_KEY=<key> python3 scripts/sign_owner_artifact.py proposal proposal.json
    TRIPS_OWNER_AUTHORITY_KEY=<key> python3 scripts/sign_owner_artifact.py authorization auth.json

The unsigned input is a JSON object with the artifact's fields except ``signature``. A file path or
``-`` for stdin is accepted. The signed artifact is printed as JSON on stdout.

This tool never prints the key and never writes to the repository. Keep the key out of the trading
process's normal environment: the runtime only needs it while performing an owner act.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from execution.amendment import AmendmentProposal  # noqa: E402
from execution.lifecycle import LiveAuthorization  # noqa: E402
from execution.owner_authority import (  # noqa: E402
    OwnerAuthorityError,
    owner_authority_status,
    sign_owner_payload,
)

ARTIFACTS = {
    "proposal": AmendmentProposal,
    "authorization": LiveAuthorization,
}


def _load(source: str) -> dict:
    if source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text())


def sign(kind: str, payload: dict) -> dict:
    payload = dict(payload)
    payload.pop("signature", None)
    artifact = ARTIFACTS[kind](**payload)
    signature = sign_owner_payload(artifact.signed_payload())
    body = {**payload, "signature": signature}
    # Round-trip through the real dataclass so an invalid artifact cannot be blessed here.
    ARTIFACTS[kind](**body)
    return {"artifact": kind, "payload": body, "signature": signature,
            "content_hash": artifact.content_hash()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", nargs="?", choices=sorted(ARTIFACTS))
    parser.add_argument("source", nargs="?", default="-", help="JSON file, or - for stdin")
    parser.add_argument("--status", action="store_true",
                        help="report whether owner acts are possible; prints no key material")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(owner_authority_status(), indent=2, sort_keys=True))
        return

    if not args.kind:
        parser.error("kind is required unless --status is given")

    try:
        result = sign(args.kind, _load(args.source))
    except OwnerAuthorityError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True),
              file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         indent=2, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
