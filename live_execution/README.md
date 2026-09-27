# Trip's Live Execution Foundation (v0.9 candidate)

This layer is intentionally outside the frozen v0.6 core.

It provides the contracts and fail-closed gates required before a broker adapter can submit real-money orders. No broker credentials are stored in this repository.

## Non-negotiable activation gates
- Frozen v0.6 core checksum verification must pass.
- Approved infrastructure verification must pass.
- Explicit operator opt-in is required via `TRIPS_LIVE_EXECUTION=ENABLED`.
- A broker adapter must be explicitly configured and authenticated outside the repository.
- Live orders require a unique idempotency key and a fresh, signed execution intent.
- Maximum notional, daily-loss, position-count, stale-data and kill-switch checks fail closed.
- Shadow/paper comparison and reconciliation must remain available.
- No Student/Evolution/Supervisor component receives order-submission authority.

This branch is a foundation for reviewed live execution. It does not contain credentials and does not silently activate trading.
