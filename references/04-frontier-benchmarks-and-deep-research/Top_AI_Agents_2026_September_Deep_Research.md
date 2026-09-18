# Top AI Agents & Agentic Architectures — September 2026

## Scope

This report surveys the most relevant high-end AI agent products, open-source agents, coding agents, and agent-development frameworks visible publicly as of **September 14, 2026**. It focuses on architecture and capabilities that are useful when designing a next-generation general-purpose agent harness.

**Important:** this is not a single leaderboard. Some projects are complete agent applications, some are coding agents, and some are frameworks/SDKs. Their strongest features are therefore compared by architectural role. Proprietary systems such as Astra/Fable/Claude Code do not expose all internals; only publicly documented behavior is treated as verified.

---

# 1. Executive conclusion

The strongest current architectures are converging on a common stack:

```text
User / Event
    ↓
Identity + Session
    ↓
Executive / Goal Compiler
    ↓
Context + Memory + World State
    ↓
Planner / Supervisor
    ↓
Model Router
    ↓
Agent Teams / Subagents
    ↓
Skills + MCP + Tools
    ↓
Computer / Browser / Shell / APIs
    ↓
Sandbox / Policy / Credentials
    ↓
Observe + Verify
    ↓
Checkpoint + Durable State
    ↓
Continue / Recover / Replan
    ↓
Evaluation + Telemetry
    ↓
Learning / Evolution
```

No single project dominates every layer.

A particularly strong synthesis would combine:

- **Hermes** — learning loop, skills, persistent memory, channels, cron, bot mode, model/provider flexibility, subagents, remote terminal backends, trajectory tooling. [1][2][3]
- **Agent Workspace 2.0** — super-agent packaging around skills, memory, sandboxes, tools, subagents and long-horizon tasks. [4]
- **Deep Agents** — opinionated long-horizon harness with filesystem, subagents, context management, persistence/checkpointing and model agnosticism. [5]
- **Letta / Letta Code** — stateful agents, persistent identity/memory, self-modification of memory/skills/prompts and harness mods, plus always-on operation. [6]
- **Claude Code / Fable-style systems** — plan/execute coding, long-running work, multi-agent collaboration, visual verification, skills and strong tool use.
- **OpenAI Agents SDK / Codex-style systems** — handoffs, sessions, guardrails, sandbox agents, tracing and strong coding/runtime primitives. [7][8]
- **OpenHands** — coding-agent runtime + Agent Server/SDK + automation separation. [9]
- **Gemini CLI** — very strong open-source terminal agent, large context, search, shell/files, MCP and a genuinely useful free tier. [10][11]
- **Agent Zero** — full Linux desktop, browser DOM annotation, document co-work, project isolation and plugin/MCP/A2A extensibility. [12]
- **Strands** — model-driven minimal loop, model/provider agnosticism, subagents, graphs, swarms, workflows, MCP, A2A, observability and a dedicated open-source harness SDK. [13]
- **Mastra** — agents + workflows + memory + human approval + replay + supervisor agents + workspaces + eval/tracing. [14][15]
- **Microsoft Agent Framework** — production multi-agent workflows, sequential/concurrent/handoff/group collaboration and Python/.NET. [16]
- **CrewAI** — Crews for autonomous collaboration + Flows for deterministic/event-driven production control, with memory/checkpointing/MCP/A2A. [17]
- **Aider** — codebase mapping, Git-native editing, automatic tests/linting, local-model support and efficient terminal coding. [18]
- **Cline / Roo Code** — plan-vs-act separation, rules/skills/custom modes, browser/terminal/file execution and broad model compatibility. [19][20]
- **SWE-agent** — explicit tool-use trajectories and inspection/replay of agent runs. [21][22]
- **AutoGPT** — workflow builder, continuous agents, deployment controls, monitoring/analytics and benchmarks. [23]
- **SuperAGI** — concurrent autonomous agents and extensible tool-driven agents. [24]
- **Nanobot** — lightweight agent runtime with subagents, model switching, WebUI, context compaction and channel/automation support. [25]

The main design lesson is therefore: **build a meta-harness, not a clone of any one agent.**

---

# 2. What counts as a top-level agent architecture?

A mature agent system should ideally provide most of the following:

1. Goal understanding and task compilation.
2. Persistent multi-session state.
3. Structured memory rather than raw transcript-only history.
4. Long-horizon planning.
5. Dynamic replanning and recovery.
6. Parallel subagents / agent teams.
7. Skills that can be loaded on demand.
8. Tool registry + MCP/A2A interoperability.
9. Real shell/filesystem/browser/computer access.
10. Sandboxed execution and granular permissions.
11. Checkpoints/resume after interruption.
12. Independent verification/evaluation.
13. Observability and trajectory replay.
14. Scheduling/background execution.
15. Model/provider abstraction.
16. Autonomous self-improvement with regression controls.

