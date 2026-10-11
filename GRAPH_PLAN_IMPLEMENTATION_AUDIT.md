# Graph Engineering Implementation Audit — Plan vs. Delivered

**Date:** 11 October 2026
**Subject:** `.worktrees/graph-engineering`, commit `078061f` — *feat(graph): add Alpha Graph Engineering plane (canonicalmodels, DAG validator, SQLite ledger, LangGraph runtime)*
**Plans audited against:**
- **Doc A** — `docs/ALPHA_GRAPH_ENGINEERING_IMPLEMENTATION_PLAN.md` (AGE, 1,578 lines)
- **Doc B** — `docs/ALPHA_MULTI_AGENT_GRAPH_ORCHESTRATION_IMPLEMENTATION_PLAN.md` (AMAGO, 1,897 lines)
- Doc B L1662 names Doc A as the design contract, so **Doc A is the tiebreaker** where they disagree.

---

## 1. Verdict

**What was asked for:** an additive graph plane behind a feature flag, with a canonical data model, a DAG validator, a durable ledger, a LangGraph execution graph, adapters to existing subsystems, observability, and an API/UI surface — shipped in phases, each with tests.

**What was delivered:** a well-engineered **library** — ~7,800 lines of new code plus 9 test files (191 tests, all passing in 298s) — implementing the *data model, validator, ledger, state schema, and execution graph* of **Doc A Phase 2**, plus a partial Phase 3.

**What was not delivered:** every phase-0/1 prerequisite that makes the plane *reachable and truthful in the product* — the feature flag, the Gateway router, event emission, a worker execution path, and the docs/inventory regeneration. Plus **Doc B (AMAGO) is entirely unimplemented**, and the implementation contains **five defects that make parts of its own documented behaviour unreachable**.

| Dimension | Delivered | Verdict |
|---|---|---|
| Canonical data model (Doc A §5) | 10 models + enums + closed transition tables | ✅ Strong |
| DAG validator (dag.py) | Kahn waves, cycle path, critical path, blocked propagation | ✅ Strong |
| SQLite ledger | 8 tables, indexes, `transition_task` re-checking, acceptance, approvals, claims, events | ✅ Strong |
| LangGraph execution graph (runtime.py) | 12 nodes, one fail-closed conditional router, bounded loop counters | ✅ Good (see defects) |
| Adapters | 3 adapters — **2 of 3 are stubs** | ⚠️ Partial |
| Event emission / observability | `append_event` exists; **no node calls it** | ❌ Missing |
| Feature flag `ALPHA_GRAPH_EXECUTION_ENABLED` | **docstring only** | ❌ Missing |
| Gateway API (`POST /api/graph/runs` …) | `app/gateway/routers/graph.py` **does not exist** | ❌ Missing |
| Worker execution (who actually does the work) | **No execution path** | ❌ Missing |
| Frontend / UI | none | ❌ Missing |
| Docs / generated artifacts | `graph/AGENTS.md` written; `ENGINE_INVENTORY.md` stale; plan phases not updated | ⚠️ Partial |
| Doc B (AMAGO): agent profiles, registry, handoffs, swarm, agent instances | none | ❌ Not started |

> **Status at 11 October 2026 (second pass).** The eight defects are fixed and
> tested (see §3, §3b, §3c); `ENGINE_INVENTORY.md` is regenerated. Still missing:
> the feature flag, the Gateway router, a worker execution path, and the
> frontend. The rows above are kept as written at audit time so the delta is
> visible rather than retroactively tidied.

---

## 2. What is implemented (and verified)

All paths below are relative to `.worktrees/graph-engineering/`.

### 2.1 `backend/packages/harness/alpha/graph/` — 10 modules, ~5,900 lines

