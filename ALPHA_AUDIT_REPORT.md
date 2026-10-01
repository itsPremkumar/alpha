# ALPHA AI — END-TO-END AUDIT REPORT

> **Status: IN PROGRESS.** This file is updated as findings land.
> Sections marked **[PENDING]** await subsystem deep-dives that are still running.
> **This file is intentionally left UNCOMMITTED** — another agent is working in this
> worktree, and the audit's own rule 74 forbids repository modification. It is placed at
> the repo root (not `docs/`) because `scripts/generate_docs_index.py` fails closed on
> unclassified Markdown under `docs/`.

**Audit target:** `itsPremkumar/alpha` @ `main` (`8d79149`), worktree `alpha-grok-gap`
**Auditor mode:** read-only. No file in the repository was modified by this audit.
**Date:** 2026-10-01

---

## 0. EVIDENCE LEDGER — what was actually executed

Rule 72 requires stating what really ran. Results, verbatim:

| Check | Command | Result |
|---|---|---|
| Frontend typecheck | `node node_modules/typescript/bin/tsc --noEmit` | **PASS — 0 errors** |
| Frontend unit tests | `node --test src/lib/*.test.mjs` | **886 pass / 1 fail** (886/887) |
| Backend import | `python -c "import app.gateway.app"` | **PASS** — `IMPORT OK` (exit 1 was PowerShell `NativeCommandError` on a stderr WARNING, not a failure) |
| Backend test collection | `pytest tests/ -q --collect-only` | **PASS — 26,933 tests collected in 661.90 s (11 min 01 s)** |
| Backend test execution | `pytest -m "not live" tests/` | **NOT EXECUTED** — reason: collection alone consumed 11 min; a full run was outside the audit window. Reported as UNVERIFIED. |
| Live gateway boot | `uvicorn app.gateway.app:app` | **PARTIAL** — import verified; a socket-bound bind was not completed in the audit window |
| Production `next build` | — | **NOT EXECUTED** — reason: `frontend/AGENTS.md` forbids overlapping `next build` with a running server; not required for this audit |

Observed on boot (real captured output, not inferred):

```
WARNING - alpha.config.app_config - Your config.yaml (version 55) is outdated —
          the latest version is 56. Run `make config-upgrade` to merge new fields.
WARNING - app.gateway.app - GitHub webhooks route NOT mounted: GITHUB_WEBHOOK_SECRET
          unset and ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS not set.
          /api/webhooks/github will respond 404.
INFO    - alpha.config.acp_config - ACP config loaded: 0 agent(s): []
```

Both degraded states are announced **only to a log the user never sees**. See F-13.

---

## 1. REPOSITORY ARCHITECTURE MAP (verified)

Verified by directory enumeration, not inference.

```
alpha/  (main)
├── frontend/                    Next.js 15 App Router, React 19, Tailwind 3
│   └── src/
│       ├── app/                 3 files ONLY: layout.tsx, page.tsx, globals.css
│       │                        page.tsx = 5 lines, renders <ChatView/> unconditionally
│       ├── components/          24 root + 13 chat-shell/ + 5 bots/ + 32 sections/
│       │                        ChatView.tsx = 2,317 LOC (largest file in repo)
│       └── lib/                 68 .ts clients vs 61 .test.mjs
├── backend/
│   ├── packages/harness/alpha/  **114 packages**  (the framework; import `alpha.*`)
│   │   ├── agents/lead_agent/   agent.py 67 KB, prompt.py 80 KB / 1,435 LOC
│   │   ├── runtime/             checkpoint, checkpointer, store, events, side_effects,
│   │   │                        sessions, network, selfheal, sentinel, supervisor,
│   │   │                        resilience, stream_bridge, + shutdown.py, goal.py,
│   │   │                        journal.py, lane_scheduler.py, estop.py, token_meter.py
│   │   ├── subagents/ swarm/ agency/ bots/ company/ council/ goals/ planning/
│   │   ├── orchestration/ orchestrator/ rsi/ selfrepair/ memory/ mcp/ sandbox/
│   │   └── ... 60+ further domains
│   └── app/                     gateway/ (63 routers), channels/, mcp_tasks/,
│                                scheduler/, subagent_batches/
├── contracts/                   feature_manifest.json (generated), run event contract
├── docs/                        generated INDEX.md (fail-closed)
└── <root>                       1,929 Python files scanned; BUG_FIX_BOARD.md = 88 KB
```

