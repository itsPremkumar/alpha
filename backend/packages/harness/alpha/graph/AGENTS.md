# `alpha.graph` — the graph plane

Alpha Graph Engineering (AGE): a canonical task vocabulary, a deterministic DAG
validator, a SQLite-backed ledger, and a top-level LangGraph execution graph.
This package is **additive** — it does not replace RunManager,
DynamicWorkflowEngine, or the lead agent.

## What this package owns

* **The canonical data model** (`models.py`): `Goal`, `TaskNode`, `GraphEdge`,
  `TaskAttempt`, `Artifact`, `EvidenceClaim`, `RunEvent`, `ApprovalRequest`,
  `CapabilityRequirement`, `GraphBudget`, `GraphSnapshot`. These are the
  vocabulary every graph-plane subsystem shares.
* **The DAG validator** (`dag.py`): cycle detection, readiness, blocked-state
  propagation, topological waves, critical path. Pure functions, no I/O.
* **The ledger** (`ledger.py`): the authoritative SQLite store for goals, tasks,
  attempts, artifacts, claims, events, and approvals. Every status transition
  goes through `TaskLedger.transition_task`, which re-checks the closed
  transition table.
* **The state schema** (`state.py`): `AlphaGraphState`, a `TypedDict` with
  explicit reducers. State is a projection, not the store.
* **The execution graph** (`runtime.py`): the top-level `StateGraph` that
  routes a request through normalize → plan → validate → schedule → dispatch →
  capture → verify → repair → final-verify → report.
* **The adapters** (`adapter.py`): seams to RunManager, DynamicWorkflowEngine,
  and the lead agent.
* **The specialist-team binding** (`adapter.py::SpecialistTeamAdapter`): the
  graph plane's *multi-agent* coordination, which is deliberately **not** a
  second agent registry. It projects graph-plane tasks onto the existing swarm
  runtime's team composition — `alpha.swarm.team.assign_specialists` is the
  capability filter (a hard filter through `alpha.capabilities.eligibility`,
  with every rejected candidate and reason recorded), and
  `alpha.swarm.team.build_team_report` is the report. `TEAM_RUNTIME.md` states
  the rule: "A team is not a new lifecycle, a new plan type, or a new run
  record. It is an ordinary SwarmPlan whose task nodes carry a declared
  capability requirement and an assignment to a named specialist."
* **The failure bridge** (`failures.py`): maps graph failures onto
  `alpha.errors.registry` codes. Never invents codes.
* **The redaction policy** (`redact.py`): selects which secret families fire
  for which output surface.

## What this package does NOT own

* **Run lifecycle**: `RunManager` (`alpha.runtime.runs.manager`) is the sole
  owner. The graph plane projects goals onto run records via
  `RunManagerAdapter`; it never calls `RunManager.create()` or
  `RunManager.cancel()` directly.
* **Wave scheduling, leases, approval waits in the DWE**:
  `DynamicWorkflowEngine` owns those. The graph plane can project tasks onto
  the DWE via `DynamicWorkflowEngineAdapter`, but the DWE's invariants (lease
  fencing, OCC, replay) are the DWE's, not the graph plane's.
* **The conversational surface**: the lead agent owns chat, tool calls, and
  clarification. The graph plane owns the task contract.
* **Error codes**: `alpha.errors.registry` is the sole owner. The graph plane
  maps its failures onto existing codes and only proposes new ones when a
  failure genuinely has no home.
* **Background loops**: `AutonomySupervisor` is the sole owner. The graph
  plane registers no loops.
* **Migrations**: `alpha.persistence.migrations` owns the SQL schema. The
  graph plane's ledger creates its own tables with `CREATE TABLE IF NOT EXISTS`
  and does not touch the existing schema.

## Load-bearing invariants

1. **A task cannot succeed unverified.** `TaskNode.mark_succeeded` requires
   every declared acceptance check to carry a verdict. `UNVERIFIED` is not a
   pass. This is `alpha.mission.acceptance`'s rule applied at the model
   boundary.
2. **Bounds are refused, not clamped.** `GraphBudget` rejects `max_concurrency=0`
   and `max_attempts=-1`. A silently-adjusted limit is a limit nobody governs
   with.
3. **Cancellation is sticky and terminal.** `TERMINAL_GOAL_STATUSES` is what
   `cancel_requested` is checked against. A watchdog cannot resurrect a
   cancelled goal.
4. **Confidence is an estimate; verification is a fact.** `EvidenceClaim`
   keeps them in different fields. Only `mark_verified` with a real
   `source_locator` promotes a claim out of `CANDIDATE`.
5. **A cycle is refused with its path.** `validate_tasks` returns the actual
   cycle so the caller can name what to break.
6. **Readiness excludes `SKIPPED`.** A skipped prerequisite produced nothing.
   `SATISFIED_TASK_STATUSES` is `{SUCCEEDED}` only.
7. **State is a projection.** The ledger is authoritative. State holds IDs and
   counters only; task models, artifact payloads, and claim provenance are
   fetched by ID.
8. **Every loop has a hard bound.** `planning_iteration` ≤ 3, `verify_cycle`
   ≤ 2, `repair_attempts` ≤ 2, per-task `retry_count` ≤ 2. When a bound is
   reached, the graph transitions to a terminal state with a reason.
