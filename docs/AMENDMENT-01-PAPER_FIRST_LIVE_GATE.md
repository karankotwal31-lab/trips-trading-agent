# Amendment 01 — `PAPER_FIRST` → `LIVE_GATE`

**Status: DRAFT — NOT IN FORCE. NOT IMPLEMENTED. NO CODE, CONFIG, MANIFEST OR FINGERPRINT HAS BEEN CHANGED.**

| Field | Value |
|---|---|
| Document ID | `AMENDMENT-01` |
| Target invariant | Constitution rule 13, `PAPER_FIRST` |
| Proposed replacement | `LIVE_GATE` (new rule 13) + rules 36–42 (below) |
| Affects frozen core | **Yes — 4 frozen files + 3 locked fingerprints** |
| Authority to enact | Owner only. No autonomous component may apply, schedule, or prepare-execute this amendment. |
| Baseline at time of drafting | 125/125 tests PASS; frozen core 16/16 OK; `live_execution_present: false` |
| Decision required from owner | Approve / Reject / Amend (see §18) |

---

## 1. The invariant being amended

Current rule, `engine/constitution.py`, frozen at SHA-256 `d67c10e31305a6149539084cee2407dd30b0532705ef776c9d7a3cb97289a06b`:

> **13. `PAPER_FIRST`** — This build cannot submit live-money orders.

It is enforced at three independent frozen code sites. All three must change; changing fewer than all three produces a system that either lies or fails inconsistently:

| # | Site | Current enforcement |
|---|---|---|
| 1 | `engine/constitution.py` | `constitution_gate()` → `{"name": "paper_first", "passed": mode == "paper", "detail": "mode must remain paper"}`. The gate returns `passed = all(checks)`, so **no non-paper mode can pass the Constitution**, regardless of Truth or Risk. |
| 2 | `engine/config_guard.py` (`2ca851a6…4523`) | `_need(cfg.get("mode") == "paper", "Trip's hard constitution permits paper mode only")` |
| 3 | `engine/risk.py` (`e2158ce4…2036`) | `GateCheck("paper_mode", config.get("mode") == "paper", "Live execution is disabled by design.")` |

Locked downstream artifacts: `engine/config.json` `"mode": "paper"` (fingerprint `270fbb5f…aa15`, also hashed inside `engine/approved_build.json`), and `engine/capability_registry.json` (`"execution": "paper-only"`, `not_supported[0] == "live-money order submission"`).

**Why this cannot be dodged.** The prohibition is not a configuration value that can be flipped; it is a policy assertion enforced in three frozen layers, one of which (`constitution_gate`) is the terminal gate for every trade decision. Any implementation that attempts live execution without amending all three will be blocked at `constitution_gate` and must not be worked around.

---

## 2. The problem with simply deleting `PAPER_FIRST`

`PAPER_FIRST` currently does double duty:

1. It **declares** that this build is research-only.
2. It **enforces** the absence of a live capital path.

Deleting it removes both, leaving the terminal gate with no live-capital assertion at all. That would be a net reduction in safety, because the frozen core contains **no** broker gateway, adapter, capital governor, execution-intent model, or execution-authority gate today. `PAPER_FIRST` is currently the *only* thing standing between an authorized paper decision and real capital.

Therefore this amendment must **replace**, not remove. The replacement must be at least as strong at the point of capital release, and must be the *last* check in a strictly ordered chain, not the only one.

---

## 3. Proposed replacement rule text

Exactly as it would appear in `engine/constitution.py`:

> **13. `LIVE_GATE`** — No component may cause a live-money order to be transmitted except through the Execution Authority Gate. The Execution Authority Gate may set `EXECUTION_AUTHORIZED = TRUE` only when `TRADE_VALID == TRUE` and `CAPITAL_RELEASE == TRUE` are both independently and deterministically established, and the runtime occupies an owner-authorized `LIVE_ENABLED` state valid for the present build, configuration, risk profile, Capital Governor profile, broker adapter, brokerage account and environment. Absent any one of those, the result is `EXECUTION_BLOCKED`. This build ships in `PAPER`. This rule does not itself grant live authorization.

And the corresponding `constitution_gate` check, replacing the current `paper_first` check:

```
mode == "paper"
  OR (mode == "live"
      AND live_authorization_valid(...)          # §7 artifact, owner-signed, unexpired, identity-matched
      AND truth_trusted_for_trade                # unchanged, still required
      AND capital_governor_permits(...)          # §10
      AND execution_gate_authorized(...))        # §6, two-permission aggregation
```

Invariant to preserve: **live mode must require a strict superset of paper mode's checks.** The amendment may add checks; it may remove none. This is a reviewable, testable property.

---

## 4. New constitutional rules proposed alongside rule 13

Added to `NON_NEGOTIABLES` in the same amendment:

| # | Rule | Statement |
|---|---|---|
| 36 | `EXECUTION_GATE_IS_DUMB` | The Execution Authority Gate contains no trading intelligence. It cannot create, resize, reprice, retime or re-route an order. It aggregates already-computed authority and answers only `EXECUTION_AUTHORIZED = TRUE/FALSE`, recording the reason. |
| 37 | `TWO_INDEPENDENT_PERMISSIONS` | Every live order requires `TRADE_VALID` and `CAPITAL_RELEASE`, derived independently. Neither may be inferred from the other. The Gate may not override either. |
| 38 | `BROKER_PROVES_EXECUTION` | The broker is authoritative for what externally happened. Trip's canonical state is derived from broker events plus reconciliation; no local component may assert an execution the broker has not confirmed. |
| 39 | `NO_UNREPRESENTABLE_INTENT` | A broker adapter may translate representation but may never alter economic meaning. Silent limit→market conversion, quantity rounding, side change, symbol substitution, time-in-force substitution or price modification is forbidden. On inability to represent the approved intent: `CAPABILITY_UNSUPPORTED` / `ORDER_INCOMPATIBLE`, and no trade. |
| 40 | `INTENT_FRESHNESS_BOUND` | Every execution intent carries an expiry consistent with causal execution. After broker outage, auth delay, restart, reconciliation, feed interruption or network loss, freshness is re-proven before submission. Expired intent → `INTENT_EXPIRED`. Expiry is never auto-extended and an old approval is never re-priced against new data. |
| 41 | `UNKNOWN_RESOLVES_FROM_EVIDENCE` | A submission whose response is lost moves to `UNKNOWN_PENDING_RECONCILIATION`. It is never blindly resubmitted. Another submission is considered only after proving the first was not accepted and confirming the intent remains valid. Persistent ambiguity means NO NEW EXPOSURE. |
| 42 | `AI_CAN_ONLY_TIGHTEN` | The AI Supervisor may observe, explain and request a halt. It may never produce a broker-executable order, and may never increase exposure, risk, capital, leverage, position size or drawdown tolerance, nor resume trading, modify an order, change a strategy, promote a model, change a limit, or switch broker or account. |

Rules 36–42 are **additions**. None of the existing 35 rules (1–34 plus the existing `PAPER_FIRST` slot) is weakened by this amendment.

---

## 5. The frozen floor

Rules that this amendment **explicitly does not** touch, and that no later autonomous process may amend:

- `TRUTH_OVER_ACTION`, `FAIL_CLOSED`, `EVIDENCE_BEFORE_INFERENCE`, `NO_FAKE_CERTAINTY`
- `RISK_BEFORE_RETURN`, `NO_MARTINGALE`
- `CLOSED_BARS_ONLY`, `CAUSAL_EXECUTION`
- `INSTRUMENT_SCOPE_LOCK`, `VALIDATED_SYMBOL_SCOPE`
- `STATE_INTEGRITY`, `ATOMIC_STATE_COMMIT`, `RECOVERY_WITHOUT_ROLLBACK`
- `EXECUTABLE_BUILD_LOCK`, `NO_SILENT_MODEL_DRIFT`, `NO_AUTONOMOUS_POLICY_MUTATION`
- `EVOLUTION_IN_QUARANTINE`, `SELF_HEALING_BOUNDARY`
- `SUPERVISOR_ADVISORY_ONLY`, `RELAY_BACKPRESSURE`, `AUTHENTICATED_SUPERVISOR_ACK`
- `HEALTH_BEFORE_TRADING`, `DURABLE_ESCALATION`, `DECISION_MIRROR`
- `OPERATOR_SURFACE_TRUTH`
- plus new rules 36–42 above.

