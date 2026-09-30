#!/usr/bin/env python3
"""Fail-closed changed-path guard for research PRs.

PR-1 is deliberately narrow. Future work packages must update this Class-A file in their own
reviewed branch before they can pass.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

PROFILES = {
    "pr1": {
        "prefixes": ("research/",),
        "exact": (".github/workflows/research-validation.yml",),
    },
    "pr2": {
        "prefixes": ("research/shadow/", "deploy/shadow/"),
        "exact": (
            ".github/workflows/research-validation.yml",
            "research/tools/check_scope.py",
            "research/tests/test_shadow_runner.py",
        ),
    },
    "pr4": {
        "prefixes": ("research/design/", "research/proposals/"),
        "exact": (
            ".github/workflows/research-validation.yml",
            "research/tools/check_scope.py",
            "research/tools/validate_proposals.py",
            "research/tests/test_pr4_design.py",
        ),
    },
    "pr5": {
        "prefixes": ("research/data_ingest/",),
        "exact": (
            ".github/workflows/research-market-data.yml",
            ".github/workflows/research-validation.yml",
            "research/tools/check_scope.py",
            "research/tests/test_data_ingest_contract.py",
        ),
    },
}


def changed_paths(base: str = "origin/main") -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed closed: {proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def path_allowed(path: str, profile: str) -> bool:
    rules = PROFILES[profile]
    return path in rules["exact"] or any(path.startswith(prefix) for prefix in rules["prefixes"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args()
    try:
        paths = changed_paths(args.base)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    violations = [p for p in paths if not path_allowed(p, args.profile)]
    print(f"profile={args.profile} changed_files={len(paths)}")
    for path in paths:
        print(f"{'OK' if path not in violations else 'BLOCK'} {path}")
    if violations:
        print(f"scope violation: {violations}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
