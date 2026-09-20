Yes. I searched the current 2026 ecosystem specifically for projects that contribute **AGI/ASI-oriented architecture ideas** rather than just ordinary chat-agent features.

The most important finding is that there is **no single open-source “ASI architecture” today**. The strongest research projects each attack a different missing capability: self-evolution, lifelong memory, computer embodiment, multi-agent society, long-horizon execution, or large-scale control.

## Top projects worth studying

| Rank   | Project                        | Main architectural contribution                                           | Value for your harness |
| ------ | ------------------------------ | ------------------------------------------------------------------------- | ---------------------- |
| **1**  | **A-Evolve**                   | Universal infrastructure for evolving agents across domains/algorithms    | ⭐⭐⭐⭐⭐                  |
| **2**  | **HyperAgents**                | Self-referential agents that rewrite the task agent across generations    | ⭐⭐⭐⭐⭐                  |
| **3**  | **Darwin Gödel Machine (DGM)** | Open-ended self-modification of agent code + benchmark selection          | ⭐⭐⭐⭐⭐                  |
| **4**  | **Continual Harness**          | Reset-free online adaptation of prompts/subagents/skills/memory           | ⭐⭐⭐⭐⭐                  |
| **5**  | **CORAL**                      | Multi-agent evolution, shared knowledge, isolated experiments, islands    | ⭐⭐⭐⭐⭐                  |
| **6**  | **MOSS**                       | Source-level rewriting of deployed agent harnesses                        | ⭐⭐⭐⭐⭐                  |
| **7**  | **Letta / Letta Code**         | Persistent identity, editable memory, experiential agents                 | ⭐⭐⭐⭐⭐                  |
| **8**  | **OpenHands V1**               | Agent + runtime + control-plane separation                                | ⭐⭐⭐⭐⭐                  |
| **9**  | **OpenClaw**                   | Persistent Gateway + runtime + context + skills + sessions + automation   | ⭐⭐⭐⭐⭐                  |
| **10** | **Hermes Agent**               | Skills-as-procedural-memory + delegation + profiles + cron + trajectories | ⭐⭐⭐⭐⭐                  |
| **11** | **Alpha 2.0**               | Super-agent orchestration + sandbox + persistent memory + subagents       | ⭐⭐⭐⭐⭐                  |
| **12** | **Deep Agents**                | Planning + filesystem context + subagents + persistence                   | ⭐⭐⭐⭐⭐                  |
| **13** | **Voyager**                    | Lifelong skill acquisition + automatic curriculum                         | ⭐⭐⭐⭐⭐                  |
| **14** | **Agent Zero**                 | Full computer + GUI + browser DOM + live document work                    | ⭐⭐⭐⭐                   |
| **15** | **CAMEL-AI**                   | Agent societies + multi-agent environments + simulation                   | ⭐⭐⭐⭐                   |
| **16** | **Prime Agent / RLM**          | Persistent REPL + recursive context/subagents + long-running goals        | ⭐⭐⭐⭐                   |
| **17** | **Ruflo**                      | Large specialist-agent catalog + adaptive/hierarchical swarm coordination | ⭐⭐⭐⭐                   |
| **18** | **Microsoft Agent Framework**  | Enterprise multi-agent orchestration + A2A/MCP interoperability           | ⭐⭐⭐⭐                   |
| **19** | **AG2**                        | Event-driven multi-agent runtime and group collaboration                  | ⭐⭐⭐                    |
| **20** | **SWE-agent**                  | Coding-agent trajectory/environment research                              | ⭐⭐⭐                    |
| **21** | **AutoGPT**                    | Continuous agent deployment/workflow automation/monitoring                | ⭐⭐⭐                    |
| **22** | **AgentGPT**                   | Historical autonomous task decomposition                                  | ⭐⭐                     |

The rankings are my architectural judgment, not an official industry ranking.

---

# 1. A-Evolve

This is probably the **most important new project for your self-improving architecture**.

