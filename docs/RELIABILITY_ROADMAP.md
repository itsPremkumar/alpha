# Production Reliability Roadmap

Status: **living plan + evidence log** for the reliability, recovery, and
connectivity hardening of Alpha. Every claim here is marked with one of three
words and each word means exactly this:

- **FIXED** — implemented, test-proven in this repository, commit named.
- **WIRED** — implemented and attached to the Gateway lifespan; proven by the
  wiring tests plus a live Gateway boot check.
- **SPECIFIED** — root-caused and designed down to files and acceptance
  criteria, but not yet implemented (ownership or sequencing noted).

This document is the authority for the workstream that produced it. Where it
conflicts with aspirational prose elsewhere, this file wins until a claim
upgrades from SPECIFIED to FIXED.

---

## 1. Incident log — why this exists

| # | Symptom (observed live) | Root cause | Disposition |
|---|---|---|---|
| 1 | Run `19f01090…` created its output file, then died mid-flight with `sqlite3.OperationalError: database is locked` | The LangGraph checkpointer was the only `alpha.db` writer still on the driver's 5 s default busy timeout (engine and sync stores already used 30 s) | **FIXED** — `6e49de3` |
| 2 | Run `4565d275…` hung on an internal await and sat `status=running` for 20+ min: no heartbeat, no SSE frames, no error anywhere; only a manual cancel finalised it | Two gaps: the run hung inside a **sync tool call with no budget** (`tool.ainvoke → run_in_executor → self.invoke` blocked forever during a self-heal retry — discovery/MCP/sandbox each had their own timeout, the plain ToolNode path had none), and no stall detector existed: leases are renewed by a loop independent of the run task, so a live process with a dead task passed every existing check | **FIXED** — `3d4795b` (watchdog bounds detection: 900 s → terminal `error` + `stop_reason="stalled"`) + `tool_timeout` budget (§2.4 bounds the call itself: 600 s → retryable `tool_timeout` ToolMessage) |
| 3 | A zombie run's SSE stream showed **zero observable events for 877 s** — the bridge's `: heartbeat` comment (15 s) was flowing but is invisible to browser JS *and* to spec-conformant SSE parsers (including this repo's own e2e parser), so a slow agent and a dead connection were indistinguishable; a consumer stalled before `subscribe` would stop even the comments | Liveness existed only as spec-comments: no client-actionable signal and no bound on total silence | **FIXED** — named `event: heartbeat` via `app/gateway/sse.py` on every run stream |
| 4 | Idle Gateway process burned 1 442 s CPU doing nothing (starved the test box) | Under investigation during incident; treated as P1 (see §3.5) | **SPECIFIED** |
| 5 | The four-legged error fan-out's SSE leg never fires: no production `configure_error_reporter` caller exists | Claimed-in-docstring wiring was never implemented; run-scoped errors still reach clients via `gateway_terminal_error_payload` | Honesty **FIXED** (docstring); binding **SPECIFIED** (§3.1) |
| 6 | `e2e_real_task.py` Task 1 end-to-end: **green** (durable `success`, byte-exact artifact, delivery gate passed) | — | Evidence that the happy path works after Fixes 1 |

---

## 2. Phase 0 — shipped (FIXED / WIRED)

### 2.1 Checkpointer write-lock parity — `6e49de3`
Shared `SQLITE_BUSY_TIMEOUT_MS` (30 000) + `SQLITE_CONNECT_PRAGMAS` in
`runtime/store/_sqlite_utils.py`, applied by the checkpointer's connection
opener. Tests: `test_checkpointer_busy_timeout.py` (2 new), 3 adapted pins,
91 green across checkpointer/blocking-io/cache/mode suites.

### 2.2 Run stall watchdog — `3d4795b` + lifespan wiring
`runtime/selfheal/run_stall.py` scans store inflight rows for
`status=running` whose `updated_at` (bumped by every progress snapshot) is
older than `run_stall.timeout_seconds` (default **900 s**, scan every
**30 s**), double-reads so late progress wins, cancels through the one
universal primitive (`RunManager.cancel` — local, durable, and lease paths
all converge there), then persists a terminal **error** with an explanatory
message and `stop_reason="stalled"`. The store's terminal-state guard already
refuses a late `interrupted`-over-`error` write; the watchdog re-asserts as
defense in depth.

Wiring: constructed from `config.run_stall` after the lease heartbeat starts
in `app/gateway/deps.py`; stopped **first** inside the single composed
`close_admission()` step (a second `ADMISSION_CLOSED` registration would
replace the first — that pin is enforced by `test_network_wiring.py`).

Tests: 9 behavioural + 3 lifespan source-assertions
(`tests/test_run_stall_watchdog.py`). Config: `run_stall` registered
startup-only in `config/reload_boundary.py`.
*Note:* the `run_stall` section is not yet listed in `config.example.yaml`
(that file is owned by a parallel workstream); defaults apply without it.

### 2.3 SSE heartbeat — silence is now client-visible and bounded
There were already two liveness mechanisms that a browser cannot use: the
stream bridge injects `HEARTBEAT_SENTINEL` every
`stream_bridge.heartbeat_interval_seconds` (default 15 s) and
`sse_consumer` forwards it as an SSE **comment** (`: heartbeat`) — invisible
to `EventSource` listeners and ignored by spec-conformant parsers. What was
missing is a *signal*.

`app/gateway/sse.py::with_heartbeats` wraps all four run-stream mounts
(`stream_run`, `join_run`, `_stream_existing_run`, stateless `POST /api/runs/stream`)
through `services.with_stream_heartbeats`: once 15 s pass with no **real**
event, a named `event: heartbeat` frame goes out. Bridge comments pass
through but deliberately **do not reset the client-facing clock** — during a
zombie run the comments keep proxies alive while the named heartbeat keeps
telling JS the connection is alive, bounded by roughly one interval plus one
comment period. The frame carries no `id:` so the `Last-Event-ID` rejoin
cursor can never move, and the reducer's unknown-event path returns state
unchanged (verified against `sse-reducer.ts:504-536`).

Design constraint that shaped it: `asyncio.wait_for(anext())` would cancel
the source generator on its first timeout, so a pump task drains the source
into a queue and the outer loop only ever waits on `queue.get()`.

Tests: `tests/test_sse_heartbeat.py` — order preservation, silence→heartbeat,
steady source never sees one, source never cancelled by a timeout, pump
cancelled on close, no trailing heartbeat after completion, comment
passthrough-without-suppression, and source-assertion wiring pins for all
four routes plus the shared frame in `services.py`.

### 2.4 Tool-call budget — a hung tool can no longer zombie a run
Incident 2's hang lived inside `tool.ainvoke → run_in_executor →
self.invoke`: the plain ToolNode path awaited a synchronous tool forever.
Every other layer already had its own budget (discovery runtime 30 s, MCP
`tool_call_timeout`, sandbox `bash_command_timeout` 600 s) — none covered a
direct call. `ToolErrorHandlingMiddleware`, which already owns tool
exceptions, now wraps the async path in `asyncio.wait_for`:

- `AppConfig.tool_timeout` — default **600 s** (identical to the sandbox
  command cap), `0` disables (a YAML `null` cannot: the loader drops nulls
  so the default applies). Registered **startup-only**
  (`reload_boundary`): the middleware stack captures the value when the
  agent is compiled, and a config edit does not rebuild a live agent.
- Overrun raises `ToolCallTimeoutError` → the existing exception path
  builds an error `ToolMessage` whose text classifies `tool_timeout` under
  `recovery.policies` (2 attempts, bounded backoff, replan) and which
  `autonomy_truth` marks retryable — a hang becomes an ordinary, retryable
  tool error the model/self-heal can react to instead of an eternal
  `running`.
- `GraphBubbleUp` (interrupt/pause) still bypasses the budget; the sync
  `wrap_tool_call` path runs inline and cannot be preempted (ToolNode
  executes through the async path). Cancelling the await cannot kill the
  executor thread — it dies with the process, but the run is free.
- Tests: `test_tool_timeout_guard.py` (4): bounded overrun + classification
  contract, control-flow passthrough, disabled budget, default pin.

### 2.5 Also in this change set
- `5491134` recursion limit 100→1000 (`client.py` + `docs/TUI.md`).
- `e494112` process-handle header discloses host shell + measured runtime.
- `719df4e` replay fixture extended through the `present_files` delivery turn.
- `6ebd170` replay miss-dump / first-differing-event diagnostics.
- `errors/report.py` docstring no longer claims a Gateway SSE sink binding
  that does not exist (claim-honesty).

---

## 3. Phase 1 — server-side next (SPECIFIED)

### 3.1 Bind the error reporter's SSE leg
**Design:** in the lifespan, call `configure_error_reporter(sse_sink=…)`
where the sink resolves `payload.context.run_id` and publishes an `error`
frame through the run's `StreamBridge`; payloads without a run id count as
`unbound_sse` (log/metric/recovery legs still fire). **Acceptance:**
`test_run_error_coded.py` extended with a bound-sink case asserting the frame
shape; `GET /api/ops` surface (if any) shows `unbound_sse` growth = 0 for
run-scoped reports.

### 3.2 Coded proxy failures (nginx)
Both `docker/nginx/nginx.conf` and `nginx.local.conf` return a bare 502 when
the gateway is down. **Specify:** `proxy_intercept_errors on` +
`error_page 502 503 504 = @gateway_unavailable` returning a JSON body
`{"detail": "gateway unavailable", "error_code": "proxy_upstream_down",
"http_status": 502}` so `api-client.ts` classifies a real payload instead of
an opaque status. Never intercept `text/event-stream` responses (SSE must
fail as-is). Add `keepalive 32` to an `upstream{}` block. **Acceptance:**
compose test: stop gateway → `GET /api/features` returns coded JSON; SSE
stream terminates with its own error frame, not a rewritten body.

### 3.3 Idle-Gateway CPU burn (incident #4)
**Reproduce** with a booted Gateway + no traffic + `Process CPU samples`
(2 × 60 s apart); if non-zero, bisect by disabling lifespan loops one at a
time (stall watchdog, network monitor, lease heartbeat, stream-bridge
cleanup, tiktoken warm-up retry). **Acceptance:** idle gateway < 1 % CPU over
5 min; regression guard test samples the loop's task counter.

### 3.4 Retry adoption policy (do not flip defaults)
The stack already provides: LLM middleware 3 attempts with decorrelated
jitter (`retry_base_delay_ms=1000`, cap 8 s) × provider `max_retries` ×
`FallbackChatModel` (5) — up to 45 calls worst-case, with
`retries_orchestrated=True` pinning provider retries to 0 to prevent
multiplication; `stream_chunk_timeout` 240 s for stalled streams. The generic
`runtime/resilience` kit (`RetryPolicy`, `CircuitBreaker`, `FullJitter`,
`ConvergenceGuard`) is **default OFF**. **Rule:** new retry wiring adopts the
kit *explicitly per call site* with an idempotency precondition — never a
global default flip (double-charging side effects is the failure mode the
side-effect ledger exists to catch).

### 3.5 Wire `ProcessSupervisor` to the Windows launcher
Carried from `docs/architecture/durable-runtime.md`'s not-implemented list:
the supervisor + restart ledger are built but not connected to
`start.ps1`/`watchdog.ps1`. **Specify:** watchdog tier-3 actions route
through the supervisor's crash-loop-bounded policy (RestartLedger budgets)
instead of raw process restarts, so a crash-looping service is quarantined
after N restarts instead of spinning. **Acceptance:**
`test_launcher_watchdog_budget.py` extended: crash-loop → quarantine + tray
status reports it.

---

## 4. Phase 2 — frontend prescriptions (SPECIFIED, owned elsewhere)

The frontend is owned by a parallel workstream; these are exact, reviewable
prescriptions from the connectivity survey — not vague advice.

1. **Propagate the rich error payload.** `sse-reducer.ts:526` already parses
   `code/message/correlationId`, but `chat-stream.ts:65,107` throws
   `new ApiClientError("response")` and drops it, so `ErrorBox` renders
   generic copy. **Change:** carry `SseErrorDetail` on `ApiClientError`
   (`api-client.ts:22-46` gains `detail`), render code + correlation id in
   `ChatView`'s failure path. *User-visible effect: every failed run shows
   WHY with a support id.*
2. **Idempotent HTTP retry.** `api-client.ts:80-130` and `http.ts:23-48` do
   one attempt. **Change:** retry GET/PUT/DELETE and probe-style POSTs with
   backoff (0.5 s/1 s/2 s, jitter); for `POST /runs/stream`, send the
   `Idempotency-Key` the server already accepts (`thread_runs.py:83-91`) so a
   retried send resumes the same run instead of creating a duplicate.
3. **Reconnect ladder.** `chat-stream.ts:109-115` caps rejoin at 2 attempts
   (Last-Event-ID rejoin already exists). **Change:** 5 attempts with full
   jitter (0.5→8 s) + the server `retryDelay`; give up into the existing
   `stopped` error with the draft preserved.
4. **Failure visibility queue.** All failures currently funnel through
   `flash()` (4.5 s auto-clear, one slot — concurrent failures overwrite).
   **Change:** per-failure identity + a bounded list (5) with a persistent
   error badge; heartbeat frames stay out of the UI entirely.
5. **Unify offline signals.** `gatewayOk` (30 s probe) and
   `serverHistoryError` render independent banners that can disagree; route
   both through one `connectivityView()` verdict (the honesty rules already
   live in `lib/network.ts`).

---

## 5. Port & connection contract (sticky binding, fallback, no port spam)

**The invariant that already holds and must never regress:** the browser only
ever speaks same-origin `/api` (or `NEXT_PUBLIC_GATEWAY_URL`), and
`next.config.mjs` rewrites to `ALPHA_INTERNAL_GATEWAY_URL` server-side — so
users never hold a backend port at all, and killing/restarting the Gateway
cannot strand a browser tab on a dead port.

| Entry point | Today | Target |
|---|---|---|
| Browser (dev/docker) | Sticky by construction (`/api` origin) | unchanged — **the** anti-spam guarantee |
| Nginx | Fixed `gateway:8001` / `127.0.0.1:8001`, no failover, bare 502 | coded 502 (§3.2) + `keepalive`; single upstream, never a scan |
| `start.ps1` | Free-or-fail, retries the *same* port (3→300 s backoff), never migrates | migrate-and-release (below) |
| Electron | Identity-checked reuse (`GET /health` → `service === 'alpha-gateway'`), else next free port ≤ +20; **never republishes** | publish chosen ports to a runtime port file |

**Migrate-and-release (start.ps1, specified):** if the preferred port is held
by a non-Alpha process after the existing 15 s free-wait → (1) probe `N+1…
N+20` with the *identity check only* (no wide scan — at most 20 bind probes of
one candidate each, the Electron rule), (2) start the Gateway on the first
free candidate, (3) write `alpha-runtime-ports.json` (`{"gateway": 8011,
"frontend": 3000, "pid": …, "updated_at": …}`), (4) release/kill the old
listener only if it was ours, (5) watchdog and tray read the same file before
ever probing. **Contract:** one file, one identity check, one bounded probe
budget, backoff never below 3 s — that is what "no port spamming" means
mechanically. **Acceptance:** `test_launcher_watchdog_budget.py` case:
foreign process on 8001 → Gateway boots on 8002, registry written, watchdog
health-checks 8002, old port never re-probed after success.

---

## 6. Self-healing ladder (as-is)

| Layer | Mechanism | State |
|---|---|---|
| Run-level | **Stall watchdog** (§2.2) cancels + explains hung runs | shipped |
| Model-level | LLM middleware retries × fallbacks × credit-exhausted routing | shipped |
| Run-level safety | `SafeRunRecoveryService` (only safe-continuation authority), startup orphan reclaim, lease heartbeat | shipped |
| Service-level | `recovery/watchdog.ps1` tiers: defer → restart component → start stack, backoff 30→300 s | shipped |
| Crash-loop | `ProcessSupervisor` + `RestartLedger` | built, **unwired** (§3.5) |
| Side-effect | ledger + reclaimer (SQL when session factory exists) | shipped, partial UI (durable-runtime doc) |
| Tool-level | **Tool-call budget**: `ToolErrorHandlingMiddleware` wraps every ToolNode call in `asyncio.wait_for` (`tool_timeout`, default 600 s — the same cap the sandbox gives `bash`); discovery runtime keeps its own 30 s default; MCP `tool_call_timeout` defaults to None (server-level budget) | shipped, default documented |

---

## 7. Verification matrix

| Claim | Proof |
|---|---|
| Hung runs are terminalised with a reason | `test_run_stall_watchdog.py` (12 tests) against real `RunManager`/`MemoryRunStore` |
| Watchdog starts/stops with the Gateway | `TestGatewayLifespanWiring` source pins + single `ADMISSION_CLOSED` registration pin |
| Streams cannot go silently mute | `test_sse_heartbeat.py` (10 tests incl. route-wiring pins) |
| A hung tool call is bounded and classified `tool_timeout` | `test_tool_timeout_guard.py` (4 tests) |
| DB-lock regression stays dead | `test_checkpointer_busy_timeout.py` + 91-test suite |
| End-to-end task honestly completes | `scripts/e2e_real_task.py` exit 0: durable `status=success`, artifact byte-exact over HTTP, delivery gate passed |
| Cancel path finalises cleanly | live evidence: manual cancel → `CancelledError` → `interrupted` → finalized, gateway healthy |

---

## 8. Operational budget table (single source of truth)

| Budget | Value | Where |
|---|---|---|
| Run stall timeout | 900 s | `run_stall.timeout_seconds` |
| Stall scan interval | 30 s | `run_stall.interval_seconds` |
| SSE named-heartbeat silence bound | 15 s | `app/gateway/services.py` (`SSE_HEARTBEAT_SECONDS`) |
| Bridge comment heartbeat | 15 s | `stream_bridge.heartbeat_interval_seconds` |
| Checkpointer busy timeout | 30 s | `runtime/store/_sqlite_utils.py` |
| LLM stream chunk gap | 240 s | `models/factory.py` |
| LLM retry | 3 × (1 s base, 8 s cap, decorrelated jitter) | `llm_error_handling_middleware.py` |
| Frontend HTTP probe | 10 s interval / 60 s request timeout | `ChatView.tsx` / `http.ts` |
| Launcher restart backoff | 3 → 300 s, reset after 10 min stable | `start.ps1` |
| Watchdog action backoff | 30 → 300 s | `watchdog.ps1` |

*Add new budgets here when you add them — a budget nobody can find is a
budget nobody respects.*
