# Bot Mode & Multi-Agent Coordination — Research, Audit, and Master Plan

**Status:** Proposed · **Date:** 2026-10-17 · **Owner:** Alpha core

This document does three things:

1. **Researches** how the leading agent products implement "Bot Mode" and multi-agent coordination — Hermes Bot Mode, Grok Bot, OpenClaw 2.0, ClawTeam — plus the A2A protocol and the published cost/quality data.
2. **Audits** Alpha's actual implementation, with `file:line` evidence, separating what is genuinely production-grade from what is inert, duplicated, or fabricated.
3. **Plans** four phases of work, ordered so that each phase removes a lie or ships a usable surface before the next begins.

**Decision log (agreed before planning):**

| Decision | Choice | Why |
|---|---|---|
| Priority order | Fix wiring/honesty gaps → product surface → coordination correctness → external adoption | Alpha's machinery is largely built; the damage is in unbacked promises. Closing those is cheap and buys trust for the expensive work. |
| Isolation model | **Pragmatic** — one Gateway process, but crash-safe, run-correlated, restart-recoverable state | A per-bot VM is the single largest infra commitment in this space and the published Grok Bot claim that motivates it is wrong (see §2). |
| Deliverable | This single document | Research, audit, and plan share too much evidence to split. |

---

## 1. Executive summary

**Alpha is not behind on multi-agent machinery. It is behind on product surface and on the honesty of a handful of seams.**

Verified strengths — do not rebuild these:

- A durable swarm engine with atomic writes, an fsync'd event journal, lease fencing, restart recovery, and pre-spend budget refusal.
- A real Contract-Net auction with real callers.
- Real deliberation (`council`, `debate`, `verifier`) that makes actual model calls, excludes self-votes, randomizes presentation order, and really executes test commands.
- A production-grade tournament arena (`alpha/arena/`, 19 modules) with cross-process locking.
- Real bot cloning with lineage and lease provisioning.
- Hard, Python-enforced computer-use safety.
- A durable, leased, multi-instance scheduler.
- Ten IM adapters, all outbound-transport (no public IP required).
- A verification overlay that distinguishes `completed` from `verified`.

Verified defects — the Phase 1 work:

- `message_agent` **does not exist as a tool**, yet the lead prompt instructs bots to call it and an endpoint serves its schema.
- The composer's swarm toggle is **advisory only** — it never reaches the run request.
- The swarm decomposer is **template-only** and contains a **duplicated definition block**.
- A swarm can be marked **`completed` by 40% keyword overlap** with its own goal text.
- Swarms carry **no `run_id`**, so run-level cancel and recovery do not cover them.
- Swarm daemon threads are **not joined at shutdown** — an in-flight swarm dies silently.
- `alpha.local.bot` is **requested but never registered**, so bot dynamic workflows fail by design.
- Three modules **fabricate their output** and remain reachable.
- Well-tested delegation/bounds libraries have **no production callers**, while their logic is re-implemented twice elsewhere.
- The frontend **cannot create a bot**, **cannot edit a bot**, and **drops the credential fields** the backend already returns.

The plan below fixes those first, then builds the Hermes-parity product surface, then makes coordination correct under restart and load, then adopts the few external ideas that survive scrutiny.

---

## 2. External research

### 2.1 Hermes Bot Mode — the direct benchmark

Hermes Bot Mode turns each agent *profile* into a first-class **bot**: a name, a deterministic avatar, its own chat history, its own memory, and its own skills/tools/MCP assignments.

The point is not more avatars. **Capability scoping moves from global to per-bot.** Previously, installing a tool or MCP server granted it to every role; a research bot inherited browser permissions it had no business holding. Bot Mode assigns skills, tools, and MCP servers *per bot*.

| Mechanic | Detail |
|---|---|
| **Bot = profile** | Config, memory, and skills stay where they were. Bot Mode is a presentation and scoping shell, not a new runtime. |
| **Per-bot capability scoping** | Tools/skills/MCP assigned per bot. This is the load-bearing feature. |
| **Async inbox, not group chat** | Each bot has a fixed inbox. `@bot` addressing. Messages are delivered on the recipient's **next run**; it **cannot interrupt a running bot**. Sender attribution is recorded, and the SOUL documents how to reply. |
| **Routines bound to a bot** | A scheduled task is owned by a specific bot; the result lands back in *that bot's* chat. Owner, schedule, and result destination are visible in one place. |
| **Cross-device messaging** | Bots reach each other across devices. |
| **Local, free** | Data stays on-device, no subscription. |

**Stated weaknesses — do not replicate:** beta quality; permission semantics unproven; long-running multi-bot stability unproven; explicitly **not real-time**.

The competitive lesson is narrow and worth stating plainly: Hermes did not out-engineer Grok Bot. It shipped near-identical mechanics **free and local**, and that was enough.

### 2.2 Grok Bot — and the myth to avoid

The widely repeated claim is "every bot gets its own cloud computer." **xAI's own documentation says the opposite:** all bots under one account **share one cloud computer**, and each bot gets a *screen*. The security boundary is **account-level, not bot-level**.

Actual mechanics: bots self-assign tasks and DM each other in work group chats; 24/7 always-on execution; routine capture from live demonstration; the product reuses Cursor's account and backend.

**The stack is commodity.** There is no original technology in it:

- **Sandbox:** E2B / Firecracker microVM (or gVisor, or Kata Containers)
- **Browser:** MCP connectors where an API exists; computer-use (screenshot → vision → coordinate click) where it does not; hand back to the human on captcha/login
- **Coordination:** an A2A-style message bus
- **Memory:** layered — in-session context, summary layer, extracted facts (mem0 / Letta lineage)
- **Routine capture:** demonstration → trajectory → parameterized routine (RPA replay, AI edition)

**Lessons:**

1. The moat is packaging, not invention. Do not chase per-bot VMs to match a claim that was never true.
2. Layered memory and routine capture are the two ideas genuinely worth copying.
3. Do not build a business on subscription arbitrage — see §2.3.

### 2.3 OpenClaw 2.0 — what a large harness rewrite actually buys

v2026.8.1, released 31 August 2026. 933 contributors, ~16,000 PRs (roughly half of all PRs ever merged into the project), after seven weeks of silence. Officially titled *"OpenClaw 2.0, Accidentally."*

**What changed:**

