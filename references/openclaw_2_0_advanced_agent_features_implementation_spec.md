# OpenClaw 2.0 — Advanced Agent Feature Extraction & Implementation Specification

> **Purpose:** A practical engineering blueprint for studying OpenClaw 2.0 and subsequent OpenClaw capabilities, extracting the most important advanced agent patterns, and implementing/adapting them in a next-generation autonomous agent runtime.
>
> **Research date:** 2026-09-25
>
> **Scope:** Agent architecture, orchestration, goals, tasks, memory, self-learning, tools, browser/computer use, security, recovery, automation, multi-agent systems, external harnesses, and dynamic agent creation.

---

## 1. Executive Summary

OpenClaw 2.0 should not be understood merely as a chatbot upgrade.

The most important architectural lesson is that an advanced agent should become a **persistent execution system for real work**.

A mature agent runtime needs to manage:

1. Persistent goals
2. Durable tasks
3. Planning and replanning
4. Specialist agents
5. Parallel swarm execution
6. Tool discovery and dynamic loading
7. Browser and computer execution
8. Terminal and coding environments
9. Evidence collection
10. Verification and review
11. Long-term memory
12. Memory consolidation / dreaming
13. Skill learning
14. Model routing
15. Model failover
16. Error recovery
17. Human intervention
18. Event-driven automation
19. Security and capability permissions
20. External agent harnesses
21. Dynamic agent creation
22. Agent-to-agent communication
23. Runtime hooks
24. Observability
25. Durable execution state

The goal should therefore be:

```text
USER
  ↓
GOAL
  ↓
PLAN
  ↓
TASK GRAPH
  ↓
ORCHESTRATE
  ↓
SPECIALIST AGENTS
  ↓
TOOLS / MCP / BROWSER / COMPUTER / TERMINAL
  ↓
EVIDENCE
  ↓
VERIFY
  ↓
COMPLETE / CONTINUE / BLOCKED
  ↓
MEMORY
  ↓
LEARNING
  ↓
FUTURE PERFORMANCE IMPROVEMENT
```

---

# 2. Core Design Principle

The system should not be designed around:

```text
User → LLM → Tool → Answer
```

Instead:

```text
User Request
     ↓
Intent / Goal Extraction
     ↓
Objective Contract
     ↓
Planner
     ↓
Task Graph
     ↓
Execution Scheduler
     ↓
Specialist Agents
     ↓
Capability / Tool Discovery
     ↓
Execution
     ↓
Evidence Collection
     ↓
Verification
     ↓
Recovery / Replanning
     ↓
Goal Completion
     ↓
Delivery
     ↓
Memory + Skill Learning
```

The LLM is only one component.

The **agent runtime** owns execution state, permissions, tasks, memory, verification and recovery.

---

# 3. Feature Priority Matrix

| Priority | Feature | Importance |
|---|---|---|
| P0 | Durable Goal Engine | Critical |
| P0 | Task State Machine | Critical |
| P0 | Sub-Agent Runtime | Critical |
| P0 | Swarm / Parallel Execution | Critical |
| P0 | Evidence + Verification | Critical |
| P0 | Persistent Memory | Critical |
| P0 | Skill Learning | Critical |
| P0 | Dynamic Tool Discovery | Critical |
| P0 | Capability Security | Critical |
| P0 | Recovery / Failover | Critical |
| P1 | Event-Driven Automation | Very High |
| P1 | Browser Automation | Very High |
| P1 | Computer Use | Very High |
| P1 | Specialist Agent Teams | Very High |
| P1 | Agent-to-Agent Messaging | Very High |
| P1 | Context Compaction | Very High |
| P1 | External Agent Harness / ACP | Very High |
| P1 | Human Live Steering | Very High |
| P2 | Decision Model | High |
| P2 | Dynamic Agent Creation | High |
| P2 | Runtime Hooks | High |
| P2 | Advanced Observability | High |
| P2 | Multi-machine Execution | High |

---

# 4. Durable Goal Engine

## 4.1 Why Goals Are Different From Prompts

A prompt is temporary.

A goal is persistent.

A goal must survive:

- model changes
- process restarts
- context compaction
- agent delegation
- failures
- tool errors
- network failures
- machine restarts

## 4.2 Goal Object

```typescript
interface Goal {
  id: string;

  title: string;
  objective: string;

  constraints: Constraint[];
  successCriteria: SuccessCriterion[];

  priority: "critical" | "high" | "normal" | "low";

  status:
    | "draft"
    | "active"
    | "paused"
    | "blocked"
    | "completed"
    | "failed"
    | "cancelled";

  ownerAgentId: string;

  parentGoalId?: string;

  budget?: {
    maxTokens?: number;
    maxTimeMs?: number;
    maxCost?: number;
    maxToolCalls?: number;
  };

  createdAt: string;
  updatedAt: string;
}
```

## 4.3 Goal Contract

Every goal should define:

```text
OBJECTIVE
CONSTRAINTS
SUCCESS CRITERIA
AVAILABLE RESOURCES
PERMISSIONS
BUDGET
DEADLINE
EVIDENCE REQUIREMENTS
FAILURE POLICY
```

## 4.4 Goal Completion Rule

Never allow:

```text
LLM says "done"
→ system marks completed
```

Instead:

```text
LLM claims completion
        ↓
Verifier
        ↓
Success criteria
        ↓
Evidence
        ↓
Independent validation
        ↓
Goal state = completed
```

---

# 5. Task Engine

Every meaningful operation should become a durable task.

## 5.1 Task States

```text
QUEUED
  ↓
PLANNED
  ↓
RUNNING
  ↓
WAITING
  ↓
RETRYING
  ↓
VERIFYING
  ↓
SUCCEEDED
```

