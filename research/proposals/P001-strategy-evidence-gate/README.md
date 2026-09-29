# P001 — fail-closed strategy evidence gate

## Rationale

WP4 already defines the research evidence contract, but current execution readiness does not read it.
Current LIVE_READY_LOCKED engineering readiness can therefore be reported while the preregistered
backtest is INSUFFICIENT_DATA and forward shadow evidence is FORWARD_INSUFFICIENT.

This proposal adds an independent standard-library verifier inside the pinned execution layer and
makes strategy_evidence_pass explicit in readiness, lifecycle, preflight and status.

## Proposed behavior

A pass requires all WP4 conditions: EVIDENCE_PASS, FORWARD_PASS, true replay weight parity,
matching committed strategy/universe/cost/criteria/data-manifest hashes, and both reports no more
than 30 days old.

The LIVE_LOCKED -> LIVE_READY_LOCKED transition is checked for every actor, including OWNER.
Owner authority cannot substitute for engineering evidence.

## Risk

This intentionally tightens readiness. With today's repository state, strategy_evidence_pass is
false because the owner data manifest and sufficient forward evidence do not exist. Readiness
therefore remains LIVE_LOCKED.

The integration risk is coupling research artifact formats to an execution-layer verifier. The
patch limits that coupling to a narrow JSON/hash contract and fails closed on parsing drift.

## Fail-closed behavior

Missing report, non-PASS verdict, missing data manifest, hash drift, stale/future timestamp,
false/missing parity, or verifier exception all refuse readiness. The patch does not loosen
PAPER_FIRST, broker gates, capital limits, or owner authorization.
