# P002 — backtest position-management parity

## Rationale

The frozen trading cycle evaluates an existing stop/target first and only then, from the current
bar close, moves protection to break-even or a trailing stop for subsequent bars. The current
backtest never performs those protective-stop adjustments.

That means backtest trade paths can differ from the runtime semantics even when signals, ATR,
costs and causal fills match.

## Proposed behavior

The patch adds one backtest helper with the same break-even/trailing equations and causal ordering
as forge_agent.apply_position_management:

- existing stop/target are tested first;
- if no exit occurred, favorable R is computed from the bar close;
- break-even threshold may raise stop to entry;
- trailing threshold may raise stop to close minus ATR multiple;
- the adjusted stop cannot retroactively trigger on the same bar.

The proposal does not import forge_agent into backtest, avoiding a hidden runtime dependency. A
pinned parity test executes both implementations on the same state/bar and compares stop/protection
state.

## Risk

Historical synthetic backtest results will change because exits become closer to runtime semantics.
That change is expected and must not be used to retune thresholds after observing results.

## Fail-closed / conservative behavior

The patch preserves the existing conservative ambiguous-bar rule: if stop and target are both
inside one OHLC bar, stop wins. It changes no risk limit, signal threshold, cost assumption, live
gate or execution authority.