Failure branches:

```text
RUNNING
  ↓
FAILED
  ↓
RETRY
  ↓
RECOVER
  ↓
REPLAN
  ↓
ESCALATE
```

Terminal states:

```text
SUCCEEDED
FAILED
CANCELLED
TIMED_OUT
BLOCKED
LOST
```

## 5.2 Task Object

```typescript
interface Task {
  id: string;
  goalId: string;

  title: string;
  description: string;

  status: TaskStatus;

  assignedAgentId?: string;

  dependencies: string[];

  input: unknown;
  output?: unknown;

  evidence: Evidence[];

  retryCount: number;

  timeoutMs?: number;

  createdAt: string;
  startedAt?: string;
  completedAt?: string;
}
```

---

# 6. Planner

The planner converts:

```text
Goal
```

into:

```text
Task Graph
```

Example:

```text
Goal:
"Improve project authentication"

Tasks:

T1 → inspect current authentication
T2 → identify vulnerabilities
T3 → research recommended architecture
T4 → design changes
T5 → implement changes
T6 → run tests
T7 → security review
T8 → regression test
T9 → final verification
```

Dependencies:

```text
T1
 ├── T2
 └── T3
       ↓
      T4
       ↓
      T5
       ↓
      T6
       ↓
      T7
       ↓
      T8
       ↓
      T9
```

The planner must support replanning.

---

# 7. Dynamic Replanning

A plan should never be immutable.

Trigger replanning when:

- dependency fails
- new evidence appears
- required tool unavailable
- resource unavailable
- model fails
- task becomes unnecessary
- user changes requirement
- verification fails

Architecture:

```text
CURRENT STATE
     ↓
NEW EVIDENCE
     ↓
PLAN VALIDATOR
     ↓
KEEP / MODIFY / REBUILD
     ↓
NEW TASK GRAPH
```

---

# 8. Sub-Agent Architecture

The parent agent should be able to create workers.

```text
Parent Agent
    |
    +-- Research Agent
    |
    +-- Coding Agent
    |
    +-- Browser Agent
    |
    +-- QA Agent
    |
    +-- Security Agent
    |
    +-- Reviewer Agent
```

Each child gets:

- isolated session
- role
- objective
- tools
- permissions
- memory scope
- workspace
- model policy
- token budget
- time budget
- return contract

---

# 9. Agent Contract

```typescript
interface AgentContract {
  role: string;

  objective: string;

  inputs: string[];

  outputs: string[];

  allowedTools: string[];

  forbiddenTools: string[];

  permissions: Permission[];

  successCriteria: string[];

  maxRuntimeMs?: number;

  maxTokens?: number;

  evidenceRequired: boolean;
}
```

The parent should never merely say:

```text
"Research this."
```

It should say:

```text
ROLE:
Research specialist

OBJECTIVE:
Determine whether X supports Y.

REQUIRED OUTPUT:
- documented capabilities
- version
- limitations
- source evidence
- implementation recommendation

CONSTRAINTS:
- use primary sources where possible
- do not speculate

SUCCESS:
At least 3 verified sources.
```

---

# 10. Swarm Architecture

Swarm execution should be bounded.

## 10.1 Good Swarm

```text
Coordinator
   |
   +-- Research A
   +-- Research B
   +-- Research C
   +-- Research D
   |
   ↓
Result Collector
   ↓
Synthesizer
   ↓
Reviewer
```

## 10.2 Bad Swarm

```text
Agent
 ↓
spawn 50 agents
 ↓
each spawns 20
 ↓
resource exhaustion
```

Therefore implement:

```text
MAX_AGENTS
MAX_DEPTH
MAX_CONCURRENCY
MAX_TOTAL_TOKENS
MAX_TOTAL_COST
MAX_RUNTIME
MAX_CHILDREN_PER_AGENT
```

---

# 11. Agent-to-Agent Communication

Use typed messages.

```typescript
type AgentMessage =
  | TaskRequest
  | TaskResult
  | Question
  | Answer
  | ReviewRequest
  | ReviewResult
  | Handoff
  | Blocked
  | ProgressUpdate
  | Cancellation;
```

Example:

```json
{
  "type": "TASK_RESULT",
  "taskId": "task_123",
  "agentId": "researcher_01",
  "status": "success",
  "result": "...",
  "evidence": [],
  "confidence": 0.91
}
```

Do not rely on free-form chat between agents for critical state.

---

# 12. Specialist Agent Teams

A useful default team:

```text
Chief / Coordinator
│
├── Researcher
│   ├── Web
│   ├── Browser
│   └── Source verification
│
├── Architect
│   ├── Design
│   ├── Dependency analysis
│   └── Architecture
│
├── Builder
│   ├── Terminal
│   ├── Git
│   └── Coding harness
│
├── QA
│   ├── Tests
│   ├── Regression
│   └── Runtime validation
│
├── Security
│   ├── Permissions
│   ├── Vulnerability checks
│   └── Policy validation
│
└── Reviewer
    ├── Evidence
    ├── Goal verification
    └── Final acceptance
```

---

# 13. Dynamic Agent Creation

The runtime should support:

```text
createAgent(role)
configureAgent()
assignTools()
assignMemory()
assignModel()
assignWorkspace()
assignPermissions()
startAgent()
pauseAgent()
resumeAgent()
destroyAgent()
```

Example:

```typescript
const agent = await agentFactory.create({
  role: "security-reviewer",

  objective:
    "Review authentication changes for security issues",

  model: "security-capable-model",

  tools: [
    "filesystem.read",
    "terminal.readonly",
    "git.diff"
  ],

  permissions: [
    "project.read"
  ]
});
```