| Module | Lines | Contents |
|---|---|---|
| `models.py` | 1,385 | `Goal`, `TaskNode`, `GraphEdge`, `TaskAttempt`, `Artifact`, `EvidenceClaim`, `RunEvent`, `ApprovalRequest`, `CapabilityRequirement`, `GraphBudget`, `GraphSnapshot`; `SATISFIED_TASK_STATUSES = {SUCCEEDED}` (L491); `TaskNode.ready_to_dispatch` (L929); `AcceptanceReport.refusal_reason` (L680) |
| `ledger.py` | 1,206 | `TaskLedger`/`TaskLedgerConfig`; 8 `CREATE TABLE IF NOT EXISTS` tables + 14 indexes; `transition_task` (L730), `begin_attempt` (L426), `park_task_for_approval` (L523), `evaluate_acceptance` (L468), `create_edge` (L835), `append_event` (L1136); default path `runtime_home()/graph_store/ledger.db` |
| `nodes.py` | 657 | 12 nodes + `NodeResult` (a `dict` subclass, so LangGraph can merge it); per-node "must not do" docstrings mirroring Doc A §6.2 |
| `runtime.py` | 274 | `build_graph()` compiles a 12-node `StateGraph`; one **fail-closed** `_route_next` over `VALID_SUCCESSORS` (L109); `GraphRuntimeConfig` refuses `max_concurrency < 1` |
| `dag.py` | 435 | `validate_tasks`, `execution_waves`, `critical_path`, `find_cycle`, `find_unknown_dependencies`, `propagate_failures`, `ready_tasks`, `select_ready_batch`, `snapshot` — pure functions, no I/O |
| `state.py` | 177 | `AlphaGraphState` (`total=False`), `append_dedupe` and `_counter` (max) reducers, `merge_graph_state` refusing unknown fields |
| `ids.py` | 173 | typed IDs + sortable `make_id`, `timestamp_of` |
| `redact.py` | 155 | `RedactionLevel.FULL`/`EDGE` as a **required** parameter, per-surface |
| `failures.py` | 116 | `GraphFailureClass`, mapped onto `alpha.errors.registry` |
| `adapter.py` | 170 | `RunManagerAdapter`, `DynamicWorkflowEngineAdapter`, `LeadAgentAdapter` |

### 2.2 Design decisions that are genuinely good

1. **Fail-closed routing** (`runtime.py:109-121`): an unmapped `next_node` raises instead of falling back to `END`, because "a default would silently drop a run whose node named a successor that does not exist — the exact false-success shape this plane exists to prevent."
2. **One conditional router instead of eleven static edges** — the docstring records the bug it fixed: static edges made `next_node` dead, producing an infinite loop that only stopped at `GraphRecursionError` after ~10,000 spins.
3. **`NodeResult` subclasses `dict`** — the earlier bespoke wrapper passed every unit test and then failed at runtime with `InvalidUpdateError`.
4. **State is a projection**: IDs and counters only; large payloads stay in the ledger (Doc A §6.3).
5. **Approval before attempt, capability before readiness** (`nodes.py:386-490`) — "asking *after* the attempt would mean approving a side effect that already happened."
6. **`capture_artifact` refuses an artifact with no URI or no hash** (`nodes.py:511-517`).
7. **`final_verify` reports `PARTIAL`** when goal-level criteria cannot be measured automatically, naming what was left unmeasured.
8. **Honesty boundary** is written down (`graph/AGENTS.md` "Honesty boundary"): no cross-process exactly-once, no automatic goal-level acceptance, no unified scheduler, no replacement of the lead agent.

### 2.3 Test suite

`backend/tests/test_graph_engineering_{ids,models,dag,ledger,state,nodes,runtime,failures,redact}.py` — **191 passed in 298s** (run 11 Oct 2026 with `uv run --no-sync python -m pytest …`). This is real coverage of the model/validator/ledger/state layers, and it is the strongest part of the delivery.

---

## 3. Defects to fix (highest priority)

These are not missing features — they are places where the delivered code cannot do what its own docstrings and `graph/AGENTS.md` claim.

> **Status: all five fixed** on 11 October 2026 in `.worktrees/graph-engineering`.
> Each fix ships with a regression test in
> `backend/tests/test_graph_engineering_nodes.py::TestDefectRegressions`.
> `198 passed` for the graph-plane suite; `ruff check` and `ruff format --check`
> clean. The analysis of each defect is kept below because the reasoning is what
> stops the shape returning.

### D1 — The repair branch is structurally dead

