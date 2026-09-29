"""Owner authority — the trust root for every act that can create financial authority.

Only the owner may amend the Constitution or authorise live trading. That claim is worthless if
"owner-signed" is a boolean a caller can set, so this module makes it a **keyed signature**: an
HMAC-SHA256 tag over the artifact's canonical payload, produced with a key that exists only in the
owner's environment and is never stored in this repository.

The key is read at call time, never cached, so an environment change is always honoured.

Fail-closed rules. All of these refuse; none of them degrades:

* no key configured                     -> ``OWNER_AUTHORITY_KEY_NOT_CONFIGURED``
* key shorter than 32 bytes             -> ``OWNER_AUTHORITY_KEY_TOO_WEAK``
* key file unreadable or empty          -> ``OWNER_AUTHORITY_KEY_NOT_CONFIGURED``
* artifact carries no signature         -> ``OWNER_SIGNATURE_REQUIRED``
* signature does not match the payload  -> ``OWNER_SIGNATURE_INVALID``

Because the verifying key is never derivable from repository content, no Strategy, Risk, Governor,
Student, Evolution, Guardian, AI Supervisor or ordinary caller can produce a passing signature.
The residual trust assumption is stated plainly: a process that can read the owner key can sign.
Keep the key out of the trading process's normal runtime environment except for the explicit owner
authority step.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Optional, Tuple

#: Environment variable holding the owner authority key (hex or raw text).
KEY_ENV = "TRIPS_OWNER_AUTHORITY_KEY"

#: Alternative: a path to a file holding the owner authority key. Must live outside the repository.
KEY_FILE_ENV = "TRIPS_OWNER_AUTHORITY_KEY_FILE"

#: Shortest acceptable key. Shorter material cannot be considered a real secret.
MIN_KEY_BYTES = 32

KEY_NOT_CONFIGURED = "OWNER_AUTHORITY_KEY_NOT_CONFIGURED"
KEY_TOO_WEAK = "OWNER_AUTHORITY_KEY_TOO_WEAK"
SIGNATURE_REQUIRED = "OWNER_SIGNATURE_REQUIRED"
SIGNATURE_INVALID = "OWNER_SIGNATURE_INVALID"
SIGNATURE_VALID = "OWNER_SIGNATURE_VALID"

ALGORITHM = "HMAC-SHA256"


class OwnerAuthorityError(Exception):
    """The owner trust root is missing or unusable, so every dependent act fails closed."""


def _material() -> Tuple[Optional[str], str, str]:
    """Locate the owner key material. Returns (material, source, reason)."""
    value = os.getenv(KEY_ENV, "").strip()
    if value:
        return value, f"env:{KEY_ENV}", ""

    path_value = os.getenv(KEY_FILE_ENV, "").strip()
    if not path_value:
        return None, "none", f"neither {KEY_ENV} nor {KEY_FILE_ENV} is configured"

    path = Path(path_value)
    try:
        raw = path.read_text().strip()
    except Exception as exc:
        return None, f"file:{path_value}", f"owner key file is unreadable: {type(exc).__name__}"
    if not raw:
        return None, f"file:{path_value}", "owner key file is empty"
    return raw, f"file:{path_value}", ""


def _decode(material: str) -> bytes:
    """Accept hex or raw text. Hex is preferred when the material is unambiguously hex."""
    text = material.strip()
    candidate = bytes.fromhex(text) if len(text) % 2 == 0 else None
    if candidate is not None and len(candidate) >= MIN_KEY_BYTES // 2:
        return candidate
    return text.encode("utf-8")


def owner_key() -> Tuple[Optional[bytes], str, str]:
    """The owner key, or (None, source, reason) when it is unusable."""
    material, source, reason = _material()
    if material is None:
        return None, source, reason
    try:
        key = _decode(material)
    except ValueError:
        key = material.encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        return None, source, (f"owner key is {len(key)} bytes after decoding; "
                              f"at least {MIN_KEY_BYTES} are required")
    return key, source, ""


def owner_authority_status() -> dict:
    """Whether owner-signed acts are possible at all. Never returns key material."""
    key, source, reason = owner_key()
    return {
        "configured": key is not None,
        "source": source,
        "reason": reason,
        "algorithm": ALGORITHM,
        "key_env": KEY_ENV,
        "key_file_env": KEY_FILE_ENV,
        "min_key_bytes": MIN_KEY_BYTES,
        "note": ("Owner acts fail closed while this is unconfigured: no amendment can verify and "
                 "no live authorization can be valid."),
    }


def sign_owner_payload(payload: bytes) -> str:
    """Produce the owner signature for a canonical payload. Owner-side only."""
    key, _source, reason = owner_key()
    if key is None:
        raise OwnerAuthorityError(f"{KEY_NOT_CONFIGURED}: {reason}")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_owner_signature(payload: bytes, signature: str) -> Tuple[bool, str, str]:
    """Verify an owner signature. Returns (ok, code, detail) and never raises."""
    key, _source, reason = owner_key()
    if key is None:
        return False, KEY_NOT_CONFIGURED, f"no owner authority key is configured: {reason}"

    supplied = str(signature or "").strip().lower()
    if not supplied:
        return False, SIGNATURE_REQUIRED, "artifact carries no owner signature"

    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return False, SIGNATURE_INVALID, "owner signature does not match the artifact contents"
    return True, SIGNATURE_VALID, "owner signature verified"
