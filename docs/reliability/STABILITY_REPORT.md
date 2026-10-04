# Alpha — Stability Report

**Cycle:** 2
**Date:** 2026-10-04
**Base commit:** `fefa49a`
**Current commit:** *(this cycle's HEAD — see §Changes for the change set)*
**Branch:** `main` (synced with `origin/main` at the start of the cycle)

> Cycle 1's report is preserved below under [Cycle 1](#cycle-1), unchanged, so
> the claim history stays readable. This cycle's report is the authority on the
> current state.

---

## Cycle 2

### Test counts

| Suite | Command | Result |
|---|---|---|
| Frontend unit | `node --test src/lib/*.test.mjs` | **1488 passed, 0 failed** (+11 this cycle) |
| Frontend typecheck | `tsc --noEmit` | **0 errors** |
| Frontend, focused | `idempotency.test.mjs`, `chat-request-error.test.mjs`, `chat-stream.test.mjs`, `history-store.test.mjs` | **47 + history-store green** |
| Backend coded run errors | `test_run_error_coded.py` | **18 passed** (+3 this cycle) |
| Backend nginx contract | `test_nginx_coded_upstream_failures.py` (new) | **25 passed** |
| Backend nginx regressions | `test_infra_audit_nginx_upstream_resolution.py`, `test_nginx_compression.py`, `test_nginx_langgraph_body_size.py`, `test_nginx_peer_network.py`, `test_nginx_provisioning.py`, `test_nginx_voice_websocket.py` | **50 passed**, unchanged |
| Backend boundaries | `test_harness_boundary.py`, `test_no_orphan_modules.py` | **green** — the new `app/gateway/error_sse.py` module is wired and respects App→Harness |

### Bugs discovered and fixed this cycle

| ID | Severity | Symptom | Root cause |
|---|---|---|---|
| ALPHA-BUG-0011 | **P1** | A lost run-admission response was never re-sent, and a blind retry would have admitted a **second run** | The Gateway already scoped `Idempotency-Key` admission and deduped a reuse; the frontend sent no key and `apiFetch` is single-shot |
| ALPHA-BUG-0012 | P2 | A transport drop on the initial POST ended the turn with no recovery path | Same gap, user-visible as a lost turn |
| ALPHA-BUG-0013 | P2 | One Gateway outage rendered two disagreeing banners with independent dismissal | `gatewayOk` and `serverHistoryError` are the same outage, rendered as two alerts |
| ALPHA-BUG-0014 | P2 | A coded run failure reached the log/metric/recovery ledger but never the watching client | No production `configure_error_reporter` caller; the SSE leg was unbound |
| ALPHA-BUG-0015 | P2 | A proxy failure reached the client as an HTML page → only "HTTP 502" | No `proxy_intercept_errors`/`error_page` in any config; Helm was a third copy |

### Remaining bugs

| Severity | Count | Items |
|---|---|---|
| P0 | 0 | — |
| P1 | 1 | Failure visibility queue (`flash()` single slot, ~60 call sites) |
| P2 | 4 | Crash-loop supervisor wiring; coded-502 **live** acceptance; error-reporter SSE operator surface; idle-Gateway CPU (unreproduced, above target) |
| P3 | 2 | Behaviour-trace spine default-off; no frontend client telemetry |

### Observability

| Gate | Status | Evidence |
|---|---|---|
| Logs | PASS | `alpha/observability/` spine; `alpha/errors/` registry |
| Trace correlation | PASS | `TraceEnvelope` + `RunContext` contextvar |
| Error UI | PASS | `chat-support-id.ts` renders code + correlationId |
| **Coded run error reaches the client** | **PASS (cycle 2)** | `app/gateway/error_sse.py` bound in the lifespan; real journal → real reporter → real bridge → real `subscribe()` |
| Unaddressable reports are countable | **PASS (cycle 2)** | `alpha_errors_sse_unbound_total` + `app.state.error_reporter_sse{published,unbound,loop_closed}` |
| Failure artifacts | PARTIAL | Evidence bundle layout specified; not yet implemented |

### Recovery

| Gate | Status | Evidence |
|---|---|---|
| Retry | PASS | LLM middleware 3×; tool timeout 600 s; stall watchdog 900 s; **admission retry 3× under an idempotency key (cycle 2)** |
| Handoff | PASS | `SafeRunRecoveryService`; peer takeover on lease expiry |
| Checkpoint | PASS | LangGraph checkpointer + `runtime/checkpoint/` |
| Resume | PARTIAL | Durable runtime wired; no live restart/resume run |

### Real workloads

Unchanged from cycle 1 except where noted. **No workload was run against a live
Gateway this cycle** — this environment has no Docker daemon and no booted
Gateway, so every live row stays as cycle 1 recorded it rather than being
re-claimed. Workload **K (LLM provider failure)** and **N (backend-only
failure)** gained *static* fault-path coverage: the admission retry is proven
against an injected transport failure through the real `sendMessage`, and the
error-reporter leg against a real run failure — but neither is a live stack
observation.

### Important findings

1. **The P1 fix was waiting for a precondition that already existed.** Cycle 1
   correctly refused to flip idempotent retry "blind", on the grounds that
   retrying a run-creating POST without a key is the double-charging failure
   `runtime/side_effects` exists to catch. Re-reading the tree first showed the
   Gateway *has* scoped, deduped `Idempotency-Key` admission on thread-scoped
   runs (`thread_runs.py`) — the key was implemented server-side and never sent.
   Same shape as cycle 1's worst bug: **built, wired, never called.** The
   refusal was right; the search that unblocked it was one grep away.
2. **Two prescriptions in the roadmap were wrong, and the code won.** §3.2 said
   intercept `502 503 504` — a Gateway-generated 503 (MCP worker-stopped,
   admission refusal) carries a body the client acts on, so intercepting it
   could only delete the server's reason; nginx never synthesises a 503 for a
   proxied request. §4.5 said to apply `connectivityView()`, which renders
   *internet* link state from `/api/ops/network` — a different question from "is
   the Gateway reachable". Both were narrowed to what the code supports and the
   deviations recorded rather than implemented blind.
3. **The prescription missed a third config.** `deploy/helm/alpha/templates/`
   ships its own nginx ConfigMap. A §3.2 fix applied to "both nginx configs"
   would have left the Helm deployment on the bare 502 it has today, and the
   existing nginx test family checks all three — which is how the third was
   found.
4. **A `keepalive` pool that cannot engage is dead configuration.** Connection
   reuse needs a static `upstream {}` block, but the Docker config resolves its
   upstream *per request* on purpose so a container restart cannot leave it
   holding a dead IP. The pool landed where the address is stable (local
   loopback, K8s Services) with the trade pinned by a test, instead of being
   added everywhere and quietly doing nothing.
5. **A UTF-8 BOM would have shipped invisibly.** Rewriting the local config with
   a Windows tool added a BOM; nginx would have rejected its own first directive
   (`unknown directive`) and every diff would still have looked correct. Caught
   by reading bytes rather than trusting the diff, and now pinned.
6. **A test harness that extracts source can silently mis-test a new binding.**
   `chat-request-error.test.mjs` runs the real extracted `sendMessage` through
   injected dependencies. Adding two module-scope bindings without injecting
   them would have thrown `ReferenceError` *inside* the request, masking every
   status-code assertion in the file behind one generic "connection" failure —
   so the harness now injects the real `sendIdempotent` with a comment saying a
   passthrough would silently drop the header the test exists to pin.

### Changes

| # | File | Change |
|---|---|---|
| 1 | `frontend/src/lib/idempotency.ts` | **new** — key minting, transport-failure classification, bounded equal-jitter retry under a fixed key |
| 2 | `frontend/src/lib/idempotency.test.mjs` | **new** — 9 cases |
| 3 | `frontend/src/components/ChatView.tsx` | per-send key; stream POST goes through `sendIdempotent`; the amber history banner is gated on `gatewayOk !== false` and the outage banner carries the local-copy disclosure |
| 4 | `frontend/src/lib/chat-request-error.test.mjs` | inject the two new bindings; new end-to-end case asserting one key across a transport retry |
| 5 | `frontend/src/lib/history-store.test.mjs` | new case: one outage, one banner |
| 6 | `backend/app/gateway/error_sse.py` | **new** — binds the reporter's SSE leg onto the run stream; `unbound_sse` metric + counters |
| 7 | `backend/app/gateway/deps.py` | bind the leg immediately after the bridge is built |
| 8 | `backend/tests/test_run_error_coded.py` | +3 cases (bound leg reaches the stream; unaddressable report counted; lifespan binding order) |
| 9 | `docker/nginx/nginx.conf` | coded 502/504 targets; `proxy_intercept_errors off` for frontend + provisioner; keepalive trade documented |
| 10 | `docker/nginx/nginx.local.conf` | same, plus `keepalive 32` and `Connection` cleared on the 15 non-WebSocket gateway locations |
| 11 | `deploy/helm/alpha/templates/configmap-nginx.yaml` | same coded targets (the third config) |
| 12 | `backend/tests/test_nginx_coded_upstream_failures.py` | **new** — 25 cases over all three configs |
| 13 | `docs/reliability/KNOWN_ISSUES.md`, `docs/RELIABILITY_ROADMAP.md` | §3.1, §3.2, §4.3, §4.5 → FIXED with deviations recorded |

### Tests added

- `frontend/src/lib/idempotency.test.mjs` — 9: key shape, retryable-classification,
  header, retry identity (same key/path/body), server answer never retried, abort
  never retried, bounded budget, abort during backoff, equal-jitter floor stepped
  to the millisecond.
- `frontend/src/lib/chat-request-error.test.mjs` — 1 new end-to-end case driving
  the real `sendMessage` through a transport failure.
- `frontend/src/lib/history-store.test.mjs` — 1 new case for the unified signal.
- `backend/tests/test_run_error_coded.py` — 3 new cases.
- `backend/tests/test_nginx_coded_upstream_failures.py` — 25 new cases.

### Evidence

`docs/RELIABILITY_ROADMAP.md` §3.1, §3.2, §4.3, §4.5 ·
`docs/reliability/KNOWN_ISSUES.md` (ALPHA-BUG-0011…0015) · test files above.

### Next cycle — highest-risk unresolved areas

1. **Live acceptance is still the gap, and it gates six gates (D–K).** The
   coded-502 fix in particular is *statically* proven only; `docker compose` with
   the Gateway stopped must return the coded JSON for real.
2. **P1 failure visibility queue** — the last P1 in the tree; a bounded list with
   a persistent badge instead of one 4.5 s `string | null` slot.
3. **Crash-loop supervisor wiring** to the Windows launcher (§3.5) — a service
   that crash-loops is still not quarantined after N restarts.
4. **Error-reporter SSE operator surface** — the counters exist but no `/api/ops`
   route exposes them, so the leg's health is not yet visible at the operator
   level.

---

# Cycle 1

**Cycle:** 1
**Date:** 2026-10-04
**Base commit:** `eaf1b32`
**Current commit:** `bc73b32`
**Branch:** `main` (synced with `origin/main`)

---

## Build / version

| Component | Version source | Value |
|---|---|---|
| Backend | `backend/pyproject.toml` | `alpha-harness` (installed dist) |
| Frontend | `frontend/package.json` | Next.js App Router, pnpm |
| Helm | `deploy/helm/alpha/Chart.yaml` | `version` + `appVersion` |

Version lockstep is enforced by `scripts/verify_versions.sh` on every `v*` tag.

---

## Test counts

| Suite | Command | Result |
|---|---|---|
| Backend wiring gates | `test_feature_manifest_wiring.py`, `test_no_orphan_modules.py`, `test_harness_boundary.py`, `test_invariants.py` | **36 passed** |
| Backend critical gates | `test_run_stall_watchdog.py`, `test_sse_heartbeat.py`, `test_tool_timeout_guard.py`, + wiring | **62 passed** |
| Backend durability (new) | `test_harness_state_durability.py` | **12 passed** |
| Backend continual harness | `test_continual_harness.py` | **5 passed** |
| Backend continuous goals | `test_continuous_goal_engine.py` | **passed** |
| Backend integration middlewares | `test_integration_middlewares.py` | **passed** |
| Frontend unit | `node --test src/lib/*.test.mjs` | **1477 passed** |
| Frontend typecheck | `tsc --noEmit` | **0 errors** |
| Frontend support-id (new) | `chat-support-id.test.mjs` | **13 passed** |
| Frontend stream (new) | `chat-stream.test.mjs` | **+4 passed** |
| Manifest drift | `scripts/check_generated_drift.py` | **0 drifted** |

**Total: 1590+ tests passing, 0 failing.**

---

## Discovered bugs

| ID | Severity | Title | Component |
|---|---|---|---|
| ALPHA-BUG-0007 | P1 | Every failed run rendered one indistinguishable sentence | Frontend SSE error path |
| ALPHA-BUG-0008 | P1 | Dropped stream re-dialed with zero delay | Frontend reconnect ladder |
| ALPHA-BUG-0009 | P1 | Corrupt JSON store read as empty state | Backend `harness/continuous/`, `harness/continual/` |
| ALPHA-BUG-0010 | P2 | `test_harness_refine_tool` failed on `main` | Backend test harness |

## Fixed bugs

| ID | Fix | Tests |
|---|---|---|
| ALPHA-BUG-0007 | `StreamRunFailure` carries `sseError`; `chat-support-id.ts` renders `code` + `correlationId` | 13 new |
| ALPHA-BUG-0008 | 5-attempt ladder with equal jitter (guaranteed floor) | 4 new |
| ALPHA-BUG-0009 | `is_degraded` / `is_durable` disclosure + staged load | 12 new |
| ALPHA-BUG-0010 | Test invokes tool with `runtime` in input dict | 1 repaired |

## Remaining bugs

| Severity | Count | Items |
|---|---|---|
| P0 | 0 | — |
| P1 | 2 | Idempotent HTTP retry; Failure visibility queue |
| P2 | 5 | Unified offline signals; Crash-loop supervisor wiring; Coded nginx 502; Error reporter SSE leg; Idle-Gateway CPU burn |
| P3 | 2 | Behaviour-trace spine default-off; No frontend client telemetry |

---

## Real workloads

| Workload | Status | Notes |
|---|---|---|
| A — Coding | **PASS** | 3 bugs found, fixed, tested, committed |
| B — Large coding | PARTIAL | Subagent delegation wired; no live run |
| C — Research | PARTIAL | Search wired; no live run |
| D — Tool orchestration | PARTIAL | Individual tools wired; no live chain |
| E — Subagent swarm | PARTIAL | Swarm runtime wired; no live run |
| F — Group chat | PARTIAL | Group chat wired; no live run |
| G — Project creation | PARTIAL | Persistence wired; no live restart |
| H — Team creation | PARTIAL | Team runtime wired; no live run |
| I — Company creation | PARTIAL | Company OS wired; no live run |
| J — Plugin/MCP | PARTIAL | MCP task runtime wired; no live failure run |
| K — LLM provider failure | PARTIAL | Retries + fallbacks wired; no live injection |
| L — Long task interruption | PARTIAL | Durable runtime wired; no live interruption |
| M — UI-only failure | **PASS** | Support identity path pinned by test |
| N — Backend-only failure | PARTIAL | Health/readiness wired; no live dependency failure |
| O — Mixed concurrent | NOT RUN | Unit-level isolation only |

---

## Observability

| Gate | Status | Evidence |
|---|---|---|
| Logs | PASS | `alpha/observability/` spine; `alpha/errors/` registry |
| Trace correlation | PASS | `TraceEnvelope` + `RunContext` contextvar |
| Error UI | PASS | `chat-support-id.ts` renders code + correlationId |
| Failure artifacts | PARTIAL | Evidence bundle layout specified; not yet implemented |

---

## Recovery

| Gate | Status | Evidence |
|---|---|---|
| Retry | PASS | LLM middleware 3×; tool timeout 600 s; stall watchdog 900 s |
| Handoff | PASS | `SafeRunRecoveryService`; peer takeover on lease expiry |
| Checkpoint | PASS | LangGraph checkpointer + `runtime/checkpoint/` |
| Resume | PARTIAL | Durable runtime wired; no live restart/resume run |

---

## Important findings

1. **The worst bug was invisible because the first link worked.** The Gateway
   sent a coded `event: error`, the frontend parsed it, a test pinned the parse —
   and nothing read the result. A green suite structurally cannot catch this
   class of defect.

2. **Full jitter was the wrong choice for the reconnect ladder.** It is uniform
   over `[0, ceiling]` and can return ~0, reintroducing the exact immediate
   re-dial it was meant to remove. Equal jitter spreads the herd *and* guarantees
   a real floor. My own test caught this; I changed the implementation, not the
   assertion.

3. **Three JSON stores under `alpha/harness/` predated the repo's fail-loud
   conventions.** The structurally identical `groups/claims.py` and
   `bots/registry.py` do log. One of them also destroyed data: it reset
   `self.entries` before parsing, so one bad entry left a half-state the next
   save wrote back.

4. **A pre-existing test failure was reproduced on clean `main` before being
   fixed**, so I wasn't papering over my own change.

---

## Changes

1. `frontend/src/lib/chat-support-id.ts` (new) — renders server `code` + `correlationId`; refuses untrusted `message`
2. `frontend/src/lib/chat-stream.ts` — `StreamRunFailure` carries `sseError`; 5-attempt equal-jitter ladder
3. `frontend/src/lib/chat-request-error.ts` — appends support line to every failure sentence
4. `frontend/src/components/ChatView.tsx` — passes `supportId` through the catch block
5. `backend/packages/harness/alpha/harness/continuous/store.py` — `is_degraded` / `is_durable` + staged save
6. `backend/packages/harness/alpha/harness/continual/state.py` — staged load; `is_degraded`
7. `backend/packages/harness/alpha/harness/continual/snapshots.py` — log unreadable manifest
8. `backend/packages/harness/alpha/tools/builtins/goal_engine_tool.py` — refuse on degraded store; refuse unpersisted resume
9. `docs/reliability/` (new) — 8 documents: system map, feature matrix, observability architecture, error catalog, failure recovery matrix, real-work validation, known issues, regression matrix, stability report
10. `docs/RELIABILITY_ROADMAP.md` — incidents 7–10 recorded; section 4 rewritten to reflect verified status

---

## Tests

1. `frontend/src/lib/chat-support-id.test.mjs` — 13 cases incl. the security property
2. `frontend/src/lib/chat-stream.test.mjs` — +4 cases (error propagation, ladder, floor)
3. `backend/tests/test_harness_state_durability.py` — 12 cases
4. `backend/tests/test_continual_harness.py` — repaired `test_harness_refine_tool`

---

## Evidence

- `docs/reliability/ALPHA_SYSTEM_MAP.md`
- `docs/reliability/FEATURE_EXECUTION_MATRIX.md`
- `docs/reliability/OBSERVABILITY_ARCHITECTURE.md`
- `docs/reliability/ERROR_CATALOG.md`
- `docs/reliability/FAILURE_RECOVERY_MATRIX.md`
- `docs/reliability/KNOWN_ISSUES.md`
- `docs/reliability/REAL_WORK_VALIDATION.md`
- `docs/reliability/REGRESSION_MATRIX.md`
- `docs/RELIABILITY_ROADMAP.md` (incidents 7–10, section 4)

---

## Next cycle

**Highest-risk unresolved area:** live validation against a booted Gateway with
real credentials. Gates D–K (real execution, verification, recovery, resume,
multi-agent, integration, regression, stability) are not yet demonstrated against
a running stack. That requires:

1. Boot the Gateway with real credentials.
2. Run workloads A–O against the live stack.
3. Inject real failures (provider timeout, tool failure, backend restart).
4. Verify recovery and resume with evidence.
5. Close the P1 frontend gaps (idempotent retry, failure visibility queue).
