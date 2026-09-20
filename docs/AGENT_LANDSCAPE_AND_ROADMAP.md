# Open-Source Agent Landscape — Research & Feature Roadmap for Alpha

**Date:** 2026-09-20 · **Status:** research + proposal (no code changes)

---

## 0. Method & limitations

| Project | Method | Notes |
|---|---|---|
| **OpenClaw** | ✅ Cloned (`github.com/openclaw/openclaw`, depth 1) + README + docs site | `apps/` was empty in the shallow clone (likely submodules); architecture taken from README/VISION |
| **OpenManus** | ✅ Cloned (`FoundationAgents/OpenManus`) + read `app/` source directly | Best source-level coverage — excerpts below are real |
| **OpenBot** | ✅ Cloned (`CopilotKit/openbot`) + README | Multi-framework adapters (`agent-langgraph`, `agent-crewai`, `agent-agno`, …) |
| **Rakazo** | ✅ Cloned (`elie222/rakazo`) + README | Electron + web + Expo mobile |
| **Hermes Agent** | ⚠️ Public docs only (`hermes-agent.nousresearch.com`) | Not cloned — no source excerpts available |
| **Bot** | ⚠️ Ambiguous — no canonical highly-starred repo by that name | Treated as OpenBot / Rakazo-class "persistent bot platform" |
| **Comparable repos** | ⚠️ Ranking article only (stars as of Apr 2026) | AutoGPT 183k, Langflow 147k, Dify 136k, LangChain 132k, Gemini CLI 100k, browser-use 86k, RAGFlow 77k, LobeHub 75k, MetaGPT 67k, AutoGen 57k, **Mem0 52k**, Flowise 52k, CrewAI 48k, LocalAI 45k, Cherry Studio 43k, Agno 39k |

**Stated limitation:** Hermes and the "Bot" entry are documented from public docs/search results only — no source files were read, so no code excerpts are given for them. Star counts are from a secondary ranking article, not from GitHub directly.

---

## 1. Project-by-project research

### 1.1 OpenClaw — *"your assistant, on your devices, in your chats"*

**Purpose.** Self-hosted personal/team assistant. Runs on your own hardware; meets you in Discord, iMessage, Slack, Teams, Telegram, WhatsApp (20+ channels) plus native macOS/iOS/Android/Windows/Linux apps.

**Architecture — the key phrase is from its own README:**

> *trusted gateway, untrusted execution, deterministic policy*

That single sentence is the most transferable idea in this whole survey. Three concentric capability layers:

| Layer | Contents |
|---|---|
| Core (8 tools) | `read`, `write`, `edit`, `apply_patch`, `exec`, `process`, `web_search`, `web_fetch` |
| Advanced (17) | `browser`, `memory`, session orchestration (`sessions_list/history/status/send/spawn`), `cron`, `gateway` (self-restart), `message` |
| Knowledge (53 Skills) | GitHub, Google Workspace (`gog`), Slack, Obsidian, 1Password, `coding-agent`, `tmux`, `session-logs` … |

**Distinctive patterns**

1. **"Skills are manuals, not keys."** A Skill teaches procedure; it grants **zero** permission. Permission lives in `tools.*`. Installing the GitHub Skill without `exec` enabled means `gh` simply cannot run.
2. **`skills.allowBundled` whitelist.** Bundled Skills auto-load if the host has the matching CLI — so a fresh install may be running Skills you never reviewed. Default should be deny; opt in by name.
3. **Exec approval gate.** Every shell command is surfaced for human review before running — a deliberate checkpoint at the most dangerous layer.
4. **Trigger → Action → Deliver.** The atomic unit of automation: cron/message/event triggers, tools+skills act, `message` delivers to a channel.
5. **Persistent runtime with a clock.** Cron + `gateway` self-restart turn it from reactive to autonomous infrastructure.

---

### 1.2 OpenManus — general-purpose agent framework (source read)

**Purpose.** Open alternative to Manus: autonomous multi-step task execution — browser, code, files, data analysis.

**Modules (from `app/`)**

```
app/agent/   base.py react.py toolcall.py manus.py browser.py swe.py
             data_analysis.py mcp.py sandbox_agent.py
app/tool/    base.py bash.py python_execute.py str_replace_editor.py
             planning.py terminate.py ask_human.py mcp.py
             file_operators.py web_search.py computer_use_tool.py
             search/ sandbox/ chart_visualization/
app/flow/    base.py flow_factory.py planning.py     # multi-agent orchestration
app/sandbox/ app/daytona/                            # execution isolation
app/mcp/     app/prompt/ app/llm.py app/config.py app/schema.py
```

