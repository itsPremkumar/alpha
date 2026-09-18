# Autonomous AI Company
## Full Goal, Architecture, Execution, Monitoring, Recovery and Continuous Operation Plan

# 1. Master Goal

The system must allow a user to give a goal such as:

> **“Create an AI software company and operate it autonomously for the next five years. Build products, manage engineering, research markets, acquire users, maintain infrastructure, support customers, discover new opportunities, monitor every agent, recover failed agents, dynamically create specialists and teams, and continue operating without requiring me to manually assign work.”**

The system converts this single instruction into a persistent autonomous organization.

The human becomes:

```text id="2m1xj2"
OWNER
```

The AI system becomes:

```text id="cb6v6c"
AUTONOMOUS ORGANIZATION
```

The organization continues operating until:

```text id="d0j7bw"
Mission completed
OR
Owner stops/pauses it
OR
Mission expires
OR
Policy requires human decision
```

---

# 2. The Core Principle

The company is not the real abstraction.

The real abstraction is:

```text id="mzn6xk"
MISSION
    ↓
ORGANIZATION
    ↓
WORKFORCE
    ↓
EXECUTION
    ↓
MONITORING
    ↓
RECOVERY
    ↓
VERIFICATION
    ↓
LEARNING
    ↓
DISCOVERY
    ↓
NEXT WORK
    ↓
CONTINUE
```

Company is simply the most demanding demonstration.

The same infrastructure should later support:

```text id="0w90ah"
Software Company
AI Agency
Research Lab
Open-Source Organization
Robotics Lab
Content Studio
Infrastructure Operator
Education System
Community
Personal Mission
Custom Organization
```

---

# 3. The User Experience

The user should not need to configure hundreds of things.

The user enters:

```text id="c3w7bf"
Create an autonomous AI software company.
Operate it for five years.
Build useful products.
Keep improving them.
Handle engineering, research, security, marketing and support.
Recover from failures automatically.
Ask me only for decisions that require my authority.
```

System responds operationally:

```text id="9nw9x4"
Mission created
Organization planned
16 permanent Bots created
4 departments created
7 projects created
Execution policy configured
Monitoring enabled
Recovery enabled
Continuous operation enabled
```

Then the organization starts.

---

# 4. Phase 1 — Goal Interpretation

The Mission Engine analyzes:

```text id="qkmxyf"
WHAT?
WHY?
WHO?
WHEN?
HOW LONG?
SUCCESS?
CONSTRAINTS?
RISKS?
RESOURCES?
AUTONOMY?
```

It determines:

```text id="r6m9e8"
goal_type
mission_duration
complexity
risk_level
required_capabilities
required_workforce
required_systems
```

---

# 5. Phase 2 — Mission Creation

Create:

```text id="mkc0zj"
Mission
├── Purpose
├── Vision
├── Constraints
├── Success Criteria
├── Strategic Goals
├── Policies
├── Autonomy Level
├── Budget
├── Time Horizon
└── Owner
```

Example:

```text id="p7d9qe"
Mission:
Build and operate a sustainable AI software company.
Duration:
5 years
Mode:
Persistent
Autonomy:
High
```

---

# 6. Phase 3 — Company Operating Model

The system determines what the company actually needs.

Possible structure:

```text id="l0hvh5"
CEO / Executive
│
├── Strategy
├── Product
├── Engineering
├── Research
├── QA
├── Security
├── DevOps / SRE
├── Marketing
├── Sales
├── Customer Support
├── Finance
└── Operations
```

The structure is not hard-coded.

The system can create or remove departments dynamically.

---

# 7. Phase 4 — Workforce Creation

The system creates permanent Hermes profiles.

Example:

```text id="g6rdnq"
CEO
CTO
COO
Product Manager
Research Lead
Backend Engineer
Frontend Engineer
AI Engineer
QA Engineer
Security Engineer
SRE
Marketing Manager
Support Agent
Data Analyst
```

Each Bot should have an isolated profile.

Hermes profiles already provide the basic foundation for independent configuration, memory and state.

---

# 8. Permanent Bot Definition

Every permanent Bot gets:

```text id="x3m4k2"
Identity
Role
Manager
Team
Department
Mission
Goals
Responsibilities
Model
Fallback Model
SOUL / Instructions
Skills
Tools
MCP
Memory
Workspace
Credentials
Permissions
Schedules
Performance Profile
Health Policy
Recovery Policy
```

---

# 9. Responsibility Is More Important Than the Worker

Do not bind responsibility permanently to a process.

Example:

```text id="w8y7gh"
RESPONSIBILITY
Maintain authentication service

Primary:
Backend-01

Backup:
Backend-02

Recovery:
SRE-01
```

If Backend-01 disappears:

```text id="v25qrd"
responsibility survives
```

and another worker takes over.

This is one of the most important features for multi-year continuity.

---

# 10. Permanent Bot Lifecycle

Use:

```text id="0u5r2w"
CREATED
CONFIGURING
STARTING
READY
ACTIVE
BUSY
IDLE
SLEEPING
DEGRADED
UNRESPONSIVE
FAILED
RECOVERING
REPLACED
SUSPENDED
ARCHIVED
```

---

# 11. Bot Runtime

Hermes remains the worker execution layer.

Your adapter should be able to:

```text id="g8p9wq"
create profile
start
stop
restart
message
inspect
run task
read logs
read status
manage routines
```

---

# 12. Important Hermes Integration Rule

Do not assume:

```text id="m0yz0u"
Permanent Bot
=
permanently-running process
```

Hermes currently manages warm Bot backends and can reap idle processes while retaining the underlying persistent profile.

Your architecture should therefore distinguish:

```text id="wbbln1"
Logical Bot
```

from:

```text id="6y0w4t"
Runtime Process
```

That allows:

```text id="p8gt2c"
Bot exists forever
but
Bot runtime wakes only when needed
```

---

# 13. Phase 5 — Company Goals

The CEO/strategy layer creates:

```text id="2hjx9t"
Strategic Goals
```

Example:

```text id="8j5o9a"
Goal 1:
Launch Product A

Goal 2:
Reach target reliability

Goal 3:
Build customer base

Goal 4:
Improve unit economics
```

---

# 14. Objectives

Each strategic goal becomes measurable objectives.

