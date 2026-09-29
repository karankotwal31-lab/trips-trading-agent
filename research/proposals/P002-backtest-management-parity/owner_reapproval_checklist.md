# Owner reapproval checklist — P002

## Pinned files proposed to change

- engine/backtest.py
- tests/test_engine.py
- infra/core_v06.sha256 (owner regeneration required because engine/backtest.py is in the frozen-core hash list)

## Approval artifacts that will change

- engine/approved_build.json
- tests/approved_tests.json
- infra/core_v06.sha256
- infra/approved_infra.json

No config fingerprint change is proposed.

## Owner-run sequence

1. Apply patch.diff on a clean reviewed branch.
2. Run the targeted parity test or python tests/test_engine.py.
3. Review changed synthetic/backtest outputs without tuning thresholds to improve them.
4. Regenerate infra/core_v06.sha256 yourself using the repository's exact existing file order after
   reviewing the backtest change. Do not accept a partial hash list.
5. Run yourself: PYTHONPATH=engine python engine/approve_build.py
6. Run yourself: python infra/approve_infra.py
7. Run yourself: python tests/suite_integrity.py --approve
8. Run sh ./scripts/verify_all.sh.
9. Review all regenerated manifest/hash diffs before commit.

The agent must never run the approval or hash-regeneration commands.
