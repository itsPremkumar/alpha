# Dynamic Workflow Engine — Current State

A gap-driven account of where the dynamic workflow plane actually stands: what
was already built and verified, what this change set added, and what is still
missing. This document exists so nobody has to infer capability from a spec or
a README claim.

Architecture and module ownership:
[`docs/ALPHA-WORKFLOW-ARCHITECTURE.md`](ALPHA-WORKFLOW-ARCHITECTURE.md).
Operations and API examples: [`docs/DYNAMIC_WORKFLOWS.md`](DYNAMIC_WORKFLOWS.md).

## Method

The specification was read as a **gap list against a working system**, not as a
greenfield build. Most of it was already implemented; rebuilding it would have
deleted working features to re-add them. Each gap was therefore:

1. audited against the code that already existed,
2. closed by extending the existing seam rather than adding a parallel one,
3. pinned by tests written before the wiring,
4. documented in the same change set.

The durable JSONL event log was kept as the persistence substrate — it already
satisfies the durability requirements — rather than being re-founded on SQLite.

## What already existed (verified, not assumed)

Baseline verification ran three suites before any change:
`test_dynamic_workflow_engine.py`, `test_workflow_runtime_correctness.py`,
`test_orchestrator_kernel.py` — **85 passed**.

Already present and left untouched:

- the typed state machine and run/node status vocabulary;
- the append-only JSONL journal, projection, hydration and replay;
- optimistic-concurrency graph patches and `PlanGraphStore` CAS revisions;
- wave concurrency, node deadlines, executor-free node kinds, signal waits;
- suspend/resume, fork/history, dry runs, template `draft → verified → promoted`;
- improvement proposals, the `/api/workflows` route surface, the Workflow Center UI.

Two audit findings were deliberately **not** changed because they are pinned by
tests as load-bearing: the stale-projection refusal in
`event_log.hydrate()`, and the pure replay fold in
`alpha.orchestrator.replay` (which emits nothing).

## Added by this change set

### Wave 1 — failure taxonomy (`alpha.workflow.failures`)

A retry-decision classification at node granularity: nineteen `NodeFailureClass`
values produced by ordered keyword rules that disclose the rule that matched,
plus `error_signature()` and a `StagnationDetector`.

It **bridges** onto vocabularies that already existed instead of creating a
seventh one: `recovery_class()` routes to `alpha.recovery.policies` (still the
single authority on what to do after a failure), `reason_code()` routes to
`alpha.bots.failure_reasons`, and `alpha.errors.registry` remains the sole
owner of stable error codes.

Wired into `runtime.py::_invoke_runner`, which now emits `failure_classified`,
`node_stagnated`, `recovery_exhausted` (carrying the terminal strategy with
`strategy_source` and `strategy_bridged`) and `node_retry_refused`.

Tests: `tests/test_workflow_failures.py` — **49 passed**.

### Wave 2 — durable attempt leases and orphan recovery

`alpha.workflow.leases` was rewritten from an in-memory placeholder into a
durable store at `runtime_home()/workflow_store/leases.json`: atomic
`os.replace`, a schema version, and a `LeaseStoreError` that fails closed.

- `WorkerLease` carries `attempt_id`, `fence_token` and `graph_version`.
- `ResultVerdict` distinguishes `ACCEPTED`, `STALE_LEASE`,
  `SUPERSEDED_REVISION` and `UNKNOWN_LEASE`. The check runs **before** a
  runner's output is folded into run state, so work that can no longer be
  believed is discarded rather than written through.
- The fence advances on reclaim and expiry, so a dead worker's late result
  reads `STALE_LEASE`, not `ACCEPTED`.
- Lease refusal at dispatch is a one-shot honest failure — never a retry loop.

In the engine: acquire before `RUNNING`, release in `finally`, a process-local
`_in_flight` set as ground truth for live nodes, and
`reconcile_orphaned_nodes()` at the top of every step. An orphaned `RUNNING`
node is **failed** with `failure_class=worker_lost` — its side effects are
unknown, so it is never silently reset — and the reconciliation is journalled
as `orphaned_nodes_reconciled`.

**This closed the crash gap.** Replay folds `node_started` into `RUNNING` with
no completer and the scheduler admits only `PENDING`/`READY`, so before this a
worker that died mid-node left the run permanently stuck on work nobody owns,
and a restart reproduced that state from the journal.

Because hydration correctly refuses a run whose projection lags its journal,
and `/replay` is deliberately read-only, that refusal had no way back. The new
`POST /api/workflows/runs/{run_id}/recover` folds the real journal into a fresh
run, installs it via `DynamicWorkflowEngine.adopt_replayed_run()`, reconciles
the orphans, and re-materialises the projection *after* reconciliation (the
reconciliation itself appends events; projecting earlier would leave the run
stale again). It reports what was folded and never reports the rebuilt run as
verified.

A throwaway dry-run engine is handed a process-local `LeaseManager()`, so
`simulate_run` never touches the real store.

Tests: `tests/test_workflow_leases.py` — **28 passed**;
`tests/test_workflow_durability_router.py` — **12 passed** (3 new: the recovery
chain, the disclosed 404, and the no-verification claim).

