# Trip's v0.7 — Neon Cloud Paper Shell (Activated State Layer)

> **Current enforced truth — owner decision D1:** Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.
>
> Historical wording below is retained as historical evidence. Where older wording conflicts with this statement, D1 governs.

Trip's v0.7 wraps the **unchanged, frozen v0.6 paper-trading core** with a durable Neon/PostgreSQL state shell. The trading architecture, Constitution, Truth Layer, Forge Gate, risk engine, execution simulation, Guardian, Evolution, Decision Mirror and Supervisor Counsel remain byte-for-byte protected by `infra/core_v06.sha256`.

## Activated infrastructure

- Dedicated Neon project: `Trips Trading Agent`.
- Private `trips_cloud` schema with authoritative runtime, lease, heartbeat and dashboard-snapshot tables.
- Authoritative paper runtime bootstrapped exactly once from `engine.store.initial_runtime(100000.0)` and verified at cloud version 1.
- Compare-and-swap runtime commits and exclusive bounded cycle leases.
- `trips_runtime_app` is function-only: no direct table SELECT/INSERT/UPDATE/DELETE and no database/role administration.
- Separate read-only dashboard capability can access only the latest dashboard snapshot and heartbeat.
- Public execution on Trip's privileged database functions is revoked.
- Heartbeat detail is bounded to 64 KiB and history is capped to the newest 10,000 records.
- First Neon dashboard snapshot is deliberately `UNAVAILABLE/UNVERIFIED` for market/diagnostics until real feeds and scheduled diagnostics are active.
- Existing Trip's Daily Review automation now checks the connected Neon runtime, heartbeat and dashboard state.

## Current activation boundary

The durable state layer is live, but **scheduled market cycles are not active yet**. The provider configuration is still `DEMO`; no demo cycle has been committed to authoritative Neon history. A dedicated GitHub trading repository and its server-side secrets are still required before hourly orchestration can be enabled. Vercel's connected deployment action was unavailable at runtime, so the dashboard is not claimed as deployed.

At this historical v0.7 checkpoint, no live-money execution layer had yet been shipped. The current execution route and authority boundary are governed by D1 above.

---

# Trip's v0.6 — Verified Control Center

Historical v0.6 description: Trip's was a **paper-only** market-analysis and trading-research agent with deterministic data-truth, risk, execution-simulation, Guardian, Evolution, Decision Mirror and Supervisor controls. v0.6 adds a production-style **read-only interactive dashboard** without weakening any v0.5 safety boundary.

## What v0.6 adds

- A responsive dark control center using the supplied monochrome portrait as a centered theme background.
- Interactive Dashboard, Market, Strategies, Evolution, Guardian, Diagnostics, Supervisor, Activity and Settings views.
- Candlestick rendering from the **same validated closed-bar series** already used by the engine; the UI never invents chart prices.
- A whitelisted `dashboard_export.py` layer. Raw evidence packets, secrets and private supervisor payloads are not exposed to the browser.
- SHA-256 `dashboard.json` sidecar verification in the browser before operational state is rendered.
- Explicit stale-snapshot and integrity-failure banners. Missing state is shown as `UNVERIFIED`, never converted into zero/healthy/delivered.
- Strict CSP, no external JavaScript/CSS/CDN dependencies, credential scanning of JS/CSS, and no third-party analytics.
- GET/HEAD-only local dashboard server with CSP/security headers, disabled directory listing and mutation methods returning HTTP 405.
- Dashboard HTML/CSS/JS/portrait/Constitution included in the approved build manifest so operator-visible truth claims cannot drift silently.
- Guardian refreshes only the **derived read-only snapshot**; dashboard export failure never rewrites authoritative trading state.
- Explicit v0.5 → v0.6 runtime migration. It refuses migration if a paper position/order is open or the runtime is halted.

## Non-negotiable execution boundary

Historical v0.6 boundary: there was **no live-money broker execution path** in that release. The dashboard has **zero execution authority** and exposes no mutation endpoints. Health PASS is not trade permission. Supervisor advice remains advisory only. Synthetic/demo data can support analysis but cannot authorize a current-market paper trade.

## Validated trading scope

- `US_EQUITY_CASH_LONG_ONLY`
- 60-minute bars
- allowlist: `SPY`, `QQQ`, `AAPL`
- structured OHLCV input
- historical v0.6 simulated/paper execution only (not a current broker stage)

Options, futures, FX, crypto, short selling, leverage and unvalidated symbols require separate instrument-aware risk/execution models and review.

## Run locally

```text
python tests/run_tests.py
PYTHONPATH=engine python engine/diagnostics_engine.py
PYTHONPATH=engine python engine/forge_agent.py
PYTHONPATH=engine python engine/dashboard_export.py
PYTHONPATH=engine python engine/dashboard_server.py
```

The dashboard server binds to `127.0.0.1:8080` by default and exposes no write endpoints.

For an existing reviewed v0.5 runtime, run `PYTHONPATH=engine python engine/migrate_runtime_v05_to_v06.py` before the first v0.6 trading cycle. The migration refuses open/pending positions.

## Current evidence status

The strategy remains **UNPROVEN**. Synthetic causal stress is a diagnostic/rejection tool, not evidence of profitability. Durable private hosting, verified independent real-time data feeds, formal exchange-calendar/session handling and the authenticated hosted Trip's → supervisor bridge remain prerequisites before any broader deployment claim.

See `docs/TRIPS_CONSTITUTION.md` for the non-negotiable Constitution.