A-Evolve describes itself as universal infrastructure for self-improving agents: give it a base agent and benchmark, and it can evolve the agent using different evolution algorithms. Its current project reports experiments across MCP-Atlas, SWE-bench Verified, Terminal-Bench and SkillsBench. ([GitHub][1])

### Architecture

```text
BASE AGENT
    ↓
EVOLUTION ENGINE
    ↓
GENERATE CANDIDATES
    ↓
RUN BENCHMARKS
    ↓
SCORE
    ↓
SELECT
    ↓
ARCHIVE
    ↓
NEXT GENERATION
```

### Take from it

* universal evolver
* benchmark abstraction
* candidate/archive system
* multiple evolution algorithms
* domain-independent evolution
* zero-manual-harness target

This should become a major subsystem of your **Evolution Factory**.

---

# 2. HyperAgents

The official Meta Research HyperAgents repository describes **self-referential self-improving agents** that can optimize themselves for computable tasks. Its algorithm has a MetaAgent select a task-agent parent, modify it, evaluate it, archive the result and repeat. ([GitHub][2])

```text
            META AGENT
                │
                ▼
          SELECT PARENT
                │
                ▼
          MODIFY AGENT
                │
                ▼
             EVAL
                │
                ▼
             SCORE
                │
                ▼
            ARCHIVE
                │
                └──────► NEXT GENERATION
```

This is a critical idea because the **proposer itself is part of the optimization system**.

---

# 3. Darwin Gödel Machine

DGM takes self-improvement even further.

The research system iteratively modifies its own code, evaluates the result on coding benchmarks, and explores an evolving population/archive of agent variants. ([GitHub][3])

A useful abstraction:

```text
                    AGENT V0
                       │
               self-improvement
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
             V1       V2       V3
              │        │        │
              └────────┼────────┘
                       ▼
                    BENCHMARK
                       │
                       ▼
                    SELECT
                       │
                       ▼
                     V4
```

### What is especially valuable

DGM is not limited to:

```text
prompt improvement
```

It targets:

```text
agent code improvement
```

That is a much more powerful outer loop.

---

# 4. Continual Harness

This is one of the strongest projects for your **online self-improvement** concept.

Continual Harness allows an LLM refiner to modify the current harness during a continuous episode—including:

```text
system prompt
subagents
skills
memory
```

without resetting the task. ([GitHub][4])

Architecture:

```text
LIVE TASK
   │
   ▼
TRAJECTORY WINDOW
   │
   ▼
FAILURE DETECTION
   │
   ▼
REFINER
   │
   ├── prompt edit
   ├── subagent CRUD
   ├── skill CRUD
   └── memory modification
   │
   ▼
CONTINUE SAME EPISODE
```

That is exactly the sort of **continual adaptation** you want.

---

# 5. CORAL

CORAL is particularly interesting because it combines:

```text
multi-agent
+
persistent shared knowledge
+
isolated experiments
+
grading
+
evolution
```

Its current repository describes multi-agent self-evolution for autonomous research and supports multiple coding-agent backends, isolated workspaces, safe evaluation and shared state. It also introduced **multi-island runs**, where groups operate with isolated attempts, notes, skills and heartbeat state and can later migrate between islands. ([GitHub][5])

This is a huge idea for your architecture:

```text
                    EVOLUTION POPULATION
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
      Island A           Island B           Island C
      knowledge A        knowledge B        knowledge C
      skills A           skills B           skills C
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                       COMPARISON
                           │
                           ▼
                        MIGRATION
```

This helps prevent the entire population from converging on one mediocre strategy.

---

# 6. MOSS

MOSS is especially important because it targets a deeper level of self-evolution.

Its repository describes **source-level rewriting of the agent harness itself**—including routing, hooks, state management and dispatch—rather than only modifying prompts or skills. ([GitHub][6])

Conceptually:

```text
Production Agent
      │
      ▼
Auto-Scan
      │
      ▼
Failure Batch
      │
      ▼
Code-Modification Agent
      │
      ▼
Modified Harness
      │
      ▼
Test
      │
      ▼
Deploy / Reject
```

For your architecture, this means:

```text
PROMPT EVOLUTION
        +
SKILL EVOLUTION
        +
MEMORY EVOLUTION
        +
HARNESS CODE EVOLUTION
```

rather than stopping at prompts.

---

# 7. Letta

Letta is probably the most useful reference for **persistent intelligence**.

Letta's current memory research explicitly frames agents that learn from experience around memory models and token-space memory; its production memory benchmark evaluates both memory usage and memory generation. ([Letta][7])

Letta Code's context architecture is particularly interesting:

```text
agent identity
+
persistent memory blocks
+
recall memory
+
context compilation
+
self-edited memory
+
future-self continuity
```

The agent can modify memories that affect later contexts, rather than merely storing chat transcripts. ([GitHub][8])

Architecture:

```text
                 AGENT IDENTITY
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
      core memory   recall        archival
          │            │             │
          └────────────┼─────────────┘
                       ▼
                 CONTEXT COMPILER
                       │
                       ▼
                     MODEL
```

This should influence your **long-term memory layer** heavily.

---

# 8. OpenHands V1

OpenHands gives you another extremely important abstraction:

```text
Harness
+
Orchestrator
+
Control Plane
```

OpenHands itself describes the stack as three pillars: the harness (agentic loop), orchestrator (environments), and control plane (observation/orchestration at scale). ([OpenHands][9])

Its Agent SDK describes the core agent as:

```text
reasoning-action loop
tool orchestration
context management
security validation
```

with an event-driven architecture. ([GitHub][10])

And its design principles emphasize:

```text
stateless by default
one source of truth
clear boundaries
optional isolation
composable components
```

([OpenHands Docs][11])

For your architecture:

```text
AGENT
  ↓
ORCHESTRATOR
  ↓
RUNTIME ENVIRONMENT
  ↓
CONTROL PLANE
```

is a very strong separation.

---

# 9. OpenClaw

OpenClaw is still one of the most complete **persistent agent operating-system** references.

Its current architecture separates:

```text
agent-core
agent runtime
sessions
context engine
skills
tools
harness registry
provider transport
```

and its reusable `agent-core` owns the loop, harness types, messages, compaction, prompts, skills and session-storage contracts. ([GitHub][12])

Take:

```text
Gateway
Session
Context Engine
Agent Core
Harness Registry
Skills
Tools
Nodes
Automation
```

and combine them with the evolution systems above.

---

# 10. Hermes

Hermes is especially good at practical autonomous operation:

```text
persistent memory
skills as procedural memory
subagent delegation
profiles
Bot Mode
cron
background work
checkpoints
Git worktrees
trajectories
multi-provider support
```

Its current architecture documentation explicitly maps agent loop, prompt assembly, providers, tools, sessions, gateway, cron, ACP and trajectory generation. ([GitHub][13])

For your architecture, Hermes is a strong **runtime/workspace foundation**.

---

# 11. Alpha 2.0

Alpha is valuable because it packages many of the pieces into a super-agent:

```text
super-agent
+
sandbox
+
persistent memory
+
subagents
+
MCP
+
skills
+
per-thread isolation
```

Its current backend architecture uses a Gateway API, embedded LangGraph-compatible runtime, `RunManager`, `run_agent()`, `StreamBridge`, sandboxing, memory, persistence and extensible tools. ([GitHub][14])

It is especially useful as a reference for:

```text
productized super-agent architecture
```

rather than just an agent SDK.

---

# 12. Deep Agents

Deep Agents is useful as a **long-horizon agent primitive layer**.

Its architecture focuses on:

```text
planning
filesystem
subagents
middleware
tool surface
state
persistence
```

on top of LangChain/LangGraph. ([GitHub][15])

For your project:

```text
Goal
 ↓
Plan
 ↓
filesystem-backed workspace
 ↓
subagents
 ↓
persistent state
```

is extremely useful.

---

# 13. Voyager

Voyager is older than the others but remains one of the most important AGI-oriented architectural ideas.

It introduced:

```text
automatic curriculum
+
ever-growing skill library
+
environment feedback
+
execution error feedback
+
self-verification
```

