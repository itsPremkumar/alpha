# ALPHA AI — AUDIT REPORT, PART II: SUBSYSTEM DEEP-DIVES

Companion to `ALPHA_AUDIT_REPORT.md` (Part I: architecture map, execution trace,
evidence ledger, findings F-01…F-18, testing/ops audit).

**Verification discipline:** each subagent ran **without a working `grep`**, and each
correctly flagged its own repo-wide negatives as weaker evidence. Every HIGH claim
below was therefore **independently re-verified by me** with a working
`Select-String` search before publication. Two claims were **corrected** as a result
(ESTOP, and the "no enforcement consumer" framing) — see §5.8.

Three of six deep-dives complete: **agent harness**, **persistence/recovery**,
**memory/observability**. Git/coding-agent, security, and docs-vs-reality are still
running.

---

# 5. AGENT HARNESS — the loop is real and well-bounded; the goal layer is not

## 5.1 What the loop actually is (extends F-01)

The node graph is built **inside LangChain**, not by Alpha. From
`backend/.venv/Lib/site-packages/langchain/agents/factory.py`:

```
model, tools,
{middleware}.before_agent | .before_model | .after_model | .after_agent   (per middleware)
```

Entry = first `before_agent`; loop entry/exit = first `before_model` / `after_model`
(`:1602-1612`); `START → entry`; `tools → {loop_entry, exit}`; the loop exits when
the model returns **no** tool calls (`:1866-1868`) or when middleware `jump_to`
redirects. Alpha's contribution is the **middleware chain** (`agent.py:494-837`).

**The structural risk this creates:** the chain is *positional*. First-listed is the
outermost `wrap_model_call`, and the `after_model` chain runs in **reverse**
(`factory.py:1737-1749`). Correctness of `ClarificationMiddleware` and
`SafetyFinishReasonMiddleware` depends on them being registered last
(`agent.py:801-806`) — enforced by convention and a comment, nothing else.

## 5.2 ✅ Termination is genuinely bounded — I traced the precedence to be sure

This is the finding I most expected to be wrong, so the evidence goes on record.

LangChain binds `recursion_limit: 9_999` (`factory.py:1778-1801`). Alpha beats it:

1. `Pregel.with_config` is overridden to return a **mutable copy**, not a
   `RunnableBinding` (`langgraph/pregel/main.py:927-929`) — so the binding is
   *merged*, not shadowed.
2. At invoke, `ensure_config` applies configs **last-wins**
   (`langgraph/_internal/_config.py:405-406`), and the caller's config is later.
3. Therefore `services.py:941` `config = {"recursion_limit": 100}` wins, and any
   client value is clamped by `_clamp_recursion_limit()` to `min(value, max)` with
   `max` defaulting to 1000 (`app_config.py:290-294`).

**The lead loop cannot run forever.** 100 super-steps ⇒ `GraphRecursionError`.

But `GraphRecursionError` is **not caught specially** in the lead worker — it lands in
`worker.py:1607 except Exception` → `RunStatus.error` with the raw message. Compare
`subagents/executor.py:1677-1745`, which catches it and distinguishes "cut off
mid-turn" from "produced an answer". So a legitimate deep task is reported to the
user as a **run error**, and the lead has no `stop_reason="turn_capped"` equivalent.

## 5.3 F-19 · The effective step budget is ~9× the advertised one · MEDIUM

`worker.py:1478-1497` runs goal continuation as **repeated fresh `astream` calls**:

```python
1478:  await _stream_once(graph_input, initial_runnable_config)
1479:  while not record.abort_event.is_set() and not llm_error_fallback_message and (...):
1497:      await _stream_once(continuation_input, _continuation_runnable_config())
```

Each call restarts `recursion_limit=100`. With
`runtime/goal.py:35 DEFAULT_MAX_GOAL_CONTINUATIONS = 8`, the worst case for **one
run** is **9 × 100 = 900** lead super-steps. Add
`max_total_subagents=6 × max_turns=150 × _GRAPH_STEPS_PER_TURN=8` and one run can
reach **~7,200** subagent super-steps. All bounded — and all invisible, because
`config["recursion_limit"]` is recorded as the string `"framework-default"`
(`agent.py:980-984`).

## 5.4 F-20 · There is no goal hierarchy in the run loop · HIGH

`agents/goal_state.py:22-31`:

```python
class GoalState(TypedDict):
    objective: str
    status: Literal["active"]        # ← no terminal state exists
    created_at: str
    updated_at: str
    continuation_count: int
    max_continuations: int
    no_progress_count: int
    max_no_progress_continuations: int
    last_evaluation: NotRequired[dict[str, Any]]
```

One flat objective string. No `parent_id`, no children, no subgoal, no task node.
**The agent loop has no goal hierarchy at all.**

Worse: the root goal's status is *only ever* `active`. On satisfaction the channel is
**deleted** (`goal.py:631-634`); on failure the goal stays `active` with
`last_evaluation.stand_down_reason` attached — and the next `/goal clear` or new
objective **overwrites it**. The durable record that "the root goal was never met" is
one field away from being erased.

