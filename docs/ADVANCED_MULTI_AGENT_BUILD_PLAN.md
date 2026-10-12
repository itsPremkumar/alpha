# Alpha Advanced Multi-Agent — Build Plan (What Is Worth Building, In What Order)

**Document type:** Gap analysis and engineering plan derived from
`docs/ALPHA_ADVANCED_MULTI_AGENT_ARCHITECTURE_PLAN.md`
**Source plan date:** 11 October 2026 · **This audit date:** 11 October 2026
**Audited tree:** `main` @ `05d9041` (worktree `feat/advanced-multi-agent-orchestration`)
**Status:** This is an audit-backed plan, not a claim that anything below is built.

---

## 0. How to read this document

The source plan is a 1,747-line research-backed blueprint for a "hybrid,
graph-governed, event-driven multi-agent runtime". Read cold, it reads like
greenfield guidance: canonical task contracts, a DAG scheduler, a capability
registry, a typed event bus, an artifact blackboard, layered verification,
durable run state, bounded budgets, a run-observability UI.

**Alpha already contains most of that substance, and in several places exceeds
it.** The gap is not "build the plan". The gap is:

1. **Wire** the two large subsystems that exist with tests but have no caller.
2. **Unify** the six independent schedulers/graph models that grew separately.
3. **Close** a short list of specific honesty gaps that the plan names and the
   repository's own `AGENTS.md` files already admit.

This document states what exists (with paths), what is missing, and the order I
recommend building it. Where the source plan and this audit disagree, this
audit follows the tree, and says so.

**Method.** Three read-only passes over the tree: the agent runtime and every
orchestration engine; the evidence/telemetry/durability/isolation/budget plane;
and a direct inspection of the graph plane, GRO, capability mesh, dynamic
workflow engine and worktree manager. Every claim below cites a file path.
Anything marked **[inference]** is derived from grep scope, not a read of a
contract.

---

## 1. The one-paragraph verdict

Alpha has ~121 engine packages, 2,030 Python files, 537,752 lines, 138 tools,
70 routers, 46 middlewares and 13 supervisor loops under
`backend/packages/harness/alpha/`. Multi-agent orchestration is not the missing
piece — **it is the over-supplied piece.** Seven schedulers exist; the graph
plane (`alpha.graph`) can plan and validate a task DAG but its execution adapter
reports `blocked`; the capability mesh (`alpha.capability_mesh`, 17 modules,
7,545 lines) has **zero importers outside its tests**; the verification loop
(`alpha.verification`) has **no production caller**; `WorktreeManager` is
battle-tested and never called by the chat run path; and there is no monetary
cost ceiling anywhere in run admission. The highest-value work in this
repository is therefore **consolidation and wiring, not construction**.

---

## 2. Structural problem: seven schedulers, one task vocabulary, none shared

The source plan's own §31 ("What Not to Build First") lists *"multiple
overlapping orchestration frameworks running the same task"*. That is the
current state. All of the following are live in `main`:

| # | Scheduler | Location | Status |
|---|---|---|---|
| 1 | **Dynamic Workflow Engine (DWE)** | `packages/harness/alpha/workflow/runtime.py` | Wired; richest. Node kinds incl. `SWARM`, `MAP`/`REDUCE`/`RACE`/`QUORUM`, `CHECKPOINT`, `GOAL_GATE`, `HANDOFF`, `WAIT`, `EVENT_WAIT`, `PARALLEL`, `SUBWORKFLOW`. Leases with cross-process `FileLock`, orphan reconciliation, time-travel fork, plan diff, templates, self-improvement proposals. |
| 2 | **ExecutionKernel** | `packages/harness/alpha/orchestrator/loop.py` | Per-run claim → dispatch over the *real* DWE via `DynamicWorkflowBridge` (`workflow/dynamic_bridge.py:95`). |
| 3 | **Swarm v2** | `packages/harness/alpha/swarm/` (25 files) | `SwarmCoordinator` + `SwarmScheduler` + `AsyncSwarmRunner`. Lease-fenced attempts, budgets, deliberation (SPRT), stigmergy, telemetry, adaptive topology. Model-facing `swarm` tool + `/api/swarms`. |
| 4 | **Graph plane (AGE)** | `packages/harness/alpha/graph/` | Canonical models, pure DAG validator, SQLite ledger, top-level LangGraph `StateGraph`. **Flag-gated off** (`ALPHA_GRAPH_EXECUTION_ENABLED`, default `False`; mounted at `app/gateway/app.py:1161`). |
| 5 | **APEX executive** | `packages/harness/alpha/apex/executive.py` | One bounded, model-free decision cycle. Its own docstring names the problem: *"the DWE and the swarm each own their own scheduler."* |
| 6 | **Enterprise mission DAG** | `packages/harness/alpha/enterprise/` | C-suite hierarchy, mission-to-sprint DAG, token treasury. Tier `preview/partial`. |
| 7 | **Legacy DAG engine** | `packages/harness/alpha/workflow/dag_engine.py` | The DWE's own legacy layer. |