### Wave 3 — plan revision diff (`alpha.workflow.graph_diff`)

`diff_graphs()` separates **structural** changes (nodes added/removed/re-typed,
edges rerouted) from **runtime** changes (prompts, budgets, timeouts, retries,
policy). The reason comes from the recorded `PlanVersion.note`/`source` — never
invented by the differ. Output is deterministically ordered, bounded, and
truncated with a marker rather than allowed to grow unbounded.

Exposed at `GET /api/workflows/{workflow_id}/plans/{version}/diff?base=<n>`.

Tests: `tests/test_workflow_graph_diff.py` — **26 passed**;
`tests/test_workflow_plan_diff_router.py` — **12 passed**.

### A race found by the regression sweep

The broad sweep caught two concurrent-wave tests failing that passed in
isolation. Both were this change set's own bug, and both are now fixed and
stress-tested:

1. **Lazy-init race on the lease manager.** A wave dispatches nodes on a
   bounded pool; threads racing the unguarded `if self._leases is None`
   each built their own manager over an empty store. One thread acquired on a
   manager the others never saw, so every later verdict came back
   `UNKNOWN_LEASE` and good results were discarded — with the tokens already
   charged. Fixed with double-checked locking; exactly one manager now exists
   per engine.
2. **Unsynchronised store mutation.** `_persist()` built its snapshot
   *outside* the lock, so a concurrent `release_lease` could delete a key
   mid-iteration and raise `dictionary changed size during iteration` out of a
   node's `finally`; and acquire/release performed an unlocked read-modify-write
   on the fence, so two attempts could mint the same token — a fence two
   attempts share is no fence at all. Every mutator's read-modify-write and
   persist are now one atomic unit.

Verified by four consecutive runs of the concurrency subset (12 passed each)
plus `tests/test_workflow_runtime_correctness.py` +
`tests/test_workflow_leases.py` together — **81 passed**.

### Wave 4 — declared verification execution (`alpha.workflow.verification`)

`WorkflowNode.config["verification_cmd"]` was written by the decomposer and
copied by the bridge into every compiled graph, and **nothing in the tree ever
read it** — so a run could succeed while its own plan still named the check
meant to prove that success. This wave makes it execute, and makes it honest
when it cannot.

**Resolution never reaches a subprocess.** The declaration is client-supplied
input (`POST /api/workflows` takes `body.graph` verbatim, and
`update_node_config` writes node config), so it resolves to exactly three
things in order: a verifier the host registered
(`DynamicWorkflowEngine.register_verifier`), a dotted path inside the
allowlisted `alpha.` prefix, or a shell-style command handed to a **host-bound**
`verification_executor`. That executor is absent by default, so
`pytest -q` — the one declaration the decomposer still emits — is `not_run`
until a host binds one. Every other dotted path (`os.system` included) is
refused at resolution *before* any import.

**The verdict contract is deliberately narrow.** A zero-argument callable
returning a bool; dict keys `passed`/`ok`/`success`/`verdict` and the pass/fail
strings are coerced; **anything else — including `None` — is `not_run`**, never
a guess. An exception is `not_run` carrying the real reason: not a pass, and
not a failure either.

**Semantics at the node gate:**

| Verdict | Effect |
| --- | --- |
| `passed` | node completes; only this appends evidence |
| `failed` / `unresolved` | **blocks** — `_fail_node` with the verifier's reason and a `verification` block; the runner's evidence is discarded |
| `not_run` | node completes, journalled as `node_verification` with `passed: false` — never as a pass |
| `not_declared` | no event at all |

The gate runs on the default / agent / tool / bot path of
`_execute_single_node`, after the lease verdict and the evidence check but
*before* either is folded into run state, and outside `_STATE_LOCK`. Kinds the
runtime measures itself — the seven executor-free kinds plus `condition`,
`router`, `map`, `reduce`, `race`, `quorum` and `compensation`, all of which
return before that path — do **not** execute a declared verifier. That is a
disclosed scope boundary pinned by
`test_a_structural_node_kind_does_not_execute_a_declared_verifier`, not an
implied claim of coverage: declare a verifier on a runnable node kind.

**Two honesty surfaces.** `GET /api/workflows/runs/{run_id}/events` carries
every `node_verification` event, so per-node outcomes are durably readable and
replayable. `DynamicExecutionResult.metadata["verification"]` reports the
**posture** — registry size, executor bound, declared node ids — and asserts no
verdict whatsoever, because re-summarising per-node outcomes from a
process-local buffer could report `0 verified` after an eviction. Execution
acceptance (`acceptance_passed`) and verification stay separate axes so a
consumer cannot mistake one for the other.

**The decomposer's fiction was removed, not papered over.** Six of its seven
declarations named either functions existing nowhere in the tree
(`verify_research_coverage`, `ping_mcp_servers`, `validate_bot_roster_health`,
`verify_test_suite_and_orphans`, `verify_memory_persistence`) or a function that
can never satisfy a zero-argument contract
(`alpha.skills.authoring.validate_skill_draft`). With resolution now live each
would have **failed every node it was attached to**, so they are gone and their
intent lives in `verification_criteria`. `pytest -q` is the sole declaration
remaining, and it is real.

