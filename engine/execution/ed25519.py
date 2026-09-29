"""Ed25519 signature primitive (RFC 8032), standard library only.

Why this lives in-tree: the repository has no third-party crypto dependency, CI installs nothing
beyond ``actions/setup-python``, and an owner trust root must not depend on a package that may or
may not be present on the runner. This is the published reference construction from RFC 8032,
kept deliberately small and auditable.

Scope, stated honestly:

* The **runtime** only ever calls :func:`verify`. It never holds a private key and contains no
  call site that signs.
* :func:`sign` and :func:`generate_keypair` exist solely so the owner-side script can produce
  signatures offline. Nothing under ``engine/execution/`` invokes them.
* This is the reference construction over Python integers. It is **not** constant-time and must
  never be used for secret-key operations inside the trading process. Verification operates on
  public data only, so timing leakage there is not a confidentiality risk.

The published RFC 8032 empty-message vector is asserted by ``tests/test_ed25519.py``.
"""

from __future__ import annotations

import hashlib
from typing import Tuple

_BITS = 256
_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493

_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _bit(data: bytes, index: int) -> int:
    return (data[index // 8] >> (index % 8)) & 1


def _recover_x(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_BASE_Y = 4 * pow(5, _Q - 2, _Q)
_BASE_X = _recover_x(_BASE_Y)
_BASE = (_BASE_X % _Q, _BASE_Y % _Q)


def _on_curve(point: Tuple[int, int]) -> bool:
    x, y = point
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _Q == 0


def _add(p: Tuple[int, int], r: Tuple[int, int]) -> Tuple[int, int]:
    x1, y1 = p
    x2, y2 = r
    x3 = (x1 * y2 + x2 * y1) * pow(1 + _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    y3 = (y1 * y2 + x1 * x2) * pow(1 - _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    return x3 % _Q, y3 % _Q


def _scal(point: Tuple[int, int], scalar: int) -> Tuple[int, int]:
    """Double-and-add. Recursion depth is bounded by the 256-bit scalar."""
    if scalar == 0:
        return 0, 1
    half = _scal(point, scalar // 2)
    acc = _add(half, half)
    if scalar % 2:
        return _add(acc, point)
    return acc


def _encode(point: Tuple[int, int]) -> bytes:
    x, y = point
    bits = [(y >> i) & 1 for i in range(_BITS - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_BITS // 8))


def _decode(data: bytes) -> Tuple[int, int]:
    if len(data) != 32:
        raise ValueError("point must be 32 bytes")
    sign = _bit(data, _BITS - 1)
    y = sum(_bit(data, i) << i for i in range(_BITS - 1))
    if y >= _Q:
        raise ValueError("y coordinate out of range")
    x = _recover_x(y)
    if x & 1 != sign:
        x = _Q - x
    point = (x % _Q, y)
    if not _on_curve(point):
        raise ValueError("point is not on curve")
    return point


def _expand(private_key: bytes) -> int:
    """The private scalar ``a``: this is where clamping belongs."""
    digest = _sha512(private_key)
    return 2 ** (_BITS - 2) + sum(2 ** i * _bit(digest, i) for i in range(3, _BITS - 2))


def _reduce(data: bytes) -> int:
    """RFC 8032 scalar reduction: interpret H(data) as a little-endian integer, reduce mod L.

    Deliberately NOT clamped. Clamping is a property of the private scalar only; using it for the
    nonce or the challenge produces signatures that are self-consistent but not RFC-compliant, so
    any other verifier would reject them.
    """
    return int.from_bytes(_sha512(data), "little") % _L


def generate_keypair(seed: bytes) -> Tuple[bytes, bytes]:
    """Derive ``(private_key, public_key)`` from exactly 32 bytes of seed material."""
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    public = _encode(_scal(_BASE, _expand(seed)))
    return seed, public


def sign(message: bytes, private_key: bytes) -> bytes:
    """Produce a detached signature. Owner-side only; the runtime never calls this."""
    if len(private_key) != 32:
        raise ValueError("Ed25519 private key must be exactly 32 bytes")
    digest = _sha512(private_key)
    public = _encode(_scal(_BASE, _expand(private_key)))
    nonce = _reduce(digest[32:] + message)
    r_point = _encode(_scal(_BASE, nonce))
    challenge = _reduce(r_point + public + message)
    s = (nonce + challenge * _expand(private_key)) % _L
    return r_point + s.to_bytes(32, "little")


def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """Structural verification. Returns False for any malformed input; never raises."""
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not isinstance(public_key, (bytes, bytearray)) or len(public_key) != 32:
        return False
    signature = bytes(signature)
    public_key = bytes(public_key)
    try:
        point = _decode(public_key)
        r_point = _decode(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    challenge = _reduce(signature[:32] + public_key + message)
    return _scal(_BASE, s) == _add(r_point, _scal(point, challenge))