---

# 3. Hermes Agent — strongest features

**Category:** general-purpose agent / personal autonomous agent / long-running assistant.

Current public Hermes materials describe a built-in learning loop: agent-curated memory, skill creation, skill improvement, session search, and a deepening user model. It is provider/model agnostic, has terminal/TUI interfaces, and can operate through Telegram, Discord, Slack, WhatsApp, Signal and CLI. Hermes also supports cron scheduling, multiple terminal backends, subagents, MCP, browser automation, media tools and trajectory generation for research/training. [1][2]

### High-value capabilities

| Feature | Strength |
|---|---|
| Self-improving skills | ★★★★★ |
| Persistent memory | ★★★★★ |
| Session search | ★★★★★ |
| Multi-channel gateway | ★★★★★ |
| Scheduled automation | ★★★★★ |
| Subagents/parallelization | ★★★★☆ |
| MCP integration | ★★★★★ |
| Browser/computer tools | ★★★★☆ |
| Multi-model/provider support | ★★★★★ |
| Remote execution | ★★★★★ |
| Desktop bot roster | ★★★★★ |
| Local/self-hosting | ★★★★★ |

### Architectural ideas worth copying

```text
User
 ↓
Gateway
 ↓
Profile / Bot
 ↓
Session
 ↓
Agent loop
 ├─ tools
 ├─ skills
 ├─ memory
 ├─ delegation
 └─ cron
 ↓
execution backend
```

### Especially notable in September 2026

Hermes v0.21.x has a **Bot Mode** where profiles become named bots with their own role, model, memory, skills, avatar and routines; bots can message each other and participate in group chats. Cron jobs can retain memory/continuity and deliver output to messaging targets. [2][3]

### Best Hermes ideas for a new harness

- memory nudges
- automatic skill generation
- skill self-improvement
- FTS/session retrieval
- model switching without architectural changes
- profiles as isolated agent identities
- recurring jobs as first-class agents
- multiple execution backends
- channel gateway
- MCP server filtering
- trajectory collection

---

# 4. Agent Workspace 2.0

**Category:** open-source super-agent harness.

Agent Workspace 2.0 is a ground-up rewrite positioned as an open-source super-agent harness for research, coding and creation. It combines subagents, memory, skills, sandboxes, tools and a message gateway, and is built on LangGraph/LangChain. It is explicitly designed for tasks ranging from minutes to hours. [4]

### Strong features

- long-horizon task execution
- skills as reusable capability modules
- persistent memory
- sandbox-aware execution
- filesystem
- subagent spawning
- planning/decomposition
- research workflows
- coding workflows
- content creation
- message gateway
- benchmark/prototyping ecosystem through LLM Space

### Architectural value

Agent Workspace is especially good as a **reference for packaging a complete harness**, rather than just exposing an agent loop.

```text
Super Agent
 ├── planner
 ├── subagents
 ├── memory
 ├── skills
 ├── tools
 ├── sandbox
 └── gateway
```

---

# 5. Deep Agents

**Category:** general agent harness / developer framework.

Deep Agents is explicitly described as a batteries-included agent harness with opinionated defaults for long-horizon, multi-step work. Its public features include subagents, filesystem access, context management, tool offloading to files, persistence/checkpointing through LangGraph, and support for frontier, open-weight and local models. [5]

### Best features

- built-in long-horizon behavior
- isolated subagents
- pluggable filesystem
- context summarization
- offloading tool output to disk
- checkpointing/persistence
- model agnosticism
- extensibility without forking

### Architectural lesson

Deep Agents demonstrates that the useful abstraction is often one layer **above** a graph runtime:

```text
LangGraph = runtime
Deep Agents = opinionated harness
```

That is exactly the direction to use for a universal agent OS.

---

# 6. Letta / Letta Code

**Category:** stateful/self-improving agent platform.

Letta is particularly important for memory-first architectures. Letta Code describes agents with persistent identity and experience across time, with capabilities to rewrite their memory, skills, prompts and even the harness through mods. It supports always-on operation, local/remote/cloud execution, a desktop app, messaging channels and Git-backed memory/context. [6]

### High-value features

- stateful agents
- durable identity
- long-term memory
- memory blocks
- cross-session continuity
- memory editing by the agent
- skill learning
- prompt learning
- “dreaming” / sleep-time compute concepts
- diagnosis tools such as `/doctor`
- searchable conversations
- Git-backed context state
- always-on agents
- remote/local execution

### Architectural lesson

Letta pushes the design from:

```text
agent + memory
```

