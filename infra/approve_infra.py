from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
FILES = (
    "infra/cloud_state.py",
    "infra/neon_state.py",
    "infra/cloud_cycle.py",
    "infra/heartbeat.py",
    "infra/neon_schema.sql",
    "infra/requirements-cloud.txt",
    "infra/core_v06.sha256",
    # `trips-cloud-paper.yml` was retired when paper trading left the architecture. Its two
    # non-trading health checks - infrastructure manifest verification and frozen-core hash
    # verification - now live in `trips-live-readiness.yml`, which performs no trading of any kind.
    ".github/workflows/trips-live-readiness.yml",
    ".github/workflows/trips-daily-deep.yml",
)
files = {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
body = {"schema_version":2,"files":files}
raw = json.dumps(body,sort_keys=True,separators=(",",":")).encode()
body["manifest_hash"] = hashlib.sha256(raw).hexdigest()
(ROOT/"infra/approved_infra.json").write_text(json.dumps(body,indent=2,sort_keys=True)+"\n")
print(body["manifest_hash"])
