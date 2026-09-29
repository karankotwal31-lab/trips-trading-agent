"""WP7 RFC 8032 Ed25519 vector tests.

Read-only import of engine/execution/ed25519.py is explicitly allowed by the owner specification.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_EXEC = ROOT / "engine" / "execution"
if str(ENGINE_EXEC) not in sys.path:
    sys.path.insert(0, str(ENGINE_EXEC))

import ed25519  # noqa: E402


VECTORS = (
    {
        "public_key": "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "message": "",
        "signature": (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    },
    {
        "public_key": "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "message": "72",
        "signature": (
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    },
    {
        "public_key": "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "message": "af82",
        "signature": (
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    },
)


def test_rfc8032_vectors_verify():
    for vector in VECTORS:
        assert ed25519.verify(
            bytes.fromhex(vector["message"]),
            bytes.fromhex(vector["signature"]),
            bytes.fromhex(vector["public_key"]),
        )


def test_tampered_message_and_signature_rejected():
    v = VECTORS[1]
    message = bytes.fromhex(v["message"])
    signature = bytearray.fromhex(v["signature"])
    public = bytes.fromhex(v["public_key"])
    assert not ed25519.verify(message + b"\x00", bytes(signature), public)
    signature[0] ^= 1
    assert not ed25519.verify(message, bytes(signature), public)


def test_noncanonical_s_malleability_rejected():
    v = VECTORS[0]
    sig = bytes.fromhex(v["signature"])
    s = int.from_bytes(sig[32:], "little")
    noncanonical = sig[:32] + (s + ed25519._L).to_bytes(32, "little")
    assert not ed25519.verify(
        bytes.fromhex(v["message"]),
        noncanonical,
        bytes.fromhex(v["public_key"]),
    )


def test_malformed_lengths_rejected_without_exception():
    assert not ed25519.verify(b"x", b"", b"")
    assert not ed25519.verify(b"x", b"0" * 64, b"0" * 31)


if __name__ == "__main__":
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failed = []
    for test in tests:
        try:
            test()
            print("PASS", test.__name__)
        except Exception:
            failed.append(test.__name__)
            print("FAIL", test.__name__)
            traceback.print_exc()
    if failed:
        raise SystemExit(f"{len(failed)} failures: {failed}")
    print(f"ALL PASS ({len(tests)} RFC8032 tests)")
