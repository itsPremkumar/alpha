# ALPHA APEX AUTOPILOT — MASTER AUTONOMY & EXECUTIVE CONTROL-PLANE SPECIFICATION

**Project:** Alpha AI Agent  
**Feature:** APEX Autopilot / Master Autonomous Execution Layer  
**Version:** 1.0 — Master Design Specification  
**Research date:** 2026-10-06  
**Primary objective:** Give Alpha a single user-controlled autonomy mode that can dynamically plan, decompose, delegate, create agents, select tools/models/workflows, execute long-horizon work, recover from failure, verify completion, persist state, and continue until the objective is actually complete.

---

## 0. Executive summary

APEX is **not another chatbot mode and not another giant prompt**. It is Alpha's **executive control plane**.

When the user activates APEX, Alpha receives an autonomy contract that lets the executive controller coordinate the capabilities already present in the application: planner, goal engine, dynamic workflows, tools, skills, slash commands, research, browser, coding, terminal, memory, MCP, A2A, subagents, swarm, verification, recovery, model routing, scheduling, persistence, and UI state.

The core rule is:

> **The user provides the objective. APEX decides the execution strategy. Existing Alpha runtimes perform the work. Verification decides whether the work is actually complete.**

The architecture therefore separates four concerns:

1. **Executive intelligence** — decide what should happen next.
2. **Execution infrastructure** — perform the selected action.
3. **Policy kernel** — enforce authorization, safety, scope and resource boundaries.
4. **Evidence/verification** — determine whether the desired outcome was achieved.

This is intentionally more powerful than a traditional `/autopilot` slash command. APEX can become the manager of the entire Alpha runtime while preserving a non-bypassable control boundary around destructive, security-sensitive or externally consequential operations.

Modern agent systems increasingly converge on this shape: an agent loop plus tools, delegation/handoffs, guardrails, persistent sessions, tracing, sandboxed execution, explicit workflows, human-in-the-loop checkpoints and durable execution. OpenAI's Agents SDK documents agents, tools, handoffs, guardrails, sessions, sandbox agents and tracing; Microsoft Agent Framework documents graph workflows, concurrency, handoff, group-chat and manager-style orchestration plus checkpoints/resuming; Anthropic distinguishes workflows from dynamically directed agents and recommends grounding autonomous loops in environmental feedback; A2A models long-running work as stateful tasks with artifacts and streaming; MCP 2026-07-28 adds a dedicated Tasks extension for asynchronous tool work. [1][2][3][4][5][6]

---

# 1. Product definition

## 1.1 User-facing concept

Recommended name:

> **APEX Autopilot**

Meaning:

> **Autonomous Planning, Execution & eXecution**

The name is intentionally simple in the UI. The internal architecture should use `APEX` consistently.

Recommended commands:

```text
/apex on
/apex off
/apex status
/apex pause
/apex resume
/apex stop
/apex steer <instruction>
/apex approve <request-id>
/apex reject <request-id>
/apex plan
/apex missions
/apex agents
/apex workflow
/apex failures
/apex evidence
/apex policy
/apex limits
/apex take-over
```

The user should also be able to enable APEX through a prominent UI switch.

## 1.2 Behavioral promise

When APEX is enabled and the user gives an objective such as:

```text
"Find why Alpha's coding workflow is failing and fix it end-to-end."
```

Alpha should be capable of automatically deciding to:

```text
understand objective
→ inspect current state
→ establish success criteria
→ research unfamiliar issues
→ create a goal
→ decompose into subgoals
→ build a dynamic workflow
→ choose execution strategy
→ create/reuse specialist agents
→ allocate workspaces and resources
→ invoke tools
→ run work sequentially or in parallel
→ observe results
→ verify intermediate outcomes
→ detect failures
→ research causes
→ repair/retry/reassign/replan
→ continue
→ run final verification
→ produce artifacts/evidence
→ close the mission
```

The user should not need to manually issue `/research`, `/debug`, `/code`, `/swarm`, `/test`, `/browser`, `/review`, etc. Those become capabilities APEX can activate internally.

## 1.3 Critical distinction

APEX is **not**:

```text
"Give the LLM every permission and tell it to do anything."
```

APEX is:

```text
LLM/Executive decides intent
        ↓
Typed action
        ↓
Capability registry
        ↓
Policy kernel
        ↓
Scoped permission / lease
        ↓
Execution engine
        ↓
Observation
        ↓
Verification
```

The policy kernel must remain authoritative even when APEX is in maximum autonomy mode. This is required because excessive functionality, permissions and autonomy are recognized risks in agentic systems, especially when prompt injection or unexpected model output can influence tool calls. [11][12]

---

# 2. Design principles

## 2.1 Executive, not executor

APEX decides **what should happen** and **who/what should do it**. Existing Alpha engines execute the selected work.

```text
APEX
 ├─ choose research
 ├─ choose agent
 ├─ choose tool
 ├─ choose workflow
 ├─ choose model
 ├─ choose retry strategy
 └─ choose verification path

Existing runtimes
 ├─ run research
 ├─ run agent
 ├─ run tool
 ├─ execute graph
 ├─ call model
 └─ run tests
```

Do not create a second independent lifecycle manager if Alpha already has a single lifecycle owner. APEX should invoke that owner.

## 2.2 Compose existing capabilities

The first implementation task is **integration**, not replacement.

Prefer:

```text
APEX → existing planner
APEX → existing goal engine
APEX → existing workflow engine
APEX → existing swarm system
APEX → existing verification
APEX → existing recovery
```

over:

```text
APEX → duplicate planner
APEX → duplicate run manager
APEX → duplicate agent runtime
```

## 2.3 Ground decisions in the environment

A high-autonomy loop must repeatedly observe actual state. Tool results, test results, files, browser state and service health are ground truth; the model's previous claim is not. Anthropic's agent guidance explicitly emphasizes obtaining ground truth from the environment at each step and using stopping conditions or checkpoints to maintain control. [3]

## 2.4 Prefer the simplest strategy that can solve the task

APEX must be autonomous without being wasteful.

```text
Tiny task       → direct execution
Simple task     → short workflow
Complex task    → dynamic orchestration
Independent set → parallelization
Large research → swarm
High uncertainty→ research + verification
Large coding   → specialist teams + integration
```

Anthropic and Microsoft both emphasize choosing patterns based on task structure rather than using maximum complexity everywhere. [2][3][4]

## 2.5 Verification is part of execution

Completion is a verified state, not a generated sentence.

```text
DONE CLAIM ≠ VERIFIED DONE
```

## 2.6 Every autonomous action is observable

Every important decision, tool call, handoff, task transition, resource allocation, approval and failure should be recorded as structured telemetry.

OpenAI's Agents SDK uses traces/spans to record generations, tool calls, handoffs, guardrails and custom events, providing a useful model for Alpha's observability design. [7]

## 2.7 Long-running work is a first-class workload

APEX missions can run for hours, days or longer, may wait for external input, may survive application restarts, and may resume from durable checkpoints. Durable execution is increasingly treated as a core reliability requirement for long-running agents. [8][9]

---

# 3. APEX architecture

```text
                         ┌────────────────────┐
                         │       USER         │
                         │   Goal / Steering  │
                         └──────────┬─────────┘
                                    │
                                    ▼
                         ┌────────────────────┐
                         │   APEX GATEWAY     │
                         │ /apex + UI + API   │
                         └──────────┬─────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │       APEX EXECUTIVE         │
                    │                              │
                    │ intent · planning · routing  │
                    │ decomposition · decisions   │
                    │ delegation · resource use   │
                    │ replanning · mission control │
                    └──────────────┬───────────────┘
                                   │
                         ┌─────────▼─────────┐
                         │ POLICY / CONTROL  │
                         │ KERNEL            │
                         │ permissions       │
                         │ scope             │
                         │ risk              │
                         │ budgets           │
                         │ approvals         │
                         │ kill switch       │
                         └─────────┬─────────┘
                                   │
                       ┌───────────▼────────────┐
                       │ CAPABILITY GRAPH       │
                       │ tools · skills · modes │
                       │ agents · MCP · A2A     │
                       └───────────┬────────────┘
                                   │
        ┌──────────────────────────┼─────────────────────────┐
        ▼                          ▼                         ▼
┌───────────────┐          ┌───────────────┐         ┌───────────────┐
│ Agent Factory │          │ Workflow/Swarms│         │ Tool Runtime  │
│ specialists   │          │ DAG · waves    │         │ browser/code  │
│ subagents     │          │ delegation     │         │ terminal/MCP  │
└───────┬───────┘          └──────┬────────┘         └──────┬────────┘
        └─────────────────────────┼──────────────────────────┘
                                  ▼
                         ┌─────────────────────┐
                         │  OBSERVE / EVENTS   │
                         └──────────┬──────────┘
                                    ▼
                         ┌─────────────────────┐
                         │ VERIFY / RECOVER    │
                         │ diagnose · repair   │
                         │ retry · reassign    │
                         │ rollback · replan   │
                         └──────────┬──────────┘
                                    ▼
                         ┌─────────────────────┐
                         │ DURABLE MISSION     │
                         │ STATE + CHECKPOINTS │
                         └──────────┬──────────┘
                                    │
                                    └──────→ NEXT DECISION
```

---

# 4. APEX control-plane modules

Required modules:

```text
apex/
├── controller
├── executive
├── mission
├── goals
├── tasks
├── planning
├── replanning
├── decisions
├── policy
├── risk
├── approvals
├── capabilities
├── command_router
├── mode_controller
├── agent_factory
├── agent_registry
├── swarm_controller
├── workflow_controller
├── tool_router
├── skill_router
├── mcp_controller
├── a2a_controller
├── research_controller
├── model_router
├── context_manager
├── memory_controller
├── resource_manager
├── scheduler
├── lease_manager
├── workspace_manager
├── checkpoint_manager
├── verification
├── recovery
├── event_bus
├── event_store
├── telemetry
├── audit
└── metrics
```

APEX must expose these as typed services/interfaces rather than making every component call every other component directly.

---

# 5. Autonomy contract

When APEX turns on, create an immutable-at-runtime mission contract plus mutable task-local controls.

Example:

```yaml
apex:
  enabled: true
  autonomy_profile: maximum_internal

  authority:
    chat: true
    slash_commands: true
    tools: true
    skills: true
    modes: true
    agents: true
    subagents: true
    swarm: true
    workflows: true
    research: true
    memory: true
    browser: true
    coding: true
    terminal: true
    git: true
    mcp: true
    a2a: true
    scheduling: true
    model_routing: true
    task_local_settings: true

  execution:
    autonomous_delegation: true
    autonomous_research: true
    autonomous_replanning: true
    autonomous_recovery: true
    autonomous_verification: true
    autonomous_context_management: true

  budgets:
    max_active_agents: 12 # Operational caps remain finite
    max_parallel_tasks: 8
    max_delegation_depth: 5
    max_replans: 20
    max_retries_per_failure_class: 4 # Infinite retries can duplicate harmful actions
    max_runtime_minutes: null
    max_tool_calls: null
    max_total_tokens: null # null means no APEX per-session spending ceiling

  controls:
    pause_allowed: true
    user_takeover: true
    emergency_stop: true

  protected_actions:
    destructive_filesystem: approval
    credential_changes: approval
    identity_changes: approval
    external_publication: approval
    irreversible_external_action: approval
```

### Important

“Maximum autonomy” means **maximum permitted autonomy inside the configured scope**. It must not mean that an LLM can disable the policy engine, erase the audit history, bypass an approval rule, expose secrets, or remove the emergency stop.

---

# 6. Autonomy profiles

The UI can expose simple choices while APEX internally uses richer execution policies.

## 6.1 OFF

