# Alpha — Feature Execution Matrix

Populated from code. "Verification" names the mechanism that distinguishes a real
side effect from a plausible sentence. Evidence column names the test or doc that
pins the claim.

Status legend: **WIRED** = end-to-end path exists and is pinned · **PARTIAL** =
path exists with a named gap · **SPECIFIED** = designed, not implemented.

| Feature | UI Entry | IPC/API | Backend Handler | Orchestrator | Worker | Tool/Provider | Persistence | Verification | Status | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| Chat / run | ChatView composer | `POST /api/threads/{id}/runs/stream` | `routers/thread_runs.py` | `RunManager` + lead agent | `runtime/runs/worker.py` | LangGraph graph | checkpoints, `runs`, `run_events` | terminal status + delivery receipt | WIRED | `test_run_event_stream_contract.py`, `e2e_real_task.py` |
| Run streaming (SSE) | `chat-stream.ts` + `sse-reducer.ts` | `GET /{rid}/stream`, `/join` | `services.py`, `sse.py` | `StreamBridge` | stream pump | SSE | event log | named `event: heartbeat` every 15 s | WIRED | `test_sse_heartbeat.py` (10) |
| Run cancellation | Stop button | `POST /{rid}/cancel` | `thread_runs.py` | `RunManager.cancel` | task cancel | — | `cancel_action`, `cancel_requested_at` | CAS-fenced; first action wins | WIRED | `CancelOutcome` enum tests |
| Thread history | ThreadSidebar | `GET /threads/{id}/messages/page` | `threads.py` | — | — | — | `run_events`, checkpoints | partial page archived with truncation disclosed | WIRED | `test_thread_lifecycle.py` |
| Regenerate / edit-replay | message menu | `POST /regenerate/prepare`, `/edit-regenerate/prepare` | `thread_runs.py` | checkpoint replay | worker | LangGraph | new `run_id`, `replay_kind` metadata | newest attempt per source is authoritative | WIRED | `RunManager.list_edit_replay_visibility()` |
| Subagent delegation | SubagentList rows | `task` tool | `subagents/executor.py` | lead agent | `SubagentExecutor` | `task`, `batch_task` | delegation ledger | `[rN]` receipts + acceptance criteria; disabled receipts mean no citations | WIRED | `tests/test_client.py` (Gateway conformance) |
| Subagent failure | — | — | `subagents/executor.py` | — | executor | — | task metadata | `try_set_terminal(SubagentStatus.FAILED)` | WIRED | `test_subagents*.py` |
| Multi-agent swarm | — | `swarm` tool | `swarm/` | `SwarmCoordinator` | `SwarmScheduler`, `AsyncSwarmRunner` | DAG executor | atomic JSON checkpoints, JSONL audit | `budget_exhausted`/`stalled` reported, never fabricated completion | WIRED | `test_swarm_v2_runtime.py` |
| Group chat | Messages section | `POST /{name}/messages` | `groups/service.py` | `GroupChatService` | — | `post_message` | transcript JSONL | gap-free, sequence-checked; read receipts re-read | WIRED | `test_group_advanced_messaging.py` |
| War room | Deliberation view | `POST /war-rooms/*` | `war_rooms.py` | `war_room.py` (scheduler) | `SubagentParticipant` | `war_room` tool | `run.json`, `transcript.jsonl` | unaddressed infection downgrades `succeeded` → `partial` | WIRED | `test_war_room_deliberation.py` |
| Dynamic workflows | Workflows section | `POST /workflows/dynamic/*` | `workflows.py` | `DynamicWorkflowEngine` | `ExecutionKernel` | node executors | JSONL sink, lease store | orphan reconcile fails `RUNNING` nodes `worker_lost` | WIRED | `test_workflow_leases.py` |
| Terminal failure identity | `ErrorBox` in ChatView | `event: error` frame | `sse-reducer.ts` → `ChatView.tsx` | — | `StreamRunFailure` | SSE | — | `code` + `correlationId` rendered; server `message` refused | WIRED | `chat-support-id.test.mjs` (13) |
| Stream rejoin | — | `GET /{rid}/join` | `thread_runs.py` | — | `chat-stream.ts` | SSE | `lastEventId` cursor | 5 attempts, equal-jitter floor | WIRED | `chat-stream.test.mjs` |
| Long-run resume | Runs view | `POST /api/workflows/runs/{rid}/recover` | `run_recovery.py` | `SafeRunRecoveryService` | — | — | checkpoint + journal | only pending model/agent nodes auto-replay | WIRED | `docs/architecture/durable-runtime.md` |
| Idempotent run create | — | `Idempotency-Key` header | `thread_runs.py` | `RunManager` | — | — | persistence index (process-wide) | reused key with different input → 409 | WIRED | `test_idempotency*.py` |
| Goal engine | — | `goal_engine` tool | `goal_engine_tool.py` | `continuous/runner.py` | — | `goal_engine` | `.alpha/goals/goals.json` | degraded store refuses; unpersisted save reported | WIRED | `test_continuous_goal_engine.py`, `test_harness_state_durability.py` |
| Harness self-improvement | — | `harness_refine` tool | `harness_refine_tool.py` | `continual/refine.py` | — | `harness_refine` | `harness.json` + snapshots | staged load; partial parse cannot destroy good entries | WIRED | `test_harness_state_durability.py` (12) |
| LLM call | — | — | `models/factory.py` | lead agent | — | provider SDK | run journal usage | `alpha_error_fallback` marker → authoritative failure | WIRED | `test_llm_error_handling*.py` |
| Memory | Memory section | `GET /api/memory/*` | `routers/memory.py` | memory manager | — | `memory` tools | memory store | read-after-write verification | WIRED | `test_memory*.py` |
| MCP tools | — | `GET /api/mcp/*` | `routers/mcp.py` | — | — | MCP servers | `mcp_tasks` | lease-based durable task runtime | WIRED | `test_mcp*.py` |
| Browser automation | — | `browser_*` tools | `community/browser_automation/` | — | `BrowserSessionManager` | Playwright | bounded session cap | screenshots on failure; traces | PARTIAL | `test_browser_automation.py` |
| Web search | — | `web_search` tool | `community/*/search.py` | — | — | DDGS/Brave/Tavily/SearXNG | — | search failure ≠ "no information" | WIRED | `test_search*.py` |
| Scheduled tasks | Scheduled section | `POST /api/scheduled/*` | `routers/scheduled.py` | `scheduler/` | scheduler | cron | schedule store | dispatch reuses the run path, never a parallel stack | WIRED | `test_scheduler*.py` |
| Company hierarchy | Company view | `/api/company/*` | `routers/company.py` | `company_os/` | — | — | multi-tenant store | ownerless read → `None`, never 403 | WIRED | `test_company_os_core.py` |
| Electron shell | desktop app | IPC | `electron/main.js` | — | main process | Chromium | local window | identity-checked port reuse | PARTIAL | `electron/tests/` |
| HTTP idempotent retry | — | — | `api-client.ts`, `http.ts` | — | — | `fetch` | — | **absent** | SPECIFIED | roadmap §4.3 |
| Failure visibility queue | — | — | `ChatView.tsx` `flash()` | — | — | — | — | **single slot, 4.5 s** | SPECIFIED | roadmap §4.4 |
| Unified offline signals | — | — | `ChatView.tsx` banners | — | — | — | — | **two independent banners** | SPECIFIED | roadmap §4.5 |
| Crash-loop supervisor wiring | — | — | `start.ps1`, `watchdog.ps1` | `runtime/supervisor/` | — | — | restart ledger | **built, unwired** | SPECIFIED | roadmap §3.5 |
| Coded nginx 502 | — | — | `nginx.conf` | — | — | — | — | **bare 502** | SPECIFIED | roadmap §3.2 |
| Error reporter SSE leg | — | — | `errors/report.py` | — | — | — | — | **no production caller** | SPECIFIED | roadmap §3.1 |
