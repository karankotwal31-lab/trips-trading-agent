# Owner reapproval checklist — P003

## Pinned files proposed to change

- engine/config.json
- engine/config_guard.py
- engine/risk.py
- engine/forge_agent.py
- engine/execution/preflight.py
- tests/test_engine.py
- infra/core_v06.sha256 (owner regeneration required after review)

## Approval artifacts that will change

- engine/approved_config.sha256
- engine/approved_build.json
- tests/approved_tests.json
- infra/core_v06.sha256
- infra/approved_infra.json

## Owner-run sequence

1. Apply patch.diff on a clean reviewed branch.
2. Run targeted risk/config tests or python tests/test_engine.py.
3. Review the 20% correlated cap and SPY/QQQ/AAPL grouping as an owner risk decision.
4. Run yourself: PYTHONPATH=engine python engine/approve_config.py
5. Regenerate infra/core_v06.sha256 yourself using the repository's exact existing file order after
   reviewing the core changes. Do not accept a partial hash list.
6. Run yourself: PYTHONPATH=engine python engine/approve_build.py
7. Run yourself: python infra/approve_infra.py
8. Run yourself: python tests/suite_integrity.py --approve
9. Run sh ./scripts/verify_all.sh.
10. Review every regenerated manifest/config fingerprint diff before commit.

The agent must never run any of the approval or hash-regeneration commands.