---

# 14. Dynamic Tool Discovery

Never inject every tool schema into every context.

Use:

```text
Agent
 ↓
Capability Search
 ↓
Tool Discovery
 ↓
Tool Description
 ↓
Schema Loading
 ↓
Execution
```

Tool registry:

```typescript
interface ToolDefinition {
  id: string;
  name: string;

  description: string;

  capabilities: string[];

  riskLevel: "low" | "medium" | "high" | "critical";

  permissions: string[];

  inputSchema: object;
}
```

Search example:

```text
Need:
"search GitHub repositories"

Capability search:
github + repository + search

Candidate:
github.search_repositories

Load schema
Execute
```

---

# 15. Code Mode

A large tool catalog can be exposed through a compact execution interface.

Instead of:

```text
500 tools × schemas
```

use:

```text
search_tools()
describe_tool()
call_tool()
```

This reduces context overhead.

---

# 16. MCP Architecture

MCP servers should be dynamically managed.

Required lifecycle:

```text
DISCOVER
  ↓
INSTALL / CONNECT
  ↓
AUTHENTICATE
  ↓
VALIDATE
  ↓
REGISTER
  ↓
INDEX CAPABILITIES
  ↓
USE
  ↓
MONITOR
  ↓
DISABLE / UPDATE / REMOVE
```

MCP permission model:

```text
server
  ↓
tools
  ↓
individual capability
  ↓
agent permission
```

Never automatically trust every tool exposed by an MCP server.

---

# 17. Browser Agent

Browser should be a persistent execution environment.

Capabilities:

```text
navigate
click
type
scroll
select
upload
download
screenshot
PDF
inspect DOM
extract content
wait
handle popup
handle authentication
maintain tabs
maintain sessions
```

State:

```typescript
interface BrowserState {
  browserId: string;
  profileId: string;
  tabs: BrowserTab[];
  activeTabId?: string;
}
```

---

# 18. Computer Use

Computer-use execution should maintain machine/session identity.

```text
Agent
 ↓
Computer Session
 ↓
Machine
 ↓
Window
 ↓
Application
 ↓
Interaction
```

Support:

```text
mouse
keyboard
screen
window management
application launch
clipboard
screenshots
```

Critical operations should require appropriate permission.

---

# 19. Terminal / Coding Environment

The coding agent needs:

```text
filesystem
terminal
Git
package managers
test runner
build system
lint
formatter
compiler
process management
```

Recommended isolation:

```text
Agent
 ↓
Workspace
 ↓
Sandbox
 ↓
Command policy
 ↓
Execution
```

---

# 20. External Agent Harness / ACP

A powerful architecture is:

```text
Main Agent
   |
   +-- Internal agent
   |
   +-- Claude Code
   |
   +-- Gemini CLI
   |
   +-- Codex-style coding agent
   |
   +-- OpenHands
   |
   +-- Other harness
```

The main runtime becomes an **agent-of-agents / harness-of-harnesses**.

Each external harness should be treated as an execution provider with:

```text
start
send
observe
interrupt
cancel
collect result
collect logs
```

---

# 21. Persistent Memory

Do not implement memory as one giant file.

Use layers:

```text
Layer 1 — Working Memory
Current task state

Layer 2 — Session Memory
Current conversation/task transcript

Layer 3 — Episodic Memory
Past experiences

Layer 4 — Semantic Memory
Facts and concepts

Layer 5 — Procedural Memory
How to perform tasks

Layer 6 — User/Project Memory
Stable preferences and project facts
```

---

# 22. Memory Object

```typescript
interface Memory {
  id: string;

  type:
    | "working"
    | "episodic"
    | "semantic"
    | "procedural"
    | "project"
    | "user";

  content: string;

  source: string;

  confidence: number;

  importance: number;

  recurrence: number;

  createdAt: string;
  updatedAt: string;

  supersedes?: string[];
  relatedTo?: string[];
}
```

---

# 23. Active Recall

When the agent encounters a question:

```text
Current problem
      ↓
Memory query
      ↓
Relevant memories
      ↓
Rank
      ↓
Context injection
```

Ranking can combine:

```text
semantic similarity
+
recency
+
importance
+
frequency
+
task relevance
+
source reliability
```

---

# 24. Memory Consolidation / Dreaming

Background processing:

```text
Live Work
   ↓
Candidate memories
   ↓
Light processing
   ↓
Reflection / relationship discovery
   ↓
Deep consolidation
   ↓
Promotion
   ↓
Durable memory
```

Potential operations:

```text
deduplicate
merge
supersede
promote
decay
archive
link
summarize
extract facts
extract procedures
```

Important rule:

> Memory should be evidence-backed whenever possible.

---

# 25. Skill Learning

A successful agent should learn reusable procedures.

Lifecycle:

```text
Task
 ↓
Outcome
 ↓
Observe corrections
 ↓
Identify reusable procedure
 ↓
Generate skill proposal
 ↓
Validate
 ↓
Test
 ↓
Review
 ↓
Publish
```

Skill object:

```typescript
interface Skill {
  id: string;

  name: string;

  description: string;

  triggerConditions: string[];

  procedure: string;

  requiredTools: string[];

  examples: string[];

  successCriteria: string[];

  version: string;

  status:
    | "draft"
    | "quarantined"
    | "approved"
    | "deprecated";
}
```

---

# 26. Skill Safety

Never allow unrestricted self-modification.

Separate:

```text
CORE RUNTIME
SYSTEM POLICIES
USER-OWNED SKILLS
AGENT-LEARNED SKILLS
EXPERIMENTAL SKILLS
```

Recommended flow:

```text
Learned change
 ↓
Sandbox
 ↓
Tests
 ↓
Regression evaluation
 ↓
Approval policy
 ↓
Promotion
```

---

# 27. Evidence Engine

Every important claim should optionally have evidence.

```typescript
interface Evidence {
  id: string;

  type:
    | "web"
    | "file"
    | "tool"
    | "test"
    | "command"
    | "screenshot"
    | "agent";

  source: string;

  content: string;

  timestamp: string;

  confidence?: number;
}
```

Evidence should be linked to tasks and success criteria.

---

# 28. Verification Engine

Verification is separate from execution.

```text
Builder
  ↓
Claims:
"Implementation complete"

Verifier
  ↓
Run tests
Inspect diff
Check requirements
Check runtime
  ↓
PASS / FAIL / NEEDS_WORK
```

For critical work, use an independent reviewer.

---

# 29. Confidence System

Confidence should not mean:

```text
LLM says 95%
```

Instead derive confidence from evidence:

```text
source quality
+
number of independent confirmations
+
test success
+
verification success
+
historical reliability
```

---

# 30. Model Routing

Separate model roles.

```text
Main Model
→ reasoning / planning / synthesis

Fast Model
→ classification / simple extraction

Decision Model
→ routing / policy / yes-no decisions

Coding Model
→ implementation

Vision Model
→ screenshots / visual interaction

Embedding Model
→ memory retrieval

Small Local Model
→ always-on background tasks
```

---

# 31. Decision Model

A small decision model can handle:

```text
Which agent?
Which tool?
Which model?
Is this risky?
Should we retry?
Should we escalate?
Should we ask the user?
Is the task complete?
```

This reduces expensive LLM calls.

---

# 32. Model Failover

Naive:

```text
Model A fails
→ Model B
```

Robust:

```text
Model A
 ↓
partial execution
 ↓
failure
 ↓
inspect state
 ↓
detect side effects
 ↓
determine replay safety
 ↓
select fallback
 ↓
continue
```

Never blindly replay side-effecting operations.

---

# 33. Recovery Engine

Recovery classes:

```text
TRANSIENT
PERMISSION
NETWORK
TOOL
MODEL
RESOURCE
LOGIC
VERIFICATION
ENVIRONMENT
```

Example:

```text
Tool timeout
 ↓
retry with backoff
 ↓
if repeated
 ↓
alternative tool
 ↓
if unavailable
 ↓
alternative agent
 ↓
if still blocked
 ↓
replan
 ↓
if impossible
 ↓
human escalation
```

---

# 34. Self-Healing

Self-healing should operate at several levels.

### Level 1 — Retry

```text
same operation
```

### Level 2 — Recovery

```text
restart failed process
```

### Level 3 — Alternative

```text
different tool/model
```

### Level 4 — Replanning

```text
different strategy
```

### Level 5 — Specialist escalation

```text
security/debug/research agent
```

### Level 6 — Human escalation

```text
ask operator
```

---

# 35. Event-Driven Automation

Do not depend only on cron.

Use:

```text
EVENT
 ↓
FILTER
 ↓
CONDITION
 ↓
DECISION
 ↓
TASK
 ↓
AGENT
 ↓
ACTION
 ↓
RESULT
```

Events can include:

```text
time
GitHub event
file changed
email received
message received
website change
system alert
task completion
agent failure
new memory
new skill
deployment event
```

---

# 36. Automation Example

```text
GitHub PR opened
       ↓
Event Listener
       ↓
Security Agent
       ↓
Code Review Agent
       ↓
Test Agent
       ↓
Reviewer
       ↓
Report
```

---

# 37. Human-in-the-Loop

The user must be able to:

```text
pause
resume
cancel
redirect
change objective
change permissions
change model
inspect reasoning artifacts
inspect tool calls
approve risky actions
inject evidence
```

Human intervention should not destroy the existing task state.

---

# 38. Approval Architecture

Sensitive capabilities:

```text
filesystem.write
shell.execute
credential access
external communication
financial action
account modification
deployment
destructive operations
```

should have policy controls.

Example:

```typescript
interface Permission {
  capability: string;

  scope: string;

  mode:
    | "deny"
    | "ask"
    | "allow";

  expiresAt?: string;
}
```

---

# 39. Capability-Based Security

Never grant:

```text
"agent = full access"
```

Instead:

```text
Agent
 ↓
Capability Token
 ↓
Specific Resource
 ↓
Specific Action
```

Example:

```text
project.read
project.write
git.read
git.commit
git.push
shell.read
shell.execute
browser.read
browser.interact
```

---

# 40. Sandboxing

Recommended execution levels:

```text
Level 0
No external execution

Level 1
Read-only

Level 2
Workspace write

Level 3
Sandboxed command execution

Level 4
Network access

Level 5
Host-level privileged action
```

Default should be the lowest capability required.

---

# 41. Runtime Hooks

The runtime should expose lifecycle events:

```text
before_goal
after_goal

before_plan
after_plan

before_task
after_task

before_agent_spawn
after_agent_spawn

before_tool_call
after_tool_call

before_model_call
after_model_call

before_memory_write
after_memory_write

before_skill_publish
after_skill_publish

before_delivery
after_delivery
```

This allows plugins to add:

- security
- telemetry
- auditing
- memory
- learning
- policy
- analytics

without modifying core execution.

---

# 42. Observability

Every agent action should produce structured telemetry.

Track:

```text
goal
task
agent
model
tool
latency
tokens
cost
retries
errors
permissions
evidence
result
verification
```

Example:

```json
{
  "event": "tool_call",
  "agentId": "builder",
  "tool": "terminal.exec",
  "latencyMs": 812,
  "success": true
}
```