Meanwhile a *correct* goal/plan store exists and is **stranded**:
`goals/models.py:34-44` `PlanVersion` with contiguous versions, one approved plan,
referential integrity, per-owner `RLock` + fsync + atomic replace
(`goals/store.py:51-96`) — and **no run-loop module imports `alpha.goals`**.
`goals/models.py:9` even defines `GoalStatus = ["active","achieved","abandoned"]`,
exactly the vocabulary the runtime goal lacks.

## 5.5 F-21 · The goal evaluator can be satisfied by the agent's own prose · MEDIUM

`runtime/goal.py:370-452` judges completion from `format_visible_conversation` — the
last 30 messages / 12,000 chars. **The agent's own final text is both the claim and
the evidence.** A model writing "All done, verified" with no failed tool calls
returns `satisfied: true`.

Mitigations are real but partial: `has_visible_assistant_evidence`, a fail-closed
`missing_evidence` default. Credit where due — the no-progress breaker keys on a
**SHA-256 of the latest visible assistant text**, not the LLM's free-text reason
(`goal.py:468-484`), which is a genuinely sound design, and stand-down reasons
(`max_continuations_reached`, `no_progress_detected`, `blocked:<blocker>`) are recorded.

Also: what the code calls "goal drift" is a **concurrency guard** —
`worker.py:2230-2233` checks whether the checkpoint/messages changed while the
evaluator ran. There is **no semantic check** that the work still relates to
`goal["objective"]`. Actual drift is unmeasured.

## 5.6 ✅ Subagents are genuinely autonomous — the strongest file in the repo

Traced end to end. Subagents are **not** "a second prompt in the same process":

| Property | Evidence |
|---|---|
| Own system prompt | `executor.py:1287-1343` builds a fresh consolidated `SystemMessage` per execution; `create_agent(system_prompt=None)` so it enters via state |
| Own tool set | `executor.py:969-975` `_filter_tools(...)`; `task` force-disabled at source (`task_tool.py:1023`) — **no recursion** |
| Own model | `executor.py:927` `resolve_subagent_model_name(...)`, overridable per-agent/category |
| Own memory | `executor.py:1177-1207` loads its **own user-scoped** skill store, intersected with the parent's allowlist |
| Isolated context | `checkpointer=False` (`:1074`); parent history injected only as **untrusted background data** (`:1290-1291`) |
| Own event loop | `executor.py:632-756` process-wide daemon thread, `atexit` shutdown, ambient `ContextVars` selectively stripped (`:759-794`) |

**Three independent caps, honestly surfaced:** turns (`max_turns × 8` super-steps,
`executor.py:93-100`, with a *measured* justification comment at `:75-89`); **tokens
— enabled by default at 1 M** (`subagents_config.py:44-71`), the opposite of the lead
path; and wall clock 1800 s.

**Result validation is real and two-layered** (`task_tool.py:1182-1272`): citation
cross-check against receipts harvested from the child's own tool messages, plus a
deterministic acceptance checklist. Uncertainty is preserved, never rounded to pass —
`UNVERIFIED` is a first-class verdict, and the `task` docstring tells the model
*"subagent reports are SELF-REPORTS, not verified facts"* (`:824-835`).

**Honest limitation, documented in-code** (`executor.py:1622-1624`): cancellation is
detected only at `astream` chunk boundaries, so a hung `bash` is not interrupted —
only the 1800 s `wait_for` ends it.

## 5.7 ✅ Swarm scheduling is a real DAG — but the default worker is not an agent

`swarm/runner.py` is correct: real per-task leases, graph validation *before*
dispatch, retry/backoff, a consecutive-failure circuit, a watchdog spawning
speculative backups, aggregation kept separate from execution status. The standout
detail: a late, lease-fenced result **still charges its tokens** (`runner.py:281-285`)
— *"the provider call already happened… losing the lease race only decides which
RESULT is authoritative; it does not un-spend the tokens."* `CodingWorktreeWorker`
gets a **per-task** git worktree and branch (`runner.py:112-115`), so two tasks cannot
share a workspace, and the repo root is never model-supplied.

### F-22 · The default swarm worker is a single bare `model.invoke()` with no tools · HIGH

`swarm/worker.py:369-394` → `_deliver_objective` (`:92-111`):

```python
response = model.invoke(
    [SystemMessage(content=system_prompt), HumanMessage(content=f"{objective}...")]
)
```

`EphemeralSubagentWorker` is the **fallback for every task** without a specialist,
bot, or worktree path (`runner.py:116`). No tools, no ReAct loop, no delegation —
despite the class name. Only `SpecialistSubagentWorker` is a real agent, and only when
`task.worker_type == SPECIALIST_WORKER_TYPE`.

So for a default decomposed plan, a "swarm" is **N concurrent single-shot text
generations**: it cannot edit a file, run a test, or call a tool. Credit: the module
docstring is candid (*"no canned summary, no fabricated confidence score"*, `:5-7`).

