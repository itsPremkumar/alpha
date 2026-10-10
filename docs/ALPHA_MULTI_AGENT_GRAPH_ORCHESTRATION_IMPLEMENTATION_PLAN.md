# Alpha AI Agent — Multi-Agent Graph Orchestration: Advanced Research & Implementation Plan

**Document type:** Research-backed architecture and implementation guide  
**Prepared:** 10 October 2026  
**Target project:** [itsPremkumar/alpha](https://github.com/itsPremkumar/alpha)  
**Current architectural context:** Python, LangChain, LangGraph, Deep Agents, Alpha's existing bot/subagent/swarm modes, UI/backend/tools, and model-provider integrations  
**Primary goal:** Make multiple Alpha agents cooperate through explicit, observable, bounded, recoverable graphs—without replacing working features or creating uncontrolled agent loops.

> **Scope and honesty note:** This is an implementation specification based on current public documentation and research, not a claim that any proposed capability has already been implemented or tested inside Alpha. The implementation agent must first inspect the actual repository, installed dependency versions, current agent modes, APIs, and tests. It must adapt code to the checked-out codebase and must not blindly paste snippets or upgrade frameworks without evidence.

---

## 1. Executive recommendation

Build **Alpha Multi-Agent Graph Orchestration (AMAGO)** as a layer over Alpha's existing agent runtime. Use **LangGraph as the primary execution-graph runtime**, keep **one authoritative task/dependency graph** for task lifecycle and scheduling, and use **Deep Agents selectively as a worker harness** where its planning, isolated-context subagents, filesystem, skills, or context-management primitives add value.

Do not model the whole system as one endless group chat, and do not make every specialist communicate directly with every other specialist. Instead, combine several graph patterns, selected per request:

1. **Supervisor / manager graph:** a coordinator chooses a specialist or invokes a specialist as a tool while retaining responsibility for the goal and final answer.
2. **Task DAG / fan-out-fan-in graph:** independent specialists work concurrently on separately scoped subtasks; a join node collects validated results.
3. **Handoff / swarm graph:** control passes to another specialist when the active role changes; keep the active-agent identity and handoff history explicit.
4. **Hierarchical graph:** a top-level coordinator delegates to a bounded team lead that owns a smaller subgraph; useful only when the task is large enough to justify another coordination layer.
5. **Review/repair loop:** a verifier or critic checks evidence, tests, and acceptance criteria; failed checks route back to a bounded repair step.
6. **Event and artifact graph:** messages, decisions, model calls, tools, commits, outputs, failures, retries, and checkpoints are linked by stable IDs for replay and debugging.

**The central design rule:** separate **control flow**, **task dependencies**, **agent identity/capability**, **shared artifacts**, and **telemetry**. They are related graphs but not the same graph.

### What success should look like

When Alpha receives “research a bug, implement a fix, test it, and explain the result,” Alpha should be able to:

- decide whether one agent is enough or multiple agents add value;
- produce a plan with dependencies and acceptance criteria;
- assign research, code review, implementation, and testing to appropriate specialists;
- run only independent tasks in parallel;
- keep private worker context from contaminating unrelated workers;
- stream accurate agent/task status to the UI;
- prevent two code-writing agents from corrupting the same files;
- recover from a crash or provider timeout using persisted state;
- stop runaway handoffs, recursive spawning, or endless debate;
- verify artifacts and tests rather than trusting a worker's self-reported success;
- show the user what actually ran, what failed, what evidence exists, and what remains incomplete.

A visually attractive graph, many agents, or a confident final message is not proof of successful orchestration. Success means the graph actually executes the intended tools, stores state, handles failure, and validates results.

---

## 2. Research summary: what graph-based multi-agent engineering really means

“Graph engineering” is not one framework or a single database. It covers several connected structures:

| Graph / structure | Nodes | Edges | Purpose in Alpha |
|---|---|---|---|
| **Execution graph** | Workflow steps, agents, tool nodes, validators | Control-flow transitions, conditions, joins, loops | Determines what executes next |
| **Task dependency DAG** | Objectives and concrete tasks | Prerequisite/data dependency | Determines what can safely run in parallel |
| **Agent capability graph** | Agent profiles, skills, tools, model providers, runtime capabilities | “Can perform”, “requires”, “allowed to use” | Helps route a task to suitable workers |
| **Delegation graph** | Coordinator, team leads, workers | Spawn/delegate/return | Makes ownership and subtask ancestry explicit |
| **Communication graph** | Agent/task channels and messages | Request, handoff, result, critique, notification | Controls who communicates with whom and what data moves |
| **Artifact/provenance graph** | Files, reports, test results, citations, commits, claims | Produced-by, derived-from, verifies, supersedes | Makes results auditable and mergeable |
| **Knowledge/memory graph** | Facts, entities, project state, errors, sources | Typed relationships with provenance and time | Lets agents reuse verified information rather than inventing it |
| **Telemetry/causal graph** | Run, node attempt, LLM call, tool call, error, approval | Parent/child and causal relationships | Explains latency, costs, failures, and blocked runs |
| **Resource/dependency graph** | Providers, queues, worker processes, database, tools | Depends-on / unavailable-because | Controls concurrency and diagnoses runtime health |

Keep these concepts separate in their models and policies. Connect them through IDs such as `run_id`, `task_id`, `agent_instance_id`, `attempt_id`, `artifact_id`, `trace_id`, and `caused_by_event_id`.

### Graphs do not require a graph database

Alpha can represent a task DAG using relational tables with `task_id` and `depends_on_task_id`. This is often the simplest durable starting point. A dedicated graph database is not a prerequisite for graph-based orchestration. Select storage according to query needs; do not introduce a new database just because the architecture uses graphs.

### Research-backed operational lesson

Multi-agent systems are coordination systems, not merely “one LLM prompt copied many times.” Public framework documentation exposes distinct patterns—sequential workflow, concurrent fan-out/fan-in, manager/supervisor, handoff, group chat, and dynamically managed teams—because they solve different coordination problems. The surveys and topology research listed at the end also treat communication structure, cost, scalability, and collaboration strategy as central design choices. Alpha should select the simplest pattern that solves each workload, then measure whether the extra agents actually improve completion quality.

---

## 3. Framework landscape and recommended roles

These projects are reference implementations, not a list of dependencies Alpha must install all at once.

### 3.1 LangGraph — primary execution runtime

**Use it for:** typed workflow state, explicit nodes and routing, branches, loops, subgraphs, concurrent task dispatch, checkpoints, interruption/resumption, and stateful control over multi-step execution.

Why it fits Alpha:

- Alpha needs more than a text conversation loop: it needs to plan, dispatch workers, validate outputs, recover from failures, and explain state transitions.
- An explicit graph lets the UI show a real workflow instead of guessing progress from assistant prose.
- State and routing can be inspected in tests. Deterministic policy checks can live in normal Python nodes, rather than being left to the model.
- It can compose different agent implementations and subgraphs while keeping Alpha responsible for overall state and policies.

Implementation guidance:

- Keep one primary LangGraph entry point for autonomous requests, with subgraphs for meaningful reusable workflows.
- Use typed state and deliberately defined reducers for concurrent updates. Never let multiple parallel workers overwrite one shared `messages` or `status` field without a defined merge rule.
- Use bounded conditional edges for repair/review loops; every loop needs an explicit exit condition and maximum attempt count.
- Persist run state with a supported checkpointer and stable thread/run identity. Persist task state and external side effects independently where needed.
- Use `Send`/map-reduce or the installed version's equivalent when the task list is dynamic; confirm exact APIs against the version pinned in Alpha before coding.
- Use interrupts/human approval at the policy boundary for high-risk actions, then resume the same workflow with durable state.

Primary sources:

- [LangGraph repository](https://github.com/langchain-ai/langgraph)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph multi-agent concepts / tutorial entry points](https://docs.langchain.com/)

### 3.2 Deep Agents — optional worker/harness layer

Deep Agents is a higher-level harness built on LangGraph/LangChain components. Its public documentation describes planning, subagent delegation, context isolation, filesystem operations, skills, and optional memory/context tools. Its subagent documentation explicitly distinguishes synchronous subagents from asynchronous/long-running work and warns that subagents are useful when separate context or specialist instructions justify the overhead—not automatically for every trivial task.

**Recommended role in Alpha:** reuse selected Deep Agents features behind Alpha-owned interfaces. For example, a research worker can run as a Deep Agent, while Alpha's scheduler still owns task lifecycle, concurrency, run budgets, permission policy, approval state, cancellation, and acceptance checks.

**Avoid dual ownership.** If the Alpha coordinator and Deep Agents both decide to decompose the same goal, independently create child agents, retry failed work, and maintain separate task lists, the system can produce duplicated work and incoherent status. Choose one canonical task ledger. Deep Agent `write_todos` or internal planning may still be useful within the worker's local scope, but the parent task DAG must be authoritative for cross-agent dependencies.

References:

- [Deep Agents repository](https://github.com/langchain-ai/deepagents)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [Deep Agents async subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [Deep Agents deep-research example](https://github.com/langchain-ai/deepagents/tree/main/examples/deep_research)

### 3.3 LangGraph supervisor pattern — useful, but prefer explicit control

The `langgraph-supervisor` repository describes a hierarchical system where a supervisor controls task delegation. Its current README also recommends using the supervisor pattern directly through tools for many use cases, because this provides more control over context engineering. Treat the helper library as a pattern/reference and compare it with Alpha's installed LangChain version before adoption.

**Use a supervisor when:** a request needs dynamic specialist selection, centralized budget/permission control, or a single final-answer owner.

**Do not use it as:** a replacement for task dependency tracking, a reason to force all subtasks into serial order, or a license to let the LLM create unlimited child agents.

Reference: [LangGraph Supervisor repository and guidance](https://github.com/langchain-ai/langgraph-supervisor-py)

### 3.4 LangGraph Swarm — useful pattern for control handoffs

The `langgraph-swarm` project demonstrates agents that dynamically hand control to a specialist and retain the active agent identity for subsequent interactions. Its examples also show why context propagation must be explicit: passing the entire conversation history is one possible policy, not always the correct one for Alpha.

**Use swarm mode when:** user-facing control should move between specialists during a conversation or a workflow needs a localized handoff, such as router → browser researcher → coding specialist → testing specialist.

**Do not use swarm mode to:** model a large set of independent parallel subtasks. Handoffs are about who controls the next step; task DAGs are about dependencies and parallelism.

Reference: [LangGraph Swarm repository](https://github.com/langchain-ai/langgraph-swarm-py)

### 3.5 OpenAI Agents SDK — study manager vs handoff semantics

The OpenAI Agents SDK documents two useful composition styles:

- **Agents as tools / manager:** the coordinator keeps ownership of the overall interaction and calls specialists as bounded helpers.
- **Handoff:** control transfers to a specialist that becomes responsible for the next part of the conversation.

This is an important product-level distinction for Alpha's bot mode. The user's “main Alpha” should remain the owner when it needs to synthesize worker output; it should hand off only when the active specialist should own the next interaction. The SDK also documents tracing, guardrails, and human review concepts worth studying independently from provider choice.

References:

- [OpenAI Agents SDK overview](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)
- [Python SDK repository](https://github.com/openai/openai-agents-python)

### 3.6 Microsoft Agent Framework — compare patterns, do not automatically add

Microsoft's current Agent Framework documents sequential, concurrent, handoff, group-chat, and manager-style orchestration patterns, as well as workflow checkpoints, observability, and human-in-the-loop interactions. This is useful comparative material for designing Alpha's coordination API.

Microsoft's own repository marks AutoGen as maintenance mode and recommends Microsoft Agent Framework for new projects. Therefore, use AutoGen's `GraphFlow`, selector group chat, swarm, and Magentic-One as historical/design references if useful, but do not start a new Alpha core dependency on AutoGen without a specific reason. Prefer a supported maintained runtime that fits Alpha's existing LangGraph stack.

References:

- [Microsoft Agent Framework repository](https://github.com/microsoft/agent-framework)
- [Microsoft Agent Framework orchestration patterns](https://learn.microsoft.com/agent-framework/workflows/orchestrations/)
- [Microsoft Agent Framework workflows](https://learn.microsoft.com/en-us/agent-framework/journey/workflows)
- [AutoGen repository — maintenance-mode notice and migration guidance](https://github.com/microsoft/autogen)
- [AutoGen AgentChat team patterns](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/)

### 3.7 Google Agent Development Kit — compare workflow agent composition

ADK documents multi-agent composition and workflow agents such as sequential, parallel, and loop-oriented orchestration. These patterns are useful to compare with LangGraph's explicit state transitions. Alpha does not need a second runtime solely to obtain these patterns.

References:

- [ADK multi-agent systems](https://adk.dev/agents/multi-agents/)
- [ADK workflow agents](https://adk.dev/agents/workflow-agents/)

### 3.8 CrewAI — compare role-based crews and deterministic Flows

CrewAI distinguishes role-based agent teams (“Crews”) from event/state-oriented control (“Flows”). That separation is conceptually relevant: Alpha's specialist definitions describe who can do work; its graph/workflow describes what happens and when. Do not conflate agent role configuration with task execution state.

References:

- [CrewAI Crews](https://docs.crewai.com/en/concepts/crews)
- [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)

---

## 4. Multi-agent graph patterns Alpha should support

Alpha should expose a small number of user-facing modes built from reusable graph primitives—not one custom orchestration implementation for every mode.

### Pattern A — Single-agent execution (baseline)

```mermaid
flowchart TD
    U[User Request] --> I[Interpret / classify]
    I --> P[Plan or direct answer]
    P --> E[Execute one agent]
    E --> V[Verify result]
    V -->|Pass| R[Respond]
    V -->|Fail and retry budget remains| E
    V -->|Budget exhausted| F[Report failure / partial result]
```

Use when the task is small, low-risk, and does not benefit from separate specialist expertise. This is the baseline against which multi-agent cost and quality improvements must be evaluated.

### Pattern B — Supervisor with specialists

```mermaid
flowchart TD
    U[User Goal] --> C[Alpha Coordinator]
    C -->|bounded call| R[Research Specialist]
    C -->|bounded call| K[Coding Specialist]
    C -->|bounded call| T[Testing Specialist]
    R --> C
    K --> C
    T --> C
    C --> V[Acceptance / synthesis]
    V --> O[User-facing result]
```

Use when the coordinator needs dynamic specialist choice but should retain ownership of the final response. Workers return structured results and artifacts, not an unbounded transcript by default.

Good for: technical Q&A requiring a research check, code changes needing a test/review pass, bug triage, planning, and tasks where each specialist's output can be judged independently.

Risks: central bottleneck, coordinator repeatedly asking the same specialist, serial execution despite independent tasks, and oversized context if full histories are copied back.

Controls: exact task contract, per-role call limit, duplicate-task detection, token/time budget, concise return schema, coordinator decision log, and verifier-driven stop condition.

### Pattern C — DAG fan-out / fan-in (parallel specialists)

```mermaid
flowchart TD
    U[User Goal] --> P[Planner: emit tasks + dependencies]
    P --> G[Validate DAG + permissions + budgets]
    G --> S[Scheduler]
    S --> R[Research Worker]
    S --> A[Architecture Worker]
    S --> Q[Existing-test / repo-analysis Worker]
    R --> J[Join: wait for required tasks]
    A --> J
    Q --> J
    J --> M[Merge evidence / create implementation tasks]
    M --> V[Verifier]
    V --> O[Result]
```

Use when independent work can be performed without needing each other's intermediate messages. It is often the best default for research comparisons, repository audits, parallel test discovery, and independent reviews.

Rules:

- Execute only tasks whose dependencies are complete.
- Enforce a maximum number of in-flight tasks, not merely a maximum number of declared agents.
- Use separate task contexts and artifact outputs.
- A join node must distinguish “all required tasks succeeded,” “some optional tasks failed,” and “required task failed.”
- Do not treat missing worker output as success.
- Use deterministic dependency checks, not a model prompt alone, to decide when a task is ready.

### Pattern D — Handoff / swarm

```mermaid
flowchart LR
    U[User / ongoing conversation] --> RT[Entry router]
    RT -->|research issue| R[Research Specialist]
    R -->|needs code edit| C[Coding Specialist]
    C -->|needs test| T[Testing Specialist]
    T -->|test failure| C
    T -->|verified| CO[Coordinator / answer]
```

Use when the active specialist should change based on context and the active-agent identity should persist in conversation state. Do not treat a handoff as proof that a task was completed. Record the handoff event, its reason, permitted context transfer, and the receiving agent.

Every handoff must have:

- `from_agent_id`, `to_agent_id`, `run_id`, `task_id` (if applicable);
- a structured reason and expected outcome;
- a minimal context package (relevant user request, task instructions, verified artifacts, constraints);
- a policy/capability check;
- hop count and cycle detection;
- a fallback when the destination is unavailable.

### Pattern E — Hierarchical teams / nested graphs

```mermaid
flowchart TD
    U[Complex goal] --> H[Top-level coordinator]
    H -->|research workstream| RL[Research team lead]
    H -->|software workstream| SL[Software team lead]
    RL --> R1[Source discovery]
    RL --> R2[Evidence review]
    SL --> S1[Code analysis]
    SL --> S2[Test design]
    R1 --> RR[Research subgraph join]
    R2 --> RR
    S1 --> SR[Software subgraph join]
    S2 --> SR
    RR --> H
    SR --> H
    H --> Z[Global verification]
```

Use only for large tasks whose workstreams need their own planning and coordination. If one supervisor can manage a small number of workers, another hierarchy level adds latency, token cost, more failure boundaries, and more difficult debugging.

Hard limits are required: maximum delegation depth, maximum total child tasks, and a shared run budget that all nested supervisors inherit. A child team may allocate its own budget, but may not increase the overall cap.

### Pattern F — Critic / reviewer / repair cycle

```mermaid
flowchart TD
    W[Worker creates result] --> V[Independent verifier]
    V --> D{Acceptance checks pass?}
    D -->|Yes| A[Accept and record evidence]
    D -->|No, retry allowed| F[Failure report + exact defects]
    F --> W2[Repair task]
    W2 --> V
    D -->|No, budget exhausted| P[Partial result + unresolved failures]
```

Use for code modifications, research synthesis, structured outputs, file generation, and any task where a claim can be checked. The verifier should see the acceptance criteria and worker artifacts; it should not merely be asked “does this look good?”

Prefer objective verification first (tests, schemas, file checks, citation resolution, diff lint, tool-result validation), then use a critic model for semantic issues. Never let the same worker's self-report substitute for independent verification when meaningful tests exist.

### Pattern G — Bounded debate / alternative proposals

Use for ambiguous architecture choices, planning alternatives, or genuine trade-offs. Start with two or three independent proposals, require cited/traceable claims, then use a decision/evaluation step with explicit criteria. Avoid perpetual round-robin debate and avoid pretending that multiple agents agreeing on the same unsupported claim constitutes evidence.

Suggested topology: independent proposals → one critic per proposal or one shared critic → deterministic scoring table → decision owner. Limit to one critique round by default; allow a second round only when the verifier identifies a material unresolved disagreement.

### Pattern H — Event-driven / blackboard collaboration

Workers can publish typed events and artifacts to a shared run ledger rather than chat directly. A scheduler reacts to events such as `TaskCompleted`, `ArtifactPublished`, or `VerificationFailed` and evaluates which task dependencies have become ready. This is a useful architecture for long-running work, but it should not require a heavyweight message broker in Alpha's first implementation.

Start with an append-only event table and an in-process async scheduler. Add a durable external queue only if Alpha must distribute work across processes/machines or handle sustained queue volumes.

### Pattern selection matrix

| User request / work type | Recommended graph | Why | Default policy |
|---|---|---|---|
| Simple Q&A or transformation | Single agent | Agent overhead is not justified | No delegation |
| Needs one specialist's expertise | Supervisor / agent-as-tool | Central ownership, narrow helper | One worker call, structured result |
| Research comparing independent sources/topics | DAG fan-out/fan-in | Parallelism with clean evidence merge | 2–3 workers initially, bounded |
| Coding task with independent analysis and tests | DAG, then serial integration and review | Separate analysis from shared writes | Parallel read-only analysis; isolated worktrees for edits |
| Conversation needs a domain transfer | Handoff/swarm | Active owner changes | Cycle/hop limit |
| Very large project with multiple workstreams | Hierarchical subgraphs | Local planning per workstream | Depth cap, parent budget |
| High-stakes code or factual output | Worker + independent verifier loop | External acceptance conditions | Bounded retry; report unresolved failures |
| Brainstorming alternatives | Debate/ensemble | Independent candidates improve coverage | 2–3 proposals, one critique pass |
| Continuous background jobs | Event-driven graph + queue | Long-lived events and resumability | Durable tasks, cancellation and leases |

---

## 5. Recommended Alpha product model: one runtime, several coordination modes

Alpha already has bot mode, specialists, subagents, and swarm mode. Preserve those user-facing concepts, but normalize them into one backend orchestration service and shared models.

### 5.1 Suggested mapping

| Existing/product concept | Internal mode | Execution behavior |
|---|---|---|
| **Bot mode** | `supervisor` or `single` | Alpha remains the user's main agent; calls specialists as tools/tasks |
| **Specialist agents** | `agent_registry` entries | Typed role/capability/configuration, not independently scheduled daemons by default |
| **Subagent mode** | `child_task` / nested subgraph | Bounded isolated task with parent-child relationship and summarized output |
| **Swarm mode** | `handoff_network` | Specialists hand off active control under explicit routing rules |
| **Parallel swarm / team mode** | `task_dag` + worker pool | Multiple independent task nodes execute concurrently and join |
| **Autopilot / Apex mode** | `autonomous_supervisor` | Goal decomposition, dependency scheduling, execution, verification, recovery, and user-visible progress |
| **Debate / council mode** | `ensemble_review` | Independent opinions/proposals + bounded evaluation |
| **Review/fix mode** | `repair_loop` | Verifier detects defects and routes specific repair tasks |

Avoid a naming problem where “swarm” is expected to mean both dynamic agent handoffs and simultaneous parallel execution. In the UI, explain the difference: **Swarm (handoff)** changes who is in control; **Parallel team** runs multiple bounded tasks at once.

### 5.2 One canonical coordinator

Implement an Alpha-owned `OrchestrationService` / graph entry point. It can call existing agents or Deep Agents, but the service is responsible for:

- creating and loading run state;
- selecting a pattern/mode;
- validating the task DAG;
- checking agent registry and tool permissions;
- applying concurrency, timeout, cost/token, recursion, and rate limits;
- creating task attempts and event records;
- scheduling eligible tasks;
- routing/recording handoffs;
- verifying required outputs;
- handling cancellation and human approval;
- returning normalized progress and results to the UI/API.

Do not duplicate this policy in the frontend, individual agent prompts, multiple schedulers, and a Deep Agents system prompt. UI can request an action, but backend policy is authoritative.

### 5.3 Execution topology overview

```mermaid
flowchart TD
    UI[Alpha UI / Telegram / API] --> API[Existing API + Run Manager]
    API --> AUTH[Validate request, run policy, budgets]
    AUTH --> ORCH[Alpha OrchestrationService]
    ORCH --> LG[LangGraph durable run graph]
    LG --> PLAN[Planner + DAG validator]
    PLAN --> SCHED[Scheduler / ready-task queue]
    SCHED --> REG[Agent registry + capability matcher]
    REG --> WORK[Worker adapters]
    WORK --> LLM[Existing model/provider layer]
    WORK --> TOOLS[Existing tools / MCP / shell / browser / coding]
    WORK --> DA[Optional Deep Agents harness]
    TOOLS --> ART[Artifact store / isolated workspaces]
    DA --> ART
    WORK --> DB[(Run/task/event store)]
    LG --> DB
    ART --> VERIFY[Verifier / integration gate]
    VERIFY -->|pass| FINAL[Final synthesis + truthful status]
    VERIFY -->|repairable failure| SCHED
    VERIFY -->|budget or policy stop| FINAL
    DB --> OBS[Trace / logs / metrics / graph events]
    OBS --> UI
    FINAL --> UI
```

---

## 6. Agent registry: specialists as governed capabilities

Do not identify a specialist only by its name and prompt. Every agent needs a structured profile and execution policy.

### 6.1 Suggested agent profile

```python
from typing import Literal
from pydantic import BaseModel, Field

class AgentProfile(BaseModel):
    agent_id: str
    display_name: str
    description: str
    role: str
    capabilities: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    allowed_tool_ids: list[str] = Field(default_factory=list)
    denied_tool_ids: list[str] = Field(default_factory=list)
    model_policy_id: str
    runtime_kind: Literal["native", "deep_agent", "remote_a2a"] = "native"
    context_policy: Literal["isolated", "filtered", "shared_summary"] = "isolated"
    max_concurrent_instances: int = 1
    task_timeout_seconds: int = 300
    max_llm_calls: int = 12
    max_tool_calls: int = 30
    max_output_bytes: int = 200_000
    approval_policy_id: str = "default"
    enabled: bool = True
    version: str = "1"
```

The exact model can be adjusted to Alpha's current Pydantic version and schema conventions. Enforce validation in code, not by prompt alone.

### 6.2 Starter specialist roster

Start with a small, useful roster. Additional roles should be added only when evaluation shows a recurring gap.

| Agent | Responsibility | Tools (principle) | Output contract |
|---|---|---|---|
| **Alpha Coordinator** | Understand user goal; choose execution pattern; own final status | Planner, registry search, task create/inspect, result synthesis | Goal, execution mode, plan, final outcome |
| **Planner / Decomposer** | Split the goal into atomic tasks and dependencies | Read-only repo/context search, DAG proposal tool | Validatable task graph, dependency reasons, acceptance criteria |
| **Research Specialist** | Collect evidence and compare sources | Web/search/fetch/read tools; cite exact sources | Findings, claim-source map, uncertainty, source list |
| **Repository Analyst** | Inspect code structure, APIs, tests, logs | Read-only filesystem/git/search | Findings with paths/line ranges, likely impact, tests to run |
| **Architecture Specialist** | Propose implementation alternatives and interfaces | Read-only source/doc tools | Alternatives, trade-offs, migration risks |
| **Implementation / Coding Specialist** | Make a scoped change | Isolated worktree/container; write only assigned scope | Patch/commit/diff, files changed, commands run |
| **Testing Specialist** | Derive and execute acceptance tests | Test runner/sandbox, source reading | Test definitions, exact results, reproducible logs |
| **Code Reviewer** | Review a specific diff against requirements | Read-only diff/tests/security tools | Severity-tagged findings tied to file/line or test |
| **Security / Permission Reviewer** | Detect untrusted input, secret exposure, unsafe tool use | Read-only source and policy tools | Concrete threat + exploitability + mitigation |
| **Integration / Merge Agent** | Integrate worker artifacts after validation | Git merge/cherry-pick within policy, tests | Integration commit/diff + conflict and validation report |
| **Verifier / Evaluator** | Determine whether acceptance conditions pass | Independent tests/schema/evidence checks | Pass/fail per criterion, evidence pointer, unresolved issues |

Some roles may initially be the same underlying agent profile with different prompts and tools. Separate logical responsibilities first; split into separate model instances only when isolation, capability, or evaluation justifies it.

### 6.3 Agent selection: capability matching before LLM choice

Use deterministic filters first and an LLM only where ranking needs semantic judgment.

1. Filter out disabled agents.
2. Filter by required capabilities/skills.
3. Filter by allowed tools and approval policy.
4. Filter out agents at concurrency or health limits.
5. Match model/provider requirements, context size, multimodal needs, latency, and cost budget.
6. Score remaining candidates using deterministic policy plus optional model ranking.
7. Persist **why** an agent was selected and alternatives rejected.
8. If no candidate can perform the task, return a capability gap instead of silently substituting a general agent.

An agent's description is routing metadata, not an authorization boundary. Authorization must be checked again by the tool broker at execution time.

---

## 7. Canonical task graph and lifecycle

The most important part of a robust multi-agent system is a dependable task ledger. An LLM's in-context to-do list is not sufficient as the only record of cross-agent work.

### 7.1 Task state machine

```mermaid
stateDiagram-v2
    [*] --> proposed
    proposed --> validated
    validated --> blocked: dependencies pending
    validated --> ready: dependencies satisfied
    blocked --> ready: dependencies satisfied
    ready --> queued
    queued --> running
    running --> succeeded: output validated
    running --> failed: unrecoverable error
    running --> retry_wait: retryable error
    retry_wait --> queued: budget remains
    running --> needs_approval: risky operation
    needs_approval --> queued: approved
    needs_approval --> cancelled: rejected
    queued --> cancelled
    ready --> cancelled
    succeeded --> [*]
    failed --> [*]
    cancelled --> [*]
```

Suggested statuses: `proposed`, `validated`, `blocked`, `ready`, `queued`, `running`, `retry_wait`, `needs_approval`, `succeeded`, `failed`, `cancelled`, `skipped`.

Define transitions centrally. Never allow an agent to set an arbitrary task status through natural language. A tool call such as `report_task_result` must validate whether the transition is legal.

### 7.2 Task contract

Each task should carry:

- `task_id`, `run_id`, `parent_task_id`, `created_by_agent_id`;
- clear objective and bounded scope;
- `assigned_agent_id` or assignment policy;
- `depends_on[]` and optional data-input artifact IDs;
- required/optional flag and expected output schema;
- acceptance criteria that can be checked;
- allowed tools, allowed filesystem/worktree scope, and data access policy;
- priority, deadline, timeout, retry count, concurrency group;
- max LLM calls/tokens, max tool calls, maximum output size, estimated resource use;
- attempt and lease identifiers;
- final status, error category, artifact references, verification result;
- creation/update timestamps and causal event IDs.

### 7.3 DAG validity checks

Before running any worker, validate the proposed dependency graph:

1. Every dependency refers to an existing task in the same run or an explicitly approved external prerequisite.
2. The graph is acyclic. Reject cycles with a readable cycle path.
3. Every task has a known output schema or an explicit “unstructured artifact” contract.
4. Dependencies are semantically usable: task B's inputs are actually produced by task A.
5. Required tasks are reachable from the run's root goal.
6. No task exceeds depth, concurrency, deadline, cost, model/provider, or permission budgets.
7. The number of tasks is bounded before expansion is accepted.
8. Shared-resource conflicts are either serialized or isolated.
9. Acceptance criteria cover every required objective.
10. The graph has at least one valid terminal state and no endless retry cycle.

Use ordinary Python graph algorithms or NetworkX if it already fits the dependency set; for small DAGs, simple topological sorting and cycle detection are enough. Do not add a graph package merely for fashion.

### 7.4 Distinguish dependency types

Use different edge types rather than one generic `depends_on` string wherever the meaning matters:

- `data_dependency`: downstream task requires an artifact/result from upstream;
- `control_dependency`: downstream cannot start until upstream completes;
- `review_dependency`: review is required before the next action;
- `resource_conflict`: tasks may not execute concurrently on one resource;
- `delegation`: task was created by a parent/manager;
- `causal`: event/artifact was created as a consequence of another event.

Resource-conflict edges do not necessarily mean a result is needed; they mean the scheduler must serialize access or provide isolation.

---

## 8. Proposed storage model

Use Alpha's existing storage/database conventions if reliable. If no durable task ledger exists, SQLite is a reasonable local-first starting point. PostgreSQL is suitable if Alpha already uses it for its core runtime. Do not introduce SQLite and PostgreSQL as duplicate sources of truth for the same task state.

### 8.1 Core tables (illustrative schema)

Adapt migrations to the database actually in use.

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    user_request TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    root_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    budget_json TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    parent_task_id TEXT,
    task_key TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,
    assigned_agent_id TEXT,
    output_schema_json TEXT,
    acceptance_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_json TEXT,
    result_json TEXT,
    UNIQUE(run_id, task_key)
);

CREATE TABLE IF NOT EXISTS task_edges (
    run_id TEXT NOT NULL,
    from_task_id TEXT NOT NULL,
    to_task_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    condition_json TEXT,
    PRIMARY KEY (run_id, from_task_id, to_task_id, edge_type)
);

CREATE TABLE IF NOT EXISTS agent_instances (
    agent_instance_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    agent_id TEXT NOT NULL,
    parent_agent_instance_id TEXT,
    status TEXT NOT NULL,
    model_route_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    context_policy TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    lease_owner TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    error_class TEXT,
    error_json TEXT,
    idempotency_key TEXT,
    UNIQUE(task_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    attempt_id TEXT,
    artifact_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    sha256 TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    source_agent_id TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    agent_instance_id TEXT,
    attempt_id TEXT,
    parent_event_id TEXT,
    trace_id TEXT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    tool_call_id TEXT,
    action_summary TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_run_status ON agent_tasks(run_id, status);
CREATE INDEX IF NOT EXISTS idx_events_run_time ON agent_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_task_status ON task_attempts(task_id, status);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_task ON artifacts(run_id, task_id);
```

**Important schema notes:**

- Use JSON columns/strings for evolving payloads, but promote fields used in queries, uniqueness, filtering, or policies into proper indexed columns.
- Add tenant/user/workspace ownership fields if the current product supports multiple users or isolation contexts.
- Enforce foreign keys and uniqueness constraints where supported.
- Append-only event records should not be silently rewritten to hide failures. Store state transitions and corrected results as new events.
- Don't store secrets, complete unredacted prompts, or sensitive tool outputs indiscriminately in event payloads.
- Run schema migrations through Alpha's existing migration system. Do not change the current database engine casually.

### 8.2 Why the durable task ledger is separate from LangGraph checkpoints

LangGraph checkpoints capture graph state and enable resumable execution. Alpha's own task ledger serves queries the UI, scheduler, user, and audit tools need: all task statuses, assignments, attempts, artifacts, and errors across runs. These are complementary. Design a controlled consistency model rather than treating a checkpoint as the only database.

A practical strategy:

- LangGraph checkpoint: current execution state for a thread/run;
- task store: normalized current task state;
- event log: append-only lifecycle events;
- artifact store: files, diffs, reports, evidence;
- trace backend: spans and runtime performance, optionally exportable via OpenTelemetry.

For key state transitions, write durable task/event records before launching external work and use stable idempotency keys for side effects.

---

## 9. Typed messages and inter-agent communication contracts

Agents should not coordinate primarily through natural-language descriptions of status. Use natural language for reasoning/content, but standardized message envelopes for task creation, handoff, completion, errors, and verification.

### 9.1 Message envelope

```python
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class AgentMessage(BaseModel):
    message_id: str
    run_id: str
    task_id: str | None = None
    from_agent_instance_id: str
    to_agent_instance_id: str | None = None  # None can mean event broadcast
    kind: Literal[
        "task_request", "task_result", "handoff", "progress",
        "clarification", "artifact_published", "verification_result",
        "error", "cancel", "approval_request", "approval_result"
    ]
    correlation_id: str
    causation_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
```

### 9.2 Task request payload

Require an objective, inputs, constraints, output contract, acceptance criteria, allowed capabilities, deadline, and budget. Avoid “solve this whole project however you want” as a generic delegation contract.

```json
{
  "objective": "Identify the cause of the failing authentication tests",
  "scope": {"paths": ["src/auth/**", "tests/auth/**"], "read_only": true},
  "inputs": [{"artifact_id": "art-123", "purpose": "test log"}],
  "output_contract": "alpha.auth_diagnosis.v1",
  "acceptance_criteria": [
    "Cite exact failing test names",
    "List evidence-backed root-cause hypotheses",
    "Do not modify files"
  ],
  "allowed_tools": ["repo_search", "read_file", "run_tests_readonly"],
  "deadline_seconds": 180,
  "budget": {"max_llm_calls": 8, "max_tool_calls": 15}
}
```

### 9.3 Task result payload

```json
{
  "status": "succeeded",
  "summary": "Two authentication tests fail after token expiry handling changed.",
  "findings": [
    {
      "claim": "The expired-token branch returns HTTP 200 instead of 401",
      "evidence": [{"artifact_id": "art-log-2", "locator": "tests/auth/test_expiry.py::test_expired_token"}],
      "confidence": "high"
    }
  ],
  "artifacts": ["art-diagnosis-7"],
  "acceptance_checks": [{"name": "failing_tests_cited", "passed": true}],
  "unresolved_questions": [],
  "actual_usage": {"llm_calls": 4, "tool_calls": 7}
}
```

A `succeeded` result means the task contract was completed and its output schema validated, not that the entire parent goal is finished. The parent verifier decides whether the result satisfies the dependency and acceptance criteria.

### 9.4 Context transfer policy

Support at least these modes:

- `isolated`: worker receives only its task, explicit inputs, relevant memory, and constraints;
- `filtered`: selected conversation excerpts and artifacts are injected based on policy;
- `shared_summary`: worker sees the user goal plus a concise verified project/run summary;
- `full_history`: exceptional; use only when necessary and explicitly enabled.

Default to `isolated` or `filtered` for delegated work. Share artifact references rather than copying large tool logs into every agent prompt. Do not mix internal chain-of-thought; communicate conclusions, evidence, assumptions, decisions, and tool/action records instead.

### 9.5 Distinguish message types

- **Handoff** = who should own the next step.
- **Delegation** = a parent assigns a bounded task to a child.
- **Fan-out** = independent tasks start concurrently.
- **Result** = a worker's structured output.
- **Event** = lifecycle/telemetry record, not necessarily a direct message to another agent.
- **Artifact** = durable output with location/hash/provenance.

These meanings should not be implemented as several synonyms for “send message.”

---

## 10. Supervisor and scheduler responsibilities

Keep the LLM coordinator and deterministic scheduler separate.

### 10.1 Coordinator (reasoning responsibilities)

- Understand the user goal and constraints.
- Decide whether delegation is justified compared with the single-agent baseline.
- Propose subtasks, outputs, acceptance criteria, dependencies, and candidate capabilities.
- Decide when evidence conflicts need review.
- Synthesize verified worker results into a user answer.
- Explain uncertainty or ask the user only for genuinely blocking input.

### 10.2 Scheduler (deterministic responsibilities)

- Validate DAG structure and task contracts.
- Determine which tasks are ready.
- Enforce concurrency/resource limits and fairness.
- Create task attempts and leases.
- Apply retry/backoff policy and detect stale workers.
- Prevent duplicate execution and repeated side effects.
- Enforce cancellation, deadlines, global run budget, delegation depth and total task count.
- Handle joins and required-vs-optional task policy.
- Emit state transitions/events and update UI progress.

An LLM may propose a task graph; ordinary code must validate and execute it.

### 10.3 Reference scheduling loop

```python
async def schedule_run(run_id: str) -> None:
    while True:
        run = await task_store.get_run(run_id)

        if run.cancel_requested:
            await cancel_queued_tasks(run_id)
            await terminate_or_cancel_workers(run_id)
            await task_store.mark_cancelled_if_quiescent(run_id)
            return

        await recover_expired_leases(run_id)
        await mark_ready_tasks_from_satisfied_dependencies(run_id)

        if await global_budget_exhausted(run_id):
            await stop_new_dispatch_and_finalize_partial(run_id)
            return

        ready = await task_store.list_ready_tasks(run_id)
        capacity = await resource_policy.remaining_capacity(run_id)

        for task in select_fair_batch(ready, capacity):
            profile = await registry.resolve_eligible_agent(task)
            if profile is None:
                await task_store.fail_with_capability_gap(task.task_id)
                continue
            await dispatch_idempotently(task, profile)

        if await run_is_terminal(run_id):
            return

        await wait_for_event_or_short_poll()
```

This is a conceptual algorithm, not drop-in code. Actual implementation must match Alpha's event loop, async model provider APIs, scheduler, database library, cancellation support, and LangGraph version. Do not busy-loop; use event notifications with bounded polling as a fallback.

### 10.4 Concurrency and budget defaults

For Alpha's local-first constraint (limited system RAM and possible local model inference), start conservatively and make the limits configurable:

- 1 active run per local model endpoint by default unless the endpoint supports concurrent requests safely;
- 2–3 concurrent worker tasks globally at first, with a health-aware ceiling;
- maximum delegation depth: 2 by default (coordinator → worker; allow worker → child only in explicitly approved modes);
- maximum total spawned tasks per run: a small configurable cap (e.g., 8 initially);
- maximum retry attempts per task: 2 by default beyond the initial attempt, with errors classified first;
- no default infinite debate loops;
- run deadline and aggregate LLM/tool budget inherited by every child;
- model-provider rate limits and tool-specific concurrency groups enforced separately.

These are **starting policy values**, not universal optima. Benchmark with Alpha's actual laptop, GPU/VRAM, provider quotas, and model mix before raising them. A remote API model may tolerate higher concurrency than a local model loaded in constrained RAM/VRAM.

---

## 11. LangGraph implementation design

### 11.1 Build one top-level run graph

Suggested high-level nodes:

```mermaid
flowchart TD
    START([START]) --> LOAD[Load request + run policy]
    LOAD --> CLASSIFY[Classify complexity / mode]
    CLASSIFY -->|simple| SINGLE[Single agent execution]
    CLASSIFY -->|delegation justified| PLAN[Plan tasks]
    PLAN --> VALIDATE[Validate contracts + DAG + budgets]
    VALIDATE -->|invalid| REPAIR_PLAN[Repair plan within limit]
    REPAIR_PLAN --> VALIDATE
    VALIDATE -->|valid| SCHEDULE[Schedule ready tasks]
    SCHEDULE --> DISPATCH[Dispatch bounded workers]
    DISPATCH --> JOIN[Join required task outputs]
    JOIN --> REVIEW[Verify goal acceptance]
    REVIEW -->|pass| SYNTH[Synthesize result]
    REVIEW -->|repairable| REPLAN[Create specific repair tasks]
    REPLAN --> VALIDATE
    REVIEW -->|not repairable / budget exhausted| PARTIAL[Finalize partial status]
    SINGLE --> REVIEW
    SYNTH --> PERSIST[Persist final status + artifacts]
    PARTIAL --> PERSIST
    PERSIST --> END([END])
```

The task scheduler can be an internal component invoked by graph nodes or a long-lived async worker that reacts to persisted events. Select based on the actual lifecycle needs. Avoid blocking a single request handler for hours if the application needs cancellation, app restarts, background execution, and progress polling.

### 11.2 Separate graph state from the normalized task ledger

Illustrative state model:

```python
from typing import Any, Literal, TypedDict

class RunState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_request: str
    mode: Literal[
        "single", "supervisor", "task_dag", "handoff_swarm",
        "hierarchical", "ensemble", "autopilot"
    ]
    plan_version: int
    task_ids: list[str]
    ready_task_ids: list[str]
    task_result_refs: list[str]
    verification_summary: dict[str, Any]
    required_failures: list[str]
    warnings: list[str]
    run_budget: dict[str, Any]
    retry_counts: dict[str, int]
    cancellation_requested: bool
    approval_required: bool
    final_status: Literal["running", "succeeded", "partial", "failed", "cancelled"]
    final_answer: str
```

The task ledger remains authoritative for normalized task details. Graph state carries IDs, bounded summaries, routing data, and current control state. This avoids growing the in-memory/checkpoint state indefinitely with every worker message and file content.

### 11.3 Safe parallel updates

When multiple nodes execute in one graph superstep, state merge semantics matter. Do not let all nodes write to `task_result`, `messages`, `current_agent`, or one shared file path without a reducer/concurrency policy.

Recommended approach:

- each worker writes its own task result row/artifact and returns a **small unique result reference**;
- append result references using a deliberate reducer or let the task store be queried at the join;
- each task has its own status and attempt identity;
- a join node sorts results deterministically by task plan order or stable task ID before synthesis;
- shared conversation updates go through one coordinator node;
- if two agents' changes conflict, create an explicit conflict-resolution task rather than relying on last-write-wins.

### 11.4 Dynamic task graphs

For arbitrary user goals, the number of subtasks is dynamic. The planner should return a strict schema. Validate that schema and convert it into task rows. Use LangGraph's supported dynamic fan-out/`Send` pattern (or the equivalent for the installed version) to dispatch tasks once dependencies are satisfied.

Do not construct arbitrary executable Python or import paths from planner JSON. Node types must come from an allowlisted registry. User/model-supplied task descriptions are data, not executable code.

### 11.5 Handoff graph

Implement an explicit `active_agent_id` or an equivalent validated route state if swarm behavior is required. A handoff operation should:

1. verify the target is registered and enabled;
2. verify the target is permitted for the current task and policy;
3. limit the handoff context to the declared context policy;
4. record a handoff event with its reason;
5. increment and validate hop/cycle counters;
6. update the active role only after the handoff is accepted;
7. preserve checkpoint/run ID;
8. define fallback and visible failure if the target is unavailable.

Avoid enabling every agent to hand off to every other agent. Build an allowlisted capability transition graph; e.g., `researcher → analyst`, `analyst → coder`, `coder → tester`, `tester → coder` on a bounded failure, `tester → coordinator` on success or terminal failure.

### 11.6 Human-in-the-loop and risky tools

Add approval nodes or interrupt gates before high-impact operations such as deleting/overwriting important files, publishing changes, pushing to remote repositories, changing system settings, accessing sensitive data, or executing destructive shell commands. Approval must be tied to an exact proposed action/payload and expire or require renewed approval if the action materially changes.

The worker cannot grant itself approval. It requests approval and suspends. After approval, the same run resumes and revalidates that the action payload still matches what was approved.

---

## 12. Parallel agents, shared files, and integration safety

Parallel reasoning is easier than parallel mutation. Alpha should default to parallel **read-only analysis**, then perform code changes in isolated scopes and integrate them deliberately.

### 12.1 File and code isolation policies

For repository work:

- Create one Git worktree or isolated workspace per write-capable task, unless the workspace engine already provides equivalent safe isolation.
- Assign each write task explicit paths/modules or a clear ownership boundary.
- Make analyzer/research/reviewer agents read-only by default.
- Store diffs, commands, test logs, and commits as artifacts linked to the task.
- Never let multiple workers concurrently mutate the same checkout without locks or isolated worktrees.
- Use an integration/merge node to check file conflicts, apply patches, run formatting/type-checks/tests, and record the resulting commit/diff.
- Re-run affected tests after merge; individually passing branch tests do not prove that the integrated result is correct.
- Keep user changes safe: check the worktree before applying changes, and never reset/clean a user workspace as a generic recovery operation.

### 12.2 Resource locks

Some tools or resources are single-writer: one browser profile, one local model endpoint, one DB migration target, one development server port, one repository branch, or one physical/remote machine. Represent those constraints in a `resource_key`/`concurrency_group` that the scheduler locks before dispatch and releases in a `finally`/lease-recovery path.

A graph edge is not a substitute for a real lock if the tasks can be launched by separate processes.

### 12.3 Artifact-based collaboration

Prefer this pattern:

`worker → artifact (patch/report/test log) → verifier/integrator → downstream task`

Avoid this pattern:

`worker A edits files → worker B assumes what changed → worker C overwrites same files → coordinator trusts chat transcript`

Artifacts should include stable IDs, URI/path, hashes where appropriate, producer task/attempt, schema/type, and validation state. Sensitive content must follow the same access policies as source artifacts.

---

## 13. Error handling, retries, cancellation, and recovery

A robust agent graph must treat failure as typed state, not as a string printed to logs.

### 13.1 Error taxonomy

Recommended categories:

- `model_timeout`, `model_rate_limited`, `model_unavailable`, `invalid_model_output`;
- `tool_timeout`, `tool_unavailable`, `tool_permission_denied`, `tool_output_invalid`;
- `task_contract_invalid`, `dag_cycle`, `agent_not_found`, `capability_gap`;
- `budget_exceeded`, `deadline_exceeded`, `cancelled`;
- `dependency_failed`, `worker_lost`, `lease_expired`;
- `artifact_missing`, `artifact_corrupt`, `workspace_conflict`;
- `approval_required`, `approval_rejected`, `policy_denied`;
- `verification_failed`, `integration_failed`, `unknown_internal_error`.

Persist `error_code`, short user-safe summary, sanitized technical details, retryability, attempt number, stack trace reference, and causal IDs. Redact credentials, cookies, authorization headers, and sensitive user content from logs.

### 13.2 Retry policy

Retry only errors classified as retryable. Use bounded exponential backoff with jitter for rate limits/transient network errors. Do not retry permission denials or invalid plans as though they were provider outages.

Before retrying any non-idempotent action, check its idempotency key and external state. For example, a shell command, code deployment, or file-writing tool can have side effects even if the agent process times out before recording completion.

A repair loop should be **goal- and evidence-directed**: the verifier emits exact failed acceptance criteria and evidence; planner creates a limited repair task; the scheduler verifies the remaining budget; and the system runs tests again.

### 13.3 Checkpoints and worker leases

For long-running work:

- persist graph checkpoints and task states before long external calls where the runtime supports it;
- maintain worker leases/heartbeats for tasks that can outlive a request;
- when a lease expires, classify the attempt as lost only after grace and process-liveness checks;
- resume the task from durable state, not from a claim the worker “remembers”;
- never run two active attempts for a single-writer task unless the operation is explicitly designed for speculation;
- reconnect UI clients to current persisted run state after app restart.

### 13.4 Cancellation

Support cancellation at all layers: frontend request, run manager, graph, task scheduler, worker process/subagent, and tools that support cancellation. A cancel request should stop new dispatch immediately, signal active workers, persist a cancellation event, and mark each task honestly (`cancelled`, `failed`, or completed before cancellation). Do not mark a run cancelled while a destructive subprocess is still operating.

### 13.5 Partial success

A run can have successful and failed optional tasks. Model final status explicitly:

- `succeeded`: all required acceptance criteria pass;
- `partial`: usable output exists but one or more required criteria remain unresolved or optional components failed, according to product policy;
- `failed`: no acceptable outcome or required work could not complete;
- `cancelled`: user or policy cancelled the run.

The final answer must summarize required failures and must not claim full success based only on one successful specialist.

---

## 14. Security, prompt injection, and tool governance

Multi-agent systems multiply attack paths because untrusted content can travel from browser pages, files, logs, MCP tools, task descriptions, and other agents into another agent's context.

### 14.1 Trust boundaries

Treat these as untrusted input:

- web pages and downloaded documents;
- issue text, repository comments, code strings, and README instructions from a target repo;
- agent-generated plans/results until validated;
- tool output that contains instructions aimed at the agent;
- MCP server descriptions and tool output unless the server is explicitly trusted;
- artifact files from other workers until checked for scope/type/content.

An agent must not promote untrusted text into system policy merely because another agent repeats it.

### 14.2 Central policy enforcement

All tools should pass through a tool broker or shared policy middleware that checks:

- current user/run permissions;
- agent-specific allowlist and denylist;
- operation risk class;
- file/network/system scope;
- required approval and approval payload hash;
- deadline and cancellation;
- idempotency and duplicate side effects;
- audit event emission;
- output-size and secret-redaction rules.

A worker prompt saying “never delete data” does not replace operating-system, container, tool broker, and user-approval controls.

### 14.3 Least privilege by role

- Researcher: search/read tools, no shell write or repo mutation by default.
- Analyst/reviewer: read-only source, logs, and test results.
- Coding worker: isolated workspace and command allowlist; no implicit host administrator access.
- Test worker: isolated test environment and controlled network access.
- Integrator: merge/apply diff tools but only after policy and validation checks.
- Coordinator: can create tasks and inspect outcomes, but should not automatically inherit every worker's unrestricted tools.

### 14.4 Secret handling and logs

Use secret references, not secrets embedded in task text. Redact keys/tokens from prompts, traces, events, artifact previews, UI logs, and error messages. Store only what is needed for replay and audit. Define retention and deletion policies for sensitive payloads.

---

## 15. UI/UX for multi-agent visibility

Alpha should show the actual state machine, not agent-generated progress prose. A user should be able to understand whether agents are active, waiting, blocked, done, or failed without reading logs.

### 15.1 Recommended views

1. **Run overview:** user goal, selected mode, status, elapsed time, remaining budget, cancel/resume controls.
2. **Live task graph:** task nodes colored by status; arrows labeled with dependency or handoff type; click a node to inspect details.
3. **Agent roster for this run:** role, task, model/provider route (subject to privacy settings), current activity, heartbeat, usage, status.
4. **Event timeline:** task created/assigned/started, tool started/completed, artifact published, handoff, retry, approval, verification, failure.
5. **Artifact panel:** reports, source evidence, patch/diff, test logs, screenshots, generated files, hashes/status.
6. **Failure panel:** readable error, impact, retryability, attempted recovery, next action required.
7. **Approval panel:** exact operation, affected target, risk, approve/reject decision, and audit record.

### 15.2 UI truthfulness rules

- Only show `running` after a task attempt has actually been created and execution has been dispatched.
- Show “queued” separately from “working”.
- Show percent complete only when it has a defensible denominator (e.g., weighted tasks); otherwise show completed/total plus a status message.
- Do not claim “agent is coding” merely because it returned a sentence saying it is coding. Prefer tool/worker events and active attempt status.
- Worker output that failed verification must remain visible as unverified/failed, not be green by default.
- When only partial results are available, label them partial.
- Allow node log details to be collapsed, with redaction before display.

### 15.3 Proposed API contracts

Match the existing API style and auth conventions in the repository. The following are conceptual resource boundaries, not an instruction to break current endpoints.

| Endpoint | Purpose |
|---|---|
| `POST /api/agent-runs` | Create a run from user goal + optional mode/constraints |
| `GET /api/agent-runs/{run_id}` | Get run summary and current status |
| `GET /api/agent-runs/{run_id}/graph` | Return tasks, typed edges, statuses, and layout hints |
| `GET /api/agent-runs/{run_id}/events` | Return paginated events; optionally SSE/WebSocket stream |
| `POST /api/agent-runs/{run_id}/cancel` | Request cancellation |
| `POST /api/agent-runs/{run_id}/resume` | Resume a paused/checkpointed run when valid |
| `POST /api/agent-runs/{run_id}/approvals/{approval_id}` | Resolve a pending approval with an explicit decision |
| `GET /api/agent-runs/{run_id}/artifacts` | List result artifacts and verification state |
| `GET /api/agents` | List enabled agent profiles and safe capability metadata |
| `GET /api/agents/{agent_id}` | Inspect a profile and allowed capabilities (no secrets) |

Do not duplicate existing endpoints if Alpha already has a route with the same purpose. Extend compatibly, document the source of truth, and add contract tests.

---

## 16. Observability and debugging graph

Instrument every workflow run as a trace, each graph node/task attempt as a child span, and each model/tool/handoff call as a correlated operation. Use OpenTelemetry-compatible concepts where possible so Alpha can export to a local viewer or future backend without rewriting all instrumentation.

### 16.1 Minimum event fields

`event_id`, `run_id`, `task_id`, `attempt_id`, `agent_instance_id`, `trace_id`, `span_id`, `parent_event_id`, `event_type`, timestamp, duration (where relevant), status, provider/model route, tool ID, redacted input/output summary, artifact IDs, error category, retry number, token/cost metrics when available, cancellation/approval state.

### 16.2 Events to emit

- `run.created`, `run.mode_selected`, `plan.proposed`, `plan.validated`, `plan.rejected`;
- `task.created`, `task.blocked`, `task.ready`, `task.queued`, `task.started`, `task.heartbeat`;
- `agent.selected`, `agent.spawned`, `agent.handoff_requested`, `agent.handoff_accepted`, `agent.finished`;
- `llm.requested`, `llm.completed`, `llm.failed`;
- `tool.requested`, `tool.approval_required`, `tool.started`, `tool.completed`, `tool.failed`;
- `artifact.created`, `artifact.verified`, `artifact.rejected`;
- `task.retry_scheduled`, `task.retry_exhausted`, `task.cancelled`;
- `verification.started`, `verification.passed`, `verification.failed`;
- `run.paused`, `run.resumed`, `run.completed`, `run.partial`, `run.failed`, `run.cancelled`.

### 16.3 Core metrics

- task success rate by role and task type;
- complete-goal success rate vs a single-agent baseline;
- time-to-first-useful-result and total wall-clock duration;
- average/maximum active workers; queue wait time;
- retries, cancellations, timeouts, stale leases, duplicate dispatch attempts;
- tool success/failure rates, model/provider error rates;
- task-plan invalidity, cycles, dependencies that never become ready;
- total LLM/tool calls and tokens/cost estimate per completed goal;
- percent of result claims with traceable evidence;
- fraction of runs with unverified “success” claims (target zero);
- parallel speedup and coordination overhead;
- merge conflict rate and post-integration test failure rate for code tasks.

### 16.4 Failure graph

For a failure, Alpha should be able to trace:

`user goal → plan node → task → assigned agent instance → model request/tool call → external effect/artifact → validator → failure → retry/repair/terminal status`.

This requires causal IDs; timestamps and plain text logs alone are not enough. Keep logs and trace payloads privacy-conscious and bounded.

---

## 17. Evaluation: prove that multiple agents improve Alpha

Multiple agents cost more and add more failure modes. Alpha should not assume a bigger team is better. Create a benchmark set of representative Alpha tasks and compare single-agent, supervisor, DAG, swarm, and hierarchical modes.

### 17.1 Benchmark categories

- simple conversational task (should usually stay single-agent);
- technical research requiring citations and comparison;
- repo audit with independent read-only analysis branches;
- code edit + test + review task;
- task with one failing worker and a required retry;
- provider rate limit / timeout / offline recovery;
- tool permission denial and approval required;
- cyclic or invalid plan from planner model;
- overlapping file-edit tasks;
- cancellation during a slow tool call;
- app restart after checkpoint with workers in flight;
- conflicting evidence from two research agents;
- adversarial repository/web content containing prompt injection.

### 17.2 Quality metrics

Measure task outcome with objective tests where possible, source/citation correctness for research, and blinded human ratings when semantic judgment is necessary. Track separately:

- goal completion/acceptance criteria pass rate;
- factual/evidence correctness;
- regression rate;
- tool execution correctness;
- plan validity and dependency correctness;
- quality and clarity of final answer;
- time and model/tool usage;
- failure recovery success;
- security and permission violations.

### 17.3 Multi-agent comparison protocol

For each task family:

1. Freeze the prompt, model route, tool set, repository commit, and task fixture.
2. Run a single-agent baseline.
3. Run the candidate multi-agent topology with the same overall model/token/time budget where practical.
4. Evaluate the same acceptance checks and independent scorer.
5. Repeat tasks where stochastic output is material.
6. Compare quality improvements against latency/cost overhead.
7. Keep the multi-agent path only where it improves the relevant outcome or enables a required operational property.

### 17.4 Suggested launch gates

Before enabling a mode broadly, require:

- no unbounded loop or unbounded recursive agent spawning in tests;
- all task statuses eventually become terminal or visibly waiting;
- duplicate event delivery does not produce duplicate dangerous side effects;
- checkpoint/restart tests pass for representative run types;
- cancellation prevents new work and stops supported tools;
- permission policy cannot be bypassed by a subagent;
- graph state survives frontend disconnection and reconnects accurately;
- required failure paths produce user-visible explanations;
- task DAG invalidity is rejected before side effects;
- a measurable improvement or necessary capability is demonstrated against baseline.

---

## 18. Staged implementation roadmap

Implement in order. Every phase needs unit tests plus an end-to-end acceptance test. Do not implement all graph types at once.

### Phase 0 — Repository audit and safety baseline

**Objective:** determine what Alpha already has; preserve working behavior.

Tasks:

- inspect entry points, bot/specialist/subagent/swarm implementations, LangGraph/Deep Agents versions, tool broker, model adapters, API routes, UI event stream, DB/migrations, logging, background tasks, and tests;
- map current code to coordinator, agent registry, scheduler, persistence, tools, UI, and telemetry responsibilities;
- locate duplicate task state/planners/retry loops;
- identify whether multiple agents can currently run concurrently and how results are merged;
- write a compatibility map: keep/reuse/refactor/deprecate; do not delete features;
- create an inventory of routes/UI controls and baseline tests;
- record known failures before changes.

**Exit criteria:** an architecture note with real file paths, dependency versions, existing behavior and regression tests. No behavior changes in this phase.

### Phase 1 — Canonical models and contracts

**Objective:** standardize `AgentProfile`, `TaskSpec`, `TaskResult`, `AgentMessage`, `ArtifactRef`, `AgentEvent`, `RunBudget`, `ErrorEnvelope`, and approval records.

Tasks:

- use Alpha's existing validation library where possible;
- version every serialized schema (`alpha.task.v1`, etc.);
- define strict status enums and legal state transitions;
- define agent/profile capability registry interface;
- add schemas for research, code diff, test result, reviewer finding, and verification output;
- write validation tests with malformed/missing/oversized payloads.

**Exit criteria:** schema validation rejects malformed plans and outputs; no worker result can write arbitrary status transitions.

### Phase 2 — Durable task ledger and events

**Objective:** persist runs, tasks, dependencies, attempts, events, approvals, and artifacts.

Tasks:

- add compatible migrations through the current database framework;
- implement repository/service APIs for legal transitions and queries;
- add unique `task_key` per run and idempotency for dispatch;
- implement append-only events and event pagination;
- attach stable IDs to current agent/tool logs;
- implement run list/details and task state after process restart.

**Exit criteria:** create a run, add tasks/edges, restart backend, and recover exact task/event state. Verify no duplicate dispatch when a creation request is retried.

### Phase 3 — Deterministic DAG validation and scheduler

**Objective:** run a task DAG without depending on an LLM to enforce dependencies.

Tasks:

- topological validation and readable cycle errors;
- calculate readiness from durable task/edge state;
- implement concurrency groups/resource locks;
- implement queue/lease/heartbeat, stale task detection, and retry classification;
- add run-level cancellation and deadlines;
- test required-vs-optional task failure behavior.

**Exit criteria:** independent tasks fan out; dependent tasks wait; cycles are rejected; concurrency limits are respected; stale worker recovery does not duplicate non-idempotent side effects.

### Phase 4 — LangGraph orchestration integration

**Objective:** one durable graph owns high-level flow; task ledger owns the normalized task lifecycle.

Tasks:

- build entry graph for request → classify → plan → validate → schedule → join → verify → synthesize;
- use the installed LangGraph version's checkpointing and dynamic fan-out mechanisms;
- define state reducers and deterministic result ordering;
- implement bounded plan-repair/goal-repair edges;
- keep worker transcripts out of global state unless explicitly needed;
- add checkpoint/resume and failure-injection tests.

**Exit criteria:** graph can pause/restart mid-run, resume from persisted state, and reports accurate progress even if the UI is disconnected.

### Phase 5 — Agent registry, supervisor and specialist routing

**Objective:** run the currently supported specialists through one interface.

Tasks:

- register current specialists with capabilities, tools, model policies, budgets, and context policy;
- implement deterministic capability filtering and auditable selection reasons;
- wrap existing agent loops in adapters rather than rewriting them unnecessarily;
- implement coordinator calls to specialist workers with strict `TaskSpec`/`TaskResult` contracts;
- add context isolation and artifact-based returns;
- prevent duplicate task assignment and recursive spawning.

**Exit criteria:** supervisor delegates to two different specialists, validates each result, handles a worker failure, and returns a final result tied to artifacts/events.

### Phase 6 — Parallel fan-out/fan-in

**Objective:** make independent specialists work simultaneously and safely.

Tasks:

- dispatch ready tasks to bounded async workers;
- support join policies for required and optional tasks;
- isolate working directories for write-capable tasks;
- use one shared budget and cancellation token;
- emit per-task progress updates;
- add deterministic ordering for merged results;
- benchmark with 2 and 3 workers before increasing the cap.

**Exit criteria:** measured wall-clock improvement on parallelizable fixtures, no shared-state corruption, and correct handling of one worker's failure while others succeed.

### Phase 7 — Handoffs / swarm mode

**Objective:** make active specialist changes explicit and bounded.

Tasks:

- add an allowlisted handoff edge/capability graph;
- validate destination/permissions/context policy;
- persist active agent, handoff count, and handoff reason;
- add cycle/hop protection and unavailable-target fallback;
- expose handoffs as UI events;
- use a checkpointer for multi-turn persistence.

**Exit criteria:** router → researcher → coder/tester transitions are visible and resumable; forbidden or cyclic handoffs are rejected.

### Phase 8 — Verification, review, and repair loops

**Objective:** do not equate worker claims with completion.

Tasks:

- implement acceptance checker by task type;
- require tests/schema/source checks where applicable;
- create repair tasks from exact failed criteria;
- bound repair rounds and shared budgets;
- distinguish success/partial/failure in API/UI;
- ensure independent reviewer results are evidence-linked.

**Exit criteria:** an intentionally wrong worker output is rejected; repair succeeds within limit or the final result honestly reports unresolved failure.

### Phase 9 — UI graph, observability, and recovery tooling

**Objective:** make multi-agent execution understandable and debug-able.

Tasks:

- graph endpoint and live events feed;
- task/agent views, logs, artifacts, approval and failure panels;
- event/correlation IDs across LLM and tool calls;
- safe log redaction and output size limits;
- health and scheduler diagnostics;
- run resume/cancel UI; restart/reconnect tests;
- metrics dashboard for cost, speed, failure, and quality.

**Exit criteria:** an operator can select a failed task and trace it to a tool/model error and artifact; user sees truthful running/queued/blocked status.

### Phase 10 — Autopilot rollout and policy tuning

**Objective:** enable autonomous multi-agent operation gradually.

Tasks:

- add mode selection thresholds; keep single-agent as baseline/fallback only when explicitly chosen by policy and surfaced in status;
- configure quotas by provider and local resource health;
- add benchmark/evaluation suite to CI or a repeatable local evaluation command;
- run shadow mode or manual confirmation for new execution patterns;
- enable background/long-running tasks only when recovery and cancellation pass acceptance tests;
- document feature flags, rollback, and migration.

**Exit criteria:** documented evaluation improvement, tested rollback path, and no regression in existing bot, subagent, swarm, chat, tool, or UI features.

---

## 19. Test plan: minimum required coverage

### 19.1 Unit tests

- Pydantic/schema validation for agent profiles, task specs/results, handoffs, artifacts, budgets, and error envelopes;
- valid topological ordering and cycle detection;
- status transition rules;
- capability filtering and missing-role handling;
- deterministic budget and concurrency calculations;
- unique task key and idempotency rules;
- context filtering and secret redaction;
- task result reducer/ordering and conflict detection;
- retry classification and backoff bounds.

### 19.2 Integration tests

- supervisor calls the assigned worker and receives a structured result;
- fan-out starts N independent tasks concurrently up to configured cap;
- join waits for required tasks and honors optional failures;
- dependency failure blocks downstream work;
- swarm handoff updates active agent and persists it;
- nested subgraph cannot exceed parent budget/depth;
- failed tool result routes to retry/repair correctly;
- approval interrupt suspends execution and resumes only after a valid decision;
- checkpoint restores run state and avoids duplicate side effects;
- event stream reconnect yields current state plus subsequent events without losing transitions.

### 19.3 Fault-injection tests

Inject:

- model timeout and provider rate limit;
- agent process exit mid-task;
- database unavailable during transition;
- duplicate dispatch request;
- delayed or duplicated event delivery;
- missing/corrupt artifact;
- invalid worker schema;
- LLM planner produces cyclic DAG;
- worker attempts forbidden tool;
- coordinator tries to spawn excessive children;
- UI client disconnects;
- application restarts while work is running;
- cancellation arrives during tool execution;
- two coding tasks touch the same file;
- malicious prompt injection in web page/repository text.

### 19.4 End-to-end scenarios

**Scenario A: parallel research** — create three bounded research tasks, run them concurrently, verify source evidence, merge findings, and display citations/uncertainty.

**Scenario B: coding fix** — read-only analysis workers identify relevant code/tests; one coding worker changes isolated worktree; tests run; reviewer inspects diff; integrator merges only if required checks pass.

**Scenario C: tool failure** — one task receives a rate limit; scheduler backs off; the UI shows queued/retry; the run recovers without duplicate write action.

**Scenario D: crash recovery** — stop the backend after at least one task artifact is written; restart and verify the run resumes or reconciles statuses correctly.

**Scenario E: approval** — worker requests a risky operation; run pauses; reject means no execution; approve only releases the exact approved action.

**Scenario F: swarm cycle** — create A→B→A handoff loop; graph guard detects hop/cycle limit and emits a visible failure.

**Scenario G: budget exhaustion** — workers hit shared budget; scheduler stops new dispatch and returns a partial result with unresolved criteria.

---

## 20. Rollout, compatibility, and non-goals

### 20.1 Non-negotiable compatibility rules

- Do not remove Alpha's current features just to make the graph implementation simpler.
- Do not rewrite the entire agent runtime before documenting existing behavior and test coverage.
- Do not create a second hidden task ledger inside each subagent.
- Do not allow the UI to be the source of truth for execution state.
- Do not silently switch model providers or agent types when the configured target fails; report fallback explicitly and require policy permission.
- Do not install all comparison frameworks (LangGraph extensions, AutoGen, CrewAI, ADK, Microsoft Agent Framework, etc.) as dependencies. Study patterns; keep the runtime stack focused.
- Do not make a graph database, broker, or cloud service mandatory until a measured requirement proves it is necessary.
- Do not claim multi-agent work occurred unless there are persisted agent/task execution events.

### 20.2 Feature flags

Add reversible flags consistent with Alpha's settings system:

- `multi_agent_orchestration_enabled`;
- `task_dag_parallel_execution_enabled`;
- `swarm_handoffs_enabled`;
- `hierarchical_delegation_enabled`;
- `independent_verifier_enabled`;
- `persistent_run_resume_enabled`;
- `live_agent_graph_ui_enabled`.

Start with DAG execution and supervisor mode; enable swarm handoffs after routing/cycle tests; enable hierarchical delegation last.

### 20.3 Recommended first milestone

The first useful milestone is not “100 agents work together.” It is:

> Alpha can take a moderately complex goal, create a validated task DAG, run two independent specialist tasks concurrently, persist every task/result/event, join the results, verify required acceptance criteria, survive a backend restart, and show honest progress/failure in the UI.

Once this passes, add explicit swarm handoff; then hierarchical teams and richer capability graphs.

---

## 21. Practical example: Alpha implements a bug fix with multiple agents

User: “Find why API tests fail, fix the bug, run tests, and explain the changes.”

### Step 1 — classify and plan

Coordinator identifies this as a coding task that benefits from specialist work. Planner proposes:

- T1: inspect failure logs and name failing tests (read-only);
- T2: analyze relevant implementation paths (read-only);
- T3: inspect test fixture/configuration and propose likely causes (read-only);
- T4: implement the selected fix (depends on T1/T2/T3 and coordinator selection);
- T5: run relevant tests and type/lint checks (depends on T4);
- T6: independently review the diff and evidence (depends on T4; can overlap with T5 if the test/review inputs permit);
- T7: integrate/final verify (depends on T5/T6).

### Step 2 — validate and run DAG

T1–T3 execute concurrently within budget. Each has read-only access, a task-scoped context, and an output contract. Their reports refer to exact paths, test names, logs, and source evidence. The coordinator reconciles conflicts and selects a diagnosis; it must not blindly average incompatible hypotheses.

### Step 3 — write in isolation

T4 runs as a coding agent in a dedicated worktree. It returns a patch or commit plus files changed and commands executed. The working tree belonging to the user is not reset or overwritten.

### Step 4 — test and review

T5 executes the required test set in the isolated environment and publishes test logs. T6 reviews the exact diff against requirements and security policy. Both results are structured and independent.

### Step 5 — verification gate

The verifier confirms:

- required tests actually executed and passed;
- no unexpected files changed;
- patch matches the accepted task scope;
- lint/type checks required by project policy passed;
- reviewer has no unresolved blocker-level finding;
- artifacts and final commit/diff are available.

Only then can Alpha report success. If tests fail, the verifier creates a repair task with the exact failing assertion/log and routes back to T4, bounded by the shared run budget.

### Step 6 — explain result

Alpha shows a graph with T1–T3 completed in parallel, T4 implemented, T5/T6 checked, and T7 verified. The response includes actual changes, tests run, test results, artifact links, and any limitations. If merge/integration was not performed, Alpha says so explicitly.

---

## 22. Copy-paste implementation prompt for Alpha's coding agent

Use the following prompt in the coding agent that can inspect and modify the Alpha repository. It intentionally instructs the agent to audit first and implement incrementally instead of replacing the codebase blindly.

```text
ROLE
You are the senior staff-level architect and implementation engineer responsible for adding robust multi-agent graph orchestration to the existing Alpha AI Agent repository:
https://github.com/itsPremkumar/alpha

MISSION
Implement an Alpha-owned multi-agent orchestration layer that unifies the existing bot mode, specialist agents, subagents, and swarm mode with a validated task/dependency DAG and explicit LangGraph execution flow. Use Deep Agents selectively as a worker harness where it adds value. The goal is reliable, observable, resumable, safe, genuinely parallel multi-agent work—not an architecture diagram or simulated progress.

NON-NEGOTIABLE RULES
1. Audit the repository, installed package versions, current architecture, existing modes, UI, APIs, tool broker, persistence, and tests BEFORE editing code.
2. Preserve all working Alpha features. Do not delete or silently replace current agent/tool/model/UI integrations.
3. Do not blindly upgrade LangGraph, LangChain, Deep Agents, Pydantic, or the database stack. Confirm actual versions, compatibility, and installed APIs first.
4. Do not install multiple alternative multi-agent frameworks as dependencies just because they are researched. LangGraph is the primary execution runtime. Treat other frameworks as reference patterns unless there is a repository-backed reason to add one.
5. Establish one canonical task ledger and one authoritative scheduler. Do not let Alpha and Deep Agents maintain competing cross-agent task state or independently spawn unlimited children.
6. The model may propose plans; deterministic code validates DAGs, transitions, permissions, budgets, concurrency, approvals, retries, and side effects.
7. Multi-agent execution must be real. Every task/agent/model/tool action should have persisted IDs and lifecycle events. Never fake task progress in the UI or claim a worker ran without evidence.
8. All loops, retries, handoffs, debate cycles, task counts, and delegation depth must be bounded by code.
9. Use least-privilege tools, task-scoped contexts, secret redaction, and explicit approvals for risky side effects.
10. Add tests and execute the relevant test suites after each phase. Be truthful about tests that cannot run in the current environment.

REFERENCE MATERIAL
- Existing Alpha graph engineering plan: ALPHA_GRAPH_ENGINEERING_IMPLEMENTATION_PLAN.md, if available locally.
- LangGraph: https://github.com/langchain-ai/langgraph
- LangGraph Graph API: https://docs.langchain.com/oss/python/langgraph/graph-api
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- Deep Agents: https://github.com/langchain-ai/deepagents
- Deep Agents subagents: https://docs.langchain.com/oss/python/deepagents/subagents
- LangGraph supervisor patterns: https://github.com/langchain-ai/langgraph-supervisor-py
- LangGraph swarm patterns: https://github.com/langchain-ai/langgraph-swarm-py
- OpenAI manager vs handoff concepts: https://developers.openai.com/api/docs/guides/agents/orchestration
- Microsoft Agent Framework orchestration comparison: https://learn.microsoft.com/agent-framework/workflows/orchestrations/
- Multi-agent collaboration survey: https://arxiv.org/abs/2501.06322
- Communication-centric multi-agent survey: https://arxiv.org/abs/2502.14321

REQUIRED ARCHITECTURE
A. AgentRegistry
- Typed profiles: ID/name/description/role/capabilities/skills/allowed tools/model policy/runtime kind/context policy/concurrency cap/timeouts/LLM and tool budgets/enabled/version.
- Capability-first eligibility filtering. Persist why an agent was selected.
- Never use an agent description as an authorization boundary; enforce every tool permission in the shared tool broker.

B. Canonical Task/DAG Model
- Typed tasks with task/run/parent IDs, objective, assigned role, dependencies, required/optional, output schema, acceptance criteria, policies, resource key, deadlines, retries, budget, state, artifact references, errors.
- Validate acyclicity, references, reachable root/terminal states, output contracts, permissions, budgets, dependency correctness, and resource conflicts before dispatch.
- Central legal status transitions. Use statuses similar to proposed/validated/blocked/ready/queued/running/retry_wait/needs_approval/succeeded/failed/cancelled/skipped.
- Distinguish data dependency, control dependency, review dependency, resource conflict, delegation, and causal links.

C. Persistence and Events
- Reuse Alpha's existing DB and migration conventions. Do not introduce a second database as a competing source of truth.
- Persist runs/tasks/edges/attempts/agent instances/artifacts/approvals/events, with indexed query fields and schema-versioned JSON payloads where appropriate.
- Use stable run_id/task_id/attempt_id/agent_instance_id/artifact_id/event_id/trace_id.
- Use LangGraph checkpoints for graph-state recovery and the canonical task ledger for queryable lifecycle; define consistency and idempotency rules.
- Emit append-only lifecycle events. Redact credentials and sensitive payloads.

D. One OrchestrationService backed by LangGraph
- Top-level flow: request -> complexity/mode classification -> plan -> deterministic validation -> schedule/dispatch -> join -> verify -> bounded repair if allowed -> synthesize -> persist terminal outcome.
- Provide single-agent, supervisor, task-DAG fan-out/fan-in, handoff/swarm, and verifier/repair patterns through shared infrastructure.
- Implement only the simplest useful patterns first; hierarchical delegation and debates must be feature-flagged and bounded.
- Dynamic task dispatch must use supported APIs for the installed LangGraph version; do not invent APIs or generate executable code from model output.
- Define state reducers and deterministic output ordering. Parallel workers must not overwrite a shared status/message/result field without explicit merge behavior.

E. Worker Adapters
- Wrap existing Alpha specialists and existing Deep Agents capabilities rather than rewriting them unnecessarily.
- Use strict TaskSpec/TaskResult contracts.
- Default delegated context to isolated or filtered. Prefer artifact references over copied logs/full conversation history.
- Deep Agent internals may plan within an assigned task, but the Alpha parent task ledger remains authoritative for cross-agent dependencies, run budget, cancellation, and acceptance.
- Worker tools must obey the same permission broker, cancellation, timeout, and telemetry policies.

F. Scheduling and Parallelism
- Deterministic ready-task selection, resource locks, bounded async concurrency, lease/heartbeat recovery, deadlines, backoff/jitter for transient errors, and shared run budgets.
- Begin with configurable conservative limits (e.g., 2–3 concurrent workers locally, small child-task cap, depth 2, bounded retries) and tune only after benchmarks on actual Alpha hardware/providers.
- Start with parallel read-only research/analysis. For coding, isolate writes with Git worktrees or equivalent; run tests and merge/integrate through explicit nodes.
- Prevent duplicate non-idempotent side effects with idempotency keys and external-state reconciliation.
- Cancellation stops new dispatch and signals active workers/tools. A run must not be marked cancelled while a dangerous process still operates.

G. Handoff/Swarm
- Explicit allowlisted transition graph between roles, active-agent state, handoff reason, context policy, hop count/cycle detection, destination validation, and fallback when a specialist is unavailable.
- Do not confuse serial handoff with parallel DAG execution. Expose both clearly.

H. Verification and Safety
- Every goal has explicit acceptance criteria.
- Prefer deterministic verification: tests, schema validation, artifact hashes/existence, source checks, diff checks, policy checks. Use an independent reviewer for semantic issues when warranted.
- Worker self-reports are not sufficient proof of completion.
- Repair is evidence-directed, bounded, and shares the parent budget. Final outcomes must distinguish succeeded/partial/failed/cancelled.
- Add human approval/interrupt gates for risky actions; approval is tied to exact payload and cannot be granted by an agent.
- Treat external pages, repository text, files, and other agent output as untrusted data. Apply least privilege and redact secrets in logs/traces/UI.

I. UI and APIs
- Reuse/extend existing endpoints rather than duplicating them. Expose run detail, task graph, events, artifacts, cancel/resume, pending approvals, and enabled agents as appropriate.
- Add a real task graph/live timeline, per-agent status, queued/running/blocked/completed/failed indicators, usage metrics, and failure explanation.
- UI must reflect persisted backend state, not pretend progress based on agent prose. Reconnect must restore current state.

IMPLEMENTATION PHASES
Phase 0: repository audit, architecture note, baseline tests, compatibility map.
Phase 1: typed schemas/contracts and unit tests.
Phase 2: durable task ledger, migrations, append-only events, IDs/idempotency.
Phase 3: DAG validation, ready scheduler, concurrency/resources/retries/cancel.
Phase 4: LangGraph orchestration and checkpoint/resume.
Phase 5: agent registry, adapters, supervisor delegation.
Phase 6: true parallel fan-out/fan-in, task-scoped workspaces, join policy.
Phase 7: bounded swarm handoffs and active-agent persistence.
Phase 8: independent verification, repair loop, partial-failure reporting.
Phase 9: graph UI, traces, log redaction, restart/reconnect tooling.
Phase 10: evaluation/feature flags/gradual autopilot rollout.
Do not skip phases because of time. Complete vertical slices with tests.

REQUIRED TESTS
- schema and legal status transitions;
- DAG topological ordering and cycle rejection;
- dependency readiness and join semantics;
- two/three parallel workers obey concurrency limits;
- one worker failure does not erase another worker's result;
- no duplicate side effect on retry/replayed dispatch;
- lost worker/expired lease recovery;
- checkpoint/restart and frontend reconnect;
- cancellation during tool call;
- bounded delegation and handoff cycle guard;
- permissions and approval enforcement from subagents;
- shared-file conflict/worktree isolation;
- acceptance verification rejects wrong or unsupported worker output;
- truthful partial/failure final state;
- no secret leakage to events/logs/UI;
- existing Alpha regression tests and bot/subagent/swarm/UI flows still pass.

DELIVERABLES
1. Architecture/audit note listing actual code paths and decisions.
2. Small, reviewable implementation commits/patches; do not do a one-shot rewrite.
3. Migrations and models.
4. Orchestration/scheduler/worker-adapter implementation.
5. Updated API/UI/event-stream behavior.
6. Unit/integration/fault-injection/E2E tests.
7. A test report with exact commands, pass/fail counts, and known blockers.
8. README/technical docs describing modes, status semantics, recovery, limits, configuration, and rollback.
9. A final verification report listing what truly works, what is partial, and what remains.

FIRST RESPONSE BEFORE CODING
Inspect the repository and report:
- current framework versions and entry points;
- where bot/specialist/subagent/swarm modes are implemented;
- existing task/persistence/event model and problems;
- minimal compatible architecture proposal;
- test commands to run;
- concrete phase-1 changes.
Then proceed with the smallest useful vertical slice. Do not stop after writing a plan if repository write access and execution are available.
```

---

## 23. Design decisions and common mistakes to avoid

### Mistake 1: “More agents means more intelligence”

Extra workers can improve coverage, independent checks, and wall-clock time for separable work. They also add model calls, context handling, coordination overhead, and new failure paths. Decide per task and measure outcome against a single-agent baseline.

### Mistake 2: every agent shares the full chat history

This increases token usage, duplicates irrelevant tool output, leaks unrelated context, and makes results hard to reason about. Use task-scoped context, shared summaries, and artifacts. Full history should be an exception.

### Mistake 3: all agents write to one shared state object

Parallel updates can overwrite fields or produce nondeterministic order. Use per-task result records, unique artifacts, explicit reducers, and deterministic joins.

### Mistake 4: “swarm” implemented as endless group chat

A swarm is useful for controlled handoffs, but a free-form group chat can cycle and waste calls. Use a task DAG for independent work and put explicit termination rules around debates and handoffs.

### Mistake 5: one LLM both plans and enforces the plan

The model can propose, but a deterministic validator and scheduler must enforce dependencies, budgets, permissions, and status transitions.

### Mistake 6: letting every subagent recursively spawn agents

Nested delegation can grow multiplicatively. Use parent-owned quotas, depth caps, total child caps, and explicit permission to delegate from each role.

### Mistake 7: using logs as state

Logs explain what happened; the task store says what is currently true. Event logs are append-only, but current state is a governed projection. Implement both and define the update path.

### Mistake 8: trusting worker self-report

A worker can say “tests passed” without executing tests. Require a tool/test event, artifact, and verifier result.

### Mistake 9: retrying non-idempotent work blindly

A tool can complete externally but lose its response locally. Use idempotency keys and reconcile external state before retry.

### Mistake 10: immediately adopting a second orchestration runtime

Alpha already uses LangGraph and Deep Agents. Introduce another framework only if a clear missing requirement and evaluated benefit justify adapter/dependency complexity.

---

## 24. Further research references

The links below are the key primary documentation and research used to shape this plan. Public documentation and APIs change; verify the exact release docs and installed package version when implementing.

### LangGraph / Deep Agents

1. LangGraph repository — https://github.com/langchain-ai/langgraph
2. LangGraph Graph API — https://docs.langchain.com/oss/python/langgraph/graph-api
3. LangGraph persistence — https://docs.langchain.com/oss/python/langgraph/persistence
4. LangGraph durable execution — https://docs.langchain.com/oss/python/langgraph/durable-execution
5. LangGraph interrupts — https://docs.langchain.com/oss/python/langgraph/interrupts
6. LangGraph Supervisor repository and recommendation to consider manual tool-based supervisors — https://github.com/langchain-ai/langgraph-supervisor-py
7. LangGraph Swarm repository — https://github.com/langchain-ai/langgraph-swarm-py
8. Deep Agents repository — https://github.com/langchain-ai/deepagents
9. Deep Agents overview — https://docs.langchain.com/oss/python/deepagents/overview
10. Deep Agents subagents — https://docs.langchain.com/oss/python/deepagents/subagents
11. Deep Agents deep-research example — https://github.com/langchain-ai/deepagents/tree/main/examples/deep_research

### Alternative framework patterns (comparative references)

12. OpenAI Agents SDK — https://developers.openai.com/api/docs/guides/agents/sdk
13. OpenAI orchestration and handoffs — https://developers.openai.com/api/docs/guides/agents/orchestration
14. OpenAI Agents SDK Python repository — https://github.com/openai/openai-agents-python
15. Microsoft Agent Framework — https://github.com/microsoft/agent-framework
16. Microsoft Agent Framework orchestration patterns — https://learn.microsoft.com/agent-framework/workflows/orchestrations/
17. Microsoft Agent Framework workflow concepts — https://learn.microsoft.com/en-us/agent-framework/journey/workflows
18. AutoGen AgentChat team patterns — https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/
19. AutoGen maintenance notice and recommendation to use Microsoft Agent Framework for new projects — https://github.com/microsoft/autogen
20. Google ADK multi-agent systems — https://adk.dev/agents/multi-agents/
21. Google ADK workflow agents — https://adk.dev/agents/workflow-agents/
22. CrewAI Crews — https://docs.crewai.com/en/concepts/crews
23. CrewAI Flows — https://docs.crewai.com/en/concepts/flows

### Research on collaboration and communication topology

24. Tran et al., *Multi-Agent Collaboration Mechanisms: A Survey of LLMs* (2025) — https://arxiv.org/abs/2501.06322
25. Yan et al., *Beyond Self-Talk: A Communication-Centric Survey of LLM-Based Multi-Agent Systems* (2025) — https://arxiv.org/abs/2502.14321
26. Aratchige & Ilmini, *LLMs Working in Harmony: A Survey on the Technological Aspects of Building Effective LLM-Based Multi Agent Systems* (2025) — https://arxiv.org/abs/2504.01963
27. Google Research, *Improving Multi-Agent Debate with Sparse Communication Topology* (2024) — https://research.google/pubs/improving-multi-agent-debate-with-sparse-communication-topology/

### Reliability and observability concepts

28. OpenTelemetry traces — https://opentelemetry.io/docs/concepts/signals/traces/
29. OpenTelemetry context propagation — https://opentelemetry.io/docs/concepts/context-propagation/
30. Python asyncio tasks and cancellation — https://docs.python.org/3/library/asyncio-task.html

---

## Final recommendation

For Alpha, the most valuable next step is **not** to maximize the number of agents. It is to build one reliable, observable multi-agent execution system in which:

- the coordinator chooses a pattern suited to the goal;
- the planner produces a validated task DAG;
- the scheduler runs ready tasks within deterministic resource and budget limits;
- specialist workers receive scoped contexts and return typed results/artifacts;
- LangGraph controls the high-level workflow and checkpoint/resume behavior;
- Deep Agents are reused selectively rather than allowed to create a competing control plane;
- swarm handoffs, parallel execution, and hierarchical teams remain distinct patterns;
- reviewers and tests verify outcomes independently;
- every transition, tool call, artifact, and failure is observable;
- the user sees truthful status and a verifiable final result.

Implement the small vertical slice first: **two parallel specialists → durable task records → join → independent verification → visible UI state → crash/restart test**. Expand to handoff/swarm, then hierarchical delegation only after that path is correct and measurable.
