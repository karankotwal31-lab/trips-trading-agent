from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

from cloud_state import CloudStateError, payload_sha256
from neon_state import NeonStateClient

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
DATA = ROOT / "runtime_data"
CORE_FREEZE = Path(__file__).with_name("core_v06.sha256")


def verify_core_freeze() -> None:
    expected = {}
    for line in CORE_FREEZE.read_text().splitlines():
        digest, rel = line.split(maxsplit=1)
        expected[rel.strip()] = digest
    changed = []
    for rel, digest in expected.items():
        path = ROOT / rel
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(rel)
    if changed:
        raise CloudStateError(f"frozen v0.6 core drift detected: {changed}")


def clean_runtime_dir() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for p in DATA.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)


def hydrate_runtime(payload: dict) -> None:
    clean_runtime_dir()
    raw_path = DATA / "runtime_snapshot.json"
    raw_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    (DATA / "runtime_snapshot.json.sha256").write_text(payload_sha256(payload) + "\n")
    (DATA / "runtime_initialized.marker").write_text("Hydrated from authoritative Neon runtime by Trip's cloud shell.\n")


def read_local_runtime() -> dict:
    path = DATA / "runtime_snapshot.json"
    sidecar = DATA / "runtime_snapshot.json.sha256"
    if not path.exists() or not sidecar.exists():
        raise CloudStateError("core cycle did not produce authoritative runtime snapshot")
    payload = json.loads(path.read_text())
    if payload_sha256(payload) != sidecar.read_text().strip():
        raise CloudStateError("local authoritative runtime checksum mismatch after cycle")
    return payload


def run_core_cycle() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE)
    proc = subprocess.run([sys.executable, str(ENGINE / "forge_agent.py")], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        raise CloudStateError(f"frozen core cycle failed rc={proc.returncode}; stderr={proc.stderr[-2000:]}")


def export_dashboard() -> tuple[dict, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE)
    proc = subprocess.run([sys.executable, str(ENGINE / "dashboard_export.py")], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise CloudStateError(f"dashboard export failed rc={proc.returncode}; stderr={proc.stderr[-2000:]}")
    path = ROOT / "docs" / "data" / "state.json"
    sidecar = ROOT / "docs" / "data" / "state.json.sha256"
    if not path.exists() or not sidecar.exists():
        raise CloudStateError("dashboard exporter did not produce signed snapshot")
    raw = path.read_bytes()
    digest = sidecar.read_text().strip()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise CloudStateError("dashboard exporter raw-byte checksum mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CloudStateError(f"dashboard exporter produced invalid JSON: {type(exc).__name__}") from exc
    canonical_digest = payload_sha256(payload)
    return payload, canonical_digest


def owner_id() -> str:
    supplied = os.getenv("TRIPS_CLOUD_RUN_ID", "").strip()
    if supplied:
        return supplied[:180]
    return f"{socket.gethostname()}-{uuid.uuid4().hex}"


def cycle() -> dict:
    verify_core_freeze()
    client = NeonStateClient()
    owner = owner_id()
    lease = client.acquire_lease(owner)
    remote_version = None
    try:
        remote = client.fetch_runtime()
        if remote is None:
            raise CloudStateError("authoritative cloud runtime does not exist; explicit bootstrap required")
        remote_version = remote.version
        hydrate_runtime(remote.payload)
        run_core_cycle()
        updated = read_local_runtime()
        committed = client.commit_runtime(remote.version, updated)
        if committed.version != remote.version + 1:
            raise CloudStateError("cloud runtime version did not advance exactly once")
        dash, dash_hash = export_dashboard()
        client.publish_dashboard_snapshot(dash, dash_hash)
        client.write_heartbeat("cloud_cycle", "PASS", {
            "runtime_version": committed.version,
            "core": "v0.6-frozen",
            "cloud_shell": "v0.7",
            "lease_owner": owner,
        })
        return {"ok": True, "runtime_version": committed.version, "dashboard_sha256": dash_hash,
                "core": "v0.6-frozen", "shell": "v0.7"}
    except Exception as exc:
        try:
            client.write_heartbeat("cloud_cycle", "HALT", {
                "error_type": type(exc).__name__, "runtime_version": remote_version,
                "core": "v0.6-frozen", "cloud_shell": "v0.7",
            })
        except Exception:
            pass
        raise
    finally:
        try:
            client.release_lease(owner)
        except Exception:
            pass


def bootstrap() -> dict:
    verify_core_freeze()
    client = NeonStateClient()
    if client.fetch_runtime() is not None:
        raise CloudStateError("authoritative cloud runtime already exists; bootstrap refused")
    clean_runtime_dir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE)
    code = "from store import initial_runtime, write_runtime; import json; p=initial_runtime(100000.0); write_runtime(p); print(json.dumps(p))"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise CloudStateError("local bootstrap initialization failed")
    payload = read_local_runtime()
    remote = client.bootstrap_runtime(payload)
    return {"ok": True, "runtime_version": remote.version, "runtime_sha256": remote.payload_sha256}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["cycle", "bootstrap"])
    args = parser.parse_args()
    try:
        result = cycle() if args.command == "cycle" else bootstrap()
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:2000]}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
