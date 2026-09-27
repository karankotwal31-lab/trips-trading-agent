# Trip's v0.7 Cloud Paper Shell — Neon transport

This directory is an **infrastructure wrapper around the frozen Trip's v0.6 core**. It does not alter trading logic, the Constitution, Truth Layer, Forge Gate, risk engine, execution simulator, Guardian, Evolution Lab, Decision Mirror, or Supervisor Counsel.

## Safety model

1. `core_v06.sha256` freezes the reviewed core files.
2. `approved_infra.json` separately fingerprints the cloud shell.
3. Scheduled runs require the database lease before hydration/execution.
4. The authoritative runtime is hydrated into the exact file format already consumed by v0.6.
5. v0.6 runs unchanged in a subprocess.
6. Updated state is committed with an atomic compare-and-swap version check.
7. Version conflict, missing remote state, bad checksum, lease failure, missing credentials, or core drift => **HALT / no authoritative state mutation**.
8. Bootstrap is explicit; scheduled cycles never silently create a fresh account.
9. The Neon login is server-side only and is intentionally not a Neon control-plane `neon_superuser` login.
10. Runtime credentials receive no direct table privileges; only EXECUTE on bounded `SECURITY DEFINER` functions through `trips_runtime_exec`.

## Activation sequence

1. Prepare and verify `neon_schema.sql` on a temporary Neon branch.
2. Apply the reviewed migration to the dedicated Trip's Neon project only after explicit approval.
3. Create a restricted PostgreSQL LOGIN and grant only `trips_runtime_exec`.
4. Add its TLS database URL as GitHub secret `NEON_DATABASE_URL`.
5. Run the `Trips Cloud Paper Cycle` workflow manually with `bootstrap` exactly once.
6. Keep engine config in DEMO mode until market-data credentials and entitlement checks are separately reviewed.
7. Verify multiple consecutive scheduled cycles before enabling any new provider configuration.

The dashboard remains read-only. The database credential must never be exposed in browser JavaScript or public deployment variables.