for lifelong embodied learning. ([GitHub][16])

The core concept:

```text
explore
 ↓
discover behavior
 ↓
turn behavior into skill
 ↓
store skill
 ↓
reuse skill
 ↓
explore further
```

This is an excellent basis for your **skill acquisition engine**.

---

# 14. Agent Zero

Agent Zero is highly relevant to your goal of:

> “the agent should be able to do anything inside the computer.”

Its current architecture gives agents:

```text
full Linux desktop
browser DOM annotation
terminal
files
desktop apps
live documents
projects
memory
plugins
MCP
A2A
host bridge
subagents
```

([GitHub][17])

The key idea is:

```text
LLM
 ↓
full computer environment
```

rather than:

```text
LLM
 ↓
limited business APIs
```

Its memory documentation also emphasizes that persistent memory requires curation because stale or incorrect memories can cause future failures. ([GitHub][18])

---

# 15. CAMEL-AI

CAMEL is important for **agent societies**.

Its current framework contains:

```text
agents
agent societies
data generation
tools
memory
storage
benchmarks
interpreters
retrievers
runtime
human-in-the-loop
```

and explicitly supports building and studying large multi-agent societies. ([GitHub][19])

Architecture:

```text
              SOCIETY
                 │
      ┌──────────┼──────────┐
      ▼          ▼          ▼
   agent A     agent B    agent C
      │          │          │
      └──────────┼──────────┘
                 ▼
              society
                state
```

This is useful for your **AI-company architecture**.

---

# 16. Prime Agent / RLM

Prime Agent is another very interesting direction.

Its current architecture uses:

```text
daemon
worker
AgentSession
model provider
persistent IPython kernel
session storage
```

and supports persistent goals, autonomous mode, heartbeats, schedules, background sessions, subagents, session trees and RLM-style programmatic context/subagent calls. ([GitHub][20])

The powerful idea is:

```text
LLM
 ↓
persistent REPL
 ↓
state
 ↓
recursive subagents
```

instead of hundreds of isolated JSON tools.

---

# 17. Ruflo

Ruflo is interesting specifically for **specialist swarms**.

Its current documentation lists 60+ agent types and coordination approaches including:

```text
hierarchical coordinator
mesh coordinator
adaptive coordinator
collective-intelligence coordinator
```

as well as coder, reviewer, tester, planner, researcher, security and memory specialists. ([GitHub][21])

Useful concept:

```text
                  SWARM MANAGER
                       │
        ┌──────────────┼─────────────┐
        ▼              ▼             ▼
 hierarchical         mesh         adaptive
```

Don't copy 60 agents literally. Copy the **dynamic topology idea**.

---

# 18. Microsoft Agent Framework

AutoGen itself is now in maintenance mode, with Microsoft directing new projects toward Microsoft Agent Framework. MAF is positioned as the production-oriented successor with multi-agent orchestration, multi-provider support, A2A and MCP interoperability. ([GitHub][22])

That makes MAF a useful reference for:

```text
enterprise agent control
cross-runtime interoperability
A2A
MCP
multi-agent systems
```

AutoGen's older Core architecture remains historically valuable because it implemented event-driven agents, message passing and distributed runtime concepts. ([GitHub][23])

---

# 19. Self-Improving Agent Template

This one is tiny but conceptually important.

The project maintains:

```text
AGENTS.md
+
self-improving-agent skill
```

and turns repeated experience into project knowledge and new skills. ([GitHub][24])

Loop:

```text
WORK
 ↓
REFLECT
 ↓
RECORD
 ↓
NEXT TASK
```

This is the simplest implementation of a compounding intelligence loop.

---

# 20. Meta-Agent

The Canvas Meta-Agent project is another useful reference.

It deliberately separates:

```text
task system
+
meta/proposer system
+
experience archive
```

and warns about validation overfitting and the need for held-out evaluation. ([GitHub][25])

That last point is extremely important for your Evolution Factory:

```text
training/evolution benchmark
≠
final hidden benchmark
```

