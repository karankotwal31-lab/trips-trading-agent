# Owner reapproval checklist — P005

## Pinned files proposed to change

- engine/execution/ed25519.py (docstring only)
- tests/test_live_readiness.py

## Approval artifacts that will change

- engine/approved_build.json
- tests/approved_tests.json

No config, core-v0.6 hash list or infrastructure manifest change is proposed.

## Owner-run sequence

1. Apply patch.diff on a clean reviewed branch.
2. Run the targeted docstring test or python tests/test_live_readiness.py.
3. Confirm no cryptographic code changed.
4. Run yourself: PYTHONPATH=engine python engine/approve_build.py
5. Run yourself: python tests/suite_integrity.py --approve
6. Run sh ./scripts/verify_all.sh.
7. Review manifest diffs before commit.

The agent must never run the approval commands.
