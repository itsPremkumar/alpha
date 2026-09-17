I researched the current **Hermes Agent architecture and Agent Core** from Nous Research’s public repository and documentation. The strongest lesson is that Hermes is not just an LLM wrapper: it is a **provider-agnostic agent runtime with persistent state, procedural memory/skills, multi-agent delegation, tool orchestration, scheduled execution, isolated profiles, gateway channels, checkpoints, and pluggable memory/context engines**. ([GitHub][1])

# Hermes Agent — high-end architecture to study

```text
                           USER / EVENT
                                │
       ┌────────────────────────┼────────────────────────┐
       ▼                        ▼                        ▼
      CLI                    Gateway                   ACP
       │                 Telegram/Discord/Slack         │
       │                 WhatsApp/Signal/etc.          IDEs
       └────────────────────────┬───────────────────────┘
                                ▼
                    ┌───────────────────────┐
                    │   SESSION ROUTER      │
                    │                       │
                    │ agent/profile         │
                    │ session key           │
                    │ platform isolation    │
                    │ auth                  │
                    └──────────┬────────────┘
                               ▼
                    ┌───────────────────────┐
                    │      AI AGENT         │
                    │      CORE LOOP        │
                    │                       │
                    │ intake                │
                    │ context               │
                    │ inference             │
                    │ tools                 │
                    │ callbacks             │
                    │ retries/fallback      │
                    │ persistence           │
                    └──────────┬────────────┘
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
       PROMPT SYSTEM     CONTEXT ENGINE       PROVIDER
             │                 │                  │
             ▼                 ▼                  ▼
        identity/skills   assemble/compact    18+ providers
        memory/profile    retrieve/maintain    OAuth/keys
        tool guidance                              │
                                                   ▼
                                             MODEL API
                               │
                               ▼
                       TOOL EXECUTION LOOP
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
          TERMINAL           BROWSER           MEMORY
          FILES              VISION             SEARCH
          PROCESS            CDP                SKILLS
             │                 │                  │
             └─────────────────┼──────────────────┘
                               ▼
                        SUBAGENT DELEGATION
                               │
                   ┌───────────┼───────────┐
                   ▼           ▼           ▼
                worker      worker      orchestrator
                   │           │           │
                   └───────────┼───────────┘
                               ▼
                           SUMMARY
                               │
                               ▼
                        PARENT CONTEXT
                               │
                               ▼
                        STATE PERSISTENCE
                               │
                   ┌───────────┼───────────┐
                   ▼           ▼           ▼
                SQLite       Memory       Files
                FTS5         profiles     artifacts
                   │
                   ▼
             TRAJECTORY DATA
                   │
                   ▼
          TRAINING / IMPROVEMENT
```

The official architecture page describes the major subsystems as the agent loop, prompt system, provider resolution, tool system, session persistence, gateway, plugins, cron, ACP and trajectory generation. ([GitHub][1])

# 1. Agent Core

The most important component is the `AIAgent` loop.

Conceptually:

```text
USER INPUT
   ↓
process_input()
   ↓
AIAgent.run_conversation()
   ↓
prompt_builder
   ↓
runtime_provider
   ↓
model API
   ↓
tool_calls?
   ├── no → final response
   └── yes
         ↓
   tool handler
         ↓
      result
         ↓
       model
         ↓
       repeat
```

This loop also owns retries, fallback, compression, callbacks and persistence. ([GitHub][1])

### Best lesson

Keep the agent loop **small and deterministic**.

Do not put all reasoning, memory, scheduling and UI code inside the loop.

---

# 2. Prompt system

Hermes uses a tiered prompt assembly system:

```text
stable
  ↓
context
  ↓
volatile
```

The public architecture documents this as identity/tool guidance/skills, context files, and runtime memory/profile/timestamp information. It also supports Anthropic prefix caching and middle-turn compression. ([GitHub][1])

A strong implementation is:

```text
Prompt Compiler
    │
    ├── SOUL / identity
    ├── user profile
    ├── project context
    ├── current goal
    ├── tools
    ├── relevant skills
    ├── memory
    ├── environment state
    └── runtime metadata
```

This is much better than one gigantic prompt.

---

# 3. Context Engine

Hermes has a pluggable context-engine architecture with lifecycle stages for ingestion, assembly, compaction and after-turn maintenance. ([GitHub][1])

Think:

