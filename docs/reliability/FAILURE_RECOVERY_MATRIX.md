# Alpha — Failure Recovery Matrix

Every failure class, the policy that governs it, and the evidence that the
recovery actually happened. Budgets are single-sourced in
[RELIABILITY_ROADMAP.md](../RELIABILITY_ROADMAP.md#8-operational-budget-table-single-source-of-truth).

## The failure lifecycle

```text
DETECTED → CLASSIFIED → CORRELATED → CAPTURED → SURFACED
  → RETRY DECISION
      ├── NO  → ESCALATE / HANDOFF / FAIL WITH EVIDENCE
      └── YES → RETRY → VERIFY
                   ├── SUCCESS → CONTINUE
                   └── FAIL → BACKOFF / CHANGE STRATEGY / HANDOFF
                              → ROOT-CAUSE ANALYSIS → FIX OR QUARANTINE
                                → REGRESSION TEST → REPLAY
```

Never infinite blind retries. Retries are policy-driven and aware of error type.

## Recovery policies

| Failure class | Policy | Budget | Evidence |
|---|---|---|---|
| Transient network | bounded retry, decorrelated jitter | LLM: 3 × (1 s base, 8 s cap) | `llm_error_handling_middleware.py` |
| Provider rate limit | respect provider timing | `Retry-After` honoured | `MODEL_PROVIDER_RATE_LIMITED` |
| Stream chunk gap | bounded wait | 240 s | `models/factory.py` |
| Tool call overrun | `asyncio.wait_for` → retryable `tool_timeout` | 600 s (`tool_timeout`) | `test_tool_timeout_guard.py` |
| Run stall | cancel + terminal `error`, `stop_reason="stalled"` | 900 s timeout, 30 s scan | `test_run_stall_watchdog.py` |
| Checkpointer lock | busy timeout | 30 s | `test_checkpointer_busy_timeout.py` |
| SSE silence | named heartbeat frame | 15 s | `test_sse_heartbeat.py` |
| Stream drop | rejoin ladder | 5 attempts, equal jitter 0.5→8 s | `chat-stream.test.mjs` |
| Crash-loop | `ProcessSupervisor` + `RestartLedger` | built, **unwired** | roadmap §3.5 |
| Service crash | watchdog tiers: defer → restart → start stack | 30→300 s backoff | `recovery/watchdog.ps1` |
| Launcher restart | free-or-fail, same port | 3→300 s backoff | `start.ps1` |

## Failure classes and their handling

| Class | Detection | Classification | Recovery | Verification |
|---|---|---|---|---|
| **configuration** | startup validation | `CONFIG_INVALID` / `CONFIG_UNAVAILABLE` | fail at boot; never a silent fallback | startup gate |
| **validation** | pydantic / schema | `INVALID_INPUT` / `TOOL_SCHEMA_INVALID` | repair args or stop with evidence | 422 with issue list |
| **auth** | auth middleware | `AUTH_*` | surface credential problem, never loop | 401/403 |
| **network** | `NetworkMonitor` | `NETWORK_UNAVAILABLE` | 4-state ladder with hysteresis; park + resume | `GET /api/ops/network` |
| **provider** | provider SDK | `MODEL_PROVIDER_*` | fallback chain; credit-exhausted routing | `alpha_error_fallback` marker |
| **LLM** | response validation | `MODEL_RESPONSE_INVALID` | retry; malformed tool args → repair, don't repeat | structured output validation |
| **tool** | `ToolErrorHandlingMiddleware` | `TOOL_EXECUTION_FAILED` | bounded retry; `tool_timeout` → retryable error | honest tool status |
| **MCP** | connection lifecycle | `MCP_*` | reconnect; unavailable optional ≠ empty result | `UNAVAILABLE_OPTIONAL` |
| **plugin** | extension load | `EXTENSION_LOAD_FAILED` | fail loudly; never a silent skip | startup gate |
| **browser** | Playwright | — | screenshot on failure; trace artifact | `test_browser_automation.py` |
| **process** | subprocess wrapper | — | exit code + signal + stdout/stderr captured | process state enum |
| **filesystem** | sandbox | `SANDBOX_POLICY_DENIED` | refuse with reason | policy verdict |
| **Git** | git tool | — | verify repository state, not just exit code | diff review |
| **database** | store layer | `PERSISTENCE_*` | busy timeout; conflict → CAS | store tests |
| **memory** | memory manager | — | read-after-write verification | memory tests |
| **serialization** | envelope validation | — | refuse unknown schema version | `TraceEnvelope.from_record` |
| **IPC** | channel manager | — | `add_done_callback` on every fire-and-forget task | channel tests |
| **frontend** | error boundary | — | `ErrorBox` + support identity | `chat-support-id.test.mjs` |
| **backend** | global exception handler | — | coded error, never bare prose | error registry |
| **concurrency** | lease store | `RUN_ADMISSION_CONFLICT` | 409 + `Retry-After` | lease tests |
| **cancellation** | `RunManager.cancel` | `RUN_CANCELLED` | stop cleanly, preserve resumable state | `CancelOutcome` enum |
| **timeout** | `asyncio.wait_for` | `TIMEOUT` | bounded, classified | timeout tests |
| **quota** | budget guard | `RUN_QUOTA_EXCEEDED` / `RSI_BUDGET_EXCEEDED` | escalate, never silently truncate | budget tests |
| **state corruption** | store load | `PERSISTENCE_CORRUPT` | quarantine + recover from last valid checkpoint | durability tests |
| **orchestration** | `RunManager` | `RUN_EXECUTION_FAILED` | orphan reclaim; peer takeover | recovery tests |
| **subagent** | executor | — | `try_set_terminal(FAILED)`; reassign/handoff | subagent tests |
| **verifier** | acceptance criteria | `RUN_DELIVERY_UNVERIFIED` | downgrade run to error | verification tests |
| **recovery** | supervisor | — | crash-loop quarantine (built, unwired) | roadmap §3.5 |

## The answer-vs-action distinction

For action-required tasks, an LLM text response alone is not sufficient. The
execution contract:

```text
REQUEST → PLAN → INTENT → ACTION → TOOL → RESULT → EVIDENCE → VERIFICATION → COMPLETION
```

A task is successful only when a **completion verifier** confirms its acceptance
criteria. When evidence is missing, the status is `UNVERIFIED`, not `SUCCESS`.

## What "recovered" means

A restart is called successful only after:

1. process started;
2. dependency initialization completed;
3. health endpoint passed;
4. task scheduler/worker heartbeat returned;
5. required queues/state stores are accessible.

Every recovery operation is recorded.