Plus `alpha/orchestration/autopilot.py::ExecutiveAutopilot`, a **regex intent
classifier returning a preset name**, standing in for strategy selection.

### 2.1 Three incompatible graph data models

`WorkflowGraph`/`WorkflowNode` (`workflow/models.py:110,118`),
`SwarmPlan`/`SwarmTaskNode` (`swarm/models.py:414,275`), and
`Goal`/`TaskNode`/`GraphEdge` (`graph/models.py`) describe the same concept with
different fields, different status vocabularies, different budget types and
different event logs. **Nothing translates between them.** This is the single
largest structural debt in the multi-agent plane and the reason a run started
in one engine cannot be observed, budgeted, resumed or verified in another.

### 2.2 Three separate budget types, only one enforced in code

`WorkflowRun.budget_limit` is a **token count only** (`workflow/models.py:261`).
`SwarmBudget` (`swarm/models.py:86`) is a task-level token/tool budget.
`GraphBudget` (`graph/models.py`) refuses bounds rather than clamping them.
APEX has the only real multi-dimensional ceiling
(`max_active_agents`, `max_parallel_tasks`, `max_delegation_depth`,
`max_tool_calls`, `max_total_tokens`, `max_runtime_minutes`).

**There is no monetary cost budget anywhere in run admission.** `CostGovernor`
(`models/cost_governor.py`) is an accounting and circuit-breaker view; the DWE
states plainly that dynamic workflows *"lack … hard per-run budget enforcement
for paid or side-effecting work"*. `model_pricing` in `config.yaml` feeds the
console reporting route, not an admission gate. The source plan's
`execution_budget_mode: zero_cost` **does not exist in code**.

---

## 3. Gap register — what is worth building

Ranked by (value × certainty) ÷ (effort × risk). Every "current state" citation
is a file path in the audited tree.

### Tier 1 — build first

| ID | Gap | Plan ref | Current state | Value | Effort | Risk |
|---|---|---|---|---|---|---|
| **G1** | **Graph-plane execution worker.** `LeadAgentAdapter.execute_task` has no real worker and reports `blocked` by design, so the graph plane can plan but not execute. | §6.1, Phase 5 | `packages/harness/alpha/graph/adapter.py`; `graph/AGENTS.md` invariant 14: *"An unbound worker reports `blocked`, never `succeeded`."* | Very high | Medium | Low |
| **G2** | **One task-contract schema + adapters across DWE / swarm / graph.** | §5.2, §23, Phase 1 | Three graph models, no translation layer. | Very high | High | Medium |
| **G3** | **Capability routing wired to measured data.** `fit_score` / `route()` are pure functions no dispatch decision consumes. | §7.1–7.3, Phase 3 | `capability_mesh/fit.py`; its own `AGENTS.md`: *"`route()` is a tested pure function, not a dispatch decision anything makes yet."* **Zero importers outside tests.** | High | Medium | Low |
| **G4** | **Verification loop production caller + automatic evidence collectors.** The acceptance gate is excellent but hands-fed. | §13, Phase 4 | `alpha.verification` (`integration.py:94` seam returns `None` with no controller); `mission/acceptance.py` collectors *"read, never execute"*. | Very high | Medium | Low |
| **G5** | **Worktree isolation in the chat/coding run path.** | §14.2, Phase 7 | `sandbox/worktrees.py::WorktreeManager` — full CRUD, merge simulation, adoption-after-restart, containment checks. `runtime/runs/worker.py` has **one** `worktree` match, a comment at `:921`. Used only by `alpha.projects`, `alpha.rsi`, `alpha.swarm`. | High | Medium | Medium |
| **G6** | **Cost ceiling at run admission + `zero_cost` mode enforced in code.** | §19.3, §29 | Absent. `CostGovernor` is accounting, not admission. | High | Medium | Low |