`nodes.py:637-646` creates the repair task with `depends_on=[task.task_id]` (the **failed** task), but `models.py:491` defines `SATISFIED_TASK_STATUSES = {SUCCEEDED}` and `models.py:964-971` marks every non-succeeded dependency unmet. Therefore:

`verify_task (refusal) → repair_task → select_ready_tasks → (repair not ready) → final_verify`

The repair task is created and **never runs**. The bounded-repair loop of `graph/AGENTS.md` invariant 8 is unreachable, and Doc A golden scenario 8 ("one task fails → downstream blocked **or repair created**") only ever gets the blocked half.

**Fix:** don't use a dependency edge to express "repairs". Either drop `depends_on` and carry the original task id in `description`/a `CAUSED_BY` edge (Doc A L345 separates `DEPENDS_ON` from `CAUSED_BY` for exactly this reason), or add an explicit `repair_of` field that `ready_to_dispatch` treats as satisfied-by-failure. Add a test asserting a failed task yields a *running* repair, not a parked one.

**Fixed as:** `repair_task` no longer passes `depends_on`; it starts the repair from `PENDING` with a clean attempt count and persists the causal link as an `EdgeType.REPAIRS` edge. `dag.py` gained `SCHEDULING_EDGE_TYPES = {DEPENDS_ON, BLOCKED_BY}` and `_scheduling_edges()`, so a causal edge can never reach the dependency map — this is the fix at the layer where the confusion actually lives, not just at the one call site that happened to trip it.

### D2 — No node ever writes an event

`TaskLedger.append_event` exists (`ledger.py:1136`) and `RunEvent.create` exists (`models.py:1459`), but **no node in `nodes.py` calls either**. The `events` table is therefore never written during a run. This deletes Doc A Phase 1 (IDs + event envelope + artifact capture) and §11 (observability graph) from the delivered plane, and it removes the only evidence source a caller would have for "what actually ran".

**Fix:** emit `node.started` / `node.completed` / `node.failed`, `task.*`, `artifact.created`, `approval.required`, `checkpoint.saved`, `run.resumed` from every node through one helper that builds the `RunEvent`, appends it, and never raises into the node (an event failure must not kill the run). Pin with a test that a run leaves events behind.

**Fixed as:** `nodes._record_event()` is the single helper: it redacts the message at `RedactionLevel.FULL` before construction, carries `run_id`/`goal_id`/`task_id` from state as ids (never prose), and swallows store errors with a logged warning. Every node that changes durable state now records what it did: goal creation, `task.created`, `plan.validated`/`plan.rejected`, `task.blocked`, `task.assigned`, `artifact.created`, `verification.passed`/`verification.failed`, `retry.scheduled`/`retry.exhausted`, `goal.completed`/`goal.partial`, and the final report.

### D3 — The `edges` table is never written, and `validate_plan` casts a string into an ID type

`create_edge` is defined but never called anywhere in the package; `plan_goal` persists only the inline `depends_on`. `validate_plan` (`nodes.py:328`) then calls:

```python
edges = ledger.list_edges(graph_id=GoalId(f"task-graph:{goal.goal_id}"))  # type: ignore[arg-type]
```

which is a string formatted into a `GoalId` — the `# type: ignore` is hiding a type error, and the call always returns `[]` because nothing ever inserts an edge. So Doc A's `GraphEdge` model (15 edge types, `valid_from`/`valid_to`, `properties`, `source_artifact_id`, the "keep task-dependency edge types separate from knowledge-relationship types" rule) is currently dead code in the execution path.

**Fix:** use the existing `dag.build_graph_id(goal_id)` (which is exported for this purpose), and persist an edge per dependency in `plan_goal` so `GraphEdge` is real. Add a test that a plan's dependencies round-trip through `edges`.

**Fixed as:** `validate_plan` now calls `ledger.list_edges(graph_id=build_graph_id(goal.goal_id))` — the `GoalId(f"task-graph:{goal.goal_id}")` string cast and its `# type: ignore[arg-type]` are gone. `plan_goal` persists a `DEPENDS_ON` `GraphEdge` per declared dependency through `ledger.create_edge`, so the `edges` table is no longer write-never and `GraphEdge`'s typed relations, `reason` and provenance are real.

