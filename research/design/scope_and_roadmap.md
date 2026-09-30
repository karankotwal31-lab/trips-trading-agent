# Trip's scope and evidence roadmap — design only

Status: **DESIGN / NO SCOPE EXPANSION**

## Current governing truth

Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED);
frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only
with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data.
Strategy verdict: UNPROVEN.

## Current executable mandate

The presently reviewed mandate remains narrow:

- instrument class: US cash equities;
- direction: long-only;
- bar interval: 60 minutes;
- symbols: SPY, QQQ, AAPL;
- no shorting;
- no leverage;
- no options/futures/FX/crypto authority.

Broker capability does not widen Trip's mandate.

## Analysis-only markets

### Commodities

The commodity layer can validate exact contracts, lifecycle metadata, two-source truth and
analytical readiness. Its decision explicitly carries `execution_authority: false`.

Therefore commodities remain **analysis-only** until commodity-specific evidence passes and the
owner separately approves a scope transition.

### Upstox / Indian markets

The Upstox channel proves interface behavior and can support read-only/conformance research.
Current mandate compatibility is separate: the current Trip's mandate is US equities, while
Upstox's current venue evidence is Indian-market oriented.

Therefore Upstox does not become executable merely because its adapter is technically conformant.
Indian-market execution remains **analysis-only** until it receives its own reviewed market scope
and passing evidence.

### Any other non-equity market

Options, futures, FX, crypto, leveraged products, shorts and any new geography remain analysis-only
by default.

Unknown scope is **not authorized scope**.

## Evidence required before a market can become executable

Every proposed market/instrument class must independently establish:

1. **Instrument identity**
   - exact executable identifier;
   - venue;
   - currency;
   - multiplier/tick;
   - settlement type;
   - expiry/lifecycle fields where applicable.

2. **Market-data truth**
   - production source;
   - independent verification source when required;
   - entitlement/provenance;
   - freshness;
   - session/holiday correctness;
   - stale/corrupt/source-disagreement fail-closed behavior.

3. **Market-specific risk**
   - position sizing;
   - exposure limits;
   - correlation/concentration controls;
   - margin/leverage rules where applicable;
   - gap/liquidity handling;
   - daily-loss/drawdown interaction.

4. **Execution semantics**
   - supported order types;
   - venue state;
   - partial fills;
   - cancel/replace;
   - disconnect;
   - duplicate/retry/idempotency;
   - ambiguous outcome reconciliation.

5. **Lifecycle controls**
   - delivery/first-notice/last-trade buffers where applicable;
   - no silent rolling;
   - corporate actions or contract changes;
   - physical-delivery prohibition unless explicitly reviewed.

6. **Broker conformance**
   - account identity;
   - environment identity;
   - read-only live verification;
   - capability-by-capability evidence;
   - mandate compatibility.

7. **Strategy evidence**
   - preregistered rules;
   - conservative costs;
   - out-of-sample/walk-forward evidence;
   - robustness/null controls;
   - no post-result threshold tuning.

8. **Forward evidence**
   - no-order shadow run;
   - replay parity;
   - missed-run detection;
   - sufficient duration/rebalances;
   - market-specific benchmarks.

9. **Owner acts**
   - explicit scope approval;
   - owner capital/risk values;
   - TASK limits;
   - any required constitutional amendment;
   - separate live authorization.

No one market may inherit another market's evidence by analogy.

## Ordered roadmap

### R0 — Preserve frozen authority

Keep the present locked state and maintain green safety/readiness gates. No live order is an
engineering test.

### R1 — Historical evidence

Supply owner-approved daily data and manifest to the Trend Harness. Record
`EVIDENCE_PASS`, `EVIDENCE_FAIL` or `INSUFFICIENT_DATA` honestly.

Do not tune the preregistered strategy/criteria after observing the result without logging a new
trial.

### R2 — Forward shadow evidence

Run the durable no-order shadow runner on real data. Accumulate the owner-fixed D3 evidence:

- at least 126 trading days;
- at least 6 monthly rebalances;
- no data gap over 3 trading days;
- max forward drawdown <= 20%;
- cost drag <= 1.5x harness assumption;
- 100% replay weight parity.

### R3 — Hosted supervisor bridge

Implement the asynchronous advisory bridge described in `supervisor_bridge.md`.

It may improve observability and request a halt; it must not become a trade-approval dependency.

### R4 — Evidence gate proposal (P001)

After owner review, make strategy evidence a deterministic fail-closed requirement for
`LIVE_READY_LOCKED` and live preflight.

Until then, WP4 remains research-only and does not grant execution authority.

### R5 — Semantic parity / concentration safety

Review P002 and P003:

- bring backtest position management into parity with the frozen-cycle break-even/trailing rules;
- add a correlated-exposure cap so highly correlated positions cannot consume the full aggregate
  exposure budget independently.

Both are tightening/accuracy proposals and require owner reapproval because they touch pinned
files.

### R6 — Protected truth cleanup

Review P004 so pinned operator/runtime wording expresses the same D1 truth without weakening
PAPER_FIRST, config mode, or the live lock.

Review P005 so Ed25519 test documentation matches the real pinned/research test surface.

### R7 — Owner-controlled live prerequisites

Only after evidence gates pass:

- install owner Ed25519 public key;
- set Capital Governor profile;
- set production TASK limits;
- verify real broker account/OAuth read-only;
- verify production market-data entitlement and independent truth;
- review/apply constitutional amendment if still desired;
- reapprove changed manifests/config/build;
- issue a separate signed live authorization;
- reconcile clean broker/local state.

These are not autonomous engineering substitutions for owner authority.

### R8 — First executable market

If all applicable deterministic conditions pass, enable only the explicitly approved market/scope.
Start with the smallest reviewed capital envelope. Any unresolved state returns to locked/no-new-
exposure behavior.

### R9 — Market expansion, one market at a time

For commodities, Indian equities/Upstox, or any other class:

```text
analysis-only
 -> preregistered market-specific evidence
 -> historical pass
 -> forward shadow pass
 -> broker + truth + risk + lifecycle pass
 -> owner scope/capital approval
 -> protected-file review/reapproval
 -> separately authorized executable scope
```

Failure at any stage leaves that market analysis-only.

## Non-goals

This roadmap does not promise:

- guaranteed profitability;
- zero-loss trading;
- automatic market expansion;
- self-authorized capital;
- self-modified risk;
- autonomous approval of a new broker or data source;
- per-trade ChatGPT approval.

The goal is evidence-backed, fail-closed autonomy inside an owner-defined envelope.
