# Dynamic Workflows

Alpha's Dynamic Workflow Engine (DWE) is a typed, evidence-gated graph runtime.
A workflow definition is a template; each run owns its state, node statuses,
budgets, approvals, patches, and terminal outcome. The Gateway remains the owner
of ordinary Agent runs, and the DWE is a correlated child orchestration plane for
hosts that explicitly use it.

## Choosing an entry point

### Full dynamic loop (opt in)

`POST /api/workflows/dynamic/perceive` previews the deterministic intent/domain
classifier, task decomposition, execution waves, and resource preview without
starting a run:

```http
POST /api/workflows/dynamic/perceive
Content-Type: application/json

{"prompt":"implement and verify a feature","context":{}}
```

`POST /api/workflows/dynamic/execute` performs the same perception and
compilation, registers the graph, optionally starts it, and returns the measured
run result:

```http
POST /api/workflows/dynamic/execute
Content-Type: application/json

{
  "prompt":"implement and verify a feature",
  "mode":"normal",
  "auto_execute":true,
  "max_steps":40
}
```

The response includes the selected decisions, discovered registries, resource
provenance, graph/run identifiers, node outputs, replans, compensation attempts,
and an explicit acceptance label. When a host supplies the shared
`ExecutionKernel`, the dynamic bridge starts the run and dispatches every wave
(and applies runtime replan patches) through that kernel's per-run claim. An
explicitly unbound bridge does not fall back to the live executor registry, so
an installed digest executor cannot silently turn a missing host binding into
claimed work. The default `alpha.local.digest` executor is only a recomputable
**graph projection**: it can demonstrate scheduling, versioning, and evidence
plumbing, but it cannot claim that a domain task was performed. Such responses
carry `execution_label: "local_digest_projection"` and
`acceptance_passed: false`. A host must bind a real executor before domain
acceptance can be true.

For `auto_execute: true`, the Gateway checks the default and task executors
against its live registry before resource assembly or workflow registration. If
any required executor is unbound, the request returns `503` with the missing
names and creates no workflow or run. Compile-only requests do not require
executor bindings and never start execution.

Recurring automation is deliberately not converted into an in-process cron
loop. The service reports the missing scheduler handoff and leaves recurring
execution to the existing scheduler/host lifecycle.

### Paradigm turns and bot mode

`POST /api/workflows/turns` remains the low-latency compatibility seam. By
default it maps a known paradigm (`direct_agent`, `subagent`, `bot_profile`,
`moa`, `deep_research`, or `deep_think`) to a bounded graph and returns a
`TurnOutcome`. Unknown paradigms and the dynamic swarm paradigm fail before a
run is created. Set `dynamic: true` to use the full perception/discovery service
for that turn instead.

`POST /api/bots/{name}/workflow` runs the same service in `bot` mode after the
server validates the bot and authorizes the caller. It is not a shortcut that
turns an inbox message or bot profile into evidence of work; the same executor
and acceptance rules apply.

## Run control and mutation

Definitions and runs are created through:

- `POST /api/workflows` and `GET /api/workflows`
- `POST /api/workflows/{workflow_id}/runs`
- `GET /api/workflows/runs` and `GET /api/workflows/runs/{run_id}`

A run can be advanced one scheduling wave with
`POST /api/workflows/runs/{run_id}/step`, stopped with
`POST /api/workflows/runs/{run_id}/cancel`, and inspected through its event
stream. Approval-gated nodes pause in `waiting_approval`; resolve the exact
request with `POST /api/workflows/runs/{run_id}/approvals/{node_id}`. Approval
IDs are checked against the active request so a stale client decision cannot
resume a different gate.

Graph changes use optimistic concurrency. `POST .../patch` requires the current
`base_graph_version`, validates typed operations and structural invariants, and
journals the committed revision. `POST .../replan` creates a bounded repair
patch and reports `committed_resume_failed` when the repaired graph cannot be
reopened or dispatched. `POST .../compensate` invokes only a real, dedicated
compensation executor; a missing callback or missing evidence is a disclosed
failure, never a fabricated rollback receipt.

To see what a revision actually changed,

```
GET /api/workflows/{workflow_id}/plans/{version}/diff?base={n}
```

