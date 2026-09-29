# Trip's documentation truth discrepancies

## Governing owner truth

Current enforced state: mode=paper; live transmission locked (LIVE_LOCKED / LIVE_READY_LOCKED); frozen Constitution rule 13 PAPER_FIRST in force. The execution layer is designed live-money-only with no simulated-broker stage; forward evidence comes from no-order shadow runs on real data. Strategy verdict: UNPROVEN.

This file is the WP5 inventory. It distinguishes historical wording from the current enforced state.
No Class-B file is edited in PR-3. Protected or non-Class-A wording is deferred to proposal P004.

## Classification rule used

Class-A wording edits in PR-3 are limited to non-pinned Markdown files. The following are treated as
Class-B and are not edited here: `docs/TRIPS_CONSTITUTION.md`, `engine/capability_registry.json`,
`engine/config.json`, all `engine/execution/*.py`, and all non-research workflow YAML.

## Discrepancies by source

### README.md — Class A, edited in PR-3

Conflicting/currently misleading present-tense statements found:

- `There is no live-money execution path.`
- `Trip's is a paper-only market-analysis and trading-research agent...`
- `There is no live-money broker execution path.`
- validated scope bullet `paper execution only`.

These described older v0.6/v0.7 checkpoints but read as current truth. PR-3 retains the history,
labels it historical, and places D1 verbatim at the top.

### HARDENING_REPORT.md — Class A, edited in PR-3

Historical/current ambiguity:

- `PASS within the deliberately narrow paper-only scope.`
- `CONTINUE_PAPER_OBSERVATION`.
- `hardened autonomous paper-research core...`.

These are preserved as v0.6 checkpoint evidence, not current execution-architecture claims. D1 is
added verbatim.

### INFRASTRUCTURE_STATUS.md — Class A, edited in PR-3

Mostly aligned with current execution architecture, but this sentence is inaccurate in present
tense:

- `No live-money broker path is pending or enabled.`

A live-money execution path now exists in code but live transmission is locked. PR-3 changes the
sentence to say that the path exists but is not enabled, and adds D1 verbatim.

### NEON_DRY_RUN_REPORT.md — Class A, edited in PR-3

No direct live-only contradiction was found. It is a historical pre-production report. D1 is added
verbatim so readers do not mistake the historical checkpoint for current system authority.

### NEON_PRODUCTION_STATUS.md — Class A, edited in PR-3

Potentially misleading wording:

- `Bootstrapped the authoritative paper runtime once from the core's own initializer.`

That was historically true of the frozen core state, but it is not a simulated-broker stage in the
current execution lifecycle. PR-3 qualifies it as a historical frozen-core runtime fact and adds
D1 verbatim.

### STUDENT_ENGINE_ARCHITECTURE.md — Class A, edited in PR-3

The design refers to:

- `paper trades`
- `executed paper trades`
- `simulated order lifecycle, realized paper outcome`.

Those references describe frozen-core/historical research evidence, not a current broker-paper
stage. PR-3 adds D1 verbatim plus that interpretation.

### STUDENT_HARD_STRESS_AUDIT.md — Class A, edited in PR-3

Conflicting historical statements:

- reference to retired `.github/workflows/trips-cloud-paper.yml`.
- `Paper-only authority remains unchanged.`

PR-3 labels the report as a historical checkpoint, preserves the audit fact, and adds D1 verbatim.

### STUDENT_INTEGRATION_STATUS.md — Class A, edited in PR-3

Present-tense statement:

- `No live-money execution exists or is authorized.`

The authorization half remains true; the existence half does not. PR-3 changes this to a historical
checkpoint statement and adds D1 verbatim.

### STUDENT_V08_INTEGRATED_HARD_TEST.md — Class A, edited in PR-3

Historical status line:

- `LOCALLY INTEGRATED / PAPER-ONLY / CLOUD DEPLOYMENT NOT YET CLAIMED`.