### D4 — `LeadAgentAdapter.execute_task` returns a fabricated success

`adapter.py:179-204` is explicitly a placeholder: *"This is a placeholder implementation… For now, the adapter returns a stub result."* It returns `{"outcome": "succeeded", …}` for a task it never ran.

This breaks three load-bearing rules at once: `graph/AGENTS.md` invariant 1 ("a task cannot succeed unverified"), Doc A §19 ("worker says done but no artifact exists → verification fails"), and Doc B L327 ("never let the same worker's self-report substitute for independent verification"). A stub that reports success is the single most dangerous shape in this codebase.

**Fix:** return an explicit non-terminal outcome (`blocked`/`needs_worker`) with a reason naming that no worker is bound, so `verify_task` fails loudly. Keep it out of the success path until Phase 5 wires a real worker.

**Fixed as:** `LeadAgentAdapter.execute_task` returns `{"outcome": "blocked", "reason": "no lead-agent worker is bound to the graph plane; bind one through GraphRuntime before dispatching (plan Phase 5)", …}`. The fabricated `succeeded` is gone, and the reason names the exact missing phase.

### D5 — `RunManagerAdapter.sync_task` is `pass`, and the ledger rides in checkpointed state

- `adapter.py:108-117` `sync_task` is an empty method with a comment, so the "attach task IDs to run metadata for the UI" claim in its docstring is not implemented.
- `runtime.py:237` injects the ledger into graph state as `_ledger` (`AlphaGraphState._ledger: Any`), and `state.py` deliberately does not annotate it with a reducer. With a checkpointer bound, LangGraph will try to serialize a `TaskLedger` object into the checkpoint. No test compiles with an actual checkpointer (the only reference is `assert config.enable_checkpoints is True` in `test_graph_engineering_runtime.py:38`), so this is an unexercised production crash path.

**Fix:** mark `_ledger` as a non-checkpointed channel / pass it via `configurable` only, and add a test that runs the graph with a real (SQLite) checkpointer and resumes from a checkpoint. Replace `sync_task`'s `pass` with the metadata write or remove the method until it is real.

**Fixed as:** `GraphRuntime.invoke` now strips `_ledger` from the state mapping and passes it only in `configurable`; `nodes._get_ledger` resolves `configurable` first and falls back to `state["_ledger"]` for callers that drive nodes directly. `RunManagerAdapter.sync_task` is a documented best-effort projection over `add_task_metadata`/`update_metadata` that logs rather than silently doing nothing.

**Still open (deliberately):** no test yet runs the graph with a *real* SQLite checkpointer and resumes from a checkpoint. That belongs with the worker seam, because a resume test needs a run long enough to interrupt.

---

## 3b. The multi-agent coordination binding (new)
The user asked that the graph plane's multi-agent half reuse **Alpha's existing bot-mode coordination system** rather than a new registry. The docs analysis confirmed that system exists and exactly what it is:

- `alpha.swarm` — `SwarmPlan`, DAG, leases, retry/backoff, measured budgets, event journal, aggregator, terminal states.
- `alpha.swarm.team` — `SpecialistProfile`, `register_roster_provider`/`resolve_roster` (the seam the `specialists:` config section feeds), `required_capability_tags`, `assign_specialists`, `TeamAssignment`, `build_team_report`.
- `alpha.capabilities.eligibility` — the shared capability vocabulary (`normalize_tags`, `match_capabilities`, `CAPABILITY_KEYWORDS`, `infer_capability_tags`, `eligible_candidates`).
- `alpha.subagents.executor` via `SpecialistSubagentWorker` — the real differentiated worker.

`docs/TEAM_RUNTIME.md` states the governing rule: *"A team is not a new lifecycle, a new plan type, or a new run record. It is an ordinary `SwarmPlan` whose task nodes carry a declared capability requirement and an assignment to a named specialist."*

**Implemented as:** `alpha.graph.adapter.SpecialistTeamAdapter` (exported from `alpha.graph`, registered in `capabilities/catalog.py` as `graph_specialist_team`), plus `tests/test_graph_engineering_adapter.py` (11 tests).

