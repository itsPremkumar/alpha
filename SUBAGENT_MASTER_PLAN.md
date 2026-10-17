# Alpha Subagents — Complete Capability & Implementation Plan

**Status:** research + design document. Nothing in Section 8 is implemented.
**Date:** 2026-10-17 · **Branch:** `main`
**Scope:** every way an AI agent product uses subagents, an accurate inventory of
what Alpha already does, the real gaps, and a phased plan to reach
full-capacity concurrent multi-variety delegation that configures itself per
scenario.

> **Correction notice.** An earlier draft of this document claimed Alpha had *no*
> async delegation, *no* depth accounting and *no* hierarchical delegation. All
> three were wrong — they were inferred from a `subagents/` glob rather than read
> from the control plane. Sections 0, 4, 5 and 8 are corrected below. The
> research in Sections 2–3 was unaffected.

---

## 0. Executive summary

Alpha's subagent subsystem is **more complete than this plan originally
assumed**. Before asking what to add, the honest answer is: most of the
machinery already exists and is under-used rather than absent.

**What Alpha already has, verified on `main`:**

- A **control plane** at `/api/subagents/control` — `spawn`, `get`, `result`,
  `heartbeat`, `checkpoint`, `replace`, `cancel`, `adopt`, `promote`, `list`.
- **Asynchronous spawn with leases.** `/subagent:spawn` returns a handle with a
  `subagent_id` and lease expiry immediately; a runner is scheduled, not
  awaited.
- **Depth enforcement.** `MAX_DELEGATION_DEPTH = 3`, `MAX_CHILDREN_PER_PARENT = 10`
  (`alpha/config/limits.py`), rejected in `lifecycle.py` at spawn time.
- **A hierarchical delegator** (`hierarchical_delegator.py`) with isolated
  ephemeral workspaces, hard token/time budgets, and typed `DeepHandoffContract`
  returns that keep raw terminal output inside the child sandbox.
- **Resilience and escalation** — automatic replacement (`resilience.py`) and
  promotion/escalation paths.
- **Per-subagent model** via `resolve_subagent_model_name`.
- **A structured result contract** (`StructuredSubagentResult`) carrying
  `status`, `stop_reason`, `result_brief`, `result_sha256`, `tool_receipts`,
  `receipt_verdict`, `acceptance_verdict`, `trace_verdict`.
- **Arena** — competitive subagents with a judge, cost projection and
  confirmation gate.

**The four findings that actually matter:**

1. **One genuinely broken seam: `set_deep_agent_runner` has no production
   caller.** Its only callers are `test_hierarchical_delegator.py`. The module's
   own docstring says that with no runner configured, `delegate` "reports an
   honest `UNRECOVERABLE_ERROR` contract". This is the *same class of defect* as
   the spawn route that "returned a `ready` record and nothing ever started it",
   fixed on 2026-10-06. **Hierarchical delegation is currently unwired.**
2. **The binding ceiling is 6, not 100.** `subagents.max_total_per_run` defaults
   to **6** at `subagent_runtime.max_running: 3`. Arena can reach 64 through a
   separate dispatcher, but everything draws from the same 3 slots — they
   contend, they do not multiply.
3. **Nothing is reachable from the UI, and no write route exists.** The
   `Execution modes` catalogue built on 2026-10-17 badges four of six families
   `No read route` because `execution_tier`, `orchestration_mode`, `EdgeMode`,
   `agent_presets` and the global subagent ceilings are `config.yaml`-only.
4. **There is no scenario layer.** Every mechanism is chosen by hand. Nothing
   classifies the work and resolves a plan. That is the largest genuine gap and
   the core of your request.

---

## 1. Method, and what is verified vs. reported

**Verified in this repository** (read from source on `main`, 2026-10-17):
`SubagentConfig`; `resolve_subagent_model_name`; `StructuredSubagentResult`;
`DeepHandoffContract`; `lifecycle.py` depth rejection; `limits.MAX_DELEGATION_DEPTH = 3`
and `MAX_CHILDREN_PER_PARENT = 10`; every route in `subagent_control.py`; the
`/subagent:spawn` and `/subagent:list` command handlers; the absence of a
production caller for `set_deep_agent_runner`; `subagent_runtime` limits;
`max_total_per_run`; `BUILTIN_CATEGORIES`; `DynamicExecutionTier`; `swarm_mode`;
`EdgeMode`; `CouncilStrategy`; Arena ceilings read live from
`GET /api/arena/config`.

**Reported externally** (Section 14): Claude Code, Codex, Hermes and Anthropic
behaviour, plus cost figures. Third-party measurements are marked
**[third-party]** — most importantly the 5.9× / 37% token figures in §2.8.

**Explicitly unverified** (do not treat as fact):

- Whether the model-facing `task` tool blocks on its child or routes through the
  async control plane. `backend_handlers.py` shows the *command* path does; the
  tool path was not traced to a conclusion. **Measure this before acting on
  Phase 1** — it decides whether that phase is a build or a wiring fix.
- Whether any third-party product's newest behaviour matches the day you read
  this. §12.4 covers drift.

---

## 2. Research synthesis

### 2.1 Claude Code

**Built-ins:** `Explore` (read-only discovery, historically Haiku-class),
`general-purpose` (search-edit-run), `Plan` (strategy, no file edits).