Otherwise your agent can simply learn the benchmark.

---

# 21. The projects I would consider truly important for your ASI architecture

If you don't have time to study 20+ projects, study these **10 deeply**:

```text
1. A-Evolve
   → universal agent evolution

2. HyperAgents
   → self-referential meta-agent

3. Darwin Gödel Machine
   → source-level recursive improvement

4. Continual Harness
   → online/reset-free adaptation

5. CORAL
   → population/island evolution

6. Letta
   → durable experiential memory

7. OpenHands
   → harness/orchestrator/control plane

8. OpenClaw
   → persistent agent runtime/Gateway

9. Hermes
   → skills + delegation + practical autonomy

10. Voyager
   → lifelong skill acquisition
```

Then layer in:

```text
Alpha
Deep Agents
Agent Zero
CAMEL
Prime Agent
Ruflo
Microsoft Agent Framework
```

---

# 22. The ASI architecture you can synthesize from them

This is the architecture I would build after studying all of them:

```text
                         ┌────────────────────┐
                         │   META-AGENT / R&D  │
                         │                    │
                         │ A-Evolve            │
                         │ HyperAgents         │
                         │ DGM                 │
                         │ CORAL               │
                         └─────────┬──────────┘
                                   │
                            EVOLUTION CONTROL
                                   │
                                   ▼
                         ┌────────────────────┐
                         │    GOVERNOR         │
                         │ policy / safety     │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │     EXECUTIVE       │
                         │ goal / strategy     │
                         └─────────┬──────────┘
                                   │
                  ┌────────────────┼────────────────┐
                  ▼                ▼                ▼
             WORLD MODEL        MEMORY          MODEL FABRIC
                  │                │                │
                  └────────────────┼────────────────┘
                                   ▼
                              PLANNER
                                   │
                                   ▼
                             SUPERVISOR
                                   │
                                   ▼
                         ┌────────────────────┐
                         │    AGENT FABRIC     │
                         │                    │
                         │ researchers         │
                         │ coders              │
                         │ analysts            │
                         │ computer agents     │
                         │ scientists          │
                         │ critics             │
                         └─────────┬──────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
                 SKILLS          TOOLS         SUBAGENTS
                    │              │              │
                    └──────────────┼──────────────┘
                                   ▼
                         COMPUTER / WORLD
                                   │
                                   ▼
                          OBSERVE / VERIFY
                                   │
                                   ▼
                             STATE STORE
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
                  MEMORY        TRAJECTORY     EVIDENCE
                    │              │              │
                    └──────────────┼──────────────┘
                                   ▼
                            LEARNING LOOP
                                   │
                                   ▼
                          EVOLUTION FACTORY
                                   │
                                   └────► next generation
```

---

# 23. What each project contributes to that architecture

```text
OPENCLAW
→ persistent runtime
→ gateway
→ sessions
→ context engine
→ skills
→ harness registry
→ automation

HERMES
→ procedural memory
→ profiles
→ delegation
→ checkpoints
→ trajectories
→ background execution

AGENT_WORKSPACE
→ super-agent
→ sandbox
→ subagents
→ skills
→ persistent memory
→ gateway

DEEP AGENTS
→ planning
→ filesystem context
→ durable state
→ subagent isolation

OPENHANDS
→ harness/orchestrator/control-plane separation
→ event-driven agent
→ security analyzer
→ runtime environments

LETTA
→ persistent identity
→ editable memory
→ experiential continuity
→ memory learning

VOYAGER
→ lifelong skill acquisition
→ automatic curriculum
→ skill composition
→ self-verification

A-EVOLVE
→ universal evolution engine
→ candidate selection
→ algorithmic evolution

HYPERAGENTS
→ recursive meta-agent
→ self-referential optimization

DGM
→ source-level self-modification
→ open-ended agent evolution

CONTINUAL HARNESS
→ online in-place adaptation

CORAL
→ population evolution
→ islands
→ shared knowledge
→ isolated evaluation

MOSS
→ harness source-code evolution

CAMEL
→ agent societies

PRIME/RLM
→ persistent REPL
→ recursive context/subagents

AGENT ZERO
→ full computer embodiment

RUflo
→ dynamic swarm topology
```

