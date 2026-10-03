# Dynamic Workflow Engine — Architecture

How the dynamic workflow plane is put together, which module owns which
decision, and where the honesty boundaries sit.

Operations, API examples and the regression suites:
[`docs/DYNAMIC_WORKFLOWS.md`](DYNAMIC_WORKFLOWS.md). What has landed and what
has not: [`docs/ALPHA-WORKFLOW-CURRENT-STATE.md`](ALPHA-WORKFLOW-CURRENT-STATE.md).

## The layer cake

| Layer | Module | Owns |
| --- | --- | --- |
| Typed graph & run state | `alpha.workflow.models` | nodes, edges, policies, `NodeStatus`, `WorkflowRun` |
| Event journal | `alpha.workflow.event_log` | append-only JSONL, projection, hydration, replay inputs |
| Replay fold | `alpha.orchestrator.replay` | a **pure** fold from journal → run state; emits nothing |
| Execution engine | `alpha.workflow.runtime` | waves, retries, budgets, approvals, patches, leases |
| Attempt claims | `alpha.workflow.leases` | durable fenced leases and result verdicts |
| Failure decisions | `alpha.workflow.failures` | retry-decision classification and stagnation |
| Plan revisions | `alpha.workflow.plan_graph` | CAS revision history |
| Revision diff | `alpha.workflow.graph_diff` | structural vs runtime change between revisions |
| REST surface | `app.gateway.routers.workflows` | owner scoping, off-loop I/O, fail-closed sink |

The harness never imports `app.*`; the Gateway reaches the engine through
`get_workflow_engine()` and calls its synchronous methods behind
`asyncio.to_thread`, so lease and journal disk I/O never lands on the event loop.

## Attempt lifecycle

```
ready ──► acquire_lease ──► RUNNING ──► runner returns ──► check_result ──► SUCCEEDED/FAILED
             │                                                        │
             └── refused (someone else holds it) ──► honest one-shot failure
                                                                  │
                          verdict ≠ ACCEPTED ──► result discarded, node failed
```

1. **Acquire before `RUNNING`.** A node is never marked `RUNNING` until its
   lease is recorded. A refusal is a one-shot honest failure naming the worker
   that already holds the claim — never a retry loop.
2. **Fence check before adopting output.** `check_result` runs *before* the
   runner's output is folded into run state. `STALE_LEASE`,
   `SUPERSEDED_REVISION` and `UNKNOWN_LEASE` all discard the work rather than
   writing it through. An unverifiable key is refused, never assumed fresh.
3. **Release in `finally`.** The lease is dropped and the process-local
   `_in_flight` entry discarded on every path out, including exceptions.

The fence is a monotonic per-key counter. It deliberately survives
`release_lease` (so a late result stays distinguishable) and **advances** on
reclaim or expiry (so a dead worker's late result reads `STALE_LEASE`, not
`ACCEPTED`).

## Orphan reconciliation

The scheduler admits only `PENDING`/`READY`, and the replay fold turns
`node_started` into `RUNNING` with no completer. Before leases existed, a
worker that died mid-node therefore left a run permanently stuck on work nobody
owns — and a restart reproduced it from the journal.

`DynamicWorkflowEngine.reconcile_orphaned_nodes()` runs at the top of
`_execute_step_locked()`, before anything is scheduled:

- a node this process is executing (`_in_flight`) is held — the lease TTL is a
  wall-clock observation and lapses on a legitimate long-running node;
- a live lease held by *another* worker is held;
- everything else `RUNNING` is **failed** with `failure_class=worker_lost`,
  because its side effects are unknown — never silently reset to `PENDING`;
- the reconciliation is journalled as `orphaned_nodes_reconciled`.

The engine's own bounded stagnation recovery may then reopen that node as a
fresh attempt; that is the pre-existing policy, it is journalled as `node_failed`
plus a `retry_node` patch, and it is bounded by `STAGNATION_RECOVERY_LIMIT`.

The process-local `_in_flight` set is ground truth for "is this node running
right now". It must never be removed: without it a healthy node whose wall-clock
TTL lapsed would be reconciled out from under a live worker.

## Recovering a crashed run

This is the part that used to be a dead end, so the chain is worth stating
explicitly:

1. A crash mid-node leaves the projection **behind** its journal
   (`last_seq` < durable last event).
2. `event_log.hydrate()` correctly **refuses** that run — a stale projection is
   never presented as current. This refusal is load-bearing and pinned by
   `test_event_log_durability.py` and `test_workflow_durability_router.py`; it
   is not something to relax.
3. The consequence was that the run simply had no way back: `GET /runs/{id}`
   404s and `/replay` is deliberately read-only (it folds into a scratch engine
   and reports `matches_live`, mutating nothing).