```text
ordinary Alpha behavior
no automatic mission control
```

## 6.2 ASSIST

```text
Alpha plans
Alpha proposes
user approves meaningful actions
```

## 6.3 AUTONOMOUS

```text
Alpha plans
Alpha delegates
Alpha executes
Alpha recovers
Alpha verifies
```

## 6.4 APEX MAX

```text
Alpha dynamically controls internal capabilities
including modes, agents, subagents, swarm, tools,
workflows, research, model routing and task-local settings
within the immutable policy boundary.
```

---

# 7. Global vs task-local authority

A major design rule is **scope separation**.

## Task-local changes

Allowed autonomously:

```text
reasoning effort
number of researchers
task priority
tool ordering
agent template
parallelism
context strategy
model selection
retry strategy
research depth
verification depth
```

## Workspace changes

Potentially autonomous with project policy:

```text
project skills
project agent templates
project workflows
repository task settings
workspace-specific tool availability
```

## Global changes

Require policy-mediated configuration transactions:

```text
global model defaults
authentication configuration
credential stores
system-wide plugins
startup services
security policies
global network policy
```

Any global mutation should use:

```text
proposal
→ diff
→ risk classification
→ authorization
→ apply
→ health check
→ rollback if failed
```

---

# 8. Unified command registry

Slash commands, bot commands and internal agent commands should use one command registry.

Example:

```yaml
commands:
  research:
    intent: research
    capability: research.engine

  code:
    intent: coding
    capability: code.engine

  debug:
    intent: debug
    capability: debugging.engine

  swarm:
    intent: swarm
    capability: swarm.engine

  review:
    intent: review
    capability: verification.review
```

Human:

```text
/research find current MCP changes
```

APEX internal:

```text
CommandIntent(
  command="research",
  args={...}
)
```

APEX itself can also invoke the same command capability.

This prevents the architecture from maintaining separate semantics for:

```text
human slash command
bot command
agent tool
workflow node
APEX action
```

All should converge on a typed command/capability layer.

---

# 9. Mode arbitration engine

Modes must become **capabilities**, not mutually exclusive islands.

Example mission:

```text
USER: fix failing feature

APEX mode sequence:

observe
→ research
→ debug
→ coding
→ testing
→ browser verification
→ security review
→ final report
```

The UI may show only:

```text
APEX AUTOPILOT: ACTIVE
```

while the mission timeline shows internal mode transitions.

### Mode arbitration inputs

```text
task category
risk
uncertainty
dependencies
available capabilities
resource state
current failures
verification requirements
```

### Mode arbitration outputs

```text
strategy
selected mode(s)
agent roles
workflow pattern
model class
toolset
verification plan
```

---

# 10. Mission object

Every APEX activation around a goal should create a `Mission`.

Conceptual schema:

```python
Mission:
    id
    user_id
    session_id
    parent_mission_id
    objective
    normalized_objective
    success_criteria
    constraints
    autonomy_profile
    policy_snapshot
    priority
    deadline
    status
    current_strategy
    plan_version
    root_goal_id
    created_at
    updated_at
    started_at
    completed_at
    resource_budget
    evidence_policy
    current_context
    last_checkpoint_id
```

Mission states:

```text
CREATED
UNDERSTANDING
PLANNING
EXECUTING
PAUSED
WAITING
BLOCKED
RECOVERING
VERIFYING
COMPLETED
PARTIAL
FAILED
CANCELLED
```

---

# 11. Goal Operating System

The goal engine should represent work as a hierarchy.

```text
MISSION
 ├─ GOAL
 │   ├─ SUBGOAL
 │   │   ├─ TASK
 │   │   ├─ TASK
 │   │   └─ TASK
 │   └─ SUBGOAL
 └─ GOAL
```

Each goal should have:

```text
objective
acceptance criteria
priority
state
dependencies
owner
contributors
inputs
outputs
constraints
budget
risk
verification method
evidence requirements
failure policy
```

### Goal splitting

APEX should autonomously:

```text
split
actionize
merge
reprioritize
cancel
reopen
escalate
```

Example:

```text
"Build feature X"

→ requirements
→ architecture
→ backend
→ frontend
→ tests
→ security
→ documentation
→ integration
```

The exact decomposition should depend on the repository and task.

---

# 12. Success criteria compiler

Before complex execution, APEX should convert the natural-language objective into explicit acceptance checks.

Example:

```yaml
success_criteria:
  - id: api
    check: endpoint_returns_200
  - id: ui
    check: feature_visible_in_browser
  - id: tests
    check: test_suite_passes
  - id: regression
    check: existing_behavior_preserved
```

This allows the final verification engine to answer:

```text
Which acceptance criteria passed?
Which failed?
Which remain unverified?
```

---

# 13. Dynamic planner

APEX should not produce only one static mega-plan.

Use **receding-horizon planning**.

```text
current state
→ choose next high-value work
→ execute
→ observe
→ update world model
→ replan
```

The plan should have versions:

```text
v1 initial plan
v2 after discovery
v3 after failure
v4 after repair
```

Each plan patch should record:

```text
reason
trigger event
changed nodes
new dependencies
estimated cost
risk effect
```

---

# 14. Workflow graph

Represent the mission as a mutable directed graph.

Supported node operations:

```text
ADD
REMOVE
SPLIT
MERGE
REORDER
RETRY
REASSIGN
PAUSE
RESUME
CANCEL
REPLACE
```

Supported edge types:

```text
depends_on
optional
blocks
produces
consumes
review_of
fallback_for
compensates
```

Supported execution patterns:

```text
SEQUENTIAL
CONCURRENT
ROUTING
HANDOFF
ORCHESTRATOR_WORKERS
MAP_REDUCE
VOTING
DEBATE
EVALUATOR_OPTIMIZER
HIERARCHICAL_SWARM
PIPELINE
EVENT_DRIVEN
```

Microsoft Agent Framework currently documents sequential, concurrent, handoff, group-chat and Magentic manager-style orchestration, while Anthropic documents routing, parallelization and orchestrator-workers as common production patterns. [2][4]

---

# 15. Agent Factory

APEX must be able to create temporary specialist agents automatically.

## Agent template

```yaml
agent:
  id: generated
  role: frontend-debugger
  mission_id: ...
  goal_id: ...
  task_id: ...
  parent_agent_id: ...

  instructions: ...
  capabilities: ...
  tools: ...
  skills: ...
  model: ...
  context_policy: ...
  memory_scope: task
  workspace: ...
  resource_budget: ...
  verification_contract: ...
  expiration_policy: task-complete
```

## Agent creation decision

APEX asks:

```text
Is specialization useful?
Is work independent?
Will context isolation improve quality?
Does expected benefit exceed cost?
Is a reviewer needed?
```

If not:

```text
execute directly
```

If yes:

```text
spawn specialist
```

OpenAI's current Agents SDK supports both manager-style “agents as tools” and handoffs, and its sandbox agents can run specialists in isolated workspaces with resumable sessions. [1][13]

---

# 16. Agent role taxonomy

Recommended built-in dynamic roles:

```text
planner
researcher
fact_checker
browser_operator
frontend_engineer
backend_engineer
database_engineer
systems_engineer
terminal_operator
git_operator
security_reviewer
code_reviewer
qa_engineer
integration_engineer
performance_engineer
ux_reviewer
documentation_agent
release_agent
failure_investigator
recovery_agent
monitoring_agent
memory_curator
model_evaluator
```

These are templates, not mandatory permanent agents.

---

# 17. Recursive delegation

An agent may delegate only within a bounded delegation graph.

Recommended controls:

```yaml
max_delegation_depth: 5
max_children_per_agent: 6
max_total_agents: 20
max_active_agents: 12
```

Example:

```text
APEX
 └─ engineering-manager
     ├─ backend-agent
     │   └─ api-test-agent
     ├─ frontend-agent
     └─ security-reviewer
```

Each child must have:

```text
parent task
specific objective
bounded permissions
bounded workspace
bounded budget
completion criteria
```

---

# 18. Swarm controller

Do not use a fixed number of workers.

Calculate swarm size from:

```text
task volume
parallel independence
latency benefit
model capacity
CPU/RAM/GPU capacity
cost budget
verification complexity
```

Example decision:

```text
5 independent research questions
→ 5 workers

3 dependent coding tasks
→ sequential/handoff

500 independent documents
→ bounded map/reduce waves
```

### Swarm controls

```text
spawn
scale_up
scale_down
pause_wave
cancel_worker
replace_worker
merge_results
vote
rank
criticize
synthesize
```

---

# 19. Capability graph

Every usable capability should advertise:

```text
id
kind
version
status
health
availability
cost
latency
risk
permissions
dependencies
input_schema
output_schema
supports_async
supports_cancel
supports_streaming
supports_resume
```

Example:

```yaml
capability:
  id: browser.navigate
  kind: tool
  status: healthy
  risk: medium
  supports_async: true
  supports_cancel: true
```

Capability categories:

```text
TOOLS
SKILLS
COMMANDS
MODES
AGENTS
WORKFLOWS
MCP_SERVERS
A2A_AGENTS
MODELS
COMPUTE
STORAGE
BROWSERS
SANDDBOXES
SCHEDULERS
```

---

# 20. Capability discovery and health

Before selecting a capability, APEX should be able to query:

```text
exists?
loaded?
authorized?
healthy?
compatible?
available?
resource-feasible?
```

For example:

```text
Browser tool
→ installed: yes
→ connection: healthy
→ permission: yes
→ current session: available
→ selected
```

If a capability becomes unhealthy:

```text
mark unhealthy
remove from candidate pool
select alternative
continue
```

---

# 21. Tool routing engine

APEX should select tool sequences, not just individual tools.

Example:

```text
research task
→ search
→ fetch
→ extract
→ normalize
→ deduplicate
→ cross-check
→ cite
→ synthesize
```

Coding task:

```text
inspect repo
→ read files
→ search symbols
→ run tests
→ patch
→ format
→ lint
→ test
→ browser check
```

Tool routing should consider:

```text
semantic fit
health
cost
latency
risk
permissions
schema compatibility
previous success rate
```

---

# 22. Research-first intelligence

APEX should automatically trigger research for:

```text
unknown technology
unfamiliar error
current external facts
rapidly changing APIs
conflicting evidence
repeated tool failure
architecture uncertainty
large implementation task
security-sensitive decisions
```

Research escalation:

```text
LEVEL 0
known facts / local inspection

LEVEL 1
single search

LEVEL 2
multiple independent sources

LEVEL 3
source comparison + primary documentation

LEVEL 4
deep research swarm

LEVEL 5
research + reproduce/test findings
```

A research task is complete only when its evidence policy is satisfied.

---

# 23. Evidence system

Use first-class evidence records.

```yaml
evidence:
  id: ...
  claim_id: ...
  source_type: url|file|tool|test|runtime
  source_ref: ...
  excerpt_ref: ...
  retrieved_at: ...
  freshness: ...
  trust_score: ...
  supports: true|false|partial
```

Evidence should support:

```text
research claims
bug diagnoses
completion claims
verification claims
model/tool health claims
```

---

# 24. Memory architecture

Separate memory scopes:

```text
ephemeral turn memory
run memory
task memory
goal memory
mission memory
project memory
agent memory
global knowledge
```

APEX should decide whether an observation belongs in long-term memory.

Rules:

```text
temporary fact → task/run memory
verified project fact → project memory
stable system convention → global/project knowledge
uncertain hypothesis → hypothesis store, NOT authoritative memory
```

Memory writes should store provenance and confidence.

---

# 25. Context engineering

APEX should dynamically choose context strategy:

```text
full history
compressed history
retrieved history
artifact summary
tool-result summary
specialist handoff packet
fresh context
```

A handoff packet should be structured:

```yaml
handoff:
  objective: ...
  completed: ...
  remaining: ...
  important_findings: ...
  files: ...
  commands: ...
  failures: ...
  constraints: ...
  verification_status: ...
  next_recommended_action: ...
```

Do not dump an entire conversation into every specialist by default.

---

# 26. Model router

Model selection should be dynamic.

Inputs:

```text
task difficulty
reasoning requirement
latency
cost
context size
tool calling support
structured output support
vision capability
coding ability
current health
provider availability
```

Example policy:

```text
simple classification
→ small/fast model

complex planning
→ strong reasoning model

vision/browser task
→ vision-capable model

local/private task
→ local model where feasible

model failure
→ health-based fallback
```

Important:

```text
fallback ≠ silent degradation
```

The switch must be observable and recorded.

---

# 27. Resource scheduler

Track:

```text
tokens
cost
LLM calls
tool calls
agents
parallel tasks
CPU
RAM
GPU
VRAM
disk
network
browser sessions
containers
runtime duration
retries
replans
```

Resource decisions:

```text
spawn more agents
reduce parallelism
switch model
serialize tasks
pause low-priority task
compact context
stop wasteful retry loop
```

---

# 28. Agent leases and heartbeats

Every background worker should have a lease.

```yaml
lease:
  id: ...
  agent_id: ...
  task_id: ...
  acquired_at: ...
  heartbeat_at: ...
  expires_at: ...
```

If heartbeat stops:

```text
lease expires
→ agent considered stale
→ inspect checkpoint
→ recover task
→ reassign/replace
```

This supports laptop restart, process crash and worker loss.

---

# 29. Workspace isolation

For coding tasks, avoid shared mutable worktrees among concurrent agents.

Recommended:

```text
mission
 ├─ agent-A worktree
 ├─ agent-B worktree
 └─ agent-C worktree
```

Then:

```text
agent result
→ review
→ merge candidate
→ tests
→ integration
```

This prevents simultaneous edits from corrupting one another and matches production multi-agent orchestration practice where isolated workspaces/sandboxes are used for specialized workers. OpenAI's current sandbox-agent design explicitly supports real isolated workspaces. [1]

---

# 30. Git orchestration

For repository missions, APEX should automatically choose a Git workflow.

Example:

```text
MISSION
 ↓
create branch/worktree
 ↓
assign agent
 ↓
implement
 ↓
agent self-test
 ↓
review
 ↓
merge candidate
 ↓
integration tests
 ↓
final verification
```

Recommended main-branch policy:

```text
ONE merge owner
MANY isolated workers
```

Do not blindly merge all agent branches.

---

# 31. Command execution control

Terminal/PowerShell/shell capability should have structured command requests.

```yaml
command_request:
  shell: powershell
  command: ...
  cwd: ...
  timeout_ms: ...
  expected_output: ...
  risk: ...
  allow_network: false
  allow_write: true
```

APEX should log:

```text
exact command
working directory
exit code
stdout/stderr metadata
start/end time
process id
risk decision
```

Do not hide command failures behind friendly summaries.

---

# 32. Browser/computer control

Browser actions should become capabilities such as:

```text
navigate
read
inspect
click
fill
upload
scroll
wait
capture
extract
verify
```

APEX should use browser automation for:

```text
research
UI testing
workflow verification
site interaction
form automation
```

Browser state must be included in checkpoints where long-running browser tasks are allowed.

---

# 33. MCP integration

Treat MCP as a capability transport layer.

APEX should discover:

```text
server capabilities
tools
schemas
health
authorization
async/task support
```

The MCP 2026-07-28 release moved long-running tool work into the `io.modelcontextprotocol/tasks` extension, with task handles, retrieval, updates and cancellation semantics. APEX should map MCP asynchronous tasks into its own task lifecycle rather than pretending every tool is synchronous. [5][6]

Recommended mapping:

```text
MCP Task
   ↓
Alpha Task Adapter
   ↓
APEX Task
   ↓
Mission
```

Never grant an MCP server more permissions than the consuming mission requires.

---

# 34. A2A integration

A2A should be treated as an external agent capability.

Map:

```text
A2A Agent
→ discovered capability
→ task
→ artifact
→ status event
```

A2A's current specification models tasks as stateful units with IDs, status, artifacts and streaming/status updates, which maps naturally to Alpha's mission/task system. [6]

APEX should be able to:

```text
discover agent
validate capability
start task
monitor task
receive artifact
request update
cancel where supported
verify result
```

---

# 35. Permissions architecture

Permission decisions must be separate from LLM output.

Use three primary outcomes:

```text
ALLOW
ASK
DENY
```

Support scoped rules by:

```text
tool
agent
mission
workspace
path
command pattern
network destination
resource
risk class
```

OpenCode's current permission design is a useful reference: permissions can be allow/ask/deny, can use command/path patterns, and can be overridden per agent. [10]

---

# 36. Risk engine

Assign every action a risk class.

```text
R0  observation/read-only
R1  reversible local mutation
R2  significant local mutation
R3  external side effect
R4  irreversible/destructive/high-impact
```

Example:

```text
read file                 R0
run tests                  R0/R1
edit source                R1
create branch              R1
merge branch               R2
delete large tree           R4
publish publicly            R4
change credential          R4
```

The risk engine must be deterministic and policy-driven.

---

# 37. Prompt-injection defense

APEX must assume that untrusted content can attempt to control the agent.

Threat sources:

```text
web pages
repository files
README files
issues
emails
MCP results
A2A agents
embedded documents
browser content
tool outputs
```

Never treat retrieved text as executable authority.

Use:

```text
origin labels
trust boundaries
content/data separation
tool-specific sanitization
policy evaluation outside the model
least privilege
approval for risky side effects
```

OWASP identifies prompt injection and excessive agency as major risks for agent systems, including attacks arriving through indirect content and compromised plugins/agents. [11][12]

---

# 38. Secrets and credentials

APEX should not place raw secrets into ordinary model context.

Use:

```text
credential broker
secret references
short-lived tokens
scoped credentials
redaction
provider-specific auth
```

Agents should request:

```text
secret_ref="github.token"
```

rather than receiving:

```text
actual token string
```

Logs must never contain credentials.

---

# 39. Human-in-the-loop

APEX should avoid unnecessary step-by-step approvals while preserving meaningful decision gates.

Good model:

```text
show overall strategy
→ approve strategy if needed
→ autonomous execution
→ interrupt only on protected events
```

Anthropic's 2026 discussion of trustworthy agents describes this as moving human oversight from every action toward plan-level review while still allowing intervention during execution. [14]

Approval request schema:

```yaml
approval:
  id: ...
  mission_id: ...
  action: ...
  reason: ...
  risk: R4
  affected_resources: ...
  reversible: false
  expires_at: ...
  proposed_by: apex
```

---

# 40. Emergency stop

The emergency stop must be implemented outside the model.

Required effects:

```text
stop new planning
stop new task dispatch
cancel cancellable tasks
terminate child processes where safe
close browser automation where safe
release leases
mark mission INTERRUPTED
persist checkpoint
```

APEX must not be able to disable its own emergency stop.

---

# 41. Pause/resume

Pause should be different from stop.

### Pause

```text
no new actions
allow safe in-flight actions to finish
checkpoint
wait
```

### Stop

```text
terminate as much as safely possible
checkpoint final state
transition to STOPPED
```

### Resume

```text
load checkpoint
revalidate capabilities
revalidate leases
revalidate external state
continue from safe boundary
```

---

# 42. Failure management

Every failure must be classified.

Recommended taxonomy:

```text
TRANSIENT_NETWORK
RATE_LIMIT
AUTHORIZATION
INVALID_INPUT
TOOL_SCHEMA
TOOL_RUNTIME
MODEL_FAILURE
MODEL_TIMEOUT
HALLUCINATED_ASSUMPTION
ENVIRONMENT_FAILURE
DEPENDENCY_FAILURE
CODE_FAILURE
TEST_FAILURE
BROWSER_FAILURE
AGENT_STUCK
AGENT_CRASH
WORKFLOW_INVALID
STATE_CORRUPTION
RESOURCE_EXHAUSTION
SECURITY_BLOCK
UNKNOWN
```

---

# 43. Autonomous recovery matrix

```text
Transient network
→ bounded retry + backoff

Rate limit
→ wait + alternate provider if policy allows

Invalid tool arguments
→ repair schema / regenerate arguments

Wrong tool
→ choose alternative capability

Unfamiliar error
→ research error + inspect environment

Code failure
→ failure investigator + coding agent

Test failure
→ reproduce + diagnose + patch + rerun

Agent stuck
→ inspect progress + interrupt + reassign

Agent crash
→ checkpoint + replace agent

Plan invalid
→ patch/replan graph

Repeated failure
→ escalate reasoning / strategy

Security block
→ do not bypass; request user/policy decision
```

---

# 44. The DAPER recovery loop

A useful operational loop is:

```text
DETECT
 ↓
ANALYZE
 ↓
PLAN
 ↓
EXECUTE
 ↓
REPORT
 ↓
VERIFY
```

Temporal has published this detect/analyze/plan/execute/report pattern for proactive issue detection and repair workflows; the same structure fits Alpha's self-healing loop. [9]

APEX should extend it with verification and replanning:

```text
Detect → Analyze → Research → Plan → Execute → Verify → Replan
```

---

# 45. No silent failures

Every failure must be visible in:

```text
runtime logs
mission timeline
failure store
APEX UI
structured API
optional user notification
```

Each failure record should contain:

```text
failure_id
mission_id
task_id
agent_id
stage
error_type
message
stack trace reference
input reference
tool reference
attempt number
root-cause hypothesis
evidence
recovery action
recovery result
```

---

# 46. Verification engine

Every major task should define a verification method.

Examples:

```text
code → tests/build/runtime/browser
research → evidence/source validation
file operation → path/hash/state check
API change → endpoint integration test
UI change → browser test
agent delegation → child task/artifact check
workflow change → end-to-end replay
```

Verification should be able to return:

```text
VERIFIED
PARTIALLY_VERIFIED
UNVERIFIED
FAILED
```

Only `VERIFIED` should close a mission unless the user explicitly accepts a partial result.

---

# 47. Verification levels

```text
V0  syntax/basic output
V1  local invariants
V2  unit/integration tests
V3  end-to-end behavior
V4  independent review
V5  adversarial/regression verification
```

APEX should choose depth based on:

```text
risk
complexity
user requirement
blast radius
uncertainty
```

---

# 48. Independent reviewer agents

For critical tasks, the implementing agent should not be the sole verifier.

Example:

```text
Implementer
     ↓
Independent Reviewer
     ↓
Test Agent
     ↓
Integration
```

For high-risk changes:

```text
Implementer
 ↓
Security reviewer
 ↓
Correctness reviewer
 ↓
Test suite
 ↓
Runtime/E2E verification
```

---

# 49. Long-running mission architecture

Mission state must survive:

```text
process crash
laptop reboot
network outage
model provider outage
tool restart
browser restart
worker loss
application update
```

Persist:

```text
mission
plan versions
goals
tasks
agent registry
agent leases
tool executions
decisions
approvals
failures
evidence
artifacts
budgets
checkpoints
current state
```

Microsoft's current durable extension explicitly supports persisted sessions, checkpoints, failure recovery and distributed execution; Temporal similarly focuses on durable execution for agents running for days/weeks/months. [8][9]

---

# 50. Idempotency

Autonomous retries create duplicate-action risks.

Every side-effecting operation should have an idempotency strategy.

Example:

```text
idempotency_key = mission_id + task_id + action_id
```

Before retrying:

```text
was side effect already applied?
```

This is essential for:

```text
payments
messages
publishing
Git operations
file mutations
API POSTs
external automation
```

---