| Change | Substance |
|---|---|
| Filesystem → **SQLite** | Durable, queryable state instead of directory trees. |
| **Active Memory** | Pulls relevant past context into qualifying sessions. |
| **Background Memory Consolidation** | A model promotes durable facts to long-term storage **with source provenance**, and keeps a "dream journal." |
| **Self-learning** | Completed tasks become reusable Skills, behind a safety check. |
| **Private Credential Request** | Masked prompt; secrets **never enter the transcript or the model context**. |
| **Shared cloud sessions** | A running task can be handed to another person with context intact. |
| **`openclaw security audit`** | Checks inbound access, tool blast radius, network exposure, plugin allowlist. |

**The expensive community lesson:** Snyk found **36% of ClawHub skills contained prompt-injection attacks**, and Palo Alto Unit 42 kept finding unblocked malicious skills for months. **Security cannot be bolted on afterwards** — it has to be a gate at install time.

**The business lesson:** growth rested on running a $20 consumer subscription for thousands of dollars of API tokens. When that OAuth path was closed on 2026-04-04, some bills rose roughly 50× and the growth curve inverted. Do not build on arbitrage.

**The product lesson:** all seven changes answer "why can't users install it" and "why don't users dare use it." None answer **"why don't they open it in week two."** Reliability and durable value — not feature count — decide retention.

### 2.4 ClawTeam — the coordination-layer thesis

Self-described as a **"framework-agnostic multi-agent coordination CLI."** Not a framework, not a model: a *team runtime*. State lives in `~/.clawteam/`; the default transport is the **filesystem**, with optional ZeroMQ P2P.

Four layers:

1. **Team and task** — `team spawn-team`, `task create/update/list/wait`, `inbox send/receive/peek`, `board show/live/attach/serve`
2. **Workspace isolation** — every worker gets a real **Git worktree** and its own **tmux session**
3. **Communication and observability** — an inbox for messages, a board for "who is doing what right now"
4. **Runtime configuration** — `preset`, `profile`, `profile wizard`, `profile doctor`, `profile test`

**Key insight:** the hard part of parallel coding agents is **not intelligence**. It is file collisions, context cross-contamination, and provenance — *which result came from whom*.

**Positioning:** reuse existing CLIs, reuse Git, avoid heavy infrastructure. You can start with a filesystem and a shell; add Docker, a message queue, or a database only when forced.

### 2.5 The coordination pattern taxonomy

The field has converged on five coordination patterns, each with a characteristic failure mode:

| Pattern | Works when | Breaks when |
|---|---|---|
| **Central orchestrator / supervisor** | The task decomposes cleanly and one team owns every agent | The orchestrator becomes a bottleneck, or lacks context a subagent had |
| **Handoffs (relay)** | Triage and routing | The handoff note drops the *why* |
| **Event-driven choreography** | High-volume, loosely coupled pipelines | You cannot answer "who is working on this right now?" |
| **Shared memory / blackboard** | Many agents contribute facts to one picture | Stale reads and conflicting writes |
| **Collaboration layer (shared rooms)** | Agents from different frameworks, plus humans | You only have one agent |

**The core diagnosis, worth quoting:** most multi-agent failures are **coordination failures, not model failures**. An agent finishes its part and passes a summary that omits the reason behind a decision. Two agents both "own" the same task. A reviewer and a writer hand work back and forth until the budget runs out. A human who should have approved a step finds out after the fact.

Cognition's formulation is the sharpest: *"Actions carry implicit decisions, and conflicting decisions carry bad results."* Their prescription is to share **full agent traces**, not just individual messages.

### 2.6 Hard numbers to plan against

Anthropic's multi-agent research system (orchestrator-worker):

- **+90.2%** over single-agent Opus 4 on internal research evals
- at roughly **15× the tokens** of a chat interaction (agents alone are ~4×)
- **token usage alone explained ~80% of performance variance**
- domains where *all agents must share the same context* are a **poor fit today**

Microsoft's architecture guidance: a single agent with tools is **"often the right default for enterprise use cases."** Add agents when tool count or context size forces it — not because a diagram looks good.

**Implication for Alpha:** multi-agent must be **selective and measurable**, never the default path. Every automatic fan-out needs a trigger threshold, a budget refusal, and a recorded reason. Alpha's existing `swarm/strategy.py` complexity tiering and `swarm/estimator.py` are the right shape for this — they just need honest numbers behind them.

### 2.7 A2A protocol

Google's Agent2Agent, donated to the Linux Foundation and now part of the Agentic AI Foundation alongside MCP, AGENTS.md, and Goose. Built on HTTP, SSE, and JSON-RPC. Core concepts: an **Agent Card** for capability discovery, task management, and multimodal exchange.

Its purpose is **interoperability across frameworks and vendors** — not internal orchestration. Adopting it makes sense for Alpha's `peer_network` and `protocols` surfaces; it is not a substitute for the internal swarm.

---

## 3. Audit: what Alpha has today

### 3.1 What "Bot Mode" means in this codebase

There is **no `bot_mode` symbol** anywhere in the tree. "Bot Mode" currently refers to three loosely connected things:

1. **Bot roster / persona engine** — `backend/packages/harness/alpha/bots/` (~36 modules). `BotProfile`, `BotRegistry`, `get_bot_registry` (`bots/registry.py:809`). Storage under `{runtime_home}/bots/`: `roster.json` (live: `backend/.alpha/bots/roster.json`, 526 KB), `events.jsonl`, `inbox/{bot}.json`, `ephemeral.json`, `runtime_profiles.json`.
2. **Bot-scoped chat** — an ordinary `lead_agent` thread whose `assistant_id` is a bot name (`agents/lead_agent/agent.py:1082`, `_roster_bot_name`). There is no separate graph.
3. **`BotModeMod`** — `mods/controllers/bot_mode_mod.py:52`, an autonomous-mission controller that tracks `mission_started` / `turn_started` / `heartbeat` events and detects stalls.

Bot API: `backend/app/gateway/routers/bots.py`, prefix `/api/bots`, **43 endpoints**.

**This naming ambiguity is itself a defect.** Phase 2.1 introduces a single `bot_mode` contract so the term stops meaning three things.

### 3.2 The ten seams that must close first

