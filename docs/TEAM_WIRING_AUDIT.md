# Team wiring audit

**Agent:** `agent/teamwire` (worktree `alpha-teamwire`)
**Scope:** the team / coordination / collaboration surface — `alpha/swarm/**`,
`alpha/subagents/**`, `alpha/bots/**`, the team tools, `alpha/company/**`, and the
Gateway routers that expose them.
**No product code was changed.** This document is the only file written.

## Method

`rg` is unavailable in this environment (a chocolatey-installed shim cannot find
`rg.exe`) and returns nothing silently, so every count below is
`Select-String`. Two files are enumerated once into a `$all` list and filtered,
so the counts are exact, not sampled.

```powershell
# the canonical census used throughout (excludes the mechanism's own package
# and every test file):
$all = Get-ChildItem -Recurse -File -Filter *.py `
        -Path backend/app,backend/packages
$prod = $all | Where-Object {
    $_.FullName -notmatch '\\tests\\' -and $_.Name -notlike 'test_*'
}
$prod | Select-String -Pattern 'alpha\.swarm' |
  Group-Object Path | Sort-Object Count -Descending |
  ForEach-Object { "{0,4}  {1}" -f $_.Count, $_.Path }
```

"Production" means every `.py` under `backend/app/**` and
`backend/packages/harness/alpha/**` (and `packages/extension-api/**`) that is
**not** under a `tests/` directory and is **not** named `test_*.py`. Tests are
excluded from caller counts on purpose: a mechanism that only its own test suite
calls is inert by the definition this audit uses.

**Live probing was not possible.** The plan's preferred method is read-only
probing of the running Gateway on 8001. Nothing is listening:

```
2026 : False
3000 : False
8001 : False
8002 : False
```

Per the hard constraints I did not start a stack, so this audit is static. Every
"reachable" claim below is therefore a claim about the *registration and import
graph*, and it is marked as such — not a claim that a live HTTP response was
observed. Where a static claim is weak I say so.

---

## Headline count

Every `.py` outside tests that mentions `alpha.swarm`, `alpha.subagents`,
`alpha.bots`, or `alpha.company`:

```powershell
cd backend
Get-ChildItem -Recurse -File -Filter *.py -Path app,packages |
  Where-Object { $_.FullName -notmatch '\\tests\\' -and $_.Name -notlike 'test_*' } |
  Select-String -Pattern 'alpha\.(swarm|subagents|bots|company)' |
  Group-Object Path | Sort-Object Count -Descending
```

The result is **150 distinct production files** (measured, not estimated). This
is not a stub
package. The interesting number is a different one, and it is the one the rest
of this document is about: how many of those files are *the tool the model can
call* versus a router, and how many mechanisms exist with **no** production
reference outside their own module.

**The single most load-bearing fact this audit found:** inside
`alpha/swarm/worker.py`, all three swarm workers run **one raw `model.invoke()`
with a system prompt and a human message, and no tools at all.** Not an agent —
a single-shot text generator. `EphemeralSubagentWorker` is named for the
subagent subsystem and does not import it.

```powershell
Select-String -Path backend/packages/harness/alpha/swarm/worker.py `
  -Pattern 'create_chat_model|invoke|ChatOpenAI|subagent'
```

```
 36: def _resolve_model(model_name: str | None, *, worker_label: str):
 87:     response = model.invoke(
```

`SubagentExecutor` does not appear in `swarm/worker.py` at all. So a "swarm of
specialists" is today a *fan-out of independent LLM completions with no shared
tool access and no ability to act*, and `alpha/subagents/executor.py` — a real
agent loop with tools, receipts, acceptance checks, and an isolated event loop —
is reached by `task_tool` but **not** by the swarm.

---

## `alpha/swarm/**` - 24 modules, 314 KB

### External entry points (complete list)

```powershell
Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
  Where-Object { $_.FullName -notmatch '\\tests\\' -and $_.Name -notlike 'test_*' } |
  Select-String -Pattern 'alpha\.swarm' |
  ForEach-Object { "{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.Trim() }
```

```
app\gateway\routers\swarms.py:15: from alpha.swarm.coordinator import get_swarm_coordinator
app\gateway\routers\swarms.py:16: from alpha.swarm.models import SwarmBudget, SwarmMode, is_terminal_swarm_status
app\gateway\routers\swarms.py:504: from alpha.swarm.incidents import get_swarm_incident_manager
app\gateway\routers\swarms.py:524: from alpha.swarm.memory import get_swarm_memory_manager
app\gateway\routers\swarms.py:623: from alpha.swarm.governor import get_swarm_resource_governor
app\gateway\autonomy\loops.py:140: from alpha.swarm.runner import AsyncSwarmRunner  # noqa: F401
packages\harness\alpha\tools\builtins\swarm_tool.py:11: from alpha.swarm.coordinator import get_swarm_coordinator
packages\harness\alpha\tools\builtins\swarm_tool.py:12: from alpha.swarm.governor import get_swarm_resource_governor
packages\harness\alpha\tools\builtins\swarm_tool.py:13: from alpha.swarm.incidents import get_swarm_incident_manager
packages\harness\alpha\tools\builtins\swarm_tool.py:14: from alpha.swarm.models import SwarmBudget, SwarmMode, TaskNodeState
packages\harness\alpha\commands\module_a_handlers.py:424: from alpha.swarm.coordinator import get_swarm_coordinator
packages\harness\alpha\planning\bridge.py:42: from alpha.swarm.coordinator import get_swarm_coordinator
packages\harness\alpha\planning\bridge.py:43: from alpha.swarm.models import SwarmMode
packages\harness\alpha\planning\meta_planner.py:21: from alpha.swarm.estimator import SwarmBenefitEstimator
packages\harness\alpha\planning\meta_planner.py:22: from alpha.swarm.models import SwarmMode
packages\harness\alpha\runtime\escalation.py:1072: from alpha.swarm.coordinator import get_swarm_coordinator
packages\harness\alpha\runtime\escalation.py:1073: from alpha.swarm.models import TaskNodeState, is_terminal_swarm_status
packages\harness\alpha\bots\capability_dispatch.py:75: from alpha.swarm.cnp_auction import ContractNetAuctionEngine, SwarmWorkerAgent, TaskAnnouncement
packages\harness\alpha\capabilities\catalog.py:172: module="alpha.swarm.cnp_auction",
```

Two things stand out.

1. **`app/gateway/autonomy/loops.py:140` is a dead import.** It is
   `AsyncSwarmRunner  # noqa: F401`, inside `swarm_status_tick()`, which then
   returns a hard-coded dict `{"active_swarms": "on-demand-only", "note": ...}`.
   The supervisor loop that is supposed to report swarm state fabricates a
   literal string instead of reading anything. This is the same class of defect
   as the "410 of 426 slash commands return `status="success"` for doing
   nothing" finding, and it is the *only* thing the swarm autonomy loop does.
2. **The swarm runtime is not a `RunManager` run.** `run_async` calls
   `coordinator.start_async` -> `AsyncSwarmRunner.start_background_swarm`, which
   keeps its own module-global registries
   (`_ACTIVE_SWARM_TASKS` / `_ACTIVE_SWARM_THREADS`) and runs the plan in a
   daemon thread or a bare `asyncio.create_task`. That is a second lifecycle
   owner - see answer 3.

### The `swarm` model tool: registered, reachable, and end-to-end capable

`swarm_tool` is imported in `alpha/tools/tools.py` and listed in `BUILTIN_TOOLS`
(line 230) **and** in `SUBAGENT_TOOLS` (line 387). `BUILTIN_TOOLS` is consumed
by the tool-assembly function at line 474 and folded into the live tool list at
line 553, and `SUBAGENT_TOOLS` is extended at line 487. So the tool reaches the
model twice over.

```powershell
Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
  Where-Object { $_.FullName -notmatch '\\tests\\' -and $_.Name -notlike 'test_*' } |
  Select-String -Pattern 'BUILTIN_TOOLS|SUBAGENT_TOOLS'
```

Verdict on the specific question - *can `strategy` / `telemetry` / `trace` run a
plan end to end today?* - is **yes, all three**, but they differ in what they
need first:

| action | what it reads | works on a freshly-spawned, unrun swarm? |
| --- | --- | --- |
| `spawn` | `coordinator.create_swarm` | n/a - creates it |
| `strategy` | `plan.metrics["strategy"]` | **yes.** Set at `decomposer.py:67` on every plan, explicit or automatic. Returns `Recorded: no` only for a pre-upgrade checkpoint. |
| `telemetry` | `coordinator.metrics(sid)["telemetry"]` | **yes.** `SwarmTelemetry.snapshot(plan)` is computed live inside `metrics()`; the key always exists. |
| `trace` (deposit) | `coordinator.deposit_trace` | **yes** - but the *automatic* completion traces are gated: `coordinator._stigmergy_enabled()` returns `False` unless the recorded strategy tier is `medium` or `full`, so a `direct`/`simple` plan silently keeps no traces. |
| `trace` (read) | `coordinator.traces` | yes, returns an honest empty list |
| `leader` | `plan.metrics["leader_election"]` | **yes** - written at `coordinator.py:301` on every `create_swarm` |

The planner is live on the same path. `create_swarm` ->
`SwarmTaskDecomposer.decompose` -> `resolve_strategy` -> `compute_dag_features`
-> `build_candidate` -> `select_best_candidate` -> `plan.metrics["strategy_candidates"]`.
Every one of those symbols is called from `decomposer.py`; none is test-only.

**The catch:** decomposition is *deterministic and template-based*. There is no
model anywhere in `decomposer.py`:

```powershell
Select-String -Path backend/packages/harness/alpha/swarm/decomposer.py `
  -Pattern 'create_chat_model|invoke|ChatOpenAI|llm'
# (no output)
```

`_decompose_hierarchical`, `_decompose_debate`, `_decompose_ensemble`,
`_decompose_map_reduce`, `_decompose_coding_worktree` are fixed task shapes. The
"planner" chooses a *template*, it does not plan. So "get three specialists to
review this design" never reaches any specialist in an intelligent sense - it
reaches a fixed 3-node template whose objectives are string templates wrapped
around the goal text.

### The HTTP surface is complete

`app/gateway/routers/swarms.py` is registered at `app/gateway/app.py:1167` and
exposes 24 routes: `evaluate`, `POST ""` (create+spawn), `GET ""`,
`GET/{id}`, `step`, `pause`, `resume`, `cancel`, `events`, `events/stream`,
`tasks/{tid}/complete`, `tasks/{tid}`, `tasks/{tid}/claim`, `expand`, `replan`,
`run-async`, `incidents`, `memory`, `messages` (GET+POST), `metrics`, `leader`,
`governor/status`. The frontend `TeamOpsSection.tsx` reads
`GET /api/swarms/{swarm_id}/messages`, and
`frontend/src/lib/swarm-messages-wiring.test.mjs` pins the route shape.

### Per-mechanism verdicts - `alpha/swarm/**`

Caller counts use the `wiring.ps1` census, which labels each hit file
`OUTSIDE` / `swarm-internal` / `subagents-internal` / `bots-internal`.

| Mechanism | Module | Production callers outside its own package | Reachable from the model? | Reachable from the UI? | Verdict |
| --- | --- | --- | --- | --- | --- |
| `SwarmCoordinator` (spawn/step/claim/complete/expand/checkpoint) | `swarm/coordinator.py` | 5 (`swarm_tool`, `routers/swarms.py`, `planning/bridge.py`, `runtime/escalation.py`, `commands/module_a_handlers.py`) | yes - `swarm` tool | yes - 24 HTTP routes | **LIVE** |
| `AsyncSwarmRunner` / `start_background_swarm` | `swarm/runner.py` | 1 (`coordinator.start_async`, itself reached by tool + HTTP `run-async`) | yes - `swarm(action='run_async')` | yes - `POST /{id}/run-async` | **LIVE** |
| `SwarmScheduler` (DAG validation, lease-fenced claim) | `swarm/scheduler.py` | 4 swarm-internal (`coordinator`, `runner`, `aggregator`) | indirectly, via run | indirectly, via `step`/`metrics` | **LIVE** |
| `SwarmTaskDecomposer` + candidate planner | `swarm/decomposer.py`, `swarm/planner.py` | 1 (`coordinator.create_swarm`) | yes - `spawn` | yes - `POST /api/swarms` | **LIVE** (template decomposition, not model planning) |
| `SwarmAggregator` (evidence-backed consensus) | `swarm/aggregator.py` | 2 (`coordinator`, `runner`) | yes - `status`/`metrics` | yes | **LIVE** |
| `run_deliberation` (Wald sequential test) | `swarm/deliberation.py` | 3: `aggregator.py`, plus **`routers/deliberation.py`** | via swarm run, plus a dedicated `/api/deliberation` router | yes | **LIVE** |
| `evaluate_consensus` (vote stances) | `swarm/consensus.py` | 4: `aggregator.py`, **`enterprise/rfc.py`**, **`routers/enterprise.py`** | via consensus-requiring swarms | yes | **LIVE** |
| `resolve_strategy` (complexity tier) | `swarm/strategy.py` | 4: `decomposer.py`, `planning/meta_planner.py`, **`groups/war_room.py`** | yes | yes | **LIVE** |
| `route_topology` / `compute_dag_features` (adaptive topology) | `swarm/topology.py` | 3: `strategy.py`, `decomposer.py`, `planner.py` | yes | yes | **LIVE** |
| Stigmergy (`StigmergicTraceStore`) | `swarm/stigmergy.py` | 3: `coordinator.py`, `models.py`, plus the `swarm` tool's `trace` action | yes - `trace` | no HTTP route | **LIVE** (model only) |
| `SwarmWatchdog` (straggler detection) | `swarm/watchdog.py` | 3: `coordinator.py`, `runner.py` | via run | via metrics | **LIVE** |
| `SwarmReflector` (stall/replan) | `swarm/reflection.py` | 2: `runner.py` | via run | via metrics | **LIVE** |
| `SwarmBenefitEstimator` | `swarm/estimator.py` | 5: `coordinator.py`, `strategy.py`, `triggers.py`, **`planning/meta_planner.py`** | via `evaluate` | via `/evaluate` | **LIVE** |
| `ContractNetAuctionEngine` + `elect_leader` | `swarm/cnp_auction.py` | `elect_leader` 2 (`coordinator.py:301`); `ContractNetAuctionEngine` 3 - `bots/capability_dispatch.py`, `planning/bridge.py`, `capabilities/catalog.py` | via `spawn` (leader election) and dispatch | no direct route | **LIVE** |
| `SwarmResourceGovernor` | `swarm/governor.py` | 2 (`swarm_tool`, `routers/swarms.py:623`) | yes - `governor` | yes - `/governor/status` | **LIVE** |
| `SwarmIncidentManager` | `swarm/incidents.py` | 2 (`swarm_tool`, `routers/swarms.py:504`) | yes - `incidents` | yes | **LIVE** |
| `SwarmMemoryManager` (tri-tier) | `swarm/memory.py` | 3 - `get_swarm_memory_manager` in `runner.py` **and** `routers/swarms.py:524` | no direct action | yes - `GET /{id}/memory` | **LIVE** (UI only) |
| `SpecialistBotWorker` / `EphemeralSubagentWorker` / `CodingWorktreeWorker` | `swarm/worker.py` | 1 (`runner._build_worker`) | via run | via run | **LIVE** - but single-shot, tool-less model calls |
| `SwarmMessageBus` | `swarm/communication.py` | 2 (`coordinator.py`, itself) | yes - `message`/`observe` | yes - `/{id}/messages` | **LIVE** |
| **`AutonomousWorkTrigger`** | `swarm/triggers.py` | **0 outside `__init__.py` and `triggers.py` itself** | no | no | **INERT** |
| `swarm_status_tick` supervisor loop | `app/gateway/autonomy/loops.py:133` | registered as a loop, but the body is a `# noqa: F401` import + a literal dict | no | no | **INERT** (returns a fabricated string) |

The two INERT rows, verified:

```powershell
& wiring.ps1 -Symbols 'AutonomousWorkTrigger'
#   2 files: swarm/__init__.py, swarm/triggers.py  == 0 outside

Select-String -Path backend/app/gateway/autonomy/loops.py -Pattern 'swarm' -Context 1,3
```

`swarm/triggers.py` is 1,963 bytes and exports `AutonomousWorkTrigger`, a
self-scheduling trigger for autonomous work. It is imported by
`swarm/__init__.py` and by nothing else in production. The `__init__.py` import
is what makes this look wired in a grep; it is the classic false positive, and
it is the same shape as `alpha.evolution.evidence` being "imported only by its
own config".

---

## `alpha/subagents/**` - 34 modules, 437 KB

This is the **most genuinely wired** part of the team surface, and it is worth
saying so plainly before the gaps: `SubagentExecutor` has 8 production callers
outside its own module.

```powershell
& wiring.ps1 -Symbols 'SubagentExecutor'
```

```
      [OUTSIDE] app\channels\manager.py
      [OUTSIDE] packages\harness\alpha\agents\middlewares\loop_detection_middleware.py
      [OUTSIDE] packages\harness\alpha\groups\runner.py
      [OUTSIDE] packages\harness\alpha\groups\war_room.py
      [OUTSIDE] packages\harness\alpha\orchestrator\domain_executors.py
      [OUTSIDE] packages\harness\alpha\tools\builtins\self_improvement_tool.py
      [OUTSIDE] packages\harness\alpha\tools\builtins\task_tool.py
      [OUTSIDE] packages\harness\alpha\tools\script_bridge\dispatcher.py
```

`task_tool` reaches it directly at line 26
(`from alpha.subagents import SubagentExecutor, ...`) and also drives the
isolated event loop via `run_on_isolated_subagent_loop`. **Answer: yes,
`task_tool` reaches `SubagentExecutor`, and it is the most capable team member
Alpha has** - real tools, receipt harvesting, acceptance criteria, capacity
accounting, an isolated asyncio loop, and a background-task registry.

| Mechanism | Module | Production callers outside its own package | Reachable from the model? | Reachable from the UI? | Verdict |
| --- | --- | --- | --- | --- | --- |
| `SubagentExecutor` (the real agent loop) | `subagents/executor.py` (99 KB) | **8** | yes - `task` tool | yes - `routers/subagents.py`, `subagent_control.py`, `subagent_batches.py` | **LIVE** |
| `check_acceptance_criteria` | `subagents/acceptance_checks.py` (95 KB) | 2 (`task_tool.py`, `self_improvement_tool.py`) | via `task` | via the status contract | **LIVE** |
| `SubagentExecutionCapacity` | `subagents/capacity.py` | 1 (`task_tool.py`) | via `task` | via the batch service | **LIVE** |
| `run_on_isolated_subagent_loop` | `subagents/executor.py` | 1 (`task_tool.py`) | via `task` | no | **LIVE** |
| `HierarchicalDelegationEngine` (`get_delegation_engine`) | `subagents/hierarchical_delegator.py` | 2 files - `tools/builtins/deep_agent_tool.py`, `planning/bridge.py:450` | yes - `delegate_to_deep_agent` | no | **LIVE** |
| Deep-agent fleet (`deep_architect`, `deep_code_reviewer`, `deep_debugger`, `deep_performance`, `deep_security`, `deep_test_synthesizer`) | `subagents/builtins/*.py` | reached via `deep_agent_tool.py` | yes - `list_available_deep_agents`, `delegate_to_deep_agent`, `inspect_deep_agent_telemetry` | no | **LIVE** |
| `SubagentLifecycleManager` (lease/heartbeat/deliverable) | `subagents/lifecycle.py` | 4 (`routers/subagent_control.py`, `runtime/escalation.py`, `commands/backend_handlers.py`, `subagent_control_tool.py`) | yes - `subagent_control` | yes - `/api/subagent-control` | **LIVE** |
| `SubagentResilienceEngine` (checkpoint) | `subagents/resilience.py` | 2 (`routers/subagent_control.py`, `subagent_control_tool.py`) | yes - `subagent_control` | yes | **LIVE** |
| `SubagentPromotionManager` (role metrics) | `subagents/promotion.py` | 2 (`routers/subagent_control.py`, `subagent_control_tool.py`) | yes - `subagent_control` | yes | **LIVE** |
| `generate_dynamic_role` (archetype keyword match -> `SubagentContract`) | `subagents/specialists.py` | 1 (`subagent_control_tool.py`) | yes - `subagent_control` | yes | **LIVE** |
| `batch_service` (parallel batch runs) | `subagents/batch_service.py` | 5 (`app/gateway/app.py`, `deps.py`, `routers/subagent_batches.py`, `app/subagent_batches/`) | yes - `batch_task` / `batch_status` / `cancel_batch` | yes - `/api/subagent-batches` | **LIVE** |
| Deep-handoff contract (`DeepHandoff`) | `subagents/deep_handoff_contract.py` | 8, but **all inside `subagents/`** - the 6 builtins + `hierarchical_delegator` + itself | no | no | **PARTIAL** - internally consistent, no external entry |
| `get_archetype_template` | `subagents/specialists.py:121` | 0 outside; `generate_dynamic_role` reads `ARCHETYPE_REGISTRY` **directly** and never calls it | no | no | **INERT** (dead accessor) |

```powershell
# the dead accessor, proven
Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
  Select-String -Pattern 'get_archetype_template' |
  ForEach-Object { "{0}:{1}" -f $_.Path, $_.LineNumber }
# subagents/specialists.py:121  (the def)
# subagents/__init__.py:27, 55 (re-export only)
```

`get_archetype_template` is a small defect rather than a large one - the
behaviour it wraps is genuinely used, just not through the accessor. Worth
noting because it is the *third* instance in this audit of a re-export in
`__init__.py` making an unwired symbol look wired.

---

## `alpha/bots/**` - 34 modules, 528 KB

### `bot_roster` is registered and has 20 implemented actions

`bot_roster_tool` is in `BUILTIN_TOOLS` (`tools.py:183`) and in
`workflow/dynamic_assembler.py`. Its `Literal` action list has 20 entries -
`list, monitor, inspect, create, generate_team, handoff, update_soul,
update_profile, routine, pause, resume, kill_switch, forge, teach, waiting_on,
journal, share, import, doctor, sandbox` - and there is an
`elif action == "<name>"` branch for **all 20**. This is the rare case of a tool
whose advertised action list and its implementation match exactly, and it is
worth contrasting with the "410 of 426 slash commands return
`status="success"` for doing nothing" finding: `bot_roster` is not that.

```powershell
Select-String -Path backend/packages/harness/alpha/tools/builtins/bot_roster_tool.py `
  -Pattern 'if action ==|elif action =='
```

| Mechanism | Module | Production callers outside its own package | Reachable from the model? | Reachable from the UI? | Verdict |
| --- | --- | --- | --- | --- | --- |
| `BotRegistry` (roster CRUD, org chart, selection) | `bots/registry.py` (36 KB) | **6** (`routers/bots.py`, `routers/groups.py`, `groups/lifecycle_ops.py`, `runtime/sentinel/scheduler.py`, `swarm/worker.py`, `workflow/dynamic_assembler.py`) | yes - `bot_roster` | yes - 36 routes in `routers/bots.py` | **LIVE** |
| `forge_bot` (transactional build + `_rollback` over 4 undo steps) | `bots/forge.py` (28 KB) | 1 (`bot_roster_tool.py:649`) | yes - `bot_roster(action='forge')` | no | **LIVE** (model only) |
| `execute_handoff` | `bots/handoff.py` | 5 (`routers/bots.py`, `planning/bridge.py`, `projects/handoffs.py`, `bot_roster_tool.py`) | yes - `bot_roster(action='handoff')` | yes - `POST /api/bots/handoff` | **LIVE** |
| `get_journal` / `BotJournal` | `bots/journal.py` (21 KB) | 4 (`forge.py`, `bot_roster_tool.py`, `orchestration/durable_replay.py`, `durable_replay_tool.py`) | yes - `journal`, `waiting_on` | no | **LIVE** |
| `discover_journals` / `waiting_on_you` | `bots/journal.py` | 1 each (`bot_roster_tool.py`) | yes | no | **LIVE** |
| `export_template` / `import_template` (`.alphabot.json` + secret scanner) | `bots/portable.py` (20 KB) | 1 each (`bot_roster_tool.py`) - the `share` and `import` actions | yes - `bot_roster(action='share'/'import')` | no | **LIVE** (model only) |
| `survey_workspace` (repo survey, secret scrub, cache) | `bots/survey.py` (24 KB) | 1 (`forge.py:508`) - reachable **only** by forging | yes, but only inside `forge` | no | **PARTIAL** |
| `plan_routine_guard` | `bots/forge.py` | 1 (`bot_roster_tool.py`, the `routine` action) | yes | no | **LIVE** |
| `build_guardrail_soul` | `bots/forge.py` | 0 outside; called from `forge.py:530` only | yes, but only inside `forge` | no | **PARTIAL** |
| `evaluate_quality_gate` | `bots/quality_gate.py` | 3 (`routers/bots.py:318`, **`swarm/aggregator.py`**) | indirectly | yes - `/quality-gate/verify` | **LIVE** |
| `get_bot_clone_engine` (`clone_bot`, `evolve_bot`) | `bots/cloning.py` | 6 (`routers/bots.py`, `routers/workflows.py`, `groups/lifecycle_ops.py`, `workflow_dag_tool.py`, `workflow/runtime.py`, `dynamic_assembler.py`) | yes - `workflow_dag` | yes - `/clone-engine`, `/evolve` | **LIVE** |
| `get_bot_inbox` (DM delivery, ack) | `bots/inbox.py` | 1 (`routers/bots.py`) | yes - `agent_message` | yes - `/inbox`, `/inbox/{id}/ack` | **LIVE** |
| `get_ephemeral_manager` (spawn/archive leased specialists) | `bots/ephemeral.py` | 1 (`cloning.py`) | indirectly | no | **PARTIAL** |
| `dispatch_task` (`CapabilityDispatcher`, auction-backed) | `bots/capability_dispatch.py` (32 KB) | 3 (`app/scheduler/service.py`, `routers/scheduled_tasks.py`, `planning/bridge.py` via `get_leader_dispatcher`) | indirectly | yes - `/api/bots/route-task`, `/work-discovery/match` | **LIVE** (scheduler-driven, not model-driven) |
| `LifecycleGovernor` (retire/disable/rescope) | `bots/lifecycle_governor.py` | 1 (`groups/lifecycle_ops.py`) | no | no | **PARTIAL** |
| `get_ceiling` (`AuthorityCeiling`) | `bots/authority_ceiling.py` (45 KB) | 2 outside the package (`groups/acquisition.py`, `safety/authority/scopes.py`) + 5 bots-internal | no | no | **PARTIAL** |
| **`AutonomyGuard`** (budget/cycle/depth bounds, `aggregate_descendants`) | `bots/autonomy_guard.py` (19 KB) | **0 outside `bots/__init__.py` + `autonomy_guard.py`** | no | no | **INERT** |
| **`get_dynamic_profile_store`** | `bots/dynamic_profiles.py` (36 KB) | **0 as the getter**; the class is touched by `lifecycle_governor.py`, `groups/lifecycle_ops.py`, `war_room_tool.py` | no (indirect at best) | no | **PARTIAL** - the class is reached, the **proposal/approval lifecycle is not** |
| **`SelfModificationGuard`** | `bots/self_modification.py` (19 KB) | **0 outside `bots/__init__.py` + itself** | no | no | **INERT** |
| **`AutonomousTeammateMesh` / `BotLoopGuard`** | `bots/teammate_mesh.py` | `AutonomousTeammateMesh`: 1 hit outside, and it is a *catalogue string* in `capabilities/catalog.py`, not a call. `BotLoopGuard`: **0 outside its own file.** | no | no | **INERT** |
| **`CapabilityEpochManager`** | `bots/epoch.py` (1.1 KB) | 0 outside `bots/__init__.py` + itself | no | no | **INERT** |
| `reassign_after_failure` | `bots/reassignment.py` | called from `bots/capability_dispatch.py` only | indirectly via dispatch | no | **PARTIAL** |
| **`resolve_effective_policy`** | `bots/authority_ceiling.py` | **0 outside its own file** | no | no | **INERT** |

The verification command for the `bots` INERT rows (production only, tests
excluded):

```powershell
foreach($t in 'AutonomyGuard','SelfModificationGuard','CapabilityEpochManager',
              'AutonomousTeammateMesh','BotLoopGuard') {
  "=== $t ==="
  Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
    Select-String -Pattern "\b$t\b" |
    Where-Object { $_.Path -notmatch '\\tests\\' } |
    ForEach-Object { $_.Path.Substring($pwd.Path.Length+1) } | Sort-Object -Unique
}
```

Results:

```
AutonomyGuard             -> bots\__init__.py, bots\autonomy_guard.py        (0 outside)
SelfModificationGuard     -> bots\__init__.py, bots\self_modification.py    (0 outside)
CapabilityEpochManager    -> bots\__init__.py, bots\epoch.py                 (0 outside)
AutonomousTeammateMesh    -> bots\teammate_mesh.py, capabilities\catalog.py  (0 calls)
BotLoopGuard              -> bots\teammate_mesh.py                           (0 outside)
```

`bots/authority_ceiling.py` is the starkest case in the whole audit: 44,798
bytes - the largest single file in `alpha/bots/` - containing an authority
ceiling, a scope registry, a grant-approval resolver, a taint tracker, a receipt
chain, and a blast-radius limiter. The only production consumers of `get_ceiling`
outside the package are `groups/acquisition.py` and `safety/authority/scopes.py`.
`resolve_effective_policy`, the function that actually combines ceiling + scopes
+ taint into the policy an agent runs under, has **zero** callers outside its own
file.

---

## The team / group tools

All five are registered in `BUILTIN_TOOLS`, and all five implement **every**
action they advertise. This is the healthiest corner of the team surface.

| Tool | `@tool` name | Registered | Actions advertised | Actions implemented | Backing module | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `group_chat_tool` | `group_chat` | yes (`tools.py` import + list) | 7 | 7 (`list, create, send, history, propose_vote, cast_vote, tally_vote`) | `alpha.groups.service.get_group_chat_service` (8 outside callers) | **LIVE** |
| `blackboard_tool` | `blackboard_record_evidence`, `blackboard_query` | yes (`tools.py:29,30` + list) | 2 | 2 | `alpha.blackboard.BlackboardEngine` | **LIVE** |
| `kanban_board_tool` | `kanban_board` | yes | 6 | 6 (`create_task, claim, submit_review, approve, request_changes, list`) | `alpha.kanban.store.get_kanban_store` (2 outside callers) | **LIVE** |
| `company_tool` | `company_os` | yes | 26 | 26 | `alpha.company.organization.get_autonomous_company_engine` (2 outside) | **LIVE** |
| `stigmergic_mesh_tool` | `emit_stigmergic_event`, `query_stigmergic_traces` | yes (`tools.py:236,237`) | 2 | 2 | `alpha.blackboard.stigmergic_event_mesh` | **LIVE** |
| `discipline_team_tool` | `consult_plan_gap_analysis`, `review_plan_invariant_gate`, `dispatch_discipline_worker` | yes (`tools.py:277-279`) | 3 | 3 | `alpha.orchestration.*` | **LIVE** |
| `war_room_tool` | `war_room` | yes | - | - | `alpha.groups.war_room` (also uses `resolve_strategy`) | **LIVE** |

```powershell
# action-list vs action-branch parity, for each of the six
foreach($t in 'group_chat_tool','blackboard_tool','kanban_board_tool',
              'company_tool','stigmergic_mesh_tool','discipline_team_tool') {
  $p = "backend/packages/harness/alpha/tools/builtins/$t.py"
  $advertised = (Select-String -Path $p -Pattern '^\s+"[a-z_]+",\s*$').Count
  $implemented = (Select-String -Path $p -Pattern 'action == "').Count
  "{0,-26} advertised={1,-3} implemented={2}" -f $t, $advertised, $implemented
}
```

```
group_chat_tool         advertised=7   implemented=7
blackboard_tool         advertised=0   implemented=0   (2 @tool defs, no action list)
kanban_board_tool       advertised=6   implemented=6
company_tool            advertised=26  implemented=26
stigmergic_mesh_tool    advertised=0   implemented=0   (2 @tool defs, no action list)
discipline_team_tool    advertised=0   implemented=0   (3 @tool defs, no action list)
```

### There is no `standup_meeting_tool`, and no compass tool

The assignment asked about `standup_meeting_tool` and "any compass/orchestration
tool". Neither exists as a model tool.

```powershell
Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
  Select-String -Pattern 'standup_meeting|compass'
```

- **`standup_meeting_tool` does not exist.** Standup exists in two other places:
  `alpha/projects/standup_engine.py` (`StandupEngine.generate_standup`, called
  from `routers/projects.py:1016` and surfaced in a project payload at line
  1084), and `alpha/company/attendance.py:162`
  (`generate_roll_call_digest`, exposed as `GET /api/company/attendance/roll-call`).
  Both are **HTTP-only** - neither is model-reachable.
- **No `compass` tool exists.** The only hit for "compass" in the whole backend
  is a comment in `alpha/sandbox/env_policy.py:39` about `COMPASS_*` env vars
  being incidentally "incidental names that merely contain PASS".
- The closest orchestration surfaces are `alpha/orchestration/autopilot.py` (via
  the discipline-team tools) and `alpha/orchestrator/domain_executors.py` (via
  `SubagentExecutor`).

### A third live path to the swarm: `ExecutiveAutopilot`

There is a third way a run reaches the team tools, and it is the one that
matters most for the user's question because it is prompt-driven and
unattended.

`resolve_agent_preset` is not the only preset selector. With
`agent_preset="auto"` (or a `None` name and a prompt), it delegates to
`ExecutiveAutopilot.resolve_preset(prompt)`
(`orchestration/autopilot.py:180`), which pattern-matches the prompt and can
recommend **`autonomous_swarm`** at `autopilot.py:103-113`:

```python
if any(re.search(pat, text) for pat in self.SWARM_PATTERNS):
    return AutopilotPlan(
        intent="swarm",
        recommended_preset="autonomous_swarm",
        requires_subagents=True,
        requires_tools=["task", "agent_message", "group_chat", "kanban_board", "swarm"],
        suggested_discipline="swarm_coordinator",
        confidence=0.92,
        ...)
```

So there are three ways a run gets the team tools: `body_autonomous=True`
(`services.py:1619`), an explicit enabling preset, or **`auto` preset
resolution on a prompt that matches `SWARM_PATTERNS`**. The third is the
interesting one because it is prompt-driven and unattended, and it is the
closest thing in the codebase to the "get three specialists to review this"
request the user described.

`AutopilotPlan.requires_tools` names `swarm` explicitly, and it is delivered by
`SUBAGENT_TOOLS` (gated on `subagent_enabled`, which the autopilot plan sets
`True` via `requires_subagents=True` and which the `autonomous_swarm` preset
also sets). **The chain works.** What it produces is a team of single-shot
tool-less completions, per finding 1.

### `alpha/roles/**` does not exist

There is no `alpha/roles` package, no `roles.py` module, and no
`alpha.roles` reference anywhere in `backend/**` or in `docs/**`. The role /
positioning system that *does* exist is `alpha/bots/templates.py`
(`get_template` / `list_templates`, exposed at `GET /api/bots/templates`) plus
`alpha/subagents/specialists.py` (`SpecialistRoleArchetype` enum +
`ARCHETYPE_REGISTRY`) and `alpha/company/archetypes.py`
(`GET /api/company/archetypes`). All three are LIVE. I flag this only because
the assignment asked, and a reader should not go looking for `alpha/roles/`.

---

## `alpha/company/**` - 17 modules, 138 KB - and the Gateway routes

`app/gateway/routers/company.py` is registered (`app.py:1178`) and exposes
**28 routes**, and `company_os` gives the model the same 26 operations. This is
the most completely wired subsystem in the entire team surface: **zero
INERT mechanisms**.

| Route group | Routes | Backing engine | Verdict |
| --- | --- | --- | --- |
| Company lifecycle | `archetypes`, `bootstrap`, `status`, `{org_id}/pause`, `{org_id}/resume` | `AutonomousCompanyEngine` | **LIVE** |
| Kanban | `kanban/sync`, `kanban/tasks`, `kanban/tasks/{id}/update`, `kanban/events/log`, `kanban/events`, `kanban/agent-check-in` | `company/kanban.py::CompanyKanbanEngine` (6 methods, all routed) | **LIVE** - also read by the frontend `lib/kanban.ts` and `KanbanSection.tsx` |
| Groups | `groups`, `groups/create`, `groups/message`, `groups/{channel_id}/messages` | `company/group_chat.py` | **LIVE** |
| Attendance | `attendance/pulse`, `attendance/check-and-heal`, `attendance/roll-call`, `attendance/status` | `company/attendance.py::AttendanceLedgerEngine` (7 methods) | **LIVE** - `roll-call` is read by frontend `lib/comm.ts:167` |
| Production line | `production-line/submit`, `production-line/advance` | `company/production_line.py::ProductionLineEngine` (5 methods) | **LIVE** |
| Strategy | `strategy/replan` | `company/strategy.py::StrategicPlanningEngine.replan_strategy` | **LIVE** |
| Executive | `executive-digest` | `company/executive.py::ExecutiveIntelligenceLayer.generate_digest` | **LIVE** |
| Evolutionary journal | `evolution-journal` | `company/self_improvement.py` | **LIVE** |
| Work discovery | `discover-work`, `kpis` | `company/discovery.py`, `company/kpi.py` | **LIVE** |
| Responsibility | `responsibilities/transfer` | `company/responsibility.py` | **LIVE** |
| Retrospective | `retrospective` | `company/organization.py::run_retrospective` | **LIVE** |
| Swarm bridge | `swarm/bots` | `company/swarm_bridge.py::SwarmLocalBridge` | **LIVE** (the one place company meets swarm) |

The frontend `TeamOpsSection.tsx` has an explicit honesty comment about the
company surface: line 513 says `GET /api/company/status` answers **404 "No active
organizations"** on a fresh install. So the routes exist and are wired; the
*data* requires a `bootstrap` first. That is a real distinction this audit draws
repeatedly: **a route that exists is not a route that answers with data.**
`TeamOpsSection` deliberately does not treat the 404 as an error.

### Gateway route registration is complete

Every router exported from `app/gateway/routers/__init__.py` is
`include_router`'d. Zero orphans.

```powershell
$imported = Select-String -Path app\gateway\routers\__init__.py -Pattern '^\s{4}([a-z_]+),' |
  ForEach-Object { $_.Matches[0].Groups[1].Value } | Sort-Object -Unique
$inc = (Select-String -Path app\gateway\app.py -Pattern 'include_router\((\w+)' -AllMatches).Matches |
  ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
$imported | Where-Object { $_ -notin $inc }
# (no output)
```

Relevant route counts on the team surface: `swarms.py` 24, `bots.py` 36,
`company.py` 28, plus `subagents.py`, `subagent_control.py`,
`subagent_batches.py`, `groups.py`, `agent_messages.py`, `war_rooms.py`,
`deliberation.py`, `council.py`, `a2a.py`, `supervision.py`.

---

## A clean negative result worth recording

**Zero tools are unregistered.** I checked every `@tool(...)` definition in
`alpha/tools/builtins/` against both `BUILTIN_TOOLS` and `SUBAGENT_TOOLS`:

```powershell
# 125 @tool definitions across builtins/*.py, 138 registered identifiers
# 8 defined-but-not-in-either-list: batch_task, batch_status, cancel_batch,
#   and 5 from code_agentic_core.py
# -> all 8 are added by a later conditional .extend()/.append() in get_tools()
# => 0 genuinely unregistered
```

The 8 are appended at `tools.py:476, 489` and elsewhere, gated on
`is_mcp_task_runtime_available()`, `subagent_enabled`, and
`is_subagent_batch_runtime_available()`. So the tool registry is not where the
inert machinery lives. **The problem is downstream of registration.**

---

## THE BIGGEST FINDING: the `tool_groups` preset mechanism is dead config

`AgentPresetConfig` has a `tool_groups: list[str] | None` field, and six presets
name groups. **Nothing resolves a group name to a set of tools.** The single
resolver in the codebase has zero callers - not even a test.

```powershell
# The resolver, app_config.py:940
def get_tool_group_config(self, name: str) -> ToolGroupConfig | None:
    return self._tool_groups_by_name.get(name)

# Its callers, across app/** and packages/** INCLUDING tests:
Get-ChildItem -Recurse -File -Filter *.py -Path backend/app,backend/packages |
  Select-String -Pattern 'get_tool_group_config' |
  ForEach-Object { "{0}:{1}" -f $_.Path, $_.LineNumber }

packages\harness\alpha\config\app_config.py:500   # a comment mentioning it
packages\harness\alpha\config\app_config.py:940   # the def itself
# => 2 hits, both in the file that defines it. ZERO CALLERS.
```

`tool_groups_by_name` is built once in `app_config.py:769-774` and then only
read by that dead getter. The index exists, the names are validated into Pydantic
models, and nothing ever looks an entry up.

Worse, the two namespaces **do not even overlap**:

| Source | Group names |
| --- | --- |
| Presets in `agent_preset_config.py` | `code`, `web`, `planning`, `bash`, `ast_grep`, `git`, `editing`, `memory`, `testing`, `retrieval`, `epistemic`, `discipline`, `governance`, `security`, `gap_analysis`, `mission`, `queue`, `provenance`, **`swarm`**, **`subagents`**, **`blackboard`**, **`a2a`**, **`company`** |
| `config.example.yaml` `tool_groups:` | `web`, `file:read`, `file:write`, `bash`, `browser`, `knowledge` |

The only overlap is `web` and `bash`. **The `autonomous_swarm` preset's five
groups - `swarm`, `subagents`, `blackboard`, `a2a`, `company` - do not exist in
the shipped config at all.** And `ToolGroupConfig` is
`name` + `extra="allow"` with **no `tools:` list field**, so even if the getter
were called, a group carries no tools to enable.

### What this means for the user's question

`autonomous_swarm` is a preset that reads as "here is the specialist team,
switched on". It is inert in two independent ways: nothing resolves its group
names, and its group names are not declared. The tools it would enable are
separately gated on `subagent_enabled`, which *is* live - so **the team tools
are reachable by an `autonomous_swarm` run today, but not because of
`tool_groups`; they arrive through `SUBAGENT_TOOLS` because
`subagent_enabled=True`.** The preset's headline feature does nothing.

---

## Answers

### 1. Is there a working specialist team today?

**Yes, but it is a 3-member team reached through one tool, and the swarm is not
on it.** Tracing "get three specialists to review this design":

1. The user asks the lead agent. `app/gateway/services.py` sets
   `subagent_enabled` **only** if `body_autonomous` (line 1619 / 1717). The
   frontend sends **no** `subagent_enabled` key at all
   (`Select-String 'subagent_enabled' frontend/src` -> 0 hits), and
   `AgentPresetConfig` defaults it to `False` for `STANDARD_PRESET_NAME`.
   So on a default chat run the team tools are **not bound**.
2. If the run *is* autonomous, or the `plan` / `deep_code` / `research` /
   `discipline` / `mission_director` / `autonomous_swarm` preset is selected, or
   `agent_preset="auto"` + a prompt matching `ExecutiveAutopilot.SWARM_PATTERNS`
   (`autopilot.py:103-113`, which recommends `autonomous_swarm` at confidence
   0.92), then `get_tools` extends `SUBAGENT_TOOLS` (`tools.py:487`), which is
   exactly `task, subagent_control, agent_message, agent_observe, group_chat,
   kanban_board, a2a, swarm, company`.
3. The lead's own prompt then *actively discourages* fan-out. `prompt.py:478`:
   "Subagents are optional. **Default to direct execution.** Do not delegate
   merely because a task is complex...". Line 491 lists "Duplicate discovery" and
   line 493 "Coordination burden" as reasons not to. Line 420: "Use one
   specialized subagent only when its configured capability provides material
   benefit unavailable on the direct path."
4. **The lead prompt never mentions the `swarm`, `company_os`, `blackboard`, or
   `deep_agent` tools at all.** Verified: 0 mentions of each in
   `agents/lead_agent/prompt.py`, while `group_chat`, `kanban_board` and
   `bot_roster` *are* documented at lines 707-711. So on the path where the
   tools are bound, the model is not told the deepest ones exist.
5. So the realistic outcome is 1-3 `task` calls to `SubagentExecutor` -
   a genuinely capable agent with tools - **and no swarm, no consensus, no
   deliberation, no stigmergy.** `swarm(action='spawn', mode='debate')` would
   give a fixed 3-node template whose workers are single-shot tool-less
   `model.invoke()` calls; that is a *worse* team than three `task` calls, and
   the prompt gives the model no reason to prefer it.

**Net: three specialists do review a design - via three `task` calls - and only
when subagents are enabled. The swarm engine, the whole point of the team
subsystem, is not what answers.** Two independent reasons, either of which alone
would be sufficient: the swarm workers cannot use tools, and the lead is never
told the tool exists.

### 2. The single highest-value thing to switch on

**Give the swarm workers the subagent executor instead of a bare
`model.invoke()`.** One change, in `alpha/swarm/worker.py`.

Today `_deliver_objective` (`worker.py:83-102`) is:

```python
response = model.invoke([SystemMessage(...), HumanMessage(...)])
```

and `SubagentExecutor` - already an 8-caller, 99 KB, fully tested agent loop
with tools, receipts, acceptance criteria, capacity accounting, and an isolated
event loop - is one import away and is *not* used. Replacing the
`EphemeralSubagentWorker` body with a `SubagentExecutor` call would:

- turn a swarm worker from a text generator into a **real agent**, so
  `_decompose_debate`'s three "specialists" actually investigate;
- make `swarm`, `task`, and `subagent_control` one capability instead of three,
  which is also what stops the lead prompt needing to document both;
- make `requires_consensus=True` meaningful, because `SwarmAggregator` and
  `run_deliberation` (Wald) are already LIVE and currently operate on prose
  summaries from workers that could not look at anything;
- require **no** new route, no new tool registration, no new preset, and no
  change to `RunManager` - the executor has its own lifecycle and returns a
  `SubagentResult`.

Ranked alternatives, if that is too large:

| # | Change | Turns inert -> live | Risk |
| --- | --- | --- | --- |
| 2 | Call `get_archetype_template` from `generate_dynamic_role` instead of reading `ARCHETYPE_REGISTRY` directly | 1 accessor | trivial - behaviour-identical |
| 3 | Implement `swarm_status_tick` for real (delete the `# noqa: F401` stub, read `coordinator.list_swarms()`) | 1 loop, and removes a fabricated-status return | low - it becomes honest either way |
| 4 | Wire `AutonomyGuard` into `CapabilityDispatcher.dispatch` | 19 KB of budget/cycle/depth bounds | **medium** - it can now *refuse* dispatches that succeed today; needs its bounds checked against `config.example.yaml` first |
| 5 | Delete `swarm/triggers.py` or wire it | 1 module | low to delete, but it is the only autonomous-swarm trigger, so deleting loses intent |
| 6 | Give `autonomous_swarm` preset real group names **and** implement `get_tool_group_config` consumption | makes a documented preset honest | medium - needs a `tools:` field on `ToolGroupConfig` and a fail-closed unknown-group check |

### 3. What must NOT be wired, and why

**`AsyncSwarmRunner` must not become a second run lifecycle owner.** This is the
sharpest constraint in the assignment, and the code already violates it.
`RunManager` is the sole lifecycle owner and `SafeRunRecoveryService` the only
safe-continuation authority; `start_background_swarm` (`runner.py:653`) keeps
its own module-global `_ACTIVE_SWARM_TASKS` / `_ACTIVE_SWARM_THREADS`,
spawns a **daemon thread** with `asyncio.run(...)`, or a bare
`asyncio.create_task`. Consequences:

- A daemon thread is **not** joined by Gateway shutdown, so an in-flight swarm
  is killed mid-task with no event, no checkpoint, and no recovery - exactly
  the failure `SafeRunRecoveryService` exists to prevent.
- The registries are **process-local and in-memory**, so a restart loses every
  running swarm, and a second Gateway process has no idea one exists. This is
  the same "single-process JSON state is never exactly-once" constraint
  `AGENTS.md` states for the peer network.
- `coordinator.checkpoint()` is called by the runner, so a swarm can leave
  half-completed task state on disk with no run record to reconcile against.

**So: do not build a team runtime that owns runs.** Concretely:

1. Do **not** add a new supervisor loop that spawns or resumes swarms. Note that
   `loops.py:133` deliberately does not - its comment says "Swarms are started on
   demand by the swarm tool/HTTP surface; a periodic loop must not spawn agent
   work on its own." That decision is correct. Leave the loop as telemetry, but
   make it *real* telemetry rather than a literal string.
2. Do **not** let a swarm start without a `RunManager` run id threaded through
   it. Today `create_swarm` takes `owner_id` but no run id, so there is no way
   to correlate a swarm to the run that asked for it.
3. Do **not** wire `SafeRunRecoveryService` into the swarm to "make it safe" -
   that would make the swarm a continuation authority. The correct direction is
   the reverse: the swarm should be *driven* by a run, so the run's existing
   recovery covers it.
4. Do **not** wire `bots/autonomy_guard.py`'s budget accounting as a *second*
   budget alongside `RunManager`'s. If a swarm fan-out is bounded, the bound
   belongs in the run's budget, with the guard reading it - not owning a
   parallel one.
5. Do **not** wire `bots/self_modification.py` (INERT, and a team runtime is
   exactly the context where a self-modifying member is least reviewable) until
   its blast-radius limiter has a caller.
6. `alpha/swarm/triggers.py` (`AutonomousWorkTrigger`) is the one INERT module
   whose *entire purpose* is to spawn agent work on a schedule. That is precisely
   the forbidden pattern. It should be deleted, not switched on.

---

## Ranked shortlist of inert machinery worth switching on

| Rank | Mechanism | Size | Verdict now | Risk of wiring | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | `SubagentExecutor` **into `swarm/worker.py`** | - | worker bypasses it | **low-med** - it adds tool use and cost to every swarm task; the win is that the executor is already governed |
| 2 | `AutonomyGuard` -> `CapabilityDispatcher.dispatch` | 19 KB | INERT | **med** - can newly refuse dispatches; check bounds against config first |
| 3 | `SelfModificationGuard` (add a caller) | 19 KB | INERT | **high** - do not wire into any team runtime |
| 4 | `tool_groups` resolution (`get_tool_group_config` + `ToolGroupConfig.tools`) | - | dead config | **med** - needs a fail-closed unknown-group rule; 5 preset group names are undeclared |
| 5 | `DynamicProfileStore` propose/approve/install lifecycle | part of 36 KB | PARTIAL - class reached, lifecycle not | **med** - introduces an approval queue; needs an owner |
| 6 | `AuthorityCeiling.resolve_effective_policy` | part of 45 KB | INERT | **med** - it is the function that *should* gate everything; turning it on may refuse existing profiles |
| 7 | `AutonomousWorkTrigger` | 2 KB | INERT | **do not wire** - violates the single-lifecycle-owner rule. Delete. |
| 8 | `CapabilityEpochManager` | 1.1 KB | INERT | low - 1.1 KB, prompt staleness marker |
| 9 | `AutonomousTeammateMesh` + `BotLoopGuard` | 4 KB | INERT | **med** - mesh has a `LoopDetectedError`; unbounded bot-to-bot DM loops are a known failure mode |
| 10 | `build_guardrail_soul` / `survey_workspace` outside `forge` | - | PARTIAL (forge-only) | low - already correct inside forge; exposing standalone needs a caller with intent |
| 11 | `swarm_status_tick` real implementation | 15 lines | INERT (fabricated) | trivial |
| 12 | `get_archetype_template` dead accessor | 8 lines | INERT | trivial - or delete it |
| 13 | `DeepHandoff` contract external entry | 13 KB | PARTIAL | low - internally consistent already |

---

## Counts summary

| Surface | Modules | INERT | PARTIAL | LIVE | UNREACHABLE |
| --- | --- | --- | --- | --- | --- |
| `alpha/swarm/**` | 24 | 2 | 0 | 19 | 0 |
| `alpha/subagents/**` | 34 | 1 | 1 | 10 | 0 |
| `alpha/bots/**` | 34 | 5 | 7 | 11 | 0 |
| `alpha/company/**` | 17 | 0 | 0 | 12 | 0 |
| team tools | 6 modules / 15 tools | 0 | 0 | 15 | 0 |
| `alpha/roles/**` | **does not exist** | - | - | - | - |
| **total** | **109** | **8** | **8** | **67** | **0** |

Two counts in this table are worth stating as *measured* rather than
*estimated*, because they are the ones the answer rests on:

```powershell
# module counts
cd backend/packages/harness/alpha
foreach($d in 'swarm','subagents','bots','company') {
  $py = (Get-ChildItem -Recurse -File -Filter *.py $d).Count
  $kb = [math]::Round(((Get-ChildItem -Recurse -File -Filter *.py $d |
        Measure-Object Length -Sum).Sum)/1kb)
  "{0,-12} {1,3} py files, {2,4} KB" -f $d, $py, $kb
}
# swarm      24 py files,  314 KB
# subagents  34 py files,  437 KB
# bots       34 py files,  528 KB
# company    17 py files,  138 KB

# the 150-file headline
Get-ChildItem -Recurse -File -Filter *.py -Path app,packages |
  Where-Object { $_.FullName -notmatch '\\tests\\' -and $_.Name -notlike 'test_*' } |
  Select-String -Pattern 'alpha\.(swarm|subagents|bots|company)' |
  ForEach-Object { $_.Path } | Sort-Object -Unique | Measure-Object
# Count = 150
```

The LIVE/INERT/PARTIAL column counts are **per-mechanism, not per-module** -
one module can hold several mechanisms (`bots/authority_ceiling.py` alone holds
both a PARTIAL `get_ceiling` and an INERT `resolve_effective_policy`), so those
columns do not sum to the module count and are not meant to.

Verdict definitions used throughout, restated so the numbers are checkable:

- **LIVE** - at least one production call site outside the mechanism's own
  module, and at least one concrete route or tool binding that reaches it.
- **PARTIAL** - called, but only from a narrow or single path (e.g. reachable
  only inside `forge`, or only from a router and not the model).
- **INERT** - implemented, and its own `__init__.py` exports it, but **no**
  production reference outside its own module.
- **UNREACHABLE** - no route and no caller. **Count: zero in this surface.** Every
  mechanism in the team subsystem is at least imported somewhere; the failure
  mode here is *imported but never called*, not *never imported*.

**Nothing in the team surface is UNREACHABLE, and almost nothing is INERT.** The
real answer to the user's question is therefore *not* "a lot of unwired
machinery". It is: **the machinery is wired, the registry is complete, and the
gap is one hop in the middle - the swarm's workers are not agents.**

---

## Bugs found (reported, not fixed - no product code was touched)

1. **`app/gateway/autonomy/loops.py:133-146` - `swarm_status_tick` returns a
   fabricated status.** It imports `AsyncSwarmRunner` with `# noqa: F401`,
   never uses it, and returns `{"active_swarms": "on-demand-only", "note": ...}`.
   A supervisor loop whose entire output is a hard-coded string. Same class as
   the "410 of 426 slash commands return `status="success"` for doing nothing"
   finding. This is a **live honesty bug**, not dead code: the loop is registered
   and its return value is real data to whatever renders it.
2. **`get_tool_group_config` (`app_config.py:940`) has zero callers, and
   `ToolGroupConfig` has no `tools` field.** The `tool_groups` preset system is
   documented config that cannot work. The five groups named by the
   `autonomous_swarm` preset (`swarm`, `subagents`, `blackboard`, `a2a`,
   `company`) are not declared in `config.example.yaml` either, and the six that
   *are* declared share only `web` and `bash` with the presets.
3. **`get_archetype_template` (`subagents/specialists.py:121`) is dead.**
   `generate_dynamic_role` reads `ARCHETYPE_REGISTRY` directly, so the accessor
   and its `__init__.py` re-export are the only references.
4. **`alpha.swarm`'s `AutonomousWorkTrigger` is dead** and is the one inert
   module that exists specifically to start agent work unattended.

## What I deliberately did not do

- **Did not start or restart the stack.** Nothing was listening on 2026/3000/
  8001/8002, so the preferred live-probe method was unavailable. Every
  "reachable" claim here is a static claim about the registration/import graph.
  A live confirmation that `POST /api/swarms` then `POST /{id}/run-async`
  actually completes a task remains **outstanding** and is the first thing
  worth doing.
- **Did not edit any product code**, per the assignment. Items 1-4 above are
  reported, not fixed.
- **Did not commit.** The working tree on `agent/teamwire` carries exactly one
  new file: `docs/TEAM_WIRING_AUDIT.md`.
