# Amendment 01 — `PAPER_FIRST` → `LIVE_GATE`

**Status: REVIEWED — NOT APPROVED AS WRITTEN. NOT IN FORCE. No frozen file, configuration value, fingerprint or manifest has been changed.**

**Review verdict: `AMEND` (see §20).** The amendment's own enforcement mechanism — the owner
signature of §7 — was found to be forgeable by any caller. That defect has been found, fixed and
regression-tested in the execution layer. Approval is now blocked on the prerequisites in §20.3,
not on the text of §3.

| Field | Value |
|---|---|
| Document ID | `AMENDMENT-01` |
| Target invariant | Constitution rule 13, `PAPER_FIRST` |
| Proposed replacement | `LIVE_GATE` (new rule 13) + rules 36–42 (below) |
| Affects frozen core | **Yes — 4 frozen files + 3 locked fingerprints** |
| Authority to enact | Owner only. No autonomous component may apply, schedule, or prepare-execute this amendment. |
| Baseline at time of drafting | 125/125 tests PASS; frozen core 16/16 OK; `live_execution_present: false` |
| Baseline at review | **273/273 tests PASS** across 7 suites; frozen core 16/16 OK; infra manifest `1c673d0e…d059` |
| Ceiling reached | **`LIVE_READY_LOCKED`** — every remaining blocker is an owner credential, capital value or signature (§21.4) |
| Review verdict | **`AMEND` — not approvable as written** (see §20) |
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

**Why this cannot be dodged.** The prohibition is not a configuration value that can be flipped; it is a policy assertion enforced in three frozen layers, one of which (`constitution_gate`) is the terminal gate for every trade decision. Any implementation that attempts live execution without amending all three will be blocked and must not be worked around.

**There are three blockers, not one** — see §21.2. The Constitution rule, the `config_guard` mode
restriction, and the frozen Risk engine's own `forge_gate` `paper_mode` check each refuse live mode
for their own reason. `engine/execution/gate.py` probes all three by running the real frozen code,
and `blockers_to_live_release()` reports them by category rather than collapsing them.

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

The floor is **all 34 surviving rules**, in Constitution order. An earlier draft of this document
listed only 25 of them; the nine marked in **bold** were missing, which would have left them
outside the floor and readable as un-frozen:

- 1–4: `TRUTH_OVER_ACTION`, `FAIL_CLOSED`, `EVIDENCE_BEFORE_INFERENCE`, `NO_FAKE_CERTAINTY`
- 5–6: `RISK_BEFORE_RETURN`, `NO_MARTINGALE`
- 7–10: **`INDEPENDENT_VERIFICATION`**, **`CONSERVATIVE_SIMULATION`**, **`AUDIT_EVERYTHING`**, **`SEPARATE_ANALYSIS_FROM_EXECUTION`**
- 11: `NO_SILENT_MODEL_DRIFT`
- 12: **`ESCALATE_UNKNOWN_UNKNOWNS`**
- 14–16: `CLOSED_BARS_ONLY`, `CAUSAL_EXECUTION`, `STATE_INTEGRITY`
- 17: **`RISK_DATA_SEPARATION`**
- 18–26: `INSTRUMENT_SCOPE_LOCK`, `ATOMIC_STATE_COMMIT`, `EXECUTABLE_BUILD_LOCK`, `DURABLE_ESCALATION`, `VALIDATED_SYMBOL_SCOPE`, `HEALTH_BEFORE_TRADING`, `SELF_HEALING_BOUNDARY`, `NO_AUTONOMOUS_POLICY_MUTATION`, `EVOLUTION_IN_QUARANTINE`
- 27: **`SUPERVISOR_EVIDENCE_PACKET`**
- 28–29: `RECOVERY_WITHOUT_ROLLBACK`, `DECISION_MIRROR`
- 30: **`FACT_INFERENCE_SEPARATION`**
- 31–32: `SUPERVISOR_ADVISORY_ONLY`, **`SUPERVISOR_EXTERNAL_VERIFICATION`**
- 33–35: `RELAY_BACKPRESSURE`, `AUTHENTICATED_SUPERVISOR_ACK`, `OPERATOR_SURFACE_TRUTH`
- plus new rules 36–42 above.

Rule 13 `PAPER_FIRST` is the **only** rule this amendment touches. That is now an exact,
machine-checkable statement rather than prose: `verify_amendment()` refuses any proposal that
removes a rule other than the prohibition, or adds a rule other than the authorizing rule.

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