toward:

```text
agent = persistent identity + memory + skills + state + experience
```

This is a strong candidate for the **identity/memory layer** of a next-generation harness.

---

# 7. OpenAI Agents SDK / Codex-style architecture

**Category:** agent runtime + coding agent infrastructure.

The OpenAI Agents SDK uses a small primitive set: agents, tools, handoffs/agents-as-tools and guardrails. Current public documentation also includes sandbox agents with real isolated workspaces, sessions, human-in-the-loop, MCP, tracing, and built-in loops. [7]

Codex-style runtime patterns expose another important architecture: sandbox levels, approval policies, workspace write access, network gating and tool/MCP control. [8]

### Best ideas

- minimal composable primitives
- agents as tools
- handoffs
- guardrails
- sandbox agents
- persistent sessions
- human-in-loop
- tracing
- provider/tool abstraction
- approval policies
- fine-grained sandbox boundaries
- noninteractive autonomous operation

### Best lesson

A small primitive core can support surprisingly rich agent graphs.

---

# 8. Claude Code / Anthropic coding architecture

**Category:** frontier coding agent.

Claude Code is one of the strongest examples of a tool-rich coding agent: repository understanding, terminal execution, file editing, browser/other tools, skills/rules, planning, multi-agent work and increasingly automated safety checks.

Cline's public Plan/Act model makes the same architectural point explicitly: planning can be separated from mutation, allowing exploration without changing the workspace before execution begins. [19]

### High-value patterns to copy

- repository-wide understanding
- plan vs execute separation
- terminal-first operation
- incremental edits
- test/fix loops
- project-local rules
- skills
- MCP
- agent teams
- checkpoint/resume
- visual verification
- permission control

---

# 9. Fable-style long-horizon architecture

**Category:** long-running general-purpose computer/knowledge-work agent.

Public descriptions emphasize multi-application work lasting hours, coding, research, computer interaction, tool use, recovery, testing, user updates and visual checking.

### Best architectural patterns

```text
Goal
 ↓
Plan
 ↓
Checkpoint
 ↓
Execute
 ↓
Observe
 ↓
Verify
 ↓
Persist
 ↓
Continue across sessions
```

The biggest lesson is **trajectory reliability** rather than single-turn quality.

---

# 10. Gemini CLI

**Category:** open-source terminal agent.

Gemini CLI is open-source and currently advertises a free tier, large context models, Google Search grounding, file operations, shell commands, web fetching, MCP and streaming JSON events. Its September 2026 v0.59.0 release added security work including SSRF prevention for MCP OAuth, fail-closed workspace trust, and restricted-mode MCP filtering. [10][11]

### Best features

- open source
- terminal-native
- large context
- free personal tier
- search grounding
- shell
- files
- web fetching
- MCP
- streaming structured events
- workspace trust
- restricted MCP mode
- broad model capabilities

### Best lesson

Gemini CLI is a strong reference for **free/open terminal-first agent UX plus security controls**.

---

# 11. OpenHands

**Category:** AI software engineering platform.

OpenHands exposes an Agent Server API through its SDK, a TypeScript client for browser UI, and a separate automation service that decides when work should run and dispatches it to the Agent Server/SDK. [9]

### Best features

- agent server separation
- SDK
- browser client
- automation/scheduling
- agent execution boundary
- software engineering workflows
- deploy/self-host orientation

### Architectural lesson

Separate:

```text
Automation control plane
        ↓
Agent runtime
        ↓
Tools/environment
```

rather than putting scheduling and task execution in one process.

---

# 12. Agent Zero

**Category:** full-computer general-purpose agent.

Agent Zero is especially relevant for “do almost anything on a computer.” Its public documentation describes a Dockerized Linux desktop, browser with DOM annotation, live document co-working, projects, skills, plugins, memory, host-machine bridge and multi-agent cooperation. [12]

### Best features

- full Linux desktop
- GUI applications
- browser DOM annotation
- document co-working
- Markdown/document/spreadsheet/presentation workflows
- project isolation
- memory
- plugin hub
- MCP
- A2A
- subordinate agents
- host bridge

### Best lesson

A general-purpose agent becomes far more capable when the runtime exposes a **real computer environment**, not only APIs.

---

# 13. Strands Agents

**Category:** open agent SDK / harness.

Strands is a model-driven agent toolkit available in Python and TypeScript. Public materials emphasize a simple agent loop, model agnosticism, subagents, graph/swarm/workflow patterns, native MCP, A2A, OpenTelemetry, local-model support and production deployment. The organization also publishes a standalone open-source `harness-sdk` aimed at end-to-end agent control. [13]

### Best features

