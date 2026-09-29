"""Owner authority — the trust root for every act that can create financial authority.

Asymmetric by design. The **owner's private key never enters this repository, this process, or any
runtime environment variable**; the trading process holds only the public verification key, which
is public material protected against substitution by build integrity
(``engine/owner_public_key.json`` is listed in ``build_guard.CRITICAL_FILES``, so swapping it trips
``EXECUTABLE_BUILD_LOCK``).

This replaces an earlier HMAC design. HMAC was rejected for two reasons: it required requesting a
shared secret from the owner (a secret the process that verifies could also sign with), and a
symmetric key in a trading process is a standing invitation to forge an owner act. With Ed25519 a
caller with full runtime access still cannot produce a passing signature.

Why verification can only ever *reject*. :func:`verify_owner_signature` has no way to expand
authority: with no configured key, or a malformed key, or an unknown key id, it refuses.

Replay protection is layered, because a single trick cannot cover all of it:

1. **Domain separation.** Every signature covers a purpose tag, so a signature produced for an
   amendment can never be replayed as a live authorization, or vice versa.
2. **Version binding.** The envelope carries a version; a future scheme cannot be confused with
   this one.
3. **Key binding.** The envelope carries ``key_id`` and it must equal the pinned key's id, so a
   signature from a rotated or unknown key is refused rather than silently accepted.
4. **Content binding.** The signature covers the artifact's canonical payload, so any mutation
   after signing invalidates it.
5. **Freshness.** Both artifacts carry ``issued_at``/``expires_at`` and are checked separately.

Absent key, unreadable key file, malformed key, mismatched key id, absent signature, malformed
signature and wrong signature all refuse. Nothing here degrades.

Code paths for signing (:func:`sign_owner_payload` is deliberately absent) live only in
``scripts/sign_owner_artifact.py`` and ``engine/execution/ed25519.py``; nothing under
``engine/execution/`` calls :func:`ed25519.sign`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Tuple

from . import ed25519

#: The owner public key. Pinned in build integrity; public material, never a secret.
PUBLIC_KEY_PATH = Path(__file__).resolve().parent.parent / "owner_public_key.json"

SIGNATURE_VERSION = "v1"
ALGORITHM = "ed25519"
#: Domain separator. Any message signed outside this framing verifies as nothing.
DOMAIN = b"TRIPS-OWNER-AUTHORITY-V1"

PURPOSE_AMENDMENT = "trips.amendment-proposal"
PURPOSE_LIVE_AUTHORIZATION = "trips.live-authorization"
PURPOSES = (PURPOSE_AMENDMENT, PURPOSE_LIVE_AUTHORIZATION)

KEY_NOT_CONFIGURED = "OWNER_AUTHORITY_KEY_NOT_CONFIGURED"
KEY_MALFORMED = "OWNER_AUTHORITY_KEY_MALFORMED"
KEY_MISMATCH = "OWNER_AUTHORITY_KEY_MISMATCH"
SIGNATURE_REQUIRED = "OWNER_SIGNATURE_REQUIRED"
SIGNATURE_MALFORMED = "OWNER_SIGNATURE_MALFORMED"
SIGNATURE_INVALID = "OWNER_SIGNATURE_INVALID"
SIGNATURE_VALID = "OWNER_SIGNATURE_VALID"

ENVELOPE_PREFIX = f"{ALGORITHM}:{SIGNATURE_VERSION}"

#: Verification costs a point multiplication (~0.2s in pure Python). Owner acts are rare, but a
#: preflight can ask the same question repeatedly inside one evaluation, so memoise on the exact
#: inputs. The cache can only ever be *asked* about a signature it has already resolved.
_VERIFY_CACHE: dict = {}


class OwnerAuthorityError(Exception):
    """The owner trust root is missing or unusable, so every dependent act fails closed."""


def signed_message(purpose: str, payload: bytes, key_id: str) -> bytes:
    """Domain-separated bytes an owner signature actually covers."""
    if purpose not in PURPOSES:
        raise OwnerAuthorityError(f"unknown signing purpose {purpose!r}")
    return b"\x00".join((DOMAIN, purpose.encode("ascii"), SIGNATURE_VERSION.encode("ascii"),
                         key_id.encode("ascii"), payload))


def key_id_for(public_key: bytes) -> str:
    """Stable identity for a public key. Also what an envelope must carry."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def load_public_key() -> Tuple[bytes, str, str]:
    """Read the pinned public key. Returns ``(raw_key, key_id, reason_if_unusable)``."""
    if not PUBLIC_KEY_PATH.exists():
        return b"", "", f"owner public key file is missing: {PUBLIC_KEY_PATH.name}"
    try:
        document = json.loads(PUBLIC_KEY_PATH.read_text())
    except Exception as exc:
        return b"", "", f"owner public key file is unreadable: {type(exc).__name__}"
    if not isinstance(document, dict):
        return b"", "", "owner public key file is not an object"
    if document.get("algorithm") not in (None, ALGORITHM):
        return b"", "", f"unsupported owner key algorithm {document.get('algorithm')!r}"
    if not document.get("configured"):
        return b"", "", "owner public key is not configured (awaiting the owner's key)"
    encoded = str(document.get("public_key", "")).strip().replace(" ", "")
    if not encoded:
        return b"", "", "owner public key file declares configured but carries no key"
    try:
        raw = bytes.fromhex(encoded)
    except ValueError:
        return b"", "", "owner public key is not valid hex"
    if len(raw) != 32:
        return b"", "", f"owner public key is {len(raw)} bytes; Ed25519 requires 32"
    declared = str(document.get("key_id", "")).strip()
    derived = key_id_for(raw)
    if declared and declared != derived:
        return b"", "", "owner public key file key_id does not match its key"
    return raw, derived, ""


