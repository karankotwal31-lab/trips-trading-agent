from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from cloud_state import CloudStateError
from neon_state import NeonStateClient


def main() -> None:
    client = NeonStateClient()
    runtime = client.fetch_runtime()
    if runtime is None:
        raise CloudStateError("authoritative runtime missing")
    age = None
    if runtime.updated_at:
        try:
            dt = datetime.fromisoformat(runtime.updated_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        except Exception:
            age = None
    status = "PASS" if age is not None and age < 3 * 3600 else "DEGRADED"
    detail = {"runtime_version": runtime.version, "updated_at": runtime.updated_at,
              "age_seconds": age, "core": "v0.6-frozen", "shell": "v0.7"}
    client.write_heartbeat("guardian_heartbeat", status, detail)
    print(json.dumps({"ok": True, "status": status, **detail}, sort_keys=True))
    if status != "PASS":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
