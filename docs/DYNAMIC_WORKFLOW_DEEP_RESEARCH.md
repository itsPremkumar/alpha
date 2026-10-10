# Dynamic Workflow Engine — Deep Research & Improvement Analysis

A comprehensive architectural review of the dynamic workflow plane: what exists,
how it fits together, and the advanced implementation opportunities that would
most improve capability without breaking the honesty contract.

## 1. System Overview

The Dynamic Workflow Engine (DWE) is a typed, evidence-gated graph runtime.
A workflow definition is a template; each run owns its state, node statuses,
budgets, approvals, patches, and terminal outcome. The Gateway remains the
owner of ordinary Agent runs; the DWE is a correlated child orchestration plane
for hosts that explicitly use it.

**Core invariant:** every claim is measured, every failure carries the real
reason, and nothing is fabricated when a prerequisite is missing.

## 2. Architecture — The Layer Cake

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

### Composition chain

```
DynamicWorkflowService
  └── DynamicWorkflowBridge
        └── ExecutionKernel
              └── DynamicWorkflowEngine
                    ├── WorkflowEventDispatcher → DurableEventLog
                    ├── LeaseManager
                    ├── ExecutorRegistry
                    └── PatchValidator / RuntimeReplanner
```

### Pipeline flow

```
prompt → DynamicPerceptionEngine.perceive()
       → DynamicDecomposer.decompose()
       → DynamicResourceAssembler.assemble()
       → DynamicWorkflowBridge.build_workflow_definition()
       → ExecutionKernel.start_run()
       → ExecutionKernel.run_to_completion()
       → ExecutionKernel.handoff()
```

## 3. Core Data Model

### NodeStatus (12 states)

`PENDING`, `READY`, `RUNNING`, `WAITING`, `SUCCEEDED`, `FAILED`, `RETRYING`,
`SKIPPED`, `CANCELLED`, `SUSPENDED`, `COMPENSATING`, `ABORTED`

### NodeType (32 kinds)

`AGENT`, `BOT`, `SUBAGENT`, `TOOL`, `MCP`, `ROUTER`, `CONDITION`, `PARALLEL`,
`MAP`, `REDUCE`, `RACE`, `QUORUM`, `LOOP`, `REVIEW`, `APPROVAL`, `WAIT`,
`EVENT_WAIT`, `SUBWORKFLOW`, `CHECKPOINT`, `COMPENSATION`, `GOAL_GATE`,
`STANDARD`, `SKILL`, `MODEL`, `WORKFLOW`, `AUTOMATION`, `PROJECT`, `COMMAND`,
`HUMAN_APPROVAL`, `MEMORY`, `RESEARCH`, `VALIDATION`, `SYSTEM`, `HANDOFF`,
`CONNECTIVITY_WAIT`

### WorkflowRunStatus (15 states)

`PENDING`, `RUNNING`, `WAITING_APPROVAL`, `WAITING_EVENT`, `SUSPENDED`,
`COMPLETED`, `FAILED`, `CANCELLED`, `BUDGET_EXHAUSTED`, `PLANNING`, `READY`,
`MUTATING`, `COMPENSATING`, `ABORTED`, `WAITING_CONNECTIVITY`

### WorkflowNode (24 fields)

`id`, `type`, `executor`, `idempotency_key`, `config`, `prompt`, `category`,
`condition`, `timeout_seconds`, `retry_policy`, `loop_policy`, `write_scope`,
`requires_approval`, `approval_request_id`, `approval_requested_at`,
`gate_timeout_seconds`, `compensation_node_id`, `budget`, `tokens_consumed`,
`status`, `evidence`, `output`, `depends_on`

### WorkflowRun (20 fields)

`run_id`, `workflow_id`, `owner_id`, `graph_version`, `status`, `state`,
`node_states`, `active_nodes`, `completed_nodes`, `failed_nodes`,
`waiting_nodes`, `iteration_counts`, `metrics`, `tokens_consumed`,
`budget_limit`, `history`, `patches_applied`, `waiting_reason`,
`approval_request_id`, `created_at`, `updated_at`

### PatchOperation (19 declared ops)