- simple core loop
- model agnostic
- local models
- subagents
- graph
- swarm
- workflow
- MCP
- A2A
- streaming
- structured output
- OpenTelemetry
- harness SDK

### Best lesson

A lightweight core can still support sophisticated topologies if **composition** is strong.

---

# 14. Mastra

**Category:** TypeScript agent/workflow framework.

Mastra combines agents with a graph-style workflow system, memory, human approval, persistent state, replay, supervisor agents, workspaces, skills, guardrails, scorers and tracing. [14][15]

### Best features

- agents
- multi-step workflows
- sequential/branch/parallel execution
- memory
- observational memory
- suspend/resume
- human-in-loop
- rewind/replay
- supervisor agents
- workspaces
- filesystem/sandbox capabilities
- skill files
- model router
- MCP
- evals/scorers
- tracing

### Best lesson

Use **agents for reasoning** and **deterministic workflow steps where reasoning is unnecessary**. This can greatly improve reliability and cost.

---

# 15. Microsoft Agent Framework

**Category:** enterprise multi-agent framework.

Microsoft Agent Framework is the successor direction to AutoGen. Its public repository positions it as an open Python/.NET framework for production agents and multi-agent workflows, with sequential, concurrent, handoff and group collaboration patterns and interoperability through standards such as A2A and MCP. [16]

### Best features

- multi-agent orchestration
- sequential workflows
- concurrent workflows
- handoffs
- group collaboration
- Python/.NET
- MCP
- A2A
- production deployment patterns

### Important ecosystem note

AutoGen itself is now in maintenance mode and points new users toward Microsoft Agent Framework. [26]

---

# 16. CrewAI

**Category:** multi-agent workflow framework.

CrewAI separates autonomous team behavior (**Crews**) from controlled, event-driven production workflows (**Flows**). Public documentation also lists memory, knowledge, checkpointing, asynchronous execution, MCP and A2A. [17]

### Best features

- autonomous role-based agent teams
- task delegation
- event-driven Flows
- branching
- explicit state
- Python-native control
- memory
- checkpointing
- async execution
- MCP
- A2A

### Best lesson

A high-end system needs both:

```text
Autonomy
+
Deterministic control
```

not only one or the other.

---

# 17. PydanticAI

**Category:** typed Python agent framework.

PydanticAI is particularly useful as a reliability-oriented foundation: typed tool interfaces, schema validation, Python-native agent construction and graph-oriented execution primitives.

### Best lesson

For a production harness, typed contracts should exist between:

```text
agent ↔ tool
agent ↔ subagent
workflow ↔ state
model ↔ structured output
```

This reduces ambiguity and makes evaluation easier.

---

# 18. Aider

**Category:** highly efficient terminal coding agent.

Aider provides codebase maps, Git integration, multi-language support, image/web-page context, voice input, automatic linting/testing, and support for cloud and local models. [18]

### Best features

- repository map
- incremental context
- Git-native changes
- automatic commits
- lint/test loops
- 100+ languages
- local models
- web/image context
- voice-to-code

### Best lesson

A coding agent should understand the **structure of the whole repository** rather than treating every file as isolated text.

---

# 19. Cline

**Category:** autonomous IDE agent.

Cline explicitly separates Plan and Act modes. Plan mode can inspect/search and strategize without modifying files or executing commands; Act mode performs changes. Cline also exposes rules and skills, MCP, broad provider support and configurable autonomy/approval. [19]

### Best features

- Plan/Act separation
- project rules
- skills
- terminal
- file modification
- browser automation
- MCP
- model/provider agnosticism
- approval/autonomy controls

---

# 20. Roo Code

**Category:** autonomous IDE coding agent / Cline ecosystem.

Roo Code adds custom modes for role specialization and broad API/model support, while keeping direct filesystem, terminal and browser operation. [20]

### Best features

- custom roles/modes
- coding agent
- browser
- terminal
- files
- MCP ecosystem
- provider flexibility
- task sharing
- global/project configuration

### Best lesson

Treat specialized agent personalities/roles as **configuration profiles**, not separate codebases.

---

# 21. SWE-agent

**Category:** benchmark-oriented software engineering agent.

SWE-agent explicitly records complete agent trajectories as structured JSON containing response/action/observation steps and provides CLI/web trajectory inspection. [21][22]

### Best features

- trajectory capture
- replay/inspection
- environment interaction
- benchmark integration
- explicit action/observation records

### Best lesson

A world-class agent should treat every trajectory as an inspectable dataset:

```text
thought / decision
→ action
→ observation
→ state
→ result
```

This is essential for self-improvement.

---

# 22. AutoGPT

**Category:** autonomous agent platform / workflow platform.

