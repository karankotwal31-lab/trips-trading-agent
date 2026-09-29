"""Run every Trip's test suite and report an aggregate result.

``tests/run_tests.py`` runs only ``test_engine.py``. The Student, cloud-shell and execution-layer
suites were therefore never exercised together, and no CI workflow ran them all. This runner
closes that gap: it verifies the suite-integrity manifest, then runs every suite as an isolated
subprocess (they mutate module globals, so in-process execution would leak state).

Run:
    python tests/run_all_tests.py
    python tests/run_all_tests.py --skip-integrity   # only while developing the suites
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from suite_integrity import SuiteIntegrityError, verify  # noqa: E402

#: (label, command, extra environment)
SUITES = (
    ("engine", [sys.executable, "tests/run_tests.py"], {}),
    ("student-engine", [sys.executable, "tests/test_student_engine.py"], {}),
    ("student-integration", [sys.executable, "tests/test_student_integration.py"], {}),
    ("execution-layer", [sys.executable, "tests/test_execution_layer.py"], {}),
    ("task-safety-kernel", [sys.executable, "tests/test_task_safety_kernel.py"], {}),
    ("commodity-readiness", [sys.executable, "tests/test_commodity_readiness.py"], {}),
    ("live-gate-amendment", [sys.executable, "tests/test_live_gate_amendment.py"], {}),
    ("live-readiness", [sys.executable, "tests/test_live_readiness.py"], {}),
    ("execution-evidence", [sys.executable, "tests/test_execution_evidence.py"], {}),
    ("live-dry-run", [sys.executable, "tests/test_live_dry_run.py"], {}),
    ("cloud-shell", [sys.executable, "infra/tests/test_cloud_shell.py"],
     {"PYTHONPATH": str(ROOT / "infra")}),
)


def run() -> int:
    import os

    results = []
    if "--skip-integrity" not in sys.argv[1:]:
        try:
            manifest = verify()
            results.append({"suite": "suite-integrity", "passed": True,
                            "detail": manifest["manifest_hash"][:12]})
        except SuiteIntegrityError as error:
            results.append({"suite": "suite-integrity", "passed": False, "detail": str(error)})
            print(json.dumps({"ok": False, "results": results}, indent=2, sort_keys=True))
            return 2

    for label, command, extra_env in SUITES:
        env = {**os.environ, **extra_env}
        completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        tail = [line for line in completed.stdout.strip().splitlines() if line.strip()]
        results.append({"suite": label, "passed": completed.returncode == 0,
                        "detail": tail[-1] if tail else ""})
        if completed.returncode != 0:
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)

    failed = [entry["suite"] for entry in results if not entry["passed"]]
    print(json.dumps({"ok": not failed, "suites": len(results),
                      "failed": failed, "results": results}, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(run())