`add_node`, `remove_node`, `replace_node`, `update_node_config`, `add_edge`,
`remove_edge`, `update_edge_condition`, `set_route`, `insert_before`,
`insert_after`, `fan_out`, `fan_in`, `create_loop`, `set_loop_limit`,
`retry_node`, `skip_node`, `request_human`, `request_review`

**Supported in core engine (11):** `add_node`, `remove_node`, `replace_node`,
`update_node_config`, `retry_node`, `add_edge`, `remove_edge`,
`insert_before`, `insert_after`, `create_loop`, `set_route`

**Deferred no-op (1):** `update_edge_condition` — recognised but not applied;
the legacy DAG tool applies it at its call site.

**Unsupported (6):** `fan_out`, `fan_in`, `set_loop_limit`, `skip_node`,
`request_human`, `request_review` — refused with an honest reason.

## 4. Run Lifecycle

### start_run

1. Validates definition exists
2. Calls `validate_workflow_graph` BEFORE any run exists — refuses unrunnable
   definitions before any side effect
3. Generates `run_id` as `run_{uuid4().hex[:12]}`
4. Under `_workflow_lock(workflow_id)`: detaches sibling graphs via deepcopy,
   resets all node statuses to PENDING, creates `WorkflowRun` with
   `status=RUNNING`
5. Emits `workflow_started` with a **deepcopy snapshot** of state — logged
   state is a snapshot, later mutations cannot rewrite it

### execute_step (one wave)

1. Acquires `_workflow_lock(run.workflow_id)`
2. Terminal/parked guard — returns immediately if status is terminal or parked
3. **Orphan reconciliation** — `reconcile_orphaned_nodes(run)` fails nodes
   left RUNNING by dead workers
4. **Budget check** — if `tokens_consumed >= budget_limit`, calls
   `_exhaust_budget`
5. **Compute ready nodes** — `scheduler.compute_ready_nodes(graph, run)`
6. **Unselected branch skip** — marks losing conditional branches SKIPPED
7. **Completion check** — all nodes SUCCEEDED/SKIPPED → COMPLETED
8. **Waiting check** — if `run.waiting_nodes` non-empty, return
9. **Deadlock detection** — finds target node; if no target, honest FAILED;
   stagnation recovery proposes evidence remediation patch
10. **Wave dispatch** — `scheduler.partition_into_waves`, write-scope collision
    journaling, bounded thread pool execution
11. **Fail-closed** — if `run.failed_nodes` and no COMPENSATING nodes, FAIL
12. **Completion** — all non-compensation nodes done → COMPLETED
13. **Cleanup** — `_sync_waiting_nodes`, release resources if terminal

### apply_patch (optimistic concurrency)

1. Validates `patch.workflow_run_id == run.run_id`
2. Validates via `PatchValidator.validate` — OCC check on `base_graph_version`
3. Deep-copies nodes/edges, applies each operation
4. Produces `WorkflowGraph(version=graph.version+1, ...)`
5. Updates `run.graph_version`, appends to `run.patches_applied`
6. Emits `patch_committed`

## 5. Key Subsystems

### 5.1 Event System

- `WorkflowEvent` — `event_id` (sortable timestamp + counter), `workflow_run_id`,
  `event_type`, `timestamp`, `idempotency_key`, `payload`
- `WorkflowEventDispatcher` — in-memory log (bounded 10K), optional durable
  sink, `fail_closed_on_durable_error` flag, credential redaction
- `DurableEventLog` — append-only JSONL at `{store}/events/{run_id}.jsonl`,
  idempotent append, fsync, tail-integrity check, projection, hydration

**56 event types** emitted across the lifecycle.

### 5.2 Lease / Orphan Recovery

- `WorkerLease` — `node_id`, `run_id`, `worker_id`, `attempt_id`,
  `fence_token`, `graph_version`, `acquired_at`, `heartbeat_at`, `ttl_seconds`
- `ResultVerdict` — `ACCEPTED`, `STALE_LEASE`, `SUPERSEDED_REVISION`,
  `UNKNOWN_LEASE`
- Acquire before `RUNNING`, release in `finally`, `check_result` before
  adopting output
- Fence survives release and advances on reclaim/expiry
- `reconcile_orphaned_nodes()` at the top of every step