**Required enforcement — added at review, see §20.1 and §21.1.** "Owner-signed" must be a
cryptographic fact, not a boolean. Implemented in `engine/execution/owner_authority.py`: an
**Ed25519** signature (RFC 8032) over a domain-separated frame of the artifact's canonical payload,
verified against the owner's **public** key in `engine/owner_public_key.json`. The private key never
enters this repository, this process, or any environment variable; the public key is public
material protected against substitution by build integrity. With no key installed, every owner act
fails closed as `OWNER_AUTHORITY_KEY_NOT_CONFIGURED`. Independently, a release basis is refused
unless accompanied by a **valid signed** authorization, so owner decision A cannot imply decision B.

Replay protection is layered: a purpose tag (an amendment signature can never be replayed as a live
authorization), a signature version, a key id that must equal the pinned key, content binding, and
the artifact's own validity window.

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
| 6 | **New** execution modules | **built; not frozen** | Gateway, adapters, transports, intent, Gate, canonical states, idempotency, reconciliation, Capital Governor, lifecycle, AI Supervisor, live authorization — already implemented additively in `engine/execution/`, outside the frozen core |
| 6a | **New** owner trust root | — | `engine/execution/owner_authority.py` and `scripts/sign_owner_artifact.py`. The signing key is owner-held and must never enter the repository. |
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
2. ~~No broker integration exists.~~ **Partially closed.** The gateway, adapter/transport contract, registry, intent model, gate, Capital Governor, reconciliation, supervisor and lifecycle now exist in `engine/execution/` — additive, outside the frozen core. **No real broker adapter ships**, deliberately: a live one needs an authorized programmable interface plus owner credentials, and must pass the same conformance and reconciliation requirements. Where no authorized interface exists the answer remains `BROKER_AUTOMATION_UNSUPPORTED`.
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
2. ~~The test suite is not integrity-pinned.~~ **RESOLVED.** `tests/suite_integrity.py` pins every safety suite, the gate script `scripts/verify_all.sh`, the pre-push hook and the CI workflow in `tests/approved_tests.json`; drift fails the run. Verified by tampering with a pinned file, which produced `unapproved safety-suite drift detected` and a non-zero exit.
3. ~~No CI test gate.~~ **RESOLVED.** `.github/workflows/trips-safety-suites.yml` runs `scripts/verify_all.sh` on push and pull request — the same script runnable locally, so local and CI cannot diverge. It has executed green since the repository's Actions entitlement was restored.
4. **Live mode increases the consequence of every existing gap.** Every unresolved item above is currently contained by `PAPER_FIRST`. Removing that containment transfers the burden to the new §6 chain, which does not yet exist.
5. **`HARD_LIMITS` live values are unreviewed.** They were chosen for a paper research system.
6. **The owner-signature mechanism was forgeable as drafted. FOUND AT REVIEW, NOW CLOSED.** The
   overriding mechanism this amendment depends on conveyed authority through a boolean any caller
   could set. Full finding and reproduction in §20.1.

---

## 18. Approval procedure (human-gated, in order)

1. **Owner reads this document.** Decision: Approve / Reject / Amend. (Engineering and security review are recorded in §20; §20.3 states precisely what still blocks approval.)
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
| Owner | **left blank deliberately — only the owner may decide this row, and it cannot be signed by a program** | ☐ Approve ☐ Reject ☐ **Amend** | |
| Engineering review | Trip's execution-layer review | **`AMEND`** — mechanism sound after the §20.1 repair; prerequisites in §20.3 outstanding | 2026-09-29 |
| Security review | Trip's execution-layer review | **`AMEND`** — one critical authorization-integrity defect found and fixed; no unfixed defect remains in the mechanism | 2026-09-29 |

---

## 20. Review record (engineering and security)

### 20.1 Critical finding: the owner signature was a boolean

`verify_amendment()` accepted a proposal carrying `owner_signed=True` — a plain field any caller
sets. The chain that followed was complete and released real capital:

```
AmendmentProposal(owner_signed=True)   -> AMENDMENT_APPLICABLE
release_basis_from_verdict(verdict)    -> CoreStateBasis(source="VERIFIED_AMENDMENT")
Lifecycle(Stage.LIVE_ENABLED, release_basis=basis) -> may_transmit_live()["permitted"] is True
UniversalBrokerGateway.submit(...)     -> outcome TRANSMITTED, adapter.submitted non-empty
```