Standing interpretation added by this amendment:

> Unknown state means NO NEW EXPOSURE. Unresolved anomaly means NO NEW EXPOSURE. Failure to prove eligibility means NO NEW EXPOSURE.

**Autonomy principle (restated, binding):** autonomous mechanisms may reduce risk but may never increase the owner's approved maximum financial authority. They may reduce allowed exposure, halt new exposure, or enter a restricted state. Raising maximum capital, maximum position size, leverage, drawdown tolerance or loss limits requires the approved configuration-change process and explicit owner authorization.

---

## 6. Authority chain (replaces `PAPER_FIRST` as the terminal safety assertion)

```
Market Data → Truth Engine → Strategy Engine → Risk Engine → Capital Governor
  → Constitution Validation → Execution Authority Gate → Universal Broker Gateway
  → Broker Adapter → Authorized Broker Transport → Real Broker
  → Broker Events → Reconciliation Engine → Canonical State
  → Audit / Guardian / Student / Supervisor Observability
```

Non-bypass invariants, each to be asserted by test:

- No broker adapter may call Strategy.
- No Strategy component may call a broker directly.
- No AI Supervisor may call `submit_order()`.
- No Student or Evolution component may reach the execution path.
- The Gate may not be skipped by any caller.

`TRADE_VALID` = truth valid ∧ strategy decision actionable ∧ risk approved ∧ constitution compliant ∧ scope valid ∧ intent current.
`CAPITAL_RELEASE` = Governor permits exposure ∧ broker state reconciled ∧ broker healthy ∧ correct account connected ∧ live environment verified ∧ build/config integrity verified ∧ no unresolved execution ambiguity ∧ no applicable halt.

---

## 7. Live authorization artifact (new, owner-controlled)

A new versioned, hash-pinned record — proposed `engine/live_authorization.json` + sidecar digest — binding:

approved strategy/build identity · approved config identity · approved risk profile · approved Capital Governor profile · broker adapter identity · brokerage account identity · environment · issuance timestamp · expiry · owner signature.

Any material change to strategy code, Risk rules, Capital Governor rules, Truth rules, symbol scope or execution semantics **invalidates the artifact**, returning the runtime to `LIVE_LOCKED` until verification completes again. This is the mechanism that makes §8 of the source specification ("immutable approved identity") enforceable rather than aspirational.

---

## 8. Lifecycle and no self-promotion

`RESEARCH → BACKTEST → SHADOW → PAPER → LIVE_LOCKED → LIVE_ENABLED`

Nothing promotes itself. Student, Evolution, Strategy, Supervisor, Guardian, broker connectivity and passing tests are **not** promotion authorities. `LIVE_ENABLED` requires explicit owner authorization **plus** deterministic verification (§9). The system ships at `LIVE_LOCKED`.

Note the deliberately split acts in §18: **granting live authorization ≠ enabling live trading.** Two separate owner decisions.

---

## 9. `LIVE_ENABLED` preconditions (deterministic — not an LLM judgement)

All must be provably `TRUE` for the present build, configuration and runtime:

| # | Precondition | Source of truth |
|---|---|---|
| 1 | Approved strategy/build identity | `build_guard.verify_build_integrity()` |
| 2 | Approved configuration identity | `config_guard.fingerprint_config()` vs `approved_config.sha256` |
| 3 | Approved risk profile | config + Risk engine |
| 4 | Approved Capital Governor profile | §10 |
| 5 | Correct live brokerage account | broker adapter |
| 6 | Correct authorized broker adapter | capability registry |
| 7 | Correct environment | live authorization artifact |
| 8 | Healthy authentication | broker adapter |
| 9 | Healthy broker connection | broker adapter health |
| 10 | Clean reconciliation | Reconciliation Engine (zero unresolved discrepancies) |
| 11 | Healthy market data | Truth Engine |
| 12 | Valid market session | session/calendar truth |
| 13 | No unresolved broker orders | reconciliation |
| 14 | No unresolved local/broker discrepancy | reconciliation |
| 15 | Valid Truth state | Truth Engine |
| 16 | Valid system clock/timezone | timezone-aware session checks |
| 17 | Global halt not active | `portfolio.halted` / Safety Controller |
| 18 | Loss/drawdown limits not breached | Risk + Capital Governor |
| 19 | Supervisor not in `SUPERVISOR_HALT_REQUEST` | AI Supervisor (§11) |
| 20 | Supervisor availability policy satisfied | `SUPERVISOR_UNAVAILABLE` handling |