| # | Seam | Evidence |
|---|---|---|
| 1 | **`message_agent` is not a real tool.** The prompt tells bots to call it; an endpoint serves its schema; no tool exists. Bot-to-bot messaging only works via REST. | `agents/lead_agent/prompt.py:679-705`; `bots/dm.py:358`; `routers/bots.py:1091`. Only textual match in tools: `tools/script_bridge/policy.py:39`. The mounted `agent_message` tool (`tools/builtins/agent_message_tool.py:44`) targets in-process subagents, not the bot roster. |
| 2 | **The composer swarm toggle is a no-op.** It persists a preference and passes a prop; it never enters the run body. | `components/SwarmModeControl.tsx:29-33` (docstring says "advisory"); `ChatView.tsx:1027-1029` state, passed at `:4437`, absent from the request at `:2440-2480`. |
| 3 | **The decomposer has no model, and its capability-tier block is duplicated.** The second definition shadows the first. | `swarm/decomposer.py:17-94` and `:100-157`; `_decompose_hierarchical` at `:606` emits a fixed research→architect→workerA→workerB→QA graph. |
| 4 | **Swarm success is a keyword match.** `aggregator` calls the quality gate with the goal text as criteria; a 40% keyword overlap passes. | `swarm/aggregator.py:234` → `bots/quality_gate.py:74`. |
| 5 | **No run correlation.** A swarm cannot be tied to the run that requested it, so run cancel and run recovery do not cover swarms. | `swarm/coordinator.py:227` (`create_swarm` takes no run id); `swarm/runner.py:149` (`run_id=None`); `swarm/worker.py:421` falls back to `plan.swarm_id`. |
| 6 | **Daemon threads are not joined at shutdown.** | `swarm/runner.py:47-49` (module globals), `:847-895` (daemon thread spawn); coordinator is a process-local singleton (`:81`) writing a shared directory. |
| 7 | **`alpha.local.bot` is never registered.** `auto_execute=true` is a guaranteed failure. | `routers/bots.py:1306`. `orchestrator/executors.py` defines `alpha.local.{digest,compensation,vote,research,validation,state,skill,mcp,memory}` — no `bot`. |
| 8 | **Three fabricated implementations remain reachable.** | `perpetual/memory_consolidator.py:19-37` (counts are arithmetic: `facts = max(1, traces//2)`); `synthesis/speculative_tournament.py:223` (emits syntactically invalid Python as its "surgical guard"); `deliberation/adversary_deliberator.py:76-127` (keyword lists presented as red-teaming). |
| 9 | **Inert libraries with duplicated bounds.** Well-tested, no production callers; the same limits are re-implemented in `swarm` and `subagents`. | `bots/delegation.py` (`run_bounded`), `bots/autonomy_guard.py`, `bots/capability_dispatch.py:843`, `alpha/tasks/` (contract + adapters, zero callers). |
| 10 | **Frontend gaps.** No bot creation, no bot editing, no credential form, polling where SSE exists. | No `POST /api/bots`; `components/chat-shell/BotDetailPanel.tsx` has no importer; `lib/channels.ts:77-82` drops `credential_fields`; `SwarmRunBoard` polls despite `routers/swarms.py:370-393`. |

**Also verified:** `tools/builtins/a2a_tool.py:14-30` pre-populates two **fake** agent cards ("Lead Research Specialist", "Adversarial Security Auditor") with `availability="available"`. No agent exists behind either.

### 3.3 What is genuinely good — do not rebuild

| Subsystem | Why it is trustworthy |
|---|---|
| `swarm/coordinator.py` | Atomic `os.replace` writes, fsync'd JSONL journal, restart recovery that re-parks RUNNING leases (`:179-221`), owner-scoped idempotency keys, pre-spend wall-budget refusal, spec lint before registration. |
| `swarm/runner.py` | Lease-fenced results, budget exhaustion handling, watchdog backups, auto-replan. |
| `swarm/worker.py:260` | `SpecialistSubagentWorker` runs the real `SubagentExecutor` with tools, receipts, turn caps, and a read-only tool filter. |
| `swarm/cnp_auction.py` | A real Contract-Net auction with real callers. |
| `deliberation/{council,debate,verifier}.py` | Real model calls; self-vote exclusion; randomized presentation order; an unreviewed candidate scores 0.0 rather than a fabricated 0.8; the verifier really runs `subprocess` and grades the exit code. |
| `alpha/arena/` | 19 modules, ~5.5k lines, durable state with a cross-process lock, budget confirmation, resume. |
| `bots/cloning.py` | Real copy/fork with lineage, a fresh `memory_scope`, and real ephemeral lease provisioning (honest `lease_status`, including failure text). |
| `computer_use/*` | Hard Python-enforced safety: panic corner, window-boundary lock, hotkey blacklist matched by subset with alias normalization, single-use operator-issued token for destructive combos. Deliberately **no** `confirmed=True` boolean a model can set. |
| `app/scheduler/service.py` | Durable, leased, multi-instance scheduled tasks with capacity admission and restart reconciliation. |
| `backend/app/channels/` | Ten adapters (Feishu, Slack, Telegram, Discord, DingTalk, WeCom, WeChat, GitHub, Signal, Buzz), all outbound-transport. Path: adapter → `MessageBus.publish_inbound` → `ChannelManager._worker_loop` (`manager.py:1871`) → `_handle_chat` (`:2315`) → `_get_or_create_thread` (`:2266`) → `runs.stream` over loopback HTTP. Same thread/run path as the web UI. |
| SSE streaming | `thread_runs.py:1067` → `StreamBridge` (`alpha/runtime/stream_bridge/`) → `sse-reducer.ts` → `task_*` events folded into `state.tasks`. |

### 3.4 Honest scorecard against the field

Legend: ✅ at parity or ahead · 🟡 partial · 🔴 gap

| Capability | Hermes | Grok Bot | Alpha today | Verdict |
|---|---|---|---|---|
| Named bot identities + roster | ✅ | ✅ | ✅ 43 routes, full roster, org chart | **At parity / ahead** |
| Per-bot capability scoping | ✅ UI | partial | 🟡 model exists (`authority_ceiling.py`, `permissions.py`) but **no UI and no single resolver** | **Gap: surface + contract** |
| Async inbox + `@` mention | ✅ | ✅ | 🟡 `bots/inbox.py` is real; `message_agent` is **not a tool** | **Gap: one broken tool** |
| Routines bound to a bot | ✅ | ✅ | 🟡 durable scheduler exists; `add_routine` at `registry.py:515`; **not bound to the bot's chat** | **Gap: binding** |
| Bot creation | ✅ | ✅ | 🔴 no `POST /api/bots`; editor unmounted | **Gap** |
| Group room / shared room | partial | ✅ | ✅ `groups/` with 5 speaker modes (mention, moderated, quorum, parallel, round-robin) | **At parity** |
| Multi-agent visualization | list | ✅ | 🟡 vertical list only; no `parent_id` in events | **Gap: event contract** |
| Always-on / 24-7 | ✅ routines | ✅ | 🟡 scheduler is real; the perpetual daemon **executes nothing** | **Gap: honesty** |
| Memory consolidation | ✅ | ✅ | 🔴 **fabricated** | **Gap: replace** |
| Credential masking | ✅ | partial | 🟡 `runtime/secret_context.py` exists, no surface | **Gap: surface** |
| Security audit | ✅ | — | 🟡 `security/injection.py`, `skills/audit/ast_audit.py` exist; no unified command | **Gap: unify** |
| A2A interop | — | bus | 🟡 `protocols/a2a.py` + `peer_network/`, **fake cards** | **Gap: make real** |
| Worktree isolation for coding agents | — | ✅ | ✅ `swarm/worker.py:537` `CodingWorktreeWorker` — real `git apply`, real commit, real merge simulation | **At parity** |
| Durable swarm state | — | — | ✅ atomic + journal + recovery | **Ahead** |
| `completed` ≠ `verified` | — | — | ✅ run-verification overlay | **Ahead** |
| Multi-provider routing / pricing | — | — | ✅ single `config.yaml` source, validated at load | **Ahead** |
| IM breadth | — | X only | ✅ 10 adapters | **Ahead** |

