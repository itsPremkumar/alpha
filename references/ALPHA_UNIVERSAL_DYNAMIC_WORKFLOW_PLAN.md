# Alpha Universal Dynamic Workflow / Dynamic Execution Orchestrator — Implementation Plan

**Status:** v1 — main-agent authored (2026-09-23). Supersedes nothing; integrates everything.
**User mandate:** make the maximum possible number of Alpha capabilities dynamically selected, composed,
executed, reconfigured, and evolved according to prompt, task context, runtime state, resources,
agent/tool availability, project state, and execution results. Workflow = **live executable graph/state
machine**, not a fixed sequence or authoring-time DAG. End-to-end: one prompt → understanding → planning
→ capability discovery → workflow generation → resource allocation → execution → observation → adaptation
→ validation → completion → memory update → self-review, with autonomous continuation for long-running work.

## 0. Inputs this plan fuses

| Input | Where it lives |
|---|---|
| System-wide capability audit (89 rows: 29 static / 37 partially dynamic / 23 fully dynamic; 13 integration points; breakage list) | capability-audit report, session `ses_f30cf0146ffesR0f9sGhTIG7a7` (conversation) |
| Architecture research (pattern matrix A–I, 14 lessons, 7 ranked reference architectures, sources + licenses) | `C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\dynwf_research.md` |
| Phase-map report (self-evolving §75 Phases 2–7, WorkSwarm §46 Phases 0–12, 6 wave bundles) | phase-map report, session `ses_f3196c25dffea2hjV8vyRACIir` (conversation) |
| Existing partial dynamic-workflow implementation (other AI agent's work, **held uncommitted**) | `alpha/workflow/**` (15 files), `routers/workflows.py`, `bots/cloning.py`, `capabilities/catalog.py`, `workflow_dag_tool.py`, 3 tests |
| Existing Alpha subsystems | planner `alpha/planning/**`, workflow DAG `alpha/workflow/dag_engine.py`, mission `alpha/mission/**`, agent loop `alpha/agents/**`, subagents `alpha/subagents/**`, tools `alpha/tools/**`, MCP `alpha/mcp/**`, memory `alpha/memory/**` + `alpha/agents/memory/**`, events `alpha/events/**`, goals `alpha/goals/**`, automation `alpha/scheduler/**`, checkpoints `alpha/runtime/checkpoint*/**`, governance `alpha/policy/**` + approval queue, monitoring `alpha/supervision/**` + trace middleware, recovery `alpha/recovery/**` |
| User spec sections | the 87-section request of 2026-09-23 (audit-first; loops must close; zero unwired features; no fake/silent success; no disabled tests; root-cause fixes; second full audit; honest final report) |

## 1. Non-negotiable constraints

1. **Honesty:** a node with no bound executor **fails with the real reason**; it never emits fabricated
   output strings or fake "verification evidence". Absent config ⇒ honest failed status naming the missing
   piece. Confidence numbers are measured (with denominator) or disclosed-neutral — never invented.
2. **Governance:** every model-generated action converts into a **typed, validated, authorized operation**
   before execution; dangerous/irreversible operations pass policy/approval gates; approvals are **durable
   wait states**, never inline prompts.
3. **Observability:** every dynamic mutation is patch-id'd, versioned, logged in an append-only event log,
   checkpointed, resumable, and explainable via a `DecisionRecord` (candidates, scores, policy ids,
   graph version, model id, why).
4. **Ownership:** disjoint file ownership per wave; no git ops for subagents; no server/gateway restarts;
   targeted pytest only; no auth changes; never disable/weaken/skip tests; `ruff` clean +
   `ast_dup_gate_dups=0` before any wave reports done; path-scoped central commits only.
5. **Frozen/in-flight surfaces (P0–P1 waves):** `app.py`, `config/*_config.py`, `pyproject.toml`,
   `contracts/feature_manifest.json` (official regen only, at wave end, centrally adjudicated),
   all `frontend/**` (UI agent), `alpha/skills/**` + `alpha/memory/**` + `alpha/evidence/**` (Hermes),
   `tools/builtins/*search*` + tools registry hook (AgentEye), `alpha/multimodal/**` + voice routers
   (voice), all `alpha/rsi/**` (RSI Wave-2), `agents/memory/manager.py` + `security/memory_redaction.py`
   (frozen seam), `routers/{enterprise,evolution,memory}.py` free post-known-issues,
   `execution_mode.py`/`factory.py`/`client.py`/`commands/**` free post-gap7.
6. **Config:** additive, default-off fields only; **no `config_version` bumps** for additive fields.
7. **No parallel universe:** everything mounts into the existing planner/DAG/mission/agent-loop/tool/MCP/
   memory/event/goal/automation/checkpoint/governance/monitoring/recovery systems. New code lives beside
   them and calls them; it never duplicates them.
8. **Licenses:** concepts re-implemented; MIT/Apache sources preferred; **no code copied** from Restate
   (BSL), Inngest (SSPL), n8n (SUL), Windmill (AGPL), Mastra/LiteLLM `ee/`.
9. **Phase-map bundles** (updater, issue-intake, contribution-manifests, perpetual worker-gen, harness A/B)
   remain a separate queue; the workflow-IR bundle (their Wave 4) is **absorbed here** as P3 input.

## 2. Repository audit summary (evidence-first)

**Counts:** 89 capability rows → **static 29 / partially dynamic 37 / fully dynamic 23**.
Full row-level table (capability | path:line | class | required runtime decision) is in the audit report;
this plan consumes it as the backlog — §11 restates the applicability list feature-only.

**What already exists and works (integration anchors):**
- DWE core: `workflow/models.py` (node/edge/run/patch schema), `runtime.py` (waves, conditions, router,
  HITL gate, loop policy, token budget, replan-on-deadlock, patch apply, node_runner evidence gate),
  `patch.py` + `patch_validator.py` (optimistic concurrency), `scheduler.py`, `replanner.py`,
  `expressions.py` (AST-whitelisted evaluator), `events.py`, `leases.py`, REST `routers/workflows.py`
  (9 permission-gated endpoints, mounted `app.py:985`), legacy `dag_engine.py`, `sdlc_engine.py`.
- Bots: `bots/cloning.py` wired to REST + BOT nodes; ephemeral TTL specialists; workload-aware selection;
  org-from-goal; contract-net auction; swarm coordinator/scheduler/governor; A2A protocol + capability cards.
- Planning/mission/goals: `planning/**` (meta-planner, hyperplan, autonomous, interview),
  mission compiler/state-machine/work-queue, `goals/store.py` goal contracts, goal-loop primitives.
- Runtime ops: checkpoint*/selfheal/sentinel/lane-scheduler, recovery policy classes + strategies,
  scheduler cron/blueprints/wake-gate, event bus, trace middleware, token budgets (6-level hierarchy),
  policy engine (`git:push/merge→approval`, `secret:export→deny`), approval queue (HITL), audit council.
- Skills/tools/MCP/memory: skill lifecycle + evolution engine + retrieval + hub (AST audits),
  ~120 builtin tools all manifest-wired, dynamic tool synthesizer capability, MCP gateway/session-pool/
  oauth/skill-lifecycle, memory stack (active/cognitive/dreaming/session-search) + memory middleware.

**Breakage this plan must remediate first (P0 — the other agent's held layer):**
1. **Fabricated node executors** in `runtime.py` — MAP `:257`, REDUCE `:272`, RACE `:285` (picks
   `candidates[0]`, nothing runs), QUORUM `:297` (static config votes presented as votes), COMPENSATION
   `:314` (string only), BOT `:345` (fake output + fake evidence), default path `:405`
   ("Default verification evidence") — and `test_dynamic_workflow_engine.py:164` +
   `test_bot_dynamic_workflow.py:141` **canonize the fakes**.
2. **Silent lease loss** — `cloning.py:116` calls `provision()`/`register_lease()` with wrong arguments
   (guaranteed `TypeError`), `:123` swallows it; clones register but never get leases; no test asserts a
   lease exists.
3. **DAG-tool drops edges** — `workflow_dag_tool.py:129` builds an edgeless base graph; sync-back
   `:137` copies only nodes; all patch edge-ops silently lost.
4. **`dynamic_*` layer unimportable** — `dynamic_decomposer.py:52` uses nonexistent `NodeType.TASK`
   (import-time `AttributeError`), `:49` wrong `RetryPolicy` fields; cascade kills `dynamic_assembler.py`
   and `dynamic_bridge.py`; bridge has 9+ more model-API mismatches (`EdgeMode.ALWAYS`, `run.graph`,
   `NodeStatus.COMPLETED`, `n.error`/`run.error`); **zero callers** anywhere (no router/tool/capability/
   command reaches perceive→decompose→assemble→bridge); phantom `/boost` suggestion (no such command);
   assembler registers a **hardcoded mock skill** into the live skills hub and mocks MCP specs.
5. Dormant-but-declared: `alpha.ledger`, `alpha.evidence` (implemented, no consumer). Manifest tension:
   `auth`/`channels` marked `excluded_local_only` yet mounted. All 28 capabilities `default_enabled=False`.

## 3. Research synthesis (condensed from `dynwf_research.md`)

**14 design lessons adopted:** (1) split plan-time topology from run-time control flow; (2) mutation =
typed, versioned patches applied at node seams with base `graph_version` recorded; (3) one append-only
event log as single truth, everything else a projection; (4) idempotent steps + idempotency keys
(`run_id, node_id, input_hash`); (5) version via markers/pins, patch→deprecate→remove lifecycle;
(6) bound unbounded loops (Continue-As-New semantics, max-iterations, context condenser);
(7) capability discovery is a **protocol** (MCP `tools/list`, A2A Agent Card, SKILL.md progressive
disclosure, per-field authority); (8) missing-capability loop with an **empirical gate** (detect →
draft → sandboxed eval → versioned registration); (9) approvals are durable wait states with scoped,
sticky, idempotent resume; (10) decisions are structured artifacts (`DecisionRecord` + OTel GenAI spans);
(11) **topology = menu of vetted archetypes chosen by logged policy — never free-form LLM topology**;
(12) parallelize only independent work, with budgets (research: ~15× token cost); (13) guardrails at
every boundary with declared `on_violation` (reask/fix/filter/refrain/noop/exception); (14) RSI = archive
+ empirical validation across 3 lanes: L0 memory-only → L1 config/router/topology → L2 code/schema
(always human-gated).

**Ranked reference architectures:** ① event-sourced execution kernel + typed versioned plan graph
(Temporal × LangGraph hybrid — primary); ② capability plane (MCP + Skills + A2A + grants loop);
③ approval/governance plane (typed signed scoped gates); ④ topology plane (swarm archetypes + cost
ledger); ⑤ planning plane (task-DAG synthesis + partial replanning + evidence slots);
⑥ execution substrate swappable behind an engine-agnostic event-log contract; ⑦ RSI evolution loop
(3 sanctioned lanes). **Composition adopted:** ① kernel, ③ gates mounted on it, ② capability plane
feeding selection, ④ topology, ⑤ planner, ⑦ evolution mutating only through sanctioned lanes — all
traced through the event log + DecisionRecords.

**Anti-patterns banned:** unbounded self-modification; authoring-time-only DAGs; mutations without
provenance; approval fatigue (batch/policy-scope approvals); plan/run confusion (plan version ≠ run
history); claiming parallel speedups that cost more tokens than they save.

## 4. Target architecture

```
 prompt ─► Perception ─► Intent/Complexity classifier ─► Capability discovery (registries, protocol)
              │                                              │
              ▼                                              ▼
        Planner (existing planning/** + meta-planner) ─► TOPO-SELECT (menu of archetypes, logged policy)
              │                                              │
              ▼                                              ▼
        WorkflowPlan v.N (typed, versioned, pydantic) ─► Policy pre-flight (policy/engine + budgets)
              │                                              │
              ▼                                              ▼
   EVENT LOG (append-only) ◄────── Execution Kernel ──────► Nodes: agent|bot|subagent|tool|skill|mcp|
   (events, DecisionRecords,        (waves, idempotent,      model|workflow|automation|project|command|
    patches, checkpoints)            resumable, bounded)      human_approval|memory|research|validation|
                                                              system   ──► node_runner (real executors)
              │                                                      │
              ▼                                                      ▼
        Observation/eval ─► Adapt (PlanPatch at seams, versioned) ─► validate ─► complete / continue
```

| Component | Responsibility | Location | Status |
|---|---|---|---|
| Execution kernel | waves, idempotent step exec, bounded loops, resume, compensation | `alpha/workflow/runtime.py` (extend) + new `alpha/workflow/kernel/**` | exists, **honesty-fix P0** |
| Event log / state store | append-only truth; projections to existing event store | new `alpha/workflow/event_log.py` + `schemas.py` | P1 |
| Plan graph + versioning | typed `WorkflowPlan`/`PlanVersion`, archetype menu | `workflow/models.py` (additive) + new `plan_graph.py` | P1 |
| Mutation engine | versioned `PlanPatch` at seams, validation, provenance | `patch.py` + `patch_validator.py` (extend) | exists, P3 |
| Decision loop | perceive→discover→topo→plan→execute→observe→adapt→stop | new `alpha/workflow/orchestrator/{loop,decision}.py` | P1–P2 |
| Capability registry | descriptors, availability, discovery protocol | extend `capabilities/catalog.py` + new `alpha/workflow/registry/capabilities.py` | P2 |
| Skill registry + generator | detect missing skill → draft → sandboxed eval → versioned register | read `alpha/skills/**` seams via adapter; generator = new module | P2 (**no `skills/**` edits until Hermes lands**) |
| Tool registry + factory | runtime tool select/create/isolate/disable | read `alpha/tools/**`; factory adapter new module | P2 (**registry hook waits for AgentEye**) |
| Plugin manager | install/configure/invoke/isolate/disable plugins | existing extensions loader (config-side) + new manager module | P2 (endpoint deferred; `app.py` frozen) |
| MCP registry/connection manager | discover/connect/auth/validate/select/monitor/disconnect | `alpha/mcp/**` + new `alpha/workflow/registry/mcp.py` | P2 |
| Model router | capability/latency/cost/reliability/privacy-aware routing + fallbacks | free-LLM router (done) + new policy layer | P4 |
| Memory router | short/working/long/episodic/semantic/procedural/project/group/presentation selection | new `alpha/workflow/registry/memory.py` over existing tiers | P4 |
| Agent/bot factory | identity/role/SOUL/skills/tools/model/permissions/lifecycle | `agents/factory.py` (free post-gap7) + `bots/*` | P5 |
| Group/team/project/goal/task managers | create-on-demand from scenario | existing `groups/`, `projects/`, `goals/`, `missions/` + creation node kinds | P5 |
| Automation engine | create/trigger automations, reminders, background jobs | `alpha/scheduler/**` + creation node kind | P5 |
| Slash-command router | scenario-driven auto-invocation + chaining | `alpha/commands/**` (free post-gap7) | P6 |
| Swarm/topology manager | archetype selection, hierarchy, supervisor, quorum real votes | `alpha/swarm/**` + `bots/*` + topology plane | P7 |
| Adaptive execution | confidence/cost/latency/reliability routing, reroute, split/merge, self-correct | `replanner.py` + `recovery/policies.py` + router policies | P8 |
| Recovery engine | circuit breakers, retry/compensation/rollback, gateway/tool loss | `alpha/recovery/**` + kernel | P9 |
| Observability/audit | DecisionRecords, OTel-shaped spans via existing tracing seams, workflow health | new event log + additive endpoints on `routers/workflows.py` (no `app.py`) | P10 |
| Evaluation framework | scenario ladder, failure-injection, benchmarks | `alpha/benchmarks/**` + new dynwf suites | P11 (continuous) |
| Policy/permission plane | typed authz ops, approval gates, permission-aware routing | `alpha/policy/**`, approval queue, RBAC | P12 |
| UI/UX | run-tree, live plan view, approval cards, health | existing `HumanApprovalCard` + UI agent lane | P13 (coordinate) |
| RSI integration | L0/L1/L2 lanes only, archives + empirical gates | `alpha/rsi/**` (post-Wave-2) + evolution engine | P15 |

## 5. Typed schemas (new `alpha/workflow/schemas.py`, pydantic; all additive, versioned)

- `NodeKind = agent|bot|subagent|tool|skill|mcp|model|workflow|automation|project|command|
  human_approval|memory|research|validation|system`
- `NodeSpec{id, kind, config, idempotency_key, budget, requires[], on_violation, timeout_s, retry}`
- `EdgeSpec{src, dst, condition?, kind: normal|conditional|barrier}`
- `PlanVersion{graph_version:int, nodes, edges, archetype, created_by, parent_version, why}`
- `PlanPatch{patch_id, base_graph_version, ops[], rationale, actor, created_at}` — reject if base ≠ current
- `ExecutionEvent{run_id, seq, type, node_id?, payload, ts, decision_ref?}` — append-only
- `DecisionRecord{run_id, step, candidates[], chosen, scores?, policy_ids[], graph_version, model_id,
  why}` — the explainability atom; emitted for topo selection, model/tool/MCP/skill/bot/swarm/workflow
  mutations, approval resolutions, reroutes
- `CapabilityDescriptor{id, kind, availability, source, version, health, authority}` 
- `RunState = created|planning|ready|running|awaiting_approval|mutating|compensating|completed|failed|
  aborted` ; `NodeState = pending|ready|running|succeeded|failed|skipped|awaiting_approval|compensating`
- Lifecycle rule: no `succeeded` without either a real executor result (with evidence refs) or an
  explicitly-disclosed `vote_source: config`-style provenance label.

## 6. Runtime decision loop (the single seam)

`orchestrator/loop.py::run_turn(prompt, ctx) -> RunOutcome`, one module-level seam (tests stub it):
1. **Perceive** — intent, domain, complexity, tier (repair of `dynamic_perception.py`, validated against
   the real command catalog).
2. **Classify needs** — required `NodeKind`s + capabilities; consult registries (**discovery protocol**);
   missing capability → *generation loop* (draft → sandboxed eval → versioned register) or honest
   "unavailable" fallback.
3. **Topology select** — from the **vetted archetype menu** (sequential | dag-waves | map-reduce | race |
   quorum/vote | loop-bounded | swarm-hierarchical | swarm-supervisor | subagent-delegation | tool-chain |
   background-autonomous), policy-logged via `DecisionRecord`.
4. **Plan** — existing planner produces `PlanVersion`; deterministic fast-path for simple prompts
   (archetype `sequential`, zero LLM planning).
5. **Pre-flight** — policy engine + budgets + protected-path check; dangerous ops → approval gate
   (durable wait state).
6. **Execute** — kernel steps nodes idempotently through real `node_runner` executors.
7. **Observe/Adapt** — evaluate results; may emit `PlanPatch` **at node seams** while running
   (add/remove/reorder nodes, swap model/agent/tool, expand subgoals, retry failed branch, spawn
   research, create missing tool/skill); every mutation logged + versioned + explainable.
8. **Validate/Complete** — evidence-gated completion; memory update; self-review; if task requires
   long-running work, **continue autonomously** (bounded loops, budgets, heartbeat).
9. **Stop conditions** — goal satisfied with evidence, budget exhausted (real numbers), approval denied,
   circuit breaker open, or operator stop — each recorded honestly.

**Routing policy inputs:** prompt signals, runtime state, resource/cost/latency budgets, tool/MCP/model
availability + health, permissions, project state, prior `DecisionRecord` outcomes, confidence (measured).

## 7. Protocols (contract each phase must satisfy)

| Protocol | Contract | Failure semantics |
|---|---|---|
| Capability discovery | registries expose `list/describe/health`; loop queries before planning | missing → generation loop or honest unavailable node |
| Dynamic mutation | `PlanPatch` typed ops, `base_graph_version` check, seam-only application, logged | version conflict → rebase or fail-closed, never silent overwrite |
| Approval | durable run state `awaiting_approval`; scoped/sticky permissions; idempotent resume; batchable | timeout → policy default (usually fail-closed), recorded |
| Recovery | retry/backoff → fallback → replan → restore checkpoint → escalate → abort (existing `recovery/policies.py` classes) | every transition logged with real error text |
| Memory | router picks tier by scenario; provenance labels on injected memory | unavailable tier → next tier or honest omission |
| Agent communication | structured messages (existing swarm events + A2A cards); handoff = contract payload (objective/completed/findings/files/decisions/remaining) | undeliverable → queued + honest status |
| Swarm | topology from menu only; real quorum votes from executor results; cost ledger per member | quorum unreachable → honest failed node |
| Model routing | capability/latency/cost/reliability/privacy/permission-aware; fallback chain (union-alpha→alpha-free precedent) | all models down → honest failed status, no fake completion |
| Slash-command routing | scenario → command resolution against **real catalog** + chaining; never reference unregistered commands | unknown → plain action, not a phantom command |
| Graph/node/edge | pydantic schemas above; nodes carry idempotency keys; edges conditional/barrier only | schema violation → fail-closed before execution |
| Checkpoint/resume | event log + snapshot per node seam; `Continue-As-New`-style bound on history | corrupt → rebuild from log or honest degraded mode |
| Integration boundary | existing subsystems called via their public seams (import, never fork) | seam missing → new module beside it, never edit frozen files |

## 8. Implementation phases (each wave: disjoint MAY-EDIT list + gates + honest report; central commits)

- **P0 — Remediate the held layer (3 waves, now):** make existing code honest and importable BEFORE any
  of it is committed. (Spawns below.)
- **P1 — Kernel:** `event_log.py`, `schemas.py`, `plan_graph.py` (versioning), run lifecycle +
  resumability, orchestrator skeleton; wire `routers/workflows.py` run endpoints onto the event log.
- **P2 — Registries + discovery:** capability/tool/skill/MCP descriptor protocol + generation loop with
  empirical gate; perception/decomposer graduate from P0 into the loop seam.
- **P3 — Runtime mutation:** seam-scoped `PlanPatch` during live runs, provenance, replan integration
  (absorbs phase-map Wave-4: IR loader + node-type enum + per-node retry policies feeding `NodeSpec`).
- **P4 — Selection policies:** model router layer, memory router, tool/MCP selection policies.
- **P5 — Creation ops:** task/goal/project/group/automation/bot creation as first-class node kinds wired
  to the real managers.
- **P6 — Slash-command auto-routing:** scenario invocation + chaining against the real catalog.
- **P7 — Swarm allocation:** archetype selection, hierarchies, real quorum, cost ledger.
- **P8 — Adaptive execution:** confidence/cost/latency routing, reroute, split/merge, self-correction,
  mid-run workflow change.
- **P9 — Recovery:** circuit breakers, compensation made real, gateway/tool/model unavailability drills.
- **P10 — Observability:** DecisionRecords surfaced, workflow-health endpoints (additive on
  `routers/workflows.py` — **no `app.py`**), audit-trail query.
- **P11 — Testing:** continuous per-category suite (§9), not a phase-end event.
- **P12 — Security/permission:** permission-aware routing, approval-fatigue mitigation, policy coverage
  audit for every `system`-kind node.
- **P13 — UI integration:** run-tree + live plan + health (coordinate with UI agent; frontend = their lane;
  we ship the endpoints).
- **P14 — Production hardening:** official manifest regen, env/prod checks, one gateway-restart smoke
  window (free-LLM + voice + websearch + dynwf live), config docs.
- **P15 — End-to-end validation + second full audit:** scenario ladder through the full lifecycle,
  long-running/24×7 drill, failure-injection, persistence/resume, unavailability, concurrency;
  then the mandated second audit and honest report.

## 9. Test plan (every category the user mandated; honesty pins in every suite)

1. **Per-capability:** each registry/protocol/node-kind has tests asserting honest failure when
   unconfigured (single invoke-seam stubbing pattern).
2. **Scenario ladder:** trivial one-shot → multi-step DAG → mid-run mutation → swarm mission →
   autonomous long-running mission; each asserts full loop closure (UI/API→storage→runtime→tool→result→
   persistence→UI/logs→recovery).
3. **Failure injection:** model down, tool down, MCP down, gateway down, corrupt event log, version
   conflict, node crash, quorum unreachable → assert honest failed status + real error text + recorded
   recovery decision. **Assert absence of fabricated strings** (regression pins against
   "Processed item"/"Default verification evidence" class of fakes).
4. **Recovery:** retry/fallback/replan/checkpoint-restore/escalate/abort each exercised end-to-end.
5. **Permission/safety:** unauthenticated mutation denied; approval-gated ops cannot self-approve;
   protected-path rules; policy `on_violation` honored.
6. **Concurrency:** parallel waves + racing patches → optimistic-concurrency holds; idempotency keys
   dedupe; no lost updates.
7. **Persistence/resume:** kill mid-run (simulate), reload from event log, complete; graph version
   continuity; no duplicate side effects.
8. **Unavailability:** every registry returns honest unavailable → planner routes around or fails closed.
9. **Long-running:** bounded-loop + Continue-As-New + heartbeat + budget exhaustion with real numbers.
10. **Gates:** targeted pytest per wave, `ruff` clean, `ast_dup_gate_dups=0`, no skipped/xfail additions.

## 10. Definition of done (per the user's acceptance rules)

Loops close end-to-end for every feature added; zero unwired features (manifest truth = official regen);
no fake/silent success anywhere (grep-grade checks for fabricated-output patterns in the workflow layer);
all tests/lint/type-checks enabled; no hardcoded secrets; auth untouched; root-cause fixes only;
second full audit completed; final report honest about limitations.

## 11. Feature-only applicability list (every Alpha capability this architecture applies to)

**Prompt/understanding/planning:** prompt intent perception · intent detection · task decomposition ·
planning · deep planning · plan mode · plan artifacts · hyperplan review · interview/meta-planner ·
goal creation · objective tracking · task creation · to-do generation · milestone creation ·
priority selection · dependency management

**Research/evidence/reasoning:** research · web search · browsing · retrieval · evidence collection ·
reasoning · reflection · verification · critique · review · self-correction · retry · recovery ·
self-review

**Execution shapes:** sequential · parallel · recursive · conditional · iterative · swarm · delegated
subagents · tool chains · autonomous background work · map-reduce · race · quorum/voting/consensus/debate ·
judge/reviewer agents · specialist delegation · hierarchical delegation · iterative refinement ·
result comparison

**Modes:** bot-mode · message-mode · interactive · autonomous · coding · research · planning · execution ·
review · monitoring · maintenance · long-running/24×7

**Skills/tools/plugins/MCP:** skill create/load/modify/disable/version/test/replace · skill discovery ·
missing-capability detection · skill generation · tool select/create · plugin
install/configure/invoke/isolate/test/disable/remove · MCP discover/connect/authenticate/validate/select/
monitor/disconnect

**Models/routing:** LLM/model selection · provider/endpoint selection · local vs cloud · fallback ·
reasoning/fast/vision/embedding/specialist model choice · cost/latency/reliability/privacy/permission-
aware routing

**Agents/bots/org:** agent/bot create+configure (identity/role/profile/SOUL/instructions/skills/memory/
tools/MCP/model/permissions/limits/objectives/communication mode/lifecycle) · group/team/organization/
department/agent-hierarchy/channel/group-chat creation · supervisor selection

**Projects/memory/context:** project/folder/repo/document/knowledge-space/context/memory/policy creation ·
short-term/working/long-term/episodic/semantic/procedural/project/agent/group/presentation memory
selection · context/checkpoint/artifact/file management

**Commands/decisions:** slash-command auto-invocation · slash-command chaining · ask-user vs autonomous
continue vs approval vs escalate vs create-task/automation/project/bot vs launch-swarm vs research vs
tools vs MCP vs self-modify-workflow vs stop

**State/ops/governance:** state/context/checkpoint/artifacts/files/code changes · git operations ·
terminal operations · browser actions · external services/APIs/credentials · permissions · budgets ·
rate limits · resource constraints · concurrency · queues · priorities · deadlines · timeouts · retries ·
compensation · rollback · failure recovery · circuit breakers · safety checks · approval gates ·
human-in-the-loop · observability · audit logs · telemetry · cost tracking · performance monitoring ·
agent/task/workflow health · completion validation

**Automation/events:** automation create · automation trigger · event handling · reminders · background
jobs · time/state/failure/capability/event/trigger-based execution · scheduling

**Topology/dynamics:** agent allocation/reassignment · subagent spawning · swarm creation ·
topology selection · hierarchy creation · workflow modification while running · add/remove/reorder tasks ·
replace agents/models · extra research · create missing tools/skills · expand subgoals · merge/split
tasks · change priorities · retry failed branches · recover from unavailable tools/gateways ·
create new workflows from discovered requirements · confidence-based routing · outcome-aware routing

**Memory-of-work:** memory update after completion · experience/FACT/TIP capture · evidence-gated
promotion · RSI L0/L1/L2 evolution lanes

**Surfaces:** workflow REST API · run-tree UI · live plan view · approval cards · health dashboards ·
evolution/timeline views · config/schema system · persistence layer · security model · permission system ·
testing framework

*(Exclusions: pure snapshot tooling with no runtime decision — feature-manifest generation script itself —
and operator-declared `excluded_local_only` constraints. Everything else in the 89-row audit is listed.)*

## 12. Risks / deferred coordination

- **Cross-agent file races:** `routers/workflows.py` is unowned-but-live — P0/P1 adopt it; if OpenClaw's
  lane adds endpoints there meanwhile, central review reconciles before commit.
- **Hermes/AgentEye/voice/UI freezes** delay P2 registry hooks and P13 UI; plan orders phases so kernel
  work (P0/P1/P3) proceeds without them.
- **Held workstream commit policy:** nothing from `alpha/workflow/**` + `bots/cloning.py` +
  `workflow_dag_tool.py` + their tests is committed until P0 gates pass (no sweeping mega-commits).
- **Phase-map bundles** (updater P-core, issue-intake, contribution-manifests, perpetual worker-gen,
  harness A/B) queue behind P0/P1 to keep concurrency safe.
- **Token cost of parallelism** (research lesson 12): swarm/map-reduce nodes must carry budgets.

## 13. Normal-mode & bot-mode completion (user directive, 2026-09-23)

**Directive:** the dynamic workflow is partially implemented in *normal mode* and *bot mode*; enhance the
idea and **complete it in both modes**. Both modes share one kernel — the difference is the execution
context and the node executors they bind.

| Aspect | Normal mode | Bot mode |
|---|---|---|
| Trigger | user prompt / agent loop / plan+code execution modes (`runtime/execution_mode.py`) | bot claim/dispatch, message-mode events, ephemeral TTL spawn, work_discovery |
| Node executors | AGENT/TOOL/RESEARCH nodes → agent factory + real `node_runner` | BOT/SUBAGENT nodes → bot registry lookup / clone (`bots/cloning.py`) with real leases |
| Side-effect gating | `work.plan`/`code.plan` → approval gate nodes (durable wait states) | bot permissions + policy engine verdicts per bot identity |
| Long-running | bounded loops + Continue-As-New + heartbeat | perpetual bots + leases + TTL + swarm cost ledger |
| Shared | event log, `PlanVersion`/`PlanPatch` mutation, `DecisionRecord`s, budgets, checkpoints, recovery, HITL — one set of schemas for both modes | same |

**Completion criteria (both modes):** (1) a prompt in normal mode flows through perceive → discover →
topology-select → plan → execute → observe → adapt → validate → memory-update via the orchestrator seam
(`orchestrator/loop.py::run_turn`); (2) the same task expressed as a bot objective (claim, message-mode
event, or TTL spawn) executes the *same* plan machinery with bot executors bound; (3) every node in both
modes fails honestly when its executor is unbound (P0/DY-R1 contract) — no fabricated outputs in either
mode; (4) cross-mode handoff is first-class: a normal-mode run may delegate a branch to a bot swarm and
a bot may hand back via the handoff contract (objective/completed/findings/files/decisions/remaining);
(5) scenario-ladder tests (§9.2) run **every rung in both modes**.

**Phasing:** P0 (running: DY-R1 runtime honesty incl. BOT nodes · DY-R2 real clone leases · DY-R3 layer
repair) → P1–P2 kernel + loop seam → **P16/DY-R4 mode integration**: wire the orchestrator seam into the
normal-mode agent path and the bot-mode dispatch path, mode-aware executor binding, cross-mode handoff
node kind, dual-mode scenario tests. Read-only mode-integration mapper (path:line evidence) lands first;
DY-R4 spawns only after P0 waves finish (file overlap on `runtime.py`/`cloning.py`/`dynamic_*`).

## 14. Mode-integration mapper findings (report `ses_f308642e7ffeWYj6r39d6rj7Ck`, received 2026-09-24)

**Normal mode:** `routers/runs.py:33` (stateless) / `thread_runs.py:943` → `services.py::start_run:1330` →
`worker.py::run_agent:762` (RunJournal `:895`, event store `:1050`, assembly `:1101`, graph exec
`agent.astream` `:1206`, abort `:1210`) → assembly `agents/factory.py:66→184` or `client.py:307→470`;
mode gating `runtime/execution_mode.py:58-69,247,264`; REST `plan_mode.py:41,53,138,151`.

**Bot mode:** `routers/bots.py` (claim `:576`, route-task `:646`, DM `:667`, inbox `:698-726`);
`work_discovery.claim_task:112-158` **state-flip only, no executor**; `handoff.execute_handoff:40-101`
**event-only**; `swarm/scheduler.dispatch_ready_tasks:36-55` **marks RUNNING, no executor**; ephemeral
`spawn:114` / `provision:238-264`; `planning/bridge.py:122` (7 paradigms `:151-456` — **none routes
through the DWE**).

**Shared holes = DY-R4 targets:** `orchestrator/loop.py::run_turn` is **greenfield** (neither
`alpha/orchestrator/` (12 entries) nor `alpha/orchestration/` (6 entries) has `loop.py`); DWE reachable
only via REST `routers/workflows.py:113-123`, which calls `execute_step` **without `node_runner`** →
fabricated default path until DY-R1's honest no-runner refusal + runner wiring land (integration note:
`test_dynamic_workflow_router.py` and `test_bot_dynamic_workflow.py::test_workflow_with_dynamic_bot_cloning`
currently pin the old fabricated completion — central fix at DY-R1 landing: bind a real `node_runner`,
pin honest semantics, never disable); `DynamicWorkflowBridge` was unexported by `workflow/__init__.py`
at mapping time (DY-R3 has since added additive exports); inbox→run consumer: none found within read
scope (mapper had no grep — repo-wide census deferred).

**Intersection matrix:** 16 rows (entry/executor/assembly/turn-seam/DWE-reachability/node_runner/mode-gating/
HITL/task-exec/swarm-dispatch/provisioning/handoff/messaging/persistence/test-canonization/dispatch-bridge)
— divergences in 12, shared holes in 4 (turn seam, DWE reachability, real executors, DWE-bypassing
dispatch). Full matrix + ownership hazards in the mapper report (conversation record).