AutoGPT now emphasizes visual workflow composition, deployment controls, continuous agents, triggers, scheduled runs, monitoring/analytics and an agent protocol. It also has its own benchmark infrastructure. [23]

### Best features

- workflow builder
- deployable agents
- continuous execution
- triggers
- schedules
- monitoring
- analytics
- benchmarks
- agent protocol

### Best lesson

Treat agents as **deployable long-running services**, not only interactive assistants.

**License caveat:** the current platform portion uses Polyform Shield while other portions of the repository retain MIT licensing. Check the relevant component before commercial reuse. [23]

---

# 23. SuperAGI

**Category:** open autonomous agent framework.

SuperAGI focuses on provisioning/deploying concurrent autonomous agents and extending them through tools. It also has benchmark and agent-management components. [24]

### Best lessons

- concurrent agents
- tool extension
- agent provisioning/deployment
- benchmark-driven evaluation

A practical warning from the current repository is that security issues remain an active maintenance concern, including reports around unsafe deserialization/SSRF in 2026. [27]

---

# 24. Nanobot

**Category:** lightweight personal agent runtime.

Current nanobot releases emphasize compactness plus durable runtime features: inline subagents, per-session model switching, WebUI, live configuration changes, context-compaction visibility, session mentions and a native terminal agent. [25]

### Best features

- small footprint
- durable workbench
- subagents
- per-session model selection
- WebUI
- context compaction
- session-to-session references
- messaging/tool integrations
- automation

### Best lesson

You can get surprisingly capable agent infrastructure without requiring a giant framework.

---

# 25. AgentGPT

**Category:** historical autonomous-agent platform.

AgentGPT is worth mentioning because of its influence, but the official repository was archived in January 2026. It remains useful historically for configurable autonomous agents and pause/resume behavior, but should **not** be considered a current frontier reference implementation. [28]

---

# 26. Legacy / lower-priority references

Projects such as early AutoGPT Classic, SuperAGI and AgentGPT were influential in autonomous-agent experimentation, but they are less important than current long-horizon and production-oriented systems.

The most valuable current references are the ones that solve infrastructure problems:

```text
persistent state
long horizons
real tools
sandboxing
subagents
skills
verification
evaluation
observability
self-improvement
```

---

# 27. Feature matrix

Legend: **● strong**, **◐ present/useful**, **○ limited/not central**, **— not a primary focus**.

| System | Long Horizon | Memory | Subagents | Skills | MCP | Computer/GUI | Sandbox | Scheduling | Eval/Trace | Self-improve | Model Agnostic |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hermes | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Agent Workspace 2 | ● | ● | ● | ● | ● | ◐ | ● | ◐ | ● | ◐ | ● |
| Deep Agents | ● | ◐ | ● | ● | ● | ◐ | ● | ◐ | ● | ○ | ● |
| Letta Code | ● | ● | ◐ | ● | ◐ | ◐ | ◐ | ● | ● | ● | ● |
| Claude Code | ● | ◐ | ● | ● | ● | ◐ | ● | ◐ | ● | ◐ | ◐ |
| Fable-style | ● | ● | ● | ● | ● | ● | ● | ● | ● | ◐ | ◐ |
| OpenAI Agents SDK | ● | ● | ● | ◐ | ● | ◐ | ● | ◐ | ● | ◐ | ● |
| Codex-style | ● | ● | ● | ● | ● | ● | ● | ◐ | ● | ◐ | ◐ |
| Gemini CLI | ● | ◐ | ◐ | ◐ | ● | ◐ | ● | ◐ | ● | ○ | ● |
| OpenHands | ● | ◐ | ● | ◐ | ◐ | ◐ | ● | ● | ● | ◐ | ● |
| Agent Zero | ● | ● | ● | ● | ● | ● | ● | ◐ | ◐ | ● | ● |
| Strands | ● | ◐ | ● | ◐ | ● | ◐ | ● | ◐ | ● | ○ | ● |
| Mastra | ● | ● | ● | ● | ● | ◐ | ● | ● | ● | ◐ | ● |
| Microsoft Agent Framework | ● | ◐ | ● | ◐ | ● | ◐ | ● | ● | ● | ○ | ● |
| CrewAI | ● | ● | ● | ◐ | ● | ◐ | ◐ | ● | ● | ○ | ● |
| Aider | ◐ | ◐ | — | ○ | ○ | ○ | ◐ | ○ | ● | ○ | ● |
| Cline | ● | ◐ | ◐ | ● | ● | ● | ● | ○ | ◐ | ○ | ● |
| Roo Code | ● | ◐ | ● | ● | ● | ● | ● | ○ | ◐ | ○ | ● |
| SWE-agent | ● | ○ | ◐ | ○ | ◐ | ○ | ● | ● | ● | ○ | ● |
| AutoGPT | ● | ● | ◐ | ◐ | ◐ | ◐ | ◐ | ● | ● | ◐ | ● |
| SuperAGI | ◐ | ◐ | ● | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ● |
| Nanobot | ● | ● | ● | ● | ◐ | ◐ | ◐ | ● | ◐ | ◐ | ● |