```text id="4h6k32"
Goal
 ↓
Objective
 ↓
Project
 ↓
Task
```

---

# 15. Projects

Example:

```text id="d6b8r0"
Product A
├── Architecture
├── Development
├── Security
├── Testing
├── Deployment
└── Growth
```

---

# 16. Task Generation

Projects generate finite work.

Every task has:

```text id="6e6qg1"
Task ID
Mission
Objective
Project
Description
Owner
Assigned Agent
Backup Agent
Dependencies
Priority
Deadline
Lease
Heartbeat
Workspace
Acceptance Criteria
Verification
```

---

# 17. Kanban

Use:

```text id="tz2qt1"
BACKLOG
PLANNED
READY
ASSIGNED
RUNNING
BLOCKED
REVIEW
TESTING
DONE
```

Additional autonomous states:

```text id="h6s5f7"
STALLED
FAILED
RECOVERING
WAITING
ESCALATED
```

Hermes already provides durable Kanban, dependencies, worker processes and task events, so use it as a lower-level work substrate where appropriate.

---

# 18. Phase 6 — Automatic Task Assignment

The Task Router finds the best worker using:

```text id="nqdr5a"
skill match
specialization
availability
workload
health
performance
permissions
model capability
deadline
cost
```

---

# 19. Permanent Agent vs Sub-Agent Decision

The router asks:

```text id="u8v7v5"
Can an existing Bot do this?
```

If yes:

```text id="ozw4ib"
delegate to Bot
```

Otherwise:

```text id="t8kqpo"
Can a temporary specialist solve it?
```

If yes:

```text id="dfq5ue"
spawn Sub-Agent
```

For a highly parallel workload:

```text id="9r1j3n"
spawn Swarm
```

---

# 20. Phase 7 — Swarm Decision

For complex workloads:

```text id="m7r6af"
Single Agent?
Team?
Swarm?
Council?
Hybrid?
```

The system evaluates:

```text id="pvw6qx"
task independence
complexity
deadline
uncertainty
cost
available resources
```

---

# 21. Example Swarm

Product research:

```text id="qbz8je"
Research Manager
│
├── Market Researcher
├── Customer Researcher
├── Competitor Researcher
├── Technology Researcher
├── Pricing Researcher
└── Risk Researcher
```

Then:

```text id="0fr6zz"
results
 ↓
Council
 ↓
synthesis
 ↓
verification
```

---

# 22. Phase 8 — Deliberation

Major decisions can use:

```text id="8gk6h0"
LLM Council
Debate
Expert Panel
Critic
Red Team
Judge
```

Example:

```text id="l41y1c"
Architecture Decision
      ↓
Architect
Security
Performance
Cost
Operations
      ↓
Council
      ↓
Judge
      ↓
Decision
```

---

# 23. Execution Graph

Represent all work as a durable graph.

```text id="nz5ty4"
Mission
│
├── Objective A
│   ├── Task A1
│   ├── Task A2
│   └── Task A3
│
└── Objective B
    ├── Task B1
    └── Task B2
```

This graph becomes the persistent source of work state.

---

# 24. Dependency Management

Example:

```text id="u32ttc"
Architecture
      ↓
Backend
      ↓
Integration
      ↓
Testing
      ↓
Release
```

Only executable nodes become READY.

---

# 25. Phase 9 — Continuous Execution

The organization continuously asks:

```text id="nxhwk2"
What is currently running?
What is blocked?
What is failing?
What can run next?
What new work exists?
What is highest value?
```

---

# 26. Agent Heartbeat

Every active Bot reports:

```text id="7ffjji"
Agent ID
Current task
Current state
Progress
Last action
Last tool call
Timestamp
```

---

# 27. Agent Liveness

Monitor:

```text id="5eyqp0"
Process
Heartbeat
Session
Model
Tools
MCP
Workspace
CPU
RAM
GPU
Network
```

---

# 28. Task Liveness

Monitor:

```text id="3mp8uz"
assigned?
started?
heartbeat?
progress?
lease?
deadline?
```

This specifically catches:

> “The task was assigned, but the agent isn't actually running.”

---

# 29. Task Lease

When assigned:

```text id="4h7ay2"
Task
 ↓
Lease
 ↓
Agent
```

The worker must renew the lease.

When the lease expires:

```text id="py69fv"
TASK SUSPECTED STALLED
```

---

# 30. Stall Detection

Detect:

```text id="0m9y7a"
assigned but never started
running but no progress
heartbeat missing
process gone
tool loop
model stuck
deadline risk
```

---

# 31. Behavioral Monitoring

An agent can be alive but useless.

Detect:

```text id="t7t2fg"
repeated same tool calls
circular reasoning
search loops
no artifact changes
no state changes
excessive latency
unexpected behavior
```

---

# 32. Phase 10 — Autonomous Recovery

When something breaks:

```text id="2bphzq"
DETECT
 ↓
CLASSIFY
 ↓
RETRY
 ↓
RESTART
 ↓
DIAGNOSE
 ↓
REPAIR
 ↓
VERIFY
 ↓
RESUME
```

If unsuccessful:

```text id="cf1b8j"
REPLACE
 ↓
RESTORE CHECKPOINT
 ↓
VERIFY
 ↓
RESUME
```

---

# 33. Recovery Is a Separate System

Do not rely on the failed Bot to repair itself.

Use:

```text id="upv6de"
Runtime Watchdog
        ↓
Recovery Supervisor
        ↓
Recovery Agent / Recovery Swarm
        ↓
Failed Worker
```

---

# 34. Failed Agent Example

```text id="2g8dz0"
Backend-01
 ↓
heartbeat missing
 ↓
lease expires
 ↓
incident created
 ↓
SRE investigates
 ↓
model configuration problem found
 ↓
repair
 ↓
restart
 ↓
health check
 ↓
resume task
```

---

# 35. Failed Repair Example

```text id="ukqf7a"
restart
 ↓
fails again
 ↓
repair attempt
 ↓
fails
 ↓
find replacement
 ↓
Backend-02
 ↓
restore checkpoint
 ↓
continue
```

---

# 36. Agent Replacement

Replacement selection uses:

```text id="rpsb03"
same capability
health
availability
performance
permissions
resource capacity
```

---

# 37. Parent Failure

Suppose:

```text id="m61jxs"
Engineering Manager
 ↓
20 Sub-Agents
```

Manager crashes.

Do NOT destroy children.

Instead:

```text id="3i4xbs"
Supervisor detects manager failure
 ↓
children remain persisted
 ↓
new manager created
 ↓
children reattached
```

---

# 38. Child Failure

```text id="0rsgxq"
Sub-Agent A fails
 ↓
checkpoint preserved
 ↓
Sub-Agent B created
 ↓
checkpoint restored
 ↓
continue
```

---

# 39. Coordinator Failure

For swarms:

```text id="sz8l9g"
Swarm Coordinator fails
 ↓
persisted swarm state loaded
 ↓
new coordinator
 ↓
workers reattached
```

---

# 40. Long-Running Execution

A five-year mission must never depend on one context window.

Use:

```text id="9e4pyy"
Session 1
 ↓
Checkpoint
 ↓
Context reset
 ↓
Session 2
 ↓
Checkpoint
 ↓
Session 3
```

Anthropic's long-running agent work explicitly uses incremental progress across sessions plus persistent artifacts to avoid depending on one context window.

---

# 41. Context Lifecycle

Context states:

```text id="4fk6j3"
ACTIVE
NEAR_LIMIT
COMPACTING
RESETTING
RESTORING
ACTIVE
```

---

# 42. Context Compaction

Preserve:

```text id="k5f7co"
Goal
Current Plan
Completed Work
Open Issues
Decisions
Artifacts
Evidence
Next Action
```

Not every old message.

---

# 43. Durable State

Store separately:

```text id="kd9n11"
Execution State
Memory
Artifacts
Events
Checkpoints
```

---

# 44. External Job System

Long-running programs should be separated from the agent loop.

Example:

```text id="l2b98w"
Agent
 ↓
Submit Build Job
 ↓
job_id
 ↓
Background Runner
 ↓
logs/status/artifacts
 ↓
Agent receives result
```

This is the right model for:

```text id="d1y8xj"
Builds
Large tests
Video rendering
Training
Simulation
Data processing
Benchmarks
```

OpenAI currently documents durable integrations for long-running agent workflows, and OpenHands has similarly moved toward separating agent reasoning from long-running execution environments/jobs.

---

# 45. Background Agent Execution

Parent can launch:

```text id="4d0j8r"
research agent
```

and continue doing something else.

API should return:

```text id="6k1q4r"
subagent_id
```

not block indefinitely.

---

# 46. Phase 11 — Continuous Work Discovery

This is what makes the company operate after its initial backlog is empty.

The Work Discovery Engine continuously examines:

```text id="f2a5b8"
repositories
issues
customer feedback
analytics
logs
security alerts
dependencies
market information
research
infrastructure
scheduled events
agent observations
```

---

# 47. Work Discovery Categories

Discover:

```text id="0s74j1"
BUG
SECURITY
MAINTENANCE
OPTIMIZATION
RESEARCH
FEATURE
DOCUMENTATION
TECHNICAL_DEBT
COST_REDUCTION
CUSTOMER_NEED
RISK
OPPORTUNITY
NEW_PROJECT
```

---

# 48. Autonomous Work Prioritization

Score:

```text id="r1x5av"
mission_alignment
impact
urgency
risk
value
dependency
cost
effort
confidence
```

Then:

```text id="q3c1hv"
do now
queue
monitor
ignore
```

---

# 49. No Meaningful Work

The organization must not invent pointless work.

```text id="fl49sl"
No useful work
 ↓
agents sleep
 ↓
monitor events
 ↓
wake when needed
```

---

# 50. Phase 12 — KPI Engine

The company defines measurable targets.

Example:

```text id="8na4ze"
Revenue
Customer Growth
Availability
Bug Rate
Security Score
Deployment Frequency
Infrastructure Cost
Product Quality
```

The system continuously compares:

```text id="8nq7av"
TARGET
vs
CURRENT
vs
TREND
```

---

# 51. KPI → Autonomous Work

If:

```text id="yqx1qa"
availability falls
```

system creates:

```text id="fj3yv6"
incident investigation
```

If:

```text id="oyq12m"
customer retention falls
```

system creates:

```text id="egbqkh"
research
product analysis
customer analysis
```

---

# 52. Phase 13 — Continuous Strategy

Every strategic period:

```text id="p3fg6m"
Measure
 ↓
Analyze
 ↓
Compare to mission
 ↓
Re-evaluate strategy
 ↓
Update objectives
```

---

# 53. Goal Drift Prevention

The system continually checks:

```text id="q5k6i5"
Does this work still serve the mission?
```

If not:

```text id="sk1l83"
cancel
reprioritize
replan
```

---

# 54. Phase 14 — Dynamic Organization

The organization can adapt.

Example:

```text id="w4s5t1"
Security workload increases
 ↓
Create Security Team
 ↓
Create Security Manager
 ↓
Create Security Bots
```

Later:

```text id="w85g3m"
Security workload decreases
 ↓
sleep unused workers
```

---

# 55. Dynamic Agent Creation

If no specialist exists:

```text id="mn4cqa"
Task
 ↓
Capability search
 ↓
No suitable agent
 ↓
Create Sub-Agent
```

If recurring:

```text id="r10ez5"
Sub-Agent
 ↓
Recurring workload
 ↓
Promote to permanent Hermes Bot
```

---

# 56. Phase 15 — Agent Performance

Track:

```text id="5xwz6r"
tasks completed
quality
success
failure
recovery
average duration
resource usage
verification
```

---

# 57. Intelligent Routing

Future tasks use historical performance.

Example:

```text id="klyu3m"
Backend A:
97% success

Backend B:
90%

Backend C:
95%
```

System prefers A unless unavailable or overloaded.

---

# 58. Phase 16 — Continuous Evaluation

Every major agent capability should be tested.

Evaluate:

```text id="7d08bz"
planning
coding
research
tool usage
memory
reasoning
recovery
security
```

---

# 59. Agent Regression Testing

If you change:

```text id="4m0gx8"
model
SOUL
skill
workflow
tool
harness
```

run benchmark tasks before rolling out globally.

OpenAI's current tracing and evaluation infrastructure is a useful reference for making agent execution observable and testable rather than treating it as an opaque chat.