### 5.3 Failure Classification

- 19 `NodeFailureClass` values from ordered keyword rules
- `error_signature()` — normalized rendering (identifiers, numbers, paths,
  hex digests collapse)
- `StagnationDetector` — measures non-progress over signatures
- Bridges onto `alpha.recovery.policies` and `alpha.bots.failure_reasons`

### 5.4 Declared Verification

- Resolves to exactly three things: registered verifier, allowlisted dotted
  path, or host-bound `verification_executor`
- Verdict contract: `passed` (completes + evidence), `failed`/`unresolved`
  (blocks), `not_run` (completes, `passed: false`), `not_declared` (no event)
- Gate runs on default/agent/tool/bot path only; executor-free kinds skip it

### 5.5 Plan Revision Diff

- `diff_graphs()` separates structural changes (nodes added/removed/re-typed,
  edges rerouted) from runtime changes (prompts, budgets, timeouts, retries)
- Reason comes from recorded `PlanVersion.note`/`source` — never invented
- Deterministically ordered, bounded, truncated with a marker

### 5.6 Time Travel

- `run_history()` — ordered, replayable timeline with stable 1-based indexes
- `fork_run()` — branch a NEW run from a point in history; completed work is
  inherited, never repeated; source never mutated
- `simulate_run()` — dry-run on throwaway engine with recording executor;
  labelled `dry_run_simulation`, zero tokens, no acceptance verdict

### 5.7 Templates & Self-Improvement

- Templates: `draft → verified → promoted` lifecycle, evidence-gated
- Self-improvement: bounded, evidence-cited proposals — never actions;
  confidence derived from sample count; unevidenced completions are `unproven`

## 6. The Five New Waves (This Change Set)

### 6.1 Triggers — schedules as data

- Durable, owner-scoped records: cron expression, interval, or named event
- Validation before storage: unparsable, reversed, out-of-bounds, and
  unsatisfiable cron expressions refused with the real reason
- `GET /triggers/due` is the seam a scheduler rides
- `workflow_triggers` autonomy loop (gated under
  `autonomy.loops.workflow_triggers`, **default off**) is the in-process firing
  seam
- Fire order: run starts BEFORE schedule advances; refused start leaves trigger
  due
- Cron advances from **due time**, not fire moment — late fire doesn't push
  subsequent fires out
- Vixie day rule: restricted both, either matches

### 6.2 Quarantine — dead-letter store

- Record written at the exact moment the engine decides a node is out of road:
  `recovery_exhausted`, `node_stagnated`, `node_retry_refused`, budget stop
- One open record per `(run, node)`: repeated failure updates the row; failure
  after replay is a new row
- Replay applies a genuine `retry_node` patch through the engine's own apply
  path — OCC check, patch validator, journalling all apply
- Refused replay (run not resident, rejected patch) leaves the record **open**

### 6.3 Connectivity Waits

- `CONNECTIVITY_WAIT` node kind makes `WAITING_CONNECTIVITY` reachable
- Probe is host-installed (`engine.connectivity_probe = ...`), absent by
  default — unprobed node **fails with the real reason**
- Three resolutions: probe measures reachable (success), release signal
  (success, "asserted not measured"), deadline sweep (failure)
- Sweep is kind-aware: connectivity waits get a fresh probe measurement before
  deadline check

### 6.4 Goal-Drift Detection

- Measures direction at the single success seam
- Terms from declared goal by published stopword rule; each completed node's
  output scored as term overlap; verdict is trailing mean over last 3 measurable
  samples
- Unmeasurable output scores `None` and is excluded
- Below 3 measurable nodes: `insufficient_evidence` with real count
- **Proposal only**: journals `goal_drift_detected`, writes `run.metrics`,
  never mutates run/node/graph

### 6.5 Worktree Isolation

- Node declaring `config.worktree` gets an isolated git checkout
- Claim store enforces: exclusive (one ACTIVE claim per path), confined
  (sanitized path under engine root), host-allowlisted (`repo_root` validated
  against `engine.worktree_repo_roots`), measured (records head commit)
- Provisioning and removal are real, bounded, argv-only git calls
- Failed removal leaves claim ACTIVE with real git error