**Definition:** Markdown + YAML frontmatter in `.claude/agents/` (project) or
`~/.claude/agents/` (user). Fields: `name`, `description`, `tools`, `model`.
`/agents` generates one. Files are hot-reloaded within seconds; `add_dirs`
directories are not.

**Precedence:** managed org settings → `--agents` CLI → project → user → plugin.
A programmatic `agents` argument overrides a same-named file.

**Isolation:** own 200K context; the lead sees only the final summary.

**Constraints worth copying:**
- Each invocation is **stateless** — no follow-ups to the same subagent, so the
  initial brief must be complete.
- A child may **not exceed the parent's cost tier**.
- Nesting is off by default; `chat.subagents.allowInvocationsFromSubagents`
  enables it to **depth 5**.
- `maxTurns` returns output marked *partial*, and the agent can be **resumed**.

**Background:** `background: true` returns immediately; Ctrl+B backgrounds a
spawned agent. **Structured:** `output_schema` validates the return, so a child
that quietly stops fails validation rather than silently passing.

### 2.2 OpenAI Codex

Custom agents are TOML in `.codex/agents/`. Required `name`, `description`,
`developer_instructions`; optional `model`, `model_reasoning_effort`,
`sandbox_mode`, `nickname_candidates`. Global:

```toml
[agents]
max_threads = 6
max_depth = 1
```