This table is a synthesis rather than a vendor-published benchmark.

---

# 28. Best feature by architectural category

## Best long-horizon execution

**Top references:** Fable-style systems, Agent Workspace 2.0, Deep Agents, Hermes, Letta Code, OpenHands.

Why: persistent state, context management, checkpoints, tools and recovery are treated as first-class infrastructure.

## Best persistent learning/memory

**Top references:** Hermes + Letta Code.

Hermes has explicit skill/memory learning and session recall; Letta goes deeper into mutable long-term context, identity and agent self-configuration. [1][6]

## Best multi-agent collaboration

**Top references:** Anthropic-style orchestrator/worker systems, Hermes Bot Mode, CrewAI, Strands, Microsoft Agent Framework, OpenAI Agents SDK, Agent Workspace.

## Best coding architecture

**Top references:** Claude Code, Codex, OpenHands, Aider, Cline, Roo Code, SWE-agent, Gemini CLI.

## Best full-computer environment

**Top references:** Agent Zero + browser/desktop systems + modern computer-use agents.

## Best workflow control

**Top references:** Mastra, CrewAI Flows, Microsoft Agent Framework, LangGraph/Deep Agents.

## Best trajectory/evaluation architecture

**Top references:** SWE-agent, AutoGPT, OpenHands, Deep Agents, Strands eval tooling.

## Best model portability

**Top references:** Hermes, Deep Agents, Strands, CrewAI, Mastra, Cline, Roo Code, OpenHands.

## Best free/open local orientation

**Top references:** Hermes, Gemini CLI, Deep Agents, Strands, Agent Zero, Aider, OpenHands, Nanobot.

---

# 29. Architecture patterns that repeatedly appear

## A. Agent loop

```text
observe
 ↓
reason
 ↓
choose tool/action
 ↓
execute
 ↓
observe result
 ↓
verify
 ↓
continue
```

## B. Planner + workers

```text
Executive
  ↓
Planner
  ↓
Task graph
  ↓
Workers
  ↓
Verifier
```

## C. Brain / hands / governor

```text
Brain = model/reasoning
Hands = tools/environment
Governor = policy/safety
```

## D. Memory outside context

```text
raw events
 ↓
structured memory
 ↓
retrieval
 ↓
context compilation
```

## E. Long-running state

```text
session
 ↓
checkpoint
 ↓
new session
 ↓
restore
 ↓
continue
```

## F. Deterministic + agentic hybrid

```text
Agent reasoning
       ↓
workflow engine
 ├─ deterministic code
 ├─ agent decision
 ├─ deterministic validation
 └─ agent decision
```

## G. Evaluation as a runtime capability

```text
result
 ↓
independent evaluator
 ↓
pass/fail
 ↓
repair
```

---

# 30. What a next-generation universal harness should steal

If building one high-end architecture, the recommended feature inheritance is:

| Source | Feature to inherit |
|---|---|
| Hermes | Learning loop, skills from experience, memory nudges, profiles/bots, cron, channels, remote terminal backends, trajectory generation |
| Agent Workspace | Super-agent packaging, skills + sandbox + memory + subagents |
| Deep Agents | Filesystem-first long-horizon harness, context offloading, isolated subagents, checkpointing |
| Letta | Stateful identity, mutable memory, skill/prompt learning, self-configuration, always-on agents |
| Claude Code | coding loop, plan/execute separation, terminal-first work, agent teams, project rules |
| Fable-style | long-running cross-app work, recovery, visual verification |
| OpenAI Agents SDK | handoffs, agents-as-tools, sessions, guardrails, sandbox agents, tracing |
| Codex | granular sandbox/approval modes, workspace isolation |
| Gemini CLI | search + shell + files + MCP + large context + open-source/free orientation |
| OpenHands | agent server vs automation separation |
| Agent Zero | full desktop environment, DOM-aware browser, document co-working, project isolation |
| Strands | simple composable loop, graph/swarm/workflow patterns, A2A/MCP, observability |
| Mastra | suspend/resume, replay, human approval, supervisor agents, workspaces, scorers |
| Microsoft Agent Framework | enterprise multi-agent workflow patterns and interoperability |
| CrewAI | autonomous crews + deterministic flows |
| Aider | repository map, Git, automatic tests/linting |
| Cline | Plan/Act mode, rules, skills |
| Roo Code | custom modes and role-specific behavior |
| SWE-agent | trajectory records, replay/inspection, benchmark orientation |
| AutoGPT | continuous deployable agents, visual workflows, monitoring/analytics |
| SuperAGI | agent provisioning/concurrency |
| Nanobot | lightweight runtime, session model switching, compaction UX |

