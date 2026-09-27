# Trip's v0.7 — Neon Production Activation Report

## Scope
Infrastructure only. No frozen v0.6 core file was changed.

## Production changes completed
- Created `trips_cloud` state schema and four private tables.
- Created bounded SECURITY DEFINER read/write functions.
- Created `trips_runtime_exec` NOLOGIN capability role and restricted `trips_runtime_app` login.
- Created `trips_dashboard_exec` NOLOGIN read-only capability role and restricted `trips_dashboard_app` login identity.
- Bootstrapped the authoritative paper runtime once from the core's own initializer.
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