## 5.8 F-23 · ESTOP: a kill switch the model is told about that stops almost nothing · HIGH

I verified this myself with a working repo-wide search, because it is the most severe
finding in the audit — **and the first pass of this finding was wrong.** The subagent
concluded "no enforcement consumer exists." That is incorrect: there is exactly one.

**The claim, in the model's own system prompt** — `agents/lead_agent/prompt.py:724`:

> **Emergency Stop (`emergency_stop_manage`)**: Instant global pause or resume for
> **all background tasks and subagents** via runtime ESTOP sentinel.

And in the tool's own docstring — `tools/builtins/estop_tool.py:78-79`:

> Allows operators or supervisor agents to **immediately suspend all autonomous
> background tasks, crons, subagents, and goal loops** with zero state corruption.

**What it does:** writes a sentinel file. `runtime/estop.py:16-19` resolves it as
`Path.cwd()/".alpha"/"ESTOP"` — while every other runtime artifact uses
`runtime_home()` and honours `ALPHA_HOME`. Run the Gateway from a different cwd and
the sentinel lands where the reader will not look.

**Complete reference set across harness + app (27 hits, verified):**

| File | Role |
|---|---|
| `runtime/estop.py` | defines the manager |
| `tools/builtins/estop_tool.py` | writes the sentinel |
| `tools/__init__.py:85` | registers the tool |
| `commands/module_a_handlers.py:458-463` | reads status (a command handler) |
| **`rsi/switchboard.py:41-59`** | **the only gate** |
| `policy.py:42`, `prompt.py:724` | names the word |

`rsi/switchboard.py` composes three freeze sources into `rsi_frozen()`, consulted at
**RSI cycle start only** (`guard_cycle_start`, `:101-105`).

**Not referenced in any place a run could be stopped:** `runtime/runs/worker.py`,
`runtime/runs/manager.py`, `runtime/lane_scheduler.py` (which holds the hard admission
gate `RunAdmissionController.try_admit`), `app/gateway/autonomy/loops.py` (all 8
registered loops), `app/gateway/services.py`.

**So:** engaging ESTOP does **not** pause runs, does **not** stop the worker, does
**not** suspend subagents, does **not** stop crons or the autonomy loops. It gates
one RSI subsystem. The model is told it can halt the fleet; the operator is told the
same; neither is true. A kill switch that visibly exists, is advertised in the system
prompt, and does not kill is worse than no kill switch — it converts a belief in safety
into a false one. *(RSI is itself almost certainly off by default, which reduces blast
radius but does not correct the claim.)*

### 5.8.1 The project's own tests confirm the scope — and confirm the claim is false

The full repo-wide search returned. **`is_engaged()` has exactly two production
callers:** `estop.py:64` (its own status dict) and `rsi/switchboard.py:51` (the RSI
gate). `module_a_handlers.py:463` merely iterates the status dict's keys; it is a
*status reader*, not a gate.

The decisive evidence is the test suite. The **only** behavioural test of ESTOP's
effect is:

```python
# backend/tests/test_rsi_kill_switch.py:54
def test_estop_engagement_refuses_cycle(runtime_home, monkeypatch):
    manager = EmergencyStopManager(root_dir=runtime_home / "estop")
    monkeypatch.setattr(switchboard, "_estop_manager", lambda: manager)
    manager.engage(reason="estop INC-777: physical motion fault")
```

**The test is named for exactly what ESTOP does: it refuses an RSI cycle.** Alpha's
own test suite documents the true scope. Every other ESTOP test
(`test_emergency_stop_system.py`, `test_dormant_wiring_p3.py`) asserts only that the
sentinel file appears and that a receipt is written — never that anything stopped.

And the tool's own return string asserts the false claim to the caller:

```python
# tools/builtins/estop_tool.py:98
return f"ESTOP successfully engaged at '{sentinel}'. Fleet execution paused."
```

**Fleet execution was not paused.** This is a finding the project could have caught with
the test it already has, by asserting that a run refuses to admit.

## 5.9 Other harness findings

