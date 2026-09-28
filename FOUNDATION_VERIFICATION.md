# Trip's execution foundation verification — 2026-09-28 UTC

Status: locally verified; draft PR; no production activation or broker-authenticated sandbox claim.

## Repairs

- Fixed unittest discovery and repository imports in the verification entry point.
- Verification now runs core, cloud shell, Student, integration and execution suites together.
- Reject non-finite quantities, prices, PnL and limits, invalid position counts and blank intent keys.
- Reject Upstox slicing before any request; reject overlong tags rather than truncating identity.
- Validate acknowledgement identity and response envelopes; malformed/error snapshots cannot become empty accounts.
- Recheck live opt-in at submission time in all three broker adapters.
- Require readiness evidence, a kill switch and durable journal at the execution entry point.
- Persist reservations before submission. Duplicate keys and uncertain/crashed submissions block automatic replay and new submissions after restart.

## Verification

Run `python scripts/verify_live_foundation.py` from any working directory using the script's absolute path if needed. Python 3.12, standard library only.

- Frozen v0.6 core: unchanged; all 16 file hashes match.
- Approved infrastructure manifest: unchanged and verified.
- 92 core + 16 cloud + 10 Student + 7 integration + 44 execution tests = 169 passing tests.
- Broker tests use mocks and make no authenticated order requests.
- Upstox response contracts checked against official place-order V3, cancel-order V3 and order-book documentation.

## Remaining release gates

- GitHub Actions failed before runner steps/logs existed at prior PR head. This is separate from the reproduced local discovery defect; the exact account/service cause is not established.
- An authorized Upstox sandbox app/token is needed for real sandbox order/cancel testing. No token was supplied or retrieved during this work.
- Upstox profile/account permissions and broker snapshot normalization still require authenticated verification. The existing profile response is not normalized to the generic preflight contract, so readiness must remain false.
- Production host wiring must supply trusted, current readiness evidence and use the same durable journal for every submission to the same account. Strategies must not supply their own readiness flags. This PR does not provision that host.
- The journal deliberately has no automatic unknown-order clearing operation. Reconciliation and explicit operator recovery are required after an uncertain submission; no automatic replay is safe.
- Live money remains disabled. No profitability, live deployment, or end-to-end operational completion is claimed.

Official contracts:
- https://upstox.com/developer/api-documentation/v3/place-order/
- https://upstox.com/developer/api-documentation/v3/cancel-order/
- https://upstox.com/developer/api-documentation/get-order-book/
