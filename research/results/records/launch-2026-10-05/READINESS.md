# Trip’s launch readiness — 5 October 2026

**Decision: NO-GO for real-money launch. Research/diagnostics are operational; live execution remains LIVE_LOCKED.**

Reviewed main: `ada9fb6ee0a19f1aa2f09c1bcd2db6f2ed4597fa`. Read-only review; no live order, cancellation, owner signature, limit or key was created. This report describes inspected source and retrieved CI evidence, not an inspected production host.

## Verified now

- Full approved safety gate PASS: frozen core 16/16, infrastructure manifest, pinned suite integrity, all 13 safety suites, and locked execution status.
- Eight research modules PASS (50 tests); supervisor sidecar PASS (7 tests).
- Fourteen engineering readiness checks PASS. These exercise recorded fixtures/local code and do not certify production operation. Source review found additional gaps despite these checks passing.
- Main’s latest retrieved daily diagnostics and readiness runs succeeded on October 4; safety suites succeeded on this main commit October 2.
- Latest historical evidence run: [37089200665](https://github.com/karankotwal31-lab/trips-trading-agent/actions/runs/37089200665), October 3 UTC, historical verdict EVIDENCE_PASS, 18 assets, approximately 19.48 years. Forward verdict FORWARD_INSUFFICIENT: **4/126 trading days; 1/6 monthly rebalances**.
- That research run reports `NEON_DATABASE_URL_PRESENT=false`. This only establishes absence in that workflow; it does not prove that the Neon project is absent or unhealthy.
- Local external-readiness probe returns NOT_VERIFIED: broker/account and both data providers are not configured in this execution environment. GitHub broker-secret presence was not inspected. Previously generating Alpaca keys is not proof that the runtime has them.

## Core architecture assessment

The intended path is coherent: independent live data → frozen Truth/Strategy/Risk → Capital Governor/Constitution → bound execution intent → authority gate → broker gateway → reconciliation. The immutable core still enforces PAPER_FIRST, config_guard permits only paper, and risk independently rejects live mode. No authority was bypassed.

The student/evolution engines and supervisor are advisory/research components; they must not promote strategies, widen limits, resume trading or submit broker orders. Broker execution feeds cannot substitute for independent strategy data. Unknown outcomes require reconciliation, not blind retries.

The current runtime and research harness are **different strategies/scopes**: approved execution is SPY/QQQ/AAPL, long-only US cash equities, 60-minute bars; historical research uses a broader 18-asset daily trend harness. Its historical pass cannot qualify the 60-minute executable strategy. P001–P005 remain proposed changes, including a strategy evidence gate, backtest/runtime position-management parity, correlated exposure limits and other corrections. These are not active merely because their proposal tests pass.

Production bootstrap currently verifies external dependencies read-only; it does not start an authoritative live trading daemon. Scheduled GitHub diagnostics/research jobs are ephemeral, and their actual runs have been delayed relative to cron. They cannot establish a continuously supervised trading runtime.

## New tested corrections: P006

[Proposal and patch](../../../proposals/P006-launch-boundary-hardening/README.md):

1. Reject future/naive freshness evidence and invalid freshness limits.
2. Recheck expected broker environment at submission.
3. Preserve zero risk-authorized size as a hard refusal.
4. Serialize duplicate-check/reserve/submit using an OS lock for one shared local runtime directory.
5. Reject a missing ledger when its checksum remains.
6. Stop locked cancellation before reaching the adapter.
7. Prevent raw external exception text from leaking into readiness reports.

13 new regressions PASS, including 16 concurrent thread calls and four processes, each proving a single test-double submission. Four existing targeted execution tests and seven production-bootstrap tests PASS. The new suite is included in the proposed runner and pinned-file inventory.

The patch is NOT applied to the approved executable; approval hashes were not regenerated. Full patched-build certification requires owner reapproval. A preliminary full execution-suite run correctly failed three build-identity checks because the candidate changes were not approved; its previous locked-cancellation expectation was corrected in the patch. Do not confuse targeted candidate tests with full release approval.

## Remaining release requirements

| Requirement | Current evidence | Needed to close |
|---|---|---|
| Reviewed executable fixes | P001–P006 proposals, not production changes | Review/apply accepted patches; owner reapprove affected manifests; run full exact-commit CI |
| Owner trust root | Repository public key is unconfigured | Owner installs/pins their public key; private key remains owner-controlled |
| Capital and TASK policy | No owner-approved governor profile | Explicit signed capital, order, exposure, concentration, loss/drawdown and frequency limits; approved TASK policy |
| Constitutional authority | PAPER_FIRST; no active LIVE_GATE | Reviewed signed amendment covering all three independent restrictions, plus separately signed live authorization bound to exact build/config/account |
| Broker | Alpaca adapter exists; live account verification NOT_VERIFIED | Credentials installed on intended runtime; exact account and US-equity permissions/restrictions verified read-only |
| Production market data | Both local provider selections missing; frozen core config DEMO | Two independent real-time entitled providers; closed 60-minute bars, provenance, cross-source agreement and session checks pass |
| Strategy qualification | Broad daily research historically passes; forward only 4/126 and 1/6 | Collect real forward evidence; qualify the exact deployed strategy/scope without extrapolating across harnesses |
| Durable runtime | No production host inspected; workflow DB credential absent | Provision chosen host, durable state, single writer/lease, clock sync, restart/recovery tests, secret injection and heartbeat |
| Order lifecycle | Offline conformance and reconciliation tested | Qualify production wiring for fills/partial fills, exits/protection, unknown outcomes, restart recovery and approved cancellation policy |
| Supervisor/report delivery | Sidecar tests pass | Deployed authenticated bridge, acknowledgements, delivery/retry evidence and operator alerts |

## Operational inputs to install securely

Existing reviewed command: `python scripts/trips_external_readiness.py` (read-only, no submit/cancel/replace path). Run from the intended private runtime after the selected build is approved. Do not paste secret values into chat or repository files.

Required selectors/credentials: `TRIPS_LIVE_BROKER=alpaca`, `TRIPS_LIVE_ACCOUNT_ID`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `TRIPS_PRIMARY_DATA_PROVIDER`, `TRIPS_SECONDARY_DATA_PROVIDER`, plus the corresponding provider credentials and real-time entitlement attestations. See `engine/execution/production_runtime.py` and `market_data.py` for exact supported configuration. No provider or owner financial limit has been invented.

## Remaining technical limits

P006 file locking is POSIX, single-host/shared-local-directory protection; it is not a distributed lease. Reconciliation and other direct state writes still require a single runtime writer. Complete loss of both ledger and checksum must be detected against an independent durable checkpoint/identity before restart. These host-level properties require deployment verification.

The governor implementation adds order value when assessing resulting exposure, including SELL intents. Exit/risk-reduction semantics therefore need an explicit reviewed policy and end-to-end qualification before live launch; changing that behavior silently would change financial authority. No claim is made that current code can safely launch simply by inserting keys.

No test demonstrates guaranteed profits or elimination of losses. The exact production strategy remains unqualified for real-money launch.
