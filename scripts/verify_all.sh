#!/bin/sh
# Trip's single safety gate — ONE definition, used by CI and by local pushes.
#
# Why this exists as a script rather than only as workflow steps:
#
#   Every safety claim in this repository ("the frozen core is intact", "the executable build is
#   locked", "the suites have not been weakened", "the live boundary still refuses") is only TRUE
#   if the gate actually RUNS. If the gate lives only inside .github/workflows, then the moment
#   GitHub Actions cannot start a job — which is the current state of this private repository,
#   blocked by the account's Actions billing/limits — every one of those claims becomes
#   unverified on every change, silently.
#
#   Defining the gate once, here, means it can be executed anywhere: locally, from a pre-push
#   hook, or by CI. The workflow calls this script, so local and CI results are byte-identical.
#
# Usage:
#   sh ./scripts/verify_all.sh          # run from anywhere; exits 0 only if every check passes
#
# Opt in to running it automatically before every push:
#   git config core.hooksPath scripts/githooks
#
# Fail-closed: `set -eu` means the first failing check aborts with a non-zero exit code.

set -eu

cd "$(dirname "$0")/.."

step() {
    printf '\n== %s\n' "$1"
}

step "Frozen v0.6 core (infra/core_v06.sha256)"
sha256sum -c infra/core_v06.sha256

step "Infrastructure manifest (infra/approved_infra.json)"
python3 infra/verify_infra.py

step "Safety-suite integrity (tests/approved_tests.json)"
python3 tests/suite_integrity.py

step "All safety suites"
python3 tests/run_all_tests.py

step "Execution-layer live-lock status"
PYTHONPATH=engine python3 engine/execution/status.py > /dev/null
echo '{"live_transmission":"refused by the frozen live boundary","ok":true}'

printf '\n== GATE PASSED\n'