**Reading of the table:** Alpha should not be pitched as behind. It should be pitched as *governed*, and then made honest about the seams above.

---

## 4. Principles for all phases

1. **Fail closed.** A missing identity, executor, or credential is a refusal with a reason code — never a widening default.
2. **No unearned status.** `completed` requires executed tasks *and* accepted criteria. `verified` requires a real check. `delivered` requires a real inbox write.
3. **No fabricated numbers.** Counts are read from stores. A not-implemented path returns `0` **and** `status: "unavailable"` with a reason.
4. **One source of truth per concept.** One limit set, one capability matcher, one acceptance gate, one bot-mode resolver.
5. **Prompt-cache safe.** Per-bot context goes in the per-turn reminder (`DynamicContextMiddleware`), never in the static system prompt.
6. **Orphan guard.** A new module must be imported by production in the same change, or added to `backend/tests/test_no_orphan_modules.py::ALLOWED_ORPHANS` with a verifiable reason.
7. **Cost story required.** Multi-agent costs roughly 15× tokens. Any automatic fan-out needs a trigger threshold and a budget refusal.

Effort scale: **S** ≤ 1 day · **M** 2–3 days · **L** 4–5 days.

---

## 5. Phase 1 — Close the honesty and wiring gaps

**Goal:** every promise the UI, the prompt, or the docs make is backed by a real code path — or is removed.

**Exit gate:** `message_agent` resolves to a real tool; no swarm can reach `completed` on keywords; no endpoint returns fabricated counts; shutdown and restart are observable; `pytest` and `pnpm -C frontend verify` are green.

### 1.1 Make `message_agent` a real tool (S)

**New:** `alpha/tools/builtins/message_agent_tool.py`

```
@tool("message_agent", parse_docstring=True)
def message_agent(runtime: Runtime, target: str, message: str, reply_to: str = "") -> str
```

**Edit:** `tools/builtins/__init__.py` (`_EXPORT_MODULES` + `__all__`), `tools/tools.py` (import, append to `builtin_tools` — **not** `subagent_tools`), `bots/dm.py` (derive `message_agent_tool_schema` from the tool so there is one schema), `routers/bots.py:1091`.

**Behaviour:**

- Sender from `resolve_runtime_bot_name(runtime)` (`dm.py:90`). If `None`, return a refusal string — no exception, no write.
- Call `send_dm(sender, target, message, thread_metadata={...}, require_bot_chat=True)`.
- If `reply_to`, ack the original **after** a successful send.
- Return `{delivery_id, target, status, reason_code}` verbatim. Never print "delivered" unless `status == "delivered"`.

**Test:** `backend/tests/test_bots_dm.py` — delivers to the inbox; refuses outside a bot chat; an unknown target writes nothing; `reply_to` acks. Registry assertion: `"message_agent" in {t.name for t in get_available_tools()}`.

**Acceptance:** the prompt line, the SOUL protocol, and `dm-schema` all name a tool that exists and works.

### 1.2 Make the composer swarm toggle dispatch (M)

**Edit:** `ChatView.tsx` (`:1027`, `:2440-2480`, `:4437`), `SwarmModeControl.tsx`, `lib/teamops.ts`, `lib/swarm-run-board.ts`.

**Change:**

- Rename the control to **"Send as swarm"** and delete the "advisory" docstring.
- On submit, if enabled: `POST /api/swarms` → `POST /api/swarms/{id}/run-async` → render a pinned `<SwarmRunBoard swarmId>`. Do **not** also call `/runs/stream` (mutual exclusivity prevents double spend).
- Extract a pure `buildSwarmSendRequest()` helper so the decision is unit-testable.

**Test:** `frontend/src/lib/swarm-send.test.mjs` asserting mutual exclusivity in both branches.

**Acceptance:** toggling changes behaviour; no double spend; the board shows the real swarm.

### 1.3 De-duplicate the decomposer and disclose its source (S)

**Edit:** `swarm/decomposer.py`.

- Delete `:100-157` (the second `_CAPABILITY_TIER` / `_apply_capability_model_routing`); keep the first.
- Move the mid-file imports to the top; drop `# noqa: E402`.
- Stamp `plan.metrics["decomposition"] = {"source": "template", "reason": "no model decomposer configured"}` in `decompose()`.

**Test:** an AST scan asserts exactly one definition of each symbol; existing decomposer tests stay green.

**Acceptance:** no shadowed definitions; every plan states how it was decomposed.

### 1.4 Replace the keyword gate with task facts (M)

**Edit:** `swarm/aggregator.py:210-260`, `bots/quality_gate.py`, `routers/bots.py:486`.

**Status derivation from tasks only:**

| Condition | Status | `terminal_reason` |
|---|---|---|
| No tasks | `partial_success` | `no_work` |
| Any failed or cancelled | `partial_success` / `failed` | propagated |
| All `COMPLETED` and every criteria-bearing task `acceptance_status == "passed"` | `completed` | — |
| Criteria present but unverified | `partial_success` | `acceptance_unverified` |

Delete `evaluate_quality_gate(deliverable_text, criteria)` from the status decision entirely. Keep the module only for `POST /api/bots/quality-gate/verify`, and add `"method": "keyword_overlap", "advisory": true` to its response so the caller knows what it is.

**Test:** a report that repeats the goal verbatim while tasks failed is **not** `completed`; a zero-task plan is not `completed`; failed acceptance yields `partial_success`.

**Acceptance:** keyword overlap can never produce a terminal success.

### 1.5 Correlate swarms to runs (M)

