# Trip's Trend Evidence Harness — preregistration

Status: **PREREGISTERED BEFORE FIRST RUN**

This directory defines a research-only daily ETF trend strategy. It does not import or modify Trip's execution, broker, credential, runtime-state, Constitution, Risk, TASK, or live-readiness paths.

## Truth statement

Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.

## Strategy

- Universe: the fixed ETF list in `universe.json`.
- Long-only.
- Asset ON only when adjusted close is strictly above its configured SMA.
- Baseline lookback: 252 trading days.
- Sizing: inverse 60-day annualized volatility, scaled toward 10% portfolio target volatility, then capped at 20% per asset, 40% per asset class, 100% gross.
- No leverage and no shorting. Unallocated weight remains cash earning 0%.
- Monthly decisions use data through close **t**; target changes execute at the next available close **t+1**. Same-bar fills are forbidden.
- Costs are applied per side at 1x, 2x and 4x the class-specific values in `cost_model.json`.
- Benchmarks: SPY buy-and-hold, equal-weight universe, and 60/40 SPY/IEF.
- Robustness grid is report-only and never used to choose the baseline after seeing results.
- Bootstrap settings are fixed before the first run.

## Data contract

Owner-supplied CSV files live under `research/data/daily/` and use columns:

`date,open,high,low,close,adj_close,volume`

A SHA-256 manifest binds every input. Validation fails closed. Fewer than 10 valid assets or fewer than 15 years of qualifying data yields `INSUFFICIENT_DATA`.

## Evidence rule

The gate reads `pass_criteria.json` only. It must report `EVIDENCE_PASS`, `EVIDENCE_FAIL`, or `INSUFFICIENT_DATA`. Profit is never inferred from synthetic scenarios, and criteria are never changed after seeing results without a new logged trial.
