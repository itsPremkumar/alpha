# Alpha — Advanced Multi-Agent Architecture & Orchestration Plan

**Document type:** Research-backed implementation blueprint  
**Target:** Alpha AI Agent (`github.com/itsPremkumar/alpha`)  
**Implementation language:** Python-first  
**Primary objective:** Upgrade the existing multi-agent architecture without deleting or breaking working capabilities; preserve Bot Mode and integrate current tools, agents, memory, skills, MCP, UI, and runtime.  
**Research checked:** 11 October 2026  
**Status:** Architecture plan, not a claim that the features are already implemented.

---

## 0. Executive Summary

Alpha should evolve into a **hybrid, graph-governed, event-driven multi-agent runtime**. It should not become a large uncontrolled group chat in which every agent talks to every other agent. The system should choose the lightest execution strategy that can reliably complete the user's task, then scale up only when task complexity, uncertainty, parallelism, or verification needs justify it.

The central idea is to separate **reasoning** (what should be done), **orchestration** (who may do it and in what order), **execution** (tools, code, browsers, APIs), **evidence** (what proves the work succeeded), and **governance** (what actions are permitted). Every existing Alpha mode can use the same runtime services instead of maintaining different and inconsistent execution loops.

### Target result

Alpha should be able to:

1. Understand the user's outcome and constraints, inspect the relevant capabilities, and select a suitable execution policy.
2. Break a goal into verifiable tasks, model task dependencies as a directed acyclic graph (DAG) where possible, and revise the graph when new information changes the plan.
3. Assign tasks to specialized agents using capability, measured success, tool access, model suitability, availability, latency, and cost—not just agent names or model size.
4. Execute independent tasks concurrently, serialize conflicting tasks, and isolate coding tasks in separate worktrees or sandboxes.
5. Communicate through typed task contracts, durable events, and versioned shared artifacts rather than passing the entire transcript to every agent.
6. Persist the run state, recover after process or machine restart, resume interrupted tasks, and prevent duplicate side effects where technically possible.
7. Check results with deterministic tests, evidence checks, independent reviews, and task-specific acceptance criteria before declaring completion.
8. Show the user a real-time view of plans, agents, tool calls, blockers, evidence, failures, retries, budgets, and the final completion status.
9. Learn from measured outcomes by updating capability statistics and proposing prompt/tool/workflow improvements—without silently rewriting its own production policies.
10. Work in local-first / low-cost configurations, while applying the same resource budgets to free, local, and optional paid model providers.

**Important boundary:** a stronger multi-agent harness can improve task completion, reliability, and autonomy, but a software architecture alone does not establish AGI or ASI. Alpha should measure concrete capabilities and reliability instead of using those labels as acceptance criteria.

---

## 1. Research Findings and Architectural Implications

This plan draws on primary documentation and engineering write-ups current at the research date. The sources offer patterns and design lessons, not proof that importing any one framework will automatically make Alpha better.

### 1.1 Use a hybrid of workflow control and dynamic agents

Anthropic distinguishes fixed workflows from agents that dynamically choose their actions. Its production multi-agent research system uses an orchestrator-worker design: a lead plans, specialists explore in parallel, and the lead decides whether more work is required before synthesis and citation. It also reports failure modes from excessive spawning, vague task boundaries, duplicate research, unbounded exploration, and noisy inter-agent updates. Those lessons point to **bounded delegation, explicit task contracts, adaptive fan-out, and evidence-backed stopping rules** for Alpha. The performance figures in that post are internal to Anthropic's own evaluation and should not be treated as a prediction for Alpha.