## 7. Orchestrator Layer

### ExecutionKernel

- Drives one `DynamicWorkflowEngine` through claim → dispatch → handoff
- Adds policy the engine does not own: per-run mutual exclusion, executor
  resolution from live registry, fail-closed guards, cross-mode handoff
- `run_turn()` module-level seam: paradigm → mode-mapped graph → execute →
  validate → handoff

### ExecutorRegistry

- Thread-safe registry binding executor names to real callables
- `DIGEST_EXECUTOR` (`alpha.local.digest`) — built-in SHA-256 projection
- `COMPENSATION_EXECUTOR` (`alpha.local.compensation`) — saga callback
- `VOTE_EXECUTOR` (`alpha.local.vote`) — quorum vote (no default binding)
- `bind_default_executors()` — idempotent P1 production binding (digest only)
- `bind_domain_executors()` — explicit opt-in for real model/tool/subagent

### Domain Executors

- `alpha.local.model` — real chat model via `create_chat_model`
- `alpha.local.tool` — real builtin tool via `ScriptDispatcher` + guardrails
- `alpha.local.subagent` — real subagent via `SubagentExecutor`
- Async-to-sync bridge on dedicated worker loop; refuses when called from a
  thread with a running event loop

### Mode Mapper

- 7 paradigms mapped: `direct_agent`, `subagent`, `bot_profile`, `moa`,
  `deep_research`, `deep_think` are expressible
- **Swarm is NOT expressible** — the DWE graph model has no dynamic
  swarm-topology construct (member join/leave, supervisor hierarchy, cost
  ledger land with P7); a static MAP fan-out would misrepresent a swarm

### DynamicWorkflowService

- Composes the full perceive → discover → decompose → assemble → bridge →
  execute pipeline
- `discover()` reads five production registries, discloses failures
- `plan()` perceives, discovers, decomposes, assembles, compiles
- `execute()` compiles and optionally executes through shared kernel

## 8. Honesty Contract

Every module enforces:

- **Missing prerequisites are failures** carrying the real reason — never
  synthesized success
- **`tokens_used`** is the provider's reported usage or `0` — never estimated
- **`evidence`** names what was actually invoked
- **Unbound executors** produce honest failures naming the missing piece
- **`acceptance_passed`** is `False` for digest projections — never claims
  domain acceptance
- **Simulations** are never reported as execution
- **Forks** never mutate their source
- **A completed run is never a verified run**
- **A suggestion is a proposal, never an action**

## 9. Known Gaps & Advanced Improvement Opportunities

### 9.1 Patch Engine Completeness (Medium effort, high value)

**Current state:** 6 of 19 declared patch operations are unsupported in the
core engine (`fan_out`, `fan_in`, `set_loop_limit`, `skip_node`,
`request_human`, `request_review`), and `update_edge_condition` is a deferred
no-op.

**Improvement:** Implement all 19 operations in the core patch engine.

- `fan_out` / `fan_in` — clone a node's subgraph to N targets / merge N
  sources into one; the graph model already supports the edge semantics
- `set_loop_limit` — update `loop_policy.max_iterations` on an existing node;
  the validator already protects `loop_policy` from arbitrary mutation
- `skip_node` — mark a node SKIPPED without executing it; the scheduler
  already treats SKIPPED as a valid terminal node state
- `request_human` / `request_review` — create an approval gate node; the
  APPROVAL node kind and `resolve_approval` already exist
- `update_edge_condition` — apply the condition mutation in the core engine
  rather than deferring to the DAG tool call site

**Value:** Eliminates the "unsupported patch operation" refusal path, makes
the patch engine self-contained, and removes the DAG tool's special-case edge
mutation.

### 9.2 Swarm Paradigm Expressibility (High effort, high value)

**Current state:** Swarm is refused with an honest reason: the DWE graph model
has no dynamic swarm-topology construct.

**Improvement:** Add a `SWARM` node kind that maps to a dynamic fan-out with
member join/leave semantics.

- A `SWARM` node would declare a member set (bots/subagents) and a topology
  (hierarchical, flat, pipeline)
- The engine would dispatch members as a bounded wave, track membership
  dynamically, and aggregate results through a supervisor node
