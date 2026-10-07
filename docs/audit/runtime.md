# Agent-core audit — runtime / agents / subagents / workflow / orchestrator

All findings below are MEASURED via ripgrep (search_files) over
`backend/packages/harness/alpha`, `backend/app`, `backend/tests`. No code was
executed; counts are ripgrep match counts, not runtime observations.

## 1. Does orchestrator/loop.py drive the real DynamicWorkflowEngine?

YES — it imports and composes the engine, no parallel path.

    orchestrator/loop.py:41  from alpha.orchestrator.executors import get_executor_registry
    orchestrator/loop.py:42  from alpha.orchestrator.mode_mapper import MODES, map_paradigm
    orchestrator/loop.py:43  from alpha.orchestrator.replay import replay_run
    orchestrator/loop.py:44  from alpha.workflow.events import WorkflowEvent
    orchestrator/loop.py:45  from alpha.workflow.models import (
    orchestrator/loop.py:52  from alpha.workflow.runtime import DynamicWorkflowEngine

    orchestrator/loop.py:132  def __init__(self, engine: DynamicWorkflowEngine | None = None)
    orchestrator/loop.py:133      self.engine = engine if engine is not None else DynamicWorkflowEngine()
    orchestrator/loop.py:170  run = self.engine.start_run(...)          # delegated
    orchestrator/loop.py:247  run = self.engine.execute_step(...)        # delegated
    orchestrator/loop.py:231  run = self.engine.get_run(run_id)          # delegated
    orchestrator/loop.py:279  self.engine.events.emit("workflow_failed", ...)

What the kernel adds is policy only: a per-run `threading.Lock` claim
(`loop.py:139-155`), a bounded wave loop (`loop.py:202-234`), and a
defense-in-depth `_fail_closed` (`loop.py:267-280`) that the docstring
itself says is redundant because the engine already fail-closes inside
`execute_step` (`workflow/runtime.py:993 _execute_step_locked`).
Notably `workflow/runtime.py` contains ZERO langgraph references
(ripgrep `langgraph` in that file: 0 matches) — the DWE is a hand-rolled
DAG scheduler, not a LangGraph graph. So the orchestrator is honest about
what it composes; there is no divergence risk between loop.py and the
DWE because the DWE owns all node execution.

Caveat (SPECULATIVE, not measured): `dynamic_service.py` and
`dynamic_bridge.py` construct their own `DynamicWorkflowEngine()` instances
(`workflow/dynamic_bridge.py:112`, `workflow/time_travel.py:418`,
`orchestrator/replay.py:192`), so if two of them are live in one process
there are multiple in-memory run registries. I did not trace which one the
REST router binds.

## 2. Every file that builds a LangGraph StateGraph + its state TypedDict

Production code — exactly ONE builder:

1. `runtime/checkpoint_state.py:60-66` — `build_state_mutation_graph()`
   * state schema: caller-supplied `state_schema`, else
     `alpha.agents.thread_state.get_thread_state_schema(mode, snapshot_frequency)`
     (`agents/thread_state.py:416`), which returns `ThreadState`
     (`agents/thread_state.py:280`, a `TypedDict` subclassing
     `langchain.agents.AgentState`) or `DeltaThreadState`
     (`agents/thread_state.py:396`, `messages: DeltaChannel`) or a
     synthesized `DeltaThreadState_f<N>` (`agents/thread_state.py:430`).
   * nodes: ONE — `as_node` -> `_finish_state_mutation`
     (`checkpoint_state.py:32-33`, returns `{}`).
   * edges: none; `set_entry_point(as_node)` + `set_finish_point(as_node)`
     (`checkpoint_state.py:64-65`). This is a state-write trampoline for
     `update_state(as_node=...)`, NOT an agent loop.

The actual agent graph is built by a vendor primitive, not by Alpha:
`agents/factory.py:19 from langchain.agents import create_agent` and
`agents/factory.py:184 return create_agent(...)` with
`state_schema=effective_state` (`:189`) and `checkpointer=checkpointer`
(`:190`). The return type is `CompiledStateGraph`
(`agents/factory.py:34,81`). Alpha never calls `add_node`/`add_edge`/
`add_conditional_edges` in product code anywhere in the repo (ripgrep for
`add_node(|add_edge(|add_conditional_edges(` : 0 exact matches in
product code; 227 case-insensitive matches are `re.compile(` noise in 43
files).

CONSEQUENCE (MEASURED): the main agent loop is NOT a hand-authored
LangGraph state machine. Node topology lives inside
`langchain.agents.create_agent`; Alpha contributes only middleware and the
state TypedDict. Interrupt support exists but is thin: only 8 matches for
`interrupt(|Command(resume|__interrupt__` across all of product code, in
`runtime/runs/worker.py` (4), `runtime/serialization.py`,
`observability/writer.py`, `observability/trace/instrumentation.py`,
`tui/app.py`.

Test/benchmark-only StateGraph builders (excluded from "production"):
`tests/test_cached_history_saver_integration.py:79,96`,
`tests/test_checkpoint_state.py:279`, `tests/test_checkpoint_retention_contract.py:78`,
`tests/test_custom_events.py:31`, `tests/test_delta_channel_state.py:399`,
`tests/test_delta_channel_checkpointers.py:70`, `tests/test_gateway_checkpoint_mode.py:53`,
`tests/test_gateway_run_drain_shutdown.py:346`, `tests/test_interrupt_serialization.py:18`,
`tests/test_langgraph_studio_routes.py:25`, `tests/test_mcp_context_headers.py:266,572,695`,
`tests/test_mcp_sync_wrapper.py:280`, `tests/test_run_worker_delta_resume.py:66,404`,
`tests/test_run_worker_rollback.py:381,1831,1873`,
`tests/test_subagent_events_e2e.py:136,242`,
`tests/test_subagent_executor.py:3559,3652`, `tests/test_threads_checkpoint_mode.py:39,163`,
`tests/test_worker_stream_subgraph_namespace.py:383`,
`scripts/benchmark/checkpoint/bench_tool_result_probe.py:86`,
`scripts/benchmark/checkpoint/bench_channels.py:220`.

## 3. Non-test importers of alpha.runtime.* and alpha.workflow.*

Ripgrep, pattern `^\s*from alpha\.runtime\.` / `^\s*import alpha\.runtime`
(same for workflow), grouped by tree.

### alpha.runtime.* — 210 non-test files, 528 import lines
(under `packages/harness/alpha/` = 176 files; `backend/app/` = 28 files;
plus intra-`runtime/` self-imports = 74 lines mostly from
`runtime/resilience/__init__.py:74`.)

Inside `alpha/runtime/` (76 files): checkpoint_state, checkpointer/{provider,
async_provider,cached_saver}, checkpoint_cache/{__init__,provider,memory,redis},
context_compaction, control, escalation, execution_mode, goal, journal,
network/{__init__,monitor,probe,states,wait_registry}, resilience/{__init__,budget,
circuit,clock,config,convergence,errors,idempotency,recovery,retry}, rlm/engine,
runs/{manager,worker,runs/store/*}, selfheal/{__init__,run_stall,watchdog},
serialization, sentinel/{__init__,cli,loop,runner,scheduler,commit,sources/*},
sessions/{__init__,states}, side_effects/{__init__,recorder,ledger},
store/{__init__,provider,async_provider}, stream_bridge/{__init__,async_provider},
supervisor/{__init__,policy,supervisor}.

Consumers elsewhere in product code (highest coupling):
`agents/factory.py:4`, `agents/thread_state.py:2`,
`agents/lead_agent/agent.py`, `agents/lead_agent/prompt.py`,
`agents/task_continuity/archive.py`, `agents/memory/tools.py`,
`agents/memory/user_model.py`, `agents/memory/summarization_hook.py`,
13 `agents/middlewares/*.py`, `client.py:3`, `subagents/{executor,lifecycle,step_events}.py`,
`checkpoint_patches.py:2`, `constants.py`, `errors/registry.py`,
`persistence/{run,projects,thread_meta,feedback,agents,side_effects}/sql.py`,
`persistence/models/__init__.py`, `sandbox/{tools,middleware}.py`,
`mcp/{tools,user_scoped_auth,context_headers}.py`, `tools/builtins/*.py` (18 files),
`tui/{app,persistence,session}.py`, `bots/{capability_dispatch,handoff,self_modification,failure_reasons}.py`,
`config/{app_config,agents_config,network_resilience_config}.py`,
`capabilities/{catalog,eligibility,honesty}.py`, `rsi/switchboard.py`,
`memory/cognitive/engine.py`, `intelligence/snapshots.py`, `swarm/aggregator.py`,
`observability/{ambient,taxonomy,trace/*}`, `models/cost_governor.py`,
`uploads/manager.py`, `guardrails/middleware.py`, `groups/activity.py`,
`utils/{messages,oneshot_llm}.py`, `community/{e2b_sandbox,aio_sandbox}`,
`skills/security_scanner.py`, `commands/{module_a_handlers,backend_handlers}.py`.

Gateway surface (`backend/app/`, 28 files, 65 import lines):
`gateway/services.py:11`, `gateway/deps.py:7`, `gateway/routers/threads.py:10`,
`gateway/routers/workflows.py:7` (the ONLY app file importing `alpha.workflow.*`),
`gateway/run_models.py`, `gateway/run_recovery.py:2`, `gateway/path_utils.py`,
`gateway/{auth_disabled,auth_middleware,internal_auth}.py`,
`gateway/routers/{agents,artifacts,browser,integrations,memory,projects,skills,swarms,teams,uploads}.py` (1 each),
`gateway/routers/thread_runs.py:3`, `channels/{manager:2,feishu,dingtalk}.py`,
`mcp_tasks/service.py:4`, `scheduler/service.py`.

### alpha.workflow.* — 47 non-test files, 121 import lines
Inside `alpha/workflow/` (36 files, 116 lines): `__init__.py:21`,
`runtime.py:12`, `registry/__init__.py:12`, `dynamic_bridge.py:7`,
`event_log.py:3`, `time_travel.py:4`, `patch.py:3`, `self_improvement.py:3`,
`plan_graph.py:2`, `dynamic_decomposer.py:2`, `dynamic_assembler.py:2`,
`templates.py:2`, `scheduler.py:2`, `router.py:2`, `graph_diff.py:2`,
`verification.py:3`, `schemas.py:1`, `replanner.py:1`, `execution.py:1`,
`observability.py:1`, `patch_validator.py:1`, `registry/{base,capabilities,
commands,identity,manifest_source,memory,mcp,models,skills,tools,wiring,engines}.py`.

Consumers outside workflow/: `orchestrator/{loop:3,replay:4,dynamic_service:6,
executors:1,domain_executors:1,mode_mapper:1}.py`,
`tools/builtins/workflow_dag_tool.py:3`, `intelligence/self_inventory.py:1`,
`orchestration/intent.py:1`, `ops/config_diagnosis.py:1`, and exactly one gateway
file: `app/gateway/routers/workflows.py`.

Observation (MEASURED): `alpha.workflow.*` has 47 importers vs
`alpha.runtime.*`'s 210 — a 4.5x gap. The workflow DAG engine is reachable
from exactly one HTTP router. No importer of `alpha.workflow.*` exists in
`alpha/agents/` (ripgrep: 0 files).

## What I could NOT verify

- No code executed. I did not instantiate a graph, run a workflow, or run
  pytest; all of the above is static ripgrep evidence.
- Whether the resume path (`workflow/runtime.py:661 adopt_replayed_run`,
  `orchestrator/replay.py`) can re-enter a run at a different state than it
  checkpointed. Needs a live replay test.
- Whether multiple `DynamicWorkflowEngine()` instances coexist in one
  process (item 1 caveat) — needs runtime instrumentation.
- Exact counts for the 4 individual `alpha/runtime` `AGENTS.md` lines and
  whether `runtime/AGENTS.md:5-6` ("RunManager remains the sole lifecycle
  owner") still holds; I read the claim, not the code that enforces it.