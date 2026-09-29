# Trip's protected-path history audit

Generated for PR-1 / WP0 against current `main` **923c235250d42d0056eb2f958632cf5fdb5f193d**.

## Current-main safety baseline

The Git tree for current `main` is `9f11a72249d791bde1d6e5bf7cc25c295fdf974c`.
That tree is byte-identical to tested PR head `f162cd5570de58acc94a296c46c872b4183aff57`,
whose **Trips Safety Suites** run `36628940138` executed `sh ./scripts/verify_all.sh`
and completed successfully. This is an exact-tree baseline, not a source-level approximation.

Relevant gate output:

```text
== Frozen v0.6 core (infra/core_v06.sha256)
== Infrastructure manifest (infra/approved_infra.json)
{"ok": true, "manifest_hash": "949df8845ffa1edca9e17fa394437fe7744c9673352ac2414cbcda826f0c3146"}
== Safety-suite integrity (tests/approved_tests.json)
{"files": 19, "manifest_hash": "c07be645f696601492142553e9a1cb0d2a565cc6022913d6def4c4f0d7e6945e", "ok": true}
== All safety suites
"failed": []
"ok": true
== Execution-layer live-lock status
{"live_transmission":"refused by the frozen live boundary","ok":true}
== GATE PASSED
```

The separate **Trips Live Readiness** run `36628940081` on the same tested tree also completed
successfully.

## Protected-path definition used

A path is treated as protected if it is covered by any of:

- `CRITICAL_FILES`, `CRITICAL_DIRECTORIES`, or `CRITICAL_PROJECT_FILES` in `engine/build_guard.py`;
- `tests/approved_tests.json`;
- `infra/approved_infra.json`;
- `infra/core_v06.sha256`;
- approval artifacts `engine/approved_build.json`, `engine/approved_config.sha256`,
  `tests/approved_tests.json`, `infra/approved_infra.json`, and `infra/core_v06.sha256`.

All `engine/execution/*.py` are protected through `CRITICAL_DIRECTORIES`.

## Main-history findings

The current main ancestry contains 70 commits. The table below lists every commit found in that
ancestry that touches at least one protected path. This is an audit only: **nothing is reverted**.

