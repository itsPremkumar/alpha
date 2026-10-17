# Alpha Subagents — Advanced Implementation Plan

**Status:** execution spec for the work remaining after Phase 0.
**Date:** 2026-10-17 · **Branch:** `main`
**Companion:** [`SUBAGENT_MASTER_PLAN.md`](SUBAGENT_MASTER_PLAN.md) owns the
research, the taxonomy and the honest inventory. This file owns *what to build
next and how*, at the level of files, contracts, tests and gates.

---

## 0. Where we actually are

| Phase | State | Commit |
| --- | --- | --- |
| 0 — wire the hierarchical-delegation seam | **done** | `e08d166` |
| — engines reachable from the picker | **done** | `d1fa69c` |
| 1 — measure the `task` blocking path | next | — |
| 2–8 | planned below | — |

Two things are now true that were not at the start of this plan:

1. **Hierarchical delegation actually executes.** `deep_runner.py` maps the six
   deep types onto their shipped `deep-*` workers and runs them through the real
   `SubagentExecutor`, so they inherit the admission controller, sandbox leases,
   receipts and usage accounting. Before this, `delegate` answered
   `UNRECOVERABLE_ERROR` forever.
2. **Arena, dynamic workflow, council, staged deliberation and orchestration mode
   are listed in the picker** — as navigational rows in an `engine:` namespace,
   because none of them can be a delegation mode (the run request carries only
   `preferred_subagent_type` and `preferred_subagent_category`).

---

## 1. Phase 1 — Measure before building

**This phase produces a page of answers, not a feature.** It is first because it
decides whether Phase 2 is a one-file wiring fix or a multi-file build, and
because we have already been wrong once about which path the model-facing `task`
tool takes.

### 1.1 The question

Does `task` block the parent's turn until the child finishes, or does it return
a handle and continue?

- `commands/backend_handlers.py` shows the **command** path (`/subagent:spawn`)
  returns a `subagent_id` and lease expiry immediately, with the runner scheduled
  rather than awaited.
- `SubagentExecutor.execute()` is the **sync** wrapper that GRO's
  `SubagentExecutionAdapter` uses, so a lane blocks its caller by design.
- **Untraced:** whether the model-facing `task` tool wraps that sync call or uses
  `execute_async()`.

### 1.2 Work

**File:** `backend/tests/test_task_tool_blocking_behaviour.py`

1. A timing test: spawn a child whose execution is artificially slow (a stubbed
   executor that sleeps), then assert whether `task()` returns before the child
   finishes. Assert the *observed* behaviour with a named constant, not the
   behaviour we hoped for.
2. A fan-out measurement: run three children through one `task`-shaped call and
   record wall time, `max_total_per_run` consumption, and whether the three ran
   concurrently or in sequence.
3. A probe of `SubagentExecutor.execute_async()`'s contract: does it yield a
   pollable/future id, and is that id the registry key (`execution_id`) the
   subagents AGENTS.md describes?

### 1.3 Gate

The test passes against **today's** behaviour and names it in a module docstring.
A green suite that asserts the wrong contract is worse than no suite, so the
docstring records the measurement date.

### 1.4 MEASURED ANSWER (2026-10-17, `main`) — phase complete

**Both, in different directions, and that is the useful part.**

| Question | Answer | Evidence |
| --- | --- | --- |
| Do sibling children overlap? | **Yes** | `task_tool` submits `executor.execute_async(prompt, task_id=tool_call_id)` and receives an `execution_id`, so each child takes a real slot in the process-wide admission controller |
| Does the parent's turn block? | **Yes** | the tool then `await asyncio.shield(_await_subagent_terminal(...))`, polling every `_SUBAGENT_POLL_INTERVAL_SECONDS` (5.0s) |
| Does the model get a handle mid-flight? | **No** | the tool returns no handle until the child is terminal |

So the fan-out that matters **already works**; the missing piece is ergonomics —
steering a running child, or continuing the turn — not parallelism.

**Consequence for Phase 2:** it is **ergonomics plus budget**, not a
parallelism build. Concretely:

1. Surface the existing `execution_id` (steer / cancel / inspect), which is what
   Claude Code's `background: true` and Hermes' `task_id` give the operator.
2. Add the unified budget and priority admission, which is still genuinely
   absent — Arena, swarms and `task` each hold a private ceiling and contend for
   the same three slots.

**Pinned by** `backend/tests/test_task_tool_blocking_behaviour.py` (9 tests). The
blocking shape is a source pin rather than a timing assertion because a timing
test would be flaky and would assert the *hoped-for* contract; the poll loop's
boundedness (poll count, wall clock, unreadable status) is tested for real. A
green suite asserting the wrong contract would be worse than none, so the pins
name today's behaviour and its date.

Two environment constraints that cost time and are worth recording:
`alpha.tools.builtins` re-exports the **tool** as `task_tool`, so the module must
be reached via `importlib.import_module`; and `tests/conftest.py` stubs
`alpha.subagents.executor`, so `SubagentStatus` has no members here and
`_is_subagent_terminal` cannot execute — it is pinned by source instead.

| If `task` blocks | If `task` returns a handle |
| --- | --- |
| Phase 2 adds a genuinely async delegation path | Phase 2 becomes **ergonomics**: surface the existing handle, steer/stop, and a background affordance in the composer |
| Fan-out needs explicit parallelism | Parallelism already exists; expose and measure it |

---

## 2. Phase 2 — Unified budget and priority admission

### 2.0 Correction to this section (measured 2026-10-17)

The original text said Arena, swarms and `task` all contend for the same three
slots. **That is only partly true, and the difference matters.**

`SubagentExecutionCapacity.slot()` has exactly **one** production call site
(`subagents/executor.py`). So the pool *is* shared by ordinary `task`, durable
`batch_task` items, Arena competitors and GRO candidate lanes — all of which run
through `SubagentExecutor`.

The **dynamic-workflow engine does not use it.** `workflow/runtime.py` runs its own
bounded wave pool and measures `peak_in_flight` itself, and
`tests/test_workflow_wave_peak_in_flight.py` asserts `.slot()` is *absent* from that
source, with the comment "the engine must not pretend to acquire the governor it
never uses". That is a deliberate invariant, not an oversight.

**Consequence:** unifying the workflow pool means overriding a stated invariant,
so it is split out as its own decision (2.4) rather than swept into this phase.
The honest scope of 2.1–2.3 is the shared pool plus the budget counters.

### 2.0.1 DONE — priority admission

`_waiters` now carry `(priority, sequence)`; a release serves the highest
priority with FIFO on ties; `slot(priority=0.0)` keeps today's behaviour exactly
for every caller that does not opt in. `SubagentCapacitySnapshot` gains
`priority_preemptions` — a cumulative count of slots that went to a waiter who was
not the longest-waiting, so contention is measured rather than only felt.

Pinned by `tests/test_subagent_capacity_priority.py` (9 tests, of which 6 are new
behaviour). The timeout/cancel transfer paths stay pinned by the pre-existing
`tests/test_subagent_execution_capacity.py`.

**Not yet done: nothing passes a non-default priority.** The mechanism is inert
until a call site adopts it — deliberately, so enabling it changes nothing on its
own. Adopting it (foreground `task` over background batches, Arena over a
low-confidence plan) is the next slice.

### 2.0.2 DONE — priority adoption at real call sites

The executor does **not** guess its own importance. It takes `admission_priority`
from the caller that actually knows what kind of work it is admitting, which is the
only source of a real signal — an executor-side heuristic would have to guess
"foreground" from data that does not carry it.

Three named bands in `capacity.py` replace bare floats so a wrong value is visible
in a diff:

| Band | Value | Meaning |
| --- | --- | --- |
| `PRIORITY_BACKGROUND` | `0.0` | The implicit default. Nobody waits on it. |
| `PRIORITY_FOREGROUND` | `10.0` | A parent turn is blocked on the result. |
| `PRIORITY_OPERATOR` | `20.0` | An operator explicitly started this run. |

Adopted at the four call sites where the caller's nature is unambiguous:

| Call site | Band | Why it is a fact, not a guess |
| --- | --- | --- |
| `tools/builtins/task_tool.py` | foreground | The parent turn blocks in `_await_subagent_terminal`'s poll loop until this child is terminal. |
| `subagents/deep_runner.py` | foreground | A hierarchical child's result is returned synchronously to a waiting parent. |
| `tools/builtins/arena_tool.py` | operator | An operator explicitly started the tournament. |
| `gro/adapters.py` | operator | `compete` is operator-initiated. |

The other eight construction sites (group runner ×2, war room, swarm worker,
lifecycle spawn, batch service, self-improvement, orchestrator domain executors)
deliberately inherit the background default. `swarm/worker.py` is the judgement
call: a swarm is operator-*authored* but long-running background work, so
background is the honest reading and it stays untouched.

**Compatibility rule worth keeping:** the keyword is passed **only when a priority
is set**. Passing `priority=` unconditionally broke every zero-argument `slot()`
in the tree with `TypeError: _ImmediateCapacity.slot() takes 1 positional argument
but 2 were given` — including `tests/test_subagent_executor.py`'s doubles. Because
`execution_capacity` is a caller-supplied extension point, the original call arity
*is* a compatibility surface, and the default path staying arity-identical is the
same inert-by-default property the ordering itself has.

Pinned by `tests/test_subagent_capacity_priority.py` — behavioural tests for
ordering and preemption, plus source pins for the argument shape and the adopted
call sites (a runtime assertion here would have to drive the whole mocked agent
graph, which proves the graph ran, not that the right keyword was passed). The
`PRIORITY_BACKGROUND`-is-never-explicit rule is itself a test.

### 2.0.3 Automatic vs manual — the full audit (measured 2026-10-17, `main`)

Before building anything else, this is what the system actually does **without a
human choosing**. Anything not on this list is operator-selected at the composer
and is a UI/ergonomics problem, not an automation gap.

**Already automatic (no operator action):**

| Behaviour | Where |
| --- | --- |
| Delegation tier routing (`general-purpose` vs `bash` policy) | `task_tool` |
| Capacity admission, queueing, rejection, queue timeout | `SubagentExecutionCapacity` |
| `max_total_per_run` ledger | `SubagentLimitMiddleware` |
| Resilience: escalation, promotion of repeated tool patterns | executor recorders |
| Arena cost projection before spend | `arena/service.py` |
| Arena verdicts (compare / synthesize) | `arena` |
| Delegation defaults **on** or off | — **see the correction below** |

**Manual today (operator picks at the composer, then it is automatic):**

| Choice | Default | Note |
| --- | --- | --- |
| Delegation mode itself | **`off`** | `ChatView.tsx:1016` `useState("off")`; `autonomous: true` is sent only when the mode ≠ off (`:2461`). So the flagship capability is **opt-in per thread** and silently off for a new thread. |
| Worker / subagent type | model-selected | Re-validated against `allowed_subagents`; a stale composer selection fails explicitly rather than running another worker. |
| Intent category (`research` / `quick`) | `general` | |
| Arena participation | none | Operator starts the tournament; N competitors then run automatically. |
| Council strategy | none | Config-only; no write route (§4). |
| Arena / dynamic-workflow / council / deliberation / orchestration | none | Config-only families with **no write route** (§4). |

**Built but not adopted — the honest "inert" list:**

| Thing | State |
| --- | --- |
| Priority admission | now adopted (§2.0.2) |
| `gro.enabled` | `false` in live `config.yaml` |
| Deep-agent runner | registered in the gateway lifespan but has no `deepagent:` config block |

The delegation-defaults-off finding is the one with real product weight: a user
who opens Alpha and asks a multi-step question gets no delegation at all unless
they first find the mode selector. That is a Phase 5 (ergonomics) decision — see
§5 — not something to change silently.

**The one genuine architectural gap.** Today `task`, durable batches, Arena and
workflow swarms each hold a private ceiling and all draw from the same
`subagent_runtime.max_running: 3`. They **contend**; they do not multiply.

### 2.1 Contract

One budget, one admission queue, priority-ordered:

```
priority = scenario_priority
         × urgency              # foreground (blocking) vs background (leased)
         × confidence_penalty   # a low-confidence auto-plan yields to an explicit choice
         / estimated_cost_weight
```