### Tier 2 — build next

| ID | Gap | Plan ref | Current state | Value | Effort |
|---|---|---|---|---|---|
| **G7** | **Interactive approval flow for `ask` governance.** Today `ask` is fail-closed with no human in the loop. | §18.3 | `guardrails/governance.py:28-31`: *"an interactive human-approval flow is the follow-up; until it exists, ask is fail-closed."* | High | Med-high |
| **G8** | **Side-effect ledger coverage.** 1 of 9 irreversible effect families announces. | §16.3 | `test_side_effect_unrecorded_ratchet.py:106-155` pins the count at 1 and bounds the unrecorded set at ≤8. | High | Medium |
| **G9** | **One run event stream across engines.** | §21 | Frozen contract exists (`contracts/run_event_stream_contract.json`) but covers lead runs only; DWE/swarm/graph each journal separately. | Med-high | Medium |
| **G10** | **Live fleet push** for dashboard panels (currently fetch-on-mount). | §22.3 | `AgentStatusPanel.tsx:73-75` refresh-on-mount; `RunsSection.tsx:173` a single `useEffect`. | Medium | Low-med |
| **G11** | **Context-exhaustion ladder execution.** Declared, not called. | §17 | `runtime/AGENTS.md`: *"no production site calls `plan_context_recovery`."* | Medium | Low |
| **G12** | **Content-addressed artifact store with schema validation.** | §9.2 | Artifacts are path strings (`SwarmTaskNode.output_artifacts`) or ledger rows (`graph.Artifact`). No type/schema check on handoff. | Medium | Medium |

### Tier 3 — do not build (yet)

**D1** A2A remote-agent bridge — the peer network already fills the use case;
no demonstrated need. **D2** Distributed queue or database — no scale
evidence; the plan itself says don't. **D3** Visual redesign or agent
marketplace. **D4** Autonomous plugin install/execute. **D5** Consensus as a
substitute for evidence. **D6** A single global agent-quality score. **D7**
Multi-machine workers. **D8** Automatic rewriting of the production supervisor
prompt (`alpha.workflow.self_improvement` already *proposes only* — keep it).

---

## 4. Phase plan

Nine phases, each independently mergeable and useful. Phase 0 is largely done
by this audit; it still needs to be written down and pinned by tests.

### Phase 0 — Baseline and honesty floor *(finish this first)*

**Goal:** Make "we broke Bot Mode" detectable, and record what already exists.

- Write `docs/architecture/multi-agent-baseline.md`: the table from §2 above,
  the gap register from §3, and the feature matrix
  (`exists / partial / unwired / not-implemented`) the source plan asks for.
- Pin the engine count in a test so a new scheduler cannot land silently.
  There is already `tests/test_feature_manifest_wiring.py` and
  `tests/test_no_orphan_modules.py`; add the graph-model inventory beside them.
- Add Bot Mode smoke tests: simple chat, one tool call, one two-agent
  delegation, one parallel task, one failed tool, one model timeout, one
  cancellation, one restart recovery, one coding loop.
- **Acceptance gate:** baseline report merged; a failing Bot Mode behaviour is
  a red test, not a surprise.

### Phase 1 — Task contract unification (G2)

**Goal:** One task vocabulary; every existing engine keeps its behaviour.