compares two recorded plan revisions. The differ separates **structural**
changes — nodes added, removed or re-typed, edges rerouted — from **runtime**
changes such as prompts, budgets, timeouts, retries and policy, so a
cosmetic prompt edit is never reported as a graph reshape. The reason string
comes from the recorded `PlanVersion.note`/`source` and is never invented. The
payload is deterministically ordered, bounded, and truncated with an explicit
marker rather than allowed to grow without limit.

`GET /api/workflows/system/registries` is a bounded, read-only view of the
capability, tool, skill, MCP, subagent, and bot registries used by planning.
Registry health and unavailable entries are returned as data; discovery does
not imply that a provider is connected or authorized.

## Bounded execution, concurrency, and measured time

**Node deadlines are enforced.** `WorkflowNode.timeout_seconds` runs every
executor call for that node under a real deadline. On expiry the node FAILS with
the measured overrun and a `node_timeout` event; the in-flight call is *fenced,
not killed*, and its late result is discarded rather than adopted. CPython
cannot safely kill a thread, so the contract is disclosure, not a false
"cancelled" claim. For a `MAP`/`REDUCE`/`RACE`/`QUORUM` child the bound is **per
child execution**, not for the whole fan-out, so one pathological item cannot
consume the entire budget.

**Wave concurrency is opt-in.** The scheduler already partitions ready nodes
into waves whose `write_scope` entries are pairwise disjoint, and the engine now
executes a wave on a bounded thread pool so those disjoint scopes actually
overlap. The limit comes from `policies.max_concurrency` (or graph
`metadata.max_concurrency`); **an undeclared workflow runs sequentially**,
because parallel waves reorder the event log relative to node order and would
silently change the observable behaviour — and the replay, projection, and
hydration built on that log — of every existing definition. A wave that cannot
be fully admitted is admitted partially and the refusal is journalled. When a
node's write scope does overlap another's, the later node is deferred to the next
wave and a `wave_write_scope_serialized` event names the pairs, so the
serialization is explained rather than silent.

Shared run bookkeeping is guarded by a process-wide reentrant lock covering every
read-modify-write (token charges, membership-guarded list appends, status
transitions, timing updates). The executor call runs outside it, which is what
delivers the parallelism; the lock only makes the microsecond-scale bookkeeping
safe now that several nodes can be in flight at once.

**Measured time is journalled, not held in run state.** Every node execution
emits a `node_timed` event and every dispatched wave a `wave_dispatched` event.
`build_run_observability` projects the per-node timeline, wave shape, slowest
nodes, timed-out nodes, and the critical path from those events. They are events
rather than `run.metrics` fields because the DWE guarantees everything in
`run.metrics` is reconstructible from the journal, and a duration cannot be
re-derived from the events that recorded the work. The useful consequence is that
timings survive a restart and a durable hydration.

`GET /api/workflows/runs/{run_id}/report` returns the combined history,
observability, and provenance payload. It reports **execution only** and carries
no acceptance verdict: a completed run is not a verified run.

## Node kinds that need no executor

These complete on a measurement the runtime takes itself, so they do not demand
an executor for work the engine already did:

| Kind | Behaviour |
| --- | --- |
| `CHECKPOINT` | Records a content-addressed SHA-256 snapshot of the run state. The evidence is recomputable, and it states that the append-only event log remains the authoritative durable record. |
| `GOAL_GATE` | Evaluates declared `acceptance_criteria` with the same safe AST evaluator used for routing. A criterion that cannot be evaluated counts as NOT met, so a gate never passes on the strength of a check that did not run. Already protected from removal or replacement by the patch validator. |
| `HANDOFF` | Publishes a handoff contract built from real run state. `decisions` stays empty because the engine journals none. |
| `WAIT` | A bounded timer. The delay is clamped to a hard ceiling, and the MEASURED sleep is reported rather than the requested number. |
| `EVENT_WAIT` | Parks the node until a named signal arrives, making `WAITING_EVENT` reachable. |
| `PARALLEL` | Runs a named member set as one bounded wave. All-or-nothing: a single non-succeeded member fails the group. |
| `SUBWORKFLOW` | Runs a registered child workflow to a terminal state through this same engine and adopts only a genuinely `completed` child. Self-recursion is refused. |

## Declared verification

A node may declare `verification_cmd`, and since `alpha.workflow.verification`
exists that declaration now **executes** — before, nothing in the tree read it,
so a run could succeed while its own plan still named the check meant to prove
that success.

