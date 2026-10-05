# P006 — launch boundary hardening

Status: proposed, tested in a disposable worktree; NOT APPLIED to the approved executable.
Base: ada9fb6ee0a19f1aa2f09c1bcd2db6f2ed4597fa.

## Defects addressed

- Health and preflight freshness accepted future timestamps; naive preflight timestamps inherited local timezone. Reject these and nonfinite/nonpositive age limits.
- Gateway read account environment but ignored expected_environment. Refuse missing/mismatched environment before the live boundary.
- Zero authorized quantity became None (unlimited) in the gateway's independent size check. Preserve zero as a refusal.
- Submission check/reservation were not serialized. Hold an OS file lock across duplicate lookup, validation, reservation and submission.
- Missing ledger payload with a remaining checksum was interpreted as a new empty ledger. Fail closed instead.
- Locked cancellation still invoked an adapter with permit=None. Refuse at the gateway too.
- External-readiness failure reports copied arbitrary exception strings. Report exception type only, avoiding credential-bearing provider error text.

## Validation

13 new offline regressions pass, including 16 simultaneous thread calls and four processes sharing one ledger (one test-double submission each). Four existing targeted execution regressions and seven production-bootstrap tests also pass.
A full execution suite on the unapproved patched tree necessarily encounters build-integrity refusals; do not call this a fully approved build. Its prior cancellation expectation is updated to enforce the lock.

## Limits

File locking requires POSIX flock and all gateway workers using the SAME durable local directory. It is not a multi-host distributed lease. Unsupported stores fail closed. Never delete the lock file while a worker is alive.
Complete deletion of both ledger and checksum still needs an external runtime identity/checkpoint policy; this patch detects the orphan-checksum case only.
This does not wire a production runtime, qualify a strategy, approve capital, or enable trading. Direct ledger operations/reconciliation require a single runtime writer; multi-host operation is not qualified.
