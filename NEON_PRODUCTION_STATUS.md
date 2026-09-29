# Trip's v0.7 — Neon Production Activation Report

> **Current enforced truth — owner decision D1:** Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.
>
> Historical wording below is retained as historical evidence. Where older wording conflicts with this statement, D1 governs.

## Scope
Infrastructure only. No frozen v0.6 core file was changed.

## Production changes completed
- Created `trips_cloud` state schema and four private tables.
- Created bounded SECURITY DEFINER read/write functions.
- Created `trips_runtime_exec` NOLOGIN capability role and restricted `trips_runtime_app` login.
- Created `trips_dashboard_exec` NOLOGIN read-only capability role and restricted `trips_dashboard_app` login identity.
- Historical frozen-core fact: bootstrapped the authoritative runtime once from the core's initializer while the frozen configuration was `mode=paper`; this is not a simulated-broker stage in the current execution lifecycle.
- Stored a truth-labelled initial dashboard snapshot and infrastructure heartbeat.
- Retired the rejected over-privileged experimental runtime role after verifying it owned zero objects.

## Fail-closed tests on production
- second bootstrap: rejected
- wrong-version state commit: rejected
- competing lease: rejected
- non-authoritative demo market cycle: not executed

## Security audit
Every `trips_cloud` table denies direct access to `public`, `trips_runtime_app`, and `trips_dashboard_app`. The runtime application receives only approved function execution. The dashboard identity receives only the two read-only snapshot/heartbeat functions.

## Known limitations
- Production branch protection could not be enabled under the current free-plan branch-protection allowance.
- Dedicated GitHub trading repository is not yet available to the connected GitHub app.
- GitHub connector cannot write repository secrets.
- Vercel deployment action surfaced but was unavailable when invoked, so deployment is not claimed.
- Real market-data credentials are not configured and provider mode remains DEMO.
