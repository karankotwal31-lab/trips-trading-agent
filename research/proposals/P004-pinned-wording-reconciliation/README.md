# P004 — protected wording reconciliation

## Rationale

Pinned operator/runtime wording currently mixes two different concepts:

1. frozen authority/configuration remains mode=paper and PAPER_FIRST blocks live transmission;
2. the additive execution route is designed live-money-only and contains no simulated-broker stage.

Both are true, but phrases such as paper-only and live-money-only can look contradictory when they
are not explicitly scoped.

This proposal reconciles protected wording to the owner-approved D1 truth without changing any
execution behavior.

## Security boundary

PAPER_FIRST remains explicit and stronger in wording: the build cannot submit live-money orders
while the rule is in force. Config mode remains paper. No gate, lifecycle transition, risk value,
broker capability, or authority check changes.

The capability registry no longer claims the whole system is simply paper-only; it explicitly says
the current mode is paper, an additive live-only route exists, and transmission is locked.

Workflow comments become consistent with the no-order shadow-evidence model.

## Risk

The risk is editorial ambiguity in security-sensitive text. Tests therefore assert all three facts:

- mode=paper;
- live-money-only additive route exists;
- live transmission remains locked by PAPER_FIRST.

No functional code path is changed.