Reproduced against the committed tree: **a live order reached the broker adapter while
`PAPER_FIRST` and the `config_guard` mode restriction were still in force on disk**
(`prohibition_rule_present == ['PAPER_FIRST']`, permitted modes `['paper']`). The frozen-boundary
verdict for honest callers was never affected — this was an authorization-integrity hole, not a
boundary-logic bug — but it defeated the exact claim this amendment rests on, and the module
docstring's assertion that it "cannot be triggered by a caller passing a convenient argument" was
false as written.

Two further paths compounded it:

| # | Path | Consequence |
|---|---|---|
| 1 | `LiveAuthorization(owner_signed=True)` — also a boolean | The two owner acts collapsed into one: decision A implied decision B by construction |
| 2 | `Lifecycle(Stage.LIVE_ENABLED, release_basis=basis)` accepted any basis with no authorization at all | The transition machine and its owner-authorization requirement were bypassable at construction time |

### 20.2 Repair (implemented and regression-tested)

- **`engine/execution/owner_authority.py`** (new) — the trust root. **Superseded by Ed25519 in §21.1.**
  The original HMAC design required the owner to hand over a shared secret that the verifying
  process could also sign with. It is replaced by an asymmetric root; the residual assumption below
  no longer applies.
- **`owner_signed` booleans deleted** from both `AmendmentProposal` and `LiveAuthorization`. Passing
  one is now a `TypeError`; authority is the `signature` field only.
- **Signatures cover contents**, so a proposal cannot be altered after signing — changing
  `target_mode`, the rule lists or even `amendment_id` yields `OWNER_SIGNATURE_INVALID`.
- **`Lifecycle` refuses a release basis** without a valid signed authorization: a basis is evidence,
  not authority, so decision A can no longer imply decision B.
- **Preflight refuses an unsigned authorization artifact** for the approved-capital-governor
  precondition, instead of treating a well-formed but unauthorized artifact as evidence.
- **`scripts/sign_owner_artifact.py`** (new) — the owner-side signing tool for the two acts.
  `--status` reports whether owner acts are possible, and prints no key material.
- **8 new tests**, including a regression that replays the attack above and asserts nothing reaches
  the broker, plus the honest path (signed proposal **and** signed authorization) still transmitting.

Stated plainly: the HMAC design's residual assumption — *a process that can read the owner key can
sign* — **no longer applies**, because there is no key in the process to read. See §21.1.

### 20.3 What still blocks approval

Approval is **not** blocked by the text of §3 — the `LIVE_GATE` replacement is sound, and the
transmission path is real code provably gated by the frozen boundary. It is blocked by:

1. **No owner key exists.** `owner_authority_status()` reports `configured: false`, so no owner act
   is currently possible at all. This is correct fail-closed behaviour, and it is also the state in
   which approval would be meaningless.
2. **§14 prerequisites 1, 3, 4, 5, 6 remain open** — strategy `UNPROVEN`, no walk-forward
   profitability evidence, market data still `DEMO`, no exchange-calendar/session engine, no durable
   hosted supervisor bridge.
3. **Capital Governor values are still unset** (§10). They are an owner input and none was invented.
4. **The source specification is still truncated mid-§27** (§17.1), so the canonical execution-state
   list and everything after it remain unreviewed.
5. **Only the owner can perform §12.** This document's mechanism refuses to apply itself, and
   re-freezing `infra/core_v06.sha256` and re-issuing the derived manifests is an owner act.

### 20.4 Unchanged by this review

The frozen core was not modified in any way: `infra/core_v06.sha256` verifies **16/16**, the infra
manifest remains `1c673d0e…d059`, and both frozen blockers — the `PAPER_FIRST` rule and the
`config_guard` paper-only restriction — are still reported by `live_release_requirements()`. The
transmission path remains real code whose only blocker is the frozen invariant, and
`apply_amendment()` still refuses.

**Drafting note:** no file in the frozen core, no configuration value, no fingerprint and no
manifest was modified in the production or the review of this document. `infra/core_v06.sha256`
verifies 16/16 and the 273-test baseline is green. This amendment takes effect only via an explicit
owner decision and the §12/§18 procedure.

---

## 21. Second review round — completing everything an autonomous process can complete

Instruction honoured: the truncated §28+ was not waited for, and work continued until every
remaining blocker was an owner credential, an owner capital value, or an owner signature.

### 21.1 The trust root is now asymmetric (Ed25519), and no key is requested