| Commit | UTC date | Author | Message | Protected-path touch | Approval/manifest changed in same commit |
|---|---|---|---|---|---|
| 923c235250d4 | 2026-09-29 | Karan Kotwal | Add five-scenario $40k empire audit and records (#18) | pinned research test + test runner/integrity | tests/approved_tests.json |
| 72e09536ca3a | 2026-09-29 | Karan Kotwal | Add fail-closed commodity readiness layer (#16) | execution commodity modules + test gate | engine/approved_build.json; tests/approved_tests.json |
| 739ae2ee3ba5 | 2026-09-29 | Karan Kotwal | Merge PR #14: production bootstrap and dual-source Truth readiness | execution, live-readiness workflow, signer entrypoint, tests/infra | engine/approved_build.json; infra/approved_infra.json; tests/approved_tests.json |
| e2c44033131d | 2026-09-29 | Karan Kotwal | chore: re-approve deterministic production bootstrap test | test manifest | tests/approved_tests.json |
| 5fa5adbae1aa | 2026-09-29 | Karan Kotwal | test: remove top-of-hour freshness race from dual-source fixture | pinned production-bootstrap test | no |
| 6291b77213c6 | 2026-09-29 | Karan Kotwal | chore: approve per-ref live-readiness concurrency | infra manifest | infra/approved_infra.json |
| db9603060a4a | 2026-09-29 | Karan Kotwal | ci: isolate live-readiness concurrency by ref | pinned live-readiness workflow | no |
| 0db54f3a6749 | 2026-09-29 | Karan Kotwal | chore: approve combined TASK and production bootstrap suites | test manifest | tests/approved_tests.json |
| c3a6238ff749 | 2026-09-29 | Karan Kotwal | chore: approve combined TASK and production bootstrap build | build manifest | engine/approved_build.json |
| 3abb80263365 | 2026-09-29 | Karan Kotwal | merge: pin TASK and production bootstrap suites | tests/suite_integrity.py | no |
| da4a9a2c60af | 2026-09-29 | Karan Kotwal | merge: keep TASK suite and add production bootstrap suite | tests/run_all_tests.py | no |
| daa725e4e02d | 2026-09-29 | Karan Kotwal | replay: add tests/test_production_bootstrap.py | pinned test | no |
| 79dccac5a5b8 | 2026-09-29 | Karan Kotwal | replay: add scripts/trips_external_readiness.py | critical project script | no |
| 0ca40f380e05 | 2026-09-29 | Karan Kotwal | replay: add engine/execution/production_runtime.py | execution | no |
| ebcb85b7b7c4 | 2026-09-29 | Karan Kotwal | replay: add engine/execution/production_data.py | execution | no |
| 3bc4b9880534 | 2026-09-29 | Karan Kotwal | replay: production bootstrap tests/test_live_readiness.py | pinned test | no |
| 64714d8817ef | 2026-09-29 | Karan Kotwal | replay: production bootstrap engine/execution/live_verification.py | execution | no |
| 46f493615b47 | 2026-09-29 | Karan Kotwal | replay: production bootstrap engine/execution/lifecycle.py | execution | no |
| 5a400f69ab31 | 2026-09-29 | Karan Kotwal | replay: production bootstrap engine/execution/channels.py | execution | no |
| 843e5286082b | 2026-09-29 | Karan Kotwal | replay: production bootstrap engine/execution/__init__.py | execution | no |
| 2989f8198c7c | 2026-09-29 | Karan Kotwal | replay: production bootstrap engine/build_guard.py | build guard | no |
| 1e698494b198 | 2026-09-29 | Karan Kotwal | Add TASK autonomous execution safety kernel (#12) | execution TASK/cycle bridge + pinned tests | engine/approved_build.json; tests/approved_tests.json |
| bc635393abd3 | 2026-09-29 | freebuff-web[bot] | Merge pull request #11 … owner-signing-tool regression tests | pinned live-gate test | tests/approved_tests.json |
| 5eaf86617758 | 2026-09-29 | karan kotwal | test: exercise the owner signing CLI as a subprocess | pinned live-gate test | tests/approved_tests.json |
| 81768d9e2094 | 2026-09-29 | Karan Kotwal | Merge PR #9: harden live activation evidence gate | build guard, execution lifecycle/verifier, owner signer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 2dd43c6c9cf8 | 2026-09-29 | Karan Kotwal | chore: approve integrity-pinned owner signer | build manifest | engine/approved_build.json |
| 483d87ca7278 | 2026-09-29 | Karan Kotwal | security: pin the offline owner signer in build integrity | build guard | no |
| 5252c3b0a477 | 2026-09-29 | Karan Kotwal | fix: harden offline owner key generation CLI | owner signer | no |
| 0a45f3c3c3d0 | 2026-09-29 | Karan Kotwal | chore: re-approve lifecycle test integrity | test manifest | tests/approved_tests.json |
| d7d032226836 | 2026-09-29 | Karan Kotwal | test: align legacy lifecycle assertions with fail-closed activation order | pinned execution-layer test | no |
| 490b9b2ff17d | 2026-09-29 | Karan Kotwal | chore: approve live-activation adversarial tests | test manifest | tests/approved_tests.json |
| ae81873d8055 | 2026-09-29 | Karan Kotwal | test: adversarially lock the LIVE_ENABLED activation boundary | pinned live-readiness test | no |
| 31edb13827b7 | 2026-09-29 | Karan Kotwal | chore: approve hardened live-activation execution build | build manifest | engine/approved_build.json |
| a28fa2115638 | 2026-09-29 | Karan Kotwal | fix: verify live data entitlement covers the full mandate | execution live verification | no |
| 4a27f1d62a4a | 2026-09-29 | Karan Kotwal | fix: require fresh bound live-account evidence before activation | execution lifecycle | no |
| fe5bb8ccee9e | 2026-09-29 | freebuff-web[bot] | Merge pull request #8 … live dry run | execution readiness + pinned test gate | engine/approved_build.json; tests/approved_tests.json |
| d9a8ae3ee240 | 2026-09-29 | karan kotwal | test: an end-to-end dry run of the whole canonical live path | execution readiness + pinned test gate | engine/approved_build.json; tests/approved_tests.json |
| a89552f1bffc | 2026-09-29 | freebuff-web[bot] | Merge pull request #7 … permit binding/order read audit | execution gateway/channels/preflight/readiness + tests | engine/approved_build.json; tests/approved_tests.json |
| 8416f5306846 | 2026-09-29 | karan kotwal | fix: bind mutation permits to the broker's account and make order reads real | execution gateway/channels/preflight/readiness + tests | engine/approved_build.json; tests/approved_tests.json |
| 8ab169806b7c | 2026-09-29 | freebuff-web[bot] | Merge pull request #6 … live-money-only execution | execution surface, live-readiness workflow, infra/tests | engine/approved_build.json; infra/approved_infra.json; tests/approved_tests.json |
| 5eb316505760 | 2026-09-29 | karan kotwal | fix: make Trip's a live-money-only execution system | execution surface, live-readiness workflow, infra/tests | engine/approved_build.json; infra/approved_infra.json; tests/approved_tests.json |
| e1dc7b2f840e | 2026-09-29 | freebuff-web[bot] | Merge pull request #5 … evidence-based readiness | build guard, broad execution layer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 3da0b13e4bc8 | 2026-09-29 | karan kotwal | fix: evidence-based readiness, per-capability conformance, and repaired translation | build guard, broad execution layer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 50cf152bf0d6 | 2026-09-29 | karan kotwal | Revert evidence-based readiness… | build guard, broad execution layer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 177f0774a0e0 | 2026-09-29 | karan kotwal | fix: evidence-based readiness, per-capability conformance, and repaired translation | build guard, broad execution layer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| a23bf20e67b5 | 2026-09-29 | freebuff-web[bot] | Merge pull request #4 … live-ready-locked | owner trust root, execution, signer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 1d39e60aeeaa | 2026-09-29 | karan kotwal | feat: asymmetric owner trust root, third frozen blocker, and LIVE_READY_LOCKED | owner trust root, execution, signer, pinned tests | engine/approved_build.json; tests/approved_tests.json |
| 7f78a5cf379a | 2026-09-29 | freebuff-web[bot] | Merge pull request #3 … owner-signature trust root | execution authority/lifecycle/preflight/status + signer/tests | tests/approved_tests.json |
| 9440ea685925 | 2026-09-29 | karan kotwal | fix: make owner authority a signature instead of a forgeable boolean | execution authority/lifecycle/preflight/status + signer/tests | tests/approved_tests.json |
| e80b0611a2fb | 2026-09-29 | freebuff-web[bot] | Merge pull request #2 … live-eligible execution layer | execution layer + safety workflow/scripts/tests | tests/approved_tests.json |
| 4285135c8ce8 | 2026-09-29 | karan kotwal | ci: move checkout and setup-python off deprecated Node runtime | pinned safety workflow | tests/approved_tests.json |
| 07bce319f73d | 2026-09-29 | karan kotwal | ci: define the safety gate once so it runs even when Actions cannot | safety workflow, verify_all, hook, suite integrity | tests/approved_tests.json |
| d72784ae79b6 | 2026-09-29 | karan kotwal | ci: use python3 in the safety-suite gate | pinned safety workflow | no |
| b334018d4277 | 2026-09-29 | karan kotwal | feat: add real-money-capable execution layer without weakening frozen core | execution layer + safety workflow/test gate | tests/approved_tests.json |
| 0a066220ab48 | 2026-09-27 | Karan Kotwal | chore: restore verified v0.8 release file trips-daily-deep.yml | pinned daily-deep workflow | no |
| 6c3eab61ff93 | 2026-09-27 | Karan Kotwal | Add files via upload | protected infra tree and infra tests | infra/approved_infra.json; infra/core_v06.sha256 |
| b045eb1267ac | 2026-09-27 | Karan Kotwal | Add files via upload | initial frozen engine/docs/tests/config trust surface | engine/approved_build.json; engine/approved_config.sha256 |

### Flag: empire-audit commit

Commit `923c235250d4` added `test_records/*` plus
`tests/test_empire_audit_40000_five_scenarios.py` and changed the pinned test runner,
suite-integrity definition, and `tests/approved_tests.json`. Per the owner rule this is flagged
for historical review only; PR-1 does **not** edit, re-approve, or revert it.

## Audit conclusion

The repository has a dense history of legitimate-looking but security-sensitive re-approvals and
protected-path edits. PR-1 therefore treats every frozen/pinned path as immutable and keeps all
implementation under `research/**` plus the new unpinned
`.github/workflows/research-validation.yml`.