4. **`POST /api/workflows/runs/{run_id}/recover`** is the explicit way back.
   It folds the real journal into a fresh run on a scratch engine, hands that to
   `DynamicWorkflowEngine.adopt_replayed_run()`, reconciles any node a dead
   worker left `RUNNING`, and only then re-materialises the projection —
   projecting *after* reconciliation, because reconciliation itself appends
   events and projecting the pre-recovery log would leave the run stale again.

Recovery reports what was folded and what was reconciled. It never reports the
rebuilt run as verified.

## Failure classification

`alpha.workflow.failures` is a **retry-decision classification**, not an
error-code taxonomy. Nineteen `NodeFailureClass` values are produced by ordered
keyword rules that disclose the rule that matched, and the module deliberately
bridges onto vocabularies that already exist rather than growing a seventh
one:

- `ClassifiedFailure.recovery_class()` → `alpha.recovery.policies`, which stays
  the single authority on *what to do* after a failure. `recovery_exhausted`
  events carry the terminal strategy it chose, with `strategy_source` and
  `strategy_bridged` so the bridge is visible rather than implied.
- `ClassifiedFailure.reason_code()` → `alpha.bots.failure_reasons`, so a node
  reads as the same work unit a swarm task or subagent would.
- `alpha.errors.registry` remains the sole owner of stable, customer-facing
  error codes. This module never invents one.

`StagnationDetector` measures non-progress over `error_signature()` — a
normalized rendering (identifiers, numbers, paths and hex digests collapse
away) — so "the same fault wearing different digits" is measurable instead of
argued about.

Emitted events: `failure_classified`, `node_stagnated`, `recovery_exhausted`,
`node_retry_refused`.

## Plan revision diff

`alpha.workflow.graph_diff.diff_graphs()` compares two graph revisions and
separates **structural** changes (nodes added/removed/re-typed, edges rerouted)
from **runtime** changes (prompts, budgets, timeouts, retries, policy). The
reason string comes from the recorded `PlanVersion.note`/`source`, never
invented by the differ. Output is deterministically ordered, bounded and
truncated with a marker rather than allowed to grow without limit.

`GET /api/workflows/{workflow_id}/plans/{version}/diff?base=<n>` exposes it.

## Honesty boundaries

These are disclosed limitations, not bugs to silently "fix":

- **The lease store is single-Gateway.** It is atomic (`os.replace`) and
  restart-recoverable for one process; it is not a shared SQL lease repository
  and gives no cross-process exactly-once guarantee. `worker_id` is a pid for
  exactly that reason — as specific as the guarantee actually available.
- **The event log and wave concurrency are likewise process-local.** Parallel
  waves reorder the event log, which is why `policies.max_concurrency` is
  opt-in: a workflow that never declares it runs sequentially and keeps its
  replay, projection and hydration bit-identical.
- **Hydration still refuses stale projections.** Recovery is an explicit,
  owner-scoped route; it is not folded into `/hydrate`.
- **A completed run is never a verified run**, and a dry run
  (`dry_run_simulation`) carries no acceptance verdict.
- **A declared verifier is a gate only where one can run.** `verification_cmd`
  executes on the default / agent / tool / bot path alone; node kinds the
  runtime measures itself — the executor-free kinds plus `condition`, `router`,
  `map`, `reduce`, `race`, `quorum` and `compensation` — return before that
  path and do not execute it. Shell commands run only through an
  operator-bound executor that is absent by default, so they report `not_run`:
  never a pass, and never a spawn. `not_run` completes the node with
  `passed: false` rather than hiding the fact that the check did not happen.

## Test suites

| Suite | Covers |
| --- | --- |
| `tests/test_workflow_leases.py` | claim/fence/expiry/reclaim, orphan reconciliation, dry-run isolation, store corruption |
| `tests/test_workflow_failures.py` | the 19 classes, rule disclosure, stagnation, recovery bridges, two-gate retry |
| `tests/test_workflow_graph_diff.py` | structural vs runtime, determinism, bounding, redaction |
| `tests/test_workflow_plan_diff_router.py` | the diff endpoint, owner scoping, 404s |
| `tests/test_workflow_durability_router.py` | journal, projection, hydration refusal, and `/recover` |
| `tests/test_workflow_event_identity.py` | event ids are unique, a fork inherits completed work, and an ambiguous id is refused |
| `tests/test_workflow_verification.py` | resolution and its refusals, the verdict contract, node-gate blocking, the disclosed scope boundary, and the bridge posture |
| `tests/test_workflow_runtime_correctness.py` | waves, concurrency, budgets, retries |