What it does:
- `describe_requirement(task)` → `(source, tags)`, preserving the **declared vs inferred vs none** distinction that `alpha.swarm.team` enforces differently (declared = strict conjunction; inferred = at-least-one hint, ranked by coverage). `capability_source` survives the projection.
- `build_plan(goal, tasks)` → a `SwarmPlan` projection carrying `objective`, `dependencies`, `capability_tags`, `capability_source`, `max_attempts`, `priority`, and `max_concurrency` from the goal's budget.
- `assign(plan)` → delegates to the existing `assign_specialists` hard capability filter.
- `apply_assignments(ledger, goal_id, assignment)` → writes the chosen `assigned_worker` back onto graph-plane tasks via `ledger.update_task`, so the **ledger stays authoritative**.
- `capability_gaps(assignment)` → the honest named list: required tags, eligible pool, every rejected candidate and why.
- `roster_status()` → whether a roster is registered at all (`no_roster_registered` disclosed, never impersonated as a team).

Three boundaries it deliberately keeps: it persists **nothing** (no plan, no swarm, no lease — it answers *"who can take this work"*, it is not a second scheduler competing with `SwarmScheduler` or the DWE); it never writes `state`/`acceptance_status`/`verification`; and it **never substitutes a generalist** for a missing capability.

This closes most of Doc B's Phase 5 (agent registry + supervisor routing + recorded selection reasons + capability-gap-instead-of-substitution) by reusing what exists, and it does so without the second ledger Doc B itself forbids (§20.1 rule 3: "Do not create a second hidden task ledger inside each subagent").

**Now wired into execution.** The adapter was initially a library capability with no caller, which is a way of shipping something that reads as done and isn't. `nodes.dispatch_worker` now resolves the specialist team before dispatching: it assigns over the *candidate* set only (assigning specialists to already-succeeded tasks would inflate their load counters and cap out assignable work), writes the chosen `assigned_worker` back through the ledger, and blocks **only** on a gap the task *declared* while a roster is registered — an inferred keyword hint is a ranking signal, not a hard conjunction (the measured failure mode recorded in `alpha.swarm.team`), and with no roster configured every task would otherwise block, which would read as a team outage rather than "no specialists are configured".

## 3c. Three further defects found while doing that

**F1 — `approval_gate` was unreachable.** `dispatch_worker` parked the task but never created an `ApprovalRequest` and never put its id into state, so `approval_gate` read an empty `approval_request_id` and raised `USER_APPROVAL_REQUIRED`: the graph could park a task and then fail instead of waiting for a human. Fixed — `dispatch_worker` now creates the approval bound by digest to the exact operation shown and returns its id.

**F2 — the ledger had no write path for an approval decision.** `create_approval` only inserts, so a pending approval could never become approved or rejected: the whole approval loop was write-blocked. Added `TaskLedger.decide_approval(...)` which delegates the legality decision to `ApprovalRequest.decide` (one decision per approval; a payload whose digest no longer matches is refused) and persists the result. Two regression tests pin both the grant and the refusal.

**F3 — `PROHIBITED` risk was dispatched, not refused.** `TaskNode.needs_approval` returns `False` for `PROHIBITED`, so a prohibited-tier task fell straight through to dispatch — the opposite of plan §13.1's "deny and record a reason". Fixed: `dispatch_worker` refuses it with `mark_task_blocked` and a reason, no attempt and no approval record.

**Docs/inventory drift fixed.** `ENGINE_INVENTORY.md` said 119 packages with no `alpha/graph` row while `AGENTS.md` had been hand-edited to "120 engines". Regenerated with `scripts/generate_engine_inventory.py`: 120 packages, 1981 files, and `| alpha/graph | 11 | 6461 | core |` — the count is generated again rather than hand-maintained.

---

## 4. Missing product surface (Doc A Phase 0/1 prerequisites)