---

# 60. Phase 17 — Model Router

Each Bot should be able to have:

```text id="kif5w3"
Primary Model
Fallback Model
Local Model
Specialist Model
```

The company can route different jobs to different models.

---

# 61. Model Health

Monitor:

```text id="s3xj7h"
availability
latency
errors
rate limits
quality
cost
```

---

# 62. Model Failover

```text id="l6t4gr"
Model A
 ↓ outage
Model B
 ↓
continue
```

---

# 63. Phase 18 — Resource Management

Track:

```text id="o4xglh"
CPU
RAM
GPU
VRAM
Disk
Network
API Quota
Browser Sessions
```

---

# 64. Adaptive Concurrency

If machine is overloaded:

```text id="whbr8a"
reduce workers
sleep idle Bots
queue low-priority work
```

If resources are available:

```text id="x5c74x"
increase concurrency
```

---

# 65. Phase 19 — Cost Governance

Each organization can have:

```text id="79qavk"
token budget
API budget
compute budget
time budget
```

---

# 66. Economic Decision Making

Before optional work:

```text id="p2z8g8"
Expected Value
vs
Expected Cost
```

Only perform high-value work.

---

# 67. Phase 20 — Security

Every Bot gets scoped:

```text id="h4s4x6"
filesystem
network
credentials
tools
MCP
repositories
```

---

# 68. Least Privilege

Example:

```text id="l8i4j2"
Marketing Bot
→ marketing workspace only

Production SRE
→ production systems

Research Bot
→ internet read
```

---

# 69. Action Risk Levels

```text id="82g2yd"
READ
WRITE
EXTERNAL
DESTRUCTIVE
IRREVERSIBLE
```

---

# 70. Policy Engine

Example:

```text id="8okn9k"
Read Git:
automatic

Create branch:
automatic

Merge PR:
manager

Deploy production:
approval/policy

Delete production data:
prohibited
```

---

# 71. Human Escalation

Escalate only when:

```text id="ez2b2r"
high risk
low confidence
policy boundary
irreversible action
unrecoverable failure
strategic decision
```

---

# 72. Phase 21 — Verification

The worker should never be the sole judge.

Use:

```text id="59v1j2"
Worker
 ↓
Verifier
 ↓
Evidence
 ↓
Result
```

---

# 73. Different Verification Modes

Code:

```text id="2zgp1u"
build
tests
security
integration
```

Research:

```text id="jcd12g"
sources
citations
fact checking
```

Strategy:

```text id="5qn5wt"
simulation
critic
council
```

---

# 74. Automatic Redo

If verification fails:

```text id="6q8sgr"
FAIL
 ↓
diagnose
 ↓
fix
 ↓
verify again
```

---

# 75. Phase 22 — Incident System

Anything abnormal becomes an incident.

```text id="qr4d0r"
INC-202

Agent:
Backend-01

Problem:
Heartbeat missing

Severity:
High

Task:
PAY-182
```

---

# 76. Incident Lifecycle

```text id="2v2lct"
DETECTED
CLASSIFIED
INVESTIGATING
RECOVERING
VERIFYING
RESOLVED
PREVENTED
```

---

# 77. Incident Correlation

If 20 Bots fail simultaneously:

```text id="v9gxqv"
20 incidents
```

should potentially become:

```text id="s4ygj2"
1 provider outage
```

---

# 78. Root-Cause Analysis

After every important incident:

```text id="v9m2k2"
What failed?
Why?
Why wasn't it prevented?
How can recurrence be reduced?
```

---

# 79. Preventive Automation

Example:

```text id="ld8b3x"
Repeated model timeout
 ↓
Circuit breaker
 ↓
automatic provider failover
```

---

# 80. Phase 23 — Circuit Breakers

Apply to:

```text id="k50qax"
Models
MCP
APIs
Tools
Agents
External Services
```

---

# 81. Phase 24 — Chaos Testing

The company should periodically test its own recovery.

Simulate:

```text id="ut6f2s"
agent crash
manager crash
coordinator crash
network outage
model outage
database failure
tool failure
disk pressure
```

Then verify recovery.

---

# 82. Phase 25 — Backups

Back up:

```text id="7g2k9v"
organizations
missions
Bots
tasks
events
memory
checkpoints
policies
workflows
```

---

# 83. Backup Verification

Do not merely create backups.

Periodically:

```text id="lryon3"
restore
 ↓
verify
```

---

# 84. Phase 26 — Windows Always-On Runtime

The Windows application should have:

```text id="jz4b0l"
Desktop UI
+
Background Organization Service
+
Watchdog
```

The GUI is not the company.

---

# 85. Windows Startup

After Windows starts:

```text id="v2dc2w"
Watchdog
 ↓
Organization Runtime
 ↓
Restore State
 ↓
Check Agents
 ↓
Check Tasks
 ↓
Check Network
 ↓
Resume
```

---

# 86. Application Crash

```text id="n1s4k5"
Application crashes
 ↓
Watchdog
 ↓
restart runtime
 ↓
restore state
 ↓
continue
```

---

# 87. Network Failure

```text id="x7o1v2"
Network down
 ↓
network tasks WAIT
 ↓
local tasks CONTINUE
 ↓
reconnect
 ↓
resume
```

---

# 88. Machine Restart During Task

Example:

```text id="7bh4yd"
12:00
Agent running deployment analysis

12:05
Windows restarts

12:07
Runtime restores state

12:08
Checkpoint restored

12:09
Agent resumes
```

No manual restart of the mission.

---

# 89. Phase 27 — Observability

Every important operation should produce:

```text id="4hz9rj"
LOG
METRIC
TRACE
EVENT
```

OpenAI's current Agents SDK tracing is a useful model here: it records workflows, agents, turns, LLM generations, tool calls, guardrails and handoffs, including long-running workers.

---

# 90. Distributed Trace

One user goal can become:

```text id="swypg8"
Goal
 ↓
CEO
 ↓
CTO
 ↓
Task
 ↓
Swarm
 ↓
Sub-Agent
 ↓
Tool
 ↓
Job
 ↓
Verifier
```

All should belong to one trace.

---

# 91. Organization Event Store

Record:

```text id="zhg4ge"
agent.created
agent.started
agent.failed
task.created
task.assigned
task.started
task.stalled
task.completed
incident.created
recovery.started
recovery.completed
decision.made
```