**Edit:** `swarm/models.py`, `swarm/coordinator.py`, `tools/builtins/swarm_tool.py`, `planning/bridge.py:215`, `swarm/runner.py:149`, `routers/swarms.py`, `alpha/runtime/runs/manager.py`.

- `SwarmPlan` gains `run_id`, `thread_id`, `requested_by`; bump `schema_version` to 3 with a tolerant `from_dict` (missing → `None`).
- `create_swarm(..., run_id=None, thread_id=None)`; persist in `to_dict`.
- `swarm_tool` gains `runtime: Runtime` and reads `runtime.context["run_id"]` (installed at `runtime/runs/worker.py:721-726`).
- `swarm/runner.py:149` passes `run_id=plan.run_id or None` — no more silent `None`.
- `GET /api/swarms?run_id=` filters; `plan.metrics["run"] = {run_id, thread_id}`.
- In `RunManager.cancel`, after the status is persisted, call `cancel_for_run(run_id, reason="run_cancelled")` inside `try/except`, **outside** the lock.
- HTTP create accepts **no client-supplied `run_id`** — server-owned only.

**Acceptance:** a swarm is never orphaned from the run that asked for it.

### 1.6 Join swarm work at shutdown (M)

**Edit:** `swarm/runner.py`, `app/gateway/app.py` (lifespan shutdown, near `:779-809`).

- `SwarmRunner.shutdown(timeout)`: set `_SHUTTING_DOWN`, stop leasing, `thread.join(timeout)`, cancel asyncio tasks, write `SWARM_INTERRUPTED` with `status="paused"` and `terminal_reason="gateway_shutdown"`, checkpoint. Return `{stopped, forced, swarms}`.
- Lifespan calls it within the existing `_SHUTDOWN_HOOK_TIMEOUT_SECONDS` bound and logs a warning naming any swarm not stopped.
- **Do not auto-resume on boot yet** — that is Phase 3.1. The coordinator already parks with `process_restart_requires_resume`.

**Acceptance:** a restart produces an event and a resumable plan, not a silent disappearance.

### 1.7 Register the bot dynamic-workflow executor (M)

**Edit:** `orchestrator/executors.py` (new `BotNodeExecutor` registered as `alpha.local.bot`), `routers/bots.py:1306`.

Resolve the bot profile, assemble via `SubagentExecutor` with that bot's tools, skills, and model, execute the node objective, and return a real result plus an evidence bundle. Refuse with 400/409 and a named reason on unknown or archived bot, ESTOP, or a missing tool pool.

**Acceptance:** `auto_execute` either works or is refused for a named reason — never a generic failure.

### 1.8 Remove fabricated implementations (M)

| File | Change |
|---|---|
| `perpetual/memory_consolidator.py:19-37` | Delete the arithmetic. Return zero counts + `status="unavailable"` + reason. `routers/projects.py:1837` returns **501** with that reason. |
| `synthesis/speculative_tournament.py:223` | Return `None` when the candidate does not `ast.parse`. Every candidate must parse before scoring; record `invalid_syntax` and never let it win. |
| `deliberation/adversary_deliberator.py` | Add `method: "keyword_heuristic"` to its output and correct the docstring. It has no production caller (see §9). |

**Acceptance:** no reachable path invents facts, counts, or code quality.

### 1.9 Wire one real caller for capability dispatch (S)

**Edit:** `swarm/team.py` (`assign_specialists`) to use `dispatch_task` / `CapabilityDispatcher` instead of ad-hoc matching. Keep the existing behaviour where an unassignable task stays unassigned **and says why**.

**Acceptance:** `dispatch_task` has a production caller; swarm no longer re-implements capability matching.

### 1.10 Close the frontend gaps (M)

**Backend:** `POST /api/bots` → `forge_bot`; `POST /{name}/smoke`.

**Frontend:**

- New `components/bots/BotCreateDialog.tsx`, mounted in `BotGallery.tsx`: template picker (`GET /api/bots/templates`), name/role/department/model/skills.
- Mount `chat-shell/BotDetailPanel` behind an Edit action on `/bots/[name]`.
- `lib/channels.ts`: pass `credential_fields` through and post to `POST /api/channels/{provider}/runtime-config` (`routers/channel_connections.py:779` — already exists, currently unreferenced by the frontend).
- `SwarmRunBoard`: consume `/api/swarms/{id}/events/stream`; keep 3-second polling only as a degraded fallback with a visible reason.

**Acceptance:** every backend capability named in the UI has a control; every control hits a real route.

### Phase 1 non-goals

No new bot engine. No per-bot VM. No model judge. No scheduler rewrite. No UI redesign. No distributed store.

### Phase 1 risks

| Risk | Mitigation |
|---|---|
| Touching the `swarm_tool` signature may break LangChain runtime injection | Cover with a registry test that asserts the tool resolves and its args parse |
| Shutdown join can stall on a blocked worker | Hard timeout, then forced cancel, then a warning naming the swarm |
| `test_no_orphan_modules` churn | Pair every new module with its production consumer in the same change |

---

## 6. Phase 2 — Bot Mode product surface (Hermes parity)

**Goal:** "Bot Mode" becomes one named contract with create / edit / scope / inbox / routine surfaces. **No new runtime.**

### 2.1 A first-class `bot_mode` contract (M)

**New:** `alpha/bots/mode.py`

```
BotModeContext(
  bot_name, thread_id, run_id,
  capabilities, toolsets, skills, mcp_servers,
  model, memory_scope, approval_mode, inbox_policy,
)
resolve_bot_mode(thread_metadata, runtime_context, registry) -> BotModeContext | None
```

Resolution is **server-side only**. A client may request `assistant_id`; it may not request capabilities.

**Edit:** `agents/lead_agent/agent.py` (`_resolve_bot_profile`), `agents/middlewares/dynamic_context_middleware.py:444`, `routers/bots.py`, `app/gateway/run_models.py`.

**Expose:** `GET /api/bots/{name}/mode` — the effective contract **plus provenance** (which layer set each value).

**Acceptance:** one endpoint answers "what may this bot do right now"; changing the profile changes the answer; a non-bot thread has no `bot_mode`.

### 2.2 Creation and editing (M)

- `POST /api/bots` → `forge_bot` (transactional: build → verify → rollback, draft-first approvals, routines cost-checked).
- `POST /{name}/smoke` runs a real bot-scoped run and returns the observed result.
- UI badge progression: **`created · unverified`** → **`smoke passed`** (with the run id).

**Acceptance:** creating a bot never claims "alive" without a smoke result.

### 2.3 Capability scoping UI (M)

