# Alpha — Real-Work Validation

The prompt's rule: **prefer real execution with safe test data over mocks.**
A green unit test is not evidence that real work happened.

## What "real work" means here

A task is successful only when a **completion verifier** confirms its acceptance
criteria. The verifier must be able to point to evidence for each stage:

```text
REQUEST → PLAN → INTENT → ACTION → TOOL → RESULT → EVIDENCE → VERIFICATION → COMPLETION
```

| Claim | Evidence required |
|---|---|
| "file created" | prove the file exists |
| "bug fixed" | prove the test passes |
| "Git commit created" | prove the commit exists |
| "research completed" | prove the evidence set |
| "browser task completed" | prove the target page/state |
| "memory saved" | perform a read-after-write verification |
| "subagent completed" | validate its output against acceptance criteria |
| "company/team created" | verify the persisted entity and reload it |

When evidence is missing, the status is `UNVERIFIED`, not `SUCCESS`.

## Workload matrix

| ID | Workload | Status | Evidence |
|---|---|---|---|
| A | Coding: find a real bug, fix it, add a regression test, run tests, verify diff | **PASS** | This cycle: 3 bugs found, fixed, tested, committed |
| B | Large coding task: divide into subgoals, assign subagents, implement, test, review | **PARTIAL** | Subagent delegation is wired and tested; no live multi-subagent coding run this cycle |
| C | Research: search/browse, collect evidence, compare sources, synthesize, preserve citations | **PARTIAL** | Search tools wired; no live research run this cycle |
| D | Tool orchestration: search → filesystem → terminal → edit → tests → Git → verification | **PARTIAL** | Individual tools wired; no live chained run this cycle |
| E | Subagent swarm: 6 agents, one intentionally failed, verify isolation + reassignment | **PARTIAL** | Swarm runtime wired and tested; no live swarm run this cycle |
| F | Group chat: create group, run coordinated task, validate routing/ordering/failures | **PARTIAL** | Group chat wired and tested; no live group run this cycle |
| G | Project creation: create, add files, execute, persist, restart, reopen, continue | **PARTIAL** | Project persistence wired; no live restart/resume run this cycle |
| H | Team creation: coordinator + workers, assign work, collect results, review, finish | **PARTIAL** | Team runtime wired; no live team run this cycle |
| I | Company creation: hierarchy, roles, delegation, reporting/escalation, persist, reload | **PARTIAL** | Company OS wired and tested; no live company run this cycle |
| J | Plugin/MCP integration: connect, invoke, make unavailable, verify error states | **PARTIAL** | MCP task runtime wired; no live MCP failure run this cycle |
| K | LLM provider failure: inject timeout/rate-limit/malformed, verify recovery | **PARTIAL** | LLM middleware retries + fallbacks wired; no live provider failure run this cycle |
| L | Long task interruption: multi-step, terminate, restart, resume, verify no duplicate side effects | **PARTIAL** | Durable runtime + checkpoint + recovery wired; no live interruption run this cycle |
| M | UI-only failure: force renderer/UI data error, verify error boundary + correlation | **PASS** | `chat-support-id.test.mjs` pins the support identity path |
| N | Backend-only failure: force one dependency failure, keep the rest alive | **PARTIAL** | Health/readiness/dependency diagnostics wired; no live dependency failure run this cycle |
| O | Mixed concurrent workload: coding + research + group + subagent + browser + memory | **NOT RUN** | Concurrency isolation tested at unit level; no live concurrent run |

## What was actually executed this cycle

Real work, with real evidence:

1. **Repository audit** — mapped the actual stack, identified all applications,
   entry points, error boundaries, async paths, state stores, retry/timeout/
   cancellation/watchdog/recovery logic. Produced `ALPHA_SYSTEM_MAP.md`.
2. **Silent-failure audit** — read every `except Exception` site in `backend/app/**`
   and `backend/packages/harness/alpha/**` (~1,500 matches), traced each return
   value into its caller. Found 3 real P1 defects.
3. **Frontend observability audit** — verified all 4 roadmap prescriptions against
   the actual current code. All 4 confirmed still broken.
4. **Fixes** — 3 defects fixed, each with regression tests.
5. **Test runs** — 36/36 wiring gates, 62/62 critical gates, 1477/1477 frontend
   tests, 12/12 new backend durability tests, 13/13 new frontend support-id tests.
6. **Manifest drift** — 0 files drifted.

## What was NOT executed

Stated plainly rather than implied:

- No live Gateway boot with real credentials.
- No live multi-agent, browser, or long-running-task workload.
- No live provider failure injection.
- No live restart/resume cycle.

Gates D–K from the definition of done (real execution, verification, recovery,
resume, multi-agent, integration, regression, stability) are **not** yet
demonstrated against a running stack. That requires a booted Gateway with real
credentials and is the next cycle's work.
