# APEX Integration Map

The Phase 0 deliverable of `docs/ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md` (§183
"Phase 0 — inventory"), produced **before** writing any APEX code.

Its purpose is to answer one question honestly: **which of the spec's 198
sections already exist in this repository, and which are genuinely missing?**
Building the 35 modules the spec's §4 lists would duplicate between twenty and
twenty-five existing owners, which §2.2 ("Compose existing capabilities"), §130
("no destructive rewrite") and §198 ("DO NOT DUPLICATE EXISTING ENGINES WHEN AN
ADAPTER WILL WORK") all forbid.

The finding is that **the spec describes an architecture this repository has
already grown, in pieces, under different names.** APEX's real job is to supply
the one layer that genuinely does not exist: an **executive control plane** that
holds an autonomy contract and runs a bounded next-action cycle over the
existing owners.

**Status note:** the tables below preserve the Phase 0 inventory as it stood
before APEX was implemented. Current execution wiring and remaining gaps are
recorded in [Operating APEX Autopilot](APEX_AUTOPILOT.md#4-the-cycle) and the
follow-up status at the end of this map.

---

## 1. The headline

| | |
|---|---|
| Spec sections already implemented | the great majority — §5, §6, §8, §9, §10, §11, §12, §14, §18–§27, §33–§38, §40–§43, §46, §47, §49–§53, §60, §63 vocabulary, §79–§81, §84, §88, §99–§103, §120–§123, §130, §135, §144–§149, §154, §156, §161, §167, §170–§172, §175, §184, §186, §187 |
| Genuinely missing | §5 **as one object**, §6 **as a profile ladder**, §63/§64 **as a loop**, §1.1's `/apex` surface, §60's `/api/apex/*` surface, §55–§59's UI projection, §188's invariants **as one checkable set** |
| Alpha's own rule for this | `backend/AGENTS.md`: `RunManager` is the sole lifecycle owner; `AutonomySupervisor` is the single owner of background loops |

So this change set is **additive and small**. It adds a control plane, not an
engine.

---

## 2. What already exists, by spec section

### Lifecycle, persistence, resume

| Spec | Existing owner |
|---|---|
| §10 Mission object, §150 lifecycle | `alpha/mission/lifecycle.py` — `MissionPhase` (7), `MISSION_TRANSITIONS`, gated terminal `PASSED` |
| §46 verification gate on completion | `alpha/mission/acceptance.py` — `AcceptanceReport`, `assert_acceptance_passed`; `alpha/runtime/runs/verification.py` |
| §49 long-running state | `alpha/missions/store.py` — `MissionStore`, JSON + `events.jsonl` journal |
| §28 leases / heartbeats | `alpha/workflow/leases.py`; `alpha/subagents/lifecycle.py` (`SubagentLease`, `renew_lease`); `alpha/swarm/scheduler.py` |
| §84 restart recovery | `app/gateway/run_recovery.py` — `SafeRunRecoveryService`; `alpha/runtime/network/wait_registry.py` |
| §50 idempotency | `alpha/runtime/resilience/idempotency.py`; `RunStore` idempotency index; `SwarmEvent.idempotency_key` |
| §40 emergency stop | `alpha/runtime/control.py` (`ControlMode.RUN/PAUSE/ESTOP` + monotonic generation fencing) + `alpha/runtime/estop.py` + `alpha/bots/kill_switch.py`, consulted together |
| §41 pause / resume | `alpha/runtime/control.py::pause`; `PlannedShutdown`; `DynamicWorkflowEngine.suspend_run/resume_run` |
| §120 checkpoints | LangGraph checkpoints via `CheckpointStateAccessor`; `SwarmCoordinator.checkpoint`; `alpha/workflow/event_log.py` |
| §82 background categories | `alpha/runtime/sessions/states.py` — 17 `SessionState` values |
| §83 offline / online | `alpha/runtime/network/monitor.py` (4 states, `UNKNOWN` real) |

### Policy, risk, safety

| Spec | Existing owner |
|---|---|
| §35 permissions (allow/ask/deny) | `alpha/guardrails/command_policy.py` (`allow`/`ask`/`deny`/`defer`); `alpha/tools/governance.py` (`AUTO`/`ASK`/`BLOCK`); `alpha/policy/engine.py` |
| §36 risk engine | `alpha/security/autonomy/classifier.py` (`ActionRiskLevel`); `alpha/tools/governance.py::RiskClass`; `alpha/mission/models.py::RiskTier` (R0–R6) |
| §37 prompt-injection defense | `alpha/safety/authority/taint.py` — 18 `UntrustedSource` values, `request_clear_from_model` always raises |
| §38 secrets | `alpha/security/vault/` — opaque `vaultref://` handles, salted fingerprints; `alpha/sandbox/env_policy.py` |
| §70 self-improvement boundary | `alpha/authority_ceiling.py::PROTECTED_COMPONENTS` (the enforcers are themselves off-limits) |
| §77 two-layer safety | `alpha/safety/authority/scopes.py` + `alpha/bots/authority_ceiling.py` (deterministic) vs. `alpha/planning`, `alpha/reasoning` (model-driven) |
| §79 filesystem policy | `alpha/sandbox/workspace_boundary.py` |
| §78 network policy | `alpha/safety/net_policy.py`, `alpha/community/url_safety.py` |
| §114 security tests | `backend/tests/test_authority_*.py`, `test_guardrails_*.py`, `test_authz_*.py` |
| §188 invariants | **partly** — see §5 below |

### Planning, execution, agents

| Spec | Existing owner |
|---|---|
| §8 unified command registry | `alpha/commands/registry.py` — `SlashCommandRegistry`; `/apex` registers here like any other command |
| §9 mode arbitration | `alpha/commands/autonomous_engine.py::LifecyclePhase`; `alpha/runtime/execution_mode.py` |
| §11 goal OS | `alpha/goals/` (`GoalContract`/`PlanVersion`/`TaskAttempt`); `alpha/workflow/dynamic_decomposer.py::DynamicGoal` |
| §12 success-criteria compiler | `alpha/mission/compiler.py`; `alpha/mission/acceptance.py` |
| §13 receding-horizon planning | `alpha/planning/autonomous.py::replan`; `alpha/workflow/replanner.py` |
| §14 workflow graph + patches | `alpha/workflow/models.py` (`WorkflowGraph`, 34 `NodeType`, 16 `PatchOperation`); `alpha/workflow/patch.py`; `alpha/workflow/time_travel.py` |
| §15–§17 agent factory / roles / bounded delegation | `alpha/bots/forge.py`; `alpha/subagents/specialists.py`; `alpha/bots/delegation.py` (`DelegationTree`, `check_depth`/`check_fanout`/`check_cycle`) |
| §18 swarm sizing | `alpha/swarm/estimator.py` + `alpha/swarm/strategy.py::resolve_strategy` + `alpha/swarm/planner.py::score_plan` |
| §19–§20 capability graph + health | `alpha/workflow/registry/` — 11 registries, one `CapabilityDescriptor` |
| §21–§23 tool routing, research, evidence | `alpha/tools/discovery/`; `alpha/research/engine.py` (`DeepResearchReport`); `alpha/evidence/store.py` |
| §24 memory scopes | `alpha/memory/` |
| §25 context engineering | `alpha/runtime/context_compaction.py`; `SubagentContextMode` (`isolated`/`snapshot`) |
| §26 model router | `alpha/models/task_router.py`; `models/workforce_router.py`; `models/performance_registry.py` |
| §27 resource scheduler | `alpha/runtime/token_meter.py`; `alpha/swarm/models.py::SwarmBudget`; `runtime/lane_scheduler.py` |
| §30 git orchestration | `alpha/swarm/worker.py::CodingWorktreeWorker`; `alpha/deepagent/workspace.py` |
| §33–§34 MCP / A2A | `alpha/mcp/` (`McpTaskService`); `app/gateway/routers/a2a.py` |
| §42–§44 failure classification & recovery | `alpha/workflow/failures.py` (`NodeFailureClass`, 19); `alpha/recovery/policies.py`; `alpha/runtime/supervisor/` |
| §51–§53 events, audit, observability | `alpha/events/bus.py`; `alpha/safety/authority/receipts.py` (hash-chained `ReceiptChain`); `alpha/observability/` |
| §54 decision ledger | `alpha/safety/authority/receipts.py::DecisionReceipt` |
| §65 scoring | `alpha/swarm/planner.py::score_plan`; `alpha/verification/budget.py` |
| §68 stuck detection | `alpha/workflow/failures.py::StagnationDetector`; `runtime/selfheal/run_stall.py` |
| §69 repeated-failure policy | `alpha/recovery/policies.py::decide_from_reason` (`max_attempts`, never infinite) |
| §94 agent reputation | `alpha/models/performance_registry.py::ModelPerformanceRegistry`; `alpha/swarm/telemetry.py` |
| §100 cancellation propagation | `alpha/mission/state_machine.py`; `DynamicWorkflowEngine.cancel_run`; `alpha/side_effects` reclaimer |
| §102–§103 orphan / zombie prevention | `DynamicWorkflowEngine.reconcile_orphaned_nodes`; `RunStallWatchdog`; `SideEffectReclaimer` |
| §108 model fallback | `alpha/models/fallback.py` — emits `FailoverEvent`, so fallback is never silent |
| §134–§136 merging, consensus, verification depth | `alpha/swarm/consensus.py`; `alpha/swarm/deliberation.py`; `alpha/verification/controller.py` |
| §139 quality-aware planning | `alpha/planning/meta_planner.py` (`ExecutionParadigm`, `model_tier`) |
| §144–§149 Protocols | `ApexCapability`-shaped behaviour already in `GuardrailProvider`, `DescriptorRegistry`, `AuthorizationProvider` |

### Control-plane / UI surfaces

| Spec | Existing owner |
|---|---|
| §132 SSE event stream | `app/gateway/routers/peer_network.py::events` (bus + `Last-Event-ID`); `routers/missions.py::events` (journal + live tail) |
| §60 API contract | `routers/missions.py`, `routers/policy.py`, `routers/workflows.py`, `routers/swarms.py` |
| §176–§177 user vs developer views | `routers/console.py`; `GET /api/ops/integration-health` |
| §181 launch gates | `alpha/benchmarks/release_gate.py`; `tests/test_release_gate.py` |
| §182 rollout | `config.yaml -> autonomy.loops.<id>.enabled`; absent id = disabled |

---

## 3. What is genuinely missing

### 3.1 An autonomy *contract* as one object (§5, §6, §7)

Today the authority surface is **five overlapping engines with three verdict
vocabularies**, and the budget surface lives in three unrelated places:

| Concern | Where it lives today |
|---|---|
| profile tiers | `alpha/security/autonomy/profiles.py` — `OBSERVER`/`ASSISTANT`/`OPERATOR`/`AUTONOMOUS` |
| capability ceiling | `alpha/bots/authority_ceiling.py` — a rank lattice, no budgets |
| tool risk | `alpha/tools/governance.py` — no budgets |
| shell verdict | `alpha/guardrails/command_policy.py` — no budgets |
| budgets | `SwarmBudget`, `AttemptBudget`, `TokenBudgetMiddleware`, `run_ownership` — four unrelated budget types |
| protected actions | `PROTECTED_COMPONENTS` (module paths) and `BASE_RULES` (globs), unrelated vocabularies |

Nothing answers *"for this mission, what may APEX do, up to how much, and what
must always be asked?"* as **one frozen, server-owned object**. That is the gap.

### 3.2 A next-action cycle (§63, §64)

`alpha/orchestration/autopilot.py::ExecutiveAutopilot` is a regex intent
classifier — it returns a preset name, and nothing consumes the result in a loop.
`alpha/mission/goalloop/` is a per-turn continuation loop gated by a judge. The
DWE and the swarm each have their own scheduler. **Nothing sequences them.**

### 3.3 The `/apex` surface (§1.1) and `/api/apex/*` (§60)

No route, no command, no UI.

### 3.4 The invariants as one checkable set (§188)

`I1`–`I12` are each *partly* enforced somewhere, but nothing states which
enforcement site is live for each one, so "APEX has twelve invariants" is not a
claim a test can check.

### 3.5 A bounded status projection (§55–§59)

The data exists across ~8 stores; no single projection answers `/apex status`.

---

## 4. The design this forces

```
alpha/apex/                    harness layer, additive
├── contract.py     AutonomyContract — the one frozen object. Composes
│                   authority_ceiling + autonomy profiles + governance risk;
│                   owns budgets and protected actions.
├── invariants.py   I1–I12 → (id, real enforcement site, live?, evidence)
├── events.py       append-only APEX event journal + live tail
├── store.py        durable session rows (atomic write, JSONL journal)
├── executive.py    the next-action cycle — deterministic, model-free
└── status.py       one bounded /apex status projection

app/gateway/routers/apex.py    /api/apex/* + SSE
app/gateway/autonomy/loops.py  apex_tick  (one adapter, one unit of work)
```

**Four rules the implementation holds to.**

1. **No second lifecycle owner.** APEX never creates, starts, stops or
   cancels a run. It reads `RunManager` and *delegates* to it. It is registered
   in `AutonomySupervisor.register_default_loops()`, not beside it.
2. **No second policy kernel.** `AutonomyContract` *narrows* and *composes*;
   it never re-implements allow/ask/deny. `alpha.policy`, `alpha.tools.governance`
   and `alpha.guardrails` remain the decision sites, and the contract records
   which one answered.
3. **No false completion.** APEX's terminal `COMPLETED` routes through
   `alpha.mission.acceptance.assert_acceptance_passed`, exactly as
   `MissionLifecycle.request_pass` already does. A mission with no evaluated
   criterion cannot close.
4. **No unverifiable claim.** Every status field is either measured or
   `null` with a machine-readable `reason`. A capability nobody probed reports
   `health="unverified"`, matching the self-inventory rule.

---

## 5. Invariant → enforcement site

The point of `alpha/apex/invariants.py` is that each row names the **module
that actually enforces it**, so the invariant set is auditable rather than
aspirational. A row whose site is absent reports `live=False` with the reason;
it does not report a pass.

| # | Invariant | Enforcement site |
|---|---|---|
| I1 | Every action belongs to a mission/task | `alpha/workflow/leases.py::LeaseManager` + `SwarmEvent.task_id` |
| I2 | Every side effect has a policy decision | `alpha/tools/governance.py::GovernanceRegistry.evaluate` |
| I3 | Every background worker has a lease | `alpha/subagents/lifecycle.py::SubagentLifecycleManager.renew_lease` |
| I4 | Every complex mission has a durable checkpoint | `alpha/missions/store.py::MissionStore._load` + `CheckpointStateAccessor` |
| I5 | Every failure creates an observable record | `alpha/workflow/failures.py::classify_node_failure` |
| I6 | Every completion requires verification | `alpha/mission/acceptance.py::assert_acceptance_passed` |
| I7 | Parent cancellation propagates | `alpha/runtime/side_effects/ledger.py::SideEffectReclaimer` |
| I8 | A denied action cannot be re-enabled by the model | `alpha/safety/authority/taint.py::TaintTurn.request_clear_from_model` (always raises) |
| I9 | APEX cannot disable the emergency stop | `alpha/runtime/control.py::read_state` (unreadable ⇒ `ESTOP`) + `AutonomySupervisor._fleet_admits_tick` |
| I10 | Global policy outranks mission preferences | `alpha/safety/authority/scopes.py::compose_scopes` ("strictest-union") |
| I11 | Retrying must be idempotent or state-checked | `alpha/runtime/resilience/idempotency.py`; side-effect `reclaim_expired` ⇒ `UNKNOWN` |
| I12 | Untrusted content cannot become authority | `alpha/safety/authority/taint.py::TaintTurn.assert_clean` |

---

## 6. Deliberately not built

| Spec | Why not |
|---|---|
| §15 automatic agent *generation* at runtime | `alpha/bots/forge.py` is a transactional builder with a smoke test. Runtime generation would need a new trust boundary; the agent factory here **composes** existing bot/subagent profiles. |
| §72 dynamic tool/plugin creation | Needs an executor allowlist plus integrated approval and hard per-run budget enforcement for paid or side-effecting work — `docs/DYNAMIC_WORKFLOWS.md` records those as the gate for auto-binding. Not wired here either. |
| §96 benchmark harness | `backend/scripts/benchmark/` already owns reproducible measurement; adding a parallel harness would fork it. |
| §109 model council | `alpha/governance/council/` and `alpha/deliberation/` exist and are reachable on demand. |
| §113 simulation mode | `alpha/workflow/time_travel.py::simulate_run` already dry-runs on a throwaway engine. |
| §164 runtime upgrades mid-mission | `alpha/evolution/update_engine.py` owns it, deliberately a separate trust boundary. |

---

## 7. Honesty statement

APEX as implemented here is a **control plane**, and this document is its
boundary. Concretely:

- APEX **plans and delegates**. It does not execute tool calls itself; the
  existing owners do, under their own policy.
- APEX **never marks a mission `COMPLETED`** without an acceptance report in
  which every criterion was evaluated and held.
- APEX's **budgets are ceilings**, not predictions, and the projection reports
  usage as measured or `null`.
- The background loop is **off unless `config.yaml` enables it**, exactly like
  the other nine loops, and it is registered through the one supervisor.
- `health` is `unverified` for every capability APEX lists, because nothing in
  this plane probes what it lists.

## 8. Current Gateway execution status

The original inventory above remains historical. The current Gateway wires
`apex_execution_tick()` as the APEX supervisor loop and exposes
`POST /api/apex/sessions/{id}/dispatch` for an operator-driven pass. Both use
the shared `start_run()` service and `RunManager`; neither adds a second run
lifecycle. The session records a dispatch generation, linked run id/status, and
deduplicated measured token totals. `RunManager` terminal status is observed,
but not treated as proof that the objective passed.

The APEX session now has an owner-scoped acceptance-report control, but Alpha
still has no automatic test/artifact/HTTP evidence collectors. Safe run recovery
continues only validated model/agent checkpoint nodes and compare-and-set links
the recovered RunManager id back to the APEX session, including restart recovery
after admission-before-link crashes; ambiguous side effects and
stale bindings remain parked. Ordinary `task`
concurrency observes the persisted per-session active-agent and parallel-task
caps; durable `batch_task` aggregates active leases across APEX batches and
Gateway workers, but it is not combined with ordinary `task` children in one
count. The Gateway task path does not apply the contract's delegation-depth
value (the lifecycle manager's own depth limit still applies). Acceptance-failure
replans are counted and capped; safe run recovery reserves retries durably by
source run and failure class against the frozen APEX ceiling, subject to the
runtime's global attempt limit. This shares the JSON store's single-process
coordination caveat. Per-session cost and CPU/RAM
measurements and cross-process coordination for the JSON session store remain
open. Completed runs wait in `awaiting_verification`; RunManager errors or
interruptions that cannot pass the safe checkpoint and APEX binding checks
remain parked for operator recovery.

The APEX-specific `agents.py` factory and specialization assessor are library
surfaces, not currently called by the Gateway dispatcher. A dispatched run is
prompted to plan and split independent work, and can use the ordinary `task`
tool under its persisted active-agent and parallel-task caps; the model chooses
whether to delegate. APEX does not yet persist or enforce a deterministic
specialization decision. The ordinary `task` path is single-level because
subagents are built with nested task delegation disabled. APEX depth zero now
withholds the first child call; a larger frozen depth value does not enable
recursive delegation. `ApexAgentFactory` has a separate lifecycle-manager depth
ceiling, but that factory is not called by the Gateway dispatcher.