- The cost ledger (tokens per member) would be journalled as `node_timed`
  events per member
- This closes the last non-expressible paradigm and makes the DWE a complete
  orchestration plane

**Value:** The DWE becomes the single orchestration plane for all paradigms;
no honest refusal needed for swarm.

### 9.3 DecisionRecords Journaling (Medium effort, medium value)

**Current state:** The engine journals no DecisionRecords or file artifacts;
handoff fields are honestly empty rather than invented.

**Improvement:** Journal decision records at each planning seam.

- `DynamicPerceptionEngine.perceive()` already produces a `PerceivedIntent`
- `DynamicDecomposer.decompose()` already produces a `DynamicGoal` with
  tasks, waves, and compensations
- These should be journalled as `decision_recorded` events with the full
  decision payload, so a replay can reconstruct not just what happened but
  **why** the planner chose that path
- The `HandoffContract` would carry real decision provenance instead of empty
  fields

**Value:** Replay becomes fully explainable — an operator can see the
perception, decomposition, and assembly decisions that produced a run.

### 9.4 Cross-Process Coordination (High effort, high value)

**Current state:** The lease store, event log, and wave concurrency are
process-local. They are atomic and restart-recoverable for ONE Gateway
process; a multi-worker deployment still needs shared lease/coordination
before claiming cross-process exactly-once execution.

**Improvement:** Add a shared coordination backend.

- The lease store already has a clean `acquire`/`release`/`reclaim` API with
  fencing tokens — this maps directly onto a shared SQL or Redis backend
- The event log's append-only JSONL with idempotency keys maps onto a
  shared append-only log (e.g., Kafka, or a simple SQL events table)
- The `worker_id` is currently a pid; a shared backend would use a
  worker UUID registered at startup
- The `ExecutionKernel` already has `recover_run()` for restart — this would
  become a cross-worker handoff

**Value:** Multi-worker deployments become safe; the DWE scales horizontally.

### 9.5 Automatic Evidence Collectors (Medium effort, high value)

**Current state:** `collect_test_exit_report` and `collect_artifact_digest`
are **readers** — they parse a test exit report or stat/hash an artifact and
return `None` when the source is missing. Nothing wires a collector into a
run automatically.

**Improvement:** Wire collectors into the verification gate.

- When a node declares `verification_cmd` and the host binds a
  `verification_executor`, the executor could automatically collect
  evidence from the run's workspace (test reports, artifact digests) before
  running the declared command
- The `EvidenceRecord` provenance contract already exists — collectors would
  produce records with `EvidenceKind`, a real `bool`, a non-empty bounded
  source, and a timestamp
- This closes the gap between "the check passed" and "the check passed
  because the evidence was collected"

**Value:** Verification becomes evidence-backed rather than
command-backed; the acceptance gate has real provenance.

### 9.6 Semantic Drift Detection (Medium effort, medium value)

**Current state:** Drift detection measures term overlap — a lexical metric.
A node that produces semantically equivalent but lexically different output
is reported as drifted.

**Improvement:** Add a semantic similarity layer.

- Use a lightweight embedding model (or a hash-based semantic fingerprint)
  to score node outputs against the declared goal
- Combine lexical overlap (current) with semantic similarity (new) into a
  composite score
- The threshold becomes tunable per workflow
- Unmeasurable output still scores `None` — the honesty contract is preserved

**Value:** Fewer false-positive drift alarms; the detector catches "the work
stopped being about the goal" even when the vocabulary changes.

### 9.7 Worktree Resource Limits (Low effort, medium value)

**Current state:** Worktree claims record the head commit and enforce
exclusivity, but do not enforce resource limits on the worktree itself.

**Improvement:** Add resource limits to worktree claims.

- Disk quota per worktree (enforced via filesystem or git object limits)
- Automatic cleanup on claim release (already partially done — failed removal
  leaves claim ACTIVE)
- Timeout on worktree provisioning (a hung `git worktree add` should not
  block the node indefinitely)

**Value:** A pathological worktree cannot consume unbounded disk or hang a
node forever.

### 9.8 Trigger Cross-Process Coordination (Medium effort, medium value)