# 51. Event bus

Define a standard event model.

Examples:

```text
mission.created
mission.started
mission.paused
mission.resumed
mission.completed
mission.failed

goal.created
goal.updated
goal.completed

task.created
task.started
task.waiting
task.failed
task.completed

agent.created
agent.started
agent.heartbeat
agent.stalled
agent.completed
agent.failed
agent.replaced

workflow.created
workflow.patched
workflow.replanned

capability.discovered
capability.health_changed

tool.started
tool.completed
tool.failed

research.started
research.completed

verification.started
verification.failed
verification.passed

approval.required
approval.granted
approval.rejected

budget.warning
budget.exhausted

checkpoint.created
checkpoint.restored
```

---

# 52. Event sourcing / audit trail

Maintain an append-only mission event history where practical.

A mission can therefore be reconstructed as:

```text
initial state
+ events
= current state
```

Use hash chaining or immutable IDs for high-integrity audit streams if needed.

The audit stream must be separate from ordinary human-facing logs.

---

# 53. Observability architecture

Use three layers.

## Logs

Detailed structured events.

## Metrics

Aggregates such as:

```text
task success rate
recovery success rate
verification pass rate
agent utilization
tool latency
model latency
token use
cost
replan frequency
failure rate
```

## Traces

End-to-end mission flow:

```text
Mission
 ├─ Decision
 ├─ LLM
 ├─ Agent
 │   ├─ Tool
 │   └─ Tool
 ├─ Verification
 └─ Recovery
```

OpenAI's Agents SDK trace/span model is a useful reference for this level of visibility. [7]

---

# 54. Decision ledger

Every high-impact APEX decision should produce a structured record.

```json
{
  "decision_id": "...",
  "mission_id": "...",
  "decision": "spawn_agents",
  "strategy": "parallel",
  "reason_codes": ["independent_work", "latency_reduction"],
  "evidence_refs": ["..."],
  "confidence": 0.91,
  "policy_result": "allow",
  "budget_before": {},
  "budget_after": {}
}
```

Do not store private chain-of-thought. Store concise, auditable decision metadata and evidence instead.

---

# 55. APEX UI — control center

Recommended screens:

```text
MISSION
PLAN
GRAPH
AGENTS
SWARM
TOOLS
RESEARCH
FAILURES
RECOVERY
VERIFICATION
EVIDENCE
DECISIONS
MEMORY
RESOURCES
APPROVALS
ARTIFACTS
TIMELINE
POLICY
```

Main header:

```text
┌────────────────────────────────────────────┐
│ 🟢 APEX AUTOPILOT                          │
│ Mission: Repair coding execution          │
│ Status: EXECUTING                          │
│                                             │
│ Tasks      14/19     Agents     5 active   │
│ Verify      7/9      Recovery   1 active   │
│ Budget      61%      Runtime    43m        │
└────────────────────────────────────────────┘
```

---

# 56. Live mission graph UI

```text
                         GOAL
                           │
             ┌─────────────┼────────────┐
             ▼             ▼            ▼
         Research       Backend      Frontend
             │             │            │
             ▼             ▼            ▼
          Evidence       Tests        Browser
             └─────────────┼────────────┘
                           ▼
                        REVIEW
                           ▼
                       VERIFIED
```

Nodes should show:

```text
status
owner
agent
attempts
time
risk
tools
evidence
verification
```

---

# 57. User steering

APEX must remain steerable without requiring the user to manually take over execution.

Commands:

```text
/apex steer "prioritize reliability over speed"
/apex steer "do not modify backend"
/apex steer "use local model only"
/apex steer "research this further"
/apex steer "stop creating new agents"
```

A steering instruction should become a **mission constraint/update**, not overwrite the entire system prompt.

Example:

```yaml
constraint:
  source: user
  priority: high
  scope: mission
  instruction: "Use local models only"
```

---

# 58. User takeover

User can take control at any time.

```text
APEX ACTIVE
   ↓
TAKE OVER
   ↓
User becomes primary operator
   ↓
APEX becomes advisor/observer
```

Resuming APEX should preserve existing mission state.

---

# 59. APEX status surface

`/apex status` should show:

```text
Mode: APEX MAX
Mission: active
Goal: ...
Strategy: hierarchical parallel
Agents: 5/12
Tasks: 14/19
Current tasks: 3
Waiting: 1
Blocked: 0
Failures: 2
Recovered: 2
Verification: 7/9
Budget: 61%
Current action: backend integration test
Checkpoint: ckpt-...
```

---

# 60. API contract

Core endpoints:

```text
POST /api/apex/enable
POST /api/apex/disable
POST /api/apex/pause
POST /api/apex/resume
POST /api/apex/stop
POST /api/apex/takeover
POST /api/apex/steer

GET  /api/apex/status
GET  /api/apex/policy
GET  /api/apex/capabilities
GET  /api/apex/events
GET  /api/apex/metrics

POST /api/apex/missions
GET  /api/apex/missions
GET  /api/apex/missions/{id}
POST /api/apex/missions/{id}/replan
POST /api/apex/missions/{id}/verify

GET  /api/apex/missions/{id}/goals
GET  /api/apex/missions/{id}/tasks
GET  /api/apex/missions/{id}/agents
GET  /api/apex/missions/{id}/workflow
GET  /api/apex/missions/{id}/failures
GET  /api/apex/missions/{id}/evidence
GET  /api/apex/missions/{id}/decisions
GET  /api/apex/missions/{id}/artifacts

POST /api/apex/approvals/{id}/approve
POST /api/apex/approvals/{id}/reject
```

Use SSE/WebSocket for mission events and task/agent streaming.

---

# 61. Canonical action envelope

Every autonomous action should pass through one envelope.

```json
{
  "action_id": "uuid",
  "mission_id": "uuid",
  "goal_id": "uuid",
  "task_id": "uuid",
  "agent_id": "uuid",
  "action_type": "tool_call",
  "capability": "browser.navigate",
  "input": {},
  "scope": {},
  "risk": "R1",
  "policy_decision": "allow",
  "idempotency_key": "...",
  "timeout_ms": 30000,
  "budget": {},
  "evidence_required": true
}
```

Execution adapters should not receive arbitrary unvalidated dictionaries from the model.

---

# 62. Canonical task schema

```yaml
task:
  id: uuid
  mission_id: uuid
  goal_id: uuid
  parent_task_id: uuid|null
  title: string
  objective: string
  state: pending
  priority: 50
  dependencies: []
  assigned_agent_id: uuid|null
  required_capabilities: []
  inputs: {}
  expected_outputs: []
  acceptance_criteria: []
  risk: R0
  retry_policy: {}
  budget: {}
  deadline: null
  workspace: null
  verification: {}
  artifact_refs: []
  evidence_refs: []
```

---

# 63. Autonomous next-action protocol

At each executive cycle:

```text
1. Load mission state.
2. Read new events.
3. Check policy and budgets.
4. Detect stale leases.
5. Update task/world state.
6. Evaluate success criteria.
7. Identify blockers.
8. Estimate uncertainty.
9. Decide whether research is needed.
10. Select execution strategy.
11. Select agents/capabilities.
12. Allocate resources.
13. Dispatch a bounded execution wave.
14. Persist decision and checkpoint.
15. Wait for events/results.
16. Verify result.
17. Recover/replan if needed.
18. Repeat until terminal state.
```

This is the central APEX loop.

---

# 64. Executive pseudocode

```python
async def apex_loop(mission_id: str):
    while True:
        state = await load_mission(mission_id)
        events = await read_new_events(mission_id)

        policy.check_runtime_state(state)
        resources.refresh()
        leases.reconcile(state)

        if state.emergency_stop:
            await stop_mission(mission_id)
            return

        state = reduce_events(state, events)

        if success_criteria_met(state):
            verification = await verify_mission(state)
            if verification.is_verified:
                await complete_mission(mission_id, verification)
                return

        blockers = analyze_blockers(state)

        if should_research(state, blockers):
            await dispatch_research_wave(state)
        else:
            strategy = choose_strategy(state, blockers)
            plan_patch = generate_plan_patch(state, strategy)
            policy.validate_plan_patch(plan_patch)
            await apply_plan_patch(plan_patch)

            wave = select_next_execution_wave(state)
            allocations = resource_manager.allocate(wave)
            actions = capability_router.resolve(wave, allocations)
            policy.validate_actions(actions)
            await dispatch(actions)

        await checkpoint(mission_id)
```

This pseudocode is intentionally architectural. Integrate it with Alpha's existing runtime rather than creating a parallel executor.

---

# 65. Executive decision algorithm

For each candidate action, estimate:

```text
expected_task_progress
expected_success_probability
cost
latency
risk
reversibility
information gain
resource consumption
verification value
```

A conceptual score:

```text
score =
    progress_value
  + information_gain
  + verification_value
  - cost
  - latency_penalty
  - risk_penalty
  - resource_pressure
```

Do not make this formula the only decision mechanism. It is an explicit substrate for deterministic constraints around model-driven planning.

Hard rules should override scores:

```text
DENY beats high score
budget limit beats high score
emergency stop beats everything
invalid capability beats high score
unverified completion cannot become verified
```

---

# 66. Information-gain planning

For uncertain problems, APEX should sometimes choose an information-gathering action over direct execution.

Example:

```text
Two competing root causes
 ↓
Run diagnostic test
 ↓
Gain information
 ↓
Choose correct repair
```

This reduces:

```text
blind retries
wrong code edits
unnecessary agent spawning
```

---

# 67. Hypothesis engine

For debugging/research:

```yaml
hypothesis:
  id: h1
  statement: "SSE event framing is malformed"
  confidence: 0.61
  evidence: [e1, e2]
  tests: [t1]
  status: untested
```

The system should actively seek disconfirming evidence instead of merely accumulating supportive evidence.

---

# 68. Stuck detection

A mission is stuck if one or more signals persist:

```text
no state progress
same tool repeatedly fails
same plan step repeated
same error repeated
agent output repeats
verification unchanged
resource usage rises without progress
```

Example thresholds:

```text
3 identical failures
5 no-progress cycles
2 contradictory verification results
```

Thresholds should be configurable by task class.

On stuck:

```text
diagnose
→ change strategy
→ research
→ spawn critic
→ reduce context
→ switch model
→ reassign
```

---

# 69. Repeated-failure policy

Never use infinite retries.

Recommended escalation:

```text
attempt 1 → retry
attempt 2 → parameter repair
attempt 3 → alternative strategy
attempt 4 → deeper diagnosis/research
attempt 5 → specialist escalation
```

If still unresolved:

```text
BLOCKED / NEEDS HUMAN / PARTIAL
```

---

# 70. Self-improvement boundary

APEX may improve:

```text
task plans
workflow templates
agent prompts
skill usage
routing heuristics
failure classifiers
verification strategies
```

APEX should NOT silently modify:

```text
policy kernel
emergency stop
secret handling rules
audit deletion logic
identity/security boundaries
permission defaults
```

Those should require explicit controlled changes and regression tests.

This mirrors the governance principle that self-improvement should be logged and bounded rather than allowing the system to rewrite the rules that constrain it.

---

# 71. Skill lifecycle

APEX should be able to:

```text
discover skill
validate skill
load skill
use skill
measure skill
improve skill
version skill
deprecate skill
```

New skills should pass:

```text
schema validation
security scan
capability dependency check
smoke test
sandbox test
```

---

# 72. Dynamic tool/plugin creation

When Alpha identifies a recurring capability gap, it may propose or create a tool/plugin/skill.

Lifecycle:

```text
gap detected
→ specification
→ implementation
→ unit tests
→ security review
→ sandbox test
→ capability registration
→ benchmark
→ controlled activation
```

Do not immediately expose newly generated tools to all agents.