---

# 31. Recommended ultimate architecture

```text
                         ┌─────────────────────────┐
                         │      USER / EVENTS      │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │ IDENTITY / SESSION CORE │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │ EXECUTIVE / GOAL ENGINE │
                         └────────────┬────────────┘
                                      │
                  ┌───────────────────┼──────────────────┐
                  ▼                   ▼                  ▼
             WORLD MODEL           MEMORY          CONTEXT ENGINE
                  │                   │                  │
                  └───────────────────┼──────────────────┘
                                      ▼
                              PLANNING ENGINE
                                      │
                                      ▼
                              SUPERVISOR CORE
                                      │
               ┌──────────────────────┼─────────────────────┐
               ▼                      ▼                     ▼
           MODEL FABRIC          TASK GRAPH           AGENT FACTORY
               │                      │                     │
        local/frontier          parallel tasks        specialists
               │                      │                     │
               └──────────────────────┼─────────────────────┘
                                      ▼
                               SKILL FABRIC
                                      │
                                      ▼
                                TOOL FABRIC
                          MCP / APIs / CLI / A2A
                                      │
                                      ▼
                              COMPUTER FABRIC
                    browser / DOM / A11y / GUI / shell
                                      │
                                      ▼
                              POLICY GOVERNOR
                     permissions / risk / credentials
                                      │
                                      ▼
                              SECURE RUNTIME
                   container / VM / WSL / isolated browser
                                      │
                                      ▼
                                  WORLD
                                      │
                                      ▼
                            OBSERVE + VERIFY
                                      │
                               ┌──────┴──────┐
                               ▼             ▼
                             PASS          FAIL
                               │             │
                               │        RECOVERY
                               │             │
                               └──────┬──────┘
                                      ▼
                               CHECKPOINT STATE
                                      │
                                      ▼
                              EVENT / TRAJECTORY
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
                telemetry          evals          failure mining
                    │                 │                  │
                    └─────────────────┼──────────────────┘
                                      ▼
                              EVOLUTION FACTORY
                         hypothesis → candidate → eval
                                      │
                         ┌────────────┴───────────┐
                         ▼                        ▼
                      promote                  rollback
                         │
                         └──────────► better harness
```

---

# 32. Free/open implementation target

For a zero-mandatory-cost stack:

```text
Runtime
  Python + Rust + TypeScript

Models
  Ollama / llama.cpp / vLLM / open-weight models

Agent runtime
  custom event loop + selected concepts from Deep Agents/Strands

Workflow
  LangGraph or custom DAG/event engine

Memory
  PostgreSQL + pgvector / SQLite + filesystem

Search
  SearXNG + direct HTTP + Playwright

Browser
  Chromium + Playwright + CDP

Computer
  OS automation + accessibility APIs + vision fallback

Tools
  MCP + native tools + CLI adapters

Sandbox
  Docker/Podman + WSL + VM isolation

Events
  NATS / Redis Streams

Observability
  OpenTelemetry + local traces

Evaluation
  pytest + Playwright + custom agent benchmark runner

Artifacts
  filesystem/object storage
```

The architecture should remain usable without paid inference. External frontier APIs can be optional accelerators.

---

# 33. The biggest architecture lesson

Do **not** build this:

```text
LLM
 ↓
tools
 ↓
chat UI
```

Build this:

```text
               AGENT OPERATING SYSTEM
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
     BRAIN            MEMORY          WORLD
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                    PLANNER
                       │
                    SUPERVISOR
                       │
                AGENT FABRIC
                       │
             SKILLS + TOOLS + MCP
                       │
                 COMPUTER FABRIC
                       │
                   GOVERNOR
                       │
                SECURE RUNTIME
                       │
                    WORLD
                       │
                 VERIFICATION
                       │
                   TRAJECTORY
                       │
               EVOLUTION FACTORY
                       │
                       └──────► better agent
```

The best architecture is therefore a **hybrid of Hermes + Agent Workspace + Deep Agents + Letta + Claude Code/Fable patterns + OpenAI/Codex runtime controls + Agent Zero computer environment + Strands/Mastra/CrewAI workflow primitives + SWE-agent evaluation**.

---

# Sources

[1] Nous Research, **Hermes Agent README** — learning loop, memory, skills, channels, model/provider flexibility, subagents, remote execution and trajectory generation. https://github.com/NousResearch/hermes-agent