**Current state:** The trigger store is single-Gateway. A multi-worker
deployment could have two Gateways fire the same trigger simultaneously.

**Improvement:** Add a fencing token to trigger firing.

- `record_fire` already computes the next fire time from the due time — this
  is idempotent
- A shared trigger store (or a fencing token in the existing store) would
  prevent double-firing across workers
- The `workflow_triggers` loop already runs on a single worker (autonomy
  supervisor is single-process) — this would make it safe to run on multiple

**Value:** Multi-worker trigger firing becomes safe; no double-fires.

### 9.9 Connectivity Probe Implementations (Low effort, medium value)

**Current state:** The connectivity probe is a callable the host installs,
absent by default. An unprobed node fails with the real reason.

**Improvement:** Provide built-in probe implementations.

- An HTTP probe (GET a URL, check status code)
- A TCP probe (connect to a host:port)
- A DNS probe (resolve a hostname)
- These would be registered by the host via `engine.connectivity_probe = ...`
  but would ship as ready-made callables in `alpha.workflow.connectivity`

**Value:** Hosts can enable connectivity waits without writing a probe
callable; the common cases are covered.

### 9.10 Quarantine Escalation Policies (Low effort, medium value)

**Current state:** Quarantine records are written and listed, but there is no
automatic escalation — a record that stays open too long is just... open.

**Improvement:** Add escalation policies.

- A record open longer than a configurable threshold emits a
  `quarantine_escalated` event
- The escalation could notify the owner (via the existing notification
  system) or trigger an automatic replay if the failure class is retryable
- The `StagnationDetector` already measures non-progress — this extends that
  to the quarantine queue

**Value:** The dead-letter queue becomes a live work queue rather than a
passive list; records don't get forgotten.

## 10. Improvement Priority Matrix

| Improvement | Effort | Value | Dependencies | Risk | Status |
| --- | --- | --- | --- | --- | --- |
| Patch engine completeness | Medium | High | None | Low — additive operations | **Implemented** |
| Swarm paradigm | High | High | New node kind, supervisor | Medium — new semantics | Not implemented (architectural) |
| DecisionRecords journaling | Medium | Medium | Event schema | Low — additive events | **Implemented** |
| Cross-process coordination | High | High | Shared backend | High — changes consistency model | Not implemented (architectural) |
| Automatic evidence collectors | Medium | High | Verification executor | Medium — new wiring | **Implemented** |
| Semantic drift detection | Medium | Medium | Embedding model | Low — additive scoring | **Implemented** (char n-gram, model-free) |
| Worktree resource limits | Low | Medium | None | Low — additive limits | **Implemented** |
| Trigger cross-process fencing | Medium | Medium | Shared store | Medium — changes firing | **Implemented** (CAS token) |
| Connectivity probe implementations | Low | Medium | None | Low — additive callables | **Implemented** |
| Quarantine escalation policies | Low | Medium | Notification system | Low — additive events | **Implemented** |

**8 of 10 implemented.** The two remaining items (swarm paradigm, cross-process
coordination) are architectural changes requiring a shared SQL backend or a new
node kind with supervisor semantics — each needs a dedicated design pass rather
than an incremental patch.

## 11. Recommended Implementation Order

1. **Patch engine completeness** — closes the "unsupported operation" refusal
   path, makes the patch engine self-contained, no new dependencies ✅
2. **Connectivity probe implementations** — low effort, makes the connectivity
   wait feature usable without custom host code ✅
3. **Quarantine escalation policies** — low effort, makes the dead-letter
   queue a live work queue ✅
4. **Worktree resource limits** — low effort, prevents pathological cases ✅
5. **DecisionRecords journaling** — medium effort, makes replay explainable ✅
6. **Automatic evidence collectors** — medium effort, makes verification
   evidence-backed ✅
7. **Semantic drift detection** — medium effort, reduces false positives ✅
8. **Trigger cross-process fencing** — medium effort, enables multi-worker ✅
9. **Swarm paradigm** — high effort, closes the last non-expressible paradigm
10. **Cross-process coordination** — high effort, enables horizontal scaling

Each improvement preserves the honesty contract: missing prerequisites are
failures carrying the real reason, and nothing is fabricated when a
prerequisite is missing.