The report remains historical; D1 is added verbatim and the status is explicitly labelled as the
status at that historical checkpoint. `No live-money authority was added` remains compatible with
D1.

### infra/README.md — Class A, edited in PR-3

This file already says the execution route is live-money-only and that the former cloud-paper
workflow is retired. It also refers to the frozen v0.6 execution simulator. Those can coexist only
when the frozen-core simulation and the live-only execution route are clearly separated. D1 is
added verbatim to make that boundary explicit.

## Workflow comments — not edited in PR-3

### .github/workflows/trips-agent.yml — Class B / P004 candidate

Conflicting comment:

- `before autonomous scheduled paper cycles resume.`

That is stale relative to the current live-only execution route and no-order forward-shadow
evidence model.

### .github/workflows/trips-live-readiness.yml — Class B / P004 review item

It says Trip's is live-money-only and that there is no paper broker stage. That aligns with the
execution-layer half of D1, but it omits the equally important frozen facts that config remains
`mode=paper` and PAPER_FIRST still blocks live transmission. P004 should reconcile wording without
weakening the gate.

### .github/workflows/trips-daily-deep.yml and trips-safety-suites.yml

No paper-vs-live wording conflict found in their current comments.

## Pinned / Class-B truth conflicts — P004 only, not edited here

### engine/capability_registry.json

- `supported.execution = "paper-only"`
- `not_supported` includes `live-money order submission`.

This conflicts editorially with the existence of the additive live-money-only execution layer, even
though live transmission is still blocked. P004 must reconcile the wording under owner reapproval.

### engine/config.json

- `"mode": "paper"`.

This is not itself a discrepancy; it is one of the frozen facts D1 must preserve. Any future change
requires owner reapproval and is outside WP5.

### docs/TRIPS_CONSTITUTION.md

- Rule 13: `PAPER_FIRST — This build cannot submit live-money orders.`

This is a governing frozen fact, not an error. P004 may reconcile surrounding wording only through
the protected-file process; it must not silently remove or weaken PAPER_FIRST.

### engine/execution/lifecycle.py

Current execution-layer strings say:

- `There is no PAPER stage. Trip's is a live-money-only execution system...`
- stage order `RESEARCH -> BACKTEST -> SHADOW -> LIVE_LOCKED -> LIVE_READY_LOCKED -> LIVE_ENABLED`.
- the build cannot reach `LIVE_ENABLED` while the frozen Constitution forbids live-money orders.

These are internally consistent with D1 only if readers distinguish **configured/frozen authority**
(`mode=paper`, PAPER_FIRST) from **execution-route design** (live-money-only, no simulated-broker
stage). P004 should use D1 wording to make that distinction explicit.

### engine/execution/status.py

Current strings say:

- `Trip's is a LIVE-MONEY-ONLY execution system...`
- there is no paper stage/endpoint/credential on the execution route;
- live capital release is refused by PAPER_FIRST, config_guard paper-only mode, and Risk paper_mode;
- the build holds at `LIVE_READY_LOCKED`.

The substance matches D1 but terminology is easy to misread. P004 should reconcile the operator
wording under the protected-file process.

### engine/execution/readiness.py

Current string says:

- `Trip's is live-money-only. There is no paper stage, no paper endpoint and no paper credential.`

As with lifecycle/status, this is about the execution route. P004 should pair it with the frozen
`mode=paper` / PAPER_FIRST authority statement so operator-facing truth is unambiguous.

## WP5 conclusion

The apparent contradiction is architectural terminology, not permission:

- the **frozen authority/configuration** is still paper and PAPER_FIRST;
- the **new execution route** is designed only for real live brokerage, with no simulated-broker
  waypoint;
- that route remains locked and cannot transmit live orders;
- current strategy evidence remains UNPROVEN;
- real forward evidence comes from the no-order shadow runner.

That is exactly D1. No protected file is changed in PR-3.
