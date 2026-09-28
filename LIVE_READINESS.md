# Trip's live-money readiness contract

Live execution is **not ready** merely because an API key exists. Activation requires every machine-checkable gate in `live_execution/readiness.py` to pass on the exact reviewed commit.

## External dependencies still required
1. Upstox is the selected broker adapter; the account/app must be provisioned before broker-authenticated checks can pass.
2. The account/app must legally and operationally permit the intended Indian-market API trading and comply with applicable exchange/broker controls.
3. Broker API credentials must be stored only in the deployment secret store, never committed.
4. Upstox order/cancel semantics must pass the Upstox sandbox; authenticated account, position, fill and reconciliation semantics must be verified before any production endpoint is enabled.
5. Production account identity/permissions, market-data entitlement, and required registered static outbound IP must be verified.
6. An operator must explicitly enable live mode.

## Failure policy
Any unknown broker order, reconciliation mismatch, stale/untrusted market data, frozen-core drift, infrastructure drift, duplicate intent, loss-limit breach, or kill-switch event halts new live submissions.

AI/research engines can propose decisions. They cannot bypass this execution boundary, increase limits, clear the kill switch, change the Constitution, or self-promote into execution authority.

No claim of profitability or 'superintelligence' is made by readiness. Those require independent evidence from out-of-sample, walk-forward and sustained live/shadow evaluation.