| Gap | Evidence | Consequence |
|---|---|---|
| **Feature flag does not exist** | `ALPHA_GRAPH_EXECUTION_ENABLED` appears only in `graph/__init__.py:54` (a docstring). No reader in `alpha/config/`, no Gateway gate. | The plane can never be turned on, so it also can never be rolled back — the plan's Phase 0 exit criterion (L1036, L1086) is unmet. |
| **No Gateway router** | `backend/app/gateway/routers/graph.py` does not exist; `graph/AGENTS.md` lists it as "(to be created)". | `POST /api/graph/runs`, `GET /api/graph/runs/{run_id}`, `/tasks`, `/events` are unimplemented, so nothing can start or observe a run. |
| **No worker execution path** | `dispatch_worker` calls `ledger.begin_attempt` and routes to `capture_artifact`, which then **requires the caller to supply** `state["artifact_request"]["uri"]` and `["content_hash"]` (`nodes.py:508-517`), and `verify_task` reads caller-supplied `state["verify_checks"]` (`nodes.py:560`). | The graph cannot execute a real task end-to-end. Every non-trivial run dies at `ARTIFACT_WRITE_FAILED`. Both plans' first-milestone vertical slice (two bounded tasks → join → independent verification) is not achievable yet. |
| **No approvals surface** | `approval_gate` requires `state["approval_request_id"]` to be pre-seeded by the caller (`nodes.py:672-674`); `dispatch_worker` parks a task but never writes the new `ApprovalRequest.id` into state. | The approval gate cannot be entered from a real run, and Doc A's "approval must bind to a payload hash" rule has no representation. |
| **No cancellation write path** | `_check_cancellation` reads `state["cancel_requested"]`, but nothing in the plane sets it. | `POST /…/cancel` and "a watchdog must not resurrect a cancelled goal" are unenforceable. |
| **No frontend** | no `frontend/` changes | Doc A §11.3 / Doc B §15 UI truthfulness rules are unmet. |
| **Docs not regenerated** | `ENGINE_INVENTORY.md` still says **119 packages** and has no `alpha/graph` row, while the root `AGENTS.md` was **hand-edited to "120 engines"** — exactly the drift the generated-inventory rule exists to prevent. `make engine-inventory` must be re-run and `README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md`, `docs/COMPARISON.md` counts regenerated in the same change set. |
| **Catalog registration is partial** | `capabilities/catalog.py` registers `graph_execution`, `graph_ledger`, `graph_dag_validator`, `graph_models`, `graph_adapters` — but not `graph.nodes`, `graph.state`, `graph.ids`, `graph.redact`, `graph.failures`. | The orphan-module guard passes only because `__init__.py` imports them; if that import is ever trimmed the modules become orphans and fail the build. |

### Doc B (AMAGO) — not started

Nothing in Doc B exists: no `AgentProfile`, no `AgentRegistry`, no `agent_instances`, no handoff/hop/cycle counters, no allowlisted transition graph, no concurrency groups/resource locks, no `AgentMessage` envelope. Given Doc B's own rollout order (L1578: "Start with DAG execution and supervisor mode; enable swarm handoffs after routing/cycle tests; enable hierarchical delegation last"), **this is the correct decision** — but it should be stated explicitly in `graph/AGENTS.md` rather than left implicit, because Doc B's phases 5–7 are the ones a reader will look for next.

---

## 5. Plan conflicts the implementation should resolve rather than inherit

The two plans disagree in ways that will bite whoever continues this work. The implementation already picked a side in some places without saying so.

| Conflict | Doc A | Doc B | Recommended resolution |
|---|---|---|---|
| Run URL tree | `/api/runs/…` (L1298) | `/api/agent-runs/…` (L1191) | **A** — `/api/runs` is the existing Alpha convention; B itself says don't duplicate existing routes (L1202). |
| Approval route | `POST /api/approvals/{id}` (L1307) | nested under run (L1197) | Keep A's top-level route and echo `run_id` in the body; both plans require run-scoped auth. |
| Task status enum | `pending/ready/queued/running/**waiting_approval**/blocked/…` | adds `proposed/validated/retry_wait/**needs_approval**` | Ship A's ten values now; adopt `retry_wait` only if a real backoff state appears. Never both `waiting_approval` and `needs_approval`. |
| Error taxonomy | 23 SCREAMING_SNAKE codes | 30 lower_snake codes | The implementation correctly chose A's convention and bridged onto `alpha.errors.registry`. Keep it; add B-only concepts (`capability_gap`, `lease_expired`, `worker_lost`) as *new registry codes*, never as a second vocabulary. |
| `blocked` as a run-level final status | yes (L819) | no | Keep `blocked`; the implementation already has `GoalStatus.BLOCKED`. |
| Approval payload hash | required (L916) | B's own `approvals` table has no hash column | Add `payload_hash` (and `payload_version`) to the `approvals` table now — it is one column and it is the difference between an approval and a rubber stamp. |
| `goal_id` in every event | required (L386) | absent from B's `agent_events` | Keep `goal_id` on `RunEvent`; the implementation already indexes `events(goal_id)`. |