Use:

```text
quarantine
→ validation
→ limited rollout
→ promotion
```

---

# 73. A/B capability evaluation

If multiple tools/models can solve the same task:

```text
candidate A
candidate B
candidate C
```

APEX can compare:

```text
success
quality
latency
cost
failure rate
verification strength
```

Then update routing metadata.

---

# 74. Learning from failures

Failure records should feed a structured knowledge base:

```text
error signature
environment
capability
model
solution
verification result
regression status
```

Next time:

```text
new error
→ retrieve similar failures
→ test known solution
```

Do not blindly apply a previously successful fix to a different context.

---

# 75. Mission replay

Every complex mission should be replayable in a controlled environment.

Inputs:

```text
mission objective
initial state
selected models
capability manifest
policy snapshot
recorded events
```

Outputs:

```text
same mission trace
comparison
regressions
performance differences
```

This is essential for reliable evolution.

---

# 76. Deterministic gates around model decisions

Where exact correctness matters, use programmatic gates.

Examples:

```text
path exists
schema valid
JSON parses
Git branch clean
tests pass
package builds
HTTP status expected
artifact exists
required evidence count met
permission rule satisfied
budget available
```

The LLM should propose; code should enforce invariants.

---

# 77. Safety architecture

APEX should have two layers.

## Layer A — deterministic kernel

```text
authentication
authorization
scope
sandbox
resource limits
approval
emergency stop
secret isolation
network policy
filesystem policy
```

## Layer B — model-driven intelligence

```text
planning
reasoning
delegation
research
strategy selection
error diagnosis
workflow adaptation
```

Never invert this relationship.

---

# 78. Network policy

Capabilities should specify:

```text
network_allowed
allowed_hosts
blocked_hosts
method restrictions
rate limits
```

For example:

```yaml
network:
  mode: allowlist
  hosts:
    - api.example.com
```

Mission-specific network requirements should be explicit.

---

# 79. Filesystem policy

Use:

```text
read scopes
write scopes
delete scopes
execute scopes
```

Example:

```yaml
filesystem:
  read:
    - C:/workspace/alpha/**
  write:
    - C:/workspace/alpha/worktrees/**
  delete: []
```

Never let a model infer filesystem authority from natural language alone.

---

# 80. Agent communication protocol

Agents should exchange structured task messages.

```yaml
agent_message:
  from: agent-a
  to: agent-b
  mission_id: ...
  task_id: ...
  type: result|request|blocker|handoff|review
  payload: ...
  artifact_refs: []
  evidence_refs: []
```

Important messages should be durable.

Ephemeral chat should not be the only source of truth.

---

# 81. Artifact-first execution

Agents should produce artifacts rather than dumping everything into conversation.

Examples:

```text
research report
patch
test report
screenshot
log bundle
benchmark
analysis
migration plan
```

An artifact should include:

```text
artifact_id
name
type
producer
mission
created_at
content_ref
integrity hash
verification state
```

A2A explicitly models artifacts as outputs associated with stateful tasks, which is a useful interoperability pattern. [6]

---

# 82. Background task architecture

APEX needs a scheduler for long missions.

Task categories:

```text
immediate
background
scheduled
waiting_for_event
waiting_for_human
retry_after
```

Example:

```text
research API update
→ schedule after 1 hour
→ wake mission
→ resume
```

MCP Tasks and A2A both provide patterns for asynchronous task status and long-running work that can inform this design. [5][6]

---

# 83. Offline/online recovery

When network disappears:

```text
network offline
→ pause network-dependent tasks
→ continue local tasks where safe
→ checkpoint
→ wait
```

When network returns:

```text
health check
→ revalidate credentials
→ revalidate external state
→ resume eligible tasks
```

Do not repeatedly hammer a broken external service.

---

# 84. Application restart recovery

Startup:

```text
load active missions
→ detect unfinished runs
→ inspect checkpoints
→ inspect leases
→ inspect external jobs
→ rebuild state
→ mark stale workers
→ resume safe work
```

The user should see:

```text
APEX resumed mission M-123 from checkpoint CK-88
```

not:

```text
Sorry, I forgot what I was doing.
```

---

# 85. Context compaction trigger

Trigger context maintenance on:

```text
token threshold
repetition
agent handoff
mission phase transition
large tool output
replan
```

Archive:

```text
raw tool output
intermediate reasoning artifacts
old conversation turns
```

Retain:

```text
mission objective
success criteria
current state
important findings
active constraints
verification status
```

---

# 86. Failure-aware context

When a task fails, a replacement agent should receive:

```text
what was attempted
what failed
exact error
relevant files
tool inputs
observed output
what has been verified
what has NOT been verified
```

This prevents restarting from zero while also avoiding huge context inheritance.

---

# 87. Mission priority scheduler

Support:

```text
critical
high
normal
low
background
```

Use weighted fair scheduling so low-priority tasks are not permanently starved.

For resource pressure:

```text
protect critical mission
pause low-priority missions
```

---

# 88. Multi-mission APEX

APEX should support multiple active missions.

```text
APEX Supervisor
 ├─ Mission A: coding
 ├─ Mission B: research
 └─ Mission C: monitoring
```

The resource scheduler controls cross-mission contention.

Each mission must maintain isolated:

```text
context
workspace
permissions
memory scope
budget
artifacts
```

---

# 89. Mission handoff between sessions

A mission can move between:

```text
chat session
Telegram bot
desktop UI
API client
background worker
```

Because the mission is durable and not bound to one chat thread.

---

# 90. APEX as a platform-level interface

Eventually any Alpha interaction can become:

```text
user request
API request
slash command
bot command
scheduled event
webhook
system alert
another agent request
```

All enter:

```text
Intent Gateway
 ↓
APEX decision system
```

This eliminates duplicated orchestration logic across frontends.

---

# 91. Goal negotiation

If the objective is ambiguous, APEX should first determine whether clarification is genuinely necessary.

Strategy:

```text
Can a safe assumption be made?
→ yes: proceed and expose assumption

No safe assumption?
→ request input
```

Avoid asking the user trivial questions that the system can resolve from local context or reasonable defaults.

---

# 92. Assumption registry

When APEX proceeds under uncertainty:

```yaml
assumption:
  id: a-17
  statement: "Current branch is the intended work branch"
  confidence: 0.84
  impact: medium
  verification_plan: "inspect git status"
```

High-impact assumptions should become explicit verification tasks.

---

# 93. Contradiction engine

When two agents disagree:

```text
Agent A says X
Agent B says Y
```

APEX should not arbitrarily pick one.

Instead:

```text
identify disagreement
→ compare evidence
→ run discriminator test
→ ask reviewer if necessary
→ update state
```

---

# 94. Agent reputation / capability scoring

Maintain non-authoritative historical metrics:

```text
task success rate
verification pass rate
average retries
latency
cost
specialty fit
recent health
```

Use them to choose workers, but do not permanently lock an agent to a role based on old statistics.

---

# 95. Capability promotion

A generated skill/agent/tool should move through:

```text
PROPOSED
SANDBOXED
VALIDATED
LIMITED
TRUSTED
DEPRECATED
```

This gives Alpha a controlled path for self-expansion.

---

# 96. Benchmark harness

APEX must ship with scenario-based evaluations.

Core benchmark categories:

```text
planning
decomposition
tool selection
delegation
parallelism
research quality
coding correctness
failure recovery
verification
long-running resume
security policy
resource efficiency
```

Metrics:

```text
task success
verified success
partial success
unsafe action rate
silent failure rate
recovery rate
recovery time
cost/task
tool-call efficiency
agent efficiency
false completion rate
```

---

# 97. Golden missions

Maintain a suite of real missions that represent Alpha's intended capabilities.

### Golden Mission A — tiny task

Expected:

```text
no unnecessary swarm
fast direct execution
```

### Golden Mission B — research

Expected:

```text
sources
cross-check
citations
evidence-backed conclusion
```

### Golden Mission C — coding

Expected:

```text
inspect
edit
test
verify
```

### Golden Mission D — failed tool

Expected:

```text
detect
classify
recover
verify
```

### Golden Mission E — agent crash

Expected:

```text
lease expiry
checkpoint
replacement
continue
```

### Golden Mission F — restart

Expected:

```text
resume from checkpoint
```

### Golden Mission G — unavailable model

Expected:

```text
health failure
routing
alternative model
continued execution
```

### Golden Mission H — prompt injection

Expected:

```text
untrusted content
blocked authority escalation
safe continuation
```

---

# 98. Stress testing

Test:

```text
100 tasks
20 agents
10 parallel tool calls
multiple failures
network interruption
worker crashes
context compaction
model switching
```

Observe:

```text
deadlocks
starvation
duplicate execution
race conditions
memory leaks
orphan tasks
stale leases
resource exhaustion
```

---

# 99. Concurrency rules

Every concurrent task should have:

```text
explicit resource ownership
workspace ownership
artifact ownership
memory scope
cancellation behavior
```

No hidden shared mutable state.

Use locking or transactional state updates where necessary.

---

# 100. Cancellation propagation

If a parent mission stops:

```text
mission stop
→ cancel goals
→ cancel tasks
→ cancel child agents
→ cancel workflows
→ cancel cancellable tools
```

Cancellation must propagate through the execution tree.

Also support child cancellation:

```text
child task cancelled
→ parent observes
→ choose alternative/replan
```

MCP's 2026 Tasks specification notes that cancellation is cooperative and its observable status can remain non-terminal briefly, so Alpha should model cancellation as a state transition with eventual observation rather than assuming instant termination. [6]

---

# 101. Deadlock prevention

APEX should detect:

```text
cyclic dependencies
waiting-for cycles
resource starvation
mutual agent blocking
```

A dynamic workflow must be validated before execution.

---

# 102. Orphan prevention

Any task without:

```text
owner
parent
resource lease
```

should be flagged.

Any agent without:

```text
active task or explicit idle state
```

should be reclaimed or parked.

---

# 103. Zombie task prevention

A task must not remain `RUNNING` forever.

Have:

```text
heartbeat timeout
execution deadline
progress timeout
absolute deadline
```

On timeout:

```text
mark suspected stuck
→ inspect
→ recover
```

---

# 104. Resource-aware swarm shrinking

If RAM/GPU/CPU pressure rises:

```text
reduce parallelism
pause low-priority workers
compress contexts
avoid model duplication
```

The system should prefer degraded throughput over crashing the whole application.

---

# 105. Self-observing APEX

APEX should monitor itself using ordinary telemetry, but the self-monitoring loop must remain lightweight.

Watch:

```text
planner latency
queue depth
agent count
task age
failure rate
verification backlog
memory pressure
CPU/RAM/GPU
```

It should be possible to create a `monitoring-agent`, but core health metrics must remain deterministic and not depend on that agent staying alive.

---

# 106. Health hierarchy

```text
Application health
 ↓
APEX health
 ↓
Mission health
 ↓
Goal health
 ↓
Task health
 ↓
Agent health
 ↓
Tool health
```

Failure at a lower level should be isolated where possible.

---

# 107. No single-point-of-LLM failure

APEX itself must not depend on one model response for:

```text
policy
security
shutdown
state integrity
```

The model can become unavailable without making the runtime unsafe.

---

# 108. Fallback strategy

Fallback hierarchy:

```text
same model retry
→ alternate configuration
→ alternate model
→ alternate provider
→ local model
→ wait
→ human escalation
```

The actual order should be task/policy-dependent.

Every fallback should emit:

```text
fallback_started
fallback_reason
fallback_target
fallback_result
```

---

# 109. Model disagreement / council mode

APEX can optionally invoke multiple models for difficult decisions.

```text
Model A
Model B
Model C
 ↓
critic/judge
 ↓
decision
```

Use only when:

```text
uncertainty is high
risk is high
expected benefit exceeds cost
```

Do not use councils for trivial tasks.

---

# 110. Planner / executor separation

The planner should not directly perform all actions.

```text
Planner
→ Action plan
→ Policy validation
→ Executor
```

This makes plans inspectable and testable.

---

# 111. Policy simulation

Before executing a plan, APEX should be able to run a dry policy simulation:

```text
plan
→ enumerate actions
→ classify risk
→ check capabilities
→ check permissions
→ estimate resources
→ identify approvals
```

UI:

```text
Plan valid
7 actions allowed
1 approval required
0 denied
Estimated runtime: ...
```

---

# 112. Dry-run mode

Provide:

```text
/apex plan
```

without execution.

It should produce:

```text
mission
subgoals
agents
workflow
tools
permissions
resource estimate
verification strategy
```

This is useful for debugging APEX itself.

---

# 113. Simulation mode

Provide a sandbox environment where external side effects are mocked.

Examples:

```text
mock API
mock filesystem
fake Git remote
fake browser target
simulated MCP server
```

Use this to test aggressive autonomy safely.

---

# 114. Security test suite

Include deliberate attacks:

```text
malicious README
prompt injection web page
malicious MCP tool description
compromised external agent
forged tool result
fake success message
secret exfiltration attempt
path traversal attempt
command injection attempt
```

Expected behavior:

```text
contain
log
block
continue safely
```

OWASP's current LLM risk guidance specifically identifies prompt injection, insecure output handling, supply-chain issues, sensitive information disclosure, excessive agency and overreliance as relevant risks for agentic applications. [11][12]

---

# 115. Tool output trust model

Every tool result should have provenance:

```yaml
tool_result:
  source: browser.navigate
  trust: untrusted_data
  authority: none
  executable: false
```

A result can provide information but cannot automatically rewrite policy.

---

# 116. External agent trust model

A2A/MCP/external tools should be classified:

```text
trusted internal
trusted external
untrusted external
```

An external agent should not inherit Alpha's internal permissions automatically.

---

# 117. Approval grouping

For long missions, bundle related approvals when safe.

Example:

```text
3 public documentation uploads
→ one approval group
```

But do not group unrelated high-risk actions merely to reduce friction.

---

# 118. Time-based approval expiry

Approvals should expire.

```yaml
approval:
  expires_at: ...
```

If the mission state materially changes, the old approval should be invalidated and re-evaluated.

---

# 119. State invalidation

If external state changes, cached assumptions may become invalid.

Examples:

```text
Git branch changed
browser session expired
API schema changed
credential expired
file modified externally
MCP capability changed
```

APEX should:

```text
invalidate affected plan nodes
→ revalidate
→ replan if necessary
```

---

# 120. Checkpoint boundaries

Checkpoint at:

```text
mission start
plan version change
agent creation
agent completion
significant tool result
failure recovery
verification milestone
approval request
before high-risk side effect
after high-risk side effect
pause
shutdown
```

Do not checkpoint after every trivial micro-event if that would create excessive storage overhead.

---

# 121. Checkpoint contents

```yaml
checkpoint:
  id: ...
  mission_id: ...
  plan_version: ...
  state_hash: ...
  active_tasks: ...
  agent_leases: ...
  budgets: ...
  pending_approvals: ...
  pending_external_tasks: ...
  current_strategy: ...
  artifact_refs: ...
  evidence_refs: ...
```

---

# 122. Mission recovery algorithm

```text
load checkpoint
 ↓
verify checkpoint integrity
 ↓
inspect external state
 ↓
reconcile leases
 ↓
identify completed side effects
 ↓
mark stale actions
 ↓
reconstruct task graph
 ↓
resume from latest safe boundary
```

Never blindly re-run all actions.

---

# 123. Partial-result policy

If full success is impossible, APEX should produce:

```text
COMPLETED
PARTIAL
BLOCKED
FAILED
```

For partial:

```text
completed items
remaining items
why blocked
what was verified
recommended next action
```

Do not turn partial into complete for UX reasons.

---

# 124. Reporting format

Final APEX report should contain:

```text
Objective
Outcome
Completed
Partial
Unresolved
Actions taken
Agents used
Important decisions
Failures encountered
Recovery actions
Verification performed
Evidence/artifacts
Resource summary
Remaining risks
```

For ordinary users, the UI can summarize this while preserving full detail behind expandable sections.

---

# 125. User notification levels

```text
QUIET
IMPORTANT
BLOCKER
SECURITY
COMPLETED
```

Examples:

```text
research completed → quiet
agent replaced → important
approval required → blocker
credential exposure attempt → security
mission verified → completed
```

---

# 126. APEX telemetry schema

Minimum fields:

```text
timestamp
trace_id
span_id
mission_id
goal_id
task_id
agent_id
component
event_type
status
duration_ms
model
provider
capability
risk
policy_decision
error_code
artifact_refs
evidence_refs
```

---

# 127. Privacy

Logs should minimize sensitive content.

Store references rather than copying sensitive payloads wherever possible.

Support:

```text
redaction
retention policies
delete policies
local-only telemetry
secret filtering
```

---

# 128. Performance objectives

Initial engineering targets:

```text
APEX decision overhead: low enough to be negligible on normal tasks
UI event latency: near-real-time
mission state updates: durable
worker heartbeat: configurable, e.g. 5–30s
stale worker detection: bounded
```

Do not optimize exact numeric thresholds before profiling the real Alpha runtime.

---

# 129. Error budget

Track APEX-specific reliability:

```text
orchestration failures
orphan tasks
false completion
silent failure
recovery failure
checkpoint restore failure
policy bypass attempts
```

A major launch criterion should be **zero silent failure in tested paths**.

---

# 130. Implementation principle: no destructive rewrite

Do not delete working Alpha features to introduce APEX.

Implementation order:

```text
inventory
→ adapter interfaces
→ event integration
→ capability registration
→ executive layer
→ UI
→ migration tests
```

Existing features become APEX capabilities.

---

# 131. Recommended repository structure

Adapt this to the current Alpha tree after inspecting the actual implementation:

```text
backend/
  ... existing runtime ...
  apex/
    controller.py
    executive.py
    schemas.py
    mission.py
    goals.py
    tasks.py
    planning.py
    replanning.py
    policy.py
    risk.py
    approvals.py
    capabilities.py
    command_router.py
    mode_controller.py
    agent_factory.py
    agent_registry.py
    swarm_controller.py
    workflow_controller.py
    tool_router.py
    model_router.py
    research_controller.py
    context_manager.py
    memory_controller.py
    resource_manager.py
    scheduler.py
    lease_manager.py
    workspace_manager.py
    checkpoint_manager.py
    verification.py
    recovery.py
    event_bus.py
    event_store.py
    telemetry.py

frontend/
  src/components/apex/
    ApexToggle
    ApexControlCenter
    ApexMissionGraph
    ApexMissionHeader
    ApexAgentTree
    ApexTaskBoard
    ApexTimeline
    ApexDecisionPanel
    ApexFailurePanel
    ApexRecoveryPanel
    ApexEvidencePanel
    ApexResourcePanel
    ApexApprovalPanel
```

Do not assume these exact paths exist; inspect the current codebase and integrate using its established architecture.

---

# 132. API event stream

Example SSE events:

```text
event: mission.updated
data: {...}

event: task.started
data: {...}

event: agent.started
data: {...}

event: tool.started
data: {...}
event: tool.completed
data: {...}
event: verification.failed
data: {...}
event: recovery.started
data: {...}
```

The frontend should reconstruct UI state from events plus durable snapshots.

---

# 133. State reconciliation

Frontend must not assume an event arrived exactly once.

Use:

```text
event_id
sequence
snapshot_version
last_seen_event
```

On reconnect:

```text
load latest mission snapshot
→ replay newer events
```

---

# 134. Multi-agent result merging

A merge agent should receive structured artifacts, not unlimited raw histories.

Example:

```text
worker A → artifact A + evidence A
worker B → artifact B + evidence B
worker C → artifact C + evidence C
                 ↓
              merger
                 ↓
        consolidated artifact
```

Then independent verification.

---

# 135. Consensus modes

Supported:

```text
majority vote
weighted vote
judge model
critic loop
evidence winner
```

Prefer evidence-based resolution over pure vote counting for factual or technical decisions.

---

# 136. Dynamic verification depth

The executive should increase verification when:

```text
uncertainty high
risk high
blast radius high
agent disagreement
multiple repairs already attempted
external side effect
```

Decrease verification for:

```text
low-risk trivial local operations
```

---

# 137. Cost-aware planning

Before a large swarm, estimate:

```text
number of agents
LLM calls
tool calls
expected runtime
```

Compare with:

```text
task value
budget
latency target
```

APEX should be allowed to choose:

```text
cheaper sequential plan
```

over:

```text
expensive parallel plan
```

when both satisfy the objective.

---

# 138. Latency-aware planning

For deadline-driven tasks:

```text
deadline
→ determine critical path
→ parallelize independent nodes
→ allocate strongest available resources to bottlenecks
```

The planner should optimize the critical path, not merely maximize agent count.

---

# 139. Quality-aware planning

For quality-driven tasks:

```text
more independent review
more verification
stronger model
research escalation
```

APEX should understand user priorities such as:

```text
fastest
cheapest
highest quality
maximum evidence
lowest risk
```

---

# 140. Mission policy presets

Useful presets:

```yaml
mission_profiles:
  fast:
    max_agents: 2
    verification: V1

  balanced:
    max_agents: 6
    verification: V3

  quality:
    max_agents: 10
    verification: V4

  research:
    max_agents: 8
    research_depth: LEVEL_4

  critical:
    approvals: strict
    verification: V5
```

APEX can still override task-locally within global constraints.

---

# 141. Mission-level objective vector

Represent user preference as:

```text
quality
speed
cost
risk
privacy
autonomy
```

Example:

```yaml
preferences:
  quality: 0.9
  speed: 0.5
  cost: 0.2
  risk: 0.1
  privacy: 0.8
  autonomy: 1.0
```

The planner uses this to select execution strategies.

---

# 142. “Do anything” interpretation

For the APEX product promise, define “anything” as:

> **Any task that Alpha's registered capabilities, resources, permissions and environment can validly and safely perform.**

This is stronger and more useful than claiming literally unlimited control.

It lets you add more capabilities later without redesigning the autonomy layer.

---

# 143. Capability extensibility

Adding a new Alpha capability should require:

```text
register capability
→ define schema
→ declare permissions
→ define risk
→ define health probe
→ define cost/latency
→ define cancellation
→ define verification
```

After that, APEX can discover and use it without hard-coded mission-specific logic.

---

# 144. APEX plugin interface

```python
class ApexCapability(Protocol):
    id: str
    version: str

    def describe(self) -> CapabilityDescriptor: ...
    async def health(self) -> HealthStatus: ...
    async def authorize(self, request: ActionRequest) -> Decision: ...
    async def execute(self, request: ActionRequest) -> ActionResult: ...
    async def cancel(self, action_id: str) -> None: ...
    async def verify(self, result: ActionResult) -> VerificationResult: ...
```

This gives every capability the same lifecycle contract.

---

# 145. Agent interface

```python
class ApexAgent(Protocol):
    async def start(self, task: Task) -> AgentHandle: ...
    async def status(self) -> AgentStatus: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...
    async def produce_artifacts(self) -> list[Artifact]: ...
    async def verify(self) -> VerificationResult: ...
```

---

# 146. Workflow interface

```python
class ApexWorkflow(Protocol):
    async def validate(self, graph: WorkflowGraph) -> ValidationResult: ...
    async def start(self, graph: WorkflowGraph) -> WorkflowHandle: ...
    async def patch(self, patch: GraphPatch) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def cancel(self) -> None: ...
    async def status(self) -> WorkflowState: ...
```