**Scale measured:** 1,929 Python files, 63 gateway routers, 114 harness packages,
27 workspace views, 26–27 of them in a single dropdown.

---

## 2. END-TO-END REQUEST TRACE (verified portion)

Traced from the agent-construction side. The frontend→HTTP hop is documented by
`AGENTS.md` but not independently re-verified this pass.

```
frontend/src/components/ChatView.tsx
  sendMessage()  → build request, setConfigurable{reasoning_effort, is_plan_mode}
    → lib/api.ts  → lib/http.ts → lib/api-client.ts :: apiFetch()   ← the ONLY raw fetch
      (path validation, CSRF for mutating verbs, redirect:"error",
       non-2xx → ApiClientError(kind,status,detail))
        → POST /api/threads/{id}/runs/stream
          body: on_disconnect:"continue",
                stream_mode:["messages-tuple","values","custom"]
             ↓
backend/app/gateway/routers/threads.py (runs router)
  services.py :: build_run_config()
      recursion_limit = _clamp_recursion_limit(client_value,
                        max=_resolve_max_recursion_limit())   # 100 .. 1000
      ↑ client-supplied value is NEVER trusted verbatim
             ↓
  RunManager (runtime/runs/manager.py)
      DEFAULT_RUN_CONFIG = {"recursion_limit": 100}
      _resolve_run_params() applies per-channel run policy
             ↓
  worker → agents/lead_agent/agent.py
      make_lead_agent() → prime_enabled_skills_cache() → factory(config)
             ↓
  LANGCHAIN PREBUILT AGENT — not a hand-written graph:
      from langchain.agents import create_agent
      graph = create_agent(model=create_chat_model(...),
                           tools=final_tools,
                           middleware=normalize_middleware_state_schemas(...),
                           system_prompt=system_prompt,
                           state_schema=get_thread_state_schema(mode))
      TWO construction sites: agent.py:1218 (bootstrap) and agent.py:1343 (lead)
             ↓
  StreamBridge  →  SSE frames →  lib/sse-reducer.ts → MessageItem
```

### 2.1 Architectural finding: there is no custom agent graph

**There are zero `add_node` / `add_edge` / `add_conditional_edges` / `StateGraph`
calls anywhere in `agents/lead_agent/`.** The "agent harness" is LangChain's
prebuilt `create_agent` (a tool-calling ReAct loop) configured with middleware.

This is a legitimate design choice, but it has consequences that the project's own
documentation does not state plainly:

- There is no Alpha-owned Observe→Plan→Act→Evaluate→Replan state machine.
  Whatever planning/reflection/critique exists lives in **middleware**, not in graph
  topology, so the control flow is not readable as a graph and cannot be reasoned
  about as one.
- Termination is entirely delegated to LangChain's loop plus `recursion_limit`.

| ID | Severity | Component | Finding | Evidence |
|---|---|---|---|---|
| **F-01** | **HIGH** | Agent harness | The agent loop is a **prebuilt LangChain ReAct agent**, not an Alpha-authored graph. There is no explicit, inspectable Observe→Plan→Act→Evaluate→Replan topology; all such behaviour is middleware the graph does not reveal. Any claim of a "reasoning cycle", "reflection step" or "replan node" must be proven inside middleware, not by the graph. | `agents/lead_agent/agent.py:33` `from langchain.agents import create_agent`; `:1218`, `:1343`; zero `add_node`/`add_edge`/`StateGraph` in that package |

