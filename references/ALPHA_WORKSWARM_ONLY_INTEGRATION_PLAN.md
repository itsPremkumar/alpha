# Alpha — WorkSwarm Feature Integration & Implementation Plan

**Scope:** Alpha AI Agent only

**Reference project:** [`openJiuwen-ai/jiuwenswarm`](https://github.com/openJiuwen-ai/jiuwenswarm)

**Reference product name used by the upstream project:** JiuwenSwarm / WorkSwarm

**Planning basis:** Current upstream repository/documentation and the WorkSwarm v0.2.6 release information available on **2026-09-23**. The upstream repository is Apache License 2.0. Verify the exact license/notice files of any source code copied into Alpha before distribution. 

> This document intentionally excludes OpenOPC and every other external agent framework. The architecture below is derived only from WorkSwarm/JiuwenSwarm capabilities and adapts those ideas to Alpha's existing architecture.

---

## 1. Executive Goal

Turn Alpha from a single autonomous agent into a **persistent, self-improving, skill-driven, multi-agent execution platform** while preserving Alpha's existing strengths:

- Windows-first desktop operation
- long-running / 24x7 execution
- local Ollama and remote OpenAI-compatible models
- autonomous task planning and execution
- tool/MCP support
- GitHub-aware coding workflows
- self-recovery
- system monitoring
- Alpha-specific RSI / recursive self-development
- Electron desktop UI

The WorkSwarm-derived layer should provide the missing orchestration capabilities:

1. **Perpetual Session** — long-lived task continuity with worker/backend dual-loop memory management.
2. **Persistent Goal Management** — keep attempting an objective across multiple turns/runs until complete, blocked, cancelled, or superseded.
3. **Work/Code execution profiles** — separate general work execution from coding execution.
4. **Plan Mode** — plan first, inspect/read first, request approval before side effects.
5. **Swarm / Team Mode** — Leader + specialized workers with parallel and sequential collaboration.
6. **SwarmFlow** — deterministic, inspectable multi-stage agent workflows.
7. **Third-party agent slots** — allow Alpha to delegate a stage to external coding agents when configured.
8. **Skill system** — installable, reusable capability packages with versioning and enable/disable controls.
9. **Skill Retrieval + Skill Graph + Symphony-style orchestration** — discover relevant skills and compose them into a route.
10. **Skill Self-Evolution** — turn validated failures/corrections into persistent improvements.
11. **TTSE/experience-style learning** — distill useful facts/tips from execution trajectories and inject them later.
12. **Auto Harness** — use task evaluation to optimize Alpha's harness/agent process itself.
13. **Trajectory visualization / observability** — make every important execution traceable.
14. **Context compression/offloading** — run long-lived sessions without unlimited context growth.
15. **Scheduled tasks / reminders** — recurring autonomous work.
16. **Slash commands / operational controls** — direct operator control over modes, sessions, memory, skills, workflows, cron, review, and debugging.
17. **Permissions and security guardrails** — `allow / ask / deny`, tool-specific policies, file access restrictions, sensitive-memory filtering.
18. **Dynamic capability loading** — MCP/connectors/extensions become runtime capabilities instead of static startup wiring.
19. **Automatic updates** — preserve Alpha's existing updater while borrowing WorkSwarm's version/config-update UX model.

---

## 2. What WorkSwarm Actually Adds That Alpha Should Implement

The current WorkSwarm repository describes multi-agent collaboration, distributed swarm execution, deterministic SwarmFlow workflows, Skill self-evolution, Skill Hub distribution, Auto Harness, broad model-provider compatibility, and tool-security controls as core capabilities. citehttps://github.com/openJiuwen-ai/jiuwenswarm

The v0.2.6 release specifically adds or highlights:

- RSI with pluggable optimization of Harness and Artifacts
- Perpetual Session
- Goal Management
- Plan Mode
- SwarmFlow 2.0 with parallelism, budgets, human-in-the-loop, and checkpoint resumption
- third-party Agent integration including Codex/Claude Code
- trajectory visualization
- Expert / Plugin / Connector / Skill marketplace work
- dynamic MCP loading
- skill graph / skill index / skill orchestration capabilities
- context and model configuration improvements
- heartbeat / scheduled reminder mechanisms
- stronger frontend controls. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

For Alpha, these should not be implemented as a collection of unrelated UI features. They should form **one execution operating system**.

---

# 3. Target Alpha Architecture

```text
┌───────────────────────────────────────────────────────────────────────┐
│                            ALPHA DESKTOP                               │
│                       Electron + React / Next UI                      │
├───────────────────────────────────────────────────────────────────────┤
│ Chat │ Goals │ Sessions │ Swarm │ Workflows │ Skills │ Memory │ Cron  │
│ Code │ Diff  │ Traces   │ Agents │ Models    │ MCP    │ Review │ Debug│
└───────────────────────────────┬───────────────────────────────────────┘
                                │ IPC / HTTP / WebSocket
┌────────────────────────────────▼───────────────────────────────────────┐
│                         ALPHA CONTROL PLANE                            │
│                                                                       │
│ Gateway / Command Router                                              │
│ Session Manager │ Goal Manager │ Scheduler │ Permission Engine        │
│ Model Router │ Agent Registry │ Skill Registry │ MCP Registry          │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────┐
│                       ALPHA EXECUTION PLANE                            │
│                                                                       │
│ Planner → Policy → Orchestrator → Workers → Tools → Verifier          │
│                 │                        │                             │
│                 ├── Single Agent        ├── Code Worker               │
│                 ├── Team Leader         ├── Research Worker            │
│                 ├── Specialist Workers  ├── Review Worker              │
│                 └── External Agents     └── Human Operator             │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────┐
│                         LEARNING PLANE                                 │
│                                                                       │
│ Trajectory Store → Evaluator → Experience Extractor → Skill Evolution  │
│                           │                                           │
│                           └────────────→ Auto-Harness                 │
└───────────────┬───────────────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────────────┐
│                         PERSISTENCE PLANE                               │
│                                                                       │
│ SQLite │ Files │ Vector Index │ Skill Store │ Artifacts │ Snapshots    │
│ Session Memory │ Task Memory │ Coding Memory │ Experience Bank        │
└───────────────────────────────────────────────────────────────────────┘
```

### Core design rule

**Do not put learning logic inside the raw tool executor.**

Learning must consume execution evidence after the run and produce versioned proposals. Tool execution must remain deterministic, permission-controlled, and auditable.

---

# 4. Alpha WorkSwarm Feature Matrix

| WorkSwarm capability | Alpha implementation | Priority | Notes |
|---|---|---:|---|
| Perpetual Session | `PersistentSessionManager` + worker replacement + memory compaction | P0 | Core 24x7 requirement |
| Goal Management | `GoalManager` state machine | P0 | Required for autonomous persistence |
| Plan Mode | `PlanEngine` + approval gate | P0 | Prevents premature side effects |
| Swarm/Team | `SwarmOrchestrator` + Leader/Worker protocol | P0 | Main multi-agent expansion |
| SwarmFlow | `WorkflowEngine` + versioned workflow IR | P0 | Deterministic orchestration |
| Parallel workflow steps | DAG scheduler | P0 | Avoid unnecessary serial execution |
| Workflow checkpoints | snapshot/restore | P0 | Critical for recovery |
| Token budgets | budget manager per task/team/workflow | P0 | Protect cost and runaway loops |
| Third-party agents | adapter interface | P1 | External Codex/Claude-style worker support |
| Skill system | `SkillRegistry` | P0 | Capability packaging |
| Skill index | semantic/tree retrieval | P1 | Large skill library support |
| Skill graph | relationship graph | P1 | Multi-skill composition |
| Skill orchestration | route/composition engine | P1 | Skill chains |
| Skill self-evolution | `SkillEvolutionEngine` | P0 | One of Alpha's main RSI pillars |
| Experience memory | validated FACT/TIP bank | P0 | Prevent repeated mistakes |
| Auto-Harness | `HarnessEvolutionEngine` | P1 | Improves Alpha process, not model weights |
| Trajectory tracing | event store + timeline UI | P0 | Required for learning and debugging |
| Context compression | rolling summaries + offload | P0 | Required for long sessions |
| Scheduled tasks | persistent scheduler | P0 | 24x7 autonomous operation |
| Slash commands | command registry | P1 | Fast operator control |
| Dynamic MCP | MCP registry + loader | P1 | Restart-free capability loading |
| Tool security | policy engine | P0 | Required before high autonomy |
| Sensitive-memory filter | redaction classifier | P0 | Privacy safety |
| Git/diff/review | Code mode integration | P0 | Important for Alpha coding agent |
| Marketplace | optional Alpha Skill Registry | P2 | Add after local system is stable |
| Distributed swarm | multi-process first, network cluster later | P2 | Do not lead with this on low-RAM hardware |
| Visual trajectory | trace graph/timeline | P1 | Debuggability |
| Heartbeat | health signal + liveness | P0 | Fits Alpha auto-recovery |
| Model profiles | provider/model routing | P0 | Local + remote support |

---

# 5. Perpetual Session

## 5.1 Problem

A normal LLM session eventually becomes unusable because context grows, working context becomes stale, and an individual worker may fail or need a fresh context.

WorkSwarm's Perpetual Session uses a dual-loop idea: a frontend/worker performs current work while a backend agent maintains task history, memory organization, tracking, archiving, and worker-context replacement. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

## 5.2 Alpha design

Implement two logical loops:

```text
Loop A: EXECUTION WORKER
  execute current task
  use tools
  write artifacts
  produce observations
  produce result / blocker / checkpoint

Loop B: SESSION SUPERVISOR
  track worker health
  archive completed turns
  compress context
  maintain goal state
  extract durable memory
  replace worker when necessary
  resume from checkpoint
```

The two loops may initially run as async tasks in the same Python backend process. They do **not** require separate machines.

## 5.3 Session state

```python
class SessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPACTING = "compacting"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
```

```python
@dataclass
class PersistentSession:
    id: str
    title: str
    project_dir: str | None
    mode: str                    # work.normal / work.plan / code.normal / etc.
    worker_id: str | None
    generation: int
    status: SessionStatus
    goal_ids: list[str]
    summary_ref: str | None
    memory_namespace: str
    context_tokens: int
    context_limit: int
    checkpoint_id: str | None
    created_at: datetime
    updated_at: datetime
```

## 5.4 Worker replacement policy

Replace a worker context when one of these occurs:

- context usage reaches configurable threshold (for example 70–85%)
- repeated tool errors indicate a poisoned local context
- session has accumulated a configurable number of completed phases
- model/provider changes
- worker crashes
- explicit `/compact` or `/new-worker`
- task enters a checkpoint-resume stage

Never discard the session. The new worker receives:

1. system constitution
2. active goal
3. compacted session summary
4. current task/step
5. relevant memories
6. validated experiences
7. open blockers
8. recent artifact/diff state
9. checkpoint information
10. required skill instructions

---

# 6. Goal Management

WorkSwarm v0.2.6 adds persistent goal management where a goal can survive repeated attempts and each attempt is evaluated as complete, blocked, or still in progress. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

Implement this as an explicit Alpha object instead of burying the goal in prompts.

```python
class GoalState(str, Enum):
    ACTIVE = "active"
    PROGRESSING = "progressing"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
```

```python
@dataclass
class Goal:
    id: str
    session_id: str
    objective: str
    success_criteria: list[str]
    constraints: list[str]
    state: GoalState
    progress: float
    attempt_count: int
    max_attempts: int | None
    blocker: str | None
    next_action: str | None
    created_at: datetime
    updated_at: datetime
```

## Goal loop

```text
GOAL ACTIVE
   ↓
PLAN
   ↓
EXECUTE
   ↓
VERIFY
   ├── success → COMPLETE
   ├── blocked → BLOCKED / request human input
   └── incomplete → update progress → next attempt
```

### Important Alpha rule

No endless retry loop. Every retry must create evidence explaining:

- what changed
- why the last attempt failed
- what the next strategy is
- what budget remains
- what prevents a repeated identical attempt

---

# 7. Work Mode vs Code Mode

WorkSwarm exposes distinct work/code execution concepts. The Alpha equivalent should be explicit and visible.

## `work.normal`

General research, planning, analysis, writing, communication, automation.

## `work.plan`

Read-only investigation and proposal creation; execution requires approval.

## `code.normal`

Repository inspection, coding, testing, Git operations, artifact generation.

## `code.plan`

Read-only repository investigation and implementation plan generation.

## `team.work.normal`

Multi-agent general work.

## `team.code.normal`

Multi-agent coding swarm.

## `team.work.plan` / `team.code.plan`

Plan-only variants.

The upstream modes documentation describes canonical three-segment modes in this general shape. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Modes.md

---

# 8. Plan Mode

WorkSwarm v0.2.6 adds plan-first execution for single agents and teams; side-effect operations are intercepted during planning and the plan can be explicitly accepted, skipped, or advanced. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

## Alpha implementation

Create a plan artifact:

```json
{
  "plan_id": "plan_123",
  "mode": "code.plan",
  "objective": "Fix authentication bug",
  "assumptions": [],
  "steps": [
    {
      "id": "inspect",
      "type": "research",
      "side_effect": false
    },
    {
      "id": "patch",
      "type": "edit",
      "side_effect": true
    },
    {
      "id": "test",
      "type": "command",
      "side_effect": false
    }
  ],
  "risks": [],
  "verification": [],
  "status": "awaiting_approval"
}
```

### Plan gate

```text
USER REQUEST
   ↓
READ-ONLY ANALYSIS
   ↓
PLAN ARTIFACT
   ↓
USER/ALPHA POLICY GATE
   ├── Execute → side effects enabled
   ├── Modify   → revise plan
   └── Cancel   → preserve plan, stop run
```

For fully autonomous Alpha, introduce a policy setting:

```yaml
plan_mode:
  default: auto_approve_low_risk
  require_approval_for:
    - destructive_filesystem
    - external_publish
    - credentials
    - production_deploy
    - irreversible_git
```

---

# 9. Swarm / Team Architecture

WorkSwarm's Leader dynamically decomposes complex tasks and assigns specialized agents; it can operate in a single machine or cluster model. citehttps://github.com/openJiuwen-ai/jiuwenswarm

Alpha should implement the local version first.

## 9.1 Roles

```text
Alpha Leader
 ├── Research Agent
 ├── Planner Agent
 ├── Coding Agent
 ├── Testing Agent
 ├── Review Agent
 ├── Security Agent
 ├── Documentation Agent
 ├── Browser Agent
 └── External Agent Adapter
```

These are logical profiles, not necessarily separate OS processes.

## 9.2 Dynamic team creation

The Leader should be able to create a temporary team based on a task:

```json
{
  "team": "task_827",
  "leader": "alpha-leader",
  "members": [
    {"role": "researcher", "model": "fast"},
    {"role": "coder", "model": "reasoning"},
    {"role": "reviewer", "model": "reasoning"}
  ],
  "max_parallel": 3,
  "budget": 50000
}
```

## 9.3 Team communication contract

Do not let agents communicate with arbitrary raw strings only. Use structured envelopes:

```python
@dataclass
class AgentMessage:
    message_id: str
    team_id: str
    sender: str
    receiver: str | None
    message_type: Literal[
        "task",
        "result",
        "question",
        "handoff",
        "review",
        "blocker",
        "approval",
        "checkpoint",
    ]
    payload: dict
    parent_task_id: str | None
    created_at: datetime
```

## 9.4 Handoff contract

Every worker result must contain:

- summary
- evidence
- produced artifacts
- files changed
- commands run
- tests run
- unresolved issues
- confidence
- recommended next step

This prevents the Leader from receiving vague narrative responses.

---

# 10. SwarmFlow — Deterministic Workflow Engine

WorkSwarm describes SwarmFlow as deterministic multi-stage orchestration using Python workflow scripts, with phases, model calls, parallel/pipeline execution, HITL, budget control, and run-tree monitoring. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/TUISwarmFlowGuide.md

The Alpha equivalent should be a first-class workflow engine.

## 10.1 Alpha Workflow IR

Do not make the user depend on Python source as the canonical representation. Define a serializable intermediate representation.

```yaml
version: "1"
workflow:
  id: "repo-repair"
  name: "Repository Repair"
  budget:
    tokens: 100000
  nodes:
    - id: research
      type: agent
      role: researcher
    - id: implement
      type: agent
      role: coder
      depends_on: [research]
    - id: test-a
      type: command
      command: "pytest -q"
      depends_on: [implement]
    - id: review
      type: agent
      role: reviewer
      depends_on: [test-a]
    - id: approve
      type: human
      depends_on: [review]
    - id: finalize
      type: agent
      role: release
      depends_on: [approve]
```

## 10.2 Supported node types

- `agent`
- `subagent`
- `command`
- `python`
- `mcp`
- `browser`
- `human`
- `human_session`
- `condition`
- `parallel`
- `join`
- `checkpoint`
- `verify`
- `memory_write`
- `skill`
- `external_agent`

## 10.3 Scheduler behavior

```text
build graph
   ↓
validate graph
   ↓
compute ready nodes
   ↓
run parallel nodes
   ↓
collect outputs
   ↓
checkpoint
   ↓
resolve dependent nodes
   ↓
continue until terminal state
```

## 10.4 Workflow guarantees

The engine must provide:

- stable `workflow_id`
- stable `run_id`
- node IDs
- attempt numbers
- idempotency keys
- checkpoint IDs
- budget accounting
- cancellation
- pause/resume
- retry policy
- timeout policy
- artifact references
- trace events

## 10.5 Checkpointing

At minimum checkpoint:

```text
before expensive node
after successful node
before human gate
after human gate
before external side effect
on recoverable failure
on graceful shutdown
```

---

# 11. Third-Party Agent Integration

The current WorkSwarm release notes describe cluster-mode support for third-party agents such as Codex and Claude Code. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

Alpha should create a generic adapter rather than hard-coding vendor names.

```python
class ExternalAgentAdapter(Protocol):
    id: str
    capabilities: set[str]

    async def start(self, request: AgentRequest) -> AgentRun:
        ...

    async def poll(self, run_id: str) -> AgentEventStream:
        ...

    async def cancel(self, run_id: str) -> None:
        ...

    async def collect(self, run_id: str) -> AgentResult:
        ...
```

Example adapters:

- local shell agent
- Claude Code
- Codex CLI
- OpenHands
- another Alpha instance

The adapter must expose only structured input/output to the orchestration engine.

---

# 12. Skill System

The upstream Skills documentation defines a Skill as a reusable capability package, commonly centered on a `SKILL.md`, optionally with `references/` and `scripts/`, and supports installed/local/online skill sources and enable/disable management. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Skills.md

Alpha should implement the same conceptual model but with Alpha-owned metadata.

## 12.1 Skill layout

```text
skills/
  git-repair/
    SKILL.md
    skill.yaml
    references/
      git-patterns.md
    scripts/
      inspect_repo.py
    tests/
      test_skill.py
    versions/
      1.0.0/
      1.1.0/
    experience/
      experience.jsonl
```

## 12.2 `SKILL.md`

Required content:

```yaml
---
name: git-repair
version: 1.0.0
description: Diagnose and repair Git repository issues
tags: [git, coding, repair]
allowed_tools: [shell, filesystem, git]
risk_level: medium
entrypoints: [repair_repository]
---
```

Then:

- when to use
- prerequisites
- exact procedure
- constraints
- failure handling
- verification
- examples
- known pitfalls

## 12.3 Skill registry

```python
class SkillRegistry:
    async def discover(self, query: str) -> list[SkillCandidate]: ...
    async def install(self, source: SkillSource): ...
    async def enable(self, skill_id: str): ...
    async def disable(self, skill_id: str): ...
    async def update(self, skill_id: str): ...
    async def remove(self, skill_id: str): ...
    async def versions(self, skill_id: str): ...
    async def rollback(self, skill_id: str, version: str): ...
```

---

# 13. Skill Index

WorkSwarm's current configuration describes a Skill Index for building a local installed-skill tree used for step-by-step retrieval. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

Alpha should build two retrieval paths:

### Fast path

Keyword / metadata / tags / tool requirement matching.

### Semantic path

Embedding retrieval over skill descriptions, examples, capabilities, and validated experience.

### Retrieval result

```json
{
  "skill": "git-repair",
  "match_score": 0.91,
  "reasons": [
    "matches git failure",
    "requires repository shell access",
    "previously successful on this project"
  ]
}
```

Do not inject every skill into the prompt. Retrieve only the candidates needed for the current task.

---

# 14. Skill Graph / Symphony-Style Orchestration

WorkSwarm now documents Skill Retrieval and Skill Symphony separately. Retrieval finds candidates; the graph represents relationships that can compose into a usable skill chain. Configuration includes graph building, edge confidence, max depth, and orchestration settings. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

Alpha should represent skill relationships as a directed graph.

```text
web-search
    ↓
research
    ↓
source-validation
    ↓
analysis
    ↓
report-writing
    ↓
review
```

## Edge types

- `can_feed`
- `requires`
- `enhances`
- `conflicts_with`
- `alternative_to`
- `produces`

## Edge metadata

```json
{
  "from": "research",
  "to": "analysis",
  "confidence": 0.87,
  "evidence_count": 42,
  "success_rate": 0.91,
  "last_validated": "2026-09-20T12:00:00Z"
}
```

## Dynamic orchestration

For a request, Alpha should:

1. retrieve candidate skills
2. inspect graph neighborhood
3. generate one or more candidate chains
4. score chains against task constraints
5. select or ask for approval
6. execute chain
7. feed results back into graph-learning signals

Important: do not blindly use historical scores as truth. Scores are evidence, not guarantees.

---

# 15. Skill Self-Evolution

This is one of the most important WorkSwarm capabilities for Alpha.

The upstream Skill Self-Evolution documentation says recurring problems and better practices can become improvement inputs for Skills; saved experience can be loaded later without immediately editing the canonical `SKILL.md`. It also explicitly avoids treating every error or correction as automatically worthy of new experience: the main agent considers evidence and decides whether an improvement is reusable. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SkillSelfEvolution.md

Alpha should preserve exactly that principle.

## 15.1 Evidence sources

- tool failure
- repeated failure pattern
- user correction
- verifier failure
- reviewer feedback
- regression
- successful optimization
- repeated successful workaround
- explicit operator instruction

## 15.2 Evolution pipeline

```text
EXECUTION TRACE
     ↓
EVIDENCE FILTER
     ↓
REUSABILITY CHECK
     ↓
PROPOSED EXPERIENCE
     ↓
SIMULATED / OFFLINE VALIDATION
     ↓
POLICY GATE
     ├── reject
     ├── save as experience
     └── update skill candidate version
                 ↓
             REGRESSION TEST
                 ↓
             PROMOTE / ROLLBACK
```

## 15.3 Never mutate the active Skill blindly

Use immutable versions:

```text
skill:v1
skill:v2-candidate
skill:v2
skill:v3-candidate
```

Promote only after evaluation.

## 15.4 Experience format

```json
{
  "id": "exp_001",
  "skill_id": "git-repair",
  "type": "TIP",
  "statement": "Run repository status and branch checks before attempting a rebase",
  "evidence": ["trace_882", "trace_918"],
  "confidence": 0.92,
  "reuse_count": 7,
  "success_count": 7,
  "failure_count": 0,
  "created_at": "...",
  "expires_at": null
}
```

---

# 16. FACT / TIP Experience Track

WorkSwarm's configuration documents TTSE as an independent capability from Skill-body evolution: trajectory data can induce FACT/TIP experience, inject guidance, and run silent hygiene/cleanup. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

Alpha should implement two classes:

### FACT

Stable information learned from execution or confirmed sources.

Example:

> This repository uses `pnpm` rather than `npm`.

### TIP

A reusable action strategy.

Example:

> Run `pnpm test -- --runInBand` before starting the browser suite.

Do not store:

- raw secrets
- passwords
- access tokens
- personal sensitive data
- unverified hallucinations
- one-off noise
- transient conversation filler

---

# 17. Experience Lifecycle / Dreaming

Implement a background maintenance process:

```text
experience bank
     ↓
deduplicate
     ↓
merge equivalents
     ↓
remove stale items
     ↓
down-rank failures
     ↓
refresh confidence
     ↓
revalidate high-value tips
```

This can run during idle periods so that the active worker remains responsive.

---

# 18. Auto-Harness for Alpha RSI

WorkSwarm's Auto-Harness manages tasks that optimize the harness itself. The current command documentation describes local harness-extension generation and a separate meta-harness path capable of creating repository changes/PRs. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SlashCommands.md

Alpha should turn this into its own **Harness Evolution Engine**.

## What can evolve

- system prompts
- planning templates
- tool selection heuristics
- retry policy
- context compression strategy
- skill routing thresholds
- verifier selection
- task decomposition templates
- workflow patterns
- reviewer prompts
- error-recovery rules
- model routing rules

## What should not be auto-mutated directly

- security policy
- credential handling
- permission defaults
- update trust roots
- signed release verification
- data retention policy
- user-visible destructive actions

## Auto-Harness pipeline

```text
sample completed tasks
       ↓
select difficult / failed / expensive cases
       ↓
run baseline harness
       ↓
propose harness modification
       ↓
run same evaluation set
       ↓
compare baseline vs candidate
       ↓
check safety / regressions / cost
       ↓
store result
       ├── reject
       └── promote candidate
```

## Candidate record

```json
{
  "candidate_id": "h_42",
  "base_version": "alpha-harness-1.7",
  "change": "new repository reconnaissance phase",
  "evaluation": {
    "success_rate": 0.91,
    "baseline_success_rate": 0.84,
    "avg_cost": 1.08,
    "baseline_avg_cost": 1.12
  },
  "regressions": [],
  "status": "candidate"
}
```

Do not promote based on one task. Use a regression suite.

---

# 19. Trajectory / Observability System

WorkSwarm added trajectory visualization and exposes debug/trace capabilities. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

Alpha should record an append-only execution event stream.

## Event schema

```python
@dataclass
class TraceEvent:
    trace_id: str
    run_id: str
    session_id: str
    parent_id: str | None
    timestamp: datetime
    actor: str
    event_type: str
    payload: dict
    duration_ms: int | None
    token_usage: int | None
    cost: float | None
    status: str | None
```

## Event types

- `run.started`
- `run.completed`
- `run.failed`
- `plan.created`
- `plan.approved`
- `goal.progress`
- `agent.spawned`
- `agent.handoff`
- `tool.requested`
- `tool.approved`
- `tool.denied`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `skill.loaded`
- `skill.updated`
- `workflow.started`
- `node.started`
- `node.completed`
- `checkpoint.created`
- `checkpoint.restored`
- `memory.read`
- `memory.written`
- `experience.proposed`
- `experience.promoted`
- `harness.candidate`
- `harness.promoted`
- `human.requested`
- `human.responded`

## UI

Provide:

```text
Run
 ├── Plan
 ├── Leader
 │    ├── Research Agent
 │    ├── Coding Agent
 │    └── Reviewer
 ├── Tools
 ├── Checkpoints
 └── Final Verification
```

Clicking a node shows exact input/output metadata, duration, token usage, tool calls, changed files, and evidence.

---

# 20. Context Compression & Offloading

WorkSwarm's configuration and UI describe context compression, offloading, suspended sessions, and resumed-session prefetching for long-running conversations and constrained hardware. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

For Alpha's low-RAM laptop target, this should be P0.

## Layers

```text
L0 — active prompt
L1 — recent turns
L2 — current task summary
L3 — session summary
L4 — long-term memory
L5 — archived raw trace
```

When context exceeds threshold:

1. preserve critical facts
2. summarize old dialogue
3. preserve unresolved tasks
4. preserve active goal state
5. persist artifacts
6. save checkpoint
7. offload old trace
8. create a compact worker context

Never summarize away:

- pending approval
- active file modifications
- test failures
- exact commands that must be rerun
- secrets (which should be omitted anyway)
- checkpoint IDs

---

# 21. Scheduled Tasks

WorkSwarm has a persistent scheduled-task/cron capability; the project documentation includes scheduling, inspection, cancellation, and execution logging. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SlashCommands.md

Alpha should use an internal scheduler rather than creating uncontrolled OS tasks for every conversation-created schedule.

## Job model

```python
@dataclass
class ScheduledJob:
    id: str
    name: str
    schedule: str
    timezone: str
    prompt: str
    mode: str
    model_profile: str | None
    skill_ids: list[str]
    enabled: bool
    next_run: datetime
    max_concurrency: int
    timeout_seconds: int
    retry_policy: dict
```

Use a persistent scheduler process with:

- startup recovery
- missed-run policy
- concurrency lock
- retry policy
- execution history
- logs
- notification hooks
- disable/cancel/resume

---

# 22. Heartbeat

WorkSwarm's recent release history discusses heartbeat/health mechanisms and scheduled reminders. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

For Alpha, combine this with the existing self-recovery architecture.

### Heartbeat checks

Every interval:

- backend process alive
- worker state valid
- event loop responsive
- scheduler responsive
- SQLite accessible
- filesystem workspace accessible
- model provider reachable
- MCP server health
- queue depth
- memory/CPU status
- disk free space

### Recovery examples

```text
worker dead          → restore from checkpoint
MCP unavailable      → reconnect / quarantine MCP
model timeout        → retry / provider route change according to policy
scheduler stuck      → restart scheduler task
DB locked            → backoff / integrity check
context corrupted    → restore last session snapshot
```

---

# 23. Dynamic MCP Loading

WorkSwarm's v0.2.6 release describes unified dynamic loading for remote, stdio, and CLI MCP services, with lifecycle operations including connect/disconnect/enable/disable/trust, and runtime injection of tools/skills without restarting. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

Alpha should build:

```python
class MCPRegistry:
    async def register(self, config): ...
    async def connect(self, server_id): ...
    async def disconnect(self, server_id): ...
    async def enable(self, server_id): ...
    async def disable(self, server_id): ...
    async def trust(self, server_id): ...
    async def refresh_tools(self, server_id): ...
```

### MCP trust model

```text
unknown → discovered → inspected → trusted → enabled
                         │
                         └── rejected/quarantined
```

No new external tool becomes unrestricted merely because an MCP server connected.

---

# 24. Tool Permissions & Security Guardrails

WorkSwarm's current configuration documents a permission engine that resolves a tool invocation to `allow`, `ask`, or `deny`, with tool-level rules, patterns, and severity. It also documents sensitive-memory filtering. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

Alpha should copy this concept directly.

## Policy model

```yaml
permissions:
  enabled: true
  mode: normal
  defaults:
    "*": allow
  tools:
    bash: ask
    powershell: ask
    write_file: ask
    git_push: ask
    delete_file: ask
    external_http_post: ask
  rules:
    - id: safe-read
      tools: [bash, powershell]
      pattern: "git status*"
      severity: LOW
      action: allow
    - id: destructive-shell
      tools: [bash, powershell]
      pattern: "rm *"
      severity: HIGH
      action: ask
```

## Alpha security tiers

`LOW` → automatic under normal policy

`MEDIUM` → automatic for trusted workspace, otherwise ask

`HIGH` → ask

`CRITICAL` → deny unless explicit privileged override

### Critical actions

- credential extraction
- disabling security controls
- unrestricted system-wide deletion
- unknown executable download + execution
- unsigned plugin installation
- production deployment
- persistent external side effect without policy allowance

---

# 25. Sensitive Memory Filtering

Before writing to long-term memory, pass candidate memory through a filter.

Remove or redact:

- API keys
- passwords
- session cookies
- access tokens
- private keys
- banking information
- identity numbers
- secrets in environment files
- other configured sensitive patterns

Then store only the minimum useful fact.

The upstream configuration explicitly supports a forbidden-memory definition mechanism for this purpose. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md

---

# 26. Git / Code Workflow

WorkSwarm's current product supports Code mode, project workspaces, Git monitoring, change statistics, per-round diffs, undo/redo, commit/push controls, and branch-diff review. Its current docs also expose `/diff`, `/review`, and `/security-review` commands. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SlashCommands.md

Alpha should integrate these as a coherent coding lifecycle.

```text
INIT
 ↓
DISCOVER REPO
 ↓
PLAN
 ↓
PATCH
 ↓
TEST
 ↓
DIFF
 ↓
CODE REVIEW
 ↓
SECURITY REVIEW
 ↓
COMMIT
 ↓
PUSH / PR (policy-gated)
```

Every coding run must record:

- repository
- branch
- base commit
- files changed
- diff snapshot
- tests run
- test results
- review result
- security review result
- final commit

---

# 27. Slash Command Layer

WorkSwarm exposes an extensive command surface for plan, sessions, modes, skills, models, MCP, diff, memory, cron, review, security review, Auto-Harness, SwarmFlow, and debug operations. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SlashCommands.md

Alpha should implement a modular command registry rather than a giant parser.

Recommended commands:

```text
/plan
/mode
/session
/resume
/new
/compact
/goal
/status
/swarm
/swarmflows
/skills
/skill
/memory
/mcp
/model
/diff
/review
/security-review
/cron
/debug
/evolve
/auto-harness
/trace
/checkpoint
/stop
/pause
/continue
```

Example:

```text
/goal "Keep fixing alpha repository until tests pass"
/swarm on
/swarmflows
/evolve git-repair
/compact
/trace run_123
```

---

# 28. Agent Profiles / Experts

WorkSwarm exposes custom agents/personas and a product area for experts. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Page-Overview.md

Alpha should store profiles separately from execution state.

```yaml
id: alpha-code-reviewer
name: Code Reviewer
role: reviewer
system_prompt: |
  Review changes for correctness, security and maintainability.
skills:
  - code-review
  - security-review
models:
  preferred: reasoning
  fallback: fast
permissions:
  bash: ask
  write_file: deny
```

Profiles should be reusable across sessions and swarm runs.

---

# 29. Model Routing

WorkSwarm supports multiple providers, OpenAI-compatible APIs, and local models. citehttps://github.com/openJiuwen-ai/jiuwenswarm

Alpha should make model routing policy-based.

```yaml
models:
  profiles:
    fast:
      provider: ollama
      model: local-fast
    reasoning:
      provider: openrouter
      model: chosen-reasoning-model
    vision:
      provider: ollama
      model: vision-model
```

The router should choose by:

- task type
- context size
- required tool capability
- reasoning requirement
- latency target
- cost budget
- local/remote policy
- privacy requirement

No silent downgrade that materially changes task behavior. Emit a trace event whenever a provider/model changes.

---

# 30. Budget Management

SwarmFlow in WorkSwarm supports team-level token budgets. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/TUISwarmFlowGuide.md

Alpha should support hierarchical budgets:

```text
Global monthly/session budget
    ↓
Goal budget
    ↓
Workflow budget
    ↓
Node budget
    ↓
Agent budget
```

Budget manager tracks:

- input tokens
- output tokens
- provider cost
- execution time
- tool cost
- external-agent cost

When budget is low:

1. compact context
2. use lower-cost model only if policy allows
3. reduce parallelism
4. postpone nonessential optimization
5. ask user if an explicit approval is required

Never continue infinite retries after budget exhaustion.

---

# 31. Workflow Monitoring UI

The WorkSwarm TUI includes a run-tree viewer for workflow runs. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/TUISwarmFlowGuide.md

Alpha's Electron UI should show the same concept visually.

## Workflow page

```text
Repository Repair                         RUNNING  12m

✓ Research                           1m 20s
✓ Dependency Analysis               0m 42s
● Implementation                    4m 11s
  ├─ Coding Worker                  ✓
  ├─ Test Worker                    ●
  └─ Security Worker                queued
○ Review
○ Human Approval
○ Finalize
```

Each node gets:

- status
- start/end time
- agent/model
- tokens
- logs
- inputs
- outputs
- artifacts
- checkpoint
- retries
- error details

---

# 32. Data Model

Use SQLite for Alpha's local control plane initially.

## Tables

```text
sessions
goals
goal_attempts
agents
agent_runs
agent_messages
workflows
workflow_nodes
workflow_runs
workflow_checkpoints
skills
skill_versions
skill_dependencies
skill_experience
skill_graph_edges
skill_index_entries
traces
trace_events
memories
memory_embeddings
scheduled_jobs
job_runs
mcp_servers
mcp_tools
model_profiles
permission_rules
approval_requests
artifacts
repository_snapshots
harness_candidates
harness_evaluations
system_health
```

## Important indexes

```sql
CREATE INDEX idx_trace_run ON trace_events(run_id, timestamp);
CREATE INDEX idx_goal_session ON goals(session_id, state);
CREATE INDEX idx_workflow_run ON workflow_nodes(workflow_run_id, state);
CREATE INDEX idx_skill_version ON skill_versions(skill_id, version);
CREATE INDEX idx_job_next_run ON scheduled_jobs(enabled, next_run);
```

---

# 33. Directory Structure

Recommended Alpha structure:

```text
alpha/
├── apps/
│   └── desktop/
├── packages/
│   ├── core/
│   ├── runtime/
│   ├── orchestrator/
│   ├── workflow-engine/
│   ├── session-engine/
│   ├── goal-engine/
│   ├── agent-registry/
│   ├── skill-engine/
│   ├── skill-retrieval/
│   ├── skill-graph/
│   ├── memory-engine/
│   ├── experience-engine/
│   ├── evolution-engine/
│   ├── harness-engine/
│   ├── trace-engine/
│   ├── scheduler/
│   ├── permission-engine/
│   ├── mcp-registry/
│   ├── model-router/
│   ├── external-agent-adapters/
│   └── shared/
├── skills/
├── workflows/
├── migrations/
├── tests/
└── docs/
```

If Alpha currently uses Python heavily, the execution/control plane can remain Python. Electron should act as UI/control surface, not own the orchestration state.

---

# 34. Core Interfaces

## Session

```python
class SessionService(Protocol):
    async def create(self, req: CreateSessionRequest) -> Session: ...
    async def resume(self, session_id: str) -> Session: ...
    async def compact(self, session_id: str) -> CompressionResult: ...
    async def replace_worker(self, session_id: str) -> WorkerReplacement: ...
    async def checkpoint(self, session_id: str) -> Checkpoint: ...
```

## Goal

```python
class GoalService(Protocol):
    async def create(self, req: GoalRequest) -> Goal: ...
    async def attempt(self, goal_id: str) -> GoalAttempt: ...
    async def verify(self, goal_id: str) -> GoalVerification: ...
    async def cancel(self, goal_id: str) -> None: ...
```

## Skill evolution

```python
class SkillEvolutionService(Protocol):
    async def inspect(self, skill_id: str, evidence: list[TraceEvent]) -> EvolutionProposal: ...
    async def evaluate(self, proposal_id: str) -> EvaluationResult: ...
    async def promote(self, proposal_id: str) -> SkillVersion: ...
    async def rollback(self, skill_id: str, version: str) -> None: ...
```

## Harness evolution

```python
class HarnessEvolutionService(Protocol):
    async def propose(self, objective: str) -> HarnessCandidate: ...
    async def evaluate(self, candidate_id: str, suite_id: str) -> HarnessEvaluation: ...
    async def promote(self, candidate_id: str) -> None: ...
    async def rollback(self, version: str) -> None: ...
```

---

# 35. Workflow Execution Pseudocode

```python
async def execute_workflow(run: WorkflowRun):
    validate_workflow(run.definition)
    checkpoint = await checkpoint_store.create(run, reason="workflow-start")

    while not run.is_terminal():
        ready = scheduler.ready_nodes(run)

        if not ready:
            if run.waiting_for_human():
                await event_bus.emit("human.requested", run.id)
                return
            if run.has_unresolved_dependency():
                raise WorkflowDeadlock(run.id)

        results = await scheduler.run_parallel(ready, run.budget)

        for result in results:
            await trace.store(result.trace_events)
            await artifact_store.persist(result.artifacts)
            scheduler.apply_result(run, result)

            if result.failed:
                policy = retry_policy.resolve(result)
                if policy.retry:
                    scheduler.schedule_retry(result, policy)
                else:
                    run.fail(result.error)
                    await checkpoint_store.create(run, reason="terminal-failure")
                    return

        await checkpoint_store.create(run, reason="phase-boundary")

    await verifier.verify_workflow(run)
    await goal_service.update_from_run(run)
```

---

# 36. Self-Evolution Pseudocode

```python
async def evolve_skill(skill_id: str, trace_ids: list[str]):
    traces = await trace_store.load(trace_ids)

    evidence = analyze_evidence(traces)
    if not evidence.is_reusable:
        return EvolutionDecision.reject("not reusable")

    proposal = await evolution_planner.create(skill_id, evidence)

    candidate = await skill_store.create_candidate(
        base_version=proposal.base_version,
        body=proposal.updated_skill_body,
        experience=proposal.experience,
    )

    result = await evaluator.run_skill_suite(candidate)

    if not result.passes:
        await skill_store.reject(candidate.id, reason=result.reason)
        return result

    await policy.require_promotion_approval(candidate, result)
    return result
```

---

# 37. Auto-Harness Evaluation Suite

Create permanent benchmark categories for Alpha.

## Category A — Planning

- requirement extraction
- ambiguity detection
- decomposition quality
- plan correctness

## Category B — Coding

- bug fixes
- feature implementation
- test generation
- refactoring
- regression avoidance

## Category C — Research

- source quality
- citation completeness
- synthesis
- contradiction handling

## Category D — Agent coordination

- correct delegation
- useful parallelism
- handoff completeness
- reviewer independence

## Category E — Reliability

- tool failures
- provider timeout
- process restart
- checkpoint recovery
- context compaction

## Category F — Security

- destructive-command interception
- secret filtering
- unknown skill rejection
- MCP trust enforcement

Every harness change must be tested across several categories rather than only the triggering task.

---

# 38. Skill Evolution Evaluation Suite

Every skill should have a small task set.

```text
skills/git-repair/tests/
  basic-status.json
  detached-head.json
  merge-conflict.json
  broken-remote.json
  permissions.json
```

A new Skill version must:

- pass critical cases
- not regress existing cases
- keep tool permissions within limits
- avoid increasing average cost excessively
- preserve expected output structure

---

# 39. Failure and Recovery Model

Every WorkSwarm-derived feature must integrate with Alpha's existing self-recovery engine.

## Failure categories

```text
MODEL_FAILURE
TOOL_FAILURE
WORKER_FAILURE
WORKFLOW_FAILURE
MEMORY_FAILURE
SKILL_FAILURE
PROVIDER_FAILURE
MCP_FAILURE
SCHEDULER_FAILURE
PERMISSION_BLOCK
HUMAN_WAIT
BUDGET_EXHAUSTED
CORRUPTED_STATE
```

## Recovery hierarchy

```text
retry exact step
   ↓
retry with corrected parameters
   ↓
replace worker
   ↓
switch model/provider according to policy
   ↓
resume checkpoint
   ↓
re-plan
   ↓
ask human
   ↓
mark blocked
```

Never silently switch to a fundamentally different strategy while pretending the original task succeeded.

---

# 40. Distributed Mode — Later Phase

WorkSwarm supports single-machine and cluster deployment. citehttps://github.com/openJiuwen-ai/jiuwenswarm

Alpha should **not** start with a networked cluster because the initial target includes an 8 GB RAM Windows laptop.

Phase-1 local architecture:

```text
one Alpha backend
multiple logical agents
async concurrency
isolated workspaces
```

Later:

```text
Alpha Gateway
   ├── Worker Node A
   ├── Worker Node B
   └── Worker Node C
```

Only introduce network distribution after local workflow semantics are stable.

---

# 41. Marketplace / Skill Sharing — Later Phase

WorkSwarm's Skill system supports built-in/local and online sources, and current releases describe skill marketplace capabilities including searching, installation, versioning, publishing, and updates. citehttps://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Skills.md citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

Alpha should first build a local registry.

Then add:

```text
Search
 ↓
Inspect manifest
 ↓
Trust check
 ↓
Install sandbox
 ↓
Static scan
 ↓
Enable
 ↓
Observe usage
 ↓
Update
 ↓
Rollback
```

Marketplace installation must never equal automatic trust.

---

# 42. Update System

WorkSwarm's recent releases centralize update status, version checking, download progress, and install/restart controls. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases

For Alpha:

```text
startup
 ↓
check update manifest
 ↓
verify signature/checksum
 ↓
compare channels
 ↓
show update status
 ↓
download in background
 ↓
stage update
 ↓
health-check current install
 ↓
restart into new version
 ↓
verify boot
 ↓
rollback on failed health check
```

For autonomous mode, updates must be policy-controlled:

```yaml
updates:
  channel: stable
  auto_check: true
  auto_download: true
  auto_install: true
  require_signature: true
  rollback_on_failed_boot: true
```

Do not let RSI rewrite Alpha's installed binary directly. RSI produces candidate source/config changes; the update/release pipeline remains a separate trusted mechanism.

---

# 43. UI Screens to Add to Alpha

## Sessions

- active
- paused
- blocked
- completed
- archived
- worker generation
- context usage

## Goals

- objective
- state
- progress
- attempts
- blockers
- next action

## Swarm

- Leader
- workers
- roles
- status
- messages
- budget

## Workflows

- graph
- run tree
- node states
- checkpoint history
- execution logs

## Skills

- installed
- disabled
- versions
- experience
- graph
- index
- trust
- update

## Memory

- session memory
- task memory
- coding memory
- experience
- filters

## Trace

- event timeline
- tool calls
- agent handoffs
- token/cost usage
- error chain

## Auto-Harness

- candidates
- benchmark results
- baseline vs candidate
- regression failures
- promote/rollback

---

# 44. Alpha Configuration

Suggested single configuration model:

```yaml
alpha:
  version: 1

execution:
  default_mode: work.normal
  max_parallel_agents: 3
  max_depth: 12
  autonomous: true

session:
  perpetual: true
  compact_threshold: 0.78
  max_worker_generation: 100
  checkpoint_every_node: true

planning:
  enabled: true
  approval_policy: risk_based

swarm:
  enabled: true
  max_agents: 6
  default_leader: alpha-leader

workflow:
  enabled: true
  max_parallel_nodes: 3
  default_budget_tokens: 50000
  checkpointing: true

skills:
  enabled: true
  evolution: true
  retrieval: true
  graph: true
  auto_save_experience: true
  auto_promote_skill_versions: false

experience:
  enabled: true
  inject: true
  cleanup: true

harness_evolution:
  enabled: true
  auto_promote: false
  evaluation_suite: alpha-core

memory:
  compression: true
  sensitive_filter: true

security:
  permissions_enabled: true
  default_policy: normal

scheduler:
  enabled: true
  max_concurrent_jobs: 2

mcp:
  dynamic_loading: true
  trust_required: true

updates:
  auto_check: true
  auto_download: true
  auto_install: true
  rollback_on_failed_boot: true
```

---

# 45. API Surface

Suggested endpoints:

```text
GET    /api/sessions
POST   /api/sessions
POST   /api/sessions/:id/resume
POST   /api/sessions/:id/compact
POST   /api/sessions/:id/checkpoint

GET    /api/goals
POST   /api/goals
POST   /api/goals/:id/attempt
POST   /api/goals/:id/cancel

GET    /api/swarm/runs
POST   /api/swarm/runs
GET    /api/swarm/runs/:id
POST   /api/swarm/runs/:id/pause
POST   /api/swarm/runs/:id/resume

GET    /api/workflows
POST   /api/workflows
POST   /api/workflows/:id/run
GET    /api/workflow-runs/:id/tree
POST   /api/workflow-runs/:id/cancel
POST   /api/workflow-runs/:id/resume

GET    /api/skills
POST   /api/skills/install
POST   /api/skills/:id/evolve
POST   /api/skills/:id/update
POST   /api/skills/:id/rollback

GET    /api/memory/search
POST   /api/memory/compact

GET    /api/traces/:run_id
GET    /api/traces/:run_id/events

GET    /api/harness/candidates
POST   /api/harness/propose
POST   /api/harness/:id/evaluate
POST   /api/harness/:id/promote
POST   /api/harness/:id/rollback

GET    /api/cron
POST   /api/cron
PATCH  /api/cron/:id
DELETE /api/cron/:id

GET    /api/mcp
POST   /api/mcp
POST   /api/mcp/:id/trust
POST   /api/mcp/:id/enable
POST   /api/mcp/:id/disable
```

Use WebSocket/SSE for live trace events and workflow state changes.

---

# 46. Implementation Order

## Phase 0 — Foundation

Implement:

- SQLite schema
- event bus
- trace model
- configuration
- policy engine
- checkpoint store

**Exit criteria:** all core events persist and replay correctly.

## Phase 1 — Perpetual Session + Goal

Implement:

- persistent sessions
- worker generation
- compaction
- goal state machine
- recovery

**Exit criteria:** Alpha survives worker replacement without losing goal state.

## Phase 2 — Plan Mode + Code Mode

Implement:

- work/code profiles
- plan artifacts
- approval gates
- Git snapshots/diff integration

**Exit criteria:** planning cannot accidentally perform blocked side effects.

## Phase 3 — Swarm

Implement:

- Leader
- dynamic workers
- structured handoffs
- parallel execution
- budget control

**Exit criteria:** a complex task can be decomposed and completed by multiple workers with traceable handoffs.

## Phase 4 — SwarmFlow

Implement:

- workflow IR
- DAG scheduler
- node types
- retries
- checkpoints
- pause/resume
- HITL
- run-tree UI

**Exit criteria:** workflow state can be persisted, interrupted, restored, and completed deterministically.

## Phase 5 — Skills

Implement:

- SKILL.md packages
- registry
- enable/disable
- versions
- local installation
- retrieval

**Exit criteria:** Alpha can discover and load only the skills relevant to a task.

## Phase 6 — Self-Evolution

Implement:

- trace evidence extraction
- experience bank
- Skill evolution proposal
- evaluation suite
- version promotion
- rollback

**Exit criteria:** Alpha can learn a reusable lesson from repeated failures without blindly modifying the active Skill.

## Phase 7 — Skill Graph / Symphony

Implement:

- graph construction
- edge confidence
- candidate chains
- route selection
- route evaluation

**Exit criteria:** multi-skill tasks can be solved through composed capability paths.

## Phase 8 — TTSE / Experience Hygiene

Implement:

- FACT/TIP extraction
- prompt injection of validated experience
- cleanup
- expiry / confidence adjustment

**Exit criteria:** past experience measurably reduces repeat failures.

## Phase 9 — Auto-Harness

Implement:

- benchmark suite
- candidate generation
- A/B evaluation
- regression gate
- promotion/rollback

**Exit criteria:** Alpha can produce evidence that a harness change improves the benchmark before promotion.

## Phase 10 — MCP + External Agents

Implement:

- dynamic MCP lifecycle
- trust
- external agent adapters
- workflow `external_agent` node

**Exit criteria:** an external agent can execute a bounded workflow stage without bypassing Alpha permissions.

## Phase 11 — Scheduler + 24x7

Implement:

- scheduled jobs
- heartbeat
- health monitor
- recovery loops
- boot/startup integration

**Exit criteria:** scheduled tasks survive application restart and recover after worker failures.

## Phase 12 — Marketplace + Distribution

Implement last:

- remote skill registry
- signatures
- version publishing
- update channels
- desktop auto-update

**Exit criteria:** installed extensions are versioned, verified, auditable, and rollbackable.

---

# 47. Testing Strategy

## Unit tests

Test every state machine independently.

Required suites:

```text
SessionStateMachineTests
GoalStateMachineTests
PlanGateTests
SwarmSchedulerTests
WorkflowDagTests
CheckpointTests
BudgetTests
SkillRegistryTests
SkillEvolutionTests
ExperienceFilterTests
HarnessEvaluationTests
PermissionEngineTests
MCPTrustTests
SchedulerPersistenceTests
TraceStoreTests
```

## Integration tests

1. model → planner → tool
2. leader → worker → reviewer
3. workflow → checkpoint → restart → resume
4. skill → tool → verifier
5. failed execution → experience extraction
6. skill candidate → benchmark → rollback
7. cron → session → execution → notification
8. MCP connect → trust → tool load
9. external agent → workflow → result

## Chaos tests

Intentionally kill:

- worker
- backend process
- model connection
- MCP process
- scheduler
- filesystem operation
- network connection

Then assert recovery behavior.

---

# 48. Acceptance Tests

Alpha WorkSwarm integration is complete only when the following scenarios pass.

### Scenario 1 — Persistent coding goal

User says:

> Keep improving this repository until the test suite passes.

Alpha creates a goal, plans, executes, records failure, changes strategy, retries, verifies, and ends only when completed or explicitly blocked.

### Scenario 2 — Long session

Run a session long enough to exceed the normal context budget.

Expected:

- compaction
- archive
- worker replacement
- no loss of goal state
- no duplicate work

### Scenario 3 — Complex swarm task

Alpha creates:

- researcher
- coder
- reviewer

Research runs in parallel where possible, coder consumes structured research, reviewer receives exact diffs.

### Scenario 4 — Workflow interruption

Stop Alpha during a multi-node workflow.

Expected:

- checkpoint exists
- restart resumes from safe node
- already-completed nodes are not duplicated

### Scenario 5 — Skill improvement

Create a repeatable failure pattern.

Expected:

- experience proposal
- evidence references
- validation
- candidate version
- promotion only after passing tests

### Scenario 6 — Security boundary

Request a critical destructive tool call.

Expected:

- `deny` or explicit approval according to policy
- complete trace event
- no silent bypass

### Scenario 7 — Dynamic MCP

Connect a new MCP server.

Expected:

- inspect
- trust gate
- enable
- tools become available
- no backend restart

### Scenario 8 — Auto-Harness

Create a harness candidate.

Expected:

- baseline evaluation
- candidate evaluation
- regression analysis
- promotion decision
- rollback capability

---

# 49. Performance Strategy for Alpha Hardware

For the known low-RAM Windows target, prioritize memory efficiency.

### Keep resident

- scheduler
- control plane
- lightweight event bus
- current worker
- SQLite

### Create on demand

- specialist agents
- embedding processes
- browser workers
- heavy model processes
- external agent sessions

### Hard limits

```yaml
resources:
  max_parallel_agents: 3
  max_parallel_browser_workers: 1
  max_parallel_heavy_tools: 1
  max_cached_traces_mb: 256
  max_live_contexts: 2
```

Use disk-backed traces and artifacts rather than retaining everything in RAM.

---

# 50. Windows-First Requirements

Alpha should follow WorkSwarm's practical cross-platform terminal approach but optimize for Windows first.

Use one command abstraction:

```python
class CommandRunner(Protocol):
    async def run(self, command: str, cwd: str, env: dict[str, str]) -> CommandResult: ...
```

Backends:

- PowerShell
- Git Bash
- WSL
- native process execution

The agent should not care which backend is used.

All command invocations must pass through the same permission engine and trace layer.

---

# 51. Important Architectural Decisions

## Decision A — Reimplement concepts rather than clone the whole project

Alpha has its own desktop runtime, agent logic, self-recovery, MCP, memory, and GitHub integration. Pulling in the entire WorkSwarm stack would create unnecessary coupling.

Use WorkSwarm as a **reference architecture and feature source**.

Where code is reused, follow Apache 2.0 obligations and keep upstream notices/attribution as required by the actual files copied.

## Decision B — Local-first

Everything required for core operation must work on one machine.

## Decision C — Async execution

Use asynchronous worker scheduling so one blocked tool does not freeze the entire runtime.

## Decision D — Event-sourced operational history

Every important execution transition produces an event. This powers debugging, recovery, memory extraction, and RSI.

## Decision E — Version everything that can evolve

Version:

- skills
- workflow definitions
- harness configurations
- agent profiles
- prompts
- routing policies

## Decision F — Separate learning from execution

Execution produces evidence.

Learning proposes changes.

Evaluation decides whether the proposal is good enough.

Promotion activates it.

---

# 52. Things NOT to Copy Blindly

Do not blindly import:

- upstream internal assumptions that conflict with Alpha's current runtime
- provider-specific configuration code
- upstream UI structure
- hard-coded paths
- platform-specific startup behavior
- deprecated commands
- exact version pins without validation
- experimental features as production defaults

The upstream release stream changes quickly, so Alpha should depend on stable interfaces and its own compatibility tests rather than tracking every internal upstream change. The current repository was updated as recently as September 22, 2026 and the release history shows rapid feature evolution. citehttps://github.com/openJiuwen-ai/jiuwenswarm/releases citehttps://github.com/orgs/openJiuwen-ai/repositories

---

# 53. Recommended Alpha Feature Set After Full Integration

After implementation, an Alpha task should follow this pipeline automatically:

```text
USER INTENT
   ↓
SESSION
   ↓
GOAL
   ↓
PLAN
   ↓
SKILL RETRIEVAL
   ↓
SKILL/WORKFLOW COMPOSITION
   ↓
LEADER
   ↓
SWARM / SPECIALISTS
   ↓
TOOLS / MCP / EXTERNAL AGENTS
   ↓
CHECKPOINT
   ↓
VERIFICATION
   ↓
RESULT
   ↓
TRAJECTORY ANALYSIS
   ↓
FACT / TIP EXTRACTION
   ↓
SKILL EVOLUTION
   ↓
HARNESS EVALUATION
   ↓
PROMOTION / ROLLBACK
   ↓
LONG-TERM MEMORY
```

For long-running tasks:

```text
GOAL STILL ACTIVE?
    ├── YES → new worker / next attempt
    ├── BLOCKED → recover / ask human
    └── COMPLETE → archive + learn
```

---

# 54. Final Alpha Architecture Outcome

The objective is not to turn Alpha into a copy of WorkSwarm.

The objective is to take the strongest WorkSwarm architectural ideas and make them native to Alpha:

```text
                 ALPHA
                   │
      ┌────────────┼────────────┐
      │            │            │
   EXECUTION    MEMORY       EVOLUTION
      │            │            │
   SwarmFlow    Perpetual     Skill RSI
   Swarm        Session       Experience
   Agents       Compression   Auto-Harness
   Goals        Trace         Skill Graph
   Plans        Archives      Evaluation
      │            │            │
      └────────────┼────────────┘
                   │
            CONTROL + SECURITY
                   │
          Permissions / MCP / Git
                   │
               24x7 Alpha
```

The resulting system should be able to:

- maintain long-lived sessions
- hold persistent goals
- plan before acting
- dynamically build specialist teams
- execute deterministic workflows
- recover from interruption
- reuse skills
- discover skills dynamically
- compose skills into capability chains
- learn reusable experience from trajectories
- evolve skills through evidence and regression tests
- evaluate improvements to the harness
- schedule autonomous work
- dynamically load MCP capabilities
- delegate bounded work to external agents
- enforce permission policies
- inspect every important execution through traces
- operate continuously on a local machine
- update safely using a separate trusted update path

This is the WorkSwarm-derived capability layer that should sit on top of Alpha's existing agent core.

---

# 55. Implementation Checklist

## Foundation

- [ ] Event bus
- [ ] Trace store
- [ ] SQLite schema
- [ ] Checkpoint store
- [ ] Permission engine
- [ ] Config loader

## Persistent execution

- [ ] Perpetual Session
- [ ] Worker generations
- [ ] Context compaction
- [ ] Goal manager
- [ ] Goal attempts
- [ ] Recovery

## Planning

- [ ] Work mode
- [ ] Code mode
- [ ] Plan mode
- [ ] Approval gate

## Swarm

- [ ] Leader
- [ ] Dynamic workers
- [ ] Structured messages
- [ ] Handoff contracts
- [ ] Parallel execution
- [ ] Budget manager

## Workflow

- [ ] Workflow IR
- [ ] DAG scheduler
- [ ] Node types
- [ ] Human nodes
- [ ] Checkpoints
- [ ] Pause/resume
- [ ] Run tree

## Skills

- [ ] SKILL.md loader
- [ ] Registry
- [ ] Versioning
- [ ] Enable/disable
- [ ] Retrieval
- [ ] Skill graph
- [ ] Skill composition

## Evolution

- [ ] Evidence extraction
- [ ] Experience store
- [ ] FACT/TIP
- [ ] Skill proposals
- [ ] Skill evaluator
- [ ] Promotion
- [ ] Rollback
- [ ] Auto-Harness
- [ ] Harness benchmark

## Runtime ecosystem

- [ ] Dynamic MCP
- [ ] MCP trust
- [ ] External-agent adapters
- [ ] Scheduler
- [ ] Heartbeat
- [ ] Health monitor
- [ ] Slash commands

## Product

- [ ] Session UI
- [ ] Goal UI
- [ ] Swarm UI
- [ ] Workflow UI
- [ ] Skill UI
- [ ] Memory UI
- [ ] Trace UI
- [ ] Evolution UI
- [ ] Update UI

## Release

- [ ] Unit tests
- [ ] Integration tests
- [ ] Recovery tests
- [ ] Security tests
- [ ] Benchmark suite
- [ ] Update/rollback testing
- [ ] Apache 2.0 attribution/notice review for reused upstream material

---

# 56. Primary Upstream References

1. WorkSwarm repository: https://github.com/openJiuwen-ai/jiuwenswarm
2. WorkSwarm releases: https://github.com/openJiuwen-ai/jiuwenswarm/releases
3. Skills: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Skills.md
4. Skill Self-Evolution: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SkillSelfEvolution.md
5. Configuration: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Configuration.md
6. TUI / SwarmFlow: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/TUISwarmFlowGuide.md
7. Slash Commands: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/SlashCommands.md
8. Page Overview: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Page-Overview.md
9. Modes: https://github.com/openJiuwen-ai/jiuwenswarm/blob/develop/docs/en/Modes.md

---

## Conclusion

**Implement the features as Alpha-native subsystems, not as a monolithic WorkSwarm fork.**

The highest-value path is:

**Perpetual Session → Goals → Plan Mode → Swarm → SwarmFlow → Skills → Skill Evolution → Experience → Trajectory → Auto-Harness → Dynamic MCP → Scheduler/Heartbeat → Marketplace.**

That sequence gives Alpha the core WorkSwarm capabilities first, while keeping the architecture compatible with your existing autonomous execution, self-recovery, GitHub-aware coding, local/remote model routing, Electron desktop, and future RSI system.