A background delegation is admitted without blocking the parent; a foreground one
blocks because the parent needs the result. Alpha already has the lease,
heartbeat, `cancel` and `checkpoint` primitives this needs.

### 2.2 Files

| File | Change |
| --- | --- |
| `alpha/subagents/runtime.py` | replace FIFO admission with priority admission |
| `alpha/subagents/capacity.py` | single resolved budget accessor shared by every mechanism |
| `alpha/config/limits.py` | one `budget_scope` constant (`run` \| `thread` \| `process`), default `run` |
| `alpha/arena/*` | charge Arena against the shared budget instead of a private counter |
| `alpha/workflow/*` | same for swarm waves |
| `config.example.yaml` | `subagents.budget_scope`, `subagents.admission.policy` |

`limits.py` is the mechanical owner (`tests/test_limit_authority_consistency.py`
pins that boundary) — the new constant goes there, not into a module that
re-derives it.

### 2.3 Honesty requirements

- A `queue_timeout` **reports**; it never degrades silently.
- A scenario may only **lower** a ceiling. Raising one needs an operator opt-in.
- Contention is observable: when Arena starves a `task` fan-out, the reason
  appears in the run record.

### 2.4 Separating decision — the workflow wave pool

Whether `workflow/runtime.py` should join the shared admission controller.

* **For:** one real ceiling; today a swarm can exceed the operator's
  `max_running` by running on its own pool.