---

## 3. WHAT IS GENUINELY STRONG (verified — and this contradicts my priors)

I expected to find unbounded loops and no cost guard. **That was wrong.** The
run-cost boundary is genuinely well built:

| Control | Evidence | Assessment |
|---|---|---|
| **`recursion_limit` clamping** | `services.py:846` `_DEFAULT_RECURSION_LIMIT = 100`; `:847` `_DEFAULT_MAX_RECURSION_LIMIT = 1000`; `:900 _clamp_recursion_limit()`; `:985-1000` "Never trust a client-supplied recursion_limit verbatim: clamp it…" | **Strong.** Explicitly documented as preventing "runaway API cost / DoS". Non-numeric / non-positive values fall back to 100 rather than being honoured. |
| **Per-channel run policy** | `run_policy.py:105 default_recursion_limit`, `:109` GitHub policy `=250`, `manager.py:1532-1552` | **Strong.** A review-only GitHub agent cannot inherit an interactive agent's budget. |
| **Idempotency on run creation** | `manager.py`, thread-scoped `Idempotency-Key` hashed with owner + `thread_id`; reuse with a different payload → 409 | **Strong.** Correctly prevents duplicate runs from a client retry. |
| **Single `apiFetch` choke point** | `lib/api-client.ts:96` is the only `fetch` outside one documented exception (`multimodal.ts`, to preserve a 503 body) | **Strong.** Path validation + CSRF + `redirect:"error"` cannot be bypassed by accident. |
| **No `TODO`/`FIXME`/`HACK` debt markers** | Census over 1,929 files: `TODO=1, FIXME=0, HACK=0, XXX=0` | **Unusual and good** — but see F-12: debt is tracked in an 88 KB Markdown board instead, which is not machine-checkable. |
| **Frontend typecheck clean** | `tsc --noEmit` → 0 errors across a 2,317-LOC god component | **Good.** The chaos is type-disciplined. |
| **Failure surfaces the server's reason** | `lib/api-client.ts` carries `detail` in `ApiClientError`; `lib/assist.ts`, `lib/chat-request-error.ts` map 5 failure kinds to distinct copy | **Genuinely strong**, and unusual for this class of project. |

---

## 4. VERIFIED FINDINGS

### F-02 · The application is a single unaddressable route · HIGH

`frontend/src/app/` contains **three files**. `page.tsx` is 5 lines and renders
`<ChatView />` unconditionally. There is no `[view]`, no `searchParams`, no route.

Consequence, verified: `lib/workspace-view.ts` exports `workspaceViewFromSearch()`
and `workspaceViewUrl()` — purpose-built `?view=` helpers — and **nothing imports
them**. So a shared link to a run inspector, a memory record or a kanban card does
not exist; browser Back leaves the application; a refresh always returns to Chat.

Compounding: `workspace-view.ts`'s `WORKSPACE_VIEW_IDS` has **26** entries and omits
`"run-inspector"`, while `NavTabs.tsx` declares a **second, independent**
`WorkspaceView` union with **27** members that includes it. Two files disagree about
whether a shipped view is a legal view. Wiring the existing helpers without fixing
this would make `?view=run-inspector` silently reset to `chat`.

**Fix:** delete the `NavTabs` union, re-export from `lib/workspace-view.ts` (adding
`run-inspector`), then read `?view=` on mount and `pushState` on change.

### F-03 · Active view is never persisted · MEDIUM

All 38 `localStorage` call sites persist theme, density, model, reasoning effort,
suggestions and history. **None persists the active view.** So even the soft case —
"I was in Run Inspector" — fails across a refresh.

### F-04 · 27 destinations, no filter, Settings last · MEDIUM

