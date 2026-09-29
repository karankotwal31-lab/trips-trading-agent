# Trip's Student Engine v0.1 — Architecture Contract

> **Current enforced truth — owner decision D1:** Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.
>
> Historical wording below is retained as historical evidence. Where older wording conflicts with this statement, D1 governs.

STATUS: DESIGN/SHADOW ONLY. NO EXECUTION AUTHORITY.

## Purpose
References below to paper trades/outcomes describe frozen-core or historical research evidence, not a current broker-paper stage.

Turn Trip's immutable evidence (paper trades, rejected setups, decisions, escalations, market regimes and replay results) into auditable lessons and testable strategy hypotheses.

## Authority boundary
Student MAY: observe, label, aggregate, replay, compare, score evidence quality, detect recurring failure/success patterns, and propose experiments.
Student MUST NOT: submit/modify orders; change risk limits; change Constitution; change truth/provider eligibility; change symbol scope; mutate champion strategy/config; acknowledge escalations; claim queued supervisor evidence was delivered.

## Pipeline
1. Episode Builder
   - consumes integrity-verified Decision Mirror + ledger + audit + truth/provenance.
   - builds episodes for executed paper trades AND rejected/no-trade setups.
2. Context/Regime Labeler
   - labels only from admissible closed-bar evidence.
   - examples: volatility/liquidity/trend/range/gap context.
   - labels are observations/derived features, never execution permission.
3. Outcome Attribution
   - separates signal quality, entry timing, sizing, stop/target path, costs, data quality, and deterministic gate decisions.
   - prevents hindsight leakage by recording what was knowable at decision time.
4. Lesson Store
   - append-only, hash-linked records.
   - lesson = evidence IDs + hypothesis + confidence class + known limitations.
   - confidence is evidence quality, NOT probability of profit.
5. Counterfactual Lab
   - replays candidate ideas on historical/shadow data with conservative fills and costs.
   - walk-forward + untouched out-of-sample required for promotion proposals.
6. Pattern Miner
   - finds repeated conditional associations (e.g. setup X under regime Y).
   - minimum sample/effect/stability thresholds; multiple-testing ledger.
7. Strategy Classroom
   - strategy plugins expose a common research-only interface.
   - new "tricks" enter as quarantined hypotheses; never as executable code.
8. Student Examiner
   - rejects leakage, tiny samples, unstable effects, missing provenance, provider mismatch, and cost-sensitive mirages.
9. Evolution Handoff
   - only Examiner-approved hypotheses become Evolution challenger proposals.
   - Evolution remains shadow-only and human-approved under Constitution.
10. Supervisor Tutor
   - supervisor can critique lessons/experiments but has zero execution authority.

## Core records
Episode: episode_id, decision_event_ids, symbol, interval, provider provenance, timestamps,
features_known_at_decision, gate outcomes, simulated order lifecycle, realized paper outcome, costs,
regime labels, data-quality flags, build/config hashes.

Lesson: lesson_id, evidence_episode_ids, hypothesis, comparison baseline, sample size,
walk-forward stability, OOS result, cost sensitivity, failure modes, evidence_grade, created_at,
student_version, hash.

Experiment: experiment_id, hypothesis_id, champion_fingerprint, challenger_fingerprint,
dataset fingerprints, train/validation/OOS windows, metrics, rejection reasons, status.

## Required metrics
Expectancy in R, win/loss distribution, max adverse/favorable excursion, drawdown contribution,
turnover/cost drag, calibration of signal-score buckets (descriptive only), regime-conditioned
performance, rejection opportunity-cost, stability across walk-forward windows.

## Learning methods allowed initially
- deterministic cohort analysis
- Bayesian descriptive updating for uncertainty intervals (never trade authority)
- bootstrap confidence intervals
- walk-forward validation
- ablation tests
- counterfactual replay
- simple regularized models/trees in research-only mode after dataset sufficiency
No online reinforcement learning controlling execution; no autonomous parameter mutation.

## Fail-closed rules
Missing provenance, stale/invalid data, insufficient sample, leakage, corrupted journal, mismatched
build/config, or unknown provider identity => lesson may be stored as INCONCLUSIVE but cannot
become an Evolution proposal.

## Capability gained
Trip's can answer: What worked? Under what conditions? What failed? Was the loss consistent with
the strategy or caused by data/execution/cost assumptions? Which rejected setups would have helped
or hurt? Is a pattern stable across regimes? Which hypothesis deserves a controlled challenger test?
It cannot answer "what will win next" as a fact and cannot self-authorize a trade.