---

## 10. Capital Governor boundary

New deterministic component. It owns account/portfolio boundaries and is separate from per-trade Risk. Proposed enforced boundaries: maximum deployable capital, maximum order value, maximum position exposure, maximum portfolio exposure, maximum concentration, maximum daily loss, maximum drawdown, maximum number of positions, maximum new exposure per period.

**Values are an owner input and are intentionally left unset in this draft. No financial limit has been invented.** For reference, the frozen `config_guard.HARD_LIMITS` ceilings currently in force are:

| Bound | Current hard ceiling |
|---|---|
| `max_risk_per_trade_pct` | 0.01 |
| `max_daily_loss_pct` | 0.03 |
| `max_total_exposure_pct` | 0.50 |
| `max_open_positions` | 5 |
| `halt_on_drawdown_pct` | 0.10 |
| `min_signal_score_floor` | 0.65 |
| `max_data_age_minutes` | 240 |
| `max_assumed_spread_bps` / `max_slippage_bps` | 50 / 100 |

**Proposed binding rule for the first live release:** Governor values may not exceed the `HARD_LIMITS` ceilings above. `HARD_LIMITS` remain unmodifiable by any autonomous component and must be re-reviewed as part of the atomic change set (§12). The Governor is layered *above* Risk and must never become a second competing risk authority.

---

## 11. AI Supervisor — one-way safety authority

The existing Supervisor architecture (`supervisor_bridge.py`, `supervisor_counsel.py`, `supervisor_relay.py`) is reused, not duplicated. Proposed structured output vocabulary: `NORMAL` · `CAUTION` · `INVESTIGATE` · `SUPERVISOR_HALT_REQUEST`, each with evidence references and explanation, delivered via a sanitised structured evidence packet.

One-way rule: a `SUPERVISOR_HALT_REQUEST` may be translated by the deterministic Safety Controller into **blocking NEW exposure only**. The Supervisor may not resume trading, increase risk, create exposure, modify an order, change a strategy, promote a model, change limits, or switch broker/account. Resumption requires deterministic recovery conditions and, where configured, explicit owner authorization.

Availability: AI supervision is **not** a required network hop for any order. If unavailable, record `SUPERVISOR_UNAVAILABLE` and follow the explicitly configured safety policy. Absence of AI is never interpreted as approval. The architecture must remain safe with the AI provider entirely absent.

---

## 12. Atomic change set

These must change together, in this order, in a single reviewed change. A partial application is a defect.

| Step | Artifact | Current identity | Change |
|---|---|---|---|
| 1 | `engine/constitution.py` | `d67c10e3…a06b` (frozen) | Rule 13 text; `constitution_gate` live branch; rules 36–42 |
| 2 | `engine/config_guard.py` | `2ca851a6…4523` (frozen) | Allowed `mode` set; live-mode hard ceilings; require live authorization artifact when `mode == "live"` |
| 3 | `engine/risk.py` | `e2158ce4…2036` (frozen) | `paper_mode` check → mode-aware check with strict-superset semantics |
| 4 | `engine/config.json` | `270fbb5f…aa15` + build-hashed | Add `live` block; `mode` semantics |
| 5 | `engine/capability_registry.json` | build-hashed | `execution` value; `not_supported` revision |
| 6 | **New** execution modules | — | Gateway, adapters, transports, intent, Gate, canonical states, idempotency, reconciliation, Capital Governor, lifecycle, AI Supervisor, live authorization |
| 7 | `engine/build_guard.py` | self-hashed | Register the new safety-critical modules in `CRITICAL_FILES` |
| 8 | `infra/core_v06.sha256` | `a00166f5…3252` | Superseded by a new core digest (e.g. `core_v09.sha256`) |
| 9 | `engine/approved_config.sha256` | `270fbb5f…aa15` | Re-issue via `approve_config.py` |
| 10 | `engine/approved_build.json` | `d67a692a…4ee0` | Re-issue via `approve_build.py` |
| 11 | `infra/approved_infra.json` | `1c673d0e…d059` | Re-issue via `approve_infra.py` if infra files change |
| 12 | `docs/TRIPS_CONSTITUTION.md` | build-locked | Rule 13 + rules 36–42 |
| 13 | `README.md`, `HARDENING_REPORT.md`, `INFRASTRUCTURE_STATUS.md`, `RELEASE_DIAGNOSTICS.json` | not locked | Remove contradicted live-execution claims; record the amendment |

