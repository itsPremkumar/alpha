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
| 4 | Idle Gateway process burned 1 442 s CPU doing nothing (starved the test box) | Not reproduced after the hardening work: on 2026-10-02 an idle Gateway (post-Task-2, zero traffic) measured **0.91 s CPU over 60 s wall** (≈1.5 %) with the stall watchdog, network monitor and 5 s system monitor all active; the original trigger is still unknown, so the 5-min <1 % acceptance and the regression guard in §3.3 remain open | **SPECIFIED** (evidence in §3.3) |
| 5 | The four-legged error fan-out's SSE leg never fires: no production `configure_error_reporter` caller exists | Claimed-in-docstring wiring was never implemented; run-scoped errors still reach clients via `gateway_terminal_error_payload` | Honesty **FIXED** (docstring); binding **SPECIFIED** (§3.1) |
| 6 | `e2e_real_task.py` end-to-end: **both tasks green** — Task 1 single-file (40 s) and Task 2 multi-file (172 s, the shape of the earlier zombie incident) each exit 0 with durable `status=success` and verified artifacts; Task 2's stream carried named `heartbeat` frames at 15 s cadence under a live parser | — | Evidence that the happy path works after Fixes 1–4 |
| 7 | Every failed run in the product rendered one indistinguishable sentence, even though the Gateway sent a coded, correlated reason and the frontend parsed it correctly | Three links each looked fine in isolation: the parse was written and pinned by a test, the throw site dropped the parsed object, and the error mapper had no field to put a code in. A defect invisible *because* the first link worked — the one shape a green suite cannot catch | **FIXED** — §4.1 |
| 8 | A dropped SSE stream with no `retry:` frame was re-dialed with **zero** delay, up to 3× in a row | `let retryDelay = 0` and the only fallback was the post-increment cap; a missing `retry:` frame read as "retry immediately", which is the worst input to give a dependency that has just failed | **FIXED** — §4.2 (equal jitter, guaranteed floor, 5 attempts) |
| 9 | A corrupt `goals.json` / harness state file reported "No active autonomous goals." / injected **no** system-reminder, and a failed save reported a successful `resume` | Three JSON stores under `alpha/harness/` wrapped `json.load`/write in `except Exception: pass` with no logger. The structurally identical `groups/claims.py` and `bots/registry.py` *do* log, so this was not the house style — three stores that predate it. `HarnessState` also lost data: it reset `self.entries` before parsing, so one bad entry left a half-populated state the next save wrote back | **FIXED** — degraded/durable disclosure + staged load |

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

**Evidence (2026-10-02):** the 2 × 60 s reproduction was run on the
post-hardening Gateway (`:8011`, after e2e Task 2, zero traffic): **0.91 s
CPU over 60.4 s wall** (≈1.5 %) — the 1 442 s burn did not reappear across
two full e2e sessions. That is above the <1 % target, so this stays open:
the 5-min sample and the loop-counter regression guard are the remaining
acceptance steps.

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

## 4. Phase 2 — frontend prescriptions

**Verification note (2026-10-04).** All four prescriptions below were re-verified
against the current tree before any change, not taken from the original survey.
Each was confirmed still broken at the cited line, so each is now either FIXED
here or still SPECIFIED with a narrower scope.

### 4.1 Propagate the rich error payload — **FIXED**

This was the worst of the four, and the reason is worth recording: the Gateway
had always sent a coded, correlated `event: error`, `sse-reducer.ts` had always
parsed it into `SseState.error`, and `tool-status-honesty.test.mjs` had always
pinned that the parse was correct. **Nothing read the result.**
`consumeChatStream` threw a bare `ApiClientError("response")` at all five
failure sites, `ChatView` collapsed that to `{ kind: "stream" }`, and
`chatRequestErrorMessage` returned fixed prose — so every failed run in the
product rendered "The response stream was interrupted", which is true of every
failed run ever seen.

A silent failure with a **passing** test suite: the defect was invisible
precisely because the parse worked.

- `chat-stream.ts` now throws `StreamRunFailure` (an `ApiClientError`
  subclass, so `kind === "response"` and every existing caller/assertion is
  unchanged) carrying `sseError`.
- `lib/chat-support-id.ts` is new and owns the filtering. Only the server's
  **`code` and `correlationId`** are rendered — server-generated tokens, which
  is what a second operator needs to find the trace. The server's failure
  **`message` is deliberately still not rendered**: it is derived from tool
  output and provider text, so it is untrusted content, and
  `chat-request-error.test.mjs:137-142` deliberately pins that a body carrying
  a secret or `<script>` never reaches the transcript. Trading a missing reason
  for an injection surface would be the wrong fix. Identifiers are shape-checked
  and bounded to 96 chars; a malformed one renders **no line** rather than being
  trimmed into something renderable.
