# Harness Improvement (`harness_improvement/`)

Alpha's implementation of the recursive harness improvement method from
`docs/ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md` ("Harness²"), adapted to Alpha's
runtime rather than ported from the guide's Rust sketches. One **improvement
run** takes a task, snapshots the current harness as an immutable
**`HarnessBundle`**, executes the task under it, has two bounded proposals
authored, validates and probes each candidate in *its own* workspace, contrasts
the results against the baseline, composes the cheapest evidence-supported
successor, executes and verifies it, selects the deliverable, and reflects what
was learned into an evidence-gated experience library. Promotion to *active* is
a separate, gated step — a run never promotes anything.

A **candidate is never an edit to the active bundle.** It is a new bundle whose
parent is the baseline; the active pointer moves only through `promotion.py`.

## Load-bearing honesty invariants

These are enforced in code *and* pinned by tests; do not relax any of them:

- **`NOT_RUN` is never a pass.** `ValidatorStatus.NOT_RUN` and a validator that
  never executed are distinct from `FAILED`: an unmeasured check cannot satisfy
  a promotion gate (`validators.py`, `promotion.py::evaluate_promotion`).
- **A gate that did not run is not a gate that passed.** `evaluate_promotion`
  is tri-state per gate (satisfied / unsatisfied / *unmeasured*); a candidate
  whose regression suite never ran cannot reach `ACTIVE`, and a required
  approval flag is a refusal even under `force` and even with
  `automatic_global_promotion` on.
- **A measurement that did not happen is `None`, never `0`/`False`.**
  `ProbeOutcome` token usage, metric counts, contrast verdicts and
  `evaluate_promotion` fields all stay explicitly unmeasured; `metrics.py`
  returns `None` for counts that were not measured, never `0`.
- **Refusals name the bound; bounds are refused, never clamped.**
  `BudgetExceeded` carries the bound, the measured spend and the ceiling; patch
  and config validation refuse an over-bound value with the bound named.
- **Absent is not corrupt.** A missing store file is genuinely empty; a
  malformed *existing* file raises `StoreCorrupt` and an unreadable one
  `StoreUnreadable`, and both surfaces report `503` rather than an empty `200`.
- **"No change" is a valid composition.** The composer returns the baseline
  bundle itself (`retained_baseline=True`) when no edit is supported; that is a
  correct outcome, not a failure, and the successor stage is skipped with a
  note saying so.
- **The engine never fabricates a result.** No executor/editor is wired in
  production; a run parks at `BLOCKED` naming the missing collaborator, and no
  probe outcome, validator result or deliverable is ever synthesized.
- **Policy is not part of the editable surface.** `PROTECTED_COMPONENT_IDS` /
  `PRIVILEGED_KINDS` refuse a diff naming the non-bypassable safety core or a
  privileged kind at *both* the patch validator and the composer.

## The run pipeline (`engine.py`, `state.py`)

Stages, in order: `QUEUED → SNAPSHOTTING → BASELINE_RUNNING → PROPOSING →
CANDIDATES_VALIDATING → PROBING → CONTRASTING → COMPOSING →
SUCCESSOR_RUNNING → VERIFYING → SELECTING → REFLECTING → COMPLETED`, with
`BLOCKED` (parked, resumable), `FAILED`, `CANCELLED` and `BUDGET_EXHAUSTED`
handled outside the forward chain.

- **A transition is persisted before the next stage starts.** The engine writes
  the run record, appends the journal line, then does the work — a crash
  resumes from the last *completed* checkpoint, never from an assumed one.
- **`stage_key(run_id, stage, inputs, config)` makes retries idempotent.** A
  stage that completed with the same key is skipped on resume, so a retry
  cannot double-spend a provider request or overwrite an artifact.
- **`BLOCKED` is honest parking, not failure.** A run with no executor/editor
  wired records the missing collaborator, and `resume` re-enters the stage it
  parked at once the collaborator exists.
- **A run is guarded by a cross-process `FileLock`** for the duration of its
  execution, so two workers cannot drive the same run.
