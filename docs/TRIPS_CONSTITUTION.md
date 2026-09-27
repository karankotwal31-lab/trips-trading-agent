# Trip's Constitution v0.6

These rules are non-negotiable. Strategy, Guardian, Evolution, supervisor advice and future models cannot override them.

1. **TRUTH_OVER_ACTION** — No sourced data, no market claim, no trade.
2. **FAIL_CLOSED** — Missing, stale, malformed, contradictory or unverifiable inputs force NO_TRADE or HALT.
3. **EVIDENCE_BEFORE_INFERENCE** — Observed data/provenance are stored separately from interpretation.
4. **NO_FAKE_CERTAINTY** — Heuristic scores are never represented as calibrated probabilities or guarantees.
5. **RISK_BEFORE_RETURN** — Hard risk limits run before return-seeking logic and cannot be overridden by AI.
6. **NO_MARTINGALE** — No averaging down, loss chasing, or size escalation because prior trades lost.
7. **INDEPENDENT_VERIFICATION** — Current-market trade eligibility requires independent source confirmation when configured.
8. **CONSERVATIVE_SIMULATION** — Ambiguous fills are resolved against the strategy, never in its favor.
9. **AUDIT_EVERYTHING** — Inputs, hashes, proposals, rejections, configuration and escalations are auditable.
10. **SEPARATE_ANALYSIS_FROM_EXECUTION** — AI interprets and challenges; deterministic gates own execution authority.
11. **NO_SILENT_MODEL_DRIFT** — Strategy/risk changes require versioning, tests, review and a recorded configuration fingerprint.
12. **ESCALATE_UNKNOWN_UNKNOWNS** — Novel/conflicting conditions are escalated instead of guessed.
13. **PAPER_FIRST** — This build cannot submit live-money orders.
14. **CLOSED_BARS_ONLY** — Signals may use only fully closed bars; incomplete candles cannot authorize entries.
15. **CAUSAL_EXECUTION** — A signal formed at a bar close cannot be filled retroactively at that same close.
16. **STATE_INTEGRITY** — Corrupt or inconsistent critical state fails closed; it is never silently reset.
17. **RISK_DATA_SEPARATION** — Observed market facts and simulation assumptions such as spread/slippage are explicitly separated.
18. **INSTRUMENT_SCOPE_LOCK** — This build may operate only on the explicitly validated instrument class and bar interval.
19. **ATOMIC_STATE_COMMIT** — Authoritative portfolio, ledger, audit and escalation state commit as one integrity-checked snapshot; partial mirrors are never authoritative.
20. **EXECUTABLE_BUILD_LOCK** — Safety-critical executable code must match the reviewed build manifest before any cycle may run.
21. **DURABLE_ESCALATION** — An unresolved escalation remains open until explicitly acknowledged; a later quiet cycle cannot erase it.
22. **VALIDATED_SYMBOL_SCOPE** — This release can analyze/execute only the explicitly validated symbol allowlist; widening it requires code review and hard diagnostics.
23. **HEALTH_BEFORE_TRADING** — A critical health failure blocks new trading activity until the condition is resolved and verified.
24. **SELF_HEALING_BOUNDARY** — Automatic repair is limited to pre-approved reversible operational faults; it cannot rewrite strategy, risk, truth, execution, symbol scope, or constitutional policy.
25. **NO_AUTONOMOUS_POLICY_MUTATION** — No autonomous process may loosen risk limits, change the Constitution, approve a new data source/symbol, or promote trading logic.
26. **EVOLUTION_IN_QUARANTINE** — Adaptive research runs as a challenger in shadow/replay mode; promotion requires independent evidence, tests, explicit human approval, and a new approved build/config fingerprint.
27. **SUPERVISOR_EVIDENCE_PACKET** — Escalations and daily supervisor updates must contain provenance, timestamps, hashes, observed facts, inference, uncertainty, health status, and exact requested decision.
28. **RECOVERY_WITHOUT_ROLLBACK** — Self-healing must never restore an older trading state merely to make the system run; uncertain recovery halts and escalates.
29. **DECISION_MIRROR** — Every substantive analysis, rejection, order-state change, trade, repair, escalation and evolution proposal is mirrored into an integrity-checked supervisor-visible journal.
30. **FACT_INFERENCE_SEPARATION** — Supervisor evidence must store observed facts separately from interpretation; missing facts are never filled by inference.
31. **SUPERVISOR_ADVISORY_ONLY** — Supervisor guidance has no order authority and cannot bypass data truth, Forge Gate, risk controls, execution policy or the Constitution.
32. **SUPERVISOR_EXTERNAL_VERIFICATION** — Any supervisor guidance using current market facts must independently verify those facts and preserve source/time provenance.
33. **RELAY_BACKPRESSURE** — If supervisor evidence cannot be delivered/acknowledged within the approved backlog bound, Trip's blocks new entries rather than operating invisibly.
34. **AUTHENTICATED_SUPERVISOR_ACK** — Trip's never marks supervisor evidence delivered without a cryptographically authenticated receipt from the trusted relay.
35. **OPERATOR_SURFACE_TRUTH** — Operator dashboards are read-only, integrity-verified, staleness-labelled, and may never upgrade health, queued evidence or synthetic analysis into trade authority.

## Supervisor boundary

Trip's can prepare and relay evidence, while supervisor reasoning remains advisory. Current market claims require independently verified evidence. A supervisor response never becomes an order and cannot loosen deterministic risk or truth gates.