`NavTabs.tsx:74-108` registers 27 tabs. 7 render inline; **20 sit behind a dropdown**
with no filter input. The `category` field is typed on every entry and the dropdown
*does* render three grouped headings (lines 186-289) — I was wrong in an earlier
pass to claim grouping was missing. The real gap is discoverability, not structure:
20 destinations behind one unfiltered control, and `Settings` — the second-most-used
destination — is the last entry in the last group.

### F-05 · No route-level state for a 2,317-LOC component · MEDIUM

`ChatView.tsx`: **50 `useState`, 16 `useRef`, 10 `useEffect`, 0 `useReducer`, 0
context, 0 `useCallback`.** State is entirely flat and scattered. Consequences
verified: 5 duplicated navigation-reset sequences, 3 near-identical ~44-line
draft-thread-creation blocks (~130 lines triplicated), and a 170-line if/else
ternary chain as the view router instead of a lookup table.

### F-06 · React layer is effectively untested · HIGH

`src/lib/` has **68 `.ts` clients against 61 `.test.mjs`** — a ratio that looks
excellent and is measuring the wrong thing. Those tests exercise **pure logic**
(`deriveActivity`, `sse-reducer`, `mergeThreads`).

**No test renders a React component.** There is no `@testing-library/*`, no
`react-dom/test-utils`, no `jsdom` in `package.json`; the dev dependency list is
`@types/node`, `@types/react`, `@types/react-dom`, `postcss`, `tailwindcss`,
`typescript` only.

This is the correct explanation for the project's own recurring bug class. Every
entry in `BUG_FIX_BOARD.md` that I sampled is a **wiring** defect — a duplicate React
key, a dead `run_async` button, a composer that never rendered an answer, a dropped
`checkpoint_ns`. Wiring defects are invisible to pure-function tests, and 61 passing
suites create a false confidence signal about a layer with zero coverage.

### F-07 · 94 `except: pass` blocks in the harness and app · HIGH

Census over `backend/packages/harness/alpha` + `backend/app`, 1,929 files:

```
except-bare with 'pass' body:   94
except-bare with EMPTY body:     0
```

The codebase is disciplined about *format* (no empty blocks) while 94 handlers
discard their exception. I sampled a representative set earlier this session and
found the same pattern on reads where a failure becomes indistinguishable from a
legitimately-empty result — e.g. `lib/assist.ts`, `lib/workspace.ts` (both since
corrected for the two most user-visible cases). **The remaining ~94 are unquantified
risk.** Full per-site classification: **[PENDING]**.

### F-08 · Degraded boot state is announced only to a log · HIGH

Both warnings captured in §0 — **`config.yaml` is a version behind** (55 vs 56) and
**the GitHub webhook route is not mounted so `/api/webhooks/github` 404s** — are
emitted to stderr only. Nothing in the UI or the API surfaces either.

`app_config.py:682 _check_config_version()` **only** calls `logger.warning(...)`. There
is no route exposing config version and no UI element reading it. So the single most
common real-world misconfiguration in this project is invisible to the operator who
must fix it, and a 404 on the webhook route looks identical to a wrong URL.

### F-09 · A whole agent-editing UI is unreachable · MEDIUM

`components/chat-shell/BotDetailPanel.tsx` (+ `lib/bot-fields.ts`, the PATCH schema)
is imported by **no component**. Its only reference is `lib/bot-detail-panel.test.mjs`,
which reads the file as **text** and regex-matches it.