---

# 24. One additional project category you should study closely

There is now a recognizable **“self-improving agents” research ecosystem**, not just isolated projects. The maintained Awesome Self-Improving Agents index currently lists 2026 work including:

```text
RoboPhD
Group-Evolving Agents
HyperAgents
CORAL
Adaptive Auto-Harness
MOSS
Recursive Self-Evolving Agents via Held-Out Selection
Continual Harness
Argus
```

([GitHub][26])

That is significant because your project should not treat “self-improvement” as one feature.

It should be an **entire subsystem**.

---

# 25. My final architecture recommendation for you

Use five evolutionary levels:

```text
LEVEL 1
Memory learning

LEVEL 2
Skill learning

LEVEL 3
Strategy/planner learning

LEVEL 4
Harness evolution

LEVEL 5
Meta-harness evolution
```

And five persistent layers:

```text
PERSONAL MEMORY
PROJECT MEMORY
WORLD MODEL
TRAJECTORY MEMORY
EVOLUTION ARCHIVE
```

And five execution layers:

```text
AGENT
→ TOOL
→ COMPUTER
→ SANDBOX
→ WORLD
```

And five intelligence layers:

```text
MODEL
→ AGENT
→ TEAM
→ SUPERVISOR
→ META-AGENT
```

Put those together and the conceptual target is:

```text
               MODEL
                 ↓
               AGENT
                 ↓
              AGENTS
                 ↓
               TEAM
                 ↓
            SUPERVISOR
                 ↓
             EXECUTIVE
                 ↓
            META-AGENT
                 ↓
          EVOLUTION FACTORY
                 ↓
            BETTER HARNESS
                 ↓
             BETTER AGENT
                 ↓
             BETTER TEAM
                 ↓
          BETTER META-AGENT
```

That is the **most interesting AGI/ASI-oriented architecture direction I would recommend studying in September 2026**. The critical caveat is that recursive self-improvement is an active research area and can produce benchmark overfitting, regressions, or unsafe behavior; the current research itself emphasizes held-out evaluation, isolation and controlled promotion. ([GitHub][25])

For your specific project, the strongest combination is **OpenClaw/Hermes for the persistent agent kernel, OpenHands/Alpha/Deep Agents for execution and orchestration, Letta/Voyager for learning and memory, and A-Evolve/HyperAgents/DGM/Continual Harness/CORAL/MOSS for the recursive evolution layer**.