### Resolution never reaches a subprocess

The declaration is client-supplied input (`POST /api/workflows` takes
`body.graph` verbatim, and `update_node_config` writes node config), so it
resolves to exactly three things, in order:

1. a verifier the host registered with
   `DynamicWorkflowEngine.register_verifier(name, callable)`;
2. a dotted path inside the allowlisted `alpha.` prefix;
3. a shell-style command handed to a **host-bound** `verification_executor`.

That executor is **absent by default**, so `pytest -q` — the one declaration
the decomposer still emits — is `not_run` until a host binds one. It is never
spawned speculatively. Every other dotted path, `os.system` included, is
refused at resolution *before* any import.

### The verdict contract

A zero-argument callable returning a bool. Dict keys
`passed`/`ok`/`success`/`verdict` and the pass/fail strings are coerced;
**anything else — including `None` — is `not_run`,** never a guess. An
exception is `not_run` carrying the real reason: not a pass, and not a failure
either.

| Verdict | Effect on the node |
| --- | --- |
| `passed` | completes; **only this** appends evidence |
| `failed` | **blocks** — `_fail_node` with the verifier's reason and a `verification` block |
| `unresolved` | **blocks**, the same way |
| `not_run` | completes, journalled as `node_verification` with `passed: false` |
| `not_declared` | no event at all |

`not_run` therefore completes the node without ever claiming it passed —
exactly how an unbound compensation callback reports `executed: False`.

### Where the gate runs, and where it does not

The gate sits on the **default / agent / tool / bot path** of
`_execute_single_node`, after the lease verdict and the evidence check but
*before* either is folded into run state, and outside `_STATE_LOCK` (an
operator-bound executor may block, and the lock guards bookkeeping, not command
execution).

Kinds the runtime measures itself do **not** run a declared verifier: the
executor-free kinds above plus `condition`, `router`, `map`, `reduce`, `race`,
`quorum` and `compensation`, all of which return before that path. That is a
disclosed boundary pinned by
`test_a_structural_node_kind_does_not_execute_a_declared_verifier` — declare a
verifier on one of those kinds and it will not be executed.

### Reading the outcome

- `GET /api/workflows/runs/{run_id}/events` carries every `node_verification`
  event, so per-node outcomes are durable and replayable.
- `POST /api/workflows/dynamic/execute` returns `metadata["verification"]` —
  registry size, whether an executor is bound, and the ids of nodes that
  declared a check. It asserts **no verdict**: re-summarising outcomes from a
  process-local buffer could report `0 verified` after an eviction, which is
  precisely the false success that field must not create.
- `acceptance_passed` is the *execution* axis and stays separate, so a consumer
  cannot mistake "real work ran" for "the check passed".

### What the decomposer declares

Six of its seven names pointed at functions that exist nowhere in this tree, or
at `alpha.skills.authoring.validate_skill_draft`, which takes three arguments
and can never satisfy a zero-argument contract. With resolution live, each would
have **failed every node it was attached to**, so they are gone; their intent
stays in `verification_criteria`. `pytest -q` is the one declaration remaining,
and it runs the moment a host binds an executor.

## External events, suspension, and waiting

- `POST /api/workflows/runs/{run_id}/signals` delivers a named signal. Only
  nodes registered for exactly that event are released, and a released node
  returns to `READY` (the scheduler admits only `PENDING`/`READY`, so a node left
  in `WAITING` could never be re-dispatched). A signal nothing waits on is
  journalled as unmatched and changes no node state, so a typo cannot silently
  advance a run.
- `POST /api/workflows/runs/{run_id}/sweep-waits` fails every external wait whose
  declared deadline has passed, with the measured age, and then applies the same
  fail-closed policy a wave does. A wait nobody satisfies must end as a failure
  rather than leaving the run non-terminal and reporting no error.
- `POST /api/workflows/runs/{run_id}/suspend` parks a live run in `SUSPENDED`
  without inventing a terminal outcome; `.../resume` releases it. Stepping a
  parked run returns its real status instead of continuing held work.

## Forking, time travel, and dry runs

- `GET /api/workflows/runs/{run_id}/history` returns the ordered, replayable
  timeline with stable 1-based indexes, which a fork can quote back.