`engine/execution/ed25519.py` implements RFC 8032 over the standard library only — the repository
has no third-party crypto dependency and CI installs nothing, so the trust root must not depend on
a package that may be absent on the runner. Both published RFC 8032 vectors are asserted in
`tests/test_live_readiness.py` (public key and signature both match byte-for-byte).

The first implementation of this section used **HMAC**, which was wrong for this system and is
replaced:

| | HMAC (rejected) | Ed25519 (current) |
|---|---|---|
| Key location | a shared secret the owner hands over, held by the verifying process | public key only, in the repository; private key never enters it |
| Who can forge | anyone who can read the secret, including the process that verifies | only the holder of the private key |
| Key rotation | shared secret, no identity | a `key_id` binds each signature to a specific key |

A symmetric key in a trading process is a standing invitation to forge an owner act, and it required
asking the owner for a secret. Neither is acceptable, so **no key is requested and none is
configured**; the pinned slot is empty and every owner act fails closed.

**Build integrity.** `engine/owner_public_key.json` is now in `build_guard.CRITICAL_FILES`, so
substituting the public key — the one thing that would let anyone forge an owner act — trips
`EXECUTABLE_BUILD_LOCK`. The build fingerprint was re-issued accordingly
(`28edab6a…67a9`, 37 pinned files).

**Domain separation and replay protection.** Every signature covers
`TRIPS-OWNER-AUTHORITY-V1 ␀ purpose ␀ version ␀ key_id ␀ payload`, so:

1. an amendment signature cannot be replayed as a live authorization, or vice versa;
2. a future scheme cannot be confused with this one;
3. a signature from a rotated-away key is refused (`OWNER_AUTHORITY_KEY_MISMATCH`), not accepted;
4. any mutation after signing invalidates it (content binding);
5. both artifacts carry their own validity window (freshness).

`scripts/sign_owner_artifact.py` is owner-side only: it generates a keypair, refuses to write key
material inside the repository, never prints the private key, and self-verifies every signature it
produces. **The owner signature remains blank.**

### 21.2 There are three frozen blockers, and the third was missed

The first round reported two. Re-reading the frozen Risk engine found a third: `forge_gate` opens
with `GateCheck("paper_mode", config.get("mode") == "paper", ...)`, so the frozen Risk engine
refuses live mode **in its own right**. Amending the Constitution and `config_guard` alone would
have produced a system reporting every precondition satisfied while its own risk gate still vetoed
live mode.

`frozen_risk_permits()` now probes the real frozen gate — the same technique as the `config_guard`
probe, so it cannot drift — and a missing `paper_mode` check is treated as a refusal.
`blockers_to_live_release()` reports all three by category, and `engine/risk.py` is named in the
§12 change set.

### 21.3 The engine work that is not blocked on an owner

| Item | What was done |
|---|---|
| Exchange calendar | `engine/execution/exchange_calendar.py` computes the US cash-equity holiday, early-close and validity schedule deterministically. `SESSION_TRUTH_UNKNOWN` is no longer waiting on an operator's clipboard, and the calendar states its own provenance limits (not a live feed; halts are not represented). |
| Broker adapters | `engine/execution/adapters.py` — Upstox and Alpaca adapters that validate and translate. **Superseded by §22**: a real `BrokerChannel` now ships for each, in `engine/execution/channels.py`, with no credential in the repository. |
| Ported from PR #1 | validate-before-network; integer-quantity discipline; refuse rather than truncate an over-long order tag; refuse a split acknowledgement instead of guessing which order it was; validate snapshot shape and never drop an identity-less order row. |
| Deliberately not ported | `require_live_opt_in()` and its `TRIPS_LIVE_EXECUTION=ENABLED` flag — an operator-settable boolean carrying financial authority, the exact pattern §20.1 found forgeable. And `LiveLimits`' invented numbers, because capital values are an owner input. |
| PR #1 | **Closed unmerged.** It touched no file on `main`. Its four genuinely good behaviours were ported; its second parallel authority model was not merged. |
| Ceiling | `LIVE_READY_LOCKED` added. Reachable by any actor once the engineering checks pass, and it grants nothing: `may_transmit_live()` still refuses, and `LIVE_ENABLED` is still owner-only and only reachable *through* it. |

### 21.4 What is left — superseded by §22

The original §21.4 classified all six remaining blockers as owner-only work. **That
classification was wrong**, and §22 corrects it. Items 3 and 4 each contained substantial
engineering that had never been written, and a build that reported "only the owner's signature is
left" was flattering itself. The table above is retained as the record of what was believed at the
time; §22 is the record of what is true.

