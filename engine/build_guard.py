from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "approved_build.json"

# Files capable of changing data truth, analysis, risk, state, execution simulation, or runtime policy.
CRITICAL_FILES = (
    "config.json",
    "config_guard.py",
    "constitution.py",
    "market_time.py",
    "providers.py",
    "truth_guard.py",
    "indicators.py",
    "strategies.py",
    "candle_intelligence.py",
    "risk.py",
    "store.py",
    "forge_agent.py",
    "backtest.py",
    "build_guard.py",
    "ack_escalation.py",
    "health_engine.py",
    "health_daemon.py",
    "evolution_engine.py",
    "supervisor_bridge.py",
    "decision_mirror.py",
    "redaction.py",
    "supervisor_relay.py",
    "supervisor_counsel.py",
    "migrate_runtime_v04_to_v05.py",
    "migrate_runtime_v05_to_v06.py",
    "diagnostics_engine.py",
    "chaos_diagnostics.py",
    "security_diagnostics.py",
    "dashboard_export.py",
    "dashboard_server.py",
    "capability_registry.json",
    # The owner public verification key. Public material, but substituting it would let anyone
    # forge an owner act, so it is as integrity-critical as the Constitution itself.
    "owner_public_key.json",
)

#: Directories whose every module is integrity-critical. The additive execution layer is the
#: real-money path: an edit to its gateway, its conformance evidence or its channel mapping changes
#: what may be transmitted, so it is covered by the same manifest as the frozen core's neighbours.
#: Without this, ``execution/conformance.py`` could be edited to answer SUPPORTED unconditionally
#: and every downstream digest would still be internally consistent.
CRITICAL_DIRECTORIES = ("execution",)


# Operator-visible truth claims are part of the reviewed trust surface even though they have
# zero execution authority. A modified dashboard must therefore trip build integrity too.
CRITICAL_PROJECT_FILES = (
    "docs/index.html",
    "docs/styles.css",
    "docs/app.js",
    "docs/assets/trips-portrait.png",
    "docs/TRIPS_CONSTITUTION.md",
    # The owner-side signer handles the private trust root. A modified copy could sign a different
    # artifact or mishandle key material, so the reviewed signer is part of executable integrity.
    "scripts/sign_owner_artifact.py",
    # Production bootstrap can inspect real broker/data credentials. It is read-only by design,
    # and pinning it prevents a modified operator tool from quietly widening its authority.
    "scripts/live_bootstrap.py",
)


class BuildIntegrityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def current_manifest(root: Path | None = None) -> Dict[str, object]:
    engine_root = root or HERE
    project_root = engine_root.parent
    files = {}
    for name in CRITICAL_FILES:
        p = engine_root / name
        if not p.exists() or not p.is_file():
            raise BuildIntegrityError(f"critical build file missing: engine/{name}")
        files[f"engine/{name}"] = _sha256(p)
    for directory in CRITICAL_DIRECTORIES:
        root_dir = engine_root / directory
        if not root_dir.is_dir():
            raise BuildIntegrityError(f"critical build directory missing: engine/{directory}")
        modules = sorted(path for path in root_dir.glob("*.py") if path.is_file())
        if not modules:
            raise BuildIntegrityError(f"critical build directory is empty: engine/{directory}")
        for path in modules:
            rel = f"engine/{directory}/{path.name}"
            files[rel] = _sha256(path)
    for rel in CRITICAL_PROJECT_FILES:
        p = project_root / rel
        if not p.exists() or not p.is_file():
            raise BuildIntegrityError(f"critical operator-surface file missing: {rel}")
        files[rel] = _sha256(p)
    manifest_body = {"schema_version": 2, "files": files}
    raw = json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_body["manifest_hash"] = hashlib.sha256(raw).hexdigest()
    return manifest_body


def verify_build_integrity(manifest_path: Path | None = None, root: Path | None = None) -> dict:
    manifest_path = manifest_path or MANIFEST_PATH
    if not manifest_path.exists():
        raise BuildIntegrityError("approved_build.json missing; executable build is not approved")
    try:
        approved = json.loads(manifest_path.read_text())
    except Exception as e:
        raise BuildIntegrityError("approved build manifest is unreadable") from e
    current = current_manifest(root)
    if approved != current:
        approved_files = approved.get("files", {}) if isinstance(approved, dict) else {}
        changed = [name for name, digest in current["files"].items() if approved_files.get(name) != digest]
        missing = [name for name in approved_files if name not in current["files"]]
        raise BuildIntegrityError(f"unapproved executable build drift detected; changed={changed}, missing={missing}")
    return current
