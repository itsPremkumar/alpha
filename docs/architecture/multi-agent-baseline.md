# Multi-Agent Baseline — what Alpha already has, measured

**Document type:** Phase 0 feature inventory and baseline capture
**Tree:** `main` @ `05d9041` · **Captured:** 11 October 2026
**Source plan:** `docs/ALPHA_ADVANCED_MULTI_AGENT_ARCHITECTURE_PLAN.md`
**Build plan:** `docs/ADVANCED_MULTI_AGENT_BUILD_PLAN.md`

The source plan's Phase 0 says it plainly: *"this document is a web-researched
architecture proposal, not a source-code audit of every current Alpha module.
Phase 0 is mandatory because some features may already exist."*

This is that audit. Every row cites a file path. `exists` means the code is
present and reachable; it does **not** mean the capability is operated. Where
those differ, the row says so.

---

## 1. The seven schedulers

All live in `main`. This is the structural finding: the source plan's §31 warns
against *"multiple overlapping orchestration frameworks running the same task"*,
and that is the current state.

| # | Scheduler | Location | Reachable from | Owns a scheduler |
|---|---|---|---|---|
| 1 | `DynamicWorkflowEngine` | `alpha/workflow/runtime.py` | `POST /api/workflows/dynamic/*` | Yes — waves, leases, OCC, replay |
| 2 | `ExecutionKernel` | `alpha/orchestrator/loop.py` | `DynamicWorkflowBridge` over the DWE | Yes — per-run claim |
| 3 | `SwarmCoordinator` / `SwarmScheduler` | `alpha/swarm/` | `swarm` tool + `/api/swarms` | Yes — lease-fenced attempts |
| 4 | Graph plane (`alpha.graph`) | `alpha/graph/` | `/api/graph/*`, flag-gated | Yes — deterministic, local |
| 5 | APEX executive | `alpha/apex/executive.py` | `/api/apex` + `apex_tick` | Yes — bounded decision cycle |
| 6 | Enterprise mission DAG | `alpha/enterprise/` | enterprise routes | Yes |
| 7 | Legacy DAG engine | `alpha/workflow/dag_engine.py` | inside the DWE | Yes |

Plus `alpha/orchestration/autopilot.py::ExecutiveAutopilot`, a **regex intent
classifier returning a preset name**, standing in for strategy selection.

### 1.1 Three task vocabularies, none shared

| Model | Field | Vocabulary |
|---|---|---|
| `WorkflowNode` | `status: NodeStatus` | pending, ready, running, waiting, succeeded, failed, retrying, skipped, cancelled, suspended, compensating, aborted |
| `SwarmTaskNode` + `SwarmPlan` | `state: TaskNodeState` / `status: str` | 7 enum values on the task **plus** 11 bare strings on the plan (`swarm/models.py`, listed on the field and enumerated by `TERMINAL_SWARM_STATUSES` ∪ `NON_TERMINAL_SWARM_STATUSES`) — swarm's two vocabularies sit on two sibling models |
| `TaskNode` | `status: TaskStatus` | pending, ready, queued, running, waiting_approval, blocked, succeeded, failed, cancelled, skipped |

Swarm carrying two vocabularies that are not checked against each other — a
typed enum on `SwarmTaskNode`, a bare string on `SwarmPlan` — is the clearest
single symptom: the plan projection and the scheduler's task states can tell
different stories about the same run.

### 1.2 Three budget types, one enforced in code

`WorkflowRun.budget_limit` is a **token count only** (`workflow/models.py:261`).
`SwarmBudget` (`swarm/models.py:86`) is task-level token/tool. `GraphBudget`
refuses bounds rather than clamping. Only APEX has a multi-dimensional ceiling
(`apex/contract.py:152`).

**There is no monetary cost budget in run admission anywhere.** `CostGovernor`
(`models/cost_governor.py`) is accounting plus a circuit breaker. `model_pricing`
in `config.yaml` feeds `GET /api/console/*` reporting, not a gate. The source
plan's `execution_budget_mode: zero_cost` **does not exist in code**.

---

## 2. Feature matrix

### 2.1 Exists and operated

| Capability | Evidence |
|---|---|
| DAG validation (cycles, readiness, waves, critical path) | `graph/dag.py` — pure, no I/O, returns the cycle path |
| Graph ledger with closed transitions | `graph/ledger.py::TaskLedger.transition_task` |
| Typed workflow graphs, waves, bounded loops, retries | `alpha/workflow/` |
| Time-travel fork / replay | `alpha/workflow/time_travel.py` |
| Cross-process attempt leases + orphan recovery | `alpha/workflow/leases.py`, `FileLock` |
| Run lifecycle, leases, heartbeat, orphan takeover | `runtime/runs/manager.py` |
| Safe continuation from a checkpoint | `app/gateway/run_recovery.py::SafeRunRecoveryService` |
| Acceptance gate (sole terminal-`passed` producer) | `mission/acceptance.py::assert_acceptance_passed` |
| Provenance-bearing evidence records | `mission/acceptance.py::EvidenceRecord` |
| Side-effect ledger with `UNKNOWN` + reconciliation | `runtime/side_effects/ledger.py` |
| Ordered, shutdown drain | `runtime/shutdown.py` (8 phases) |
| Network monitor + parked-session registry | `runtime/network/` |
| Tool governance by risk class | `tools/governance.py::GovernanceRegistry` |
| Authority ceiling rank lattice | `bots/authority_ceiling.py` |
| Delegation depth/fanout/hop ceilings | `bots/delegation.py` |
| Git worktree manager | `sandbox/worktrees.py::WorktreeManager` |
| Merge simulation in a throwaway worktree | `sandbox/merge_simulation.py` |
| Run event stream + frozen wire contract | `contracts/run_event_stream_contract.json` |
| SSE with `Last-Event-ID` replay and `StreamGap` | `app/gateway/sse.py`, `stream_bridge/` |
| Per-run cost reporting (cache-aware) | `routers/console.py` |
| Loop detection, run-stall watchdog | `LoopDetectionMiddleware`, `runtime/selfheal/run_stall.py` |
| Group nesting with a single authority parent | `groups/scope.py` |