---

## 22. Engineering remediation — evidence, not module presence

### 22.1 Module presence was not engineering completion

`live_readiness()` answered five questions with `importlib.import_module`:

```
broker_adapters_present, reconciliation_engine_present, supervisor_provider_present,
owner_trust_root_present, execution_authority_gate_present
```

All five passed the moment the files existed. A module that imports cleanly is a module that has
been *written*; it is not a subsystem that has been *run*. Worse, the reading flattered the build:
it let a program declare "everything left is the owner's signature" while the conformance suite,
the broker channels, the market-data path and the supervisor bridge had never been executed even
once.

`engine/execution/readiness.py` replaces all five. Every check now **executes the subsystem it
names** and reports a SHA-256 digest over what it observed:

| Check | What it actually runs |
|---|---|
| `frozen_core_digest_verified` | `verify_frozen_core_digest()` |
| `executable_build_integrity` | `build_guard.verify_build_integrity()` |
| `owner_trust_root_exercised` | Ed25519 against the **published RFC 8032 vectors**, not a self round trip |
| `broker_channel_implementation_exercised` | both channels constructed; both refuse to build without a credential |
| `broker_specific_normalization_exercised` | both brokers' normalizers on good *and* malformed payloads |
| `per_capability_conformance_evidence` | the conformance suite run against both channels |
| `translation_round_trip_proven` | canonical → provider-native → canonical, every order shape, every adapter |
| `reconciliation_engine_exercised` | agreeing *and* disagreeing fixtures |
| `market_data_pipeline_exercised` | provider → frozen `closed_bars_only` → frozen `validate_bars` |
| `data_health_fails_closed` | the health logic against a deliberately month-old series |
| `session_calendar_exercised` | the calendar at real instants, including its own validity limit |
| `supervisor_bridge_exercised` | the bridge end to end, plus its escalation-surface check |

`engineering_ready` is `all(record.passed)` and nothing else. A check that raises is recorded as
**failed**, and a check that is not executed is recorded as missing, so the executed-check
inventory can never quietly shrink.

### 22.2 The blanket conformance flag is gone

`_ChannelInjectedAdapter(conformance_verified=True)` flipped all fifteen `CORE_CAPABILITIES` to
SUPPORTED. It was a claim with no content — nothing recorded *which* capability was exercised,
against *which* interface, in *which* environment, by *which* run — and it was unfalsifiable in the
dangerous direction.

`engine/execution/conformance.py` replaces it with a **typed, versioned, digest-checked record per
capability** (`CapabilityEvidence`) and a separate **mandate record** (`MandateEvidence`).
`resolve()` recomputes the status from the record's own facts every time it is asked:

* a record whose digest does not match its content is not evidence;
* an unknown schema version, a stale record, a record from another interface and a record from
  another environment all degrade to UNVERIFIED;
* a record that was never *exercised* is UNVERIFIED — "we were not allowed to try" is never
  recorded as "the broker cannot do it";
* a recorded UNSUPPORTED is a **proven negative** and is preserved, because a broker genuinely
  cannot do some things.

The concrete result, from the recorded conformance run: Upstox and Alpaca each resolve fifteen
core capabilities to SUPPORTED, `order_replace` to SUPPORTED only where the endpoint exists, and
`order_preview` / `streaming_events` to **UNVERIFIED** because this implementation has no probe for
them. One shared flag could never have produced that.

### 22.3 Upstox is ineligible for this mandate, and conformance cannot override that

Upstox's v3 API is a documented, machine-callable **Indian** market interface. It passes every
conformance probe here. None of that makes it eligible to trade SPY, QQQ and AAPL.

`MandateEvidence` records which instruments and asset class a broker was *observed* to serve. For
Upstox that is `IN_EQUITY_CASH`. Mandate compatibility is decided by that record alone; there is no
code path from "all fifteen capabilities SUPPORTED" to "SPY is tradeable here". The registry,
the gateway, `require_mandate_compatible()` and `PreflightEvaluator` all consult it, and the test
suite asserts that adding SUPPORTED records does not move a single mandate verdict.

### 22.4 Translation repaired: canonical economics first, serialization second