- New **Capabilities** section on `/bots/[name]`: requested vs effective grant, the ceiling, toolsets, skills, MCP servers, memory scope, model.
- Writes go through `PUT /api/bots/{name}/capabilities` → `registry.rescope_bot` (`bots/registry.py:708`, leader-only, ceiling-intersected).
- `PATCH` must **reject** `capabilities` and `toolsets` so the scoping UI is not a bypass.
- Show the capability fingerprint / epoch change on save.

**Acceptance:** over-ceiling requests are **refused, not silently trimmed**; the UI shows the refusal.

### 2.4 Routines bound to a bot (M)

- **Edit:** `routers/scheduled_tasks.py:62` (`resolve_scheduled_task_assistant_id`) to accept roster bot names via `get_bot_registry()`; stamp `botName` in the scheduled run metadata.
- **New:** `POST/DELETE /api/bots/{name}/routines`. A routine writes a scheduled task with `assistant_id=<bot>` and `thread_id=canonical_bot_chat_id(bot)` (`bots/dm.py`) so results land in that bot's chat; store `task_id` on the profile routine (`registry.py:515`, `add_routine`).
- UI: routines table (schedule, next run, last result, pause/resume) on the bot page.

**Acceptance:** a routine fires, the run is bot-scoped, and the result is visible in `GET /api/bots/{name}/chat`.

### 2.5 Async inbox, made real and visible (M)

- `bots/inbox.py`: `drain_for_prompt(bot_name, limit)` returns unread messages and marks them **`read`** — not `acked`.
- `DynamicContextMiddleware`: inject a per-turn reminder — "Messages received while you were away" with sender, body, and `delivery_id` — instructing a reply via `message_agent(reply_to=...)`.
- `message_agent(reply_to=...)` acks the original after a successful send.
- UI: inbox panel, unread badge, manual Ack (existing `POST /{name}/inbox/{id}/ack`).

**Acceptance:** a DM sent while the bot is idle is visible on its next run **exactly once**; ack is explicit and observable.

This is deliberately **not** real-time — matching Hermes. Interrupting a running bot is a different, much harder problem and the plan does not pretend otherwise.

### 2.6 Group room `@` mention routing (M)

- **Edit:** `routers/groups.py:217` (`mentions/preview`), `alpha/groups/orchestration.py`, `TeamOpsSection.tsx` group composer.
- **New:** `components/groups/MentionComposer.tsx` — `@` autocomplete from `GET /groups/{name}/roster`; a live preview showing `resolved` / `unresolved` and "will route to X" **before** send; mentions rendered as chips; the room's routing mode displayed beside the composer.

**Acceptance:** typing `@coder` shows the resolved member; an unresolved mention says it will not route; the next speaker matches the mention.

### Phase 2 non-goals

No per-bot MCP process isolation. No cross-device sync. No marketplace. No voice. **No real-time bot interruption** — async is the contract.

---

## 7. Phase 3 — Coordination correctness under restart and load

Still single-process. The goal is that a restart, a cancellation, or a model failure leaves the system in a **named, resumable, honest** state.

### 3.1 Run-correlated lifecycle and recovery (M)

- **New:** `swarm/recovery.py` — `scan_recoverable(owner_id)`, `resume(swarm_id)`, `abandon(swarm_id, reason)`.
- Config `swarm.resume_on_start: none | owned | all` (default `none`).
- `GET /api/swarms?status=paused`; reuse `POST /{id}/resume`.
- `SwarmRunBoard`: a recovered banner with Resume / Abandon, naming the `terminal_reason`.

**Acceptance:** kill the Gateway mid-swarm → restart → the swarm is `paused` with a reason, resumable, with fresh leases.

### 3.2 Model-backed decomposition (L)

- **New:** `swarm/llm_decomposer.py` — a strict JSON schema (id, objective, deps, capability_tags, acceptance_criteria, output_artifacts, estimated_seconds), DAG validation (no cycles, deps exist, within limits), then `swarm/planner.select_best_candidate` scores candidates.
- Config `swarm.decomposition: auto | template | model`; budget pre-check before the call.
- Record `metrics["decomposition"] = {source, model, candidates, chosen, rejected_reasons, tokens}`. On any failure, fall back to templates **and say so**.

**Acceptance:** invalid model JSON yields a template plan **with the reason**; no plan claims `source="model"` without a real model call.

This is the single highest-leverage correctness change: it turns the swarm from a fixed five-node template into something that can actually match the task.

### 3.3 A real acceptance gate (L)

- **New:** `swarm/judge.py` — a rubric judge over the goal plus per-task evidence, returning `{verdict, score, reasons, evidence_refs, model}`. **Judge failure means `unverified`, never pass.**
- Aggregator: per-task `acceptance_status` (already real, via `verify_acceptance_criteria` and claim grounding) plus an optional judge for non-mechanical criteria. `quality_score` comes **only** from the judge.
- Plan field `acceptance_mode: "evidence" | "judge" | "both"`.

**Acceptance:** `completed` requires accepted tasks; `unverified` is a distinct, visible outcome; no keyword path remains.

### 3.4 Delegation-graph event contract and visualization (M)

**Backend:** `tools/builtins/task_tool.py:1197` — `task_started` gains `parent_id`, `depth`, `agent_name`, `subagent_type`, `run_id`, `swarm_id`; the same on `task_running` and terminal events. Depth comes from runtime context set by `SubagentExecutor` on spawn.

**Frontend:** `lib/sse-reducer.ts` `SubagentTask` gains `parentId`, `depth`, `agentName`, `edges`; new `components/agent-ui/DelegationGraph.tsx` (nodes = tasks, edges = `parent_id`) rendered beside `SwarmBoard`, with the existing list as fallback.

**Acceptance:** nested delegation renders as a tree; a missing `parent_id` renders as a root and **never** a guessed edge; no event field is invented client-side.

### 3.5 One set of limits (M)

- `alpha/config/limits.py` remains the only numeric source.
- `bots/delegation.DelegationLimits` becomes the only enforcement; `subagents/lifecycle.py` and `swarm/runner.py` call it instead of re-implementing depth/fan-out/hops/token/cost/wall-clock bounds.
- `bots/autonomy_guard.py` merges into `delegation` (or is deleted; update the orphan allowlist).
- `bots/capability_dispatch` is the only assignment path (from 1.9).
- `alpha/tasks` adapters power `GET /api/tasks?run_id=` — one projection consumed by both the graph plane and Team Ops.

**Acceptance:** changing a limit in one place changes every engine; no second bound check disagrees.

### Phase 3 non-goals