[1]: https://github.com/A-EVO-Lab/a-evolve?utm_source=chatgpt.com "GitHub - A-EVO-Lab/a-evolve: The official repository of \"Position: Agentic Evolution is the Path to Evolving LLMs\". · GitHub"
[2]: https://github.com/facebookresearch/hyperagents?utm_source=chatgpt.com "GitHub - facebookresearch/HyperAgents: Self-referential self-improving agents that can optimize for any computable task · GitHub"
[3]: https://github.com/kew-lab/darwin-godel-machine/blob/main/README.md?utm_source=chatgpt.com "darwin-godel-machine/README.md at main · kew-lab/darwin-godel-machine · GitHub"
[4]: https://github.com/sethkarten/continual-harness?utm_source=chatgpt.com "GitHub - sethkarten/continual-harness: Official repository of the paper: Continual Harness: Online Adaptation for Self-Improving Foundation Agents and PokeAgent Speedrun Track 2 · GitHub"
[5]: https://github.com/Human-Agent-Society/Coral?utm_source=chatgpt.com "GitHub - Human-Agent-Society/CORAL: Open-source autoresearch powered by autonomous coding agents. Run Claude Code, OpenCode, and Codex with grading, shared knowledge, and multi-agent evolution. Accepted at COLM 2026. · GitHub"
[6]: https://github.com/yordanoskassa/moss/blob/main/README.md?utm_source=chatgpt.com "moss/README.md at main · yordanoskassa/moss · GitHub"
[7]: https://www.letta.com/blog/towards-agents-that-learn/?utm_source=chatgpt.com "Memory Models: Towards Agents That Learn | Letta"
[8]: https://github.com/letta-ai/letta-code/blob/main/src/agent/prompts/letta_local_memfs.md?utm_source=chatgpt.com "letta-code/src/agent/prompts/letta_local_memfs.md at main · letta-ai/letta-code · GitHub"
[9]: https://www.openhands.dev/blog/agent-control-plane?utm_source=chatgpt.com "The Software Agent Control Plane | Apr 03, 2026"
[10]: https://github.com/OpenHands/docs/blob/main/sdk/arch/agent.mdx?utm_source=chatgpt.com "docs/sdk/arch/agent.mdx at main · OpenHands/docs · GitHub"
[11]: https://docs.openhands.dev/sdk/arch/design?utm_source=chatgpt.com "Design Principles - OpenHands Docs"
[12]: https://github.com/openclaw/openclaw/blob/main/docs/agent-runtime-architecture.md?utm_source=chatgpt.com "openclaw/docs/agent-runtime-architecture.md at main · openclaw/openclaw · GitHub"
[13]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md?utm_source=chatgpt.com "hermes-agent/website/docs/developer-guide/architecture.md at main · NousResearch/hermes-agent · GitHub"
[14]: https://github.com/bytedance/agent-workspace/blob/main/backend/AGENTS.md?utm_source=chatgpt.com "agent-workspace/backend/AGENTS.md at main · bytedance/agent-workspace · GitHub"
[15]: https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md?utm_source=chatgpt.com "deepagents/libs/ARCHITECTURE.md at main · langchain-ai/deepagents · GitHub"
[16]: https://github.com/MineDojo/Voyager?utm_source=chatgpt.com "GitHub - MineDojo/Voyager: An Open-Ended Embodied Agent with Large Language Models · GitHub"
[17]: https://github.com/agent0ai/agent-zero?utm_source=chatgpt.com "GitHub - agent0ai/agent-zero: Agent Zero AI framework · GitHub"
[18]: https://github.com/agent0ai/agent-zero/blob/main/docs/guides/memory.md?utm_source=chatgpt.com "agent-zero/docs/guides/memory.md at main · agent0ai/agent-zero · GitHub"
[19]: https://github.com/camel-ai/camel?utm_source=chatgpt.com "GitHub - camel-ai/camel: 🐫 CAMEL: The first and the best multi-agent framework. Finding the Scaling Law of Agents. https://www.camel-ai.org · GitHub"
[20]: https://github.com/PrimeIntellect-ai/prime-agent/blob/main/packages/coding-agent/docs/architecture.md?utm_source=chatgpt.com "prime-agent/packages/coding-agent/docs/architecture.md at main · PrimeIntellect-ai/prime-agent · GitHub"
[21]: https://github.com/ruvnet/ruflo/wiki/Agents?utm_source=chatgpt.com "Agents · ruvnet/ruflo Wiki · GitHub"
[22]: https://github.com/microsoft/autogen/blob/main/README.md?utm_source=chatgpt.com "autogen/README.md at main · microsoft/autogen · GitHub"
[23]: https://github.com/microsoft/autogen/blob/main/README.md?plain=1&utm_source=chatgpt.com "autogen/README.md at main · microsoft/autogen · GitHub"
[24]: https://github.com/Grail-Computer/Self-Improving-Agent/blob/main/README.md?utm_source=chatgpt.com "Self-Improving-Agent/README.md at main · Grail-Computer/Self-Improving-Agent · GitHub"
[25]: https://github.com/canvas-org/meta-agent?utm_source=chatgpt.com "GitHub - canvas-org/meta-agent: Continual harness optimization · GitHub"
[26]: https://github.com/selfimproving-agent/awesome-Self-Improving-Agents?utm_source=chatgpt.com "GitHub - selfimproving-agent/Awesome-Self-Improving-Agents: A curated list for Self-Improvement in Foundation Model Based Agentic Systems. · GitHub"