`represent_intent` went straight from a raw intent to provider-native fields, and
`assert_preserves_economic_meaning` was then applied to that payload. That could never work:
Alpaca spells quantity `qty` **as a string**, Upstox spells side `transaction_type` and
time-in-force `validity`, and neither carries the canonical names. Rather than weaken the
economic-meaning check to fit — which would have made it vacuous — the order of operations changed:

1. **The gateway validates a canonical economic representation** — symbol, side, quantity,
   order_type, time_in_force, limit_price — in `contracts.canonical_economic_representation`,
   while the order is still broker-neutral. Nothing provider-native exists yet.
2. **Only then** may the adapter serialize *from that object* into Alpaca/Upstox field names.
3. **Only then** is the adapter required to decode its own payload back via `economic_view()`, and
   `assert_preserves_economic_meaning` — **unchanged, not weakened** — compares canonical to
   canonical.

`tests/test_execution_evidence.py` mutates quantity, side, symbol, order_type, time_in_force and
price one at a time, in each broker's own field names, and asserts every mutation is refused.
It also asserts the gateway validates before it serializes, against the source order rather than
against a comment.

### 22.5 The Supervisor bridge is wired, not merely importable

There was a provider abstraction, a runner, a policy and a `SafetyController`, and nothing
connecting them to the running system. `engine/execution/supervisor_bridge.py` is that connection:
a collector that gathers allowlisted, observable facts, a `SanitizedEvidencePacket` that refuses
anything off the allowlist, and a bridge that runs the provider and writes only to the
`SafetyController`.

Two guarantees are structural. **One-way:** the bridge exposes no resume, cancel, submit or promote
method — the test asserts the absence of an escalation surface, not the presence of a check.
**Outside broker authority:** the collector is handed a `ReadOnlyBrokerObservation` exposing exactly
four reads and no mutation, so the supervision path has nothing to escalate to.

### 22.6 Market data is a real, fail-closed object

`engine/execution/market_data.py` wraps the **frozen** `providers.get_provider` (no second parsing
path, no source-kind relabelling), calls the **frozen** `truth_guard.validate_bars`, enforces
closed 60-minute bars through the **frozen** `market_time.closed_bars_only`, routes every source
through the provenance guard so a broker execution feed can never become a Truth source, integrates
the exchange calendar, and fails closed on staleness, cadence, provenance and session. It refuses to
construct without a provider credential — which is precisely the part that is an owner act.

### 22.7 The execution layer is now under build integrity

`build_guard.CRITICAL_DIRECTORIES` covers `engine/execution/*.py`. Without this,
`execution/conformance.py` could be edited to answer SUPPORTED unconditionally and every downstream
evidence digest would still be internally consistent. The build manifest grew from 37 to 64 files.

### 22.8 Corrected stale HMAC references

The owner trust root moved to Ed25519 (RFC 8032); four places still described the live
authorization or amendment signature as an HMAC tag: `execution/lifecycle.py`,
`execution/amendment.py` (module docstring and `AmendmentProposal`), and `execution/status.py`. All
four now describe an Ed25519 signature, domain-separated by purpose. The remaining HMAC mentions
are deliberate: `engine/supervisor_relay.py` uses an HMAC for its **delivery-acknowledgement
receipt**, which is a different, live mechanism and is part of the frozen core, and the amendment
document's comparison table describes the rejected owner-signature design.

### 22.9 Status

**`NOT_COMPLETE`.** The engineering work in items 3 and 4 is done and proven by executed evidence.
What remains is:

| # | Blocker | Engineering | Owner / external |
|---|---|---|---|
| 1 | `owner_public_key_configured` | — | install the owner's Ed25519 public key |
| 2 | `capital_governor_profile_set` | — | capital, loss, exposure and position values; **none invented** |
| 3 | `broker_channel_and_conformance` | **done**: real channels, broker-specific normalization, per-capability typed evidence, reconciliation executed, paper/sandbox lifecycle exercised | broker account credentials and the owner's OAuth approval |
| 4 | `production_market_data` | **done**: production provider wrapper, frozen Truth integration, provenance guard, closed 60-minute bars, calendar/session integration, fail-closed health | provider API key, subscription and real-time entitlement |
| 5 | `owner_signed_live_authorization` | — | owner decision B, signed |
| 6 | `constitutional_amendment_applied` | — | re-freezing the frozen core is an owner act |

Nothing in this section requests any of the owner items. No key was generated, no capital value was
invented, no signature was requested, no broker credential was requested, and no live or real-money
execution was attempted. `may_transmit_live()` still refuses, and `LIVE_ENABLED` remains owner-only.