- `POST /api/workflows/runs/{run_id}/fork` branches a NEW run from a point in
  that history. Completed work at the fork point is **inherited rather than
  repeated**, because replaying a model call or sandbox write would double a real
  side effect; each fork gets its own workflow id and graph so two forks never
  share mutable state; and the source is never mutated.
  `reset_completed_nodes` re-runs that work deliberately and is disclosed as
  dangerous, because idempotency keys are per-run and cannot protect a repeated
  effect. An un-replayable prefix, an unknown event id, and an out-of-range index
  are all rejected with the real reason — and so is an **ambiguous** one: event
  ids are unique (a sortable timestamp plus a process-local counter), and a log
  whose ids collide is refused by name rather than resolved to the first match,
  because that would silently truncate the prefix and re-run work the fork
  claimed to inherit.
- `POST /api/workflows/simulate` dry-runs a registered workflow against a
  recording executor on a **throwaway engine**, so it cannot touch the caller's
  definitions, runs, durable sink, or token budgets. Every result is labelled
  `dry_run_simulation`, charges zero tokens, and carries no acceptance verdict. A
  graph that parks at a gate says so instead of projecting past it.

## Real domain executors

The registry previously shipped only `alpha.local.digest` (a hash) and six
bounded local projections, so no node could invoke a model, tool, or subagent.
`alpha.orchestrator.domain_executors` adds three that perform genuine work:

| Key | What it really does |
| --- | --- |
| `alpha.local.model` | Resolves a real chat model through the model factory and invokes it, reporting the provider's own token usage (0 when none was reported — never an estimate). An unknown model name surfaces the factory's own error instead of silently falling back. |
| `alpha.local.tool` | Dispatches through the real `ScriptDispatcher`, so a workflow node gets the same tool list and the same guardrail decision a model-issued call would get. |
| `alpha.local.subagent` | Delegates through the real `SubagentExecutor`, mirroring `task_tool`'s construction. |

They are **opt-in**: `bind_domain_executors()` must be called explicitly, because
these spend money and reach the network and must never be bound merely by
importing a module. The engine's node seam is synchronous while tool assembly is
async, so the executors bridge through a dedicated worker loop and **refuse**
when called from a thread with a running event loop rather than deadlocking.
`GET /api/workflows/system/executors` reports what is actually bound, whether
all three domain executors are bound, and the host-managed opt-in policy. There
is intentionally no `config.yaml` switch for binding them: this Gateway path
does not yet define the strict executor allowlist, per-run budget enforcement,
and approval contract needed to safely expose paid model calls or side-effecting
tools to dynamic workflows. Operators must not treat importing the module,
listing an executor, or compiling a workflow as permission to execute it.
The workflow run inspector distinguishes an unbound registry, the digest-only
projection, and host-bound domain executors. A host binding means an executor
can resolve a matching node; it does not prove that the executor ran or that
the workflow's goal was accepted. Older Gateway responses that omit readiness
fields are shown as unknown rather than inferred from the executor list.
For auto-execution, the Gateway checks every executor named by the decomposed
tasks against the live registry before resource assembly, plan persistence, or
run creation. Missing bindings return 503 with the executor names. Compile-only
requests still assemble without provisioning resources, register the compiled
workflow, and persist its initial plan revision; they do not start a workflow
run or execute any node.

A tool that needs populated `runtime.state` (sandbox paths, thread outputs) has
none on this seam, because it is not inside a LangGraph run. The tool's own real
error is surfaced rather than fabricating that context.

## Templates and improvement proposals

`alpha.workflow.templates` stores reusable graphs with an enforced
`draft -> verified -> promoted` lifecycle. `capture_from_run` always yields a
draft — capturing a graph and vouching for it are separate acts. `verify`
re-checks that a run COMPLETED, that the run's graph is structurally identical to
the template's, and that every succeeded node carried real evidence. `promote`
refuses a draft. `instantiate` deep-copies, so patching one caller's run cannot
corrupt the library.

`alpha.workflow.self_improvement` turns measured signals into typed proposals.
**A suggestion is a proposal, never an action** — nothing in it mutates a run, a
graph, or a template, and applying one produces a normal typed patch that must
still pass `PatchValidator` and the run's optimistic-concurrency check. Every
suggestion cites the measured signal that produced it, confidence is derived from
sample count rather than asserted, and a completion asserted without evidence is
reported as `unproven` instead of being folded into a success rate. Signals that
would fire on every serial workflow (parallelisation, wave underuse) require a
real independent sibling, because in a linear chain the last node always
dominates and always has nothing to overlap with.