| ID | Sev | Finding |
|---|---|---|
| F-24 | MEDIUM | **No wall-clock timeout on an interactive lead run.** `RunStatus.timeout` exists but `worker.py` never sets it; `RunRecord` has no deadline. 100 super-steps × 90 s provider latency ≈ 2.5 h on one admission slot; `RunAdmissionController` defaults to `pool_size × 2` (`lane_scheduler.py:256-262`), so ~10 such runs 429 every new run. |
| F-25 | MEDIUM | **Lead token budget ships disabled.** `TokenBudgetMiddleware` registered at `agent.py:754-759` only `if enabled`; `token_budget_config.py:110` defaults `False`; `config.example.yaml:96` `enabled: false` — directly under a comment claiming *"Prevents runaway API costs by enforcing hard token limits per run."* Cost is bounded by **step count, not dollars**; one step can be an enormous context. |
| F-26 | MEDIUM | **`alpha.critique`, a deterministic AST code reviewer, is off by default** (`capabilities/catalog.py:342-347`, `default_enabled=False`). Zero-LLM-cost static review exists, is complete, and is unused while the finish-verifier reaches for an LLM rubric. Its docstring also references a `critique_scan.py` that does not exist. |
| F-27 | MEDIUM | **Lead-run failures never become escalation records.** `runtime/escalation.py` is a well-built JSONL handoff ledger with a cross-process file lock and psutil liveness, and `DOMAIN_AGENT` exists — but no run-lifecycle module calls it. Reached only from swarm and subagent recovery. |
| F-28 | LOW | Nesting depth 1 is enforced by a **denylist string** (`"task"` in `disallowed_tools`), not a depth counter. A future tool re-enabling `task` would nest with no guard. |
| F-29 | INFO | ✅ `alpha.critic` is **genuinely live** and the best-implemented review layer in the codebase: `FinishFirstVerifierMiddleware` **withdraws** a terminal message (`RemoveMessage`) and re-asks, once per run, fail-closed if the critic crashes, and — critically — treats `ToolMessage.status` as untrustworthy, resolving anything absent to `"unknown"`, never `"success"`. |
| F-30 | INFO | ✅ `LoopDetectionMiddleware` has two independent layers (order-independent multiset hash + per-tool frequency window), strips `tool_calls` rather than raising, correctly scoped `(thread_id, run_id)`. |
| F-31 | INFO | ✅ `reasoning/loopguard.py` explicitly documents that it does **not** enforce anything and is absent from the capability catalog. Rare, valuable honesty. |

---

# 6. PERSISTENCE & RECOVERY — a strong core, and one documentation lie

## 6.1 ✅ What is genuinely correct (and should not be "fixed")

- **`UNKNOWN` is a real state, not a label.** `statuses.py:166-178` —
  `UNKNOWN → {RECONCILED}` is the *only* legal exit; `COMPLETED`/`FAILED` are terminal.
  An exception deliberately leaves the entry **unsettled** rather than recording
  failure (`recorder.py:378-385`), because whether an irreversible effect landed is
  precisely what is unknown. Compensation is attempted and the ledger is **not**
  settled (`recorder.py:20-51`).
- **Resume gating uses an exact-match node allowlist**, not substring
  (`run_recovery.py:105-106`: `{"model","agent","lead_agent"}`) — deliberately, so
  `charge_customer_model` cannot pass. A pending **tool** node becomes
  `recovery_confirmation_required` (`:197-209`).
- **Startup orphan reconciliation is lease-aware.** `claim_for_takeover` re-checks
  status **and** lease in one conditional UPDATE (`persistence/run/sql.py:713-724`),
  run **before** the server accepts requests (`deps.py:643-658`).
- **Dual execution is fenced by the database**, not convention:
  `Index("uq_runs_thread_active", "thread_id", unique=True, ...)` over
  `status IN ('pending','running')` (`model.py:70-76`). Multi-worker **fails closed at
  startup** (`deps.py:84-154`).
- **Ownership loss is terminal for writes.** Once `ownership_lost` is set, all status
  persistence is refused (`manager.py:409-417`), closing the late-finalization race.
- **Windows watchdog details most projects get wrong:** PID-reuse detection via
  process start time (`watchdog.ps1:213-230`) and an OS-level supervisor lock so two
  supervisors cannot both act (`:299-319`). It is itself supervised
  (`Invoke-WatchdogOfWatchdog`, `:604-656`).

## 6.2 ✅ The "sole continuation authority" claim **holds** — verified

A second resume path is the classic way duplicate work happens. For chat runs, there
is none:

| Caller | Continuation? |
|---|---|
| HTTP create/stream/wait | user-initiated |
| Scheduled tasks | **excluded** — `run_recovery.py:403-409` blocks on `scheduled_task_id` |
| MCP notifications | **excluded** — same predicate |
| Regenerate / edit-replay | **excluded** — `:410-416` blocks on `replay_kind` |
| `NetworkWaitService` | **structurally cannot** — `deps.py:703` constructs it with **no `launcher=`**, so `resume_due()` returns `[]` |
| `alpha.orchestrator.restart` | sweeps swarm/subagent/bot only; no `start_run` in its call graph |

`SafeRunRecoveryService._launch_recovery` imports the *same* `start_run` boundary
HTTP uses, with `idempotency_key=f"auto-recovery:{source.run_id}"` — so a crash
between inspection and admission reuses the admission rather than spawning a second
worker. **This claim is accurate.**

## 6.3 F-32 · A documentation file flatly contradicts the code about the side-effect ledger · HIGH

`docs/architecture/durable-runtime.md:266-270` and `:434-446` state:

> *"No production module constructs `SqlSideEffectLedger`… in a real deployment no
> effect is ever announced, the table is empty."*
> *"`SqlSideEffectLedger` … but no module under `backend/app/` or the harness
> constructs it."*

**Refuted by code.** `app/gateway/deps.py:519-524`:

```python
from alpha.persistence.side_effects import SqlSideEffectLedger
app.state.side_effect_recorder = SideEffectRecorder(SqlSideEffectLedger(sf))
set_side_effect_recorder(app.state.side_effect_recorder)
app.state.side_effect_reclaimer = SideEffectReclaimer(app.state.side_effect_recorder.ledger)
app.state.side_effect_reclaimer.start()
```

…and a real call site wraps an irreversible submit — `app/mcp_tasks/service.py:190-199`:

```python
async with announce_effect(tool_call_id=..., level=SideEffectLevel.HIGH_RISK) as effect:
    submission = await driver.submit(driver_request)
```

The sibling `runtime/AGENTS.md` was updated to match
(`side_effect_ledger_production_writer: exists`); **this doc was not.** Two sibling
documents now disagree about whether a safety mechanism is live. An operator reading
it would conclude the ledger is dead code and would not rely on it. **The code wins:
it is real, and the doc is wrong.** The most serious documentation defect found.

**Caveat keeping this from CRITICAL:** ledger *coverage* is narrow. Only MCP task
submit is bracketed. `bash`, `write_file`, `git_push`, browser actions and payments
are classified in `_TOOL_LEVELS` but **not announced**. The run-level node-allowlist
gate is what protects them and it is sound — so there is no duplicate-side-effect path
via auto-resume, but the per-effect ledger is not a general safety net.
*(Full `announce_effect` call-site enumeration UNVERIFIED — the deep-dive lacked grep.)*

## 6.4 F-33 · The user-visible message feed is the one durable plane that defaults to volatile · MEDIUM

`config.example.yaml:3709-3712` ships:

```yaml
run_events:
  backend: memory     # "No persistence, data lost on restart (default)"
```

Every other durable plane defaults to SQLite. Checkpoints survive; **the feed the UI
actually reads does not.** After a crash the conversation graph state is intact but
`GET /messages/page` — the transcript — is gone, partially mitigated by
`ensure_checkpoint_history_seeded` (`services.py:1456-1510`), which only re-seeds
*legacy threads with an empty feed*. This directly undercuts "the work is not lost."

## 6.5 F-34 · A 2-hour outage becomes a task failure, contradicting the stated guarantee · MEDIUM

`durable-runtime.md` promises a network outage does not become a task failure. The
mechanism: the worker parks on a *provable* link-down as `stop_reason=network_waiting`
— correctly, because a **timeout does not park** (a timeout cannot distinguish a wedged
proxy from healthy work). `NetworkWaitRepository` records the park durably.

But `deps.py:703` installs **no resume launcher** on the service designed to hold
those sessions, so continuation falls entirely to `SafeRunRecoveryService`'s
stop-reason scan — whose own bound is `max_resume_attempts: 3` with
`resume_backoff_seconds: 5.0`. **A 2-hour outage exhausts the budget in ~15 seconds
and lands on `recovery_exhausted`, long before the link returns.** The doc states the
bounding honestly but never states that consequence, so an operator tuning
`max_resume_attempts` for normal flapping has no warning it is also the entire outage
budget.

## 6.6 Other durability findings

| ID | Sev | Finding |
|---|---|---|
| F-35 | MEDIUM | **Checkpointer schema default is `memory`.** `database_config.py:143-146` defaults `backend="memory"`; the shipped template overrides to `sqlite` (`:3637-3639`) and `scripts/configure.py:40` copies it verbatim. A hand-written or truncated `config.yaml` gets `InMemorySaver` — total loss of all graph state — announced only at `logger.info`. |
| F-36 | MEDIUM | `runtime/selfheal/` declares 5 fault types and 3 healing actions (`models.py`); `watchdog.py` constructs **1 of each** (`STALE_LOCK` / `CLEAR_LOCK`). `TERMINATE_PROCESS` and `RESET_STATE` are never produced. Surface overstates detector. |
| F-37 | LOW | `PLANNED_SHUTDOWN_ORDER` declares 7 phases; the Gateway **registers 3** (`ADMISSION_CLOSED`, `OPERATIONS_DRAINED`, `WORKERS_STOPPED`). `is_clean` inspects only *registered* steps, so an incomplete drain reports clean for the four unimplemented phases. |
| F-38 | LOW | `wait_registry.py:22-24` docstring *and* inline comment both claim *"the default launcher is `app.gateway.run_recovery`."* The signature is `launcher: Launcher \| None = None` — **there is no default.** This is exactly the stale comment that would invite someone to "fix" it by wiring a launcher and creating the second continuation authority §6.2 says does not exist. |
| F-39 | INFO | ✅ `alpha.selfrepair.engine` is **honest refusal** — `attempt_repair` returns `outcome="refused"` for every kind, `verify_repair` returns `False` with a reason. No false repair is ever reported. |
| F-40 | INFO | ✅ `alpha.runtime.supervisor` is a correct, complete, sliding-window process supervisor — and `durable-runtime.md:430-433` truthfully says it is **not** wired; `start.ps1` owns process startup. A disclosed gap, not a hidden one. |

---