Steps 8–11 are **deliberately last**: changing code without re-issuing fingerprints would trip `EXECUTABLE_BUILD_LOCK` and `verify_infra.py`, which is the intended behaviour.

---

## 13. Test surface impact

Existing tests that **must** change because they assert the current prohibition (all in `tests/test_engine.py`, which is not frozen):

| Test | Current assertion |
|---|---|
| `test_dashboard_export_never_claims_live_execution_or_health_trade_authority` | `payload["system"]["live_execution_supported"] is False` |
| `test_build_manifest_covers_dashboard_truth_surface` | `CRITICAL_FILES` / `CRITICAL_PROJECT_FILES` membership |
| `test_config_guard_*` | any `mode` rejection assertion |

New tests required (proposed, one per invariant):

1. `constitution_gate` live branch requires a strict **superset** of paper checks.
2. Gate returns `EXECUTION_BLOCKED` when either permission is independently false.
3. Gate cannot be bypassed by any caller in the authority chain; no adapter→Strategy or Strategy→broker path exists.
4. Adapter cannot alter economic meaning (limit→market, qty rounding, side, symbol, TIF, price).
5. `INTENT_EXPIRED` after each staleness trigger in §40.
6. Lost submission → `UNKNOWN_PENDING_RECONCILIATION`, no blind resubmit, resubmit only after proof of non-acceptance.
7. Long-only / scope violations rejected at the execution boundary (defense in depth).
8. `SUPERVISOR_HALT_REQUEST` blocks new exposure and nothing else; Supervisor cannot resume or increase.
9. `SUPERVISOR_UNAVAILABLE` follows configured policy and never implies approval.
10. Lifecycle: no component can promote; `LIVE_ENABLED` unreachable without the authorization artifact.
11. Material change to any bound identity invalidates live authorization → `LIVE_LOCKED`.
12. `UNVERIFIED` broker capability fails closed; `BROKER_AUTOMATION_UNSUPPORTED` when no authorized programmable interface exists.
13. Capital Governor cannot exceed `HARD_LIMITS`; no autonomous increase path.
14. Plugin/connector classification — natural-language "Order placed" is never accepted as evidence.

---

## 14. External preconditions that block implementation of this amendment

Not code. These are separate, owner-verifiable prerequisites. Until they close, implementation of this amendment should not begin:

1. **Strategy verdict is `UNPROVEN`.** Latest synthetic causal stress: worst net P&L ≈ **−$5,347.07**, best ≈ **+$163.97**, worst drawdown ≈ **5.385%**, all small-sample. Enabling real capital behind an unproven edge is a commercial decision for the owner, and must be recorded as such.
2. **No broker integration exists.** No gateway, adapter or transport is present anywhere in the repository.
3. **No verification of profitability evidence.** Real multi-regime historical / walk-forward evidence is outstanding.
4. **Market data is `DEMO`.** No real feeds, no entitlement/provenance evidence, no independent verifier configured.
5. **No formal exchange-calendar / session engine.** Sessions, holidays, early closes and DST transitions are not covered; the source specification makes session truth mandatory before new exposure.
6. **No durable hosted supervisor bridge.** `transport_mode` remains `local_outbox_until_hosted_bridge`.
7. **No live brokerage credentials exist** and must not be supplied until §12 is complete and reviewed.

---

## 15. What this amendment does NOT authorize