### 2.2 Exists but not operated — the highest-value findings

| # | Capability | Evidence | Why it is not operated |
|---|---|---|---|
| **U1** | Graph plane can plan but **not execute** | `graph/adapter.py::LeadAgentAdapter.execute_task` | No real worker; reports `blocked` by design (`graph/AGENTS.md` invariant 14) |
| **U2** | Capability mesh — 17 modules, 7,545 lines | `alpha/capability_mesh/` | **Zero importers outside its own tests.** `fit.route()` is a tested pure function nothing calls |
| **U3** | Verification loop | `alpha/verification/` | `integration.py:94` returns `None` with no controller; no production caller |
| **U4** | Automatic evidence collectors | `mission/acceptance.py` | Collectors are **readers only**; nothing wires one into a run |
| **U5** | Worktree isolation for coding tasks | `sandbox/worktrees.py` | `runtime/runs/worker.py` has one `worktree` match — a comment at `:921`. Used only by `alpha.projects`, `alpha.rsi`, `alpha.swarm` |
| **U6** | Context-exhaustion ladder | `runtime/context_exhaustion.py` | `runtime/AGENTS.md`: *"no production site calls `plan_context_recovery`"* |
| **U7** | Interactive approval for `ask` | `guardrails/governance.py:28-31` | *"an interactive human-approval flow is the follow-up; until it exists, ask is fail-closed"* |
| **U8** | Arena task-contract / coverage / evidence / synthesis | `alpha/arena/` | Engine primitives with tests, **no executor wiring** |
| **U9** | War-room auto-trigger | `groups/war_room.py` | `groups/AGENTS.md`: *"the auto-trigger is not wired into the conversation loop"* |

### 2.3 Deliberate refusals — correct as-is, not gaps

These are honesty properties, not missing features. Do not "fix" them.

| Behaviour | Evidence |
|---|---|
| A blocked task never reports `succeeded` | `graph/adapter.py` |
| Readiness excludes `SKIPPED` | `graph/models.py::SATISFIED_TASK_STATUSES = {SUCCEEDED}` |
| An approval wait cannot be un-parked by pause/resume | `apex/control.py` |
| Terminal states are unreachable over HTTP | `mission/acceptance.py` is the only writer |
| A GRO winner with no declared criteria is `UNVERIFIED` | `gro/` |
| `not_run` is journalled as `passed: false`, never as a pass | `workflow/verification.py` |
| Single-process JSON/JSONL is never called exactly-once | `harness/AGENTS.md`, APEX/enterprise/peer-network sections |
| The digest executor is always a local graph projection | `orchestrator/executors.py` |
| `compare` / `synthesize` in Arena are refused **by name** | `arena/` |

### 2.4 Partial coverage with a named ratchet

| Item | Measured |
|---|---|
| Side-effect ledger coverage | **1 of 9** irreversible effect families announces — `tests/test_side_effect_unrecorded_ratchet.py:106-155` names the other eight |
| DWE executor allowlist + per-run paid-work budget | Absent — stated in `harness/AGENTS.md` |
| `GATEWAY_WORKERS > 1` with a process-local event store | Rejected at startup (correct) |
| Subagent `task` nesting | Disabled in every child; the task path is single-level by design |

---

## 3. Phase 0 acceptance gate

| Requirement | Status |
|---|---|
| Inventory of modes, agents, tools, stores, APIs | ✅ this document |
| Trace one request UI → response | ✅ via `make dev` + the run event stream |
| Record module ownership, framework versions, persistence, test coverage | ✅ §1–§2; `ENGINE_INVENTORY.md` for the generated list |
| Export a feature matrix | ✅ §2 |
| Baseline report checked into `docs/architecture/` | ✅ this file |
| Smoke tests for existing user-facing modes | ⏳ **next ticket** — see below |

**The one gap left in Phase 0 is a Bot Mode smoke test.** Everything above is
inventory; nothing yet makes "Bot Mode broke" a *red test* rather than a
surprise.

### The smoke suite to write

`backend/tests/test_bot_mode_smoke.py`, offline and model-free:

1. simple chat → one assistant turn
2. one tool call → one receipted tool result
3. two-agent delegation → a delegation ledger entry
4. parallel tasks → both accounted for
5. failed tool → the failure is visible, not swallowed
6. model timeout → bounded retry, then an honest failure
7. cancellation → `CancelOutcome` is terminal
8. restart recovery → an orphan is reconciled
9. coding loop → a test run is recorded as evidence

---

## 4. Honesty statement

This baseline asserts **what is in the tree**, not what Alpha can do end to end.
Two examples of the difference, stated so neither is mistaken for the other:

* The graph plane has a complete canonical model and a working DAG validator.
  It has **no executor**, so it cannot run a task today.
* `assert_acceptance_passed` is the only thing that can write a terminal
  `passed`. Because no automatic collector feeds it, most runs reach completion
  with the gate **unexercised**, which is a different statement from "verified".

A capability count in this document is a count of code, not of behaviour.