```text
                 CONTEXT ENGINE
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      INGEST         ASSEMBLE       COMPACT
        │              │              │
        ▼              ▼              ▼
    index/state     retrieve      summarize
        │
        ▼
      AFTER TURN
```

This should be a **separate interface** in your own system.

---

# 4. Provider abstraction

Hermes currently resolves provider/model combinations across many providers and supports credential pools, aliases and runtime selection. The architecture docs describe support for 18+ providers. ([GitHub][1])

So:

```text
                    MODEL FABRIC
                         │
       ┌─────────────────┼────────────────┐
       ▼                 ▼                ▼
   OpenAI             Anthropic        local model
       │                 │                │
       ▼                 ▼                ▼
      API              API             Ollama/etc.
```

The agent should not know which vendor is behind the model.

---

# 5. Tool registry

Hermes has a centralized tool registry with **70+ registered tools across roughly 28 toolsets**, with each tool self-registering and the registry handling schema collection, dispatch, availability checks and error wrapping. ([GitHub][1])

The tool fabric includes:

```text
web
terminal
process
file
vision
image
browser
skills
memory
session search
delegation
cron
MCP
integrations
```

The current user-facing tool docs describe terminal/files, browser, multimodal tools, orchestration, memory/recall and automation as major tool groups. ([GitHub][2])

### Strong architecture

```text
Tool Registry
   │
   ├── schema
   ├── availability
   ├── policy
   ├── execution
   └── error handling
```

---

# 6. Browser + computer use

Hermes exposes both structured browser operations and vision-based browser interaction:

```text
browser_navigate
browser_snapshot
browser_click
browser_type
browser_scroll
browser_vision
browser_console
browser_cdp
...
```

alongside desktop/computer capabilities in appropriate environments. ([GitHub][3])

The important architecture is:

```text
Browser
 ├── structured state
 ├── DOM/CDP
 ├── interaction
 └── vision fallback
```

This gives better reliability than vision-only automation.

---

# 7. Persistent memory

Hermes has deliberately **bounded, curated memory**.

The built-in design stores:

```text
MEMORY.md
USER.md
```

and injects a snapshot at the start of a session. Session history is separately searchable with FTS5 without needing an LLM call. ([GitHub][4])

The separation is:

```text
Persistent Memory
→ critical facts

Session Search
→ historical recall

Context
→ what the model sees now
```

That distinction is excellent.

---

# 8. Memory providers

Hermes supports pluggable external memory providers. The current architecture says a provider can:

```text
prefetch relevant memories
sync conversations
extract memories at session end
mirror built-in writes
provide memory tools
```

with Honcho as one implementation and self-hosting available for that provider. ([GitHub][5])

For your architecture:

```text
                    MEMORY FABRIC
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
      local           semantic           external
    markdown          vector/db          provider
        │                │                 │
        └────────────────┼─────────────────┘
                         ▼
                   context retrieval
```

---

# 9. Skills = procedural memory

This is one of Hermes' most valuable ideas.

Hermes explicitly treats Skills as **procedural memory**: reusable approaches to recurring task types. Skills are loaded on demand through `skills_list` and `skill_view`, while `skill_manage` can create, update and delete them. ([GitHub][6])

So:

```text
Task
 ↓
skill discovery
 ↓
load relevant skill
 ↓
execute
 ↓
learn/improve skill
 ↓
future task
```

This is much more interesting than static prompt templates.

---

# 10. Self-improvement

Hermes describes itself as **self-improving through skills**, where reusable procedures are written from experience. ([GitHub][7])

The deeper architecture you should adopt is:

```text
successful task
       ↓
lesson extraction
       ↓
skill candidate
       ↓
validation
       ↓
skill registry
       ↓
future execution
```

For a truly advanced version, add:

```text
candidate skill
→ benchmark
→ regression
→ security
→ promotion
```

before making it permanent.

---

# 11. Subagent delegation

This is one of Hermes' strongest features.

A `delegate_task` creates a child `AIAgent` with:

```text
fresh context
own task ID
own terminal session
parent's enabled toolsets
blocked child-only tools
focused system prompt
```

Only the child's final summary enters the parent's context. ([GitHub][8])

That is extremely efficient:

```text
PARENT
  │
  ├──── worker A
  │
  ├──── worker B
  │
  └──── worker C
          │
          └── summaries
                ↓
             PARENT
```

The parent does **not** receive every intermediate tool call.

---

# 12. Delegation security