- A failure with no reported identity renders byte-identically to the previous
  sentence — pinned by test, because most failures carry no id.

Coverage: `src/lib/chat-support-id.test.mjs` (13 cases, incl. the security
property), `chat-stream.test.mjs` (+4), `chat-request-error.test.mjs` (24 pass).
A missing import in the `chat-request-error.test.mjs` harness is also fixed
(`StreamRunFailure` / `chatSupportId` are now bound there, or every
`instanceof` would be false and the new path silently untested).

### 4.2 Reconnect ladder — **FIXED**, and the zero-delay retry was the real bug

The prescription said "5 attempts with full jitter". The sharper defect was one
line below it: `let retryDelay = 0`, so a stream that dropped **without** a
`retry:` frame was re-dialed with **no wait at all** — hammering the very
dependency that had just failed to deliver, three times in a row.

`MAX_REJOIN_ATTEMPTS` is now 5, and the fallback ladder is **equal jitter**
(`half + random(half)` of `min(8s, 500ms·2ⁿ)`), not full jitter. Full jitter is
uniform over `[0, ceiling]` and can return ~0 — it spreads the herd but
reintroduces the immediate re-dial it was meant to remove. Equal jitter spreads
the herd *and* guarantees a real floor. A server-supplied `retry:` delay still
wins and is used verbatim; the ladder is only the "nobody told us" case.

*User-visible effect:* a laptop that sleeps for two seconds no longer loses the
rest of an answer irreversibly — every later byte was still on the server behind
`Last-Event-ID`.

### 4.3 Idempotent HTTP retry — **SPECIFIED, deliberately not flipped**

`api-client.ts` and `http.ts` are still single-shot, and the frontend still
sends no `Idempotency-Key`. This one is **not** being done blind: retrying a
`POST /runs/stream` without an idempotency key is exactly the
double-charging-side-effects failure `runtime/side_effects/` exists to catch, so
the prescription stands but needs the key plumbed end-to-end and verified
against the server's real dedupe before it lands. Left open rather than
half-done.

### 4.4 Failure visibility queue — **SPECIFIED, owned elsewhere**

`flash()` is still one `string | null` slot with a 4.5 s auto-clear and ~60
call sites, duplicated per-section. It is a real defect (concurrent failures
overwrite each other) but it is a broad `ChatView` refactor across ~15
components, not a change to land beside a correctness fix.

### 4.5 Unify offline signals — **SPECIFIED, owned elsewhere**

`gatewayOk` and `serverHistoryError` still render two independent banners with
independent dismissal that can visibly disagree. The honesty machinery the fix
needs (`connectivityView()`) is built and tested in `lib/network.ts`; it is
simply not applied to these two signals.

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
| Both end-to-end tasks honestly complete | `e2e_real_task.py` exit 0 × 2 (2026-10-02, Gateway `:8011`): durable `status=success`, terminal end frame, no error frames, artifacts verified (`e2e-task-1.md`; 3 × `e2e-task-2-*.md`), exact contents in the reply |
| Named heartbeats reach a live parser mid-run | e2e Task 2 stream: `{"type": "heartbeat"}` frames at +31/+46/+61 s (15 s cadence) while the model was silent |
| Idle Gateway CPU (incident #4 evidence) | 60 s sample post-Task-2: 0.91 s (≈1.5 %); no burn recurrence across both sessions; §3.3 5-min acceptance still open |
| Pre-existing config-coupled suite failures fixed | `2251170`: honesty-suite kill-switch opt-in fixture + forced L1 chain gate — 11/11 green under both the shipped template and an operator `config.yaml` |
| Cancel path finalises cleanly | live evidence: manual cancel → `CancelledError` → `interrupted` → finalized, gateway healthy |
| A failed run names itself to the operator | `frontend/src/lib/chat-support-id.test.mjs` (13) + `chat-stream.test.mjs` (+4) + `chat-request-error.test.mjs` (24) — code + correlation id reach the UI; the server's untrusted message still cannot |
| A brief network blip does not lose the answer | `chat-stream.test.mjs` — 5-rejoin budget, and a guaranteed non-zero first backoff (the full-jitter version failed this) |
| A corrupt harness store is never an empty one | `backend/tests/test_harness_state_durability.py` (12) — degraded ≠ empty, and a partial parse no longer destroys the entries that parsed |
| The pre-existing `harness_refine` tool test failure | FIXED — it invoked the tool with no `runtime`, which pydantic rejects; reproduced on a clean `main` before the fix, repaired at the call site |

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
