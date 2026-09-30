# Owner reapproval checklist — P001

Do not run these commands until the patch, targeted tests and full review are accepted.

## Pinned files proposed to change

- engine/execution/strategy_evidence.py (new, covered by CRITICAL_DIRECTORIES)
- engine/execution/readiness.py
- engine/execution/lifecycle.py
- engine/execution/preflight.py
- engine/execution/status.py
- tests/test_live_readiness.py
- tests/test_execution_evidence.py

## Approval artifacts that will change

- engine/approved_build.json
- tests/approved_tests.json

No config or infrastructure-manifest change is proposed.

## Owner-run sequence

1. Apply patch.diff on a clean reviewed branch.
2. Run targeted tests: python tests/test_live_readiness.py and python tests/test_execution_evidence.py.
3. Confirm missing strategy evidence leaves readiness locked.
4. Run yourself: PYTHONPATH=engine python engine/approve_build.py
5. Run yourself: python tests/suite_integrity.py --approve
6. Run sh ./scripts/verify_all.sh.
7. Review regenerated manifest diffs before commit.

The agent must never run the approval commands.
