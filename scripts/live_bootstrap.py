"""Run Trip's non-mutating production bootstrap.

Default mode is offline inspection only. Pass --verify-external explicitly to allow read-only
requests to the configured live broker account and production market-data providers.

This command never constructs the execution gateway and never places, cancels, replaces or
modifies an order.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from execution.production_bootstrap import production_bootstrap_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-external", action="store_true",
        help="perform read-only broker/account and market-data verification")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero unless external verification and owner inputs are complete")
    args = parser.parse_args()

    report = production_bootstrap_status(verify_external=args.verify_external)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))

    if args.strict:
        if not report["external_verification_complete"]:
            return 2
        if not report["owner_inputs_complete"]:
            return 3
        if report["releases_capital"] or report["places_orders"]:
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
