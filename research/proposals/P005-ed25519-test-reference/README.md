# P005 — Ed25519 test-reference correction

## Rationale

The pinned Ed25519 module says its published RFC 8032 vector is asserted by
tests/test_ed25519.py, but that file does not exist.

WP7 already added the real research vector suite at research/tests/test_ed25519_vectors.py,
covering published vectors, message/signature tampering, malformed lengths and non-canonical S.

This proposal corrects the pinned docstring and adds a pinned regression assertion that the
referenced vector suite exists.

## Risk

No cryptographic algorithm, key handling, signing, verification or authority behavior changes.
The only execution-layer change is documentation.

## Fail-closed behavior

The pinned test fails if the documented vector-test path disappears or the obsolete nonexistent
path returns. Existing RFC 8032 behavior tests remain unchanged.