# 7. MEMORY & OBSERVABILITY — real provenance, defeated by `logger.debug`

## 7.1 What memory actually exists

| # | Layer | Store | Live? |
|---|---|---|---|
| 1 | DeerMem user/agent facts (default) | files + SQLite FTS5 index, **no vector DB** | ✅ default-on, middleware read+write, 10 CRUD routes |
| 2 | L1 typed pipeline | per-(user,agent) JSON, BM25+ngram | ✅ wired, but `enabled` defaults `False` (config value UNVERIFIED) |
| 3 | Cognitive 6-tier | per-owner `cognitive_state.json`, fsync+atomic replace | ✅ 15 routes + a model tool + direct prompt injection |
| 4 | Wave-2 typed types (affective, narrative, prospective, social…) | per-type JSON | ⚠️ **read wired, write is an allowlisted orphan** |
| 5 | A **second** `CognitiveMemorySystem` | **in-memory**, path supplied by the model | ✅ exposed as a model tool |
| 6 | Session search (cross-thread) | SQLite FTS5 over turns | ✅ sanctioned cross-thread path |
| 7 | Active memory router | files + System One escalation | ❌ `default_enabled=False` |

Layers from the brief that do **not** exist as distinct stores: short-term (working
memory is owner-scoped, not thread-scoped), team/org, and **project** — there is no
project-scoped memory store anywhere.

**There is no thread-level memory isolation anywhere in the codebase.** Cross-thread
reading is the sanctioned `session_search` path; everything else is shared across a
user's threads.

## 7.2 ✅ Genuinely strong: provenance and contradiction handling

- `Fact` carries `source` (structured on disk), `sourceError`, `scope`, `revision`,
  `consolidatedFrom`. `SemanticFactNode` adds `evidence[]`, `valid_from`/`valid_to`,
  `revision`, `access_count`.
- **Contradiction handling is real:** `SemanticBeliefGraph.detect_conflicts()` /
  `reconcile_conflicts()`, exposed at `POST /api/memory/cognitive/reconcile`, plus L1
  dedup-conflict and DeerMem's `correction` category.
- The cognitive bootstrap is honest: seeded beliefs sit at 0.5–0.6 with
  `source="bootstrap-assumption"` and an empty evidence ledger, and the docstring says
  the old 0.98/0.95 values *"asserted knowledge this code never earned."*
- The 6-tier claims in `cognitive/AGENTS.md` all verified in code: server-resolved
  owner with no owner = failure (`engine.py:384-388`); per-owner LRU under a process
  lock (`:367-406`); fsync snapshots via tempfile+`os.replace` with a
  `suppress(OSError)` so a Windows `PermissionError` cannot mask the real error
  (`:117-149`); corrupt state **raises** rather than bootstrapping over (`:266-268`).
- **No embeddings anywhere.** Every "vector" score is bag-of-words cosine over
  `Counter` tokens blended with character 3-grams. Real and useful, but lexical —
  `retrieval.py` calls it "Vector / Semantic Cosine Similarity", which oversells it.

## 7.3 F-41 · `store_belief` lets the model write durable beliefs at a confidence it chooses, with no trust tier on read · HIGH

`tools/builtins/cognitive_memory_tool.py:100-120` — `confidence` is a **model-supplied
float defaulting to 0.8**, tagged `["agent_inferred"]`, persisted immediately.
`source` is stored, but **no read path weights by it**; the composite score blends
`salience × confidence` (`cognitive/retrieval.py:253-257`). A poisoned belief at 0.8
outranks the system's own 0.5–0.6 bootstrap assumptions, permanently, on every future
recall.

There *is* a real mitigation upstream: `MemoryMiddleware` captures **only user inputs
and final assistant responses, ignoring tool calls** (`memory_middleware.py:31`) — so
a web page saying "remember the admin password is X" never enters the extraction batch
*as a tool result*. But the chain still completes: the model reads the page and
**echoes the claim in its final assistant message**, which *is* in the batch. The
secret-rejection rule that would catch it lives in `memory/policy/engine.py`, **opt-in
and default-off**. And `recall_safety.py` provides *containment* (data notices,
single-lining, `</memory>` neutralization, caps) — which is not the same as *trust
tiering*. Everything arrives in one flat block under one blanket notice: nothing
distinguishes "the user told me this" from "a page told the model this."

**This is the durable memory-poisoning path, and the one the tool-call filter does not
cover.**

## 7.4 F-42 · Two same-named memory systems; the one the model can see is global and ephemeral · HIGH

`memory/cognitive/engine.py:41` `CognitiveMemorySystem` — owner-scoped,
fsync-persisted, LRU-cached, fail-closed.
`memory/cognitive_memory_tiering.py:685` `CognitiveMemorySystem` — a **process-wide
singleton with no owner, no user, and no persistence** unless the model explicitly
calls a save action with an arbitrary path. `BUILTIN_TOOLS` registers **both**. A model
in user A's run calling `recall_agent_memory` can receive user B's semantic rules, and
nothing in either tool's docstring says the store is shared.