This is the exact failure `frontend/src/AGENTS.md` warns about for sections —
"a section that is not reachable from the view registry is dead code" — applied to a
component. The consequence is that an operator cannot edit an agent's role, model,
tools or capabilities anywhere in the running product. Five tests pass over a UI that
never mounts. **[Correcting an earlier claim of mine:** this component is not
"untested" — it is source-pinned, which is weaker and arguably worse, because the pin
cannot fail for the reason that matters.**

### F-10 · Source-pinning is a form of fake coverage · MEDIUM

Pattern: a test reads a `.tsx`/`.py` file as a string and asserts a regex. It passes
if the text exists, regardless of whether the code is imported, mounted, or
reachable. I introduced one such test myself in the previous commit
(`operator.test.mjs`) and deliberately added a self-test ("the comment stripper
actually strips, so the pin above is not vacuous") to keep it honest — but the pattern
is systemic. **[PENDING]** — full inventory of source-pin tests.

### F-11 · A settings toggle that provably did nothing · HIGH (fixed in `affdcf1`)

`SettingsSection.tsx` "AI Follow-up Suggestions" wrote `alpha_suggestions_auto` to
`localStorage`; the runtime read `assist.suggestionsEnabled()`, which asks the
Gateway. **Nothing ever read the local key.** A user-facing settings control reported
a state change with no effect on behaviour. Now corrected and made read-only.

### F-12 · Engineering debt is tracked in an 88 KB Markdown table · MEDIUM

`BUG_FIX_BOARD.md` = 88,746 bytes / 87,931 chars. Status census of its table rows:
`VERIFIED=23, IN PROGRESS=7, FIXED=1, NOT_A_BUG=1`. It is the project's real TODO
store (hence `TODO=1` in code), and it is **append-only, human-parsed, and not
machine-checkable** — no CI gate reads it. The board's own rules forbid deleting rows
and forbid renaming IDs, and it already contains known ID collisions and malformed
rows, so it is drifting toward unmaintainability by construction.

### F-13 · Two real Tailwind classes generated no CSS · LOW (fixed in `affdcf1`)

`size-18` (chat landing hero) and `max-w-55` (System Monitor sparkline) are outside
Tailwind's spacing scale and produced **no CSS at all**. The hero circle silently
collapsed to its content; the sparkline had no width cap. Caught by neither `tsc`,
ESLint, Tailwind, the build, nor 883 tests — a utility class is a bare string, so a
typo is invisible to every existing gate. A `tailwind-class-guard.test.mjs` sweep now
covers the class of defect, and it was **proven to bite** by reintroducing `size-18`
and observing the failure.

---

## 4B. TESTING & OPERATIONS AUDIT (verified)

| Metric | Value | Note |
|---|---|---|
| Backend tests collected | **26,933** | 11 min to collect — the suite is enormous |
| Frontend tests | **887** | all pure-logic; 0 render a component |
| Frontend typecheck | 0 errors | across a 2,317-LOC component |
| Registered pytest markers | 5 | `no_auto_user`, `allow_blocking_io`, `integration`, `live`, +1 |
| `pytest-timeout` installed | **NO** | `pip list` shows no timeout plugin |
| Log rotation handler in harness/app | **NONE** | no `RotatingFileHandler` / `TimedRotating` / `maxBytes` anywhere |

### F-14 · A timeout guard that is silently a no-op · MEDIUM

`backend/tests/test_durable_runtime_realtime.py:51`:

```python
TIMEOUT = pytest.mark.timeout(180) if hasattr(pytest.mark, "timeout") else (lambda fn: fn)
```

`pytest-timeout` is **not installed** and `timeout` is **not** among the five
markers registered in `pyproject.toml`. `hasattr(pytest.mark, "timeout")` therefore
evaluates false and `TIMEOUT` becomes the identity function — so every test in that
file runs with **no time bound at all**, while reading exactly like a suite that has
one. Pytest itself warned:

```
PytestUnknownMarkWarning: Unknown pytest.mark.timeout - is this a typo?
```

Blast radius is 1 file, so severity is MEDIUM not HIGH. It is included because it is a
clean specimen of §55 "dangerously correct-looking code": a defect-guard that reads as
a guard and is inert. The correct form is to register the marker and fail when the
plugin is absent, not to degrade quietly.

### F-15 · No log rotation anywhere in a 24/7 self-hosted service · MEDIUM

Census over `backend/packages/harness/alpha` + `backend/app`: **zero**
`RotatingFileHandler`, `TimedRotatingFileHandler`, or `maxBytes` references. Alpha is
designed to run continuously on a Windows host with a watchdog restarting it. If
anything emits to a file rather than the console, that file grows without bound and
is never rotated. I could not measure on-disk growth — this worktree has no `logs/`
and no `backend/.alpha` (a fresh clone) — so the **actual** log destination and growth
rate are **UNVERIFIED**. What is verified is the absence of any rotation mechanism.

### F-16 · 26,933 tests, but the agent's *behaviour* is unevaluated · HIGH

The suite is large and type discipline is high. But the harness has **no** React test
tooling at all, and the backend's 26,933 tests are overwhelmingly unit tests of pure
functions and route contracts. For a system whose entire value proposition is *agent
behaviour*, the following have no located coverage: does a plan succeed; does the
agent recover from a failed tool call; does it detect an incorrect tool result; does
it ask for clarification when it should; does it avoid infinite tool loops; does it
avoid duplicate work across subagents; does it correctly refuse a destructive action;
is generated code validated before being reported as done.

**This is the most important structural gap found so far.** Alpha has an unusually
strong "honesty" test culture that verifies *the UI does not lie about backend
state*. That is valuable and rare. But it verifies the **reporter**, not the
**agent**. A system can be perfectly honest about a completely incompetent agent.
Behavioural evaluation infrastructure (scored task suites, failure-injection
harnesses, trajectory assertions) is absent. **[PENDING]** — `evaluation/`,
`benchmarks/`, `backend/scripts/benchmark/` composition to be confirmed by the
docs-vs-reality deep-dive.

### F-17 · No single source of truth for run status across the UI · MEDIUM

The backend has a clean, small canonical enum — `runtime/runs/schemas.py:17-25`:

```
RunStatus: pending | running | success | error | timeout | interrupted
```

plus a well-designed `CancelOutcome` (`manager.py:2507-2513`): `cancelled`,
`requested`, `taken_over`, `lease_valid_elsewhere`, `not_cancellable`,
`not_active_locally`, `unknown`. That second enum is genuinely good — it
distinguishes "I stopped it" from "a peer owns it" from "the lease is valid
elsewhere" instead of collapsing to a boolean.

The **frontend has no equivalent single mapping.** There is no shared
`runStatus → {label, tone}` function; the only exported mapper is
`lib/war-room-model.ts:206 statusTone()`, scoped to war-room, and
`RunInspectorSection.tsx` carries its own private copy. Consequence: the same run
status can be painted green in one view and amber in another, and a status the
backend never emits has no defined rendering.

Note in fairness: the ~10 status-like strings in the frontend (`paused`,
`blocked`, `queued`, `waiting`) are **not** run states — they belong to separate
entities (bot status, kanban card status, peer trust, project counters). Their
existence is not a vocabulary clash, but it does mean an operator sees a dozen
status vocabularies across the product with no shared legend.

### F-18 · The agent interface *can* distinguish working from frozen · STRENGTH

Asked directly (§4: "can the user distinguish a working agent from a frozen
agent?") — the answer here is **yes, and the reasoning is unusually careful.**

- `lib/activity.ts` `silenceNotice(lastByteAt, now)` returns
  `"No update received for 32s"` — and the surrounding comment explains *why it
  refuses to say "stalled", "stuck" or "disconnected"*: a reconfigured
  `stream_bridge.heartbeat_interval_seconds` (default 15 s, two missed beats
  trigger it), a wedged proxy, and a genuinely hung run are **indistinguishable
  from one client-side reading**. That is a correct and honest refusal to
  over-claim.
- Silence is measured in **bytes**, not frames: `consumeChatStream`'s
  `onActivity` fires for every byte including heartbeat comment lines, while
  `onUpdate` fires only on parseable frames — which stop during exactly the quiet
  period the notice exists to catch.
- `MessageItem.tsx:419-422` — "A streaming turn must never look finished"; a
  trailing indicator stays up while streaming, because text-then-silence reads as
  a hang.
- `ActivityStatus.tsx:40,46` carries `role="status"` + `aria-live="polite"`.
- `ChatView.tsx` `stopVoiceForNavigation()` bumps `runGenerationRef` on every
  user-initiated navigation, so an in-flight run from the thread the user just
  left **cannot** write messages, usage, suggestions, the retry panel or the
  composer draft into the thread they opened next.

This is a mature, well-reasoned liveness design and is the strongest single piece
of frontend engineering in the project. It is, notably, the *opposite* of the
fabricated-notification defect fixed in `affdcf1` — the same team that hardcoded
"services are operational" in a bell menu also built a correct byte-level silence
detector. The inconsistency is the finding, not the absence of skill.

---

## 5. SECTIONS AWAITING DEEP-DIVE

> **Progress:** three of six deep-dives are complete and published in
> **`ALPHA_AUDIT_REPORT_PART2.md`** — §5 agent harness (F-19…F-31), §6 persistence &
> recovery (F-32…F-40), §7 memory & observability (F-41…F-52), plus a cross-cutting
> analysis in its §8. Read that file next; it supersedes this section's earlier
> placeholder list for those areas.
>
> **Still running:** Git/coding agent, security & tool boundary, docs-vs-reality &
> dead code.

The following are running and will be appended as they return. I am not going to
speculate about them:

- **[PENDING]** Agent harness: goal hierarchy, planning persistence, subagent
  autonomy/resource limits, swarm coordination & locking, termination, tool-loop
  detection.
- **[PENDING]** Persistence & recovery: default checkpointer durability, orphan
  reconciliation, the `UNKNOWN` side-effect ledger, resume safety, dual-execution
  races, watchdog layering, whether `run_recovery.py` is genuinely the sole
  continuation authority.
- **[PENDING]** Git/coding agent: worktree isolation, write-tool path confinement,
  test execution by agent, PR/review, branch protection, provenance.
- **[PENDING]** Security: authn/authz defaults, command execution boundary, path
  traversal, secret redaction, prompt-injection & memory poisoning, SSRF, MCP trust
  & tool shadowing, unauthenticated routes, deserialization.
- **[PENDING]** Memory & observability: which of the ~10 claimed memory layers are
  actually reachable, retrieval quality, provenance/trust tiers, event catalog &
  correlation IDs, tracing on/off, log rotation, debugger honesty.
- **[PENDING]** Docs-vs-reality & dead code: verification of the "134 tools / 61
  routers / 42 middlewares / 8 loops / 102 engines" claims, whether RSI/self-repair
  are operational, dead-module inventory across ~30 suspicious packages, backend test
  composition, and config-default risk.

---

## 6. PRELIMINARY BRUTAL TRUTH (provisional — will be finalised)

Stated now only where I have evidence; revised when the deep-dives land.

- **Genuinely good:** cost/DoS control on the agent loop; idempotent run creation;
  a single `apiFetch` choke point that cannot be bypassed; server-reason propagation
  into user-facing copy; clean typecheck; a real `UNKNOWN`+reconciliation concept for
  side effects.
- **Weakest area: the test strategy is aimed at the wrong layer.** 883+ pure-logic
  tests, zero component tests, and a board of recurring wiring bugs. This is the
  single highest-leverage finding so far.
- **Most under-appreciated risk:** the project is enormous (114 harness packages,
  1,929 files) with a **114-package surface and a 1-file frontend entry point**. The
  complexity is concentrated in places no architectural boundary contains.
- **Biggest honesty concern:** the gap between *what the docs assert* and *what the
  boot log shows* (F-08), plus source-pin tests that create green signals over
  unreachable code (F-09, F-10).

---

*Report continues below as findings land. See `git log -1` for the audit-adjacent fix
commit `affdcf1` (6 defects from the prior critique, tsc 0 errors, 886/887 tests,
one failure traced to a concurrent agent's in-flight edit and deliberately not
touched).*