No distributed coordinator. No Redis swarm store. No per-bot VM. No new autonomous replanning beyond the existing `auto_replan`. No new status vocabulary.

### Phase 3 risks

| Risk | Mitigation |
|---|---|
| Model decomposition adds cost and latency | Gate behind `swarm.decomposition`, pre-check budget, cache the plan on the swarm record |
| Judge produces false negatives | Never block on judge failure — downgrade to `unverified`, which is a visible non-success |
| Event-contract drift between backend and reducer | One reducer test per added field; unknown fields are ignored, not invented |

---

## 8. Phase 4 — Selective external adoption

Only the ideas that survive the audit of §2.

### 4.1 A2A interop on the existing peer network (L)

- **Delete the fake cards** in `tools/builtins/a2a_tool.py:14-30`. Build cards from `BotRegistry` and `PeerNetworkService` peers with real capabilities and real availability.
- **New:** `PeerA2ATransport` over the paired-peer transport; `GET /.well-known/agent-card.json`; `POST /api/a2a/tasks` creates a real inbox message or run.
- A delegation returns `accepted` — **never** `completed` — unless a real result exists.

**Non-goal:** no public unauthenticated A2A server. Pairing stays required.

### 4.2 Honest memory consolidation with provenance (L)

- **New:** `alpha/memory/consolidation.py`, replacing the fabricated path. Read real episodic traces, extract facts with a model, and write `{fact, source_trace_ids, extractor_model, confidence, created_at}`; dedupe; append a "dream journal" JSONL.
- Response: `{status, traces_read, facts_written, facts_rejected, sources}` — zeros are real zeros. **Opt-in only**, no daemon by default.

**Acceptance:** every written fact cites a trace id that exists; a failed extraction writes nothing and says why.

This is the OpenClaw idea worth taking: consolidation is fine, *provenance* is what makes it trustworthy.

### 4.3 Worktree isolation for coding swarms (M)

- **Edit:** `swarm/worker.py:537` (`CodingWorktreeWorker`), `swarm/runner.py` (worker selection); **new** `swarm/merge_queue.py`.
- `CODING_WORKTREE` always uses one worktree per task, with the base ref pinned to the plan's start SHA; merges are serialized through `merge_queue`; each reports `clean | conflicted | failed` with the affected files. Surface branch, commit SHA, and merge verdict; clean up worktrees on terminal.

**Non-goal:** no auto-merge without a green test command; no shared working tree for parallel writers.

The worker already does the hard part (real `git apply`, real commit, real merge simulation). This adds the coordination that ClawTeam correctly identifies as the actual problem.

### 4.4 Security audit surface (M)

- **New:** `alpha/security/audit.py`, `POST /api/security/audit`, and a `/security audit` command (`commands/catalog.py:371`).
- Checks: skill prompt injection (`alpha/security/injection.py`, `skills/audit/ast_audit.py`), credential leakage in transcripts, tool-allowlist drift, bot capability vs ceiling, channel webhook auth, worktree path escapes, MCP trust.
- Returns findings with severity, evidence, and an explicit `scanned` / `not_checked` split. CLI: `python -m alpha.security.audit --json`.

**Non-goal:** no "security score", no auto-fix, no network scanning.

The OpenClaw lesson is that this must exist **before** a skill marketplace, not after 36% of packages turn out to be hostile.

---

## 9. Kill list — delete, do not fix

| # | Target | Reason |
|---|---|---|
| 1 | `alpha/bots/teammate_mesh.py` | Orphaned duplicate of `dm.py` + `inbox.py`. Also remove the `teammate_mesh` entry in `capabilities/catalog.py:51` — that catalog string is the only thing keeping it "referenced." |
| 2 | `swarm/decomposer.py:100-157` | Duplicate capability-tier block; the second shadows the first. |
| 3 | `bots/quality_gate.py` keyword gate **as a status decider** | 40% keyword overlap cannot decide whether work succeeded. Keep only as a labelled advisory endpoint, or delete the endpoint and its tests. |
| 4 | `perpetual/memory_consolidator.py:19-37` arithmetic | Fabricated counts on a live route. |
| 5 | `synthesis/speculative_tournament._synthesize_surgical_guard` | Emits invalid Python as a candidate. Fix to parse, or return `None`. |
| 6 | `components/chat-shell/BotDetailPanel.tsx` | If still unmounted at the end of Phase 2, delete it rather than leave a second, dead editor. |
| 7 | `ENHANCED_MUTATION` cosmetic `version + 1` (`bots/cloning.py:102`) | Implement a real mutation, or remove the enum value. A mode that does nothing is worse than a mode that does not exist. |
| 8 | Fake A2A cards (`tools/builtins/a2a_tool.py:14-30`) | Claims two available specialists that do not exist. |
| 9 | Duplicate bound enforcement in `subagents` / `swarm` | After 3.5, one limit set only. |
| 10 | Any `auto_execute` path that cannot execute | After 1.7, none should remain. |

**Orphan-guard bookkeeping:**

- Deleting `synthesis/speculative_tournament` requires removing its `ALLOWED_ORPHANS` entry.
- Deleting `teammate_mesh` requires the `capabilities/catalog.py` edit and a check against `test_discovery_plane_parity.py`.
- Every new Phase 3/4 module must ship with its production import in the same change.

---

## 10. Benchmark mapping — what we take from whom

| Source | Adopted | Rejected |
|---|---|---|
| **Hermes Bot Mode** | Bot = profile with per-bot capability scope; async inbox with `@` addressing; routines bound to a bot with results landing in its chat | Real-time group chat (it is explicitly async); beta-grade permission semantics |
| **Grok Bot** | Layered memory; group work chat; routine-capture direction (Phase 4 candidate) | **Per-bot VM** — official docs say account-shared, and the user chose pragmatic isolation; subscription-arbitrage economics |
| **OpenClaw 2.0** | Memory consolidation **with provenance**; private credential request; a unified `security audit`; durable queryable state | "Self-learning auto-skills" without a safety gate; a plugin marketplace without supply-chain controls |
| **ClawTeam** | Coordination layer rather than framework; worktree-per-worker; inbox + board; profile/doctor for runtime config | Filesystem-only transport at scale; a tmux dependency |
| **A2A** | Agent Card discovery for the peer network; `accepted` ≠ `completed` | Public unauthenticated agent endpoints |
| **Anthropic / Cognition / Microsoft** | Orchestrator-worker with budget refusal; share traces rather than summaries; single agent as the default | Automatic fan-out on every query — 15× cost for ~90% quality only on research-shaped work |

---

## 11. Test matrix, merge order, rollout