---

## 6. Recommended work order

Both plans converge on the same instruction: **ship the smallest truthful vertical slice first, behind a flag, and do not skip phases.** Concretely, in this order:

1. **Fix D1–D5** (the five defects above) with a regression test each. Nothing else is trustworthy until the repair loop runs, events land, edges persist, and no stub can report success.
2. **Add the flag** (`ALPHA_GRAPH_EXECUTION_ENABLED`, default off) as a real config field read at the Gateway boundary — the plane already documents that it must be checked *outside* the graph package.
3. **Add the worker seam**: one real executor bound at startup (lead-agent or subagent), invoked by `dispatch_worker`, returning `succeeded / needs_input / blocked / retryable_failure / terminal_failure` per Doc A §8.2 — never a stub. Until then, tasks must fail with "no worker bound".
4. **Add `app/gateway/routers/graph.py`** with the four documented routes, run-scoped auth, owner checks, and rate/body limits consistent with the existing routers.
5. **Wire `approval_gate`**: `dispatch_worker` writes the `ApprovalRequest.id` into state, the gate binds to a payload hash, and an unapproved park **suspends** the run instead of routing to `__end__` (see D6 below).
6. **Regenerate the inventory/manifest/docs** in one change set (`make engine-inventory`, `scripts/generate_docs_index.py`, plus README/llms/FAQ/COMPARISON counts).
7. **Only then** consider Doc B's agent registry / handoffs, and only behind its own flags and hop counters.

### D6 — two smaller issues worth fixing while you are in there

- `approval_gate` sets `next_node: "__end__"` when an approval is still pending (`nodes.py:684-690`), so the LangGraph run *terminates* while `GraphStatus.WAITING_APPROVAL` is set. A parked run and a finished run then differ only by a status string, and a caller polling `run_id` cannot tell "waiting" from "done". Park it as a suspended/checkpointed state instead.
- `LedgerConfig`'s honesty statement is right (process-local, restart-recoverable, not cross-process exactly-once), but the ledger opens with `check_same_thread=False` and no WAL/busy-timeout configuration. `alpha/runtime/store/_sqlite_utils.py` already sets `SQLITE_BUSY_TIMEOUT_MS = 30_000`; reuse it so a second reader cannot produce a spurious "database is locked".

---

## 7. What is explicitly **not** needed yet (and should stay out)

Both plans are emphatic, and the current delivery is right to have skipped these: Neo4j/Graphiti/GraphRAG/FalkorDB as mandatory dependencies, Redis/Kafka/Temporal, in-memory checkpoints for real runs, a second orchestration runtime, Deep Agents wholesale, graph visualisation as proof of implementation, or any claim of AGI/ASI/guaranteed autonomy. Doc B's agent-profile registry and handoff graph should stay behind their own flags until the Doc A slice above is green.

---

## 8. Bottom line

The **engineering core is good** — the models, the validator, the ledger, the state reducers and the fail-closed router are the hardest parts of Doc A and they are built and tested properly. What is missing is everything that makes the plane *reachable and honest in the product*: the flag, the API, the worker, the events, and the approval/cancel write paths. Fix the five defects in §3 first (they silently break the plane's own invariants), then ship the flag → worker → router → approvals slice, and regenerate the inventory/docs with it. Doc B should be recorded as a deliberate, flagged future phase rather than left as an unstarted expectation.