- Define `TaskContract`, `TaskResult`, `AgentManifest`, `RunEvent`,
  `VerificationReport`, `Budget`, `ToolContext` as versioned Pydantic models in
  a new `alpha.tasks.contracts` (do **not** relocate `graph/models.py`).
- Write three **adapters**, not three new engines:
  `WorkflowNode → TaskContract`, `SwarmTaskNode → TaskContract`,
  `graph.TaskNode → TaskContract`, plus the reverse for status writes.
- Add `contracts/task_contract.v1.json` — the JSON schema the plan asks for and
  that does not exist today.
- Map existing mode identifiers to an `ExecutionPolicy` profile while keeping
  every public identifier (`bot_mode`, `swarm`, `dynamic`) valid.
- **Do not** implement a scheduler in this phase.
- **Acceptance gate:** each engine round-trips its own models through the
  contract with no field loss; Bot Mode and subagent paths unchanged.

### Phase 2 — Graph-plane execution worker (G1) + strategy selection

**Goal:** The flag-gated graph plane can actually execute work.

- Implement `LeadAgentAdapter.execute_task` by dispatching through the existing
  `SubagentExecutor` (inheriting its admission controller, sandbox leases,
  receipts and usage accounting — the pattern `gro/SubagentExecutionAdapter`
  already uses).
- Replace `alpha.orchestration.autopilot`'s regex classifier with a
  deterministic preflight estimator over the source plan's §4 inputs — no extra
  LLM call.
- Keep `ALPHA_GRAPH_EXECUTION_ENABLED` default `False`. Enable in tests only.
- **Acceptance gate:** a graph-plane goal with three tasks and one dependency
  completes with a real worker; an unassignable task reports `blocked` with a
  reason and never `succeeded`.

### Phase 3 — Capability routing wired to measured data (G3)

**Goal:** Assignment explains itself and improves from evidence.

- Add a **run-outcome recorder**: every completed task in any engine writes a
  measured record (task class, agent version, model, outcome, latency, tokens,
  cost) into the capability mesh store.
- Feed the recorder's output into `capability_mesh.fit.score_entities` and make
  one real dispatch decision consult it — the natural first consumer is
  `swarm.team.assign_specialists`, which already does hard capability filtering
  with recorded rejections.
- Keep the cold-start rule the source plan states: undeclared history uses
  declared capabilities and test results, never a fabricated score.
- **Acceptance gate:** a routing decision logs *why* an agent was chosen, with
  score components; an agent with no history is not penalised.

### Phase 4 — Verification and evidence closure (G4)

**Goal:** Nothing completes on a fluent answer.

- Wire `alpha.verification` into the turn path: `drive_verification_from_turn`
  needs one call from the middleware owner, with a controller supplied.
- Add automatic evidence collectors that **read what a run already produced**:
  test exit reports, artifact digests, workspace-change summaries. Feed them
  into `mission.acceptance.evaluate_trusted_acceptance`.
- Extend `subagents/acceptance_checks.py`'s `checked`/`holds` vocabulary to the
  new contract so deterministic evidence never reads as acceptance.
- **Acceptance gate:** a fluent but incomplete answer fails verification in a
  test; every terminal success carries a verification report.

### Phase 5 — Durability, isolation and cost closure (G5, G6, G8, G11)

**Goal:** Parallel coding agents cannot corrupt each other; money and paid
calls are bounded in code.

- **Worktrees in the run path:** have `runtime/runs/worker.py` acquire a
  `WorktreeManager` worktree per coding task via the existing
  `projects.workspace.with_task_worktree()` lease, with an explicit
  write-scope declaration per task. Keep `run_agent()`'s default path
  unchanged unless a coding task declares a scope.
- **Cost ceiling:** add `budgets.max_cost_usd` and `budgets.mode
  (zero_cost | budgeted | unrestricted)` to `AppConfig`, enforced at run
  admission. In `zero_cost`, a paid-provider call is a **refusal in code**, not
  a prompt instruction. `free_gateways` already exists as the config surface.
- **Side-effect ledger:** extend `announce_effect` from 1 to the 9 families the
  ratchet names, one family per PR with its own test.