- **Provenance travels on the bundle.** The engine stamps
  `source_run_id=run.run_id` on accepted candidates and on the composed
  successor at the *stage handlers*, not inside the pure builders: the
  promotion router reads `bundle.source_run_id` to reconstruct probe/contrast/
  deliverable evidence from the run record, and the stamp is outside the
  bundle digest, so ids are unchanged. Do not move this stamp into
  `patches.py`/`compose.py` — those functions are pure and must not need run
  context.
- **Collaborators are injected, never imported.** `executor.py` defines the
  `HarnessExecutor`/`HarnessEditor` protocols; `ScriptedExecutor`/
  `ScriptedEditor` are the offline actors (they answer by bundle id + role) so
  the real state machine is exercised without a model call.

## The promotion ladder (`promotion.py`)

`LADDER` is the single forward-edge table:
`draft → statically_validated → probed → regression_tested → shadow → canary →
active`. Everything not listed is not a promotion.

- **`activate` is an atomic pointer swap that remembers its predecessor.**
  `ActivePointer.previous_bundle_id` records what was active, so `rollback`
  is a one-step restore; both events land in the durable promotion log.
- **A rollback that cannot be performed says so.** With no prior bundle the
  refusal names that — it never clears the pointer and calls it a rollback.
- **Approval is substantive, not performative.** The router's `approve` records
  an operator approval only when the *other* gates already pass; the one
  condition an explicit operator action is meant to satisfy — automatic global
  promotion being off — is removed by re-evaluating under an explicit-operator
  policy, and every other unmet gate still refuses with its own words.
- **`promote_state` records a decision, not authority**: it validates the
  ladder edge and appends the log row; only `activate`/`rollback` move
  `ActivePointer`.

## The store (`store.py`)

Everything durable lives under `runtime_home()/harness_improvement/`: bundle
manifests and content-addressed blobs, run records + JSONL event journals,
experiences, the promotion log, the budget ledger, probe workspaces and the
active pointer. Writes are atomic (temp + fsync + `os.replace`), and each writer
is **single-process JSON/JSONL — atomic against a crash in one process, never
cross-process exactly-once**; a second Gateway worker racing the same root must
serialize writes itself.

`Listing` separates items from `unreadable` rows so "three bundles" and "three
bundles plus one file nobody could parse" are different answers. `load_bundle`
re-verifies the payload hash against `compute_bundle_id` and refuses a modified
manifest; listings report a malformed one as `malformed JSON`/`malformed
bundle` rather than dropping it. `safe_id` is the one place an id becomes a
path segment.

## Candidate validation (`patches.py`)

The editor is untrusted input; the validator is the gate between "proposed" and
"stored". Checks in report order: base mismatch, hypothesis completeness (a
missing falsification condition means the hypothesis can only be believed, not
probed), protected surface and privileged kinds (refused *before* any content
inspection, with the full refusal list returned), component-id path hygiene,
content screens (credential-store paths, network-capable commands), and size
bounds (refused, not clamped). Nothing in this module runs, imports or formats
candidate content — the probe stage is the first place any of it executes, in
its own workspace.

## Probing, contrast, composition (`probes.py`, `contrast.py`, `compose.py`)

- **Probes are isolated per candidate**: each gets its own workspace, and
  `environment_fingerprint` records the matched-environment facts. A differing
  environment or a task-fingerprint mismatch is a disclosed confounder, never
  averaged away.
- **A contrast answers eight narrow questions and can say `CONFOUNDED`.**
  `CONFOUNDED` outranks every other verdict; single-run results are flagged as
  such because one run cannot separate an improvement from noise;
  `targeted_failure_resolved` is `None` unless the baseline actually failed the
  named check.
- **Composition adopts only supported benefit**. A positive contrast is
  necessary but not sufficient — the targeted failure must not have reappeared.
  When two candidates touch the same component, the cheapest intervention wins
  (tool guidance before skill before script before subagent before always-on
  prompt text) and the loser is recorded with the conflict as its reason. Every
  adopted/rejected edit travels with its evidence refs.

## Experience library (`experiences.py`)

A separate library from conversation/semantic memory because its entries may
*inform a future proposal*. No evidence, no lesson: `build_experience` returns
`None` with a reason unless the contrast is positive (or records an explicit
regression), the probes were not confounded, and the configured evidence floor
is met. Scope travels with the lesson (task types, domain tags, trigger
symptoms, and what it does *not* apply to); staleness is computed against
compatibility versions and review conditions, not assumed. Negative findings
are recorded too — the library is not a wall of wins.