---

# 43. Agent Dashboard

Useful dashboard sections:

```text
SYSTEM
├── CPU
├── RAM
├── Disk
├── Network
└── Processes

AGENTS
├── Active
├── Waiting
├── Blocked
├── Failed
└── Completed

TASKS
├── Queue
├── Running
├── Verification
└── Failed

GOALS
├── Active
├── Paused
└── Completed

MEMORY
├── Recent
├── Important
└── Consolidation

SKILLS
├── Active
├── Proposed
└── Experimental

TOOLS
├── Connected
├── Failed
└── Disabled
```

---

# 44. Context Management

Long-running agents require context management.

When context approaches a threshold:

```text
Detect pressure
 ↓
Extract unresolved tasks
 ↓
Flush durable memory
 ↓
Summarize transcript
 ↓
Preserve important evidence
 ↓
Compact
 ↓
Continue
```

Never summarize away:

```text
goal
constraints
success criteria
unresolved tasks
important decisions
tool side effects
evidence
user corrections
```

---

# 45. Session Architecture

Separate:

```text
Agent
Session
Goal
Task
Conversation
Execution
Workspace
Memory
```

Example:

```text
Agent A
 ├── Session 1
 │    ├── Goal 1
 │    └── Tasks
 │
 └── Session 2
      └── Goal 2
```

---

# 46. Persistent Sessions

A background agent should survive:

```text
UI closed
process restart
temporary network loss
model failure
machine reboot
```

The session state should be stored durably.

---

# 47. Multi-Machine Architecture

For future scaling:

```text
Control Plane
     |
     +---- Windows Worker
     |
     +---- Linux Worker
     |
     +---- Cloud Worker
     |
     +---- GPU Worker
```

Task scheduler chooses the execution environment.

---

# 48. Resource-Aware Scheduling

Each worker reports:

```text
CPU
RAM
GPU
VRAM
disk
network
availability
current tasks
model availability
```

Scheduler:

```text
Task requirements
       ↓
Worker capability matching
       ↓
Resource availability
       ↓
Security policy
       ↓
Select worker
```

---

# 49. Agent Lifecycle

```text
CREATED
  ↓
INITIALIZING
  ↓
READY
  ↓
RUNNING
  ↓
WAITING
  ↓
RUNNING
  ↓
COMPLETED
```

Failure:

```text
RUNNING
 ↓
ERROR
 ↓
RECOVERING
 ↓
RUNNING
```

Permanent:

```text
ERROR
 ↓
QUARANTINED
```

---

# 50. Complete Agent Execution Loop

```text
1. Receive request

2. Determine whether request creates a goal

3. Create objective contract

4. Load relevant memory

5. Discover required capabilities

6. Select model

7. Create plan

8. Validate plan

9. Create tasks

10. Schedule independent tasks

11. Spawn specialists

12. Execute tools

13. Record evidence

14. Monitor execution

15. Recover failures

16. Replan when necessary

17. Verify task results

18. Verify goal

19. Produce result

20. Store useful memories

21. Learn reusable skills

22. Update telemetry

23. Remain available for follow-up
```

---

# 51. Reference Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                        USER / CHANNELS                      │
│ Telegram / Web / Desktop / CLI / API / Voice              │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       GATEWAY / ROUTER                     │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       GOAL ENGINE                           │
│ objective / constraints / success / budget / permissions   │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       ORCHESTRATOR                         │
│ planner / scheduler / replanner / model router             │
└───────────────┬──────────────────┬──────────────────────────┘
                │                  │
                ▼                  ▼
        ┌───────────────┐   ┌──────────────────┐
        │ TASK ENGINE   │   │ AGENT FACTORY    │
        └───────┬───────┘   └────────┬─────────┘
                │                    │
                └─────────┬──────────┘
                          ▼
                 ┌─────────────────┐
                 │ AGENT RUNTIME   │
                 │ sessions        │
                 │ roles           │
                 │ policies        │
                 └────────┬────────┘
                          │
              ┌───────────┼────────────┐
              ▼           ▼            ▼
          Tools/MCP    Browser      ACP/Harness
              │           │            │
              └───────────┼────────────┘
                          ▼
                 ┌─────────────────┐
                 │ EXECUTION FABRIC│
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ EVIDENCE ENGINE │
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ VERIFICATION    │
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ DELIVERY        │
                 └────────┬────────┘
                          ▼
             ┌───────────────────────────┐
             │ MEMORY + DREAMING        │
             │ SKILL LEARNING           │
             │ TELEMETRY                │
             └───────────────────────────┘
```

---

# 52. Suggested Module Structure

```text
agent-runtime/
│
├── core/
│   ├── runtime
│   ├── lifecycle
│   ├── state
│   └── events
│
├── goals/
│   ├── goal-engine
│   ├── goal-validator
│   └── success-checker
│
├── planning/
│   ├── planner
│   ├── task-graph
│   └── replanner
│
├── orchestration/
│   ├── scheduler
│   ├── swarm
│   ├── delegation
│   └── agent-factory
│
├── agents/
│   ├── registry
│   ├── profiles
│   ├── sessions
│   ├── roles
│   └── messaging
│
├── tools/
│   ├── registry
│   ├── discovery
│   ├── permissions
│   ├── execution
│   └── mcp
│
├── environments/
│   ├── terminal
│   ├── browser
│   ├── computer
│   ├── git
│   └── remote
│
├── memory/
│   ├── working
│   ├── episodic
│   ├── semantic
│   ├── procedural
│   ├── retrieval
│   └── dreaming
│
├── skills/
│   ├── registry
│   ├── discovery
│   ├── learning
│   ├── evaluation
│   └── publishing
│
├── models/
│   ├── router
│   ├── failover
│   ├── decision
│   └── providers
│
├── verification/
│   ├── evidence
│   ├── validators
│   ├── tests
│   └── reviewers
│
├── recovery/
│   ├── retry
│   ├── rollback
│   ├── replanning
│   └── escalation
│
├── automation/
│   ├── scheduler
│   ├── events
│   └── triggers
│
├── security/
│   ├── capabilities
│   ├── approvals
│   ├── sandbox
│   └── audit
│
└── observability/
    ├── telemetry
    ├── metrics
    ├── traces
    └── dashboard
