"""Suite and gate integrity for Trip's safety checks.

The frozen core and the executable build are hash-pinned, but the TEST SUITE that substantiates
every safety claim was not. That meant a safety test could be quietly weakened without tripping
``EXECUTABLE_BUILD_LOCK``. Nor was the gate itself pinned, which is worse: weakening
``scripts/verify_all.sh`` would make every assertion behind it vacuous while the suite files
stayed byte-identical.

This module pins the suites, the runners and the gate to ``tests/approved_tests.json``. It is a
tripwire, not a cryptographic guarantee: like ``approved_build.json`` it must be regenerated with
``--approve`` when something legitimately changes, which makes any weakening an explicit,
reviewable commit instead of a silent edit.

Run:
    python tests/suite_integrity.py            # verify
    python tests/suite_integrity.py --approve  # re-approve after review
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).resolve().parent / "approved_tests.json"

#: Every file whose integrity justifies a safety claim: the suites, the runners, and the GATE
#: ITSELF. Pinning the gate matters as much as pinning the tests - a weakened gate makes every
#: assertion behind it vacuous, and the CI workflow is the gate's other entry point.
PINNED_FILES = (
    "tests/run_tests.py",
    "tests/run_all_tests.py",
    "tests/suite_integrity.py",
    "tests/test_engine.py",
    "tests/test_student_engine.py",
    "tests/test_student_integration.py",
    "tests/test_execution_layer.py",
    "tests/test_live_gate_amendment.py",
    "tests/test_live_readiness.py",
    "infra/tests/test_cloud_shell.py",
    "scripts/verify_all.sh",
    "scripts/githooks/pre-push",
    ".github/workflows/trips-safety-suites.yml",
)


class SuiteIntegrityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_manifest() -> dict:
    files = {}
    for relative in PINNED_FILES:
        path = ROOT / relative
        if not path.exists():
            raise SuiteIntegrityError(f"pinned suite file missing: {relative}")
        files[relative] = _sha256(path)
    body = {"schema_version": 1, "files": files}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["manifest_hash"] = hashlib.sha256(raw).hexdigest()
    return body


def verify(manifest_path: Path | None = None) -> dict:
    manifest_path = manifest_path or MANIFEST
    if not manifest_path.exists():
        raise SuiteIntegrityError("approved_tests.json missing; the safety suites are not approved")
    try:
        approved = json.loads(manifest_path.read_text())
    except Exception as exc:
        raise SuiteIntegrityError("approved test manifest is unreadable") from exc
    current = current_manifest()
    if approved != current:
        approved_files = approved.get("files", {}) if isinstance(approved, dict) else {}
        changed = [name for name, digest in current["files"].items()
                   if approved_files.get(name) != digest]
        missing = [name for name in approved_files if name not in current["files"]]
        raise SuiteIntegrityError(
            f"unapproved safety-suite drift detected; changed={changed}, missing={missing}")
    return current


def approve() -> dict:
    manifest = current_manifest()
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


if __name__ == "__main__":
    if "--approve" in sys.argv[1:]:
        written = approve()
        print(json.dumps({"approved": True, "manifest_hash": written["manifest_hash"],
                          "files": len(written["files"])}, sort_keys=True))
    else:
        try:
            verified = verify()
        except SuiteIntegrityError as error:
            print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
            raise SystemExit(2)
        print(json.dumps({"ok": True, "manifest_hash": verified["manifest_hash"],
                          "files": len(verified["files"])}, sort_keys=True))