---

# 147. Policy interface

```python
class ApexPolicy(Protocol):
    def evaluate(self, action: ActionRequest, context: RuntimeContext) -> PolicyDecision: ...
    def require_approval(self, action: ActionRequest) -> bool: ...
    def enforce_budget(self, action: ActionRequest) -> BudgetDecision: ...
```

Policy must be deterministic where possible.

---

# 148. Recovery interface

```python
class RecoveryStrategy(Protocol):
    def can_handle(self, failure: Failure) -> bool: ...
    async def recover(self, failure: Failure, state: MissionState) -> RecoveryResult: ...
```

Strategies:

```text
retry
repair_arguments
switch_tool
switch_model
research
reassign
rollback
replan
wait
human
```

---

# 149. Verification interface

```python
class Verifier(Protocol):
    def supports(self, task: Task) -> bool: ...
    async def verify(self, task: Task, result: ArtifactBundle) -> VerificationResult: ...
```

Composite verification:

```text
unit verifier
+ integration verifier
+ browser verifier
+ reviewer
```

---

# 150. Mission lifecycle

```text
CREATED
  ↓
UNDERSTANDING
  ↓
PLANNING
  ↓
EXECUTING
  ↓
OBSERVING
  ↓
VERIFYING
  ├─ pass → COMPLETED
  └─ fail → RECOVERING
                 ↓
              REPLANNING
                 ↓
              EXECUTING
```

Additional terminals:

```text
BLOCKED
PARTIAL
CANCELLED
FAILED
```

---

# 151. Goal/task lifecycle

```text
PENDING
 ↓
READY
 ↓
RUNNING
 ├─ WAITING
 ├─ FAILED
 ├─ BLOCKED
 └─ COMPLETED
```

A task may be reopened after verification failure.

---

# 152. Agent lifecycle

```text
CREATED
 ↓
INITIALIZING
 ↓
RUNNING
 ├─ PAUSED
 ├─ STALLED
 ├─ FAILED
 └─ COMPLETED
```

Stalled agents can be replaced.

---

# 153. Decision checkpoints

APEX should checkpoint before these decisions:

```text
large fan-out
high-risk action
workflow rewrite
agent swarm expansion
model/provider switch
external side effect
long wait
```

This improves recovery and auditability.

---

# 154. External side-effect journal

For side effects, store:

```text
action ID
idempotency key
request summary
external system
response summary
final observed state
rollback/compensation method
```

This helps prevent duplicate effects after crashes.

---

# 155. Compensation model

Some operations cannot be rolled back directly but can be compensated.

Examples:

```text
created branch → delete branch
published message → publish correction
created temporary resource → cleanup
```

Define:

```text
rollback
compensate
manual recovery
```

for each high-risk capability.

---

# 156. Artifact integrity

Important artifacts should support:

```text
hash
producer
mission
timestamp
version
verification status
```

A final report should reference artifact IDs rather than silently embedding unverifiable content.

---

# 157. Research source freshness

For current information, store:

```text
retrieved_at
published_at if available
source domain
```

APEX should detect stale evidence when the task explicitly requires current information.

---

# 158. Source quality hierarchy

A generic preference order:

```text
primary official documentation
official source code/specification
first-party announcements
high-quality independent technical sources
community discussions
search snippets
```

Search snippets should not be treated as final evidence when primary documentation is available.

---

# 159. Research contradiction policy

If sources conflict:

```text
record both
identify dates/versions
prefer authoritative/current source
run tests if the disagreement is technical
mark unresolved if needed
```

---

# 160. Version-aware planning

For software tasks, APEX should track:

```text
runtime version
library versions
API version
browser version
OS
model/provider version
```

When an issue is version-specific, preserve the exact environment in evidence.

---

# 161. Environment fingerprint

Mission should optionally capture:

```text
OS
CPU/RAM/GPU
Python/Node/runtime
Git revision
dependency lock hash
active services
tool manifest
model manifest
```

This improves reproducibility.

---

# 162. Capability manifest snapshot

At mission start, save a snapshot of:

```text
tools
skills
MCP
A2A
models
permissions
versions
health
```

If the environment changes, APEX can detect drift.

---

# 163. Drift detection

Detect:

```text
capability removed
model unavailable
permission changed
dependency updated
workspace changed
external service changed
```

Then:

```text
revalidate affected plan
```

---

# 164. Runtime upgrades

If Alpha updates while APEX missions are running:

```text
pause mission
checkpoint
upgrade
health check
restore state
resume
```

Never assume an in-flight mission is compatible with a changed runtime.

---

# 165. Safe autonomous startup

At application launch:

```text
load policy
start core services
health check
load unfinished missions
reconcile leases
restore checkpoints
show user status
```

APEX should not silently revive a dangerous mission without verifying its policy snapshot and external state.

---

# 166. Multi-user future readiness

Even if Alpha initially remains single-user, design identifiers around:

```text
user_id
workspace_id
project_id
mission_id
```

This prevents future architectural rewrites.

---

# 167. Permission inheritance

Recommended inheritance:

```text
GLOBAL POLICY
 ↓
WORKSPACE POLICY
 ↓
MISSION POLICY
 ↓
AGENT POLICY
 ↓
TASK POLICY
 ↓
ACTION
```

A lower scope may tighten permissions but should not silently weaken stronger global protections.

---

# 168. Agent-specific permission model

Example:

```yaml
agent:
  role: reviewer
  permissions:
    filesystem:
      read: ["src/**"]
      write: []
    shell:
      allow: ["pytest *", "git diff *"]
      deny: ["git push *"]
```

This style is consistent with current granular agent permission patterns used by coding-agent systems. [10]

---

# 169. Trust levels

Agents can have:

```text
T0 observer
T1 local worker
T2 project operator
T3 external operator
T4 protected operator
```

Higher trust requires stronger controls and narrower deployment.

---

# 170. Autonomous policy compiler

User settings such as:

```text
"use local models"
"never modify production"
"be as autonomous as possible"
```

should compile into machine-readable policy.

Example:

```yaml
constraints:
  model_provider: [local]
  production_write: deny
  autonomy: maximum
```

---

# 171. Policy conflict resolution

Priority:

```text
emergency stop
→ security policy
→ global policy
→ workspace policy
→ mission policy
→ task policy
→ agent preference
→ model preference
```

Lower layers cannot override higher layers.

---

# 172. Resource reservation

Before a large operation, APEX can reserve:

```text
2 agents
4GB RAM
1 browser
10k tokens
```

Then dispatch.

Reservation should prevent resource overcommit.

---

# 173. Backpressure

When queues grow:

```text
stop spawning
reduce concurrency
prioritize critical tasks
```

APEX should be able to say:

```text
"No additional agents needed; current workers are resource-bound."
```

---

# 174. Queue architecture

Separate:

```text
planning queue
execution queue
agent queue
verification queue
recovery queue
background queue
```

But retain one mission-level coordinator.

---

# 175. Scheduling policy

Use priority + aging + deadlines.

Conceptually:

```text
effective_priority = base_priority + aging + deadline_pressure
```

Do not let one swarm monopolize the runtime indefinitely.

---

# 176. Observability for users vs developers

User view:

```text
high-level mission status
important actions
blockers
final result
```

Developer view:

```text
events
traces
stack traces
tool I/O metadata
state transitions
policy evaluations
```

Both derive from the same underlying event model.

---

# 177. Debug mode for APEX itself

Introduce:

```text
APEX DEBUG
```

It should show:

```text
why next action selected
candidate actions
policy result
resource decision
agent selection
workflow patch
verification decision
recovery strategy
```

This is invaluable during Alpha development.

---

# 178. Deterministic replay of decision logic

Given the same:

```text
state snapshot
policy
capability manifest
```

the deterministic parts of APEX should produce reproducible outputs.

Model-driven portions may differ, but the runtime gates should remain stable.

---

# 179. APEX self-test

On startup or manually:

```text
/apex self-test
```

Run:

```text
policy test
capability health
agent spawn test
workflow test
checkpoint test
recovery test
verification test
event stream test
emergency stop test
```

---

# 180. Fault injection framework

Add controlled faults:

```text
drop network
kill worker
return malformed tool output
return model error
fail verification
corrupt temporary state
expire lease
exceed budget
```

Expected behavior must be machine-tested.

---

# 181. Launch gates

APEX should not be called production-ready until:

```text
No known unsafe policy bypass
No silent failure on critical paths
Mission state survives restart
Worker loss recovers
Tool failures recover
Verification blocks false completion
User stop works
Permissions are enforced outside the LLM
```

---

# 182. Rollout strategy

## Stage 1 — Shadow

APEX plans but does not execute.

## Stage 2 — Safe local autonomy

Read-only + low-risk local actions.

## Stage 3 — Coding autonomy

Sandboxed repository changes.

## Stage 4 — Multi-agent autonomy

Dynamic agent creation and swarm.

## Stage 5 — Long-running missions

Persistent/background execution.

## Stage 6 — External integrations

MCP/A2A/browser/external side effects under explicit policy.

## Stage 7 — Maximum internal autonomy

Full internal composition under the policy kernel.

---

# 183. Phase-by-phase implementation plan

## Phase 0 — inventory

Before coding:

```text
inspect existing Alpha architecture
identify lifecycle owners
map all capabilities
map existing APIs
map event/logging systems
map state stores
map workflow engines
map agent runtimes
```

Deliverable:

```text
APEX_INTEGRATION_MAP.md
```

## Phase 1 — core contract

Implement:

```text
Mission
AutonomyContract
Decision
ActionRequest
PolicyDecision
```

## Phase 2 — capability graph

Implement registry + health + schemas.

## Phase 3 — command/mode unification

Connect slash commands and modes.

## Phase 4 — executive loop

Implement next-action controller.

## Phase 5 — dynamic planning

Add graph patches + replanning.

## Phase 6 — agent factory

Add temporary specialists + leases.

## Phase 7 — swarm control

Add concurrency + hierarchy.

## Phase 8 — verification/recovery

Unify verification and self-healing.

## Phase 9 — persistence/resume

Add mission checkpoints and restart recovery.

## Phase 10 — APEX UI

Add mission graph, agents, failures, evidence.

## Phase 11 — security hardening

Test prompt injection, permission bypass, secrets.

## Phase 12 — stress/E2E

Run golden missions and fault injection.

---

# 184. Recommended implementation order inside Alpha

The highest-value sequence is:

```text
1. Inventory existing engines
2. Define APEX state/events/schemas
3. Integrate with the existing lifecycle owner
4. Build unified capability registry
5. Build policy kernel adapter
6. Build APEX executive loop
7. Connect existing goal/planner/workflow systems
8. Connect dynamic agents/swarm
9. Connect research/tool/model routers
10. Connect verification/recovery
11. Add durable mission checkpoints
12. Add UI
13. Add fault injection
14. Run real missions
15. Fix integration defects
```

Do not begin with the UI. The control plane must be correct first.

---

# 185. Migration strategy

Avoid a big-bang rewrite.

Use adapters:

```text
ExistingPlannerAdapter
ExistingGoalAdapter
ExistingWorkflowAdapter
ExistingAgentAdapter
ExistingSwarmAdapter
ExistingResearchAdapter
ExistingVerificationAdapter
ExistingRecoveryAdapter
ExistingMemoryAdapter
```

After migration, old APIs may eventually become thin wrappers over APEX.

---

# 186. Compatibility requirement

When APEX is OFF:

```text
existing Alpha behavior should remain functional
```

When APEX is ON:

```text
APEX orchestrates existing capability implementations
```

This is a critical backward-compatibility test.

---

# 187. Anti-patterns to explicitly forbid