```

---

# 53. Database Model

Suggested entities:

```text
agents
sessions
goals
tasks
task_dependencies
agent_messages
tool_definitions
tool_permissions
tool_calls
mcp_servers
workspaces
browser_sessions
computer_sessions
memories
memory_links
skills
skill_versions
skill_evaluations
models
model_runs
evidence
verifications
automations
events
approvals
audit_logs
```

---

# 54. Event Bus

Everything should produce events.

Example:

```text
goal.created
goal.updated
goal.completed

task.created
task.started
task.failed
task.completed

agent.created
agent.started
agent.paused
agent.failed
agent.completed

tool.called
tool.failed

memory.created
memory.promoted

skill.proposed
skill.approved

model.failed
model.switched

verification.failed
verification.passed
```

This allows the rest of the platform to remain loosely coupled.

---

# 55. Important Design Rule: Separate State From LLM Output

Never treat model output as the source of truth.

Bad:

```text
LLM:
"Task completed."

Runtime:
task.status = completed
```

Good:

```text
LLM:
"Task appears complete."

Runtime:
execute verification
inspect evidence
validate success criteria
update durable state
```

The runtime owns truth.

---

# 56. Important Design Rule: Every Action Should Be Auditable

Store:

```text
who
what
when
why
which model
which tool
which permission
which input
which output
which evidence
which result
```

This makes debugging and recovery possible.

---

# 57. Important Design Rule: Never Silently Fail

Bad:

```text
tool failed
→ agent continues
→ final answer says success
```

Good:

```text
tool failed
→ retry
→ alternate method
→ replan
→ if impossible:
   blocked state
→ report exact blocker
```

---

# 58. Important Design Rule: Never Silently Change the Goal

If the requested goal becomes impossible:

```text
Original goal
 ↓
Blocker
 ↓
Alternative discovered
 ↓
Ask / policy decision
```

Do not automatically redefine the user's objective.

---

# 59. Important Design Rule: Evidence Before Confidence

Prefer:

```text
"I verified X using A and B."
```

over:

```text
"I think X is true."
```

For autonomous work:

```text
Action
 ↓
Evidence
 ↓
Verification
 ↓
Completion
```

---

# 60. OpenClaw Feature → Implementation Mapping

| OpenClaw concept | Implementation target |
|---|---|
| Goals | Goal Engine |
| Sub-agents | Agent Runtime |
| Swarm | Parallel Orchestrator |
| Tasks | Durable Task Engine |
| Tool Search | Capability Registry |
| Code Mode | Tool Execution Gateway |
| Memory | Layered Memory System |
| Dreaming | Background Consolidation |
| Skill Workshop | Skill Evolution Engine |
| Browser | Browser Environment |
| Computer Use | Desktop Environment |
| ACP | External Harness Gateway |
| Model Failover | Recovery Controller |
| Decision Model | Lightweight Decision Layer |
| Automations | Event Automation Engine |
| Hooks | Runtime Event Bus |
| Team Presets | Agent Role Templates |
| Sessions | Persistent Session Manager |
| Approvals | Capability Security |
| Compaction | Context Manager |

---

# 61. What NOT to Copy Blindly

Do not copy a feature merely because OpenClaw has it.

Evaluate every feature against:

```text
Does it improve reliability?
Does it improve autonomy?
Does it reduce context cost?
Does it improve recovery?
Does it improve safety?
Does it improve observability?
Does it improve user control?
```

Avoid:

- unrestricted self-modification
- unlimited agent spawning
- unlimited retries
- giant tool prompts
- memory without provenance
- agent-reported completion without verification
- automatic privilege escalation
- blind model failover
- hidden side effects
- uncontrolled recursive delegation

---

# 62. Recommended Implementation Phases

## Phase 1 — Runtime Foundation

Implement:

```text
Agent
Session
Goal
Task
Event Bus
State Store
```

Acceptance:

- agent survives restart
- task survives restart
- goal state is durable

---

## Phase 2 — Planning

Implement:

```text
Planner
Task Graph
Dependencies
Scheduler
Replanner
```

Acceptance:

- independent tasks run concurrently
- dependent tasks wait
- failed tasks trigger recovery/replanning

---

## Phase 3 — Sub-Agents

Implement:

```text
Agent Factory
Roles
Contracts
Messaging
Delegation
```

Acceptance:

- parent creates worker
- worker executes isolated task
- result returns structurally
- parent can continue

---

## Phase 4 — Swarm

Implement:

```text
parallel execution
concurrency limits
depth limits
budget limits
result aggregation
```

Acceptance:

- bounded parallelism
- no recursive explosion
- structured synthesis

---

## Phase 5 — Tool System

Implement:

```text
Tool Registry
Capability Search
Schema Loading
MCP
Permissions
```

Acceptance:

- tool schemas loaded on demand
- agent cannot access unauthorized tools

---

## Phase 6 — Execution Environments

Implement:

```text
Terminal
Git
Browser
Computer
Filesystem
```

Acceptance:

- environment state persists
- side effects are tracked

---

## Phase 7 — Evidence + Verification

Implement:

```text
Evidence Engine
Verification Engine
Independent Reviewer
```

Acceptance:

- completion requires success criteria
- important claims have evidence

---

## Phase 8 — Memory

Implement:

```text
Working Memory
Episodic Memory
Semantic Memory
Procedural Memory
Retrieval
Consolidation
```

Acceptance:

- useful previous information can be retrieved
- memory doesn't grow uncontrollably

---

## Phase 9 — Skill Learning

Implement:

```text
skill extraction
proposal
evaluation
sandbox
promotion
rollback
```

Acceptance:

- agent can learn reusable procedures
- bad learned behavior can be rejected

---

## Phase 10 — Recovery

Implement:

```text
retry
backoff
fallback
rollback
replan
escalation
```

Acceptance:

- transient errors recover
- permanent failures become visible
- side effects are not blindly replayed

---

## Phase 11 — Automation

Implement:

```text
cron
events
conditions
triggers
background tasks
```

Acceptance:

- tasks execute without active chat
- history is durable

---

## Phase 12 — Advanced Autonomy

Implement:

```text
dynamic agent creation
decision model
multi-machine workers
external harnesses
advanced self-learning
```

---

# 63. Testing Strategy

Every feature should have:

### Unit Tests

```text
goal state
task state
permission checks
routing
memory ranking
```

### Integration Tests

```text
agent → tool
agent → subagent
goal → tasks
task → verification
memory → retrieval
```

### Failure Tests

```text
model timeout
network failure
tool failure
process crash
machine restart
partial execution
duplicate event
```

### Adversarial Tests

```text
prompt injection
malicious tool
permission bypass
recursive spawning
memory poisoning
skill poisoning
unsafe command
```

---

# 64. Autonomous Evaluation

Build benchmark scenarios:

```text
Research task
Coding task
Browser task
Multi-step task
Long-running task
Failure recovery task
Memory recall task
Skill learning task
Multi-agent task
Security task
```

Measure:

```text
completion rate
verification accuracy
recovery rate
tool efficiency
token efficiency
cost
latency
false completion rate
unsafe action rate
memory usefulness
skill improvement
```

---

# 65. Advanced RSI / Self-Improvement Integration

A safe recursive improvement architecture:

```text
Observe
 ↓