def owner_authority_status() -> dict:
    """Whether owner-signed acts are possible at all. Never returns key material."""
    raw, key_id, reason = load_public_key()
    configured = bool(raw)
    return {
        "configured": configured,
        "algorithm": ALGORITHM if configured else None,
        "signature_version": SIGNATURE_VERSION if configured else None,
        "key_id": key_id or None,
        "key_source": f"engine/{PUBLIC_KEY_PATH.name}",
        "protected_by": "build_guard.CRITICAL_FILES (EXECUTABLE_BUILD_LOCK)",
        "reason": reason,
        "purposes": list(PURPOSES),
        "private_key_in_process": False,
        "note": ("Asymmetric: this process holds only the public verification key. Owner acts "
                 "fail closed while this is unconfigured, so an owner cannot be impersonated by "
                 "absence of a key — only the owner's private key can produce a passing "
                 "signature, and it never enters this repository or this process."),
    }


def verify_owner_signature(purpose: str, payload: bytes, envelope: str) -> Tuple[bool, str, str]:
    """Verify an owner signature. Returns ``(ok, code, detail)`` and never raises."""
    supplied = str(envelope or "").strip().lower()
    if not supplied:
        return False, SIGNATURE_REQUIRED, "artifact carries no owner signature"

    raw, key_id, reason = load_public_key()
    if not raw:
        return False, KEY_NOT_CONFIGURED, f"no owner authority key is configured: {reason}"

    parts = supplied.split(":")
    if len(parts) != 4 or parts[0] != ALGORITHM or parts[1] != SIGNATURE_VERSION:
        return False, SIGNATURE_MALFORMED, (
            f"signature envelope must be {ENVELOPE_PREFIX}:<key_id>:<hex>; refusing to guess")

    _, envelope_version, envelope_key_id, signature_hex = parts
    if envelope_version != SIGNATURE_VERSION:
        return False, SIGNATURE_MALFORMED, (
            f"signature version {envelope_version!r} is not {SIGNATURE_VERSION!r}")
    if envelope_key_id != key_id:
        # Replay of a signature produced under another key, or of a rotated-away key.
        return False, KEY_MISMATCH, (
            f"signature was produced for key {envelope_key_id}, pinned key is {key_id}")
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False, SIGNATURE_MALFORMED, "signature is not valid hex"
    if len(signature) != 64:
        return False, SIGNATURE_MALFORMED, f"signature is {len(signature)} bytes; 64 required"

    cache_key = (purpose, key_id, payload, signature)
    cached = _VERIFY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        message = signed_message(purpose, payload, key_id)
    except OwnerAuthorityError as exc:
        return False, SIGNATURE_MALFORMED, str(exc)

    valid = ed25519.verify(message, signature, raw)
    result = ((True, SIGNATURE_VALID, "owner signature verified") if valid else
              (False, SIGNATURE_INVALID, "owner signature does not match the artifact contents"))
    if len(_VERIFY_CACHE) < 4096:
        _VERIFY_CACHE[cache_key] = result
    return result
