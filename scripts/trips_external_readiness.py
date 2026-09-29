#!/usr/bin/env python3
"""Run Trip's external live-readiness checks without constructing an execution gateway.

This command may authenticate to the configured live broker and market-data providers, but the
broker surface is structurally read-only and the script has no submit/cancel/replace path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from execution.production_runtime import external_readiness_report  # noqa: E402


def main() -> int:
    report = external_readiness_report()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get("verified") else 2


if __name__ == "__main__":
    raise SystemExit(main())
