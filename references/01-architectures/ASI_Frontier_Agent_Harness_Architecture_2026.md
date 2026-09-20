# ASI-Oriented Frontier Agent Harness Architecture

## Purpose

A reference architecture for a general-purpose autonomous computer agent designed to execute long-horizon digital work across browsers, desktop applications, terminals, files, APIs, development environments, research workflows, data tools, creative software, and multi-agent teams.

This document synthesizes public architecture patterns from OpenAI GPT-6 Astra / Responses computer environments, Anthropic Claude Fable 5.1 / Claude Code / Managed Agents, NVIDIA AVO / OpenShell, and Moonshot Kimi K3, plus current agent-system research and open-source systems such as Alpha and OpenHands.

## Executive conclusion

Do not build the system around a single monolithic agent loop. Build an Agent Operating System consisting of:

1. Executive Control Plane
2. Model Fabric
3. Agent Runtime / Orchestrator
4. Computer-Use Fabric
5. Tool and Skill Fabric
6. Persistent State and Memory Plane
7. Secure Execution Plane
8. Multi-Agent Collaboration Plane
9. Verification / Evaluation Plane
10. Evolution Factory
11. Observability / Replay Plane
12. Human Governance Plane

The architectural invariant is:

> Models decide what should happen; the harness determines how work is decomposed, executed, verified, remembered, recovered, secured, and improved.

---

# 1. What was learned from the frontier systems

## OpenAI Astra

OpenAI's GPT-6 Astra is publicly described as strong in computer use, browsing, software engineering, science, and professional workflows. Astra can interact with applications, perform research, create documents/spreadsheets/presentations, install and test software, troubleshoot visually, and continue complex tasks while preserving context across context windows.

OpenAI's computer-environment architecture is especially important. The published Responses API design combines orchestration, executable shell actions, a persistent hosted runtime, reusable skills, and compaction/context management. This lets a prompt become a workflow that can discover a skill, acquire data, process local state, and create durable artifacts.

Astra's Codex improvements also describe notes that persist between context windows plus searchable earlier context, rather than relying entirely on one compressed summary.

Design lesson for this project:

- Separate model intelligence from execution state.
- Keep a persistent workspace.
- Treat skills as reusable executable capabilities.
- Use multiple context mechanisms instead of one summary.
- Treat computer use as a first-class action modality.
- Add verification and confirmation gates around consequential actions.

## Claude Fable 5.1 / Claude agent stack

Anthropic's public Fable 5.1 material emphasizes jobs lasting hours and spanning many applications, including browser work, Slack/Cowork workflows, unattended managed agents, multi-day coding, research, and visual verification. Its descriptions emphasize planning, tool use, recovery, user updates, tests, and visual checks.

Anthropic's broader agent-engineering work adds several architectural patterns:

- Long-running agents require durable artifacts and structured handoff between sessions.
- Multi-agent work can use a planner/generator/evaluator structure.
- Agent teams can execute in parallel against shared artifacts while being independently supervised.
- Context engineering is more important than simply increasing prompt size.
- Managed agents benefit from separating the "brain" from execution "hands" so the execution environment can evolve independently.
- Claude Code Auto Mode uses a two-layer defense: prompt-injection detection on tool outputs and action classification before high-risk tool calls. Safe actions can proceed without prompts; higher-risk actions are classified.
- Denied actions should preferably return a recoverable result so the agent can choose a safer strategy rather than immediately stopping.

Design lesson:

Build a model-agnostic "brain" interface and a replaceable "hands" runtime. Give the runtime its own policy layer and make long-running state explicit.

## NVIDIA AVO

NVIDIA's August 2026 AVO publication is the clearest public example of harness-level performance amplification for long-horizon autonomy. NVIDIA describes AVO as an architecture with persistent memory, tools, an execution loop, candidate selection/lineage, and supervisor intervention. In the ARC-AGI-3 public-set experiment, the same AVO design with Claude Opus 5 completed all public levels, and NVIDIA reports that AVO used about 12% fewer environment actions than a comparison system using the same model family.

AVO's most important idea is not any single prompt. It is the evolutionary loop:

inspect -> hypothesize -> modify -> execute -> evaluate -> retain/reject -> continue

For long optimization runs, AVO stores previous implementations, evaluation results, profiler/compiler feedback and accumulated understanding. A supervisor detects stagnation and changes strategy.

Design lesson:

The agent should be able to create and evaluate candidate strategies, not just produce a single answer. Your harness should maintain lineage and benchmark evidence for every candidate.

## NVIDIA OpenShell

OpenShell provides a very strong reference for the execution/security plane. It separates a control-plane gateway from a per-sandbox supervisor and unprivileged agent child.

Its documented sandbox uses overlapping controls including:

- filesystem policy
- process privilege reduction
- seccomp
- network namespaces
- network policy proxy
- credential injection
- gateway relay

Design lesson:

Never let model output directly become unrestricted host execution. Put a policy-enforced runtime between the agent and the computer.

## Kimi K3

Kimi K3 is primarily a model architecture rather than a complete harness. Its relevance is therefore on the intelligence/model-fabric side.

Public Kimi documentation describes:

- 2.8T total parameters
- sparse MoE with 16 of 896 experts activated under Stable LatentMoE
- Kimi Delta Attention (KDA)
- Attention Residuals (AttnRes)
- native multimodality
- 1M-token context
- long-horizon coding and knowledge work
- native visual interaction use cases

Design lesson:

The harness should support very large-context frontier models but should not depend on large context as the only memory mechanism. Context windows are compute resources; memory should be externalized and retrieved deliberately.

---

# 2. Target architecture

```text
                         HUMAN / API / EVENT INPUT
                                   |
                                   v
                        +-------------------------+
                        |     EDGE / GATEWAY       |
                        | auth, tenants, sessions  |
                        | quotas, routing, policy  |
                        +------------+-------------+
                                     |
                                     v
                        +--------------------------+
                        | EXECUTIVE CONTROL PLANE  |
                        | goal compiler             |
                        | world model               |
                        | planner                   |
                        | scheduler                 |
                        | risk manager              |
                        | budget manager            |
                        +-----+--------------+-----+
                              |              |
                 +------------+              +----------------+
                 |                                              |
                 v                                              v
       +------------------+                           +------------------+
       |   MODEL FABRIC   |                           |  MEMORY / STATE  |
       | frontier router  |                           | world state      |
       | reasoning       |                           | episodic         |
       | coding          |                           | semantic         |
       | vision          |                           | procedural       |
       | local models    |                           | artifact graph   |
       +---------+--------+                           +--------+---------+
                 |                                             |
                 +------------------+--------------------------+
                                    |
                                    v
                          +--------------------+
                          |   AGENT FABRIC     |
                          | planner agents     |
                          | researchers        |
                          | coders             |
                          | browser agents     |
                          | computer operators |
                          | evaluators         |
                          | red-team agents    |
                          +---------+----------+
                                    |
                     +--------------+--------------+
                     |                             |
                     v                             v
            +------------------+          +------------------+
            | TOOL / SKILL FAB |          | COLLABORATION    |
            | MCP              |          | A2A / task bus   |
            | APIs             |          | handoffs         |
            | skills           |          | shared artifacts |
            +---------+--------+          +---------+--------+
                      |                             |
                      +-------------+---------------+
                                    |
                                    v
                       +----------------------------+
                       | COMPUTER-USE FABRIC        |
                       | browser / DOM / CDP        |
                       | desktop / GUI              |
                       | accessibility tree         |
                       | screenshot / vision        |
                       | keyboard / mouse           |
                       | terminal / shell           |
                       | app-specific adapters      |
                       +-------------+--------------+
                                     |
                                     v
                       +----------------------------+
                       | SECURE EXECUTION PLANE     |
                       | VM / container / sandbox   |
                       | filesystem isolation       |
                       | process policy             |
                       | network egress policy      |
                       | secret broker               |
                       | identity / credentials     |
                       +-------------+--------------+
                                     |
                                     v
                       +----------------------------+
                       | WORLD / WORKSPACE          |
                       | apps • websites • files    |
                       | DBs • cloud • devices      |
                       | local / remote computers   |
                       +-------------+--------------+
                                     |
                                     v
                       +----------------------------+
                       | OBSERVABILITY + EVAL        |
                       | traces • replay • graders  |
                       | tests • screenshots        |
                       | evidence • cost • latency  |
                       +-------------+--------------+
                                     |
                                     v
                       +----------------------------+
                       |     EVOLUTION FACTORY      |
                       | failure mining             |
                       | hypothesis generation      |
                       | candidate harnesses        |
                       | benchmark search           |
                       | security red-team          |
                       | canary / rollback          |
                       +----------------------------+
                                     |
                                     +------> next version
```