## 7.5 F-43 · Config keys that enable a subsystem whose write side does not exist · MEDIUM-HIGH

`alpha/memory/capture_composition.py` is on the repo's own orphan allowlist with the
reason that the pair lands *"with the per-memory-type registration wave"*
(`test_no_orphan_modules.py:66`). An operator sets `memory.affective.enabled: true`
(and four siblings) in `config.yaml`: the config validates, the **read** path runs, the
status record says `STATUS_EMPTY`, and the block renders nothing — permanently. The
configuration surface is a lie told in YAML.

## 7.6 F-44 · Both observability substrates are unreachable from configuration · HIGH

- `ObservabilityConfig.enabled` defaults **`False`** (`observability/config.py:68`).
- `TraceConfig.enabled` defaults **`False`** (`observability/trace/config.py:51`).
- **There is no `observability:` or `trace:` key in `AppConfig` at all** — the full
  field list was read. So **neither substrate is reachable from `config.yaml`**, and
  the only way to enable either is to construct it in Python, which nothing does.
- `runtime/journal.py:62-63` says so in-source: *"a no-op unless a writer is installed
  (**nothing installs one in production today**)."*
- Langfuse/LangSmith are opt-in behind env **and** complete credentials; Monocle is
  opt-in and Gateway-lifespan-only, so `AlphaClient` and the TUI stay uninstrumented
  even when enabled.

**A production incident in a default deployment is untraceable at span level.** What
*does* work out of the box is genuinely useful: run-feed event rows plus
`X-Trace-Id`/`alpha_trace_id` on every log line from a single `ContextVar`. It is not
a trace — no spans, no parent/child, no per-stage latency, no agent depth.

## 7.7 F-45 · Integration Health — the panel that proves wiring is wired — fails open · HIGH

`app/gateway/routers/ops_integration.py:86-114`, four subsystems behind
`except Exception: logger.debug(...)`, all returning `{}`, **HTTP 200**:

```python
except Exception: logger.debug(...)   # autonomy supervisor, event bus,
                                      # capabilities, peer network
```

If the autonomy supervisor fails to construct, the endpoint answers 200 with
`autonomy: {}` and the frontend renders a populated panel. The single surface whose
job is integration truth is the one that lies most easily.

*(The same defect class in a client component: `SupervisorSection.tsx:157-158` asserts
*"The supervisor singleton registered no loops in this process"* — a cause the client
cannot know. See F-52.)*

## 7.8 F-46 · The silent-failure gate is satisfied by `logger.debug` · MEDIUM

The repo has a real ratchet: `scripts/check_no_silent_failures.py` against a
`BASELINE` of ~1,570 pre-existing silent failures. But `LOG_METHODS` **includes
`"debug"`** — so `except Exception: logger.debug(...)` counts as *reported*. Six memory
call sites (`prompt.py:922, 950, 969`; `recall_composition.py:297`; `rerank.py:122,155`;
`active_memory.py:122`) are all this shape, and `debug` is off in the shipped default.

The gate is honest about being a ratchet, but its definition of "not silent" is
satisfied by a message no operator will ever see. Worse,
`recall_composition.py:297` computes a `SurfaceStatus(status=ERROR)` and then
**discards it** — `prompt.py:965` takes only `composed.text` — so the module's own
"honest per-surface status" is unreachable to any operator.

The correct pattern already exists in the same codebase and is the counter-example:
`observability/writer.py:538 _fan_out` counts failures per sink, logs once, and
discloses in `disclosure()`.

Two journal call sites are worse still: `runtime/journal.py:84` and `:110` both do
`except Exception: return` with **no log at all**, wrapping every behaviour-trace emit
from the journal.

## 7.9 F-47 · The event catalog does not match the vocabulary operators use · MEDIUM

- `task.*` and `goal.*` event types **do not exist in any catalog.**
- The run feed has **only `llm.tool.result`** — a result. There is no `tool.started`
  and no `tool.failed`, so **a tool that is called and never returns leaves no row at
  all**, and a failure visible only as a missing row is indistinguishable from a tool
  that was never called. The contract's own `known_gaps: tool-call-intent` admits this.
- `run.end`'s metadata schema is `{"status": {"const": "success"}}` — **hard-coded** —
  so a run that ended in `error` still carries a `run.end` row reading `success`.
- `catalog.py` is validated against the **JSON contract** in tests, but
  **catalog↔producers is not validated at all**; a producer can emit `run.start` under
  category `message` and nothing catches it.
- Three vocabularies exist. The one the UI reads is live. `observability/events.py` and
  the 18-layer `BehaviourTraceWriter` have **no producer** — the 9/18 `CALL_SITE`
  coverage claim is true of one writer and says nothing about the other.
- **No `goal_id` and no `plan_id` in the event stream.** `seq` is scoped to
  `thread_id`, not `run_id`, so paging one run's events interleaves with other runs.

### F-48 · IM-originated runs record no subagent step history · HIGH

