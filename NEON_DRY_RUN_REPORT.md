# Historical: Neon Pre-Production Dry Run

> **Current enforced truth — owner decision D1:** Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.
>
> Historical wording below is retained as historical evidence. Where older wording conflicts with this statement, D1 governs.

This report records the pre-production test-branch stage. Production activation has since progressed; see `NEON_PRODUCTION_STATUS.md` and `INFRASTRUCTURE_STATUS.md`.

# Trip's v0.7.1 — Neon Pre-Production Dry Run

## Scope

Infrastructure only. No file under the frozen v0.6 core contract was changed.

## Project state

- Neon project: `Trips Trading Agent`
- production branch: `main`
- production tables at checkpoint: **0**
- disposable schema-test branch: used for database semantics and privilege verification

## Verified database invariants

- Missing authoritative runtime returns null; it does not create state.
- First explicit bootstrap creates version 1.
- Re-bootstrap is rejected with `already_initialized`.
- Commit with an incorrect expected version is rejected.
- Valid compare-and-swap commit advances version 1 -> 2.
- Lease owner A prevents owner B from acquiring the active lease.
- Owner B cannot release owner A's lease.
- Heartbeat and dashboard snapshot functions round-trip correctly.
- Public EXECUTE on privileged functions is revoked.

## Privilege audit

A Neon API-created login was found to be a member of `neon_superuser` and therefore failed least-privilege requirements. It is not approved for use.

A manually created restricted test login showed:

- superuser: false
- inherit: false
- create-role: false
- create-database: false
- role memberships: none
- direct runtime-table SELECT: false
- direct runtime-table INSERT: false
- approved function EXECUTE: true

Production will use that privilege model.

## Release gate

Schema and role creation on `main` remain intentionally unapplied pending explicit production-mutation approval.