Measure
 ↓
Identify bottleneck
 ↓
Propose improvement
 ↓
Implement in sandbox
 ↓
Run tests
 ↓
Run benchmark
 ↓
Compare against baseline
 ↓
Review
 ↓
Promote
 ↓
Monitor
 ↓
Rollback if regression
```

Never:

```text
Agent thinks it is better
→ automatically rewrites core
→ deploys itself
```

---

# 66. RSI Improvement Targets

The system can improve:

```text
planning strategies
tool selection
prompt templates
skill procedures
memory retrieval
model routing
retry policies
verification strategies
agent role definitions
task decomposition
resource scheduling
```

Core security and authorization should remain governed.

---

# 67. Example: Fully Autonomous Coding Task

User:

```text
Fix authentication bugs in my project.
```

Runtime:

```text
1. Create goal
2. Inspect repository
3. Load project memory
4. Create architecture plan
5. Spawn researcher
6. Spawn security reviewer
7. Spawn implementation agent
8. Run research in parallel
9. Analyze existing code
10. Implement fix
11. Run tests
12. Run security checks
13. Review Git diff
14. Run regression
15. Verify success criteria
16. Commit if authorized
17. Update project memory
18. Extract reusable skill
19. Report exact evidence
```

---

# 68. Example: Research Task

```text
Goal:
Determine whether technology X is suitable.

Research agents:
A → official documentation
B → GitHub
C → technical papers
D → practical implementation

Then:

Result collector
 ↓
Deduplication
 ↓
Source verification
 ↓
Synthesizer
 ↓
Reviewer
 ↓
Final report
```

---

# 69. Example: Self-Healing Task

```text
Task running
 ↓
API failure
 ↓
Retry
 ↓
Failure
 ↓
Alternative provider
 ↓
Failure
 ↓
Check local model
 ↓
Continue
 ↓
Verification
 ↓
Complete
```

---

# 70. Example: Background Agent

```text
Every morning:

Event
 ↓
Create task
 ↓
Research new developments
 ↓
Compare against project capabilities
 ↓
Identify useful changes
 ↓
Create implementation proposals
 ↓
Run evaluation
 ↓
Store findings
 ↓