Hermes deliberately blocks certain tools in leaf subagents:

```text
delegate_task
clarify
memory
send_message
cronjob
```

while orchestrator children can retain delegation with a bounded spawn depth. ([GitHub][8])

This is a strong pattern:

```text
Parent permissions
       ↓
Inherited child permissions
       ↓
Additional restrictions
```

rather than:

```text
child gets full root access
```

---

# 13. Hierarchical orchestration

Hermes supports:

```text
leaf
orchestrator
```

roles.

An orchestrator child can itself delegate workers, controlled by `max_spawn_depth`. The default is flat delegation to prevent uncontrolled recursive expansion. ([GitHub][8])

So the architecture can become:

```text
                    MASTER
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       Manager      Manager     Manager
          │           │           │
       workers      workers     workers
```

This is a very good foundation for your planned super-agent.

---

# 14. Mechanical parallelism

Hermes makes an important distinction between:

```text
delegate_task
```

and

```text
execute_code
```

`delegate_task` is for reasoning-heavy tasks; `execute_code` is for mechanical, batched computation where full LLM reasoning would be wasteful. ([GitHub][8])

This should exist in your architecture:

```text
Reasoning problem
→ agent

Mechanical problem
→ deterministic execution
```

That saves both time and inference.

---

# 15. Background execution

Top-level model calls can run in the background and return a handle immediately, then completion arrives later. ([GitHub][8])

This supports:

```text
user
 ↓
start long task
 ↓
handle returned
 ↓
agent continues
 ↓
completion event
 ↓
user notified
```

That is essential for an always-on agent.

---

# 16. Cron architecture

Hermes makes scheduled jobs first-class agent tasks rather than merely shell jobs. Jobs can:

```text
one-shot
recurring
pause
resume
edit
trigger
remove
attach skills
deliver results
run in fresh sessions
run without an LLM
```

The current Cron tool manages these operations directly from agent interaction. ([GitHub][9])

Architecture:

```text
                 SCHEDULER
                     │
            ┌────────┼────────┐
            ▼        ▼        ▼
         one-shot recurring  script
            │        │        │
            └────────┼────────┘
                     ▼
                 fresh session
                     │
                   agent
```

This is more powerful than a normal cron wrapper because a scheduled job can become an actual autonomous workflow.

---

# 17. Profiles

Hermes profiles are essentially independent agent environments.

Each profile gets:

```text
config
API keys
SOUL.md
memory
sessions
skills
cron jobs
state
```

and the official docs explicitly recommend separate profiles rather than sharing a Hermes home across agents. ([GitHub][10])

So:

```text
Profile A
 ├── memory
 ├── tools
 ├── skills
 └── credentials

Profile B
 ├── memory
 ├── tools
 ├── skills
 └── credentials
```

This is excellent isolation for:

```text
personal
coding
research
business
automation
```

---

# 18. Bot architecture

The newer Bot Mode turns profiles into persistent named bots.

Each bot gets its own:

```text
role
model
memory
skills
chat
avatar
routines
```

and local bot backends are independently managed. Bots can also message each other. ([GitHub][11])

Conceptually:

```text
                   BOT ROSTER
                       │
       ┌───────────────┼──────────────┐
       ▼               ▼              ▼
   Research Bot     Coding Bot     Manager Bot
       │               │              │
       └───────────────┼──────────────┘
                       ▼
                  collaboration
```

For your architecture, this is a clean abstraction for **persistent specialist agents**.

---

# 19. Multi-machine messaging

Hermes can connect multiple instances/machines and route Bot messages through a desktop relay. ([GitHub][11])

That suggests:

```text
Machine A
   │
   ▼
Hermes Agent
   │
Gateway/Relay
   │
   ▼
Machine B
   │
   ▼
Hermes Agent
```

This becomes useful for distributed agent teams.

---

# 20. Checkpoints / rollback

Hermes uses a checkpoint mechanism based on a shadow Git store and can automatically snapshot the working directory before destructive file operations. ([GitHub][12])

That gives you:

```text
before file mutation
       ↓
checkpoint
       ↓
agent changes
       ↓
problem?
  ↓
rollback
```

This should absolutely be in a high-end coding harness.

---

# 21. Git worktrees

Hermes also recommends isolated Git worktrees for experiments and has automatic disposable worktree support via `-w`. ([GitHub][13])

For multi-agent coding:

```text
main repo
  │
  ├── agent-A worktree
  ├── agent-B worktree
  └── agent-C worktree
```

