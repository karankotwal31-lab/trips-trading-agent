# CI is blocked by GitHub Actions billing — how to clear it

## What is happening

Every GitHub Actions job on this repository fails before it starts, with:

```
The job was not started because an Actions budget is preventing further use.
```

Measured facts (2026-09-29):

| Observation | Value |
|---|---|
| Workflow runs in repository history | **34 — all `failure`, zero successes** |
| First run | `2026-09-27T15:48:33Z` (repository creation day) |
| Total Actions time ever consumed | **120 seconds** |
| Repository visibility | **PRIVATE** |
| Account | personal — `karankotwal31-lab` |
| GitHub platform status | All Systems Operational |

Because total consumption is **two minutes** and no run has ever succeeded — including the very
first one, on the day the repository was created — this is **not** an exhausted monthly quota.
Waiting for a quota reset will not help. GitHub-hosted runners are metered for private
repositories, and this account has no usable Actions entitlement: either no payment method is on
file, or the Actions spending limit is set to zero.

## How to clear it (pick one)

1. **Set an Actions spending limit above zero** — `github.com/settings/billing/spending_limits`.
   This needs a payment method on file. Included free minutes are consumed before anything is
   charged, so for this workload the cost should be effectively zero; the limit simply has to
   permit usage instead of blocking it.
2. **Verify the payment method** — `github.com/settings/billing/payment_information`. New
   accounts with no valid payment method are blocked from Actions on private repositories.
3. **Raise the included minutes** by moving to GitHub Pro (2,000 → 3,000 minutes/month for
   private repositories).
4. **Make the repository public** — public repositories get unlimited free Actions minutes.
   ⚠️ **Do not do this without a full audit first.** This repository is a real-money trading
   system: it contains the Constitution, the risk and execution design, and infrastructure notes
   that name production database objects and privilege models. Publishing it would expose the
   strategy and the operational surface to everyone.

After any of these, re-run the workflow (`Actions → Trips Safety Suites → Run workflow`) or push
an empty commit; the gate script is verified to pass unmodified.

## What is NOT affected

The block is entirely in GitHub's job scheduler. Nothing in this repository depends on it:

- The safety gate is defined **once** in `scripts/verify_all.sh` and is runnable anywhere:

  ```sh
  sh ./scripts/verify_all.sh
  ```

- `.github/workflows/trips-safety-suites.yml` calls that same script, so a local run and a CI run
  execute byte-identical checks.
- Opt in to running the gate before every push:

  ```sh
  git config core.hooksPath scripts/githooks
  ```

The workflow is deliberately left **enabled** while Actions is blocked. A visibly failing check
that means "this never ran" is honest; silently skipping the gate would leave every safety claim
in this repository unverified without anyone noticing.

## Why the gate is pinned

`tests/suite_integrity.py` hashes the suites, the runners **and the gate itself**
(`scripts/verify_all.sh`, `scripts/githooks/pre-push`, and the workflow) into
`tests/approved_tests.json`. Weakening the gate to make it pass is therefore an explicit,
reviewable commit rather than a silent edit.