Notify user
```

---

# 71. Minimum Viable Advanced Agent

If implementation resources are limited, implement these first:

```text
1. Durable Goal Engine
2. Durable Task Engine
3. Planner
4. Sub-Agent Runtime
5. Tool Registry
6. Evidence Engine
7. Verification Engine
8. Memory
9. Recovery
10. Security
```

Do not prioritize cosmetic UI over these foundations.

---

# 72. Ideal Advanced Agent

The mature system should eventually behave like:

```text
Persistent
Context-aware
Goal-driven
Tool-capable
Multi-agent
Parallel
Evidence-driven
Self-monitoring
Self-recovering
Self-learning
Memory-enabled
Event-driven
Security-aware
Human-steerable
Model-agnostic
Environment-aware
```

---

# 73. Final Architecture Philosophy

The strongest lesson from OpenClaw's evolution is:

> **The intelligence of an autonomous agent is not only the intelligence of its LLM. It is the intelligence of the runtime surrounding the LLM.**

A powerful model without:

```text
memory
planning
verification
permissions
recovery
tools
orchestration
persistent state
```

is still a fragile assistant.

A strong runtime can combine:

```text
small local models
+
large hosted models
+
specialist agents
+
external coding harnesses
+
MCP
+
browser
+
computer use
+
persistent memory
+
learned skills
```

into one coherent autonomous system.

---

# 74. Recommended Target Architecture

```text
                         USER
                          │
                          ▼
                     ┌─────────┐
                     │ GATEWAY │
                     └────┬────┘
                          │
                          ▼
                  ┌───────────────┐
                  │ GOAL ENGINE   │
                  └───────┬───────┘
                          │
                          ▼
                  ┌───────────────┐
                  │ ORCHESTRATOR  │
                  └───────┬───────┘
                          │
             ┌────────────┼────────────┐
             │            │            │
             ▼            ▼            ▼
          PLANNER      MEMORY      DECISION
             │
             ▼
        ┌────────────┐
        │ TASK GRAPH │
        └─────┬──────┘
              │
       ┌──────┼───────┐
       ▼      ▼       ▼
    AGENT   AGENT   AGENT
       │      │       │
       └──────┼───────┘
              │
              ▼
       CAPABILITY BUS
              │
     ┌────────┼──────────┐
     ▼        ▼          ▼
   TOOLS    BROWSER    COMPUTER
     │        │          │
     └────────┼──────────┘
              ▼
         EXECUTION
              │
              ▼
          EVIDENCE
              │
              ▼
         VERIFICATION
              │
       ┌──────┴──────┐
       ▼             ▼
    COMPLETE       REPLAN
       │             │
       ▼             └───────┐
    DELIVERY                 │
       │                     │
       ▼                     │
 MEMORY + SKILL LEARNING ◄───┘
```

---

# 75. Official / Primary References

The implementation should be validated against the current OpenClaw documentation because OpenClaw changes rapidly.

Primary areas to study:

- OpenClaw 2026.8.1 / 2.0 release documentation
- Goals
- Sub-agents
- Swarm
- Tasks
- Memory
- Dreaming
- Skill Workshop / self-learning
- Tool Search
- Code Mode
- Browser
- Computer Use
- ACP agents
- Model Failover
- Decision Models
- Automations
- Hooks
- Agents / team configuration
- Security and approvals

Useful official documentation domains:

```text
https://docs.openclaw.ai/
https://github.com/openclaw/openclaw
```

---

# 76. Final Implementation Checklist

## Runtime

- [ ] Persistent agent runtime
- [ ] Persistent sessions
- [ ] Persistent goals
- [ ] Persistent tasks
- [ ] Event bus
- [ ] State machine

## Planning

- [ ] Goal extraction
- [ ] Objective contract
- [ ] Task graph
- [ ] Dependencies
- [ ] Dynamic replanning

## Multi-Agent

- [ ] Agent factory
- [ ] Agent roles
- [ ] Agent contracts
- [ ] Sub-agents
- [ ] Swarm
- [ ] Agent messaging
- [ ] Dynamic agent creation

## Tools

- [ ] Tool registry
- [ ] Tool search
- [ ] Dynamic schema loading
- [ ] MCP
- [ ] Code Mode
- [ ] Capability permissions

## Execution

- [ ] Terminal
- [ ] Filesystem
- [ ] Git
- [ ] Browser
- [ ] Computer use
- [ ] External coding harnesses
- [ ] Remote workers

## Intelligence

- [ ] Model router
- [ ] Decision model
- [ ] Model failover
- [ ] Context management
- [ ] Evidence collection
- [ ] Verification

## Memory

- [ ] Working memory
- [ ] Episodic memory
- [ ] Semantic memory
- [ ] Procedural memory
- [ ] Active recall
- [ ] Memory consolidation
- [ ] Dreaming

## Learning

- [ ] Skill extraction
- [ ] Skill proposal
- [ ] Skill evaluation
- [ ] Skill sandbox
- [ ] Skill promotion
- [ ] Skill rollback

## Reliability

- [ ] Retry
- [ ] Backoff
- [ ] Recovery
- [ ] Replanning
- [ ] Side-effect tracking
- [ ] Human escalation
- [ ] Durable background execution

## Security

- [ ] Capability-based permissions
- [ ] Approval system
- [ ] Sandboxing
- [ ] Audit logs
- [ ] Prompt injection defenses
- [ ] Tool trust boundaries
- [ ] Skill poisoning protection
- [ ] Memory poisoning protection

## Operations

- [ ] Telemetry
- [ ] Metrics
- [ ] Tracing
- [ ] Agent dashboard
- [ ] Task dashboard
- [ ] Goal dashboard
- [ ] Resource monitoring
- [ ] Multi-machine scheduling

---

# 77. Bottom Line

The highest-value OpenClaw-inspired features are:

```text
1. Durable Goals
2. Durable Tasks
3. Parent/Child Agents
4. Swarm Execution
5. Specialist Teams
6. Agent Messaging
7. Dynamic Tool Discovery
8. Persistent Memory
9. Memory Dreaming / Consolidation
10. Self-Learning Skills
11. Evidence + Verification
12. Browser + Computer Execution
13. External Agent Harnesses
14. Model Routing + Failover
15. Event-Driven Automation
16. Human Live Steering
17. Capability-Based Security
18. Context Compaction
19. Runtime Hooks
20. Dynamic Agent Creation
```

These should be treated as **architecture primitives**, not isolated features.

The end goal is an agent that can:

```text
UNDERSTAND
   ↓
PLAN
   ↓
DELEGATE
   ↓
EXECUTE
   ↓
OBSERVE
   ↓
VERIFY
   ↓
RECOVER
   ↓
LEARN
   ↓
REMEMBER
   ↓
IMPROVE
```

while keeping the human in control of objectives, permissions and high-impact actions.
