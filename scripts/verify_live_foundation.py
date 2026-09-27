from __future__ import annotations
import subprocess, sys, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run(cmd):
    print("+"," ".join(cmd),flush=True)
    subprocess.run(cmd,cwd=ROOT,check=True)

def main():
    run(["sha256sum","-c","infra/core_v06.sha256"])
    run([sys.executable,"infra/verify_infra.py"])
    suite=unittest.defaultTestLoader.discover(str(ROOT/"live_execution"/"tests"),pattern="test_*.py",top_level_dir=str(ROOT))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    print("LIVE_EXECUTION_FOUNDATION_VERIFIED")

if __name__=="__main__": main()