Source: [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### 1.2 More than one orchestration pattern is useful

Microsoft Agent Framework documents sequential, concurrent, handoff, group-chat, and Magentic (manager-coordinated) patterns, along with human approval/request-information interactions. Its guidance also distinguishes open-ended agents from workflows where the path should be explicit. Alpha should expose multiple execution strategies through one common runtime instead of forcing all work into a single swarm design.

Sources: [Microsoft Agent Framework overview](https://learn.microsoft.com/en-us/agent-framework/overview/), [workflow orchestrations](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/), [handoff orchestration](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/handoff)

### 1.3 Durable state is a first-class capability

LangGraph's persistence documentation distinguishes checkpointed thread state (short-term/run state, interruption, time travel, recovery) from longer-lived shared stores. Alpha needs the same conceptual distinction whether it uses LangGraph directly or its own adapters: a task's active execution state is not the same thing as durable user/project memory.

Source: [LangGraph — Persistence](https://langchain-ai.github.io/langgraph/concepts/durable_execution/)

### 1.4 Agent interoperability needs two separate interfaces

MCP is designed to expose tools, resources, and prompts to clients. A2A is designed for interoperable communication between agent applications. Alpha should use MCP for tool/capability integration and consider A2A for remote/external agent collaboration, but neither protocol should replace Alpha's internal task scheduler, trust model, or permission checks.

Sources: [MCP project](https://modelcontextprotocol.io/), [MCP server concepts](https://java.sdk.modelcontextprotocol.io/latest/server/), [A2A Protocol](https://a2a-protocol.org/latest/)

### 1.5 Observability must explain the complete run

OpenAI Agents SDK tracing illustrates the value of recording agent runs, turns, model generations, function calls, guardrail checks, and handoffs as related spans. OpenTelemetry supplies general conventions for correlating traces, logs, and metrics. Alpha should be provider-independent and adopt a trace model that links a user request to the plan, child tasks, agents, LLM calls, tool calls, retries, approvals, artifacts, and final evaluation.

Sources: [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-js/guides/tracing/), [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)

### 1.6 Safety is not a final prompt; it is an execution boundary

OWASP's LLM guidance identifies excessive agency as a risk when an agent has more functionality, permission, or autonomy than the task requires. Alpha's supervisor must not be able to bypass tool permissions simply because a task was delegated to a subagent. Permissions should be enforced by the runtime/tool gateway, not trusted to model instructions alone.

Source: [OWASP — Top 10 for LLM Applications (Excessive Agency)](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-2023-v09.pdf)

### 1.7 Framework strategy for Alpha

Alpha already has a Python harness and has used LangChain/LangGraph/Deep Agents patterns. Do not migrate the entire project just to follow a current framework trend. First add clear internal contracts and adapter boundaries. Reuse the existing LangGraph runtime where it already works; prototype new ideas behind interfaces. Microsoft states that AutoGen is in maintenance mode and directs new users to Microsoft Agent Framework, so AutoGen should be evaluated as a source of patterns rather than selected as a new strategic dependency by default.

Source: [Microsoft AutoGen repository and migration guidance](https://github.com/microsoft/autogen)

---

## 2. Non-Negotiable Design Rules

These requirements govern every phase of implementation.

1. **Additive, not destructive.** Inventory existing Alpha features first. Preserve Bot Mode, subagents, swarm behavior, skills, MCP, memory, models/providers, tools, UI actions, API routes, and current user workflows. Make new behavior opt-in until tests demonstrate compatibility.
2. **One runtime, many policies.** Bot Mode, specialist delegation, swarm, graph workflows, coding mode, research mode, and autopilot must share task state, tracing, budgets, permissions, checkpoints, and result verification.
3. **The model proposes; the runtime disposes.** An LLM may propose subtasks, routing, retries, or a plan edit. Deterministic runtime code validates the proposal against schemas, permissions, budgets, dependencies, and risk policies before accepting it.
4. **Every task has an observable outcome.** Each task needs a goal, inputs, owner, expected artifact, acceptance criteria, timeout, budget, and terminal state. “Agent sent a message” is not completion.
5. **No fake progress.** UI status must come from runtime events. Never show “completed” merely because the model returned fluent text.
6. **Evidence before success.** Completion requires an evaluation policy appropriate to the task: tests for code, source references for research, assertions for data transformations, and confirmation for irreversible external actions.
7. **Bounded autonomy.** Every run has limits for depth, task count, concurrency, time, retries, tokens/cost, tool calls, disk, and network access.
8. **Isolation by default for conflicts.** Agents that can edit the same files, database records, or external resources must not race without an explicit coordination strategy.
9. **No hidden silent fallback.** When a model, tool, memory backend, or agent fails, record the error and expose the change in execution strategy. Fallbacks must be policy-approved and visible.
10. **No production self-modification without review.** Agents may create proposed patches to prompts, tools, skills, and policies; tests and an approval/merge gate must control activation.
11. **Local-first design.** The core scheduler, task store, event log, policy enforcement, and health checks must work without an expensive hosted service. Optional distributed infrastructure can be added later.
12. **Reliability over agent count.** More agents are not automatically better. Spawn only when expected benefit is higher than coordination and resource cost.

---

## 3. Target Architecture: Alpha Multi-Agent Control Plane

### 3.1 High-level architecture

```mermaid
flowchart TB
    U[User / UI / API / Bot Mode] --> ING[Request Intake & Intent Contract]
    ING --> POL[Policy, Permissions & Risk Gate]
    POL --> SUP[Executive Orchestrator]
    SUP --> PLAN[Planner / Task Graph Builder]
    PLAN --> SCHED[Scheduler & Resource Manager]
    SCHED --> REG[Agent Capability Registry]
    REG --> POOL[Execution Pool]
    POOL --> R[Research Agents]
    POOL --> C[Coding / Terminal Agents]
    POOL --> D[Data / Browser / API Agents]
    POOL --> V[Verifier / Critic / Test Agents]
    R --> BUS[Typed Event Bus + Artifact Store]
    C --> BUS
    D --> BUS
    V --> BUS
    BUS --> EVAL[Evidence & Acceptance Evaluator]
    EVAL -->|Need more work| SUP
    EVAL -->|Pass| OUT[Result Composer & User Update]
    EVAL -->|Blocked / Approval| HITL[Human Approval / Clarification]
    HITL --> SUP
    SUP <--> STATE[Durable Run State / Checkpoints / Event Log]
    POOL <--> TOOLS[Tool Gateway: MCP / APIs / Shell / Browser]
    TOOLS --> SANDBOX[Permissioned Sandboxes / Worktrees]
    OBS[Tracing, Logs, Metrics, Cost & Health] --- ING
    OBS --- SUP
    OBS --- SCHED
    OBS --- POOL
    OBS --- EVAL
```

### 3.2 Architectural layers and responsibilities

| Layer | Responsibility | Should not do |
|---|---|---|
| Interaction layer | UI, API, Bot Mode, CLI, streaming updates | Own task scheduling logic separately per interface |
| Intent/contract layer | Turn the request into a structured objective, constraints, output format, and success criteria | Assume an ambiguous goal is fully specified |
| Policy gateway | Permissions, risk, approval requirements, isolation level, spend limits | Trust model-generated claims that an action is safe |
| Executive orchestrator | Decide execution strategy, maintain goal-level state, approve replans, synthesize results | Directly execute arbitrary shell or external side effects |
| Planner | Decompose goals, define dependencies, acceptance criteria, expected artifacts | Mark its own plan complete without evidence |
| Scheduler | Ready queue, priorities, concurrency, locks, quotas, backpressure, retries | Make semantic judgments based solely on queue order |
| Agent registry/router | Discover eligible agents and choose based on capabilities and history | Route only by hard-coded agent name |
| Agent runtime | Invoke model, tools, skill, instructions, sandbox, and local loop | Bypass runtime policy or write shared state without contracts |
| Event/artifact layer | Durable event log, task results, artifacts, provenance, state transitions | Use unstructured chat transcripts as the only source of truth |
| Evaluation layer | Deterministic assertions, independent critique, evidence validation, quality score | Treat consensus or self-reported confidence as proof |
| Tool gateway | Centralized schemas, permissions, approvals, timeouts, rate limits, audit records | Grant a child agent more privileges than its parent task permits |
| Observability | Correlated traces, logs, status, health, spend, alerts, replay | Log secrets or expose private reasoning by default |
| Governance | Version prompts/skills/policies, tests, review, rollback | Silently update the live system based on one run |

### 3.3 Control plane versus data plane

Separate **control plane** logic from **data plane** execution.

- Control plane: objective state, planning, dispatch, priorities, permissions, agent selection, retries, cancellation, approvals, and completion decisions.
- Data plane: agents calling models, reading/writing files, using tools, browsing, executing code, and producing artifacts.
- The control plane gives agents narrowly scoped capabilities. Agents report results and events; they do not directly mutate the planner's internal state or waive their own permissions.
- This separation allows the UI, Bot Mode, APIs, and future remote workers to observe or control the same execution without reimplementing orchestration.

---

## 4. One Runtime, Multiple Execution Modes

Do not remove existing Bot Mode. Treat a mode as an **execution policy profile** over the shared runtime, with a compatibility adapter for today's behavior.

| Mode/profile | Recommended behavior | Best use | Default constraints |
|---|---|---|---|
| Direct/Bot Mode | One primary agent, tools, optional bounded specialist delegation | Chat, direct tool use, routine tasks | No swarm unless triggered or requested |
| Supervisor + Specialists | One supervisor delegates bounded tasks and regains control after each child | General multi-step work | Parent owns final task and response |
| Graph Workflow | Explicit DAG with typed nodes, dependencies, gates, and conditional branches | Coding, CI, data processing, repeatable research | Deterministic dependency and retry semantics |
| Parallel Research | Independent research agents with separate scopes and evidence packets | Broad research/comparison/search | Deduplicate sources and merge evidence, not prose alone |
| Swarm | Dynamically create/assign tasks through a coordinator; agents contribute via contracts/events | Large, decomposable goals | Hard caps and no peer-to-peer message flood |
| Handoff/Network | One agent explicitly transfers ownership to another | Domain routing and support conversations | Every handoff carries a summary and task contract |
| Group Review/Council | Independent candidates or critics evaluate a specific decision/artifact | Difficult choices, architecture, high-uncertainty outputs | Stop after sufficient evidence; do not confuse majority with truth |
| Autopilot | Goal-driven supervisor plans, executes, monitors, replans, and verifies autonomously | User-authorized long-running projects | Persisted checkpoints, budgets, audit trail, stop controls |
| Recovery/Repair | Diagnose task/run failure, retry safely, switch provider/tool, or rollback | Runtime errors, broken integrations | Must not mask original failure or bypass policy |

### Mode compatibility rule

At startup, map current Alpha mode identifiers to a versioned `ExecutionPolicy` object. Keep old API/UI values valid. For example, current `bot_mode` may map to `strategy="supervisor"`, `max_parallelism=1`, `delegation="on_demand"`, while the UI continues to display Bot Mode. Add migration tests that compare existing Bot Mode behavior before and after integration.

### Strategy selection

Build a deterministic preflight estimator with a small, explainable classifier—not a separate LLM call for every request. Inputs:

- task type and explicit user instructions;
- number of distinct deliverables;
- apparent dependency structure;
- uncertainty and need for external evidence;
- likely need for tools, code, browser, or external APIs;
- consequence/risk of side effects;
- time/cost limits and available capacity;
- prior performance for comparable tasks.

A simple routing policy could look like this:

```text
if request is simple and one tool/answer is enough:
    DIRECT
elif deliverables have fixed steps and dependencies:
    GRAPH_WORKFLOW
elif exploration has several independent questions:
    PARALLEL_RESEARCH
elif one specialist should own the next interaction:
    HANDOFF
elif goal is open-ended and user authorized continued work:
    AUTOPILOT
else:
    SUPERVISOR_SPECIALISTS
```

The LLM can suggest a different mode, but runtime policy performs the final selection and records why.

---

## 5. The Canonical Task Graph

### 5.1 Model work as a DAG plus controlled dynamic replanning

Represent a run as a task graph. Each node is a task; each directed edge means a dependency, not just a conversational relationship. Tasks without unresolved dependencies may run concurrently. A task that discovers a new requirement can submit a proposed graph update to the supervisor, which validates scope, budgets, permissions, cycles, and duplicate work before applying it.

Recommended task states:

```text
CREATED -> READY -> DISPATCHED -> RUNNING -> VERIFYING -> SUCCEEDED
                 |         |        |             |
                 |         |        |             +-> RETRY_PENDING -> READY
                 |         |        +-> WAITING_APPROVAL -> READY / CANCELLED
                 |         +-> BLOCKED / CANCELLED / FAILED
                 +-> BLOCKED
```

Additional states: `PAUSED`, `TIMED_OUT`, `SKIPPED`, `SUPERSEDED`, and `COMPENSATING` for external actions that require rollback/compensation. Define legal state transitions in code and reject illegal transitions.

### 5.2 Task contract

Every task should use a typed model (Pydantic recommended in Python) with at least:

```json
{
  "task_id": "task_uuid",
  "run_id": "run_uuid",
  "parent_task_id": "task_uuid_or_null",
  "title": "Inspect API authentication flow",
  "objective": "Identify how authentication is implemented and list defects",
  "task_type": "code_analysis",
  "input_refs": ["artifact://run/file-index.json"],
  "dependencies": ["task_setup"],
  "scope": {"paths": ["src/auth/**"], "resources": ["repository-read"]},
  "acceptance_criteria": ["Findings cite file and line", "No source files modified"],
  "expected_artifacts": ["auth_findings.json", "auth_findings.md"],
  "eligible_capabilities": ["repository_read", "python_analysis"],
  "risk_level": "low",
  "approval_policy": "not_required",
  "priority": 50,
  "deadline": null,
  "retry_policy": {"max_attempts": 2, "retryable_errors": ["provider_timeout"]},
  "budget": {"max_model_calls": 12, "max_tool_calls": 30, "max_tokens": 18000, "max_cost_usd": 0.0},
  "status": "CREATED",
  "idempotency_key": "run_uuid:task_setup:auth_analysis:v1",
  "created_at": "ISO-8601",
  "schema_version": 1
}
```

This is illustrative—not a frozen schema. Use separate typed fields for token and cost budgets and avoid treating `0` as “unlimited.” In local/free-only mode, `max_cost_usd=0` must mean paid provider calls are blocked, not simply that estimated cost is unknown.

### 5.3 Plan quality gate

Before dispatch, validate that:

- every node has a measurable acceptance criterion and expected output;
- dependencies reference real tasks and the graph contains no cycles;
- tasks are scoped to avoid overlapping file/resource writes;
- tasks are not duplicating the same research question without a reason;
- plan size and nesting remain under policy limits;
- high-risk effects have approval gates before execution;
- each output flows to a consumer or final deliverable;
- verification tasks exist for consequential deliverables;
- there is a defined stopping condition.

If the plan is too large, compress it into phases and generate detail just before a phase is executed. Avoid planning hundreds of granular tasks up front when most are conditional.

### 5.4 Graph node types

Use a small set of composable node types:

- `AgentTask`: an agent performs a scoped objective.
- `ToolTask`: deterministic tool/function call; no LLM if a function suffices.
- `ParallelGroup`: fan-out to independent tasks and collect results.
- `Join`: wait for required predecessors and assemble the inputs.
- `Condition`: deterministic or evaluator-backed branch.
- `ApprovalGate`: pause pending human authorization.
- `Verify`: run tests, assertions, evidence checks, or review.
- `Loop`: bounded iterate-until-condition node with maximum iterations.
- `Compensation`: execute a predeclared compensating action when safe and supported.
- `Subgraph`: invoke a reusable workflow with a stable input/output contract.

Prefer deterministic functions for simple logic. This aligns with production guidance to use workflows where the path is predictable and agents where dynamic decisions are necessary.

---

## 6. The Advanced Orchestrator / Executive Agent

The executive orchestrator owns the goal and is not just another general-purpose chat agent.

### 6.1 Responsibilities

1. Parse the request and state a normalized outcome, constraints, and completion test.
2. Inspect capability registry and available resources.
3. Select the execution policy, risk profile, and budget.
4. Create or revise the task graph.
5. Dispatch eligible tasks and resolve resource conflicts.
6. Watch for stalled tasks, errors, evidence gaps, and budget exhaustion.
7. Decide whether to retry, reassign, reduce scope, ask for approval, ask the user, or stop.
8. Run final acceptance evaluation and make sure all requested deliverables are present.
9. Compose a concise final result, including blockers and verification performed.

### 6.2 Separate the executive from the planner and evaluator

Use different contracts, not necessarily different LLMs, for:

- **Executive/manager:** priority, policy-compliant decisions, dependencies, resource trade-offs, and stop/continue decision.
- **Planner:** candidate plan and acceptance criteria.
- **Domain agents:** scoped execution.
- **Verifier:** independently tests claims and deliverables.
- **Final composer:** communicates the verified result and unresolved limitations.

At low complexity, these roles may be implemented by the same underlying model invocation or agent definition to save cost. For high-risk or high-uncertainty work, use a different prompt/context and preferably an independent verifier. Role separation is primarily a contract and permission boundary, not a requirement for five separate models.

### 6.3 Dynamic replanning protocol

An agent should not silently rewrite the active graph. It submits a `PlanChangeProposal` containing:

- reason and new evidence;
- tasks to add, remove, split, or supersede;
- dependencies affected;
- predicted additional resources and risk;
- changed deliverables/acceptance criteria;
- whether user approval is needed.

The supervisor validates proposal schema, ownership, budget headroom, permissions, and whether finished tasks would be invalidated. Append accepted changes to the audit log. Keep the original plan version available for replay and debugging.

### 6.4 Stop/continue controller

The controller should stop when any of the following is true:

- all required criteria pass and no mandatory verification remains;
- further work has low expected information gain relative to cost;
- the task is blocked by missing credentials, user data, permission, or external dependency;
- time, token, model-call, tool-call, disk, or monetary budgets are exhausted;
- a loop/cycle or repeated-failure threshold is reached;
- a policy violation occurs;
- the user cancels or pauses the run.

Do not use “agent feels confident” as the only stop condition. Track unanswered acceptance criteria and evidence coverage.

---

## 7. Agent Capability Registry and Smart Assignment

### 7.1 Agent manifest

Each available agent needs a machine-readable manifest. Recommended fields:

- stable `agent_id`, display name, version, description;
- capabilities and excluded task types;
- supported inputs/outputs and schema versions;
- model/provider preferences and fallback policy;
- tools, skills, MCP servers, data permissions, filesystem scope;
- max concurrency, timeout, token/tool budgets;
- isolation requirement (read-only, process sandbox, container, worktree);
- risk rating and approval requirements;
- quality metrics by task class, recent failure rate, median latency, average cost;
- health state and last successful invocation;
- prompt/skill/config version and checksum.

Example conceptual manifest:

```yaml
agent_id: code_reviewer
version: 1
capabilities: [code_review, test_analysis, security_review]
inputs: [task_contract, diff_artifact, test_report]
outputs: [review_findings_v1]
permissions: [repository_read, test_report_read]
max_concurrency: 2
isolation: read_only
preferred_models: [strong_reasoning, code_specialist]
approval_required_for: []
```

### 7.2 Capability matching

First apply hard filters:

- task capability match;
- required output schema support;
- permissions/scope match;
- healthy agent and available slot;
- model/provider available under current cost policy;
- runtime/sandbox compatibility.

Then rank eligible candidates by a configurable score. One suggested form is:

```text
score(agent, task) =
    w_capability * capability_match
  + w_quality    * quality_for_task_class
  + w_reliability * recent_success_rate
  + w_evidence   * output_validation_rate
  - w_latency    * estimated_latency
  - w_cost       * estimated_cost
  - w_queue      * queue_delay
  - w_context    * context/setup overhead
  - w_risk       * permission/risk mismatch
```

Normalize values, record score components, and make weights configurable. Never allow a high total score to override a failed hard constraint. Cold-start agents should use declared capabilities and test results rather than fabricated historical scores.

### 7.3 Performance data and fairness

Store metrics per **agent version × task class × model profile**, not one global “goodness” score. Use recency weighting to adapt to new versions, but require a minimum sample count before relying on scores. Record uncertainty and avoid over-penalizing a new agent for having little data. Evaluate routing changes on a held-out task set before enabling them globally.

### 7.4 Agent lifecycle

`REGISTERED -> VALIDATING -> READY -> BUSY -> READY`, with `DEGRADED`, `QUARANTINED`, `DISABLED`, and `UPDATING` states. Run smoke tests when agents, skills, tools, and models change. A repeatedly failing agent should be quarantined with a reason and alert, not silently retried indefinitely.

---

## 8. Task Scheduler, Parallelism, and Resource Governance

### 8.1 Scheduler responsibilities

The scheduler is a deterministic subsystem. It:

- selects tasks whose dependencies are satisfied;
- prioritizes tasks according to user priority, critical path, risk, age, and deadline;
- reserves model slots, tools, file locks, worktrees, browser sessions, and external rate-limit capacity;
- enforces global and per-agent concurrency;
- applies per-run and global resource budgets;
- handles backpressure and queue aging;
- updates task state atomically and emits events;
- cancels tasks after user cancellation or dependency invalidation.

### 8.2 Priority policy

Do not rely on arbitrary static priority alone. A composite score can include:

- user-specified priority;
- critical-path impact (a task blocking many descendants is important);
- deadline slack;
- task age (to prevent starvation);
- expected value of information;
- risk and approval status;
- estimated runtime and resource cost.

Document the formula and expose it in diagnostics so a task is not mysteriously skipped.

### 8.3 Concurrency budgets

Use hierarchical quotas:

- **Global**: overall active runs, model calls, processes, browser sessions, RAM/CPU, disk, network, API spend.
- **Per run**: max active agents, total task count, max graph depth, total model/tool calls, deadline, retries.
- **Per agent**: parallel slots, context budget, timeout, tool-call rate.
- **Per tool/provider**: request rate, concurrent calls, retry-after handling.
- **Per task**: its own deadline and budgets.

For a modest local Windows machine, start conservatively with 2–4 concurrent LLM tasks and make the value adaptive to provider rate limits, available RAM, GPU/model type, and tool occupancy. This is a starting configuration, not a universal benchmark. Many cheap agents can overload memory or rate limits faster than they improve quality.

### 8.4 Parallelism only for independent work

Before running tasks concurrently, check:

- no unresolved dependency exists between them;
- write scopes do not conflict;
- they do not share a non-thread-safe browser/session;
- the model provider and tools can sustain the workload;
- duplicated exploration is intentional (e.g., independent verification).

For research, independent queries and source verification can often run in parallel. For software changes, parallelize bounded analysis, test design, and disjoint implementation; run integration and merge verification after joining results.

### 8.5 Deadlocks and resource locks

Use named leases for shared resources with:

- owner task/run ID;
- TTL/lease renewal;
- acquisition timeout;
- fencing token or version check to prevent stale owners writing after lease loss;
- structured waiting status and deadlock diagnostics.

Avoid hold-and-wait where possible. Acquire resources in a stable order or reserve them only immediately before use. The scheduler must be able to release expired leases after crashes.

### 8.6 Backpressure

If the model provider, filesystem, browser, or machine is saturated, stop spawning child tasks. Queue them, lower concurrency, or route to a compatible alternative only if the fallback policy allows it. Expose a `capacity_limited` status rather than displaying a generic agent failure.

---

## 9. Typed Inter-Agent Communication and Shared Blackboard

### 9.1 Do not use one giant shared chat transcript as the database

Agents should exchange typed messages and artifact references. Every message should include:

- `event_id`, `run_id`, `task_id`, `parent_task_id`;
- sender/recipient identity and agent version;
- message type and schema version;
- correlation/causation IDs;
- timestamp and deadline where relevant;
- payload or immutable artifact references;
- provenance/evidence and data classification;
- idempotency key where an action could repeat.

Core message types:

- `TaskAssigned`, `TaskAccepted`, `TaskStarted`, `ProgressUpdate`;
- `TaskBlocked`, `TaskFailed`, `TaskCompleted`, `TaskCancelled`;
- `ArtifactPublished`, `EvidenceAdded`, `FindingReported`;
- `DelegationRequested`, `HandoffRequested`, `HandoffAccepted`;
- `PlanChangeProposed`, `PlanChangeAccepted`, `PlanChangeRejected`;
- `ApprovalRequested`, `ApprovalGranted`, `ApprovalDenied`;
- `BudgetWarning`, `BudgetExhausted`, `AgentUnhealthy`;
- `VerificationPassed`, `VerificationFailed`, `RunFinalized`.

### 9.2 Shared blackboard / artifact store

Create a versioned blackboard per run/project with clearly separated areas:

1. **Goal and constraints:** canonical user objective, source, and scope.
2. **Plan:** current graph version plus prior versions.
3. **Task board:** status, owner, dependency, next action, blocker.
4. **Evidence:** source URLs, excerpts/notes, code locations, test reports, tool outputs, timestamps, provenance.
5. **Artifacts:** files, patches, structured JSON, screenshots, generated plans, reports.
6. **Decisions:** decision, alternatives, rationale summary, evidence, owner, timestamp.
7. **Risks and assumptions:** current unresolved risks, confidence limitations, verification action.
8. **Run ledger:** events, budget consumption, approvals, errors, retries, and side effects.

Treat artifacts as immutable versions. New revisions reference the old artifact and explain what changed. For small installations, SQLite plus a filesystem artifact directory is a reasonable initial implementation; define repository interfaces so a database/object store can be swapped in later. Avoid deploying a distributed database until real contention or multi-machine deployment justifies it.

### 9.3 Message vs artifact

Use a message for a short event or coordination signal. Use an artifact for long content or output needed later. A child agent should return a **result packet** with summary, artifact references, criteria results, evidence, limitations, and recommended next step—not copy a complete chain of thought or full conversation.

### 9.4 Context delivery

Build context just in time for each task:

- canonical task contract;
- necessary parent summary and user constraints;
- only relevant files, tool descriptions, memories, and evidence;
- task dependency outputs and artifact references;
- required output schema, safety rules, and acceptance criteria;
- a compact run ledger summary.

Do not give every subagent the entire history by default. It increases token usage, risks confusion, and makes it difficult to distinguish facts from stale plans. Allow full history as an explicit debugging mode, with sensitive content redacted.

---

## 10. Advanced Delegation and Dynamic Swarms

### 10.1 Contract-first delegation

Every delegated task must specify:

- **Objective:** exactly what question/result to resolve;
- **Boundary:** what is in scope and out of scope;
- **Inputs:** artifact references and known assumptions;
- **Methods:** required or permitted tools; source-quality expectations;
- **Deliverables:** strict schema/file type/fields;
- **Acceptance criteria:** what will be checked;
- **Dependencies:** what must be completed first;
- **Constraints:** deadline, token/tool budget, cost, permissions;
- **Stop condition:** when to stop exploring and return;
- **Escalation route:** which blocker should return to supervisor or user.

For research, divide tasks by distinct questions, time periods, systems, or source classes—not just give four agents the same broad query. For coding, divide by independent modules or responsibilities and use separate worktrees.

### 10.2 Adaptive fan-out

Start small. A reasonable default:

- simple request: 0–1 child agents;
- moderate multi-part task: 2–4 specialists;
- highly parallel research: 3–5 first-wave specialists, then add another wave only if evidence gaps remain;
- larger swarms: require a high-complexity score and a resource-budget check.

These numbers are initial policy defaults to test and tune, not universal best counts. Each additional agent must have a non-overlapping objective or a clear independent-validation purpose.

### 10.3 Subtask decomposition quality

Planner/verifier should check for:

- complete coverage of the user's requirements;
- minimal overlap between sibling tasks;
- explicit task dependency edges;
- expected output usefulness to the parent;
- balance (no huge task plus many trivial tasks);
- tasks small enough to be assigned and verified;
- no orphan tasks and no cycle;
- estimated cost and value.

Use a second “plan critic” only for complicated tasks or when the planner's plan failed previously. It should return targeted issues and corrections, not regenerate the entire plan by default.

### 10.4 Handoff protocol

When one agent hands off work, require a structured transfer packet:

```json
{
  "handoff_id": "uuid",
  "task_id": "task_uuid",
  "from_agent": "research_generalist",
  "to_capability": "security_review",
  "ownership_change": true,
  "completed_work": ["artifact://..."],
  "current_findings": ["..."],
  "open_questions": ["..."],
  "constraints": ["read-only"],
  "next_required_action": "Inspect findings for security issues",
  "acceptance_criteria": ["..."],
  "budget_remaining": {"model_calls": 4, "tool_calls": 8}
}
```

The receiver acknowledges or rejects the handoff with a reason. The scheduler updates ownership atomically. The same task must not be owned simultaneously by two agents unless the policy explicitly defines it as collaborative.

### 10.5 Avoid peer-to-peer message storms

Use bounded topology:

- executive ↔ team leads;
- team lead ↔ assigned specialists;
- specialist ↔ artifacts/event bus;
- verifier ↔ artifacts and task contract.

Allow direct agent-to-agent messages only when a concrete need exists and the communication is logged. No unrestricted broadcast. Cap messages per task, suppress redundant progress messages, and summarize event bursts for the UI.

---

## 11. Agent Team Topologies and Collaboration Patterns

Alpha should select the topology based on work structure.

### A. Orchestrator–worker hierarchy

Best default for broad tasks. Executive delegates to domain/team leads; team leads use specialist agents. The executive retains responsibility for final integration.

### B. Parallel map–reduce

Map: partition scope and gather independent findings. Reduce: normalize outputs into a shared schema, deduplicate, reconcile conflicts, and synthesize. Do not simply concatenate long agent responses.

### C. Supervisor with tools/agents

Treat a specialist agent as a callable capability returning a structured result. The supervisor remains in control. This is often less confusing than transferring the entire conversational ownership.

### D. Sequential quality pipeline

Use for fixed transformations: inspect → implement → test → review → package. A failed gate returns to the relevant stage with a specific correction request.

### E. Event-driven reactive team

Use events for changes and failures: a test failure can wake a repair agent; a tool outage can wake a recovery agent; a new artifact can wake an evaluator. Event handlers must be idempotent and debounced to avoid loops.

### F. Independent council / evaluator–optimizer

Use multiple candidates or critiques for a hard, consequential choice. Keep first-pass answers independent to reduce anchoring. Compare against explicit criteria and external evidence. Stop when the decision is good enough or budget is reached. The majority answer is not automatically correct.

### G. Mesh handoff

Use only where dynamic routing is essential and agents can assume ownership safely. Mesh topology should remain bounded; every transfer is explicit and auditable.

### H. Hierarchical swarm

Use a manager per large workstream, with local worker pools. Do not permit a worker to spawn an unbounded nested swarm. Graph depth and task count have enforceable caps.

---

## 12. Result Aggregation, Conflict Resolution, and “Best Parts” Synthesis

### 12.1 Normalize before comparing

Each result should be transformed into a common schema that includes:

- answer/finding;
- evidence references;
- confidence category and its basis;
- assumptions and unknowns;
- acceptance criteria passed/failed;
- known limitations;
- recommended next action.

Confidence is a routing hint, not a truth score. Evidence quality and independent validation should carry more weight.

### 12.2 Conflict matrix

When agents disagree, classify the disagreement:

1. **Factual:** sources/results differ. Retrieve stronger or newer evidence, or perform a targeted tool call.
2. **Interpretive:** same facts, different explanation. Compare against explicit criteria and alternatives.
3. **Implementation:** patches or designs differ. Run tests, benchmarks, static checks, or a proof of concept.
4. **Preference:** trade-off varies by user priority. Surface options or apply stored explicit preferences.
5. **Missing information:** no result is adequately supported. Report uncertainty or request input.

Do not ask a fresh LLM to arbitrate every disagreement without supplying evidence. The arbiter should compare source quality, recency, reproducibility, acceptance criteria, and test outcomes.

### 12.3 Synthesis algorithm

1. Validate schemas and required fields.
2. Reject malformed results or route them back for repair.
3. Deduplicate claims by normalized claim key and evidence fingerprint.
4. Group supporting and contradicting evidence.
5. Resolve factual conflicts through targeted verification.
6. Select the best-supported portions from different agents.
7. Run final acceptance checks against the original task—not just each subtask.
8. Record rejected alternatives and the reason in a concise decision log.
9. Generate the user-facing response from verified artifacts.

This supports Alpha's “combine the best answer from specialist agents” goal without turning synthesis into unsupported blending.

---

## 13. Verification, Critique, and Quality Gates

### 13.1 Use layered verification

**Layer 1 — Schema validation:** outputs match declared models and include required fields.

**Layer 2 — Deterministic checks:** run unit tests, integration tests, type checking, linting, assertions, file-existence checks, JSON-schema checks, link checks, or SQL constraints as relevant.

**Layer 3 — Evidence validation:** research claims link to real sources; code-review claims point to actual lines/diffs; tool claims match tool outputs; generated artifacts exist and can be opened.

**Layer 4 — Independent evaluator:** a separate context/agent reviews difficult outputs against the original criteria. It should have read access to the evidence and be allowed to fail the result.

**Layer 5 — Human approval:** require approval for high-impact irreversible actions, permission expansion, publishing/deployment, destructive operations, purchases, messages sent externally, or other defined high-risk actions.

### 13.2 Acceptance criteria are executable where possible

Examples:

- Code change: relevant tests pass, changed files are in scope, static analysis passes, app launches, no critical regression appears.
- Research: every key factual claim has a traceable source, source date is considered, conflicts are acknowledged, coverage checklist is complete.
- File generation: output exists at the promised path, format is valid, content has been reviewed, links work.
- API task: expected status/schema, auth behavior, failure cases, timeout, and integration test pass.
- Data transformation: expected row counts/invariants, schema, null handling, and sample checks pass.

A quality score can help prioritize additional review, but should never override a failed mandatory criterion.

### 13.3 Retry only with a changed strategy

Before retrying, classify the error: transient provider/network, tool schema mismatch, context overflow, bad plan, wrong capability, permissions, logic/test failure, rate limit, or unknown. The retry policy should choose an action:

- retry with bounded exponential backoff and jitter for transient errors;
- repair arguments based on structured validation output;
- compact or partition context for context overflow;
- reassign to a different eligible agent/model for repeated quality failure;
- replan if dependencies/scope were wrong;
- request approval or user input when blocked;
- stop and escalate for policy violations or repeated non-progress.

Log each attempt as a distinct event and preserve the original error.

### 13.4 Anti-loop logic

Track repeated task signatures, nearly identical tool calls, identical failed tests, no-op patch attempts, cycles in task graph changes, and lack of progress. When the loop detector triggers, pause that branch, summarize attempted strategies, and request a new plan or surface a blocker. Do not automatically increase retries indefinitely.

---

## 14. Coding and Software Engineering Agents

Because Alpha is expected to execute substantial coding work, implement safe collaboration for repositories explicitly.

### 14.1 Repo map and ownership manifest

Before code work, create/reuse a repo map with packages, entry points, tests, APIs, config, dependency graph, and generated files. Assign agents write scopes such as:

- Agent A: `src/api/**`;
- Agent B: `tests/api/**`;
- Agent C: read-only architecture review;
- integrator: merge and cross-module test.

Scopes must be checked by tooling. Prompt instructions alone are not a sufficient write boundary.

### 14.2 Worktree-isolated implementation

For parallel coding tasks:

1. Create a separate Git worktree/branch per implementation agent.
2. Give each agent a file scope and acceptance tests.
3. Require small, reviewable commits and a summary artifact.
4. Run tests in the worktree before review.
5. Ask an independent reviewer to inspect the diff and relevant tests.
6. Merge via an integrator, resolving conflicts explicitly.
7. Run full integration tests after merge.
8. Keep the previous known-good commit and support rollback.

Never let multiple coding agents edit the same working directory concurrently unless the operation is intentionally serialized and the files are locked.

### 14.3 Coding task graph

A robust default workflow:

`Repo inspection → Requirements/acceptance criteria → Plan review → Worktree allocation → Independent implementation → Unit tests → Diff review → Integration/merge → Integration tests → Runtime smoke test → Final summary`

If the change is small, skip unnecessary agents while preserving the safety and test gates appropriate to the risk.

### 14.4 Build-fix-verify loop

On test failure:

- collect exact command, exit code, relevant stdout/stderr, environment, and diff;
- map failure to the responsible task/owner;
- assign a focused repair task with the failing test and expected behavior;
- ensure the repair agent sees the failed result, not a paraphrased vague complaint;
- rerun the failing test first, then the broader suite;
- cap repair iterations and compare fingerprints to identify repeated no-op attempts.

### 14.5 Safe execution sandbox

Shell/terminal tasks should run with explicit working directory, environment allowlist, command timeout, process-tree cancellation, output caps, filesystem scope, and network permission. For untrusted code, use a stronger isolation boundary (container or VM where available). Do not claim that subprocess execution alone is a secure sandbox.

---

## 15. Memory and Knowledge Architecture for Multi-Agent Work

Keep memory types separate to avoid stale or cross-task contamination.

1. **Conversation memory:** recent messages needed to continue a user conversation.
2. **Run/checkpoint memory:** task graph, node state, current attempt, pending approvals, and resumable state.
3. **Project memory:** repository architecture, conventions, decisions, environment and known issues.
4. **Agent memory:** scoped learnings about a role's workflow, tools, and failure modes.
5. **Episodic memory:** selected past task summaries with outcomes and artifacts.
6. **Semantic/knowledge memory:** normalized facts and documents with provenance, timestamps, and source quality.
7. **Operational memory:** tool/model reliability, API limits, failure counts, benchmark scores.

### Memory write policy

- A child agent proposes memory updates; a central memory policy validates and stores them.
- Store provenance, timestamp, confidence basis, source, privacy label, scope, and expiry/review date.
- Do not store secrets or raw credentials in LLM-readable memory.
- Do not promote speculative statements into durable facts.
- Avoid writing every conversation into semantic memory; use explicit extraction and deduplication.
- Distinguish user preference from one-time instruction.
- Give each agent only the memories relevant to its task and authorized scope.

### Memory retrieval scoring

Rank by task relevance, trust, recency, scope match, and source quality. Include older durable facts when useful, but flag conflicting or stale data. When memory conflicts with the current user request or verified live evidence, prefer the current request/strong evidence and record the conflict for cleanup.

---

## 16. Long-Running Execution, Checkpoints, and Recovery

This is essential for any autopilot or multi-hour task mode.

### 16.1 Durable state model

Persist:

- run metadata and policy version;
- plan/graph versions;
- task status and dependency state;
- agent assignment and attempt count;
- last durable checkpoint;
- artifact references and checksums;
- budget counters;
- approvals and user decisions;
- tool side-effect ledger;
- error classification and last known safe action;
- cancellation/pause/resume state.

### 16.2 Event log and state reducer

For every consequential state change, append an event and update current state transactionally. Prefer a single-writer run coordinator or compare-and-swap versioning to avoid two workers overwriting each other's state. The event stream should permit reconstruction of status and forensic debugging.

Do not persist hidden chain-of-thought as a default requirement. Persist plans, concise decision summaries, task actions, tool inputs/outputs subject to redaction, evidence, and structured outcomes. These are sufficient for operations and reproducibility without retaining sensitive internal reasoning traces.

### 16.3 Idempotency and exactly-once caveat

Across process restarts, “exactly once” execution of an external side effect is not generally guaranteed unless the remote system provides idempotency or transactional semantics. Design for **at-least-once delivery plus idempotent handlers**, using idempotency keys and querying external state before retrying. If a side effect's result is ambiguous, mark it `SIDE_EFFECT_UNCERTAIN` and require reconciliation instead of blindly repeating it.

### 16.4 Crash recovery procedure

On boot:

1. Load active run records and expired leases.
2. Check each task's state and last checkpoint.
3. Mark interrupted in-flight attempts as `INTERRUPTED` or equivalent; do not assume they failed before producing a side effect.
4. Reconcile tool side-effect ledger and known external status where safe.
5. Requeue only tasks whose retry policy permits it and whose dependencies remain valid.
6. Restore budget counters and enforce remaining budget.
7. Resume from the last verified checkpoint.
8. Emit a visible recovery event and alert if any task cannot be safely resumed.

### 16.5 Pause, resume, and cancel

`PAUSE` should stop new work and allow safe in-flight work to reach a checkpoint, unless immediate interruption is required. `CANCEL` should cancel queued tasks, signal running workers, terminate child processes, and record cleanup results. `RESUME` must re-check permissions, credentials, dependency versions, policy version, and expired deadlines before dispatch.

---

## 17. Reliability: Timeouts, Retries, Circuit Breakers, and Self-Healing

### 17.1 Failure categories

Use a stable error taxonomy:

- `MODEL_RATE_LIMIT`, `MODEL_TIMEOUT`, `MODEL_UNAVAILABLE`, `MODEL_INVALID_OUTPUT`;
- `TOOL_TIMEOUT`, `TOOL_SCHEMA_ERROR`, `TOOL_PERMISSION_DENIED`, `TOOL_UNAVAILABLE`;
- `TASK_BUDGET_EXHAUSTED`, `RUN_BUDGET_EXHAUSTED`, `CONTEXT_OVERFLOW`;
- `DEPENDENCY_FAILED`, `TASK_DEADLOCKED`, `RESOURCE_LOCK_TIMEOUT`;
- `SANDBOX_FAILURE`, `REPOSITORY_CONFLICT`, `TEST_FAILURE`;
- `PERSISTENCE_FAILURE`, `CHECKPOINT_FAILURE`, `EVENT_DELIVERY_FAILURE`;
- `POLICY_BLOCK`, `APPROVAL_REQUIRED`, `USER_INPUT_REQUIRED`;
- `UNKNOWN_ERROR`.

Every error should include retryability, retry-after if available, attempt, owning task, responsible component, user-safe summary, redacted diagnostic detail, and correlation ID.

### 17.2 Circuit breakers

Maintain per-provider/tool/agent health states: `CLOSED`, `OPEN`, `HALF_OPEN`. Repeated timeouts or server failures open a breaker, temporarily suppressing new calls. After a cooldown, test with a small probe. Only route to a fallback when its capability and policy meet the requirement; never silently substitute a weaker model if it could change correctness or privacy.

### 17.3 Recovery agent restrictions

A recovery agent can inspect diagnostics and propose a repair plan. Runtime code executes approved recovery actions. Recovery agents must not:

- delete logs or evidence of failure;
- change their own tool permissions;
- disable tests/guardrails to force a pass;
- modify the production config without approval;
- repeat destructive actions after an ambiguous result.

### 17.4 Health endpoints and watchdogs

Expose component health separately from task health. Suggested local endpoints, adapted to Alpha's current API conventions:

- `GET /api/system/vitals`: CPU, memory, disk, process status, queue depth, worker counts, event-store status.
- `GET /api/agents`: registered agents, health, active tasks, last success, current limits.
- `GET /api/runs/{run_id}`: current plan, state, progress, budget, blockers.
- `GET /api/runs/{run_id}/events`: paginated event stream / SSE where supported.
- `GET /api/alerts`: current and recent reliability alerts.
- `POST /api/runs/{run_id}/pause`, `/resume`, `/cancel`: guarded lifecycle actions.

Health checks must test real dependencies (state store writable, worker can accept a task, selected provider reachable when configured), not just whether the web server returns 200.

---

## 18. Security, Permissions, and Governance

### 18.1 Capability-based permissions

Give each task a scoped permission token derived from the requesting run and the parent task's authority. A child task cannot expand permissions on its own. Tool gateway verifies the token for every operation.

Use least privilege:

- repository read and repository write are separate capabilities;
- terminal read-only diagnostics and arbitrary shell are separate;
- browser read and submit/publish actions are separate;
- database read-only and mutation are separate;
- file read/write/delete are separate;
- installing a skill, running a binary, deploying, sending messages, or publishing requires explicit policy checks.

### 18.2 Prompt injection and untrusted content

Treat webpages, repositories, issue text, PDFs, tool outputs, agent messages, and third-party skill descriptions as untrusted data. Instructions embedded in retrieved content do not override system policy. Use taint/provenance labels for untrusted content and prohibit a content-reading agent from directly authorizing high-impact actions based only on that content.

### 18.3 Approval policy

Define approval rules centrally and attach them to action classes. At minimum require human approval for:

- deleting or overwriting user data outside an explicit disposable sandbox;
- pushing to protected branches, publishing releases, deploying to production;
- external messages, posts, purchases, financial transactions, or sharing private data;
- permission escalation, installing untrusted executable code, or enabling a new external service;
- actions with uncertain irreversible side effects.

Approvals should include exact action, target, data scope, expected consequence, and a preview. Approval is action-specific and expires when the proposed action changes materially.

### 18.4 Secret management and logs

Use a secret manager/OS credential store or environment injection; never put API keys in agent memory, event payloads, generated Markdown, source files, or ordinary logs. Redact tokens, headers, and personal data before logging. Make detailed request/response capture opt-in with retention controls.

### 18.5 Policy decision records

For every blocked/approved action, log the policy rule, decision, scope, actor, approver, and reason. The user should be able to understand why Alpha refused or paused without exposing sensitive security internals.

---

## 19. Model Routing and Resource-Efficient Agentic Work

### 19.1 Model per role, not one model for everything

Model choice is part of the agent profile. Examples:

- cheap/fast model for intent classification, structured extraction, task labeling, simple summarization;
- strong reasoning model for high-level plans, ambiguity resolution, conflict arbitration;
- coding specialist for implementation and debugging;
- vision-capable model for image/screenshot tasks;
- local model for privacy-sensitive or offline tasks where quality is sufficient;
- deterministic functions for calculations, file checks, schema validation, and state transitions.

Keep the provider/model choice behind an adapter. Validate the model supports required tool calling, context length, structured output, vision, and streaming before assignment.

### 19.2 Cost-aware orchestration

Track estimated and actual token usage, provider cost, tool latency, retries, cache savings, and total run wall time. Select the mode with best expected utility under limits. Use caching for deterministic reads and repeated analysis where valid; cache keys must include input version, model/prompt version, relevant context, and tool version.

### 19.3 Local-first and zero-cost guarantees

Implement an explicit `execution_budget_mode`:

- `zero_cost`: only local models and explicitly allowed free services; block paid calls in code, not merely by prompt;
- `budgeted`: optional providers allowed within a hard spend ceiling;
- `unrestricted_by_budget`: only if user explicitly configures it, but still subject to permissions and safety policy.

If a local model cannot meet the required tool/structured-output capability, return a clear compatibility failure or use an explicitly approved fallback. Do not silently make a paid call because a local model failed.

### 19.4 Keep model selection explainable

For each call, record the role, selected provider/model, selection reason, constraint checks, fallback history, and budget consumption. Redact secrets and don't log hidden reasoning. Allow the UI to show “used local model” or “fallback blocked by zero-cost mode.”

---

## 20. MCP, A2A, Plugins, and External Agent Integration

### 20.1 MCP as the tool integration boundary

Centralize MCP clients/server adapters in the tool gateway. Normalize descriptions and schemas; validate tool inputs and outputs; set timeouts and concurrency; apply permissions; record outputs as artifacts/events; and test tool descriptions against real use cases. Keep Alpha's internal `ToolCapability` interface independent of a single MCP SDK version.

### 20.2 A2A as an optional remote-agent bridge

Use A2A when Alpha needs to cooperate with external/remote agent applications. Map remote agents to an untrusted or semi-trusted capability in the registry. Before accepting results, require:

- identity/endpoint validation;
- authentication and authorization as appropriate;
- declared capabilities and supported content types;
- task contract and deadline;
- result provenance and artifact transport policy;
- output schema validation;
- safety and evidence checks;
- remote failure/timeouts/retry handling.

Never delegate sensitive local files or secrets to a remote agent unless explicitly authorized. A2A provides interoperability, not automatic trust.

### 20.3 Plugin lifecycle

Every skill/tool/plugin should have a manifest including name, version, source, license, integrity hash, dependencies, tool schemas, permissions, network access, data handled, cost tier, health check, and rollback/uninstall procedure. Installation should run a validation sandbox and compatibility tests before activation. Pin versions for production; keep upgrade proposals separate from live installation.

---

## 21. Observability: Trace Every Task from User Request to Verified Result

### 21.1 Required correlation hierarchy

```text
user_request_id
└── run_id
    ├── plan_version
    ├── task_id
    │   ├── attempt_id
    │   ├── agent invocation span
    │   ├── model call span(s)
    │   ├── tool call span(s)
    │   ├── artifact/evidence events
    │   └── verification span(s)
    └── final evaluation + response
```

### 21.2 Logs, traces, metrics, events are different

- **Logs:** human-readable diagnosis and structured errors.
- **Traces:** causal relationship and duration across agents, providers, tools, and tasks.
- **Metrics:** counts/rates/latencies/costs and trend monitoring.
- **Events:** durable domain facts that drive UI and workflow transitions.
- **Artifacts:** outputs and evidence that must be reused or audited.

Do not try to make a single unstructured log file serve all these purposes.

### 21.3 Required metrics

Execution:

- task success/failure/blocked rate;
- end-to-end completion rate against acceptance criteria;
- median and p95 task/run latency;
- time to first useful progress event;
- retry count and retry recovery rate;
- crash/restart recovery success;
- timeout/circuit-breaker events;
- queue depth and utilization.

Multi-agent coordination:

- planning coverage and plan revision count;
- duplicate-task rate;
- agent spawn count per successful task;
- handoffs per run;
- idle/wait time and critical-path time;
- write-conflict/merge-conflict rate;
- failed delegation and schema rejection rate;
- percentage of tasks with verified artifacts.

Quality and efficiency:

- acceptance criteria pass rate;
- first-pass verification pass rate;
- regression rate;
- evidence coverage and citation correctness where applicable;
- token/model-call/tool-call usage per successful task;
- cost per verified completion;
- quality gain over single-agent baseline;
- unnecessary agent/tool call rate.

Safety:

- approval requests and denial rate;
- policy violations blocked;
- untrusted-content/prompt-injection detections;
- sensitive-data redactions;
- actions performed without required approval (target: zero).

### 21.4 User-facing event stream

The UI should display meaningful state changes such as:

- “Plan created: 6 tasks; 2 can run in parallel.”
- “Research agent started task R-02.”
- “Task R-02 found a source conflict; targeted verification queued.”
- “Code agent is blocked by a failing dependency install.”
- “Paused: this action needs approval.”
- “Verification passed: 12 tests passed; 1 skipped with reason.”

These examples must be generated from actual events and test outputs. Avoid fake percentage progress where the system cannot estimate completion credibly. If progress is estimated, label it as an estimate and base it on remaining graph work, not token generation alone.

### 21.5 Debug/replay mode

Add a developer-facing mode with:

- run timeline and graph visualization;
- state snapshots at key transitions;
- prompt/config/model identifiers (not necessarily full sensitive prompt content);
- tool arguments/results with redaction;
- retries, stack traces, subprocess output, and error categories;
- budget accounting and routing decisions;
- replay using mocked/captured external results;
- diff between two runs/plans/config versions.

Replay should default to **no real external side effects**. A separate explicit mode may permit live side effects only when policy and approvals allow them.

---

## 22. User Experience and Runtime Controls

The interface must show that Alpha is actually working rather than merely returning a chat response.

### 22.1 Run view

Recommended panes:

- **Goal:** original request and normalized success criteria;
- **Plan:** graph, dependencies, status, retries, and priority;
- **Agents:** role, task, model/provider, health, active/queued/blocked state;
- **Activity:** real-time event stream with filter/search;
- **Artifacts:** outputs, diffs, test reports, sources, and logs;
- **Budget:** elapsed time, tokens, model calls, tool calls, cost mode and remaining limits;
- **Approvals:** exact action preview and approve/reject controls;
- **Diagnostics:** last error, error class, relevant logs, recovery actions;
- **Result:** acceptance checks, evidence, limitations, final response.

### 22.2 User controls

Expose pause, resume, cancel, retry failed task, reassign agent, approve/deny action, change priority, revise scope, and export run diagnostics. Each control must call the runtime API and display its actual result. Avoid buttons that only update UI state.

### 22.3 Streaming

Use the existing Alpha streaming architecture if stable; stream typed run events by SSE/WebSocket rather than polling an entire conversation transcript repeatedly. Event sequence numbers should allow reconnecting clients to ask for events after the last seen ID. Add pagination/retention for long runs.

### 22.4 Completion report

For each completed run, include:

- requested goal and delivered artifacts;
- tasks completed and verification performed;
- important evidence or source links;
- tasks skipped/blocked and why;
- known limitations and assumptions;
- model/tool/cost information only at the level the user has enabled;
- commit/branch/deployment links where applicable.

---

## 23. Suggested Python Package Boundaries

Adapt names to Alpha's real repository layout after an inventory. These are logical boundaries, not a command to relocate all current files at once.

```text
alpha/
  orchestration/
    executive.py              # goal lifecycle and high-level decisions
    planner.py                # plan proposal and plan quality checks
    graph.py                  # task graph, dependencies, graph validation
    scheduler.py              # queues, priorities, leases, concurrency
    policies.py               # mode profiles, risk, budgets, stop rules
    delegation.py             # task contracts and handoff protocol
    aggregation.py            # normalize, deduplicate, reconcile, synthesize
    state_machine.py         # validated state transitions
  agents/
    registry.py               # discover and version agents
    manifest.py               # Pydantic manifest models
    router.py                 # eligibility filters and assignment scoring
    runtime.py                # shared execution adapter
    specialists/               # existing agent implementations remain compatible
  messaging/
    events.py                 # typed event models and schema versions
    bus.py                    # event dispatch interfaces
    idempotency.py
  persistence/
    run_store.py              # active state and checkpoints
    event_store.py            # append-only event records
    artifact_store.py         # artifact references and hashes
    migrations/
  verification/
    criteria.py               # acceptance criteria contracts
    evaluators.py             # deterministic and LLM-based checks
    evidence.py               # provenance and evidence validation
    test_runner.py
  tools/
    gateway.py                # permissioned tool invocation
    capabilities.py           # common abstraction for local/MCP/API tools
    sandbox.py
    rate_limits.py
  observability/
    logging.py
    tracing.py
    metrics.py
    redaction.py
  integrations/
    langgraph_adapter.py      # use if current Alpha already relies on LangGraph
    mcp_adapter.py
    a2a_adapter.py            # optional, later stage
  api/
    runs.py                   # run status/events/lifecycle controls
```

### Boundary guidelines

- Avoid creating duplicate new versions of an existing module simply to fit this proposed tree. Define the interface, then adapt the existing module behind it.
- Keep provider/framework imports out of task schemas and central state models.
- Make persistence and tool interfaces injectable so unit tests can use in-memory or fake implementations.
- Keep control-plane code testable without calling an LLM.
- Use type hints, Pydantic validation, and explicit errors at module boundaries.
- Pin dependencies and run compatibility tests before upgrading framework versions.

---

## 24. Core Interfaces and Pseudocode

The following is an implementation sketch, not production-ready code.

### 24.1 Core interfaces

```python
from typing import Protocol, Any

class AgentExecutor(Protocol):
    async def execute(self, task: "TaskContract", context: "TaskContext") -> "TaskResult": ...

class RunStore(Protocol):
    async def load_run(self, run_id: str) -> "RunState": ...
    async def save_transition(self, event: "RunEvent", expected_version: int) -> "RunState": ...

class Scheduler(Protocol):
    async def dispatch_ready_tasks(self, run_id: str) -> None: ...

class Verifier(Protocol):
    async def evaluate(self, task: "TaskContract", result: "TaskResult") -> "VerificationReport": ...

class ToolGateway(Protocol):
    async def call(self, capability: str, arguments: dict[str, Any], context: "ToolContext") -> "ToolResult": ...
```

### 24.2 Runtime loop

```python
async def run_until_blocked(run_id: str) -> None:
    while True:
        state = await run_store.load_run(run_id)

        if state.cancel_requested:
            await cancel_pending_and_signal_workers(state)
            await finalize_cancelled_run(state)
            return

        await reconcile_expired_leases(state)
        await enforce_run_budgets(state)
        await process_completed_attempts(state)
        await apply_validated_events(state)

        if state.requires_user_approval:
            await set_run_status(state, "WAITING_APPROVAL")
            return

        if all_required_tasks_terminal(state) and all_required_checks_pass(state):
            await finalize_verified_success(state)
            return

        ready = await find_ready_tasks(state)
        if ready:
            scheduled = await scheduler.reserve_and_dispatch(ready, state.budgets)
            if scheduled:
                continue

        decision = await executive.decide_next_action(state)
        # Validated by deterministic policy before it can mutate the run.
        await validate_and_apply_executive_decision(decision, state)

        if decision.kind in {"WAIT_FOR_EVENT", "WAIT_FOR_USER", "STOP_BLOCKED"}:
            return
```

### 24.3 Idempotent event handling

```python
async def handle_task_completed(event: TaskCompleted) -> None:
    if await event_store.already_processed(event.event_id):
        return

    task = await run_store.get_task(event.task_id)
    if task.status not in {"RUNNING", "VERIFYING"}:
        await record_transition_conflict(event, task.status)
        return

    result = await artifact_store.resolve(event.result_ref)
    report = await verifier.evaluate(task.contract, result)
    await run_store.commit_result_and_event_atomically(event, result, report)
    await scheduler.refresh_dependents(task.task_id)
```

The code must make transactional boundaries, cancellation, schema validation, and exception handling explicit. `except Exception: pass` and retry-all behavior are prohibited in the orchestration core.

---

## 25. Persistence Choice and Deployment Strategy

### Stage 1 — single-machine dependable core

- SQLite for runs, tasks, events, approvals, idempotency records, and small metadata.
- Filesystem artifact store with content hashes and atomic writes.
- Async worker tasks with bounded concurrency.
- Periodic checkpoints and startup recovery.
- No external broker required.

### Stage 2 — optional queue/backend adapter

If measurements show queue contention, high concurrency, or multi-process needs, introduce a queue behind an interface. Use an established durable queue rather than a home-grown network protocol. Keep task events and idempotency semantics the same.

### Stage 3 — multi-machine workers

Only when Alpha actually needs remote workers, use explicit worker identity, authentication, lease management, artifact transport, task fencing, A2A/MCP trust policies, and deployment health monitoring. Don't distribute execution just because a distributed architecture looks advanced.

### SQLite concurrency caution

SQLite is practical for a single local application, but it is not a substitute for a multi-writer distributed coordination system. Use transactions, short writes, WAL where appropriate, a single run-state coordinator or version checks, and backups. Re-evaluate storage when the measured workload no longer fits.

---

## 26. Implementation Roadmap: Build in Safe, Verifiable Increments

Each phase must be mergeable and useful on its own. Do not implement all features in one giant pull request.

### Phase 0 — Feature inventory and baseline capture

**Goal:** Know what Alpha already does, so upgrades do not erase capabilities.

Tasks:
- Inventory all existing modes, including Bot Mode, subagents, swarm, tools, memory, skills, APIs, scheduled tasks, and UI controls.
- Trace one real request from UI to final response and identify where execution stops being visible or where task status is lost.
- Record current module ownership, framework versions, provider adapters, state persistence, logging, and test coverage.
- Export a feature matrix with `exists / partially working / broken / not tested / planned` states.
- Create smoke tests for all existing user-facing modes before changes.
- Capture baseline tasks: simple chat, tool use, two-agent delegation, parallel task, failed tool, model timeout, cancellation, restart recovery, coding test loop.

**Acceptance gate:** baseline report checked into `docs/architecture/`; existing features and route names recorded; a test can detect when Bot Mode breaks.

### Phase 1 — Canonical contracts and compatibility adapters

**Goal:** Standardize core objects without replacing agents.

Tasks:
- Implement `TaskContract`, `TaskResult`, `AgentManifest`, `RunState`, `RunEvent`, `VerificationReport`, `Budget`, and `ToolContext` as typed schemas.
- Add schema version fields and migration tests.
- Wrap existing agents in `AgentExecutor` adapters.
- Map existing modes to `ExecutionPolicy` profiles while preserving external identifiers.
- Normalize tool errors and results into a common envelope.
- Add centralized correlation IDs and redaction-safe structured logs.

**Acceptance gate:** current Bot Mode and subagent tasks operate through adapters; no feature removal; schemas validate both success and failure outcomes.

### Phase 2 — Graph scheduler and dependency engine

**Goal:** Make assignment and dependencies real runtime concepts.

Tasks:
- Implement DAG validation, ready-task calculation, legal state transitions, priority queue, concurrency limits, leases, and cancellation.
- Add a local SQLite repository with atomic state updates and append-only events.
- Use deterministic tool nodes for work that does not need a model.
- Add bounded fan-out and aggregation nodes.
- Add task/task-scope conflict checks; start with conservative serialization for writes.
- Implement scheduler diagnostics explaining why each queued task is waiting.

**Acceptance gate:** tasks run in dependency order; independent tasks run concurrently within policy; no dependency cycles are accepted; cancellation and retries are tested.

### Phase 3 — Delegation contracts and capability router

**Goal:** Better delegation without duplicate work or agent roulette.

Tasks:
- Create capability manifests for current Alpha agents.
- Implement hard eligibility checks and explainable candidate ranking.
- Add contract-first task creation and structured handoff/return packets.
- Add duplicate-task detection by scope and normalized objective.
- Add bounded re-delegation/reassignment and parent-child result propagation.
- Add adaptive fan-out with conservative limits.

**Acceptance gate:** every child task has explicit scope and output contract; assignment logs show why an agent was selected; malformed outputs return to repair or fail visibly.

### Phase 4 — Verification and evidence-first completion

**Goal:** Stop accepting a text answer as proof that work is done.

Tasks:
- Add schema, deterministic, evidence, independent-agent, and human gates.
- Make acceptance criteria part of task state.
- Build task-specific evaluators for research, coding, file generation, browser/tool actions, and data tasks.
- Add evidence packets, source/artifact provenance, and contradiction detection.
- Add repair loops with classified errors and bounded retries.
- Make final status derive from required checks, not model self-report.

**Acceptance gate:** tests prove that a fluent but incomplete answer does not complete a task; final report includes verification and limitations.

### Phase 5 — Durable runs, restart recovery, and self-healing

**Goal:** Survive process exits, laptop restarts, and transient provider/tool failures.

Tasks:
- Add checkpoints, event replay/state reconstruction, expired-lease recovery, idempotency keys, and external side-effect reconciliation.
- Implement pause/resume/cancel from API and UI.
- Add provider/tool circuit breakers and fallback policies.
- Create bounded recovery diagnostics and repair proposals.
- Validate boot recovery with forced-kill and power-loss simulations where feasible.

**Acceptance gate:** interrupted run resumes from the last safe checkpoint; no unsafe duplicate side effect in test fixtures; original exceptions remain visible.

### Phase 6 — Advanced collaboration, synthesis, and Autopilot

**Goal:** Upgrade swarm quality and long-running autonomy.

Tasks:
- Add map–reduce research, hierarchical supervisors, bounded group critique, conflict resolution, and event-triggered helper agents.
- Add plan-change proposals and supervisor-validated replanning.
- Add project-level task board and run timeline UI.
- Add autonomy budgets and stopping rules.
- Keep Autopilot opt-in until benchmark and failure handling meet required thresholds.

**Acceptance gate:** dynamic graph changes are logged and validated; swarm reduces task gaps or improves measured quality over baseline without exceeding agreed cost/time limits.

### Phase 7 — Coding worktrees, advanced tools, and integrations

**Goal:** Safely execute repository work with multiple coding agents.

Tasks:
- Build isolated Git worktree orchestration, file-scope ownership, independent code review, integration merge, and test-on-merge.
- Normalize MCP tools behind the gateway; test tool descriptions and schemas.
- Add optional A2A remote-agent adapter only if there is a real use case.
- Add plugin manifests, version pinning, install validation, and rollback.
- Add replay mode with fake/model-recorded responses and no real side effects by default.

**Acceptance gate:** a representative multi-file change can be implemented by agents in separate worktrees, merged only after checks, and rolled back cleanly.

### Phase 8 — Routing optimization, evaluation-driven improvement, and governance

**Goal:** Let Alpha improve its operating policies safely.

Tasks:
- Build a benchmark set from anonymized/approved actual failure cases and representative tasks.
- Track agent capability by task class and version.
- Run offline routing-policy experiments and compare against current baseline.
- Propose improved prompts/tool descriptions/workflows in a branch or proposal store.
- Require tests, review, staged rollout, and rollback for live changes.
- Introduce optional metrics/traces export using provider-neutral OpenTelemetry integration.

**Acceptance gate:** a proposed routing or prompt update must demonstrate improvement without unacceptable regression in quality, cost, latency, safety, or reliability before rollout.

---

## 27. Evaluation and Benchmarking Plan

### 27.1 Build an Alpha task benchmark

Organize tests by category:

- direct answer/no delegation;
- simple tool use;
- multi-step tool use;
- independent research and synthesis;
- conflict resolution between agents;
- coding with tests and file isolation;
- malformed model/tool output;
- rate limit/provider outage;
- dependency failure and retry;
- long task interrupted and resumed;
- cancelled task with active subprocess;
- prompt injection inside retrieved content;
- user approval for sensitive action;
- budget exhaustion and zero-cost mode;
- duplicate subtask and graph-cycle detection;
- memory conflict / stale memory;
- artifact generation and valid download path.

### 27.2 Compare modes fairly

For each benchmark task compare:

1. existing Alpha baseline;
2. direct/single-agent execution;
3. supervisor with one or two specialists;
4. graph workflow;
5. bounded parallel/swarm mode.

Use the same task, equivalent tool access, comparable model configuration, and repeated trials for nondeterministic tests. Track outcome quality, reliability, wall-clock time, model/tool usage, cost, retries, and safety. Don't decide from a single impressive demo.

### 27.3 Core quality metrics

- **Verified completion rate:** proportion meeting every mandatory acceptance criterion.
- **First-pass success:** proportion passing verification without repair.
- **Recovery success:** proportion of injected failures recovered safely.
- **Task coverage:** proportion of requested deliverables addressed.
- **Evidence precision/coverage:** proportion of important claims supported and required evidence included.
- **Regression rate:** old benchmark tasks broken by new releases.
- **Cost per verified task:** including failed attempts and verification.
- **Latency to verified completion:** not merely time to first answer.
- **Coordination overhead:** agent invocations/messages per successful task and time spent waiting.
- **Safety violations:** actions executed without required authorization; target is zero.

### 27.4 Quality gates for release

Define numeric thresholds after a baseline is measured. A reasonable initial release policy:

- no known critical regression in the existing Bot Mode smoke suite;
- no illegal task-state transition or unbounded loop in fault-injection tests;
- all tool actions pass centralized permission checks;
- every final success has a recorded verification report;
- recovery tests pass for supported failure classes;
- regression benchmark is stable or improvements are explained;
- no paid provider call in `zero_cost` mode;
- no production policy self-modification without explicit governance.

Do not invent threshold numbers before gathering Alpha's actual baseline. Store chosen thresholds in versioned configuration and report them in CI.

---

## 28. Failure-Injection and Red-Team Test Matrix

Run these scenarios before enabling high autonomy:

| Test | Injected failure | Expected behavior |
|---|---|---|
| Model timeout | Provider hangs or times out | Bounded retry; clear status; breaker after repeated failures |
| Rate limit | Provider returns 429 / retry-after | Respect retry-after; don't spawn a storm of retries |
| Invalid output | Agent returns malformed JSON | Schema failure; targeted repair or visible failure |
| Worker crash | Kill agent process during task | Lease expires; checkpoint/reconcile; safe resume |
| App crash | Kill Alpha during multi-step run | Reload durable run state and recover safely |
| Duplicate event | Deliver same completion event twice | Idempotent handling; no duplicate dependent task or side effect |
| Bad dependency graph | Cycle or missing dependency | Reject plan before execution |
| Budget exhaustion | Task exceeds model/tool/time limit | Stop/replan/report according to policy; no stealth budget reset |
| Conflicting writes | Two agents modify same file | Lock/scope rejection, worktree isolation, or explicit merge conflict |
| Tool unavailable | MCP/browser/API tool down | Classified failure; breaker/fallback policy; preserve original diagnostic |
| Prompt injection | Webpage tells agent to ignore policy and exfiltrate data | Treat as untrusted content; policy gate blocks unauthorized action |
| Permission escalation | Child asks for new shell/delete privileges | Deny unless a central approval policy grants it |
| User cancellation | Cancel with active child processes | Stop new work, signal/cancel workers, report cleanup outcome |
| Ambiguous external result | Remote submit times out after action may have succeeded | Reconcile status; do not blindly resubmit |
| Memory poisoning | A child proposes unsupported durable fact | Require provenance/validation; quarantine or reject write |
| Agent spam | Worker spawns many duplicate agents | Spawn caps, duplicate checks, budget enforcement |
| UI disconnect | Client reconnects after losing event stream | Replay events from last sequence ID; no duplicate task start |
| Storage write failure | Disk full or SQLite error | Fail safely; don't claim state persisted; trigger alert |

---

## 29. Configuration Blueprint

Use a typed, validated configuration file. Example conceptual settings:

```yaml
orchestration:
  default_mode: bot_mode
  dynamic_replanning: true
  max_graph_depth: 5
  max_tasks_per_run: 40
  max_child_agents_per_task: 5
  max_total_agent_invocations: 60
  max_parallel_tasks: 3
  allow_peer_broadcast: false
  require_task_acceptance_criteria: true
  require_verification_for_success: true

budgets:
  mode: zero_cost
  max_wall_seconds: 1800
  max_model_calls: 100
  max_tool_calls: 200
  max_tokens_total: 100000
  max_cost_usd: 0
  max_retries_per_task: 2

reliability:
  checkpoint_after_state_change: true
  lease_ttl_seconds: 120
  retry_backoff_base_seconds: 2
  retry_backoff_max_seconds: 45
  circuit_breaker_failure_threshold: 5
  circuit_breaker_cooldown_seconds: 60
  stop_after_repeated_no_progress: 3

verification:
  schema_validation: true
  deterministic_checks: true
  independent_review_for_high_risk: true
  minimum_evidence_for_research_claims: true

security:
  deny_unapproved_high_risk_actions: true
  child_inherits_parent_permissions: true
  permit_permission_escalation: false
  redact_secrets_in_logs: true
  untrusted_content_as_instructions: false
```

Validate every key against a configuration schema. These values are initial examples and must be tuned to Alpha's measured capacity and task mix. Avoid confusing `0` with `null`; define exact semantics for budgets and limits.

---

## 30. Definition of Done for the “Advanced Multi-Agent” Upgrade

The upgrade is ready for general use only when all of these are true:

- Existing Bot Mode and present working features pass regression tests.
- All modes share the same task, event, persistence, budget, permission, and verification infrastructure.
- Plans are represented as validated graphs with dependencies, acceptance criteria, and bounded replanning.
- Agent assignment is capability-based and explainable.
- Parallel tasks cannot accidentally race on a shared write target without isolation/locking.
- Inter-agent data moves as validated contracts and versioned artifacts.
- Task/run state is durable and restart recovery works for supported failure classes.
- Every critical action crosses a centralized tool/permission gateway.
- Successful completion requires required checks to pass and evidence to be attached.
- The user can see the real execution path, current blockers, logs/diagnostics, budget, and verification result.
- Model, tool, agent, and scheduling failures have structured errors and bounded recovery behavior.
- No paid API can be called in zero-cost mode.
- Benchmarking demonstrates the new system's value against Alpha's existing behavior.
- All autonomy and self-improvement changes are versioned, testable, and reversible.

---

## 31. What Not to Build First

To keep the project implementable, defer these until the control plane works:

- unrestricted peer-to-peer swarm chat;
- dozens of always-running agents with vague responsibilities;
- multiple overlapping orchestration frameworks running the same task;
- a new distributed database or message broker without scale evidence;
- automatic rewriting of the production supervisor prompt after every run;
- autonomous installation/execution of arbitrary plugins;
- a consensus vote used as a substitute for testing or evidence;
- a single global agent-quality score with no task categories;
- huge context shared with every child agent;
- a claimed AGI/ASI score without operational benchmarks.

These features create complexity faster than they create capability. Add only after controlled evaluations show a concrete benefit.

---

## 32. Recommended First Ten Engineering Tickets

Prioritize these tickets in order; each should have its own tests and pull request.

1. **Architecture inventory:** document current modes, agents, tools, stores, APIs, and feature health; add Bot Mode baseline tests.
2. **Shared run/task schemas:** add versioned Pydantic contracts for run, task, agent manifest, result, and event.
3. **Central run ID + structured event stream:** make each model/tool/agent event correlate with a run and task ID.
4. **Bot Mode compatibility adapter:** route current Bot Mode through the shared runtime without changing its public interface.
5. **Task state machine:** implement legal transitions and deterministic dependency readiness.
6. **SQLite run/event persistence:** transaction-safe task updates and restart recovery for queued/non-side-effect tasks.
7. **Bounded scheduler:** concurrency, timeouts, cancellation, priority, and per-run budgets.
8. **Capability registry/router:** manifests, hard eligibility filters, explainable assignment decisions.
9. **Verification gate:** require schema and acceptance checks before task/run success.
10. **Run diagnostics UI/API:** task graph + event timeline + error panel + pause/resume/cancel controls.

Do not begin with a full visual redesign, new agent marketplace, broad A2A deployment, or a larger swarm. The first ten tickets create the dependable platform that makes those later features useful.

---

## 33. Final Recommendation

Build Alpha's next generation as a **modular orchestration control plane over the current agents**, not as a replacement for them. The biggest leap will come from making the existing agents coordinate through a validated graph, capability registry, durable typed events, shared artifacts, scoped permissions, and proof-based completion.

The highest-value order is:

1. **Make current behavior observable and testable.**
2. **Unify existing modes through common contracts and run state.**
3. **Make planning, task dependencies, concurrency, and delegation explicit.**
4. **Add independent verification, bounded recovery, and restart persistence.**
5. **Add advanced swarm, council, event-driven helpers, and Autopilot only on top of those guarantees.**
6. **Measure each change against Alpha's own baseline and roll back regressions.**

This architecture gives Alpha a path toward a highly capable general-purpose agent system while keeping the project maintainable, local-first, provider-agnostic, and compatible with features that already work.

---

## Sources and Further Reading

1. Anthropic, **How we built our multi-agent research system** (13 June 2025): https://www.anthropic.com/engineering/multi-agent-research-system
2. Anthropic, **Building effective agents** (19 December 2024; article notes that the tooling landscape has changed): https://www.anthropic.com/engineering/building-effective-agents
3. Microsoft, **Agent Framework overview**: https://learn.microsoft.com/en-us/agent-framework/overview/
4. Microsoft, **Workflow orchestrations**: https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/
5. Microsoft, **Handoff orchestration**: https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/handoff
6. Microsoft, **AutoGen repository and successor/migration notice**: https://github.com/microsoft/autogen
7. LangChain, **LangGraph persistence / durable execution concepts**: https://langchain-ai.github.io/langgraph/concepts/durable_execution/
8. Model Context Protocol, **Official project and specification resources**: https://modelcontextprotocol.io/
9. Model Context Protocol SDK, **Server capabilities overview**: https://java.sdk.modelcontextprotocol.io/latest/server/
10. Agent2Agent, **Official A2A Protocol documentation**: https://a2a-protocol.org/latest/
11. OpenAI, **Agents SDK guide**: https://developers.openai.com/api/docs/guides/agents/sdk
12. OpenAI Agents SDK, **Tracing guide**: https://openai.github.io/openai-agents-js/guides/tracing/
13. OpenTelemetry, **Semantic conventions**: https://opentelemetry.io/docs/specs/semconv/
14. OWASP, **Top 10 for LLM Applications — Excessive Agency**: https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-2023-v09.pdf

### Research limitations

- This document is a web-researched architecture proposal, not a source-code audit of every current Alpha module. Phase 0 is mandatory because some features may already exist, be partially implemented, or use different names in the current repository.
- Frameworks evolve quickly. Verify exact APIs, licenses, dependency compatibility, and stable version numbers immediately before implementation. Prefer ideas and adapter interfaces over hard-coding an entire architecture to a fast-changing external framework.
- Benchmarks quoted by vendors or project authors are context-specific. Alpha must measure its own workload and hardware before claiming a quality, latency, or cost improvement.