`subagent.*` is emitted only from the parent's `stream_mode=custom` consumer. The web
frontend requests `custom`; the **Gateway default and the IM channel manager
(`app/channels/manager.py STREAM_MODES = ["messages-tuple","values"]`) do not.** So a
Telegram run that delegates three subagents and loses one leaves a feed with nothing in
it. The only record is the terminal `ToolMessage` and `ThreadState.delegations` — which
carry no per-step tool calls, no timing, and no child run id.

**The flagship multi-agent surface is the unreconstructable one.** Fully documented in
`known_gaps`, fully unfixed.

## 7.10 F-49 · No live log rotation · MEDIUM-HIGH *(refines F-15)*

`scripts/rotate_logs.py:12-19` states it plainly: rotation is **pre-launch only**
(5 MiB, 3 backups), invoked by the Makefile before `make dev`/`make start` and by
`start.ps1`. Its own docstring: *"Live rotation of a Python-owned sink is
`RotatingFileHandler`'s job, and that is what `backend/debug.py` now uses"* — a dev
tool, not the Gateway. **The Gateway's stdout/stderr stream does not rotate while
running**, and neither do the Docker/k8s compose files. A 24/7 service on a Windows
host grows `logs/gateway.log` without bound.

## 7.11 F-50 · Raising log level requires a restart · MEDIUM

`apply_logging_level` is called once from `configure_logging` in the lifespan
(`app.py:305`); `log_level` and `logging` are both in `STARTUP_ONLY_FIELDS`. No admin
route re-invokes it. An operator debugging a 03:00 incident edits `config.yaml` or sets
`ALPHA_LOG_LEVEL` and sees no effect until the next deploy. *(UNVERIFIED that no other
caller exists — the deep-dive lacked grep.)*

## 7.12 F-51 · `VitalsStrip` computes its own denominator · LOW-MEDIUM

`WorkspaceVitals.tsx` filters `subsystems` by a hard-coded 7-key list, so if the Gateway
stops returning a subsystem the ratio can go **up** — the failing row disappears and the
health number improves. The module's own docstring names this exact risk and mitigates
it by exporting the list for a test — which proves the list does not *change*, not that
it matches the *server*.

## 7.13 F-52 · Supervisor empty state asserts a cause the client cannot know · LOW

`SupervisorSection.tsx:157-158` — `status.loops.length === 0` renders
*"The supervisor singleton registered no loops in this process."* The client did not
inspect the supervisor; the Gateway returned `{"loops": []}`. If the supervisor failed to
construct, this states a specific cause it has no evidence for. Small, but the same
defect class as F-45 in a client component.

## 7.14 ✅ The frontend debugger is real — a counter-example worth protecting

The deep-dive traced three panels and **manufactured no finding for any of them**:

- `RunInspectorSection.tsx:222-344` `RunStatusPanel` — nine `Measured` tiles, every one
  mapping a real `RunRecord` field, including a genuine two-model derivation
  (configured alias vs `response_metadata.model_name` from the run's own
  `llm.ai.response` rows). `measured()` maps `null → null` and renders "not reported",
  not a dash.
- `RunInspectorSection.tsx:617-740` `DelegationPanel` — **the best honesty pattern in the
  repository.** It deliberately does *not* read the stream-gated `subagent.*`; it reads
  `ThreadState.delegations` and cross-checks against the stream count, printing an
  explicit amber note in *both* disagreement directions. Every row is annotated
  *"delegating work is not the same as that work succeeding."*
- `WorkspaceVitals.tsx:293-455` `VitalsStrip` — every number traces to a named route in
  a `title`; `probesFailed` is a distinct boolean from `subsystems.length === 0`, so a
  failed probe never renders as `0/7`; `costView()` separates a measured `$0.0000` from
  `null` = "not reported".

---

# 8. THE PATTERN ACROSS ALL THREE DEEP-DIVES

The single most important structural observation, and it is not any individual finding:

**This repository has an unusually strong honesty-in-code culture, and it is
systematically defeated at exactly two points.**

1. **By the `logger.debug` idiom.** A silent-failure ratchet exists and counts
   `logger.debug` as "reported" (F-46). Six memory surfaces and two journal emitters
   are therefore silent at the shipped log level while the gate stays green.

2. **By building a complete, tested, self-documenting subsystem and wiring nothing to
   it.** The wave-2 memory types (F-43), both observability substrates (F-44), the 18-
   layer trace writer, the ESTOP sentinel outside RSI (F-23), the versioned plan store
   (F-20), the escalation ledger (F-27), the AST code critic (F-26), the Python process
   supervisor (F-40). In each case the artifact is *better documented than most
   production systems manage*, which makes the gap legible to a reader who goes
   looking — and invisible to an operator using the product.

The second is the more costly failure, because the honesty artifacts describe the
*shape* of the system accurately. That accuracy is what makes the gaps hard to see: the
docs read like a finished system, and the primary operator surfaces — Integration
Health, the run inspector — do not go looking on the operator's behalf.

**Concretely: the failure mode is not dishonesty, it is completion without wiring.**