Do not implement:

```text
one giant system prompt as “autonomy”
LLM-controlled permission checks
infinite retries
unbounded agent spawning
shared mutable worktrees
silent model fallback
fake progress bars
success based only on model text
memory without provenance
raw secrets in context
duplicate lifecycle managers
unstructured tool calls
```

---

# 188. Required invariants

APEX must enforce:

```text
I1: Every action belongs to a mission/task.
I2: Every side effect has a policy decision.
I3: Every background worker has a lease.
I4: Every complex mission has a durable checkpoint.
I5: Every failure creates an observable record.
I6: Every completion requires verification.
I7: Parent cancellation propagates.
I8: A denied action cannot be re-enabled by the model.
I9: APEX cannot disable the emergency stop.
I10: Global policy outranks mission/task preferences.
I11: Retrying must be idempotent or state-checked.
I12: External/untrusted content cannot become execution authority.
```

---

# 189. Acceptance criteria

APEX implementation is successful when all of the following are true.

### Autonomy

```text
[ ] User enables APEX once
[ ] Alpha can create/manage a mission
[ ] Alpha creates subgoals automatically
[ ] Alpha dynamically chooses execution strategy
[ ] Alpha can switch internal modes
[ ] Alpha can invoke slash-command capabilities internally
```

### Agents

```text
[ ] Alpha creates specialist agents
[ ] Alpha delegates tasks
[ ] Alpha runs independent tasks in parallel
[ ] Alpha can replace failed agents
[ ] Alpha can resize a swarm
[ ] Alpha bounds recursive delegation
```

### Workflows

```text
[ ] Dynamic DAG creation works
[ ] Graph patches work
[ ] Replanning works
[ ] Handoff works
[ ] Parallel fan-out/fan-in works
[ ] Complex workflow can be resumed
```

### Recovery

```text
[ ] Tool failure does not silently terminate mission
[ ] Repeated failure triggers strategy change
[ ] Agent crash is detected
[ ] Stale lease is recovered
[ ] Model outage is routed around
[ ] Network outage is handled
```

### Verification

```text
[ ] False completion is blocked
[ ] Tests can become evidence
[ ] Browser checks can become evidence
[ ] Research claims store evidence
[ ] Independent review can be required
```

### Persistence

```text
[ ] Mission survives process restart
[ ] Mission resumes from checkpoint
[ ] Pending approvals persist
[ ] Background tasks persist
```

### Safety

```text
[ ] Policy is outside model control
[ ] Emergency stop works independent of model
[ ] Secrets are protected
[ ] Risk classes are enforced
[ ] Prompt-injection tests pass
```

### UI

```text
[ ] APEX state is obvious
[ ] Mission progress is visible
[ ] Agents are visible
[ ] Failures are visible
[ ] Recovery is visible
[ ] Evidence is visible
[ ] User can pause/resume/stop/take over
```

---

# 190. Example end-to-end mission

User:

```text
/APEX ON

"Make Alpha reliably perform an end-to-end coding task.
Find all failures, fix them, and prove that it works."
```

APEX:

```text
MISSION CREATED
 ↓
Success criteria compiled
 ↓
Inspect capability health
 ↓
Inspect coding pipeline
 ↓
Build initial plan
 ↓
Create:
  investigator
  backend debugger
  frontend debugger
  test agent
  reviewer
 ↓
Run independent diagnosis
 ↓
Merge findings
 ↓
Identify root causes
 ↓
Patch workflow
 ↓
Run tests
 ↓
Test actual tool execution
 ↓
Test agent delegation
 ↓
Test browser/IDE behavior
 ↓
Failure found
 ↓
Create recovery task
 ↓
Research failure
 ↓
Patch
 ↓
Re-run
 ↓
Verify
 ↓
Regression
 ↓
Final evidence
 ↓
MISSION VERIFIED
```

The user did not have to manually drive each step.

---

# 191. Example small-task behavior

User:

```text
"Rename this variable from foo to user_id."
```

APEX should likely:

```text
single agent
single workspace
no swarm
minimal verification
```

This is important. APEX should optimize the mission rather than perform theatrical autonomy.

---

# 192. Example complex research behavior

User:

```text
"Deeply research a rapidly changing technology and compare implementation options."
```

APEX may choose:

```text
research planner
 ↓
8 parallel source researchers
 ↓
source normalization
 ↓
contradiction checker
 ↓
technical evaluator
 ↓
final synthesizer
 ↓
claim verification
```

---

# 193. Example recovery behavior

```text
Tool call fails
 ↓
failure classifier says TOOL_RUNTIME
 ↓
inspect logs
 ↓
retry once
 ↓
fails again
 ↓
search exact error
 ↓
discover dependency mismatch
 ↓
create repair task
 ↓
patch dependency
 ↓
run tests
 ↓
retry original task
 ↓
verify
```

This is the desired behavior: **failure becomes information, not a stopping point.**

---

# 194. Example long-running behavior

```text
DAY 1
Mission begins
→ architecture
→ coding
→ checkpoint

DAY 1 evening
worker crashes
→ lease expires
→ replacement agent
→ continue

DAY 2
network unavailable
→ external tasks parked
→ local tests continue

DAY 2 later
network returns
→ credentials revalidated
→ mission resumes

DAY 3
final verification
→ mission complete
```

---

# 195. Final architectural doctrine

The APEX doctrine for Alpha should be:

> **One objective in. APEX dynamically determines the strategy. Existing Alpha capabilities perform the work. The runtime records everything important. Failures trigger diagnosis and recovery. Plans can change. Agents can appear and disappear. Work can continue for long periods. Verification determines success. The user can steer, pause, stop or take over at any point. The policy kernel always remains authoritative.**

This gives Alpha a general-purpose autonomy substrate rather than a collection of disconnected “modes.”

---

# 196. Research-derived architectural guidance

The design above deliberately incorporates recurring patterns documented by current agent platforms and protocols:

### OpenAI Agents SDK

Useful patterns for Alpha:

```text
agents
agents-as-tools
handoffs
guardrails
sessions
sandbox agents
tracing
human-in-the-loop
```

Source: OpenAI Agents SDK documentation. [1][7][13]

### Anthropic

Useful patterns:

```text
augmented LLM
routing
parallelization
orchestrator-workers
agents
grounding in environmental feedback
sandboxing
stopping conditions
```

Source: Anthropic, *Building Effective AI Agents*. [3]

### Microsoft Agent Framework

Useful patterns:

```text
explicit graph workflows
sequential
concurrent
handoff
group chat
Magentic manager
checkpoints
resuming
human-in-the-loop
agent harness
```

Source: Microsoft Agent Framework documentation. [2][4][8]

### MCP 2026

Useful patterns:

```text
stateless protocol core
Tasks extension
async tool calls
status updates
cancellation
OAuth/authorization hardening
```

Source: MCP 2026-07-28 release/specification. [5][6]

### A2A

Useful patterns:

```text
stateful tasks
artifacts
streaming
push notifications
external agent interoperability
```

Source: A2A specification. [6]

### Temporal

Useful patterns:

```text
durable execution
recovery
long-running workflows
signals/events
retries
multi-agent orchestration
```

Source: Temporal AI/durable-execution material. [9]

### OpenCode

Useful pattern:

```text
allow / ask / deny
per-agent permissions
command/path patterns
```

Source: OpenCode permissions documentation. [10]

### OWASP

Threats that must be explicitly considered:

```text
prompt injection
insecure output handling
supply chain
sensitive disclosure
excessive agency
overreliance
```

Source: OWASP LLM risk guidance. [11][12]

---

# 197. References

[1] OpenAI Agents SDK — https://openai.github.io/openai-agents-python/  
[2] Microsoft Agent Framework — Workflows & Orchestrations — https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/  
[3] Anthropic — Building Effective AI Agents — https://www.anthropic.com/research/building-effective-agents  
[4] Microsoft Agent Framework — Workflow concepts — https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/  
[5] Model Context Protocol — 2026-07-28 release — https://blog.modelcontextprotocol.io/posts/2026-07-28/  
[6] MCP Tasks / A2A specifications — https://tasks.extensions.modelcontextprotocol.io/specification/2026-07-28/tasks and https://a2a-protocol.org/latest/specification/  
[7] OpenAI Agents SDK — Tracing — https://openai.github.io/openai-agents-python/tracing/  
[8] Microsoft Agent Framework — Durable Extension — https://learn.microsoft.com/en-us/agent-framework/integrations/durable-extension  
[9] Temporal — AI agents and durable execution — https://temporal.io/blog/building-ai-agents-that-overcome-the-complexity-cliff and https://temporal.io/blog/using-multi-agent-architectures-with-temporal  
[10] OpenCode — Permissions — https://docs.opencode.ai/docs/permissions/  
[11] OWASP — LLM06:2025 Excessive Agency — https://genai.owasp.org/llmrisk/llm062025-excessive-agency/  
[12] OWASP — Top 10 for Large Language Model Applications — https://owasp.org/www-project-top-10-for-large-language-model-applications/  
[13] OpenAI Agents SDK — Multi-agent orchestration — https://openai.github.io/openai-agents-python/multi_agent/  
[14] Anthropic — Trustworthy agents in practice — https://www.anthropic.com/research/trustworthy-agents  

---

# 198. Final build instruction for Alpha engineers/agents

When implementing this specification:

```text
READ THE EXISTING ALPHA CODEBASE FIRST.
DO NOT DELETE EXISTING FEATURES.
DO NOT CREATE A SECOND LIFECYCLE OWNER.
DO NOT DUPLICATE EXISTING ENGINES WHEN AN ADAPTER WILL WORK.
DO NOT LET THE LLM BE THE FINAL AUTHORITY FOR SECURITY OR PERMISSIONS.
DO NOT DECLARE SUCCESS WITHOUT VERIFICATION.
DO NOT USE INFINITE RETRIES.
DO NOT SPAWN UNBOUNDED AGENTS.
DO NOT SHARE MUTABLE WORKSPACES ACROSS CONCURRENT CODING AGENTS.
DO NOT SILENTLY FALL BACK TO ANOTHER MODEL OR TOOL.
DO NOT LOSE MISSION STATE ON RESTART.
DO NOT HIDE FAILURES FROM THE USER OR OBSERVABILITY SYSTEM.
DO BUILD THE EXECUTIVE CONTROL PLANE AROUND TYPED STATE, EVENTS, CAPABILITIES AND POLICIES.
DO REUSE ALPHA'S EXISTING PLANNER, GOAL, WORKFLOW, SWARM, TOOL, RESEARCH, MEMORY, VERIFICATION AND RECOVERY COMPONENTS WHERE THEY ALREADY EXIST.
DO MAKE APEX CAPABLE OF CREATING GOALS, SUBGOALS, TASKS, AGENTS, WORKFLOWS AND SWARMS DYNAMICALLY.
DO MAKE APEX ABLE TO SWITCH MODES INTERNALLY.
DO MAKE APEX ABLE TO REPLAN AFTER NEW INFORMATION OR FAILURE.
DO MAKE APEX ABLE TO CONTINUE LONG-RUNNING WORK THROUGH CHECKPOINTS.
DO MAKE THE UI SHOW WHAT APEX IS DOING.
DO TEST APEX WITH REAL END-TO-END MISSIONS AND FAULT INJECTION.
```

## End state

```text
USER:
"Do this."

ALPHA APEX:

UNDERSTAND
PLAN
DECOMPOSE
RESEARCH
SELECT
SPAWN
DELEGATE
EXECUTE
OBSERVE
VERIFY
RECOVER
REPLAN
CONTINUE
PERSIST
RESUME
SYNTHESIZE
PROVE
COMPLETE
```

**That is the target architecture for Alpha APEX Autopilot.**