Then integrate after verification.

This dramatically reduces agent collision.

---

# 22. Terminal backends

Hermes supports several execution backends including:

```text
local
Docker
SSH
Daytona
Modal
Singularity
Vercel Sandbox
```

according to the current architecture documentation. ([GitHub][1])

This is a very strong abstraction:

```text
Terminal Interface
       │
       ├── local
       ├── container
       ├── SSH
       └── remote sandbox
```

The agent doesn't need to care which backend executes the command.

---

# 23. Security/container model

The current configuration includes container hardening such as:

```text
capability dropping
no-new-privileges
PID limits
tmpfs limits
```

and controlled environment-variable forwarding. ([GitHub][14])

The architecture is:

```text
Agent
 ↓
Terminal abstraction
 ↓
Sandbox
 ↓
restricted process
 ↓
filesystem/network limits
```

For your project, make this a separate **Execution Kernel**.

---

# 24. Tool-result spillover

Hermes also has an important context-protection mechanism for oversized tool outputs.

Large MCP/web results can spill over into files rather than flooding the model context, and the runtime warns when a tool result is incomplete/truncated. ([GitHub][14])

This is an excellent high-end feature:

```text
huge tool result
      ↓
size threshold
      ↓
store externally
      ↓
return compact reference
      ↓
agent fetches relevant portions
```

Never blindly dump giant command/web/database output into the model.

---

# 25. Callbacks

The Agent Core exposes callbacks for:

```text
tool progress
thinking
reasoning
clarification
step completion
streaming
tool generation
status
```

which power CLI, Gateway and ACP interfaces. ([GitHub][15])

The clean architecture is:

```text
Agent Core
    │
    ▼
Event/Callback Bus
    │
 ┌──┼──────┬───────┐
 ▼  ▼      ▼       ▼
CLI UI Gateway ACP telemetry
```

This keeps the core independent of the UI.

---

# 26. ACP integration

Hermes exposes itself as an editor-native agent over stdio/JSON-RPC for VS Code, Zed and JetBrains. ([GitHub][1])

So the core can be consumed by:

```text
CLI
Desktop
Web
Messaging
IDE
API
```

without cloning the agent logic.

That is exactly how your architecture should work.

---

# 27. Trajectories

A very interesting advanced feature is trajectory generation.

Hermes can generate ShareGPT-format trajectories from sessions for training-data generation. ([GitHub][1])

Conceptually:

```text
agent session
     ↓
trajectory recorder
     ↓
tool/action sequence
     ↓
dataset
     ↓
training/evaluation
```

This is extremely valuable for your future self-improvement system.

---

# 28. Learning architecture

Putting these together gives a powerful Hermes learning loop:

```text
             TASK
               │
               ▼
           AGENT RUN
               │
        ┌──────┼───────┐
        ▼      ▼       ▼
      memory  skill   trajectory
        │      │       │
        │      ▼       ▼
        │   reusable   training
        │   procedure  data
        │
        ▼
     future context
```

The official documentation explicitly positions skills as procedural memory and trajectories as training data. ([GitHub][6])

---

# 29. The highest-value Hermes architecture

For your own project, I would extract this:

```text
                         AGENT OS
                            │
                     ┌──────┴───────┐
                     ▼              ▼
                 GATEWAY          ACP/API
                     │
                     ▼
                SESSION ROUTER
                     │
                     ▼
                  AGENT CORE
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   PROMPT          CONTEXT       MODEL
   COMPILER        ENGINE        ROUTER
       │             │             │
       └─────────────┼─────────────┘
                     ▼
                 TOOL FABRIC
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
    terminal       browser        MCP
    files          vision         APIs
       │             │              │
       └─────────────┼──────────────┘
                     ▼
             DELEGATION FABRIC
                     │
          ┌──────────┼─────────┐
          ▼          ▼         ▼
       worker      worker   orchestrator
          │          │         │
          └──────────┼─────────┘
                     ▼
                  SUMMARY
                     │
                     ▼
               VERIFICATION
                     │
                     ▼
                 SESSION DB
                     │
          ┌──────────┼───────────┐
          ▼          ▼           ▼
       memory      skills     trajectory
          │          │           │
          └──────────┼───────────┘
                     ▼
                  LEARNING
```

# 30. What I would add to make it stronger than Hermes

Hermes is an excellent base, but for the **high-end harness you've been designing**, I would put an additional layer above Agent Core:

```text
                    META EXECUTIVE
                         │
                  GOAL COMPILER
                         │
                    WORLD MODEL
                         │
                  STRATEGIC PLANNER
                         │
                    SUPERVISOR
                         │
                  HERMES AGENT CORE
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
    Skills              Tools           Agents
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                COMPUTER / EXECUTION
                         │
                         ▼
                     VERIFIER
                         │
                         ▼
                 TRAJECTORY STORE
                         │
                         ▼
                EVOLUTION FACTORY
```

So **Hermes becomes the execution kernel**, rather than the entire intelligence architecture.

## My ranking of Hermes features for your project

| Priority | Hermes feature                | Value                       |
| -------- | ----------------------------- | --------------------------- |
| **S+**   | Agent Core loop               | Fundamental runtime         |
| **S+**   | Skills as procedural memory   | Learning/capability reuse   |
| **S+**   | Persistent memory             | Long-term continuity        |
| **S+**   | Context engine                | Long-context control        |
| **S+**   | Subagent delegation           | Parallel intelligence       |
| **S+**   | Provider abstraction          | Model independence          |
| **S+**   | Tool registry                 | Universal action interface  |
| **S**    | Profiles                      | Isolated agents             |
| **S**    | Session persistence + lineage | Durable work                |
| **S**    | Checkpoints/rollback          | Safe coding                 |
| **S**    | Browser + terminal            | Computer capability         |
| **S**    | Cron                          | Autonomous operation        |
| **S**    | Gateway                       | Multi-channel agent         |
| **S**    | Callbacks/events              | Live control                |
| **S**    | Trajectories                  | Learning/evaluation data    |
| **A+**   | Bot Mode                      | Persistent specialist fleet |
| **A+**   | Orchestrator children         | Hierarchical teams          |
| **A+**   | Worktrees                     | Multi-agent code isolation  |
| **A**    | ACP                           | IDE integration             |
| **A**    | External memory providers     | Advanced personalization    |

The most important architectural principle from Hermes is:

```text
                 CORE
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
   MODEL        TOOLS        STATE
     │            │            │
     └────────────┼────────────┘
                  ▼
                LOOP
                  │
                  ▼
              PERSIST
```

Keep that core **small, modular and provider-independent**. Then place **Executive, World Model, Supervisor, Verification and Evolution** above it.

That combination would give you an architecture substantially more ambitious than Hermes alone while retaining its strongest practical design ideas. ([GitHub][1])

[1]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md?utm_source=chatgpt.com "hermes-agent/website/docs/developer-guide/architecture.md at main · NousResearch/hermes-agent · GitHub"
[2]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/tools.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/features/tools.md at main · NousResearch/hermes-agent · GitHub"
[3]: https://github.com/NousResearch/hermes-agent/blob/main/toolsets.py?utm_source=chatgpt.com "hermes-agent/toolsets.py at main · NousResearch/hermes-agent · GitHub"
[4]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/features/memory.md at main · NousResearch/hermes-agent · GitHub"
[5]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/features/memory-providers.md at main · NousResearch/hermes-agent · GitHub"
[6]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/tools-reference.md?utm_source=chatgpt.com "hermes-agent/website/docs/reference/tools-reference.md at main · NousResearch/hermes-agent · GitHub"
[7]: https://github.com/NousResearch/Hermes-Agent?utm_source=chatgpt.com "GitHub - NousResearch/hermes-agent: The agent that grows with you · GitHub"
[8]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/features/delegation.md at main · NousResearch/hermes-agent · GitHub"
[9]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/cron.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/features/cron.md at main · NousResearch/hermes-agent · GitHub"
[10]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/profiles.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/profiles.md at main · NousResearch/hermes-agent · GitHub"
[11]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/bot-mode.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/bot-mode.md at main · NousResearch/hermes-agent · GitHub"
[12]: https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/config_defaults.py?utm_source=chatgpt.com "hermes-agent/hermes_cli/config_defaults.py at main · NousResearch/hermes-agent · GitHub"
[13]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/git-worktrees.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/git-worktrees.md at main · NousResearch/hermes-agent · GitHub"
[14]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/configuration.md at main · NousResearch/hermes-agent · GitHub"
[15]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/agent-loop.md?utm_source=chatgpt.com "hermes-agent/website/docs/developer-guide/agent-loop.md at main · NousResearch/hermes-agent · GitHub"