**Agent loop — `app/agent/react.py` (real excerpt):**

```python
class ReActAgent(BaseAgent, ABC):
    max_steps: int = 10
    current_step: int = 0
    state: AgentState = AgentState.IDLE

    @abstractmethod
    async def think(self) -> bool: ...   # decide next action
    @abstractmethod
    async def act(self) -> str: ...      # execute it

    async def step(self) -> str:
        should_act = await self.think()
        if not should_act:
            return "Thinking complete - no action needed"
        return await self.act()
```

**Distinctive patterns**

1. **Explicit `max_steps` + state machine.** `base.py` loops `while current_step < max_steps and state != FINISHED`, and on exhaustion returns `f"Terminated: Reached max steps ({max_steps})"` — *never* an infinite loop, and never a crash.
2. **`ask_human` as a first-class tool.** Human-in-the-loop is a tool the agent calls, not a UI hack.
3. **`terminate` as a tool.** Completion is an explicit action the model takes, which makes it observable and testable.
4. **Planning as both a tool and a flow.** `tool/planning.py` for in-run plans; `flow/planning.py` + `flow_factory.py` for multi-agent orchestration.
5. **Per-agent tool specialization.** `BrowserAgent` gets `BrowserUseTool`; `SWEAgent` gets `StrReplaceEditor` + `PythonExecute`. Narrow by default.
6. **Pluggable sandbox backends.** `app/sandbox/` and `app/daytona/` — isolation is an interface, not a hardcoded container.

---

### 1.3 OpenBot (CopilotKit) — *"AI coworkers"*

**Purpose.** Enterprise agent platform. Headline: **"Each coworker gets a computer of its own — a real browser with its own logins, its own files, and only the tools you grant."**

**Architecture (from README):**

> Anything a Bot does to a computer, a file, an MCP server or a component goes through **one gateway that decides and records it**. Every tool call the Bot makes comes back through the gateway, which resolves the target, decides it against your policy, records an audit row, and only then acts — or refuses and **names the rule**.

- One container per Bot: own Chromium, logins, workspace — built by a **supervisor**.
- Decisions land in PostgreSQL; threads in a state store.
- Admin routes: `/admin/computers`, `/admin/playground`.
- Framework-agnostic: `agent-langgraph`, `agent-crewai`, `agent-agno`, `agent-adk`, `agent-ag2`, `agent-claude-sdk`, `agent-computer`, `agent-bot` …

**Distinctive patterns**

1. **Single policy gateway for every side effect** — tool calls, file edits, MCP, UI components all pass one chokepoint.
2. **Refusals name the rule.** "Denied by policy X" beats a silent failure for debuggability and trust.
3. **Audit row per action** — decisions are persisted, not just logged.
4. **Per-agent computer** (container + browser profile) — stronger isolation than a shared sandbox, and it preserves logins.

---

### 1.4 Rakazo — persistent AI teammates

**Purpose.** Open-source Grok-Bot alternative: **persistent** bots with their own conversations, memory, routines, and history. Apache-2.0, self-hosted, BYO model.

**Modules:** `apps/{api, desktop, mobile, web, worker, www}` — Electron desktop + Expo mobile + web + a **worker** process.

**Distinctive patterns**

1. **Persistence as the product.** Bots own conversations, memory, **routines**, and history — the bot is a long-lived entity, not a request.
2. **Routines** (scheduled behaviour) as a first-class bot attribute.
3. **Multi-surface from one core** — `api` + `worker` shared by desktop/mobile/web.
4. **Tool source pluralism:** Composio, Pipedream Connect, MCP, OpenAPI.

---

### 1.5 Hermes Agent (docs only)

- Memory in two files: `MEMORY.md` + `USER.md`; pluggable providers (Honcho, Mem0, Hindsight, Supermemory …).
- **Checkpoints:** auto-snapshot of the working dir *before* file changes, `/rollback` to restore.
- Context files auto-discovered: `.hermes.md`, `AGENTS.md`, `CLAUDE.md`, `SOUL.md`, `.cursorrules`.
- Subagents with **isolated context + restricted toolsets + own terminal sessions** (3 concurrent by default).
- Event hooks for guardrails; cron in natural language; MCP stdio/HTTP with per-server filtering.
- Credential pools rotate on rate limit; fallback providers fail over.

**No source excerpts — not cloned.**

---

### 1.6 Notable patterns from the wider top-20