## Evidence, replay, and durability

Every workflow event is appended to the durable JSONL sink before listeners are
notified. The Gateway sink fails closed when persistence fails; a run is not
advertised as durably executable when the store is unwritable. The following
surfaces expose the actual journal rather than an in-memory success shape:

- `GET /api/workflows/system/durability`
- `GET /api/workflows/runs/{run_id}/events/durable`
- `POST /api/workflows/runs/{run_id}/project`
- `POST /api/workflows/hydrate`
- `GET|POST /api/workflows/{workflow_id}/plans`

Projection and hydration report corrupt tails, stale projections, missing
graphs/definitions, and skipped owner-scoped resources. Replay folds the
validated event log into a fresh projection and reports covered-field
mismatches; it never emits new replay events. The engine keeps the authored
base graph separately from its compatibility projection, so replay can reapply
patch revisions in order and restore journaled node outputs without treating a
latest projected graph as the original template.

Workflow plan revisions are append-only. Ordinary registration does not create
an implicit revision; the explicit plan endpoint records the current graph, and
the first patch seeds its real base revision when necessary before advancing it
with compare-and-set. A same-version different graph is rejected rather than
overwritten.

## Ownership and safety

For real HTTP requests, workflow definitions and runs carry a server-resolved
owner. Reads and mutations enforce that owner; request bodies cannot self-assert
ownership. Event payloads are redacted before persistence. Model/user text is
data, not a system instruction. The workflow plane does not replace
`RunManager`, the group/bot lifecycle owner, or the scheduler, and it does not
create a second parent-run stream.

## Failure classification and recovery

**Why a node failed is decided, not guessed.**
`alpha.workflow.failures` classifies each node failure into one of nineteen
`NodeFailureClass` values by ordered keyword rules, and every classification
discloses the rule that matched rather than presenting an unexplained label.
This is a **retry-decision classification**, not an error-code taxonomy:
`alpha.errors.registry` remains the sole owner of stable, customer-facing error
codes, and this module bridges onto vocabularies that already existed instead
of growing a seventh one.

- `ClassifiedFailure.recovery_class()` routes onto `alpha.recovery.policies`,
  which stays the single authority on *what to do* after a failure.
  `recovery_exhausted` events carry the terminal strategy it chose together
  with `strategy_source` and `strategy_bridged`, so the bridge is visible.
- `ClassifiedFailure.reason_code()` maps onto `alpha.bots.failure_reasons`, so a
  node reads as the same work unit a swarm task or subagent would.

`StagnationDetector` measures non-progress over `error_signature()`, a
normalized rendering in which identifiers, numbers, paths and hex digests
collapse away — so "the same fault wearing different digits" is measurable
instead of argued about. The engine emits `failure_classified`,
`node_stagnated`, `recovery_exhausted` and `node_retry_refused`.

**Every attempt holds a durable, fenced lease.**
`alpha.workflow.leases` records each attempt at
`runtime_home()/workflow_store/leases.json` with an atomic replace. The
lifecycle is: acquire *before* the node is marked `RUNNING`, release in
`finally`, and **check the fence before adopting any output**. A result whose
lease reports `STALE_LEASE`, `SUPERSEDED_REVISION` or `UNKNOWN_LEASE` is
discarded rather than written through — an unverifiable key is refused, never
assumed fresh. A lease refusal at dispatch is a one-shot honest failure naming
the worker that already holds the claim, never a retry loop. The fence
deliberately survives release and **advances** on reclaim or expiry, so a dead
worker's late result reads `STALE_LEASE` instead of `ACCEPTED`.

**A dead worker can no longer strand a run.** The scheduler admits only
`PENDING`/`READY` and replay folds `node_started` into `RUNNING` with no
completer, so before this a worker that died mid-node left the run permanently
stuck on work nobody owns — and a restart reproduced that state from the
journal. `reconcile_orphaned_nodes()` now runs at the top of every step: a node
this process is actually executing (tracked in a process-local `_in_flight`
set) or one with a live lease held by another worker is held; anything else
`RUNNING` is **failed** with `failure_class=worker_lost`, because its side
effects are unknown and it is never silently reset. The reconciliation is
journalled as `orphaned_nodes_reconciled`.