[2] Nous Research, **Hermes Bot Mode documentation** — profiles as bots, isolated role/model/memory/skills, routines, bot-to-bot messaging and group chats. https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/bot-mode.md

[3] Nous Research, **Hermes Scheduled Tasks / MCP / tools documentation** — cron automation, persistent-memory jobs, MCP integration and broad tool registry. https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/cron.md

[4] ByteDance, **Agent Workspace 2.0 README** — super-agent harness, skills, memory, sandboxes, tools, subagents, LangGraph/LangChain. https://github.com/bytedance/agent-workspace

[5] LangChain, **Deep Agents README / documentation** — long-horizon harness, subagents, filesystem, context management, persistence, model agnosticism. https://github.com/langchain-ai/deepagents

[6] Letta, **Letta Code README** — stateful agents, memory, identity, self-improvement, skills, prompts, harness mods and always-on agents. https://github.com/letta-ai/letta-code

[7] OpenAI, **Agents SDK documentation** — agents, tools, handoffs, guardrails, sandbox agents, sessions, tracing, MCP. https://github.com/openai/openai-agents-python

[8] OpenAI, **Codex repository/documentation** — sandbox and approval architecture. https://github.com/openai/codex

[9] OpenHands, **OpenHands repository** — Agent Server/SDK, TypeScript client and automation separation. https://github.com/All-Hands-AI/OpenHands

[10] Google, **Gemini CLI README** — open-source terminal agent, free tier, large context, search, files, shell, web, MCP. https://github.com/google-gemini/gemini-cli

[11] Google, **Gemini CLI v0.59.0 changelog** — SSRF prevention, fail-closed workspace trust, restricted MCP filtering. https://github.com/google-gemini/gemini-cli/blob/main/docs/changelogs/latest.md

[12] Agent Zero, **README** — full Linux desktop, browser DOM annotation, live document co-work, projects, memory, plugins, MCP/A2A and subagents. https://github.com/agent0ai/agent-zero

[13] Strands Agents, **GitHub organization / SDK** — model agnosticism, subagents, graph/swarm/workflows, MCP, A2A, OpenTelemetry and harness SDK. https://github.com/strands-agents

[14] Mastra, **README** — agents, workflows, memory, human-in-loop, suspend/resume, replay. https://github.com/mastra-ai/mastra

[15] Mastra, **AI Agent Framework** — workspaces, supervisor agents, MCP, memory, evals and tracing. https://mastra.ai/ai-agent-framework

[16] Microsoft, **Agent Framework** — production multi-agent workflows, Python/.NET, orchestration and interoperability. https://github.com/microsoft/agent-framework

[17] CrewAI, **README** — Crews, Flows, memory, checkpointing, async execution, MCP/A2A. https://github.com/crewAIInc/crewAI

[18] Aider, **README** — repository map, Git, tests/linting, local/cloud model support, images/web context. https://github.com/Aider-AI/aider

[19] Cline, **Plan & Act documentation** — separation of planning from mutation. https://github.com/cline/cline/blob/main/docs/core-workflows/plan-and-act.mdx

[20] Roo Code, **README** — autonomous coding agent, custom modes, tools, browser and model compatibility. https://github.com/mtkresearch/Roo-Code

[21] SWE-agent, **Trajectories documentation** — structured thought/action/observation trajectory records. https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md

[22] SWE-agent, **Inspector documentation** — CLI and web trajectory inspection/replay. https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/inspector.md

[23] AutoGPT, **README** — continuous agents, workflow builder, deployment controls, schedules, monitoring, analytics and benchmark ecosystem. https://github.com/Significant-Gravitas/AutoGPT

[24] SuperAGI, **README** — concurrent autonomous agents and extensible tools. https://github.com/TransformerOptimus/superagi

[25] nanobot, **README** — lightweight runtime, subagents, model switching, WebUI, compaction and automation. https://github.com/HKUDS/nanobot

[26] Microsoft AutoGen, **README** — maintenance mode and migration direction to Microsoft Agent Framework. https://github.com/microsoft/autogen

[27] SuperAGI, **issues** — active security reports and fixes. https://github.com/TransformerOptimus/SuperAGI/issues

[28] AgentGPT, **GitHub** — archived Jan 28, 2026. https://github.com/reworkd/AgentGPT

---

# Research limitations

This report intentionally does not claim access to private internal architectures of proprietary systems. Public descriptions for products such as Claude Code, Fable and other frontier assistants reveal behavior and selected engineering practices but not their complete model-serving or internal orchestration implementation. Rankings and “best feature” labels are therefore expert synthesis, not vendor-neutral benchmark scores.

For implementation decisions, verify the project's current license, security advisories, release state and API compatibility before importing code into a commercial product.
