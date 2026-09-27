# Trip's live-money readiness contract

Live execution is **not ready** merely because an API key exists. Activation requires every machine-checkable gate in `live_execution/readiness.py` to pass on the exact reviewed commit.

## External dependencies still required
1. A specific supported broker/account must be selected.
2. The account must legally/operationally permit the intended US-equity cash trading.
3. Broker API credentials must be stored only in the deployment secret store, never committed.
4. Broker-specific order, cancel, fill, position and account semantics must be implemented and tested against that broker's sandbox/test environment before any production endpoint is enabled.
5. Production account identity/permissions and market-data entitlement must be verified.
6. An operator must explicitly enable live mode.

## Failure policy
Any unknown broker order, reconciliation mismatch, stale/untrusted market data, frozen-core drift, infrastructure drift, duplicate intent, loss-limit breach, or kill-switch event halts new live submissions.

AI/research engines can propose decisions. They cannot bypass this execution boundary, increase limits, clear the kill switch, change the Constitution, or self-promote into execution authority.

No claim of profitability or 'superintelligence' is made by readiness. Those require independent evidence from out-of-sample, walk-forward and sustained live/shadow evaluation.