9. **The ledger is process-local and restart-recoverable**, not cross-process
   exactly-once. The AGENTS.md honesty constraint applies: single-process
   SQLite state is never cross-process exactly-once.
10. **Redaction is level-gated.** `RedactionLevel.FULL` for traces/logs;
    `EDGE` for approval previews and artifact references shown to users. The
    level is a required parameter, never a default.
11. **A repair is caused by a failure, not gated on it.** `repair_task` starts
    the repair from `PENDING` with a clean attempt count and records the link
    as a `REPAIRS` edge, which `dag.SCHEDULING_EDGE_TYPES` excludes from the
    dependency map. Gating a repair on the `FAILED` task it repairs makes it
    permanently un-ready, because `SATISFIED_TASK_STATUSES` is `{SUCCEEDED}`.
12. **Every node records an event, and an event failure never kills the run.**
    `nodes._record_event` appends a redacted, causally-linked `RunEvent` and
    swallows store errors: telemetry degradation is not task failure (plan
    §19). Before this, `events` was write-never and "what actually ran" had no
    answer but the caller's word.
13. **The ledger rides in `configurable`, never in state.** State is
    checkpointed; a live SQLite store is not a checkpointee.
    `nodes._get_ledger` resolves `configurable` first and falls back to
    `state["_ledger"]` only for callers that drive nodes directly.
14. **An unbound worker reports `blocked`, never `succeeded`.**
    `LeadAgentAdapter.execute_task` has no real worker yet (plan Phase 5) and
    says so with a reason, so `verify_task` fails loudly instead of a stub
    reporting success for work that never ran.
15. **Multi-agent coordination reuses the swarm runtime; it does not fork it.**
    `SpecialistTeamAdapter` projects onto `alpha.swarm.team.assign_specialists`
    — the existing hard capability filter with recorded rejection reasons —
    rather than building a second agent registry. It persists no plan, starts
    no swarm, takes no lease, and never substitutes a generalist for a missing
    capability: an unassignable task is a named capability gap.

## Feature flag

`ALPHA_GRAPH_EXECUTION_ENABLED` (default `False`) controls whether the Gateway
routes requests through the graph plane. When disabled, the lead agent handles
everything as before. The flag is checked at the Gateway boundary, not inside
the graph plane, so the graph plane can be tested without the flag.

## Tests

`backend/tests/test_graph_engineering_*.py` covers:

* `test_graph_engineering_ids.py`: ID shape, sortability, uniqueness.
* `test_graph_engineering_models.py`: model construction, transition tables,
  acceptance verification, budget bounds, claim lifecycle.
* `test_graph_engineering_dag.py`: cycle detection, readiness, waves, critical
  path, blocked propagation.
* `test_graph_engineering_ledger.py`: CRUD, transitions, conflicts, idempotency.
* `test_graph_engineering_state.py`: reducer behavior, merge semantics.
* `test_graph_engineering_nodes.py`: node behavior with a stub ledger, plus
  the `TestDefectRegressions` class pinning the five audit defects
  (dead repair branch, write-never events, unpersisted edges, the
  fabricated-success adapter, the ledger in checkpointed state).
* `test_graph_engineering_runtime.py`: graph compilation, basic execution.
* `test_graph_engineering_failures.py`: failure classification, code mapping.
* `test_graph_engineering_redact.py`: redaction levels, secret families.

## Integration points

* **Gateway**: `app/gateway/routers/graph.py` (to be created) exposes
  `POST /api/graph/runs`, `GET /api/graph/runs/{run_id}`,
  `GET /api/graph/runs/{run_id}/tasks`, `GET /api/graph/runs/{run_id}/events`.
* **RunManager**: `RunManagerAdapter.sync_goal` is called after every goal
  transition.
* **DynamicWorkflowEngine**: `DynamicWorkflowEngineAdapter.project_tasks` is
  called when a goal opts into DWE execution.
* **Lead agent**: `LeadAgentAdapter.execute_task` is called when a task opts
  into lead-agent execution.
* **Swarm specialist team**: `SpecialistTeamAdapter` is the multi-agent
  coordination seam. It projects a goal's tasks into a `SwarmPlan`, runs the
  existing `assign_specialists` hard capability filter over it, and writes the
  chosen `assigned_worker` back onto the graph-plane tasks. Three boundaries
  are load-bearing: it persists nothing (no plan, no swarm, no lease — it
  answers "who can take this work", it is not a second scheduler), it never
  writes `state`/`acceptance_status`/`verification`, and it never substitutes a
  generalist for a missing capability — an unassignable task is reported as a
  named capability gap with the full rejection list.
* **Error registry**: `alpha.errors.registry` is the sole owner of error codes.
  New codes are added there, not in the graph plane.
* **Capabilities catalog**: `alpha.capabilities.catalog` registers the graph
  plane as an optional subsystem so the orphan-module guard passes.

## Honesty boundary

The graph plane does not claim:

* **Cross-process exactly-once execution.** The ledger is process-local.
* **Automatic goal-level acceptance.** `final_verify` reports `partial` when
  goal-level criteria cannot be evaluated automatically.
* **A unified task scheduler.** The graph plane's scheduler is deterministic
  and local; the DWE's scheduler is feature-rich and lease-based. They are
  not the same thing.
* **Replacement of the lead agent.** The graph plane is a complement, not a
  replacement.