"""Run every research-only test module in an isolated subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main() -> int:
    tests = sorted(p for p in HERE.glob("test_*.py") if p.name != Path(__file__).name)
    results = []
    for path in tests:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
        for line in stdout_lines:
            if line.startswith("SCENARIO_BIAS_REPORT="):
                print(line)
        tail = stdout_lines
        results.append({
            "test": path.name,
            "passed": proc.returncode == 0,
            "detail": tail[-1] if tail else "",
        })
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
    failed = [r["test"] for r in results if not r["passed"]]
    print(json.dumps({"ok": not failed, "failed": failed, "results": results}, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