### 11.1 Test matrix

| Layer | Command / file |
|---|---|
| Backend unit | `cd backend && uv run pytest tests/test_bots_dm.py tests/test_swarm_*.py tests/test_no_orphan_modules.py -q` |
| New truth suite | `backend/tests/test_bot_mode_truth.py` — tool exists, no keyword success, no fabricated counts, run correlation |
| Gateway routes | `backend/tests/test_bots_groups_a2a.py`, `test_bot_lifecycle.py` |
| Frontend | `pnpm -C frontend verify` (typecheck + `node --test src/lib/*.test.mjs`) |
| Live probe | New `backend/scripts/probe_bot_mode_truth.py` — create bot → smoke → DM → inbox → swarm → restart |
| Manual | Bot DM; swarm toggle on/off; kill the Gateway mid-swarm; a routine fires into a bot chat |

### 11.2 Merge order

1. **Phase 1 backend truth:** 1.3 → 1.1 → 1.5 → 1.4 → 1.6 → 1.7 → 1.8 → 1.9
2. **Phase 1 UI wiring:** 1.2 → 1.10
3. **Phase 2:** 2.1 → 2.2 → 2.3 → 2.5 → 2.4 → 2.6
4. **Phase 3:** 3.5 → 3.1 → 3.3 → 3.2 → 3.4
5. **Phase 4:** 4.4 → 4.1 → 4.3 → 4.2

### 11.3 Flags

Ship anything that only **removes a lie** behind no flag — those changes cannot make the product worse. Use flags for cost-bearing changes:

| Flag | Default | Effect |
|---|---|---|
| `swarm.decomposition` | `template` | `model` enables 3.2 |
| `swarm.resume_on_start` | `none` | `owned` / `all` enables 3.1 auto-resume |
| `memory.consolidation` | `false` | Enables 4.2 |
| `a2a.enabled` | `false` | Enables 4.1 |

### 11.4 Rollback

Every phase is independently revertable. Phase 1 changes are localized and covered by new tests. Phase 2 adds a contract module without removing the old resolution path until 2.1 is proven. Phase 3's model decomposition falls back to templates by design. Phase 4 is flag-gated.

---

## 12. Appendix

### 12.1 Key files referenced

**Bot engine:** `alpha/bots/registry.py` · `profile.py` · `dm.py` · `inbox.py` · `cloning.py` · `handoff.py` · `reassignment.py` · `authority_ceiling.py` · `permissions.py` · `quality_gate.py` · `dynamic_profiles.py` · `lifecycle_governor.py`

**Swarm:** `swarm/coordinator.py` · `runner.py` · `worker.py` · `scheduler.py` · `decomposer.py` · `planner.py` · `aggregator.py` · `cnp_auction.py` · `consensus.py` · `deliberation.py` · `team.py` · `strategy.py` · `topology.py` · `models.py`

**Deliberation / arena:** `deliberation/{engine,council,debate,verifier,router,invocation}.py` · `alpha/arena/*` · `alpha/models/moa/*`

**Runtime:** `alpha/runtime/runs/{manager,worker}.py` · `alpha/runtime/stream_bridge/*` · `alpha/runtime/checkpointer/*` · `alpha/agents/thread_state.py`

**Gateway:** `app/gateway/app.py` · `services.py` · `deps.py` · `routers/{bots,swarms,groups,teams,deliberation,subagents,subagent_control,channels,channel_connections,scheduled_tasks,thread_runs}.py`

**Channels:** `app/channels/{base,message_bus,service,manager,store,run_policy}.py` · `app/channels/{feishu,slack,telegram,discord,dingtalk,wecom,wechat,github,signal,buzz}.py`

**Frontend:** `ChatView.tsx` · `components/{SwarmRunBoard,SwarmModeControl,SubagentList}.tsx` · `components/agent-ui/SwarmBoard.tsx` · `components/bots/*` · `components/sections/{TeamOpsSection,ChannelsSection,SubagentsSection}.tsx` · `lib/{sse-reducer,chat-stream,channels,bots,teamops,swarm-structure-view}.ts`

### 12.2 Endpoint index (existing)

| Prefix | Router | Notable |
|---|---|---|
| `/api/bots` | `bots.py` | 43 routes; no `POST ""` (create) today |
| `/api/swarms` | `swarms.py` | 24 routes incl. `/events/stream` (unconsumed by UI) |
| `/api/groups` | `groups.py` | 70 routes; `mentions/preview`, `/runs` |
| `/api/teams` | `teams.py` | roster, report |
| `/api/deliberation` | `deliberation.py` | `run`, `evaluate` |
| `/api/subagents` | `subagents.py`, `subagent_control.py` | catalog, spawn, batches |
| `/api/channels` | `channels.py`, `channel_connections.py` | providers, connections, `runtime-config` (unused by UI) |
| `/api/scheduled-tasks` | `scheduled_tasks.py` | 14 routes, durable |

### 12.3 External sources

- Hermes Bot Mode — multi-agent practice and per-bot inbox/routines: `baijiahao.baidu.com/s?id=1878441401001096540`, `blog.csdn.net/weixin_28221501/article/details/164208603`
- Grok Bot — official-doc correction and stack teardown: `blog.csdn.net/2301_79655886/article/details/164359046`; `docs.x.ai/grok-bot/overview`
- OpenClaw 2.0 — release analysis and community security data: `hub.baai.ac.cn/view/57629`; `openclaw.ai/blog/openclaw-2-accidentally`
- ClawTeam — coordination-layer design: `cloud.tencent.com/developer/article/2689440`; `github.com/HKUDS/ClawTeam`
- Coordination pattern taxonomy: `syncaiagents.com`
- Anthropic, *How we built our multi-agent research system* (2025-06); Cognition, *Don't Build Multi-Agents* (2025-06); Microsoft Azure Architecture Center, *AI agent orchestration patterns* (2026-02)
- A2A protocol: `a2a-protocol.org`; `github.com/a2aproject`
- Agentic AI Foundation (MCP + AGENTS.md + Goose): `linuxfoundation.org`

### 12.4 What this document deliberately does not claim

- It does not claim any capability is "verified" in a running deployment. Every statement about current behaviour is a statement about **source**, traced to a file and line.
- It does not claim multi-agent is the right default. The published evidence says the opposite: single agent with tools unless tool count or context forces otherwise.
- It does not claim a per-bot VM is required for parity. The published Grok Bot claim that motivates it is contradicted by xAI's own documentation.
- It does not claim the phases are cheap. Phase 1 is small; Phases 2–4 are multi-week each.
