# Trip's Commodity Readiness Layer

## Status

**Capability built; commodity execution remains disabled.**

The approved Trip's configuration is still `US_EQUITY_CASH_LONG_ONLY` with `SPY / QQQ / AAPL`. This layer does not change that configuration, the frozen Constitution, capital authority, broker authority, or the live-money lock.

## What is implemented

1. **Hash-bound contract master** — exact executable contracts only. Continuous/back-adjusted research series are rejected as execution identities.
2. **Lifecycle facts** — settlement type, First Notice Date for physical-delivery contracts, Last Trade Date, expiration, multiplier, tick size, venue, sector, currency, evidence source and observation time.
3. **Owner-scoped universe policy** — approved roots, venues and sectors plus metadata freshness. No commodity symbol or production threshold is defaulted by Trip's.
4. **Independent market truth** — primary and secondary evidence can be required to be real-time, current, contract-identical and from independent source families. Price or venue-state disagreement fails closed.
5. **Liquidity evidence** — reported volume and open interest must be non-zero before analysis eligibility.
6. **TASK lifecycle handoff** — the exact contract produces `ContractLifecycleEvidence`; TASK independently applies owner-approved First Notice / Last Trade buffers at the execution boundary.
7. **No silent roll or substitution** — the scanner exposes eligible exact contracts but never changes an existing proposal from one contract to another. A different contract requires a new strategy proposal and a fresh full evaluation.

## Authority separation

```text
contract master + two-source market evidence
                  |
                  v
        Commodity Readiness Gate
        (analysis eligibility only)
                  |
                  v
          autonomous Trip's brain
        chooses exact contract/idea
                  |
                  v
            frozen Risk sizing
                  |
                  v
               TASK kernel
      lifecycle/liquidity/execution safety
                  |
                  v
        immutable ExecutionIntent
                  |
                  v
     broker + owner authority gates
```

Commodity Readiness has **zero broker mutation authority, zero capital authority and zero symbol-substitution authority**.

## Inputs still required before activation

Engineering can prove the machinery without inventing live facts. Activation still requires owner-approved commodity roots/venues/sectors; owner-approved TASK and Capital Governor values; an authoritative contract-master source; entitled independent real-time market sources; read-only verification against the intended live brokerage account; the owner's Ed25519 trust root and separate signed owner acts; and out-of-sample/walk-forward evidence for the actual commodity strategies.

Until those exist and the frozen live transition is separately authorized, commodity capability remains non-executing.