- **Mem0 (52k)** — memory as a *separate product layer*. Validates that memory deserves its own subsystem, not a dict on the agent.
- **browser-use (86k)** — browser as a first-class tool surface.
- **AutoGen / CrewAI / MetaGPT** — multi-agent is mainstream; role-based crews and conversational agent graphs.
- **Langflow / Dify / Flowise** — visual builders dominate by stars. A graph/flow authoring UI is a real adoption driver.

---

## 2. Our stack, current features, goals

### 2.1 Stack

| Layer | Technology |
|---|---|
| Agent runtime | LangGraph-compatible super-agent (Python, `uv`) |
| Backend | FastAPI Gateway (`app/gateway`) + harness package (`alpha`); routers for bots, memory, skills, MCP, runs, supervision, ops, channels … |
| Frontend | Next.js 15 App Router (pnpm), SSE streaming |
| Desktop | Electron (bundled Node 22 + uv, NSIS installer) |
| Deployment | `make dev` / Docker Compose: nginx 2026 → Gateway 8001, frontend 3000, optional provisioner 8002 |
| Config | `config.yaml` + `extensions_config.json` (both gitignored, API-writable); `agent_presets`, `tool_groups`, `sandbox`, `authorization`, `scheduler`, `skills` |

### 2.2 Current features

- **Sandbox** per-thread isolated execution; `sandbox.allow_host_bash: false` by default.
- **Tools:** built-ins + MCP + community + skills (`skills/public`, `skills/custom`); skill review contracts + CI waivers.
- **Subagent delegation**; bot fleet (templates, departments, org chart, kill switch, handoff, heartbeat, work-discovery matching, quality gates).
- **Memory:** persistent memory subsystem (`memory-full.example.yaml`).
- **IM channels:** Feishu, Slack, Telegram, Discord, DingTalk.
- **Extensions:** 5 contribution kinds (middleware, task lifecycle, observers, gateway services, HTTP routers).
- **Self-healing:** `self_healing_runner.py` (retry + patch + postmortem), `environment_auto_healer.py`, `routers/supervision.py` watchdog, `start.ps1` bounded auto-restart with backoff.
- **Auth/policy:** `authorization` section, `agent_presets` (built-ins `standard`, `minimal`).

### 2.3 Goals (from this session)

Production robustness (self-healing, supervised auto-start, retry/backoff, fault isolation); surface real backend data in the UI; Settings with LLM config; full bot-creation flow; a working production build and installer.

---

## 3. Prioritized capability roadmap

Legend — **Complexity:** S = days, M = 1–2 weeks, L = 3+ weeks. **Deps** = must exist first.

### P0 — Robustness & trust foundations

| # | Capability | What | Why | Inspired by | Deps | Cx |
|---|---|---|---|---|---|---|
| 1 | **Retry with configurable backoff** | Backoff (fixed/exponential + jitter) in `self_healing_runner.run_with_self_healing` and any supervisor restart | Retries are currently immediate; a failing dependency gets hammered | OpenClaw `gateway` self-restart; `start.ps1` already has `min(30,3*n)` | none | S |
| 2 | **Deterministic policy gateway** | One chokepoint that resolves target → evaluates policy → writes audit row → acts, or refuses **naming the rule** | OpenBot's core idea; turns scattered checks into auditable, testable policy | **OpenBot** | `authorization` | M |
| 3 | **Step budget + stuck detection** | Hard `max_steps`, `AgentState`, explicit `terminate`; surface "terminated at max steps" as a first-class run outcome | Prevents runaway loops; makes completion observable | **OpenManus** `react.py` | none | S |
| 4 | **`ask_human` / approval tool** | A tool the agent calls to request approval; surfaces in UI as a card | Human-in-the-loop as protocol, not UI hack | **OpenManus** `ask_human`; OpenClaw exec gate | HumanApprovalCard (exists) | M |

```python
# P0.1 sketch — backoff in the self-healing loop
delay = min(base * (2 ** (attempt - 1)), cap) * (1 + random.random() * jitter)
await asyncio.sleep(delay)
```

```python
# P0.3 sketch — mirror OpenManus' explicit budget
while self.current_step < self.max_steps and self.state != AgentState.FINISHED:
    ...
else:
    results.append(f"Terminated: Reached max steps ({self.max_steps})")
```

### P1 — Capability & knowledge separation

