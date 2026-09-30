# Research historical-data bootstrap

This directory contains a **research-only** internet ingestion path for the preregistered Trend
Harness. It is intentionally separate from `research/shadow/**` and every `engine/**` path.

## Source

The bootstrap uses `yfinance==1.7.0` to request Yahoo Finance daily history with:

- `auto_adjust=False`
- `back_adjust=False`
- `repair=True`
- `actions=False`
- `multi_level_index=False`

The raw OHLC fields stay raw. `Adj Close` is mandatory and is written separately as
`adj_close`; the bootstrap **never substitutes Close for Adj Close**.

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
