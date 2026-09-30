# Owner reapproval checklist — P004

## Pinned files proposed to change

- docs/TRIPS_CONSTITUTION.md
- engine/capability_registry.json
- engine/execution/lifecycle.py (docstring only)
- engine/execution/readiness.py (operator note only)
- engine/execution/status.py (operator note only)
- .github/workflows/trips-agent.yml (comment only)
- .github/workflows/trips-live-readiness.yml (comment only)
- tests/test_engine.py

engine/config.json is deliberately not changed; mode remains paper.

## Approval artifacts that will change

- engine/approved_build.json
- tests/approved_tests.json
- infra/approved_infra.json (because trips-live-readiness.yml is pinned there)

infra/core_v06.sha256 and engine/approved_config.sha256 do not need regeneration for this proposal.

## Owner-run sequence

1. Apply patch.diff on a clean reviewed branch.
2. Run the targeted wording test or python tests/test_engine.py.
3. Confirm PAPER_FIRST still explicitly prohibits live-money submission while in force.
4. Run yourself: PYTHONPATH=engine python engine/approve_build.py
5. Run yourself: python infra/approve_infra.py
6. Run yourself: python tests/suite_integrity.py --approve
7. Run sh ./scripts/verify_all.sh.
8. Review all regenerated manifest diffs before commit.

The agent must never run the approval commands.
