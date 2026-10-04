# Alpha — System Map

Built from the code, not from documentation. Every row names the module that
actually owns the behaviour, and every "status" claim names the evidence.

Companion documents: [FEATURE_EXECUTION_MATRIX.md](FEATURE_EXECUTION_MATRIX.md) ·
[OBSERVABILITY_ARCHITECTURE.md](OBSERVABILITY_ARCHITECTURE.md) ·
[ERROR_CATALOG.md](ERROR_CATALOG.md) · [FAILURE_RECOVERY_MATRIX.md](FAILURE_RECOVERY_MATRIX.md) ·
[KNOWN_ISSUES.md](KNOWN_ISSUES.md) · [STABILITY_REPORT.md](STABILITY_REPORT.md)

---

## 1. Runtime topology

Four cooperating processes, one public entry point. Root guide:
[Service Topology](../AGENTS.md#service-topology).

| # | Process | Package / module | Start | Port | Health | Role |
|---|---|---|---|---|---|---|
| 1 | **Nginx** | `docker/nginx/nginx.conf`, `nginx.local.conf` | `make dev` / Docker | `2026` | — | Single public entry. `/api/langgraph/*` → Gateway (rewritten onto native routes); other `/api/*` → Gateway API; `/` → Frontend |
| 2 | **Gateway API** | `backend/app/gateway/` (FastAPI) | `make dev`, `make gateway`, Docker | `8001` | `GET /health`, `GET /health/ready` | REST API **plus** the embedded LangGraph-compatible agent runtime. Owns the event bus, all routers, and the process-local observability substrate |
| 3 | **Frontend** | `frontend/` (Next.js App Router, pnpm) | `make dev`, Docker | `3000` | — | Operator UI. Talks to the Gateway only through same-origin `/api` (or `NEXT_PUBLIC_GATEWAY_URL`) — never holds a backend port, so a Gateway restart cannot strand a tab |
| 4 | **Provisioner** | `docker/provisioner` | only when sandbox is provisioner/K8s mode | `8002` | — | Optional; absent in the default local stack |

Plus **Electron** (`electron/`) — a desktop shell that reuses the same
Frontend/Gateway, and **IM channels** (`backend/app/channels/`: Feishu, Slack,
Telegram, Discord, DingTalk) which reach the same agent through the Gateway.

---

## 2. Request path (the load-bearing spine)

```text
browser / IM channel / TUI / LangGraph Studio
  ↓  same-origin /api/*            (nginx :2026, or direct :8001)
FastAPI routers  (backend/app/gateway/routers/*)
  ↓  run admission + owner scoping (authz, thread ownership)
RunManager  (packages/harness/alpha/runtime/runs/manager.py)   ← sole lifecycle owner
  ↓  async, journaled, checkpointed
run_agent worker  (runtime/runs/worker.py, a worker thread)
  ↓
LangGraph graph  (packages/harness/alpha/agents/lead_agent/agent.py)
  ↓
lead agent → middlewares → LLM / tools / subagents / memory / MCP / browser
  ↓
StreamBridge → SSE (named `event: heartbeat` every 15 s) → frontend reducer
```

The identity spine that makes all of this debuggable:

```text
X-Trace-Id / alpha_trace_id
  ↓  ContextVar (alpha/trace_context.py) — the ONLY source
trace_id → run_id → thread_id → message_id → tool_call_id → step_id
```

Every path that reaches a run binds one **first**; downstream treats it as a
plain `str` with no `if trace_id:` guards. A caller-sent id is **replaced**, so a
persisted run can never disagree with its header and logs. Full contract:
[backend/docs/STREAMING.md](../../backend/docs/STREAMING.md#request-trace-context-packagesharnessalphabetracepy).

---

## 3. Subsystem map

### 3.1 Orchestration & runtime

| Subsystem | Owner module | Persistence | Failure modes | Recovery |
|---|---|---|---|---|
| Run lifecycle | `runtime/runs/manager.py` | `runs` store (memory / SQLite), `run_events` (memory / JSONL / DB) | crash mid-run, lease expiry, peer takeover | orphan reclaim at startup; `orphan_recovered` stop reason; peer takeover on lease expiry |
| Execution journal | `runtime/journal.py` | journal / event store | write failure, flush failure | batch returned to buffer for retry; failure logged with `exc_info` |
| Checkpointing | `runtime/checkpointer/`, `runtime/checkpoint/` | LangGraph checkpointer (memory / SQLite / Redis) | `database is locked` | 30 s busy timeout on every connection (`6e49de3`) |
| Event bus | `runtime/events/` | memory / JSONL / DB (FTS5) | FTS unavailable | `_fts_available = false` → LIKE fallback, disclosed |
| Lane scheduler | `runtime/lane_scheduler.py` | in-process | controller resolution failure | advisory `True` — `try_admit` remains the authority |
| Side effects | `runtime/side_effects/` | `side_effects` ledger | DB error during reclaim | reclaimer keeps running; per-attempt log |
| Network monitor | `runtime/network/` | durable parked-session registry | offline / degraded | 4-state ladder with hysteresis; re-probe schedule; parked sessions resume |
| Connectivity waits | `runtime/network/wait_registry.py` | wait registry | claim lease outlives process | startup reclaims waits whose lease outlived the previous process |
| Sessions | `runtime/sessions/` | session state store | process restart | session state is durable and reloaded |
| Shutdown | `runtime/shutdown.py` | ordered drain | in-flight work at shutdown | admission closed first, then run drain, then park |

### 3.2 Resilience kit

`runtime/resilience/` — `RetryPolicy`, `CircuitBreaker`, `FullJitter`,
`ConvergenceGuard`, idempotency, budget. **Default OFF by design**: new retry
wiring adopts the kit explicitly per call site with an idempotency
precondition. A global default flip is the double-charging failure mode the
side-effect ledger exists to catch.

### 3.3 Self-healing ladder

| Layer | Mechanism | State |
|---|---|---|
| Run-level | **Stall watchdog** (`runtime/selfheal/run_stall.py`): `status=running` with `updated_at` older than 900 s (scan 30 s) → cancel + terminal `error` with `stop_reason="stalled"` | shipped |
| Run-level safety | `SafeRunRecoveryService` — the **only** safe-continuation authority | shipped |
| Model-level | LLM middleware retries × fallbacks × credit-exhausted routing | shipped |
| Service-level | `recovery/watchdog.ps1` tiers: defer → restart component → start stack | shipped |
| Crash-loop | `ProcessSupervisor` + `RestartLedger` (runtime/supervisor/) | built, **unwired** to the Windows launcher |
| Tool-level | **Tool-call budget**: every ToolNode call wrapped in `asyncio.wait_for` (`tool_timeout`, default 600 s) | shipped |

### 3.4 Observability

`packages/harness/alpha/observability/` — the run-correlation spine:
`RunContext` + contextvar propagation, collision-resistant ids, timed spans
with a closed status set, a closed event taxonomy, injected sinks with bounded
disclosed overflow, redaction at the only write path, and a metrics bridge into
the registries the Gateway already scrapes.

**Default off and inert** — `enabled: false` means no event, no file, no sink
call. Sampling is **per run**, not per event: a half-sampled run is worse than
no run, because you cannot correlate a turn you have but not the tool call that
preceded it.

Separately: `alpha/tracing/` (Monocle span factory), `alpha/errors/` (the one
stable error-code registry + reporter), and `runtime/sentinel/` (the
continuous debug loop).

### 3.5 Verification

| Concern | Owner |
|---|---|
| Run acceptance criteria | `runtime/runs/verification.py` — a terminal `success` is never presented as verified without evidence for every required kind |
| Tool result honesty | `_honest_tool_status` — a tool cannot be `SUCCESS` merely because it was launched |
| Delivery receipts | worker derives requirements from workspace snapshots; missing/unverifiable coverage **downgrades the run to error** |
| Invariant registry | `diagnostics/invariants.py` — fail-loud self-checks at assembly |
| Self-inventory | `workflow/registry/`, `intelligence/self_inventory.py` — 11 read-only registries; a broken source reports `count: null`, never `0` |

---

## 4. Health surfaces

| Endpoint | Meaning |
|---|---|
| `GET /health` | liveness (public, for orchestrator probes) |
| `GET /health/ready` | readiness — concurrently probes the ORM engine and the effective checkpointer/Store backend under one bounded deadline; 503 while either is unreachable |
| `GET /api/ops/version` | service name + installed `alpha-harness` version |
| `GET /api/ops/status` | liveness + uptime + UTC + docs flag |
| `GET /api/ops/resources` | host CPU/memory/disk/load; `null` for anything the OS will not expose |
| `GET /api/ops/runtime` | previous shutdown's ordered-drain outcome + live connectivity + parked-session counts |
| `GET /api/ops/network` | 4-state link reading, measured per-endpoint round-trip, re-probe schedule |
| `POST /api/ops/network/recheck` | one immediate measurement through the same serialized transition the poll loop runs |
| `GET /api/ops/integration-health` | live feature-manifest coverage + supervisor status |
| `GET /api/intelligence/inventory` | the self-inventory plane |

Every block carries an explicit `reported` flag, so "not measured" never renders
as a healthy value.

---

## 5. Known structural limits

Stated plainly rather than discovered later:

- **Single-process guarantees only.** The local event store, the lease store and
  wave concurrency are atomic and restart-recoverable for **one** Gateway
  process — never cross-process exactly-once. Multi-worker requires
  `run_events.backend: db`; the startup gate rejects process-local stores when
  `GATEWAY_WORKERS > 1`.
- **The crash-loop supervisor is built but not wired** to `start.ps1` /
  `watchdog.ps1`, so a crash-looping service is not yet quarantined after N
  restarts.
- **The error reporter's SSE leg is not bound** — no production
  `configure_error_reporter` caller exists. Run-scoped errors still reach
  clients via `gateway_terminal_error_payload`.
- **Nginx returns a bare 502** when the Gateway is down; no coded proxy-failure
  body yet.
- **Idle-Gateway CPU burn** (incident #4) did not reproduce across two e2e
  sessions (0.91 s over 60 s) but stays above the <1 % target, so it remains
  open.