- **Context ladder:** give `plan_context_recovery` its one production caller.
- **Acceptance gate:** two coding tasks with overlapping write scopes cannot
  both run; a `zero_cost` run refuses a paid model; all 9 effect families
  announce; a context-overflow run escalates instead of erroring.

### Phase 6 — Observability unification and live UI (G9, G10)

**Goal:** One timeline for one run, whatever engine produced it.

- Add a run-scoped **unified event sink** that ingests the DWE, swarm and graph
  journals into the existing `run_events` contract. Prefer extending the frozen
  `contracts/run_event_stream_contract.json` over adding a second stream.
- Give the dashboard panels a live transport. The graph plane already emits
  `RunEvent` rows; reuse the existing SSE/`Last-Event-ID` machinery
  (`stream_bridge`, `sse_consumer`) rather than inventing a socket.
- Build the run view the source plan §22.1 describes (Goal / Plan / Agents /
  Activity / Artifacts / Budget / Approvals / Diagnostics / Result) on top of
  the existing `RunInspectorSection` and `WorkflowRunInspector` rather than as a
  new page.
- **Acceptance gate:** a run spanning two engines renders one timeline with no
  duplicated or dropped events; a reconnecting client resumes from its last
  sequence id.

### Phase 7 — Interactive approvals and governance (G7)

**Goal:** `ask` becomes a real gate with a preview and an audit trail.

- Build the approval request/grant/deny flow behind the existing
  `GovernanceRegistry` risk classes, reusing `alpha.mods/approvals.py`'s durable
  hold store rather than a second one.
- Approvals must be action-specific with exact action, target, data scope,
  expected consequence and a preview, and must expire when the action changes.
- Record a policy decision record per blocked/approved action.
- **Acceptance gate:** a high-risk tool call parks with a preview and cannot
  proceed without an explicit grant; the grant is journalled with rule, actor
  and reason.

### Phase 8 — Advanced collaboration, only on top of 1–7