**Delegation is explicit-request-only by default.** Issue
[#36973](https://github.com/openai/codex/issues/36973) reports conflicting
guidance when positive multi-agent framing meets the restriction, and that broad
`AGENTS.md` delegation instructions get lost after compaction.

**Context forking:** v2 replaced `fork_context` with `fork_turns`; the default
was reported to fully hydrate parent context, and the recommended policy is
`fork_turns: "none"` for bounded isolated forks.

**Sandbox:** children inherit the parent policy; per-agent `sandbox_mode` can
narrow it. **UI:** `/subagents` and `/agent` to switch/inspect/stop; the app
shows active children above the composer.

**Caution from their own docs:** raising `max_depth` "can turn broad delegation
instructions into repeated fan-out, which increases token usage, latency, and
local resource consumption" — keep it at 1 unless there is a reason.

### 2.3 Hermes Agent (Nous Research)

The most comparable system: open-source, delegation-first.

- **`delegate_task(tasks=[...])`** — each task is `{"goal", "context"}`. A list
  runs as a **parallel batch**: the fan-out primitive is one call with N briefs.
- **Async delegation (2026-06, issue #5586).** `async_delegation` spawns a
  background agent and returns a **`task_id` immediately**. Desktop offers
  `Steer` (queue guidance) and `Stop`. **Alpha's `/subagent:spawn` plus
  `heartbeat`/`checkpoint`/`cancel` is the same design already built.**
- **Child isolation:** own conversation, own terminal, scoped toolset, own
  iteration budget.
- **Model routing:** children inherit the parent model; global
  `delegation.model`/`provider` override exists; per-task model inside `tasks[]`
  is still an open request ([#61622](https://github.com/NousResearch/hermes-agent/issues/61622)).
- **`output_schema`** on delegation. They also fixed a wedge where a hung
  background child left a session looking dead: children now make provider
  requests **inline on their own conversation thread** rather than a nested
  worker thread. A stall monitor remains as backstop.
- **Economics:** parent on GLM 5.2 (~$0.95/1M input), children on Nemotron 3
  Super (~$0.09/1M) — ~**10× cheaper per child**.

### 2.4 Anthropic's orchestrator-worker research system

The canonical production reference.

- Lead agent decomposes, spawns **3–5 subagents in parallel**; children use
  **3+ tools in parallel**.
- **90.2%** improvement over single-agent internally; **up to 90%** time cut.
- **~15× the tokens** of chat — spent in *cheap disposable* contexts the main
  model never reads.
- Every brief needs four things: **objective, output format, tool/source
  guidance, clear task boundaries**. Without them agents duplicate work and
  leave gaps.
- Three patterns worth taking without multi-agent: externalise state to memory
  before context fills; isolate workers with self-contained briefs; **verify
  high-stakes outputs with a separate pass**.
- A dedicated **Citation Agent** runs last.

Anthropic's own framing: the full topology is only justified when the task is
**provably breadth-first**.

### 2.5 Claude Code Agent Teams (experimental)

| | Subagent spawning | Git worktrees | Agent Teams |
|---|---|---|---|
| Hierarchy | peer | none | **Team Lead → Teammates** |
| Session | one | many | one |
| Coordination | parent todo list | filesystem | **Shared Task List** |
| Use | subtasks of one goal | parallel features | role-based coordinated work |

Teammates **claim** tasks, mark completion, pull the next — claiming (not
assignment) is what prevents duplication. Communication is the task list and
mailbox, never direct agent-to-agent calls. Feature-flagged and explicitly
experimental.

### 2.6 Agent-as-tool / MCP

An agent can be exposed **as a tool**: `mcp-agent`'s "agent servers" (a
server-of-servers callable by Claude Desktop, Cursor, any MCP client); Google
ADK's `McpToolset` and the inverse (an MCP server exposing ADK tools).

The governing principle, and the reason tool scoping is a *correctness* feature:

> "a subagent that can only read orders cannot delete them, regardless of what
> the model is told to do." **Scope is enforced by architecture, not by
> instruction.**

Alpha already embodies this: the `alpha` leader preset denies `write_to_file`,
`run_command`, `git_push`, `deploy_production`, `drop_database` — it routes but
cannot perform.

### 2.7 Topologies

From the MIT field guide and multi-agent-debate surveys (arXiv 2607.26212, 141
studies):

- **Router** — classify, route to a specialist. No synthesis.
- **Orchestrator-workers** — a central LLM decomposes and synthesises;
  subtasks are *not* pre-defined. That is the difference from parallelisation.
- **Explicit workflow** — developer-defined control flow. Predictable, rigid.
- **Swarm / handoff** — the active agent decides whether to continue or hand off.
- **Blackboard** — a *structured shared solution space* with named panels;
  each specialist writes only its own panel. Explicitly not a chat log.
- **Debate** — agents exchange arguments and converge.

Findings worth acting on: **72.2%** of surveyed systems use a **fully connected**
topology (cost grows with *n*); the field converged on static/fully-connected/
voting **by convention rather than evidence**; sparse topologies are promising
(EMNLP Findings 2024).

Two counter-intuitive results:

- **Debate is not reliably better than voting.** NeurIPS 2025 decomposes debate
  into ensembling + communication and finds **majority voting comparable**.
- Anthropic defines parallelisation as exactly two things: *sectioning*
  (independent subtasks) and *voting* (same task, diverse outputs).

### 2.8 Cost economics

| Claim | Source | Mark |
|---|---|---|
| Multi-agent ≈ **15× the tokens** of chat | Anthropic | vendor |
| **90.2%** quality gain; up to **90%** time cut | Anthropic internal | vendor |
| Child models ~**10× cheaper** (GLM 5.2 → Nemotron 3 Super) | Hermes | vendor |
| Explore inheriting the parent model made fan-outs up to **5.9×** more tokens and **never faster** | Systima | **[third-party]** |
| Pinning children to a cheaper model: **37% fewer** tokens, under half the wall time | Systima | **[third-party]** |
| Non-independent subtasks: sequential metered **2.6× fewer** tokens, ~**5× cheaper**, and was **faster** | Systima | **[third-party]** |
| **50–90%** savings routing bulk work to 10–50× cheaper models | MindStudio | [third-party] |
| Cached input bills at **1/10** → a stable prefix beats a tier downgrade | AgentPatterns | [third-party] |
| Devin Fusion: frontier performance at ~**35% lower** cost | Cognition | vendor |
| Explore pattern: **2–2.5× cheaper at 85–95% quality** | community | [third-party] |

**The rule that falls out:** use orchestrator-workers only when **all four**
hold — (1) subtasks cannot be listed in advance, (2) the work is breadth-first,
(3) correctness is worth comfortably more than ~15× chat tokens, (4) every
worker gets a self-contained brief. Otherwise **route by tier instead of fanning
out**.

The distinction that matters most: *orchestrator-worker is a **quality** lever;
model routing is a **cost** lever.* They are not substitutes.

---

## 3. Complete taxonomy of subagent usage

### 3.1 By dispatch shape

| # | Pattern | Shape | Seen in |
|---|---|---|---|
| 1 | **Delegation** | 1 → 1, wait | universal baseline |
| 2 | **Fan-out / sectioning** | 1 → N concurrent, then synthesize | Anthropic (3–5); Hermes batch |
| 3 | **Fan-in / synthesis** | N results → verifier/judge/citation pass | Anthropic Citation Agent; Arena judge |
| 4 | **Recursive / nested** | children spawn children | Claude depth 5; Codex `max_depth`; **Alpha depth 3** |
| 5 | **Background / async** | returns an id; parent continues | Hermes `task_id`; Claude `background`; **Alpha `/subagent:spawn` + lease** |
| 6 | **Competing / tournament** | N race, judge picks | **Alpha Arena (to 64)** |
| 7 | **Sequential pipeline** | A → B → C | Anthropic "subagent as compiler"; **Alpha workflow graphs** |
| 8 | **Shared-task-list claim** | peers claim from a queue | Claude Agent Teams; Hermes Conban |
| 9 | **Worktree isolation** | one agent per worktree | Codex; Claude parallel dev |
| 10 | **Debate / adversarial** | critique to convergence | MAD literature |
| 11 | **Agent-as-tool** | agent exposed as a callable tool | mcp-agent; ADK |

### 3.2 By role

Discoverer · Planner · Executor · Command specialist · Researcher · Deep
researcher · Fact-checker · Critic/reviewer · Verifier/judge · Test synthesiser
· Security · Performance · Debugger · Architect · Librarian.

### 3.3 By lifecycle

`ephemeral` · `resumable` (**Alpha `checkpoint`**; Claude `maxTurns` + resume) ·
`persistent` (named profile with memory; Hermes bot mode) · `scheduled` ·
`supervised` (workforce/kanban).

### 3.4 By coordination

`none` · `parent-mediated` (default) · `shared-blackboard` · `shared-task-list`
· `peer-messaging` · `market/auction` (research-grade only).

### 3.5 By isolation

`context` · `filesystem` (**Alpha `workspace_mode: "isolated"`**, ephemeral
scratch) · `git` · `tool` (allowlist) · `network` · `process` (`max_running`) ·
`credential/trust`.

### 3.6 By routing

`auto-perceive` (**Alpha tier**) · `operator-pinned` (**Alpha composer**) ·
`category-preset` (**Alpha `category:*`**) · `named-worker` (**Alpha `type:*`**)
· `model-tier` · `capability-match` (Hermes #61622, still open).

---

## 4. Corrected inventory

### 4.1 The control plane (exists)

`/api/subagents/control`:

| Route | Purpose |
|---|---|
| `POST /spawn` | Provision a task-scoped subagent **with an active lease**; runner scheduled, not awaited |
| `GET /{id}` · `GET /{id}/result` | Detail and result |
| `POST /{id}/heartbeat` | Lease renewal |
| `POST /{id}/checkpoint` | Checkpointing |
| `POST /{id}/replace` | Replace a worker |
| `POST /{id}/cancel` | Cancel |
| `POST /adopt` | Adopt orphans |
| `POST /promote` | Promote a subagent role |
| `GET` | List |

Commands: `/subagent:spawn` returns `{subagent_id, role, status, lease expiry}`
plus `autonomous_directives`; `/subagent:list`.

`SpawnSubagentRequest` already carries `objective`, `role`, `instructions`,
`model_override`, `workspace_mode` (default `isolated`), `timeout_seconds`,
`lease_duration_seconds`, `parent_agent_id`, `parent_task_id`, **`depth`**,
`current_action`.

### 4.2 Limits

| Setting | Value |
|---|---|
| `MAX_DELEGATION_DEPTH` | **3** |
| `MAX_CHILDREN_PER_PARENT` | **10** |
| `subagent_runtime.max_running` | **3** |
| `subagent_runtime.max_queued` | 64 |
| `subagents.max_total_per_run` | **6** (1–50) |
| `subagents.token_budget.max_tokens` | 2,000,000 |
| per subagent (`SubagentConfig`) | `max_turns` 50, `timeout_seconds` 900 |
| `disallowed_tools` default | `["task", "ralph_loop", "session_search"]` |

Note the tension worth resolving in Phase 2: a depth cap of 3 and a per-parent
child cap of 10 exist, while the *runtime* cap is 6 per run. A depth-3 tree is
already possible structurally; the run budget is what actually binds.

Arena (live): `default_agents: 4`, `max_agents: 64`, `default_wave: 4`,
`max_wave: 32`, `estimate_tokens_per_call: 60000`,
`require_confirmation_over_calls: 100`, `max_subagent_calls_per_run: 1000`,
`max_tokens_per_run: 50000000`, `max_wall_seconds_per_run: 7200`,
`max_judge_attempts: 2`, `reasoning_bank_seed: true`. Profiles `quick` 3/3,
`standard` 5/4, `deep` 8/4. Modes `decide`/`plan` implemented; **`compare` and
`synthesize` advertised but not implemented**.

### 4.3 Assets stronger than the ecosystem

- **`DeepHandoffContract`** — `executive_summary` (capped 300 words),
  `unified_diff`, `test_oracles`, `security_stamps`, `invariant_assertions`,
  `tokens_consumed` (real usage), `tokens_returned` (parent-visible estimate),
  `session_id`, `agent_type`, `artifacts`, `error_detail`. It carries *evidence*
  (`test_oracles`, `security_stamps`) rather than only a schema — stronger than
  Hermes' `output_schema` and Claude's.
- **`hierarchical_delegator.py`** — isolated ephemeral workspaces, hard token and
  time budgets, clean-context synthesis, and an explicit rule that *only typed
  contracts cross the parent boundary* while raw terminal output and trace dumps
  stay inside the child sandbox.
- **`StructuredSubagentResult`** — status, stop reason, `result_sha256`,
  `tool_receipts`, `receipt_verdict`, `acceptance_verdict`, `trace_verdict`.
- **Resilience** — automatic replacement on failure.
- **Structural least privilege** — the leader preset's deny-list.

### 4.4 The real gaps

1. **`set_deep_agent_runner` has no production caller.** Only
   `test_hierarchical_delegator.py` calls it. In production
   `hierarchical_delegator.delegate` therefore reports the honest
   `UNRECOVERABLE_ERROR` contract. *This is a wiring defect, not a missing
   feature* — and the identical class of bug was fixed in the spawn route on
   2026-10-06.
2. **Unverified:** whether the model-facing `task` tool uses the async control
   plane or blocks. Measure before planning Phase 1.
3. **One shared pool, no cross-mechanism budget.** `task`, durable batches,
   Arena and workflow swarms each hold private ceilings and contend for the same
   3 slots. No unified budget exists.
4. **No scenario layer.** Nothing classifies a task and resolves a plan.
5. **No write routes for the orchestration modes.** `execution_tier`,
   `orchestration_mode`, `EdgeMode`, `agent_presets` and the global subagent
   ceilings are `config.yaml`-only — which is why four of six catalogue families
   badge `No read route`.
6. **No worktree-per-writer** for concurrent code edits, though
   `workspace_mode: "isolated"` exists as the primitive.
7. **No per-task model in a single fan-out** (Hermes #61622; Codex per-agent
   TOML).
8. **No `fork_turns: "none"` equivalent** — parent-context hydration is
   implicit and unmeasured.

---

## 5. Gap matrix

| Capability | Claude Code | Codex | Hermes | **Alpha** | Priority |
|---|---|---|---|---|---|
| Per-subagent model | ✅ | ✅ | global | **✅** | — |
| Structured return | ✅ | — | ✅ | **✅ richer** | — |
| Evidence-carrying contract | — | — | — | **✅ `DeepHandoffContract`** | — |
| Tool scoping | ✅ | ✅ `sandbox_mode` | ✅ | **✅** | — |
| Async / background | ✅ | ✅ | ✅ `task_id` | **✅ spawn+lease** | — |
| Depth accounting | ✅ 5 | ✅ `max_depth` | ✅ | **✅ 3** | — |
| Resumable | ✅ | — | — | **✅ `checkpoint`** | — |
| Resilience / replacement | — | — | — | **✅** | — |
| Competition + judge | — | — | — | **✅ Arena** | — |
| Hierarchical isolation | — | — | ✅ | **⚠️ built, unwired** | **P0** |
| Cross-mechanism budget | — | — | — | **❌** | **P0** |
| Scenario auto-configuration | — | — | — | **❌** | **P0** |
| UI for any mode | ✅ | ✅ | ✅ | **❌** | **P0** |
| Write routes for modes | ✅ | ✅ | ✅ | **❌** | **P0** |
| Shared task list / claim | ✅ Teams | — | Conban | **❌** | P3 |
| Blackboards | — | — | — | **❌** | P4 |
| Per-task model in one fan-out | — | per-agent | ❌ open | **❌** | P1 |
| Worktree-per-writer | ✅ | ✅ | — | **❌** | P2 |
| Isolated context fork | ✅ | ✅ `fork_turns` | ✅ | **⚠️ implicit** | P1 |

---

## 6. The scenario engine

The core of your request: **classify the scenario, configure the whole subagent
stack for it, run at capacity, automatically.**

### 6.1 Principles

1. **The scenario is measured, not guessed** — derived from the `PerceivedIntent`
   Alpha already computes (`intent_type`, `primary_domain`, `complexity_score`).
2. **Every configured number is read or declared.** A scenario may not invent a
   limit; it selects among limits that exist.
3. **Automatic means fully inspectable.** The resolved plan renders in the UI
   with its reason *before* it runs.
4. **A scenario never self-elevates.** Wanting 24 workers against a ceiling of 3
   produces a disclosure and a proposal, not a silent raise.

### 6.2 Scenario taxonomy

| Scenario | Signal | Dispatch | Concurrency | Verification |
|---|---|---|---|---|
| `trivial_lookup` | `complexity < 0.2`, single-domain | none | — | none |
| `bounded_task` | 1 file, known shape | 1 × `quick`/`general` | 1 | inline |
| `breadth_first_research` | ≥3 independent questions | fan-out × `research` | N | fact-check pass |
| `deep_investigation` | multi-hop, gaps expected | × `deep-research`, recursive gap fill | waves | contradiction report |
| `wide_codebase_change` | N modules | N × executor, **worktree each** | N | `deep-code-reviewer` gate |
| `root_cause` | failure, unknown origin | `deep-debugger` × k, exclusive hypotheses | k | patch synthesis |
| `competitive_design` | several valid answers | **Arena** | `agents` × wave | judge + bounded repair |
| `adversarial_settle` | disagreement | council `debate`/`council` | n | quorum |
| `high_risk_change` | auth/payments/deploy | executor + `deep-security` + `deep-performance` | 3 | **hard gate** |
| `refactor_with_impact` | AST impact unknown | `deep-architect` → executor → `deep-test-synthesizer` | sequential | mutation validation |
| `test_debt` | coverage gap | `deep-test-synthesizer` fan-out | N | coverage delta |
| `long_background` | >1 turn latency | **async spawn + lease** | pool | reported on completion |
| `meta_improvement` | recurring failure | Sentinel / self-update loop | low | operator approval |

### 6.3 The resolution function

```
resolve(scenario, intent, operator_config) -> ExecutionPlan
```

An immutable, inspectable value — not mutated globals:

```python
@dataclass(frozen=True)
class ExecutionPlan:
    scenario: str
    reason: str                      # why this scenario matched
    dispatch: str                    # none|single|fan_out|arena|council|pipeline|async
    workers: tuple[WorkerSpec, ...]  # name/category/type + model + tools + budget
    concurrency: int                 # clamped to the operator ceiling
    per_run_total: int               # clamped to max_total_per_run
    depth: int                       # clamped to MAX_DELEGATION_DEPTH
    verification: tuple[str, ...]
    budget: Budget                   # tokens, calls, wall seconds
    requires_confirmation: bool
    degradations: tuple[str, ...]    # what was clamped, and to what
```

`degradations` is the honesty payload. **The plan never silently shrinks.**

### 6.4 Worked examples

**"Audit this auth service"** → `high_risk_change`. One executor
(`deep-security`, read-scoped tools) + one independent reviewer + one performance
pass; concurrency 3; `deep-code-reviewer` as a **hard gate** — no merge without a
verdict. No degradation.

**"Research X, Y, Z, then write one report"** → `breadth_first_research`. Fan-out
3 × `research` (one brief per question) at concurrency 3, wave 1; parent
synthesizes; `fact_checker` verifies merged claims; cost projected first because
it crosses the ~15× threshold.

**"Fix the flaky login test"** → `root_cause`. `deep-debugger` × 3 with
**mutually exclusive** hypotheses, synthesis, one executor applies the fix,
`deep-test-synthesizer` proves it.

**"Postgres or SQLite?"** → `competitive_design` → Arena `quick` (3 competitors,
wave 3), judge with `max_judge_attempts: 2`, cost estimated first. Far cheaper
than a frontier model deliberating, and Arena already refuses unconfirmed runs
above 100 projected calls.

**"While I keep working, go refactor the billing module"** → `long_background`.
One async `/subagent:spawn` with a lease; the parent continues; the result
arrives as an event. **This already works** — it just is not reachable from the
composer.

### 6.5 Anti-goal

Do **not** build a router that picks an agent "because it sounds relevant". The
research is unambiguous: agents duplicate work and leave gaps when briefs are
poor. The scenario is a **cheap classifier**, not a planner — the parent still
writes the briefs.

---

## 7. Full-capacity concurrent multi-variety execution

### 7.1 Why "set `max_running` to 64" is the wrong move

- **Rate limits and money bind before slots do.** Hermes' 10× child-cost
  example scales to real spend at 64 workers.
- **The pool already serialises you.** Arena, swarm and `task` draw from the same
  3 slots; raising the number without a cross-mechanism budget just makes the
  contention less visible.
- **Fan-out over non-independent work measured *slower*** — sequential metered
  2.6× fewer tokens, ~5× cheaper, and was faster **[third-party]**.
- **Synthesis dominates.** Child reports are billed to the parent. 64 verbose
  reports cost more than the 64 children.

### 7.2 Admission model

Priority-classed admission, replacing first-come-first-served:

```
priority = scenario_priority
         × urgency              # blocking path vs background/lease path
         × confidence_penalty   # low-confidence plans yield to explicit ones
         / estimated_cost_weight
```

Background work is admitted without blocking the parent's turn; foreground work
blocks because the parent needs the result. **Alpha already has the lease,
heartbeat and cancel primitives this needs.**

### 7.3 Wave scheduling

1. **Wave 0 — classify.** Cheap model, schema-validated output, no tools.
2. **Wave 1 — fan out.** N workers at the operator ceiling. **Mixed varieties
   run simultaneously** (`deep-security` + `deep-performance` + executor) — this
   is the "many varieties at once" requirement, supported by the shared pool; it
   only needs admission control.
3. **Wave 2 — verify.** Gatekeepers run on wave 1's output. A failing gate
   **stops** the pipeline; it does not annotate it.
4. **Wave 3 — repair (bounded).** Only where a repair loop exists. Bounded
   explicitly; unbounded self-repair is a spend leak.
5. **Synthesis.** Parent, fed **capped** child returns
   (`executive_summary` ≤300 words, `tokens_returned`) so 64 workers cannot flood
   the context.

### 7.4 Isolation matrix

| Concern | Mechanism | Status |
|---|---|---|
| Context | own window + no parent hydration | ⚠️ unmeasured |
| Filesystem | `workspace_mode: "isolated"`, ephemeral scratch | ✅ exists |
| Filesystem (writers) | one git worktree per writer | ❌ add |
| Tools | per-worker allowlist (structural) | ✅ exists |
| Model | `resolve_subagent_model_name` | ✅ exists |
| Process | priority admission | ❌ add |
| Spend | per-scenario budget | ❌ add |

---

## 8. Implementation plan

Ordered by dependency. Nothing here builds what already exists.

### Phase 0 — Wire the unwired seam (P0, smallest, highest value)

`hierarchical_delegator.py` is built and correct; `set_deep_agent_runner` is
never called in production. One composition-root call decides whether Alpha has
hierarchical delegation at all.

1. Call `set_deep_agent_runner` from the composition root with a runner backed
   by the **existing** lifecycle/executor.
2. Add a wiring test in the style of `swarm-structure-wiring.test.mjs`: assert a
   production caller exists, so this can never silently revert.
3. Add a test that `delegate` with a configured runner returns a real contract,
   and without one returns `UNRECOVERABLE_ERROR` — never a fabricated success.

**Gate:** the wiring test fails if `set_deep_agent_runner` loses its caller.

### Phase 1 — Measure the `task` path (P0, and it is a measurement, not a build)

Before writing any Phase-1 code, answer: does the model-facing `task` tool block
on its child, or does it route through `/subagent:spawn` and the lease?

1. A timing test that spawns a known slow child and asserts whether the parent's
   turn returns before the child finishes.
2. Record `max_total_per_run` consumption for a real fan-out.

**Gate:** one page of measured answers. **This decides whether Phase 2 is a
wiring fix or a feature.**

### Phase 2 — Unified budget and priority admission (P0)

1. One budget across `task`, durable batches, Arena and workflow swarms:
   `subagents.budget_scope: run | thread | process` (default `run`).
2. Priority admission (§7.2) replacing first-come-first-served.
3. Per-scenario ceilings that may only **lower** the global default.
4. A `queue_timeout` that **reports** rather than degrading silently.

**Gate:** Arena at 64 and a concurrent swarm demonstrably contend *and report*.

### Phase 3 — Scenario engine (P1, after Phase 2)

**Files:** new `alpha/subagents/scenarios.py`, `alpha/subagents/plan.py`; reuse
`PerceivedIntent`.

1. Classifier on a cheap model with schema-validated output.
2. `resolve()` → immutable `ExecutionPlan` with `reason` and `degradations`.
3. The §6.2 library, each with its verification chain.
4. **Shadow mode first:** classify and display, execute nothing, for one
   release; compare prediction against what happened before letting it steer.

**Gate:** shadow-mode accuracy report; zero silent degradations.

### Phase 4 — Write routes for the modes (P0 for reachability)

1. `GET/PATCH /config/delegation` returning the **effective resolved** config.
2. Validate writes with the same rules `config.py` enforces at load —
   **hot-reload or refuse**, never accept-then-ignore.
3. Settings UI driven entirely by that route; a value the server did not send
   renders unknown.
4. Decide `compare` / `synthesize`: implement, or withdraw them from the
   advertised list.

**Gate:** every control maps to a route returning the server's confirmed value.

### Phase 5 — Ergonomics: make the existing control plane reachable (P1)

The capability exists; the UI does not expose it.

1. A **Scenario** selector in the composer showing the resolved plan and its
   degradations before submission.
2. An **async delegate** affordance — a `task_id` handle with steer/cancel,
   surfacing the existing control plane.
3. Show which mechanism ran each delegation and what it consumed.
4. Link the `Execution modes` catalogue from the composer.

**Gate:** a background delegation is reachable from the composer and reports
honestly.

### Phase 6 — Worktree-per-writer and context forking (P2)

1. Worktree per writer for concurrent code edits — the pattern both Claude Code
   and Codex document; `workspace_mode: "isolated"` is the primitive.
2. A `fork_turns: "none"` equivalent so a child never hydrates the parent's
   context.

**Gate:** two concurrent writers never collide; a child's prompt tokens do not
scale with parent history.

### Phase 7 — Per-task model in one fan-out (P1)

Per-worker `model` in a single delegation call (Hermes #61622, Codex per-agent
TOML). `resolve_subagent_model_name` already resolves; it needs a per-request
input. Enables the Hermes economics: strong orchestrator, ~10× cheaper children.

**Gate:** one fan-out with three different models, each resolved and reported.

### Phase 8 — Team / claim mode (P3), blackboard (P4)

Only if the need is real. Claim semantics belong on the existing kanban board,
not a new queue. **Do not** build a blackboard as shared chat history — that is
the failure mode the pattern exists to avoid.

---

## 9. Config schema

Additive, following `orchestrator.example.yaml`'s convention ("all blocks are
optional; omission preserves current Alpha behavior").

```yaml
subagents:
  # --- capacity (existing) ---
  max_total_per_run: 6
  budget_scope: run                 # run | thread | process        [NEW]
  max_depth: 3                      # mirrors limits.MAX_DELEGATION_DEPTH

  # --- admission ---
  admission:
    policy: priority                # fifo | priority              [NEW]
    background_yields_to_foreground: true                          [NEW]
    queue_timeout_reports: true                                    # [NEW]

  # --- routing ---
  routing:
    orchestrator_model: ""          # "" = the run's model
    executor_model: ""              # cheap executor, Hermes-style
    auto_route: true                                              # [NEW]
    scenario_confidence_floor: 0.7  # below this, ask the operator  [NEW]

  # --- scenarios (opt-in) ---
  scenarios:
    high_risk_change:
      enabled: true
      require_verdicts: ["receipt_verdict", "acceptance_verdict"]   # [NEW]
    breadth_first_research:
      max_workers: 5                # Anthropic's 3-5 band
      require_cost_projection: true
```

**Rule:** no key is added unless a phase can act on it, and no key raises a
ceiling without explicit operator opt-in.

---

## 10. Budget and cost model

Alpha's own numbers are the basis: `estimate_tokens_per_call: 60000` for an Arena
competitor versus a couple of thousand for a `quick` lookup — ~30× spread.

1. **Project before running.** Arena already projects calls and gates on
   `require_confirmation_over_calls`. Generalise it to fan-out.
2. **Charge measured, not estimated** — Arena's own docstring says the estimate
   is only for the gate; measured spend is the authority.
3. **Report the split.** Parent context, child context, and synthesis tokens are
   different costs. `DeepHandoffContract` already carries `tokens_consumed` and
   `tokens_returned` — surface both.
4. **Cache the stable prefix** — cached input bills at ~1/10 **[third-party]**, so
   a stable subagent system prompt is worth more than a tier downgrade.
5. **Never self-elevate.**

---

## 11. Honesty contracts

1. **A degradation is always reported** — `degradations` non-empty whenever the
   plan differs from the request, and shown.
2. **An unreported value is `null`, never `0`.**
3. **A completed run is never "verified"** — verification is a separate gate; a
   failed gate blocks rather than annotates.
4. **Dynamic-workflow digest execution is always a local graph projection.**
5. **A failed read is not an empty result** — independent reads report
   independently.
6. **A tier the operator did not choose is labelled auto-routed** wherever shown.
7. **Mode availability is reported, not assumed** — Arena advertises `compare`
   and `synthesize` without implementing them.
8. **Single-process JSON/JSONL state is never cross-process exactly-once.**
9. **An unwired seam reports `UNRECOVERABLE_ERROR`, never a fabricated success** —
   the standard `hierarchical_delegator` already holds itself to.

---

## 12. Risks, and what not to build

**12.1 Fan-out is not free.** ~15× tokens **[vendor]**. Auto-configuration makes
fan-out *easier*, so it makes spend *less* controlled. Hence shadow-first (Phase
3) and §10.5.

**12.2 Tool-scope prompts are not a security boundary.** Only the runtime
allowlist is.

**12.3 Competence does not transfer.** A cheap executor that cannot do the work
produces confident nonsense. Route by *measured* success per task class and fall
back.

**12.4 Version drift is fast.** Claude Code's Explore inheriting the parent model
caused a 5.9× surprise **[third-party]**. Pin and re-measure; do not trust a blog,
and do not trust §2's third-party numbers beyond their date.

**12.5 Do not build these:**

| Tempting | Why not |
|---|---|
| A free-form agent marketplace | Unreviewable; scope becomes a prompt |
| Debate by default | Voting is comparable at lower cost (NeurIPS 2025) |
| Fully-connected topologies | 72.2% do this; cost grows with *n* |
| Unbounded self-repair loops | Spend leak with no terminal state |
| Deep nesting "because it's possible" | Already depth 3; Codex advises `max_depth: 1` |
| Auto-elevation of any ceiling | Breaks operator cost control |
| A second subagent framework | The control plane, handoff contract and resilience already exist |

---

## 13. Open questions

1. **Phase 1 is a measurement, not a build.** Do you want me to run it before
   committing to any further subagent work? It is cheap and it decides Phase 2.
2. **Budget scope** — `run` (today), `thread` (a long conversation accumulates),
   or `process` (whole Gateway capped)?
3. **Scenario aggressiveness** — shadow for one release (recommended), or straight
   to enforcing?
4. **Arena gaps** — implement `compare`/`synthesize`, or withdraw them from the
   advertised list? Advertising an unimplementable mode is the exact defect the
   Execution modes catalogue exists to catch.
5. **Tier override** — `execution_tier` is auto-routed today. Keep, or add an
   explicit operator override once Phase 4's write routes exist?
6. **Depth** — 3 today, with a 6-per-run budget that binds first. Is depth 3 what
   you want, or should the run budget be the only ceiling?

---

## 14. Sources

**Vendor / primary**
- Anthropic, *How we built our multi-agent research system* —
  https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, *Building effective AI agents* —
  https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, *Code execution with MCP* —
  https://www.anthropic.com/engineering/code-execution-with-mcp
- Claude Code docs, *Subagents in the SDK* —
  https://code.claude.com/docs/en/agent-sdk/subagents
- Claude Code 101, *Subagents* — https://academy.claude.com/courses/claude-code-101/subagents
- Anthropic Skilljar, *Introduction to subagents* —
  https://anthropic.skilljar.com/introduction-to-subagents
- OpenAI, *Multi-agent (Responses API)* —
  https://developers.openai.com/api/docs/guides/responses-multi-agent
- ChatGPT Learn, *Subagents* — https://learn.chatgpt.com/docs/agent-configuration/subagents
- Hermes Agent, *Subagent Delegation* —
  https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation
- Hermes Agent overview — https://hermes-agent.nousresearch.com
- Google ADK, *MCP tools* — https://adk.dev/tools-custom/mcp-tools

**Issues / feature requests**
- openai/codex#36973 — conflicting multi-agent delegation guidance
- anthropics/claude-code#47936 — async subagents stopping early
- NousResearch/hermes-agent#5586 — non-blocking background delegation
- NousResearch/hermes-agent#61622 — per-subagent model selection
- NousResearch/hermes-agent#477 — OpenHands coding-agent skill

**Academic**
- *Multi-Agent Debate Strategies: Survey, Taxonomy, and Challenges* (141 studies) — arXiv:2607.26212
- *Debate or Vote: Which Yields Better Decisions in Multi-Agent LLM* — NeurIPS 2025
- *Improving Multi-Agent Debate with Sparse Communication Topology* — EMNLP Findings 2024
- *A field guide to multi-agent architectures* (MIT) —
  https://medium.com/mitb-for-all/a-field-guide-to-multi-agent-architectures-f6f8c689c406
- *Tool Descriptions Are Smelly!* — https://arxiv.org/html/2602.14878v1

**Third-party** (marked in-text; verify before relying on)
- Systima, *The Subagent Tax* — https://systima.ai/blog/subagent-tax
- AgentPatterns, *Cost-Aware Agent Design* —
  https://agentpatterns.ai/token-engineering/cost-aware-agent-design
- MindStudio, *Claude Code Agent Teams*; *model orchestration*
- hidekazu-konishi.com — Claude Code subagents and orchestration guide
- Firecrawl, *Multi-Agent Orchestration With Codex*

**In-repo**
- `backend/packages/harness/alpha/subagents/` — `lifecycle.py`, `lifecycle_runner.py`,
  `hierarchical_delegator.py`, `deep_handoff_contract.py`, `yield_handoff.py`,
  `context_snapshot.py`, `resilience.py`, `escalation.py`, `promotion.py`,
  `capacity.py`, `token_collector.py`, `status_contract.py`
- `backend/app/gateway/routers/subagent_control.py` — the control plane
- `backend/packages/harness/alpha/config/limits.py` — depth and child caps
- `docs/audits/SUBAGENT_VISIBILITY.md` — the 2026-10-06 fake-`ready` finding
- `MULTI_AGENT_PLAN.md` — running multiple agents against *this repo*
  (developer process; unrelated to the product feature)
- `AGENT_TOOLING_IMPLEMENTATION_PLAN.md` — prior research-plan format
- `orchestrator.example.yaml` — additive-config convention this plan follows