---

# 3. The Executive Control Plane

The Executive should not perform every tool call itself. It is the highest-level controller.

Core modules:

### Goal Compiler

Converts a natural-language request into an objective contract:

```yaml
goal:
  objective: ...
  constraints: []
  required_outputs: []
  quality_bar: []
  forbidden_actions: []
  permissions: []
  deadline: ...
  budget: ...
  risk_tier: ...
```

### World Model

Maintain a machine-readable representation of:

- current task state
- active applications
- filesystem state
- browser state
- credentials available
- known entities
- dependencies
- previous actions
- verified facts
- pending decisions
- external side effects

### Planner

Use hierarchical planning:

```text
Goal
 -> phases
   -> milestones
     -> tasks
       -> atomic actions
```

Plans should be adaptive, not rigid. Replanning is triggered by failed assumptions, new evidence, blocked tools, cost changes, or improved strategies.

### Scheduler

Select:

- sequential vs parallel work
- model
- agent role
- tool
- execution environment
- effort level
- budget
- verification depth

### Risk Manager

Calculate action risk before execution.

Recommended dimensions:

```text
reversibility
scope
externality
financial impact
privacy impact
security impact
third-party impact
credential use
production impact
irreversibility
```

---

# 4. The Model Fabric

Never hard-code one model.

Expose a common model interface:

```python
ModelRequest(
    modality,
    task_class,
    context,
    tools,
    reasoning_effort,
    latency_budget,
    cost_budget,
    safety_profile,
)
```

The router chooses among:

- frontier general reasoning model
- coding-specialist model
- vision model
- browser/computer-use model
- local model
- fast cheap model
- critic/evaluator model
- security monitor model
- summarizer/compressor model

Use capability routing instead of brand routing.

Example:

```text
high-stakes architecture -> strongest reasoning model
large codebase navigation -> coding model
GUI task -> computer-use vision model
simple extraction -> cheap model
security review -> separate monitor model
candidate evaluation -> independent evaluator
```

Use ensemble escalation when confidence is low.

---

# 5. Universal Agent Loop

Every agent should run the same fundamental loop:

```text
OBSERVE
  ↓
BUILD STATE
  ↓
SELECT RELEVANT CONTEXT
  ↓
REASON / HYPOTHESIZE
  ↓
PLAN NEXT ACTION
  ↓
POLICY CHECK
  ↓
EXECUTE
  ↓
OBSERVE RESULT
  ↓
VERIFY
  ↓
UPDATE MEMORY
  ↓
DECIDE: continue / retry / replan / delegate / finish
```

Do not allow the agent to declare completion solely because it believes it is finished.

Completion requires evidence.

---

# 6. Computer-Use Fabric

This is the layer required for the requirement "anything that a person can do on a computer."

Do not implement a screenshot-only agent.

Use a modality hierarchy:

```text
Level 1: Direct API
Level 2: CLI / shell
Level 3: Structured application interface
Level 4: DOM / accessibility tree
Level 5: browser automation / CDP
Level 6: visual GUI
Level 7: raw mouse + keyboard
```

Prefer the highest-level reliable interface.

Example:

```text
Change calendar event
 -> calendar API
 -> fallback DOM
 -> fallback GUI
```

This makes the system both faster and more reliable.

### Computer observation

Maintain a unified observation object:

```json
{
  "screen": "screenshot_ref",
  "accessibility_tree": "tree_ref",
  "dom": "dom_ref",
  "active_window": "...",
