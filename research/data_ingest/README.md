# Research historical-data bootstrap

This directory contains a **research-only** internet ingestion path for the preregistered Trend
Harness. It is intentionally separate from `research/shadow/**` and every `engine/**` path.

## Source

The active CI bootstrap calls Yahoo Finance's unauthenticated
`/v8/finance/chart/{symbol}` JSON endpoint directly, trying `query1` and `query2` with bounded
retries. It explicitly requests `includeAdjustedClose=true`.

The raw OHLC fields stay raw. `indicators.adjclose[0].adjclose` is mandatory and is written
separately as `adj_close`; the bootstrap **never substitutes Close for Adj Close**.

`yfinance_daily.py` is retained only as a documented research fallback/diagnostic path after the
first GitHub Actions attempt returned empty frames for every symbol. The workflow no longer depends
on that library.

This is an unofficial research data source, not a licensed production Truth provider and not a
broker feed. A future live-money system still requires the two independent production realtime
providers already required by the execution layer.

## Output contract

For every successfully downloaded symbol:

```text
date,open,high,low,close,adj_close,volume
```

The bootstrap also writes `manifest.sha256` and `ingest_summary.json`. The existing WP1 harness
then performs the authoritative schema/history/sufficiency checks and produces
`EVIDENCE_PASS`, `EVIDENCE_FAIL`, or `INSUFFICIENT_DATA`.

The GitHub workflow uploads the entire normalized dataset plus the backtest report as a short-lived
artifact. It does not commit market data or silently alter the preregistered strategy, universe,
costs, or pass criteria.

## Forward checkpoint recovery

The scheduled workflow restores only the latest successful same-branch checkpoint, searching
all API pages. Production main never restores a PR checkpoint. The helper verifies the journal
hash chain before installing it, carries only the journal, and regenerates reports and heartbeat.
Missing, expired, duplicate, empty or corrupted checkpoint artifacts stop the run instead of
silently resetting the evidence window. A new research branch may initialize its own chain;
main requires an existing recoverable checkpoint.

The fixed US-listed ETF universe accepts a same-day bar only after 17:00 New York time,
with daylight-saving time handled by the IANA timezone database. Earlier dates remain usable;
future dates are rejected. This conservative research buffer also delays early-close sessions
until 17:00 and does not certify provider entitlement or exchange-calendar completeness.

If an older verified checkpoint contains a session recorded before that cutoff, recovery
retains the completed prefix and stores every affected suffix event intact inside
`QUARANTINED_EVENT` audit records, including its original hash and the source archive hash.
These records cannot count as portfolio states, decisions or rebalances. The original artifact
is never modified. Recovery refuses if no completed portfolio state precedes the affected
session, so it cannot invent or silently restart an evidence window.

Actions artifacts remain time-limited backups, not a 24/7 durable trading host. Keep the durable
deployment requirements in `deploy/shadow/README.md`; a passing scheduled research workflow
does not enable order transmission or prove live readiness.