| # | Capability | What | Why | Inspired by | Deps | Cx |
|---|---|---|---|---|---|---|
| 5 | **Skills grant no permissions** | Assert at load: a Skill may not widen `tools.*`; log + refuse violations | OpenClaw's best idea; stops "install a skill, silently gain access" | **OpenClaw** | skills loader | S |
| 6 | **Bundled-skill allowlist** | `skills.allowBundled: [name, …]`, default deny | Auto-loading unreviewed skills is a real footgun | **OpenClaw** | #5 | S |
| 7 | **Per-bot/per-agent toolset scoping** | Extend `agent_presets` so each bot declares its tool groups | Narrowest surface per worker; already 80% built via `agent_presets` + `disabled_tools` | OpenManus; Hermes subagents | existing | S |
| 8 | **Narrow subagent toolsets** | Subagents inherit a *restricted* subset, not the parent's full set | Contain blast radius | **Hermes** | #7 | M |

### P2 — Memory, context & continuity

| # | Capability | What | Why | Inspired by | Deps | Cx |
|---|---|---|---|---|---|---|
| 9 | **Curated memory files** | `MEMORY.md` (project) + `USER.md` (user), bounded and curated, alongside existing memory | Cheap, inspectable, editable — and matches how agents already read this repo | **Hermes**; OpenClaw `memory_search/get` | memory subsystem | S |
| 10 | **Checkpoint + rollback** | Snapshot before mutating file operations; `/rollback` | Reversible agent edits; huge trust win | **Hermes** | CLI layer (prototype done this session) | M |
| 11 | **Context-file auto-discovery** | Auto-load `AGENTS.md`/`CLAUDE.md`/scoped `AGENTS.md` | Already partially true via AGENTS.md; make it explicit and budgeted | **Hermes** | `check-agent-guidance` | S |

### P3 — Autonomy & orchestration

| # | Capability | What | Why | Inspired by | Deps | Cx |
|---|---|---|---|---|---|---|
| 12 | **Trigger → Action → Deliver** | First-class automation unit: cron/event trigger, allowlisted action, deliver to channel | The reusable shape behind every scheduled job | **OpenClaw**; prototype `scripts/run-trigger.ps1` shipped | #2 | M |
| 13 | **Bot routines** | Scheduled behaviour attached to a bot profile (not a global cron) | Makes bots persistent entities | **Rakazo** | #12, bots router | M |
| 14 | **Session/subagent orchestration** | `sessions_spawn`/`sessions_send`-style fan-out with isolated contexts and result aggregation | Parallelism with isolation | **OpenClaw**; Hermes | #7, #8 | L |
| 15 | **Flow/graph authoring UI** | Visual builder over existing flows | Visual builders dominate by stars; big adoption lever | Langflow/Dify | #14 | L |

### P4 — Surfaces & operations

| # | Capability | What | Why | Inspired by | Deps | Cx |
|---|---|---|---|---|---|---|
| 16 | **Per-bot computer** | One container/sandbox per bot with its own browser profile + workspace | Strong isolation + preserved logins | **OpenBot** | sandbox, provisioner | L |
| 17 | **Worker process split** | Separate long-running `worker` from the request path (Rakazo `apps/worker`) | Scheduled/background work stops competing with requests | **Rakazo** | #12 | M |
| 18 | **Refusals that name the rule** | Every denial returns the policy/rule id | Debuggability and user trust | **OpenBot** | #2 | S |
| 19 | **Fix the production build** | `frontend/.next/standalone` missing `server.js` (pnpm symlink) | Nothing ships without it | — | — | S |

### Recommended build order

```
#19 (unblock shipping)
  → #1 #3 (loop safety, backoff) — small, high value
  → #2 #18 (policy gateway + named refusals)
  → #5 #6 #7 (permission/skill separation, presets)
  → #9 #10 #11 (memory, checkpoints, context)
  → #4 (approval tool)
  → #12 #13 #17 (triggers, routines, worker)
  → #8 #14 (narrow subagents, orchestration)
  → #16 (per-bot computer)
  → #15 (visual flow builder)
```

Rationale: ship-blocking work first; then cheap safety wins that reduce incident rate; then the trust layer (policy + permissions); then memory/continuity; then autonomy; then expensive isolation and UI.

---

## 4. Highest-leverage takeaways

1. **"Trusted gateway, untrusted execution, deterministic policy"** (OpenClaw) — adopt as an explicit architectural statement for our Gateway.
2. **"Skills are manuals, not keys"** (OpenClaw) — enforce mechanically, not by convention.
3. **One policy chokepoint that records an audit row and names the rule on refusal** (OpenBot) — we have `authorization`; it needs to become the single path.
4. **Explicit step budgets and explicit termination** (OpenManus) — cheap, and eliminates a whole class of runaway-run incidents.
5. **Memory as a subsystem, not a dict** (Mem0/Hermes) — the top-20 validates it as a product layer.
