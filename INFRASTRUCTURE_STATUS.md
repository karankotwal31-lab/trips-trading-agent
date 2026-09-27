# Trip's Infrastructure Status — v0.7 Neon Cloud Paper Shell

## Frozen core

Trip's v0.6 trading/safety core remains unchanged. `infra/core_v06.sha256` verifies the frozen core byte-for-byte. Cloud orchestration is outside the core and cannot silently alter strategy, risk, Constitution or execution logic.

## Neon production state

- Project: `Trips Trading Agent`
- Database: `trips`
- Production schema: `trips_cloud`
- Tables: `runtime`, `cycle_lease`, `heartbeats`, `dashboard_snapshot`
- Authoritative runtime: initialized
- Runtime cloud version: **1**
- Runtime SHA-256: `be71acbd0c101daa91218d9fa8e1bd9966bd01d62020d5c1fdca7544985d6d2b`
- Initial paper equity: **100000.0 simulated**
- Open positions: 0
- Pending entries: 0
- Market cycle executed on production state: **false**
- Latest activation heartbeat: PASS
- Dashboard snapshot: present; market remains UNAVAILABLE and strategy UNPROVEN

## Production privilege model

`trips_runtime_app`:
- superuser: false
- create-role/create-database: false
- direct Trip's table SELECT/INSERT/UPDATE/DELETE: false
- approved SECURITY DEFINER functions: executable
- credential: provisioned in Neon; never written into this package

`trips_dashboard_app`:
- superuser/admin: false
- direct Trip's table access: false
- may execute only `get_dashboard_snapshot()` and `get_latest_heartbeat()`
- may not execute `get_runtime()` or `commit_runtime(...)`
- login credential intentionally not activated after the platform blocked safe non-exposing initialization

The earlier over-privileged experimental `trips_runtime` role owned zero objects and has been retired.

## Verified production invariants

- duplicate bootstrap -> rejected (`already_initialized`)
- stale/wrong-version commit -> rejected (`version_conflict_or_missing`)
- competing lease owner -> rejected (`lease_held`)
- valid lease owner can release its lease
- authoritative runtime remained version 1 after rejection tests
- public EXECUTE on privileged functions: false
- public direct table access: false
- runtime direct table access: false
- dashboard direct table access: false
- heartbeat payload bounded to 64 KiB; retention capped to 10,000 rows

## Free-tier limitation

Neon refused marking the production branch as protected because the current plan has reached its protected-branch allowance. Trip's compensates with function-only credentials, explicit bootstrap, immutable core checks, leases, versioned compare-and-swap commits and fail-closed state validation. This is not presented as equivalent to plan-level branch protection.

## Pending external activation

1. Create/expose a dedicated private GitHub repository for Trip's trading agent. The existing `trips-youtube-control` repository remains intentionally untouched.
2. Store the restricted `NEON_DATABASE_URL` as a repository secret. The current GitHub connector does not expose repository-secret writes.
3. Add reviewed market-data credentials (`TWELVE_DATA_API_KEY` and an independent verifier such as Alpha Vantage) as server-side secrets.
4. Review and approve the provider/config fingerprint change from DEMO before any real-data paper cycle.
5. Enable hourly cloud cycles and daily deep diagnostics, then verify multiple consecutive clean runs before any strategy-evidence claim.
6. Deploy the read-only dashboard once a safe server-side read credential can be attached.

No live-money broker path is pending or enabled.
