from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from chaos_diagnostics import run_chaos
from health_engine import run_health_check
from security_diagnostics import run_security_diagnostics
from store import write_json

ROOT=Path(__file__).resolve().parent.parent


def now_iso(): return datetime.now(timezone.utc).isoformat()


def _cmd(name, argv, timeout):
    try:
        p=subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return {"name":name,"passed":p.returncode==0,"returncode":p.returncode,"tail":"\n".join(p.stdout.splitlines()[-25:])}
    except subprocess.TimeoutExpired as e:
        return {"name":name,"passed":False,"returncode":None,"tail":f"timeout after {timeout}s"}
    except Exception as e:
        return {"name":name,"passed":False,"returncode":None,"tail":f"{type(e).__name__}: {e}"}


def run_deep_diagnostics() -> dict:
    health=run_health_check("daily", apply_repairs=True, persist=True)
    commands=[
        _cmd("compile", [sys.executable,"-m","compileall","-q","engine","tests"],60),
        _cmd("safety_tests", [sys.executable,"tests/run_tests.py"],180),
        _cmd("synthetic_causal_stress", [sys.executable,"engine/backtest.py"],180),
    ]
    chaos=run_chaos()
    security=run_security_diagnostics()
    passed=health["status"]!="HALT" and all(x["passed"] for x in commands) and chaos["passed"] and security["passed"]
    report={
        "schema_version":1,"project":"Trip's","engine":"Trip's Guardian Deep Diagnostics","generated_at":now_iso(),
        "passed":passed,"health_status":health["status"],"commands":commands,"chaos":chaos,"security":security,
        "action":"CONTINUE_PAPER_OBSERVATION" if passed else "HALT_AND_ESCALATE"
    }
    write_json("deep_diagnostics_latest.json",report)
    return report


if __name__ == "__main__":
    r=run_deep_diagnostics()
    print(json.dumps({"passed":r["passed"],"action":r["action"]},indent=2))
    raise SystemExit(0 if r["passed"] else 2)