---

# 92. Event Replay

Allow the user to reconstruct what happened.

---

# 93. Timeline

Example:

```text id="e9q9p3"
09:00 Objective created
09:02 Engineering plan created
09:05 12 tasks dispatched
09:12 Backend Agent failed
09:13 Recovery started
09:16 Replacement assigned
09:25 Task resumed
09:31 Verification passed
```

---

# 94. Phase 28 — Human Command Center

Main UI:

```text id="6m4mzi"
Dashboard
Organization
Agents
Teams
Bots
Sub-Agents
Swarms
Projects
Kanban
Tasks
Mission
Monitoring
Incidents
Recovery
Memory
Workflows
Approvals
Analytics
Policies
```

---

# 95. Live Agent Fleet

Example:

```text id="v7r6a8"
CEO              ● ACTIVE
CTO              ● ACTIVE
Backend-01       ● BUSY
Backend-02       ● IDLE
Security-01      ● SLEEPING
Research-01      ● BUSY
SRE-01           ● RECOVERING
```

---

# 96. Live Task Fleet

```text id="xk0bt5"
TASK-001   RUNNING
TASK-002   REVIEW
TASK-003   BLOCKED
TASK-004   STALLED
TASK-005   DONE
```

---

# 97. Live Swarm View

```text id="s6g1y1"
SWARM-001

Workers:
38

Running:
22

Completed:
13

Failed:
1

Recovering:
2
```

---

# 98. Agent Detail

Display:

```text id="w0yzjf"
Identity
Role
Model
Memory
Skills
Tools
Permissions
Health
Heartbeat
Current Task
Performance
History
Chat
```

---

# 99. Why Is This Agent Idle?

The system should explicitly explain:

```text id="iiuw5x"
Sleeping because:
No assigned work
No scheduled work
No relevant events
No urgent responsibilities
```

---

# 100. Why Is This Task Stalled?

```text id="b1wzll"
No heartbeat for 14 minutes
Agent process unavailable
Recovery initiated
```

---

# 101. Why Was This Agent Replaced?

```text id="l1h3fz"
3 consecutive failures
+
lease expired
+
restart unsuccessful
```

This builds user trust.

---

# 102. Why Was a New Agent Created?

```text id="4tr5cr"
Required capability not available
Existing workers overloaded
Task deadline high
Temporary specialist justified
```

---

# 103. Phase 29 — Communication

Permanent Bots can communicate directly.

Hermes already supports Bot-to-Bot messaging, mentions, group chats and cross-machine Bot communication.

Your organization layer should add:

```text id="2te9gq"
TASK
REQUEST
STATUS
PROGRESS
WARNING
INCIDENT
DECISION
RECOVERY
APPROVAL
ESCALATION
RESULT
```

---

# 104. Department Channels

```text id="6f4vw2"
#executive
#engineering
#security
#research
#marketing
#operations
```

---

# 105. Project Rooms

Each complex project:

```text id="b5v6oo"
Project Room
├── Chat
├── Tasks
├── Agents
├── Git
├── Artifacts
├── Decisions
└── Incidents
```

---

# 106. Incident Rooms

Failed infrastructure can automatically create:

```text id="4c5a7e"
#incident-202
```

with logs, agents, recovery actions and timeline.

---

# 107. Phase 30 — Organizational Memory

Store:

```text id="i0lhwy"
Agent Memory
Team Memory
Project Memory
Company Memory
Mission Memory
Incident Memory
Decision Memory
```

---

# 108. Knowledge Provenance

Every important fact should contain:

```text id="1u0h7m"
source
agent
date
evidence
confidence
project
```

---

# 109. Decision Memory

Example:

```text id="7d7faw"
Decision:
Use PostgreSQL.

Reason:
Scalability + operational requirements.

Date:
2026-09-14

Evidence:
...

Outcome:
...
```

---

# 110. Decision Reopening

Later evidence can reopen the decision.

---

# 111. Phase 31 — Autonomous Improvement

The company can improve:

```text id="fpb4qj"
skills
workflows
agent routing
models
team structure
schedules
recovery policies
```

---

# 112. Safe Self-Improvement

Never:

```text id="cv5gwp"
AI directly changes trusted runtime
```

Instead:

```text id="1luwqr"
PROPOSE
 ↓
SANDBOX
 ↓
TEST
 ↓
EVALUATE
 ↓
CANARY
 ↓
DEPLOY
 ↓
MONITOR
 ↓
ROLLBACK
```

---

# 113. Skill Learning

Repeated workflow:

```text id="b8k3rx"
Observe
 ↓
Extract
 ↓
Create Skill
 ↓
Test
 ↓
Save
```

---

# 114. Skill Repair

Broken skill:

```text id="m3ty9h"
failure
 ↓
diagnose
 ↓
patch
 ↓
evaluate
 ↓
rollback/deploy
```

---

# 115. Workflow Learning

Convert repeated work into reusable workflows.

---

# 116. Harness Evolution

Maintain multiple agent execution strategies:

```text id="g7k6zz"
Harness A
Harness B
Harness C
```

Benchmark them and promote better configurations.

Anthropic's current work explicitly notes that harness assumptions become stale as models improve.

---

# 117. Phase 32 — Strategic Intelligence

CEO can request:

```text id="g55q2u"
Strategy Council
```

with:

```text id="q0ds5r"
Market Expert
Technical Expert
Financial Expert
Security Expert
Operations Expert
Devil's Advocate
```

Then:

```text id="g1b2fb"
Council
 ↓
Decision
 ↓
Verification
 ↓
Execution
```

---

# 118. Phase 33 — Model Council

For uncertain decisions:

```text id="5tr6l0"
Model A
Model B
Model C
Model D
```

independently produce answers.

Then:

```text id="pr0x7r"
Blind Review
 ↓
Critique
 ↓
Evidence
 ↓
Judge
```

---

# 119. Phase 34 — Verification Council

For critical output:

```text id="0q7sxe"
Reviewer A
Reviewer B
Security
Fact Checker
Judge
```

---

# 120. Phase 35 — Recovery Council

For difficult incidents:

```text id="p6w1c2"
Runtime Specialist
SRE
Security
Configuration
Dependency Analyst
```