- **No change to instrument scope.** Remains `US_EQUITY_CASH_LONG_ONLY`, 60-minute bars, `SPY` / `QQQ` / `AAPL`. Broker capability does not equal Trip's authority.
- No short selling, leverage, derivatives, options, futures, FX or crypto.
- No new symbols, strategies or timeframes.
- No increase in any `HARD_LIMITS` ceiling.
- No autonomous promotion of Student or Evolution output.
- No bypass of Truth, Risk, Forge Gate, Guardian, Decision Mirror or the Supervisor boundary.
- No screen-coordinate automation, DOM clicking, CAPTCHA or MFA bypass, stolen sessions, undocumented or reverse-engineered private APIs, or credential replay. Where no authorized programmable interface exists: `BROKER_AUTOMATION_UNSUPPORTED`.
- **No live order authority is created by this document.** It is a proposal awaiting owner decision.

---

## 16. Emergency halt and rollback

- A deterministic Safety Controller may block new exposure immediately and enter a restricted state.
- **Cancellation is a separate question.** Per the source specification, a safety system may not be assumed to cancel arbitrary working orders unless that behaviour is explicitly part of the approved risk/halt policy. Who or what caused every cancellation must be recorded.
- No self-healing action may restore an older trading state to make the system run (`RECOVERY_WITHOUT_ROLLBACK`).
- Reverting this amendment means reverting the full §12 change set and re-issuing all four fingerprints — not editing the code back in place.
- Recommended owner-reviewable distinction: **L1** reduce/restrict · **L2** block new exposure (autonomous) · **L3** flatten/close existing positions (owner-authorized only).

---

## 17. Residual risks and open items (flagged, not resolved)

1. **The source specification is truncated.** It ends mid-sentence in §27 at `"BROKER_ACK`. The canonical execution-state list is incomplete and all sections after §27 are absent. Reconciliation details, conformance requirements, deployment and QA sections cannot be reviewed until supplied.
2. **The test suite is not integrity-pinned.** `build_guard.CRITICAL_FILES` contains no test file, and `infra/approved_infra.json` covers no test file. The 125 tests that substantiate every safety claim in this repository can therefore be weakened **without** tripping `EXECUTABLE_BUILD_LOCK`. Recommended hardening, independent of this amendment: register `tests/` and `infra/tests/` in the build and infra manifests.
3. **No CI test gate.** No workflow under `.github/workflows/` invokes any test runner; `tests/run_tests.py` also omits the Student and cloud-shell suites. The "125/125 PASS" baseline was produced manually. A live-capital system should not depend on a manually-run, partially-wired suite.
4. **Live mode increases the consequence of every existing gap.** Every unresolved item above is currently contained by `PAPER_FIRST`. Removing that containment transfers the burden to the new §6 chain, which does not yet exist.
5. **`HARD_LIMITS` live values are unreviewed.** They were chosen for a paper research system.

---

## 18. Approval procedure (human-gated, in order)

1. **Owner reads this document.** Decision: Approve / Reject / Amend. (§18.0 — *this step is where we are now.*)
2. If Approve: supply the missing specification sections (§17.1) and close §14 preconditions 1–6.
3. Owner supplies Capital Governor values (§10) and the `HARD_LIMITS` live review.
4. Implement the §12 change set on a branch. No autonomous component may perform or schedule this.
5. Run all four suites plus the §13 new tests; record the new baseline.
6. Re-issue fingerprints in the §12 order; verify `verify_infra.py`, `sha256sum -c`, and `verify_build_integrity()` all pass.
7. **Owner decision A — grant live authorization.** Creates the §7 artifact. The system still ships `LIVE_LOCKED`.
8. **Owner decision B — enable live trading.** Moves the runtime to `LIVE_ENABLED`. Separate, explicit, revocable, and the only step that permits real capital release.

Steps 7 and 8 are deliberately distinct. This amendment is scoped to making those steps *possible under deterministic control*, not to taking them.

---

## 19. Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Owner | | ☐ Approve ☐ Reject ☐ Amend | |
| Engineering review | | ☐ | |
| Security review | | ☐ | |

**Drafting note:** no file in the frozen core, no configuration value, no fingerprint and no manifest was modified in the production of this draft. `infra/core_v06.sha256` verifies 16/16 and the 125-test baseline is unchanged. This amendment takes effect only via an explicit owner decision and the §12/§18 procedure.