Map–reduce research synthesising **normalised schemas**, not concatenated
prose; hierarchical supervisors; bounded group critique; conflict resolution by
evidence class (the source plan's §12.2 matrix); event-triggered helper agents
with idempotent, debounced handlers; plan-change proposals with
supervisor validation; the project task board. **Autopilot stays opt-in until
benchmarks justify it.**

### Phase 9 — Routing optimisation and governance

Benchmark set built from anonymised approved failure cases; per-agent-version
× task-class statistics; offline routing experiments against the measured
baseline; proposals for prompt/tool/workflow changes that require tests,
review, staged rollout and rollback. Optional provider-neutral OpenTelemetry
export.

---

## 5. First ten engineering tickets

Concrete, one PR each, mapped to this repository:

1. **Baseline document + scheduler-inventory ratchet test** (Phase 0).
2. **`contracts/task_contract.v1.json`** + `alpha.tasks.contracts` Pydantic
   models (Phase 1).
3. **DWE ↔ contract adapter**, with round-trip tests (Phase 1).
4. **Swarm ↔ contract adapter** (Phase 1).
5. **Graph-plane ↔ contract adapter**, plus `ExecutionPolicy` mapping that
   keeps `bot_mode` valid (Phase 1).
6. **`LeadAgentAdapter.execute_task` real worker** over `SubagentExecutor`
   (Phase 2).
7. **Deterministic preflight strategy estimator** replacing the regex
   classifier (Phase 2).
8. **Run-outcome recorder → capability mesh → `assign_specialists` routing**
   (Phase 3).
9. **Verification loop caller + automatic evidence readers** (Phase 4).
10. **`budgets.mode` + `max_cost_usd` enforced at run admission** (Phase 5).

---

## 6. Definition of done

Adopted from the source plan §30, tightened against what this repository can
actually prove:

- Bot Mode and every currently working feature pass regression tests.
- All modes share one task contract, one event contract, one budget type and
  one verification gate — verified by the Phase 1 round-trip tests, not by
  assertion.
- Plans are validated graphs with dependencies, acceptance criteria and
  bounded replanning; a cycle is refused with its path.
- Assignment is capability-based and explainable; score components are logged.
- Parallel tasks cannot race on a shared write target without a lock, a scope
  declaration or a worktree.
- Inter-agent data moves as validated contracts and content-addressed
  artifacts.
- Task/run state is durable and restart recovery works for the supported
  failure classes.
- Every critical action crosses the tool/permission gateway.
- Successful completion requires the required checks to pass **and** evidence
  to be attached.
- The user can see the real execution path, blockers, diagnostics, budget and
  verification result.
- **No paid API can be called in `zero_cost` mode** — enforced in code, with a
  test that attempts the call.
- Benchmarking demonstrates the change's value against Alpha's own measured
  baseline.
- All autonomy and self-improvement changes are versioned, testable and
  reversible.

---

## 7. Invariants this plan must not break

Each of these is stated in the repository's own `AGENTS.md` files as
load-bearing. Violating one is a review failure regardless of test coverage.

- **`RunManager` is the sole run-lifecycle owner;** the graph plane projects
  goals onto run records and never calls `create()`/`cancel()` directly.
- **`SafeRunRecoveryService` is the only safe-continuation authority** — never
  a second execution path.
- **`AutonomySupervisor` is the sole background-loop owner;** declare loops in
  `register_default_loops()`, gate them under `config.yaml -> autonomy.loops`.
- **`alpha.errors.registry` is the sole owner of error codes.**
- **Harness never imports `app.*`** (`tests/test_harness_boundary.py`).
- **`config.yaml` is the only model file.** No second model file, no
  `$ALPHA_MODELS_CONFIG_PATH`.
- **Honesty rules:** an unreadable source reports `count: null`, never `0`; a
  completed run is never "verified"; single-process JSON/JSONL state is never
  cross-process exactly-once; the digest executor is always a local graph
  projection.
- **Bounds are refused, never clamped.**
- **Route order is load-bearing** — declare `/tree`, `/summary`, `/rules/preview`
  and friends before the `/{name}` catch-alls.
- **Feature counts are generated, never hand-typed.** A registry change means
  regenerating `contracts/feature_manifest.json` **and** updating `README.md`,
  `llms.txt`, `llms-full.txt`, `docs/FAQ.md` and `docs/COMPARISON.md` in the
  same change set.
- **Documentation travels with code:** `README.md` for user-facing changes, the
  nearest `AGENTS.md` for architecture changes, and a new `docs/` file needs a
  `FILE_OVERRIDES` entry in `scripts/generate_docs_index.py` (never a hand
  edit of `docs/INDEX.md`).
- **TDD is mandatory** — every feature or fix ships with tests in
  `backend/tests/`; run `make test` and `make test-blocking-io`.
- **The task path is single-level** (nested delegation disabled in every
  child). Changing that is a separate, deliberate decision.

---

## 8. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Adding a ninth scheduler instead of unifying | **High** | Phase 1 adapters are the gate; no new scheduler PR merges without one |
| Graph plane enabled by default before the worker exists | Medium | Keep `ALPHA_GRAPH_EXECUTION_ENABLED=False`; Phase 2 turns it on in tests only |
| Capability routing scores fabricated from no data | Medium | Phase 3 cold-start rule + minimum sample count before any score is trusted |
| Worktree isolation leaks branches/worktrees | Medium | `WorktreeManager._adopt_existing_worktrees` already handles restart; reuse the lease, don't re-create |
| Cost ceiling blocks local/free workflows | Medium | `zero_cost` semantics defined against `free_gateways`; test with a free-only config |
| Approval flow stalls unattended runs | Medium | Expiry, a default-deny timeout, and an audit trail from day one |
| Event unification rewrites the frozen contract | Medium | Extend `run_event_stream_contract.json`; never fork it |

---

## 9. What "success" looks like in one sentence

Alpha gets one task contract, seven engines that already exist adapt to it
instead of competing with it, the two unwired subsystems (graph-plane worker,
capability mesh) gain real callers, the acceptance gate gets automatic evidence
rather than manual feeding, and paid calls and parallel writes become bounded
in code — measured against Alpha's own baseline rather than against a promise.