Because hydration correctly refuses a run whose projection lags its journal —
and that refusal is load-bearing — such a run previously had no way back.
`POST /api/workflows/runs/{run_id}/recover` is the explicit, owner-scoped way
back: it folds the real journal into a fresh run, installs it, reconciles the
orphaned nodes, and only then re-materialises the projection (projecting
*after* reconciliation, because reconciliation itself appends events). The
response reports what was folded and what was reconciled, and never reports
the rebuilt run as verified.

## Current boundaries

The orchestration graph, scheduling, retries, approvals, conditional routing,
bounded loops, patch OCC, replay, and compensation plumbing are implemented, as
are real node deadlines, opt-in wave concurrency, the seven executor-free node
kinds, external-signal waits, operator suspend/resume, forking, dry-run
simulation, measured observability, template promotion, improvement proposals,
failure classification, durable attempt leases and crash recovery.

What remains true and must keep being said plainly:

- A deadline is enforced by **fencing**, not by cancelling: CPython cannot kill a
  thread, so timed-out work may still be completing in the background and its
  result is discarded rather than adopted.
- Wave concurrency, the durable event log **and the lease store** are
  **process-local**. They are atomic and restart-recoverable for ONE Gateway
  process; a multi-worker deployment still needs shared lease/coordination
  before claiming cross-process exactly-once execution. The lease `worker_id`
  is a pid for exactly that reason — as specific as the guarantee available.
- Hydration **still refuses** stale projections. `/recover` is an explicit
  route and does not relax `/hydrate`.
- The template store is local and atomic for ONE Gateway process. It is not a
  shared multi-worker repository.
- `alpha.local.digest` remains a `local_digest_projection`. Binding a real
  domain executor is an explicit host opt-in, and a run is only domain-complete
  when a real executor produced its evidence.
- A **dry run is a projection**. It shares no state with the caller's engine, is
  handed a process-local lease manager, and asserts nothing about acceptance.
- An **improvement suggestion is a proposal**. Nothing in it has been shown to
  work; only a re-measured run can show that.
- A **completed run is never a verified run**, and recovery only ever rebuilds a
  run to match its journal — it does not vouch for the work.

Known gaps that are not implemented (see
[`ALPHA-WORKFLOW-CURRENT-STATE.md`](ALPHA-WORKFLOW-CURRENT-STATE.md) for the
full list): workflow triggers still disclose the missing scheduler handoff
rather than creating a second cron owner, and there is no failure quarantine
store, no connectivity wait state, no goal-drift detection and no worktree
claiming.

## Regression coverage

The implementation is covered by `backend/tests/test_dynamic_workflow_service.py`,
`test_dynamic_workflow_router.py`, `test_dynamic_workflow_engine.py`,
`test_workflow_dag_edges.py`, `test_workflow_durability_router.py`,
`test_orchestrator_kernel.py`, `test_orchestrator_mode_mapper.py`, and
`test_bot_dynamic_workflow.py`, plus:

- `test_workflow_runtime_correctness.py` — deadlines, opt-in concurrency,
  executor-free node kinds, signals, suspend/resume, shared-state safety, and
  the write-scope disclosure
- `test_workflow_time_travel.py` — history, forking, and dry-run simulation
- `test_workflow_templates_and_improvement.py` — the template lifecycle and
  evidence-cited proposals
- `test_workflow_observability_router.py` — the REST observability/control routes
- `test_workflow_verification.py` — declared verification: resolution and its
  refusals, the verdict contract, node-gate semantics, the disclosed scope
  boundary, and the bridge posture
- `test_workflow_event_identity.py` — event ids are unique, a fork inherits
  completed work, and an ambiguous id is refused by name
- `test_workflow_leases.py` — claim/fence/expiry/reclaim, orphan
  reconciliation, dry-run isolation, and lease-store corruption
- `test_workflow_failures.py` — the nineteen failure classes, rule
  disclosure, stagnation, and the recovery/reason bridges
- `test_workflow_graph_diff.py` and `test_workflow_plan_diff_router.py` —
  structural-vs-runtime revision diff, its endpoint, and owner scoping
- `test_workflow_durability_router.py` — the journal, projection, the
  stale-projection refusal, and `/recover`

and the frontend `workflows.test.mjs` / `workflows-observability.test.mjs` client
contract tests.
