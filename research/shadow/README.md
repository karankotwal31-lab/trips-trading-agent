# Trip's no-order forward shadow runner

This directory is **research-only**. It has no broker, order, execution, credential, or network
path. It reads owner-supplied daily CSV data, reuses the preregistered Trend Harness
`target_weights` function, and writes only hypothetical evidence.

## Daily flow

1. Verify the existing journal hash chain.
2. Validate the owner CSV manifest and data with the WP1 rules.
3. Refuse a market entry when data are invalid/insufficient or more than three weekdays stale.
4. Mark the hypothetical portfolio to the newest common market close.
5. When a new month proves that the prior observed date was month-end, compute the decision using
   only data through close **t**, then apply the hypothetical rebalance at close **t+1**.
6. Apply the same class-specific 1x per-side costs from `research/trend/cost_model.json`.
7. Append immutable `DECISION`, `HYPOTHETICAL_REBALANCE`, and `STATE` evidence to
   `journal.jsonl`.
8. Update `heartbeat.json`.

No order is transmitted. Every journal event explicitly records `execution_authority: NONE`.

## Files

- `journal.py`: append-only SHA-256 chain and tamper verification.
- `runner.py`: one deterministic daily no-order cycle.
- `shadow_report.py`: forward metrics, benchmarks, replay parity, and D3 verdict.

Runtime state files are intentionally **not committed**. A durable host should mount a persistent
state directory and an owner-managed data directory.

## D3 forward gate

`shadow_report.py` returns one of:

- `FORWARD_PASS`
- `FORWARD_FAIL`
- `FORWARD_INSUFFICIENT`

A pass requires all owner-fixed D3 conditions:

- at least 126 processed trading days;
- at least 6 monthly rebalances;
- no data gap greater than 3 trading days;
- max forward drawdown <= 20%;
- forward cost drag <= 1.5x the harness assumption;
- 100% replay weight parity.

The report also includes SPY buy-and-hold, equal-weight-universe, and 60/40 SPY/IEF benchmark
metrics over the observed forward window.

## Example

```sh
python research/shadow/runner.py \
  --data-dir /srv/trips-shadow/data/daily \
  --journal /srv/trips-shadow/state/journal.jsonl \
  --heartbeat /srv/trips-shadow/state/heartbeat.json

python research/shadow/shadow_report.py \
  --data-dir /srv/trips-shadow/data/daily \
  --journal /srv/trips-shadow/state/journal.jsonl \
  --output /srv/trips-shadow/state/forward_report.json
```

The external process that refreshes owner CSV files is outside this runner. No API key or secret is
stored by the shadow process.
