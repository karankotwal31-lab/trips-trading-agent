from __future__ import annotations
import subprocess, sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def run(cmd):
    print("+"," ".join(cmd),flush=True)
    subprocess.run(cmd,cwd=ROOT,check=True)

def main():
    run(["sha256sum","-c","infra/core_v06.sha256"])
    run([sys.executable,"infra/verify_infra.py"])
    for script in ("tests/run_tests.py", "infra/tests/test_cloud_shell.py",
                   "tests/test_student_engine.py", "tests/test_student_integration.py"):
        run([sys.executable, script])
    suite=unittest.defaultTestLoader.discover(str(ROOT/"live_execution"/"tests"),pattern="test_*.py")
    if suite.countTestCases() == 0:
        raise SystemExit("No execution foundation tests discovered")
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    print("LIVE_EXECUTION_FOUNDATION_VERIFIED")

if __name__=="__main__": main()