Then determine recovery.

---

# 121. Phase 36 — Continuous Company Loop

This is the central loop:

```text id="st8p9v"
MISSION
 ↓
STRATEGY
 ↓
OBJECTIVE
 ↓
PROJECT
 ↓
TASK
 ↓
ASSIGN
 ↓
EXECUTE
 ↓
MONITOR
 ↓
VERIFY
 ↓
COMPLETE
 ↓
MEASURE
 ↓
DISCOVER
 ↓
PRIORITIZE
 ↓
NEW OBJECTIVE
 ↓
CONTINUE
```

---

# 122. Failure Loop

At any point:

```text id="07ahuk"
FAILURE
 ↓
INCIDENT
 ↓
RECOVERY
 ↓
VERIFY
 ↓
RESUME
```

---

# 123. Workforce Loop

```text id="63ar63"
WORKLOAD
 ↓
CAPACITY
 ↓
ADD WORKERS
 ↓
PROCESS
 ↓
WORKLOAD DROPS
 ↓
SLEEP/SUSPEND
```

---

# 124. Learning Loop

```text id="6ay8ub"
OUTCOME
 ↓
EVALUATE
 ↓
LEARN
 ↓
IMPROVE
 ↓
TEST
 ↓
DEPLOY
```

---

# 125. Strategic Loop

```text id="wv0s2p"
KPI
 ↓
TREND
 ↓
STRATEGY REVIEW
 ↓
REPLAN
 ↓
EXECUTE
```

---

# 126. Phase 37 — Always-On State Machine

Organization:

```text id="slw5pg"
DRAFT
BOOTSTRAPPING
ACTIVE
DEGRADED
RECOVERING
PAUSED
STOPPED
ARCHIVED
```

Mission:

```text id="v27l1s"
PROPOSED
PLANNING
ACTIVE
REPLANNING
COMPLETED
CANCELLED
```

---

# 127. Phase 38 — Durable Database

At minimum:

```text id="vh1zfg"
organizations
missions
goals
objectives
departments
teams

agents
agent_profiles
agent_runtime
agent_health
agent_heartbeats

responsibilities
agent_backups

projects
tasks
task_dependencies
task_leases
task_attempts
task_checkpoints

subagents
swarms
swarm_workers
swarm_tasks

workflows
jobs

events
messages
channels

incidents
recoveries

memories
knowledge
decisions
artifacts

policies
permissions
approvals

metrics
traces
audit
```

---

# 128. Phase 39 — Runtime Separation

Use this architecture:

```text id="9nh9as"
┌───────────────────────────────┐
│       DESKTOP APPLICATION     │
│             UI                │
└──────────────┬────────────────┘
               │
┌──────────────▼────────────────┐
│      ORGANIZATION CONTROL     │
│           PLANE               │
│                               │
│ Mission                       │
│ Scheduling                    │
│ State                         │
│ Tasks                         │
│ Policies                      │
│ Monitoring                    │
│ Recovery                      │
└──────────────┬────────────────┘
               │
       ┌───────┼────────┐
       ▼       ▼        ▼
    HERMES   SWARM    JOBS
      BOTS
       │       │        │
       └───────┼────────┘
               ▼
        TOOLS / MCP / OS
```

---

# 129. Deterministic vs AI Layer

Keep:

```text id="ddx4ae"
AI:
planning
reasoning
decisions
research
```

separate from:

```text id="qwdk1p"
Deterministic:
state
leases
heartbeats
processes
permissions
scheduling
recovery
```

The LLM should not be the only thing keeping the organization alive.

---

# 130. Failure Independence

If:

```text id="atv3cm"
CEO fails
```

company continues.

If:

```text id="48wvfg"
CTO fails
```

engineering continues.

If:

```text id="7qbeis"
Swarm manager fails
```

swarm can recover.

If:

```text id="ub2e8g"
LLM provider fails
```

fallback models or queued execution continue.

---

# 131. Phase 40 — Autonomous Decision Hierarchy

Every event follows:

```text id="t1ohlo"
OBSERVE
 ↓
CLASSIFY
 ↓
ASSESS
 ↓
PLAN
 ↓
CHECK POLICY
 ↓
ACT
 ↓
VERIFY
 ↓
RECORD
 ↓
LEARN
```

---

# 132. Phase 41 — Company Startup

When user creates company:

```text id="x9y8ad"
1. Parse mission
2. Determine operating model
3. Create company
4. Create policies
5. Create departments
6. Create teams
7. Create permanent Hermes Bots
8. Create responsibilities
9. Configure backups
10. Create initial projects
11. Create objectives
12. Create Kanban
13. Start monitoring
14. Start scheduler
15. Start work discovery
16. Start first tasks
```

---

# 133. Phase 42 — Steady-State Company

Once running:

```text id="t1fp7n"
Monitor
 ↓
Execute
 ↓
Discover
 ↓
Prioritize
 ↓
Delegate
 ↓
Verify
 ↓
Measure
 ↓
Recover
 ↓
Improve
```

This is the company's permanent operating cycle.

---

# 134. Phase 43 — Example First Day

```text id="v5a3ji"
09:00
Company created

09:02
Org structure generated

09:05
Permanent Bots initialized

09:10
Product strategy generated

09:15
Engineering tasks created

09:20
Coding swarm started

09:25
Security review started

09:30
QA swarm started

09:45
First feature complete

10:00
Verification passed

10:05
Next feature discovered
```

The user doesn't manually create every task.

---

# 135. Phase 44 — Example Failure

```text id="2f9r5g"
14:05
Backend-02 assigned TASK-431

14:08
No heartbeat

14:09
Lease expired

14:09
Incident created

14:10
SRE Agent assigned

14:12
Root cause:
MCP connection failure

14:13
Reconnect attempted

14:14
Health test passed

14:15
TASK-431 resumed
```

---

# 136. Phase 45 — Difficult Failure

```text id="h1m3s8"
Agent crash
 ↓
restart
 ↓
crash again
 ↓
repair
 ↓
repair fails
 ↓
replacement worker
 ↓
checkpoint restored
 ↓
verification
 ↓
continue
```

---

# 137. Phase 46 — Strategic Change

User says:

> “Focus more heavily on the AI developer-tool market.”

System:

```text id="9dm5rk"
Update Mission Strategy
 ↓
Impact Analysis
 ↓
Replan Objectives
 ↓
Reprioritize Projects
 ↓
Cancel obsolete work
 ↓
Create research
 ↓
Adjust teams
 ↓
Continue
```

---

# 138. Phase 47 — Company Has No Immediate Work

Do not fabricate work.

```text id="w12fcp"
All current objectives healthy
No urgent incidents
No high-value opportunities
```

Then:

```text id="2v2bbr"
workers sleep
monitoring remains active
scheduled work remains active
event listeners remain active
```

---

# 139. Phase 48 — Five-Year Operation

The organization should survive:

```text id="z4z6ql"
context resets
agent restarts
model changes
skill updates
tool changes
network outages
computer restarts
Windows updates
```

through:

```text id="pclms7"
durable state
checkpoints
versioning
backup
watchdog
recovery
```

---

# 140. Phase 49 — Five-Year Knowledge Evolution

Over years:

```text id="0xe6hn"
Experience
 ↓
Memory
 ↓
Knowledge
 ↓
Skills
 ↓
Better workflows
 ↓
Better routing
 ↓
Better performance
```

---

# 141. Phase 50 — Five-Year Organizational Evolution

The organization may evolve from:

```text id="s4z6xp"
10 agents
```

to:

```text id="jll4yr"
50 agents
```

or back down to:

```text id="i8tp1o"
15 highly effective agents
```

based on workload.

---

# 142. Phase 51 — Human Relationship

The human should not act as:

```text id="uw9hzg"
Task dispatcher
```

Instead:

```text id="1nyxw2"
Owner
Strategy authority
Policy authority
Approver
```

The organization handles normal operation.

---

# 143. Phase 52 — Human Dashboard

The human asks:

```text id="vjfxpl"
How is the company?
```

and gets:

```text id="t0jfmh"
Company Health: 94%

Agents:
42 active
8 sleeping
1 recovering

Projects:
7 active

Tasks:
83 running
17 blocked

Incidents:
0 critical
2 warnings

Mission Progress:
61%

Human attention:
2 approvals
```

---

# 144. Phase 53 — Daily Executive Report

```text id="xhwxg8"
What happened?
What was completed?
What failed?
What was recovered?
What changed?
What risks exist?
What decisions were made?
What needs me?
What's next?
```

---

# 145. Phase 54 — Emergency Mode

If a major failure occurs:

```text id="s74w7d"
NORMAL
 ↓
CRITICAL INCIDENT
 ↓
EMERGENCY MODE
```

Prioritize:

```text id="ch7n9l"
Safety
Security
Recovery
Continuity
```

---

# 146. Phase 55 — Disaster Recovery

If the organization loses its runtime:

```text id="l9v6z8"
Watchdog
 ↓
Restore database
 ↓
Restore event state
 ↓
Restore mission
 ↓
Restore Bots
 ↓
Restore tasks
 ↓
Restore checkpoints
 ↓
Verify
 ↓
Resume
```

Temporal is a strong reference for this class of durable execution because its stated guarantee is that workflows can resume after crashes/network/infrastructure failures, including very long waits.

---

# 147. Phase 56 — External Machine Execution

Eventually:

```text id="4s9r5a"
Company
├── Windows Worker
├── Linux Worker
├── Cloud Worker
└── Home Server
```

All execute under one logical organization.

---

# 148. Phase 57 — Agent Mobility

Task:

```text id="4oj5le"
running on Windows
```

can move to:

```text id="ng7m6r"
cloud worker
```

using checkpoint/state.

---

# 149. Phase 58 — External Agent Federation

The organization can eventually delegate to external A2A-compatible agents.

```text id="cbg9d9"
Internal Bot
 ↔
External Research Agent
```

---

# 150. Phase 59 — Company-to-Company AI Network

Future:

```text id="1qcz1t"
Company A
 ↔
Company B
 ↔
Research Organization
```

with authentication, authorization and contracts.

---

# 151. Phase 60 — Complete Autonomous Company Lifecycle

```text id="m8m1xd"
USER GOAL
   ↓
MISSION
   ↓
COMPANY DESIGN
   ↓
ORGANIZATION
   ↓
PERMANENT BOTS
   ↓
RESPONSIBILITIES
   ↓
PROJECTS
   ↓
OBJECTIVES
   ↓
TASKS
   ↓
KANBAN
   ↓
DISPATCH
   ↓
HERMES EXECUTION
   ↓
SUB-AGENTS
   ↓
SWARMS
   ↓
LLM COUNCILS
   ↓
VERIFICATION
   ↓
DELIVERY
   ↓
MONITORING
   ↓
KPI
   ↓
WORK DISCOVERY
   ↓
NEW OBJECTIVE
   ↓
CONTINUE
```

---

# 152. Failure Lifecycle

```text id="0c7f4n"
FAILURE
 ↓
HEARTBEAT/LEASE DETECTION
 ↓
INCIDENT
 ↓
DIAGNOSIS
 ↓
RETRY
 ↓
RESTART
 ↓
REPAIR
 ↓
VERIFY
 ↓
RESUME
 ↓
REPLACE IF NECESSARY
 ↓
ROOT CAUSE
 ↓
PREVENTION
```

---

# 153. Workforce Lifecycle

```text id="vt18gx"
PERMANENT BOT
       │
       ├── Active
       ├── Sleeping
       └── Reassigned

TEMPORARY SUB-AGENT
       │
       ├── Execute
       ├── Complete
       └── Archive

SWARM
       │
       ├── Expand
       ├── Execute
       ├── Contract
       └── Archive
```

---

# 154. The Most Important Architectural Relationships

```text id="g2g9pl"
BOT
= WHO

RESPONSIBILITY
= WHAT MUST ALWAYS BE COVERED

TASK
= FINITE UNIT OF WORK

SUB-AGENT
= TEMPORARY SPECIALIST

SWARM
= PARALLEL WORKFORCE

WORKFLOW
= HOW WORK IS EXECUTED

MISSION
= WHY THE ORGANIZATION EXISTS

CONTROL PLANE
= WHAT KEEPS EVERYTHING ALIVE

MEMORY
= WHAT THE ORGANIZATION KNOWS

EVENT STORE
= WHAT HAPPENED

VERIFIER
= WHETHER IT ACTUALLY WORKED

WORK DISCOVERY
= WHAT SHOULD HAPPEN NEXT
```