Tests: `tests/test_workflow_verification.py` — **55 passed**.

### A silent fork defect found by the regression sweep

The first combined regression run surfaced one failure —
`test_workflow_time_travel.py::test_fork_inherits_completed_work_and_resumes_after_it`
— and it was **not** caused by this change set's feature work. A control run of
the same file set at `da27f7d` was green (479 passed), while the defect
reproduces at `da27f7d` too, on its own.

`WorkflowEvent.event_id` was `datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")` — a
microsecond-resolution timestamp, which is **not unique**.
`node_attempt_started` and the `node_completed` written immediately after it are
emitted back to back, so they routinely land in the same microsecond. A probe
against the base commit captured the collision happening on its own, under real
event ids and no patching:

```
(3, 'node_attempt_started', '20261003164840744801')
(4, 'node_completed',       '20261003164840744801')
```

`_prefix_for_fork` resolved a caller-quoted `event_id` with a **first-match**
scan, so the fork anchored at index 3, its prefix ended before the completion,
and `inherited_completed_nodes` came back empty — silently. This is not a
cosmetic assertion failure. Forking exists so completed work is inherited
*rather than repeated*, specifically because replaying a model call or a sandbox
write doubles a real side effect; a silent collapse quietly made the dangerous
behaviour the default one.

Fixed in two parts:

1. event ids are a sortable timestamp plus a process-local counter, so a
   back-to-back pair can no longer collide; and
2. `_prefix_for_fork` refuses an **ambiguous** id by name, naming the colliding
   events, so a log written before this fix fails loudly instead of mis-forking.

Tests: `tests/test_workflow_event_identity.py` — **5 passed**, including a
frozen-clock test that makes the collision deterministic, a sortability test, and
a fork test that pins the inherited work the defect destroyed.

## Still missing

These are audit-confirmed gaps that this change set did **not** close. They are
listed rather than implied:

- **Workflow triggers (spec §56–§59).** Cron/interval/event triggers are not
  implemented. The intent remains to ride the existing `ScheduledTaskService`
  rather than create a second cron owner, and the honest refusal at
  `dynamic_service.py` for recurring prompts is unchanged — it discloses the
  missing handoff instead of pretending to schedule.
- **Failure quarantine / dead-letter.** Exhausted nodes end `FAILED` and are
  reconciled, but there is no separate quarantine store holding them for
  operator replay.
- **Connectivity wait state.** A node waiting on network reachability has no
  first-class wait state of its own.
- **Goal-drift detection.** No detector compares a run's trajectory against its
  original goal.
- **Worktree claiming.** Per-task git worktree isolation is not wired into the
  workflow plane.

## Honesty boundaries

- The lease store, the event log and wave concurrency are **process-local**.
  They are atomic and restart-recoverable for a single Gateway; they are not a
  shared multi-worker lease repository and are never described as
  cross-process exactly-once.
- Hydration **still refuses** stale projections. `/recover` is an explicit,
  owner-scoped route; it does not relax `/hydrate`.
- A completed run is never a verified run, and a dry run
  (`dry_run_simulation`) carries no acceptance verdict.

## Verification

| Gate | Result |
| --- | --- |
| `test_workflow_failures.py` | 49 passed |
| `test_workflow_leases.py` | 30 passed (incl. 2 concurrency regressions) |
| `test_workflow_graph_diff.py` | 26 passed |
| `test_workflow_plan_diff_router.py` | 12 passed |
| `test_workflow_durability_router.py` | 12 passed |
| `test_workflow_verification.py` | 55 passed |
| `test_workflow_event_identity.py` | 5 passed |
| runtime + leases together | 81 passed |
| concurrency subset × 4 repeats | 12 passed each |
| `test_docs_index.py` + `test_docs_claim_honesty.py` + classification | 8 passed |
| `test_harness_boundary.py` + `test_feature_manifest_wiring.py` | passed |
| `ruff format --check` / `ruff check` | clean |
| broad `-k "workflow or dynamic or event_log or plan_graph or orchestrator"` | **698 passed, 7 skipped**; 1 failure, see below |

The two concurrency tests above were **proven to fail without the fix** — the
guard was removed, the suite went red, then restored — so they pin the bug
rather than the post-fix state.

### Failures this change set does not own

- `test_client_live_policy.py::test_ci_unit_test_workflow_runs_duration_aware_shards`
  is pre-existing on `main` (`.test_durations` is absent) and unrelated.
- `test_no_orphan_modules.py` reports `alpha.groups.enforcement` and
  `alpha.tools.discovery.code_mode` as unreferenced. Both files are **not in
  this change set**, and the orphan is a property of this branch's base commit
  `5e7063e`: the main tree's later `alpha/groups/write_watch.py` carries the
  `from alpha.groups.enforcement import …` wiring that this base predates. The
  modules added here (`alpha.workflow.failures`, `alpha.workflow.graph_diff`)
  are absent from that list precisely because they are imported by
  `runtime.py` and `workflows.py`. Neither subsystem is in scope to change.
