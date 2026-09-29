"""Owner-side signing tool for the two acts that can release capital.

Trip's has exactly two owner acts:

* **Decision A** — ``AmendmentProposal``: amend CONSTITUTION rule 13 ``PAPER_FIRST`` to ``LIVE_GATE``.
* **Decision B** — ``LiveAuthorization``: authorise one approved live identity.

Both are signed with the owner's **Ed25519 private key**, which never enters this repository and is
never read by the trading process. Only the public key lives in the repo, pinned by build
integrity, so nobody can substitute their own.

Typical first run (offline, on the owner's machine)::

    python3 scripts/sign_owner_artifact.py --keygen --out ~/.secrets/trips_owner_ed25519.key
    # copy the printed "public_key" block into engine/owner_public_key.json, set configured=true,
    # then re-issue the build fingerprint:  cd engine && python3 approve_build.py

Signing afterwards::

    python3 scripts/sign_owner_artifact.py proposal proposal.json --key ~/.secrets/...key
    python3 scripts/sign_owner_artifact.py authorization auth.json --key ~/.secrets/...key
    python3 scripts/sign_owner_artifact.py --status

This tool never writes into the repository, never prints the private key, and never embeds a
signature into a live authorization. The owner sign-off row in AMENDMENT-01 is left blank by
design: that is a human decision, not a program output.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from execution import ed25519  # noqa: E402
from execution.amendment import AmendmentProposal  # noqa: E402
from execution.lifecycle import LiveAuthorization  # noqa: E402
from execution.owner_authority import (  # noqa: E402
    ALGORITHM,
    PURPOSE_AMENDMENT,
    PURPOSE_LIVE_AUTHORIZATION,
    SIGNATURE_VERSION,
    OwnerAuthorityError,
    key_id_for,
    load_public_key,
    owner_authority_status,
    signed_message,
    verify_owner_signature,
)

PRIVATE_KEY_ENV = "TRIPS_OWNER_PRIVATE_KEY"
ARTIFACTS = {
    "proposal": (AmendmentProposal, PURPOSE_AMENDMENT),
    "authorization": (LiveAuthorization, PURPOSE_LIVE_AUTHORIZATION),
}


def _refuse_in_repo(path: Path, what: str) -> None:
    """The repository is public. Key material must never land inside it."""
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise SystemExit(f"refusing to place {what} inside the repository at {path}")


def keygen(out_path: str) -> int:
    destination = Path(out_path).expanduser()
    _refuse_in_repo(destination, "the owner private key")
    destination.parent.mkdir(parents=True, exist_ok=True)
    seed = secrets.token_bytes(32)
    private, public = ed25519.generate_keypair(seed)
    destination.write_text(private.hex() + "\n")
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    document = {
        "algorithm": ALGORITHM,
        "configured": True,
        "key_id": key_id_for(public),
        "note": ("Owner Ed25519 public verification key. Public material. Pinned in "
                 "build_guard.CRITICAL_FILES; substituting it trips EXECUTABLE_BUILD_LOCK."),
        "public_key": public.hex(),
    }
    print(json.dumps({
        "private_key_written_to": str(destination),
        "private_key_printed": False,
        "next_steps": [
            f"copy the 'owner_public_key' object below into {ROOT / 'engine' / 'owner_public_key.json'}",
            "then re-issue the build fingerprint: cd engine && python3 approve_build.py",
            "keep the private key offline; the trading process never needs it",
        ],
        "owner_public_key": document,
    }, indent=2, sort_keys=True))
    return 0


def _load_private(args: argparse.Namespace) -> bytes:
    if getattr(args, "key", None):
        raw = Path(args.key).expanduser().read_text().strip()
    else:
        raw = os.getenv(PRIVATE_KEY_ENV, "").strip()
    if not raw:
        raise SystemExit(
            f"no private key: pass --key PATH or set {PRIVATE_KEY_ENV} for this command only")
    try:
        private = bytes.fromhex(raw.replace(" ", ""))
    except ValueError:
        raise SystemExit("private key is not valid hex") from None
    if len(private) != 32:
        raise SystemExit(f"private key is {len(private)} bytes; Ed25519 requires 32")
    return private


def sign(kind: str, source: str, args: argparse.Namespace) -> int:
    payload = dict(json.loads(sys.stdin.read() if source == "-"
                              else Path(source).read_text()))
    payload.pop("signature", None)
    artifact_type, purpose = ARTIFACTS[kind]
    artifact = artifact_type(**payload)
    raw_public, key_id, reason = load_public_key()
    if not raw_public:
        raise SystemExit(f"no owner public key is installed: {reason}\n"
                         f"run --keygen and install the public key first")
    private = _load_private(args)
    message = signed_message(purpose, artifact.signed_payload(), key_id)
    signature = ed25519.sign(message, private)
    envelope = f"{ALGORITHM}:{SIGNATURE_VERSION}:{key_id}:{signature.hex()}"
    body = {**payload, "signature": envelope}
    artifact_type(**body)  # round-trip: refuse to bless a structurally invalid artifact
    ok, code, detail = verify_owner_signature(purpose, artifact.signed_payload(), envelope)
    if not ok:
        raise SystemExit(f"self-verification failed: {code} {detail}")
    print(json.dumps({"artifact": kind, "content_hash": artifact.content_hash(),
                      "key_id": key_id, "payload": body, "signature": envelope,
                      "verified": True}, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", nargs="?", choices=sorted(ARTIFACTS))
    parser.add_argument("source", nargs="?", default="-", help="JSON file, or - for stdin")
    parser.add_argument("--key", help=f"path to the owner private key (or set {PRIVATE_KEY_ENV})")
    parser.add_argument("--status", action="store_true",
                        help="report whether owner acts are possible; prints no key material")
    parser.add_argument("--keygen", action="store_true", metavar="OUT",
                        help="generate an Ed25519 keypair; writes the private key to OUT (0600)")
    args = parser.parse_args()

    if args.keygen:
        if not args.key:
            parser.error("--keygen requires --out PATH for the private key")
        return keygen(args.key)
    if args.status:
        print(json.dumps(owner_authority_status(), indent=2, sort_keys=True))
        return 0
    if not args.kind:
        parser.error("kind is required unless --status or --keygen is given")
    try:
        return sign(args.kind, args.source, args)
    except OwnerAuthorityError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True),
              file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
