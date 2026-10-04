# Alpha — Stability Report

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
