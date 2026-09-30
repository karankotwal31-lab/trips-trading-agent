# P003 — correlated-exposure cap

## Rationale

The current total exposure and position-count limits do not distinguish three independent positions
from three highly correlated positions. Within the current SPY/QQQ/AAPL mandate, correlated longs
can therefore consume the total exposure budget as though they were independent risks.

## Proposed control

The patch adds an explicit correlation bucket configured by the owner-reviewed config:

- max_correlated_exposure_pct = 0.20
- group = SPY, QQQ, AAPL
- the correlated cap must be strictly tighter than max_total_exposure_pct;
- a symbol can belong to only one group;
- groups may contain only validated symbols.

Enforcement is defense in depth:

1. forge_gate refuses new entries when the bucket is already exhausted;
2. the frozen-cycle pending-entry path caps quantity to remaining bucket headroom;
3. live preflight caps its risk-authorized quantity to the same remaining bucket headroom.

Unknown/non-numeric held-position economics are treated as infinite correlated exposure, creating no
fake headroom.

## Risk

This is deliberately restrictive and can lower position sizes or reject trades that currently pass.
It may change historical diagnostics and simulated results. Those changes must not be used to loosen
the cap after observing performance.

Static correlation groups are conservative and do not claim a statistically stable live
correlation estimate. A future dynamic correlation model would require its own evidence and review.

## Fail-closed behavior

- malformed group config -> ConfigError
- group includes unvalidated symbol -> ConfigError
- symbol appears in multiple groups -> ConfigError
- correlated cap is not tighter than total cap -> ConfigError
- unknown held-position economics -> no correlated headroom
- bucket exhausted -> Forge rejection / zero candidate quantity

No existing total exposure, daily loss, drawdown, PAPER_FIRST or owner authority gate is loosened.