## Budgets (`budgets.py`)

Ceilings are *enforced / exhausted / unenforceable* — an unenforceable cap
(the provider reports no usage for that dimension) is reported as such, never
silently treated as "under budget". The **deliverable reserve is withheld
before research starts**: refinement stages may not spend into the budget the
task artifact needs. The ledger records measured stage charges.

## Configuration (`config.yaml -> harness_improvement`)

`config.py` owns the operator surface; defaults are the guide's conservative
ones: **disabled until an operator turns it on**, one sequential step, one
candidate per scope, one concurrent probe, measured ceilings, and a promotion
gate that parks for human approval. Bounds are refusals, not clamps. This model
is the only place the keys are *declared*: `HarnessImprovementSettings` is built
from `model_dump()` via `from_mapping`, which refuses unknown keys, so a field
added on one side without the other fails a test, not a run. The engine does not
import `alpha.config` — the Gateway converts.

## HTTP surface and production posture (`app/gateway/routers/harness.py`)

18 routes under `/api/harness`: reads (`GET /status`, `/runs`, `/runs/{id}`,
`/runs/{id}/events`, `/bundles`, `/bundles/{id}`, `/bundles/{id}/diff`,
`/experiences`, `/experiences/{id}`, `/promotions`, `/budgets`) are
authenticated; mutations (`POST /runs`, `/runs/{id}/resume`,
`/runs/{id}/cancel`, `/candidates/{id}/approve`, `/candidates/{id}/reject`,
`/active/{id}/activate`, `/active/{id}/rollback`) are admin-gated. The
subsystem is **installation-scoped** — one store, one active bundle — so
payloads are not partitioned per user.

Four properties are load-bearing:

- **The execution plane's absence is machine-readable.** No production process
  wires a `HarnessExecutor`, `HarnessEditor` or baseline provider, so
  `GET /status` carries a `harness_capability` row (`state: absent`,
  `smart: false`) naming the exact wiring a deployment would have to add, and
  it answers **even while the feature is disabled** — the client asking whether
  this plane can execute is precisely the client that should be told first.
  `POST /runs` refuses with `409 harness_baseline_absent` when no active bundle
  and no provider exist; it never parks a run that could never progress.
- **The kill switch splits run/advance writes from stop-or-restore writes.**
  While disabled, `POST /runs`, `resume`, `approve` and `activate` refuse with
  `503 harness_improvement_disabled` naming the setting — nothing may run or
  move the ladder. `cancel`, `reject` and `rollback` stay reachable: refusing a
  stop would leave a run unhaltable, a rejected candidate standing, or a bad
  active bundle unrestorable the moment an operator disables the feature.
  Reads and `GET /status` stay reachable throughout.
- **An unreadable store is a degraded state, never an empty `200`.** A corrupt
  bundle manifest, run record or ledger answers `503` carrying the store's own
  reason; a missing file stays genuinely empty. Promotion/decision routes are
  absent-store-tolerant only where the answer is meaningful.
- **Store I/O never runs on the event loop.** Config resolution, listing scans
  and ledger reads go through `asyncio.to_thread`. The run driver stays on the
  loop because it awaits the injected executor; its own writes are small atomic
  JSON writes.

## Tests

All offline, no model calls, scripted actors only:

- `backend/tests/test_harness_improvement_engine.py` — the full cycle, resume
  from each checkpoint, cancellation, budget exhaustion, the retained-baseline
  path, and the provenance stamp on candidates/successor.
- `backend/tests/test_harness_improvement_patches.py` — every rejection reason
  in report order, protected-surface refusals, path hygiene, bounds.
- `backend/tests/test_harness_improvement_config.py` — defaults, every bound
  refusal, and the engine-settings round-trip (`from_mapping` unknown-key
  refusal).
- `backend/tests/test_harness_improvement_promotion.py` — the ladder edges,
  tri-state gates, approval flags, atomic activation, one-step rollback,
  history log honesty.
- `backend/tests/test_harness_improvement_router.py` — route inventory, auth
  and admin sweeps, the disabled posture, start refusals, a full cycle read
  over HTTP, promotion flows, and the degraded-store `503`/`unreadable` split.