* **Against:** an existing test deliberately asserts `.slot()` is absent there,
  with the reason "the engine must not pretend to acquire the governor it never
  uses". Unifying it also has to reconcile two different concurrency
  measurements (`peak_in_flight` vs the pool's `running`/`queued`).

This is a **decision, not a bug fix**, and it should not be done as a side effect
of Phase 2. Options: (a) leave the pools separate and make the *combined*
ceiling explicit in configuration and reported honestly; (b) unify and retire
`peak_in_flight`; (c) give the workflow pool its own budget in the same accounting
so the operator sees both numbers rather than one.

### 2.5 Gate

Arena at 64 concurrent with a swarm **demonstrably contend and both report it**,
and neither can exceed `max_running` in aggregate. Plus: a scope change from
`run` to `process` changes measured behaviour in the direction the docs claim.

---

## 3. Phase 3 — Scenario engine (shadow-first)

The largest genuine addition, and the one that most needs restraint.

### 3.1 Files

| File | Change |
| --- | --- |
| `alpha/subagents/scenarios.py` | **new** — the classifier |
| `alpha/subagents/plan.py` | **new** — `ExecutionPlan` and `resolve()` |
| `alpha/workflow/intent.py` | reuse `PerceivedIntent`, do not re-perceive |
| `alpha/subagents/registry.py` | expose the scenario catalogue for the UI |

### 3.2 `ExecutionPlan` is immutable and inspectable

```python
@dataclass(frozen=True)
class ExecutionPlan:
    scenario: str
    reason: str                      # why this scenario matched
    dispatch: str                    # none|single|fan_out|arena|council|pipeline|async
    workers: tuple[WorkerSpec, ...]  # name/category/type + model + tools + budget
    concurrency: int                 # clamped to the operator ceiling
    per_run_total: int               # clamped to max_total_per_run
    depth: int                       # clamped to limits.MAX_DELEGATION_DEPTH (3)
    verification: tuple[str, ...]
    budget: Budget
    requires_confirmation: bool
    degradations: tuple[str, ...]    # what was clamped, and to what
```

`degradations` is the honesty payload: **the plan never silently shrinks.** A
plan that wanted 24 workers against a ceiling of 3 produces a disclosure, not a
quiet clamp.

### 3.3 Sequencing — shadow mode is not optional

1. **Shadow (one release):** classify and display, execute nothing. Report how
   often the predicted scenario matched what actually happened.
2. **Advisory:** show the plan; the operator may follow it or override.
3. **Enforcing:** the plan steers, and any divergence is recorded.

Shipping straight to enforcing would let a misclassifier silently reshape every
run in the deployment.

### 3.4 Anti-goal

Do **not** build a router that picks a specialist "because it sounds relevant".
The research is unambiguous: agents duplicate work and leave gaps when briefs are
poor. The scenario is a **cheap classifier**; the parent still writes the briefs.

---

## 4. Phase 4 — Write routes for the orchestration modes

Four of six families in the Execution modes catalogue badge `No read route`
because `execution_tier`, `orchestration_mode`, `EdgeMode`, `agent_presets` and
the global subagent ceilings are `config.yaml`-only.

### 4.1 Contract

- `GET /api/config/delegation` → the **effective resolved** config, with
  per-field provenance (explicit value vs inherited default). This is the same
  `*_source` discipline `bots/model_config.py` already uses.
- `PATCH /api/config/delegation` → validates with **the same rules `config.py`
  enforces at load**, then either hot-reloads or **refuses**. Never
  accept-then-ignore.
- Bounds are **refused, not clamped**, naming the bound.
- An unknown enum value is preserved verbatim and offered back as an option, so a
  value from a newer Gateway is displayed rather than snapped.

### 4.2 Files

`app/gateway/routers/config_delegation.py` (new) ·
`alpha/config/delegation_config.py` (new schema) · `config.example.yaml` ·
Settings UI in `frontend/src/components/sections/`.

### 4.3 Also in this phase

Decide `compare` and `synthesize`. They are advertised Arena modes and **not
implemented** — the backend already refuses them by name, and the catalogue
reports them honestly. Either implement them or withdraw them from the advertised
list; advertising a mode that cannot run is exactly the defect the catalogue
exists to catch.

### 4.4 Gate

Every control in Settings corresponds to a route that returns the server's
**confirmed** value after a write, and a rejected write leaves the previous value
intact and says why.

---

## 5. Phase 5 — Ergonomics (make what exists reachable)

The capability exists; the UI does not expose it. This phase is mostly wiring.

1. **Scenario selector** in the composer, showing the resolved plan and its
   degradations before submission.
2. **Background delegate affordance** — a handle with steer/cancel, driving the
   control plane that already exists at `/api/subagents/control`.
3. **Per-delegation attribution** — which mechanism ran it (`task` / Arena /
   durable batch / swarm) and what it consumed, using the measured
   `tokens_consumed` and `tokens_returned` split the contracts already carry.
4. **Link the Execution modes catalogue** from the composer, beside the new
   `engine:` group.

Gate: a background delegation is reachable from the composer and reports
honestly, including when it is refused.

---

## 6. Phase 6 — Isolation hardening

1. **Worktree-per-writer** for concurrent code edits. `workspace_mode:
   "isolated"` is the primitive; the worktree is the collision fix both Claude
   Code and Codex document.
2. **`fork_turns: "none"` equivalent** — a child must never hydrate the parent's
   context, so prompt tokens do not scale with parent history.
3. **Per-worker `sandbox_mode`** narrowing (Codex's shape), defaulting to
   **inherit** so behaviour does not change silently.

Gate: two concurrent writers never collide; a read-only worker physically cannot
write, independent of its prompt.

---

## 7. Phase 7 — Per-task model in one fan-out

Enables the Hermes economics: strong orchestrator, ~10× cheaper children.

`resolve_subagent_model_name` already resolves a model per subagent; it needs a
per-request input so one fan-out can carry three models. Cheap executor,
expensive verifier, cheap executor.

Gate: one fan-out with three different models, each resolved and reported; the
measured token/cost split shows the routing actually happened.

---

## 8. Phase 8 — Team / claim mode, then blackboard

**Only if the need is real.** Claude Code's Agent Teams claim tasks from a shared
list; Alpha's kanban board is already a task store, so implement `claim`/
`release` on it rather than building a second queue.

The blackboard (a *structured* shared solution space with named panels, each
specialist writing only its own) is research-grade. **Do not** build it as shared
chat history — that is the failure mode the pattern exists to avoid.

---

## 9. Cross-cutting: what every phase must honour

1. **TDD is mandatory** (`backend/AGENTS.md`). Tests first, in `backend/tests/`.
2. **Never fabricate.** A field is populated only from measured data. This is why
   `deep_runner` leaves `unified_diff`, `security_stamps`, `invariant_assertions`
   and `artifacts` empty on every run.
3. **Bounds are refused, not clamped**, and the bound is named.
4. **An absent value is `null`, never `0`** — zero reads as an instruction.
5. **A failed read is not an empty result.** Independent reads report
   independently (`Promise.allSettled` semantics on the backend too).
6. **A degradation is always reported.**
7. **A completed run is never "verified"** — verification is a separate gate, and
   a failed gate blocks rather than annotates.
8. **Harness/app boundary** — logic in `alpha.*`, wiring in `app.gateway`.
   `tests/test_harness_boundary.py` enforces it.
9. **Update `subagents/AGENTS.md` in the same change set** as any behaviour change.

---

## 10. Recommended order and the reason for it

```
Phase 1 (measure)   -> decides Phase 2's shape; cheap; no risk
Phase 2 (budget)    -> every later phase spends through it
Phase 4 (write routes) -> makes Phase 3's plan configurable at all
Phase 3 (scenarios) -> the big one; needs a budget and configurable ceilings
Phase 5 (ergonomics)  -> makes 2/3 visible
Phase 6 (isolation)  -> independent; do whenever parallel work lands
Phase 7 (model routing) -> cost lever; needs Phase 2's accounting
Phase 8 (teams/blackboard) -> last, and only on demonstrated need
```

Phase 4 sits before Phase 3 deliberately: a scenario engine that wants to
respect an operator's ceiling needs that ceiling to be settable at runtime.

---

## 11. Risks

| Risk | Mitigation |
| --- | --- |
| Fan-out is ~15× tokens **[vendor]** | shadow-first; never self-elevate; project before running |
| Fan-out over non-independent work is *slower* — sequential metered 2.6× fewer tokens and was faster **[third-party]** | the scenario's independence check is a hard veto, not a preference |
| Tool-scope prompts are not a security boundary | only the runtime allowlist is; Phase 6 enforces it structurally |
| A cheap executor that cannot do the work produces confident nonsense | route by *measured* success per task class; fall back |
| Auto-configuration makes spend *less* controlled | degradations, projections, and no self-elevation |
| Version drift in third-party behaviour is fast | pin and re-measure; do not trust §2's third-party numbers beyond their date |
| A misclassifier silently reshapes every run | Phase 3 shadow mode for a full release before enforcing |

**Do not build:** a free-form agent marketplace · debate by default (voting is
comparable at lower cost, NeurIPS 2025) · fully-connected topologies (72.2% of
systems do this; cost grows with *n*) · unbounded self-repair loops · deep
nesting "because it's possible" (already depth 3) · a second subagent framework
(the control plane, handoff contract and resilience already exist).

---

## 12. Open decisions

| # | Question | Default if unanswered |
| --- | --- | --- |
| 1 | Run Phase 1's measurement now? | **yes** — it is cheap and de-risks Phase 2 |
| 2 | `budget_scope`: `run` (today), `thread`, or `process`? | `run`, unchanged |
| 3 | Shadow mode for a release before enforcing? | **yes** |
| 4 | Implement `compare`/`synthesize`, or withdraw them? | withdraw from the advertised list |
| 5 | `execution_tier`: stay auto-routed, or add an override? | stay auto-routed |
| 6 | Depth 3 with a 6-per-run budget that binds first — which is the real ceiling? | keep both; document that the run budget binds |

---

## 13. Verification discipline for this work

The failure mode that cost time on Phase 0 was **verifying against a stale
build**: a probe against a `next start` serving an older `.next` reported the
feature missing when the code was correct and committed.

- `next dev` and `next start` must not be confused; confirm which one serves
  the port and which `distDir` it reads.
- After `next build`, wait for `prerender-manifest.json` (not `BUILD_ID`) and
  confirm `server/vendor-chunks` is **absent** — a build with it can boot and
  then 500.
- A supervisor (`start.ps1 -WatchdogMode`) must be stopped **before** building,
  or it respawns the server mid-build.
- Claim "visible in the browser" only after reading the rendered DOM, and say
  which build it was read from.