---

# 155. Final Company Architecture

```text id="7j7c6f"
                            HUMAN OWNER
                                  │
                                  ▼
                          ┌───────────────┐
                          │    MISSION    │
                          └───────┬───────┘
                                  │
                                  ▼
                       ┌────────────────────┐
                       │ ORGANIZATION BRAIN │
                       └─────────┬──────────┘
                                 │
                     ┌───────────┼───────────┐
                     │           │           │
                  Strategy    Structure     Policy
                     │           │           │
                     └───────────┼───────────┘
                                 │
                         ┌───────▼────────┐
                         │  WORK ENGINE   │
                         └───────┬────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
                 PROJECTS       TASKS       KPIs
                    │            │            │
                    └────────────┼────────────┘
                                 │
                         ┌───────▼─────────┐
                         │ WORKFORCE ROUTER│
                         └───────┬─────────┘
                                 │
               ┌─────────────────┼────────────────┐
               │                 │                │
          PERMANENT BOT      SUB-AGENT         SWARM
               │                 │                │
               └─────────────────┼────────────────┘
                                 │
                          HERMES RUNTIME
                                 │
                       ┌─────────┼─────────┐
                       │         │         │
                     TOOLS      MCP      JOBS
                       │         │         │
                       └─────────┼─────────┘
                                 │
                           EXECUTION
                                 │
                     ┌───────────▼────────────┐
                     │ LIVENESS + MONITORING  │
                     └───────────┬────────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
                 HEALTH        TASK        RESOURCE
                    │            │            │
                    └────────────┼────────────┘
                                 │
                         FAILURE DETECTED?
                                 │
                         ┌───────┴───────┐
                         │               │
                        NO              YES
                         │               │
                         │          RECOVERY
                         │               │
                         │      ┌────────┼────────┐
                         │      │        │        │
                         │    RETRY    REPAIR   REPLACE
                         │      │        │        │
                         │      └────────┼────────┘
                         │               │
                         └────────┬──────┘
                                  ▼
                              VERIFY
                                  │
                                  ▼
                              COMPLETE
                                  │
                                  ▼
                              MEASURE
                                  │
                                  ▼
                         DISCOVER NEW WORK
                                  │
                                  ▼
                              REPLAN
                                  │
                                  └──────────→ CONTINUE
```

# 156. What “Run for Five Years” Actually Means

It should **not** mean:

```text id="2th8tf"
five years of continuous LLM thinking
```

It means:

```text id="o5z5yy"
five years of continuous mission availability
+
persistent organizational state
+
continuous event monitoring
+
scheduled work
+
event-triggered work
+
automatic task creation
+
agent recovery
+
resource management
+
strategic replanning
```

Agents can sleep.

Processes can restart.

Models can change.

Machines can reboot.

The **mission and organization continue**.

---

# 157. Minimum System Required Before Claiming “Autonomous Company”

The company mode should not be considered production-ready until it passes:

```text id="10e8sv"
✓ Permanent Hermes Bots
✓ Independent profiles
✓ Responsibilities
✓ Backup agents
✓ Projects
✓ Kanban
✓ Task dependencies
✓ Sub-Agents
✓ Swarms
✓ LLM Council
✓ Heartbeats
✓ Leases
✓ Task liveness
✓ Agent liveness
✓ Checkpoints
✓ Durable execution
✓ Background jobs
✓ Agent recovery
✓ Agent replacement
✓ Parent failure recovery
✓ Coordinator recovery
✓ Model failover
✓ Network recovery
✓ Windows restart recovery
✓ Verification
✓ Audit
✓ Event store
✓ Work discovery
✓ KPI monitoring
✓ Strategic replanning
✓ Dynamic workforce
✓ Resource management
✓ Permission engine
✓ Human escalation
✓ Backup/restore
```

---

# 158. The Final Product Goal

The final product should be capable of taking:

> **“Run this company autonomously for five years.”**

and internally transforming it into:

```text id="v2x1a8"
CREATE MISSION
      ↓
DESIGN ORGANIZATION
      ↓
CREATE PERMANENT HERMES BOTS
      ↓
ASSIGN RESPONSIBILITIES
      ↓
CREATE PROJECTS
      ↓
GENERATE OBJECTIVES
      ↓
CREATE TASKS
      ↓
DISPATCH
      ↓
USE SINGLE AGENT / SUB-AGENT / SWARM
      ↓
USE COUNCIL WHEN DECISION QUALITY REQUIRES IT
      ↓
EXECUTE
      ↓
MONITOR EVERYTHING
      ↓
DETECT STALLS
      ↓
RECOVER FAILURES
      ↓
REPLACE FAILED WORKERS
      ↓
PRESERVE CHECKPOINTS
      ↓
VERIFY OUTPUT
      ↓
MEASURE BUSINESS/PROJECT KPIs
      ↓
DISCOVER NEW WORK
      ↓
REPLAN
      ↓
DYNAMICALLY EVOLVE ORGANIZATION
      ↓
CONTINUE
```

# 159. The Final Definition of Your Company Mode

> **Company Mode is a persistent autonomous operating mode in which the agent transforms a high-level business goal into a living organization of permanent Hermes Bots, temporary sub-agents and dynamic swarms; assigns responsibilities and finite tasks; continuously discovers and prioritizes new work; executes through durable workflows and background jobs; monitors every worker, task and resource using heartbeats, leases and event telemetry; detects stalls, crashes and behavioral anomalies; autonomously diagnoses, repairs, restarts or replaces failed workers; preserves work through checkpoints and context resets; independently verifies outcomes; continuously evaluates organizational performance; dynamically changes its workforce, models and workflows; and operates across system restarts, outages and changing conditions until the owner changes or stops the mission.**

This is the **flagship capability** your agent should demonstrate. It exercises almost every advanced component you have been designing: Hermes Bot Mode, Sub-Agents, Swarms, LLM Councils, durable execution, memory, Kanban, monitoring, recovery, verification, dynamic workforce management, continuous work discovery and autonomous strategic operation. Hermes gives you the persistent-profile worker primitive; your project supplies the **company operating system around those workers**.