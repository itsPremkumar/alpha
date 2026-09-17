# Universal ASI-Oriented Agent Harness

## 1. Architectural Objective

The system is a **persistent agent operating system**, not a chatbot.

It must support:

```text
General reasoning
+
Long-horizon execution
+
Computer use
+
Browser use
+
Software development
+
Deep research
+
Multimodal creation
+
Multi-agent collaboration
+
Persistent memory
+
Goal management
+
Scheduling
+
Autonomous background operation
+
Self-learning
+
Self-improving skills
+
Self-improving strategy
+
Controlled recursive harness evolution
+
Verification
+
Recovery
+
Security
```

The architecture is designed around five principles:

```text
1. Model-independent
2. Environment-independent
3. Goal-driven
4. Evidence-driven
5. Evolution-driven
```

---

# 2. Master Architecture

```text
                                      HUMAN
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                       Prompt         Event        Schedule
                          │             │             │
                          └─────────────┼─────────────┘
                                        ▼
                             ┌────────────────────┐
                             │    CONTROL PLANE   │
                             │                    │
                             │ Gateway            │
                             │ Identity           │
                             │ Authentication    │
                             │ Session Manager    │
                             │ Scheduler          │
                             │ Event Bus          │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │   EXECUTIVE BRAIN  │
                             │                    │
                             │ Goal Compiler      │
                             │ Intent Resolver    │
                             │ Strategic Reasoner │
                             │ Priority Manager   │
                             │ Resource Manager   │
                             └─────────┬──────────┘
                                       │
             ┌─────────────────────────┼────────────────────────┐
             ▼                         ▼                        ▼
      ┌──────────────┐          ┌──────────────┐         ┌──────────────┐
      │ WORLD MODEL  │          │ MEMORY       │         │ MODEL FABRIC │
      │              │          │              │         │              │
      │ environment  │          │ working      │         │ reasoning    │
      │ entities     │          │ episodic     │         │ coding       │
      │ applications │          │ semantic     │         │ vision       │
      │ processes    │          │ procedural   │         │ local        │
      │ relationships│          │ project      │         │ evaluator    │
      └──────┬───────┘          │ failure      │         │ embedding    │
             │                  │ trajectory   │         └──────┬───────┘
             │                  └──────┬───────┘                │
             └────────────────────────┼────────────────────────┘
                                      ▼
                             ┌────────────────────┐
                             │   PLANNING CORE    │
                             │                    │
                             │ Goal decomposition │
                             │ Hierarchical plan  │
                             │ DAG planning       │
                             │ Search             │
                             │ Scheduling         │
                             │ Replanning         │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │   SUPERVISOR CORE  │
                             │                    │
                             │ Progress           │
                             │ Stagnation         │
                             │ Failure             │
                             │ Cost               │
                             │ Quality            │
                             │ Compute            │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │    AGENT FABRIC    │
                             │                    │
                             │ Research           │
                             │ Coding             │
                             │ Browser            │
                             │ Computer           │
                             │ Data               │
                             │ Science            │
                             │ Design             │
                             │ Security           │
                             │ Testing            │
                             │ Verification       │
                             └─────────┬──────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
        ┌────────────┐          ┌────────────┐           ┌────────────┐
        │ SKILL      │          │ TOOL       │           │ AGENT BUS  │
        │ FABRIC     │          │ FABRIC     │           │            │
        │            │          │            │           │ delegation │
        │ skills     │          │ MCP        │           │ handoffs   │
        │ workflows  │          │ APIs       │           │ task DAG   │
        │ expertise  │          │ CLI        │           │ messaging  │
        └──────┬─────┘          └──────┬─────┘           └─────┬──────┘
               └───────────────────────┼────────────────────────┘
                                       ▼
                             ┌────────────────────┐
                             │  COMPUTER FABRIC   │
                             │                    │
                             │ Files              │
                             │ Shell              │
                             │ Browser            │
                             │ DOM/CDP            │
                             │ Accessibility      │
                             │ GUI                │
                             │ Keyboard/Mouse     │
                             │ Vision             │
                             │ Apps               │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │   GOVERNOR KERNEL  │
                             │                    │
                             │ Risk               │
                             │ Policy             │
                             │ Permissions        │
                             │ Credentials        │
                             │ Injection defense  │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ EXECUTION FABRIC   │
                             │                    │
                             │ Local              │
                             │ Docker             │
                             │ WSL                │
                             │ VM                 │
                             │ microVM             │
                             │ Remote sandbox     │
                             └─────────┬──────────┘
                                       │
                                       ▼
                                      WORLD
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ OBSERVATION ENGINE │
                             │                    │
                             │ tool outputs       │
                             │ filesystem         │
                             │ DOM                │
                             │ screenshots        │
                             │ process state      │
                             │ logs               │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ VERIFICATION       │
                             │                    │
                             │ tests              │
                             │ assertions         │
                             │ visual checks      │
                             │ critics            │
                             │ evidence           │
                             └─────────┬──────────┘
                                       │
                                ┌──────┴──────┐
                                ▼             ▼
                              PASS          FAIL
                                │             │
                                │             ▼
                                │       RECOVERY ENGINE
                                │             │
                                │       retry/repair
                                │       reobserve
                                │       replan
                                │       rollback
                                │       delegate
                                │
                                └─────────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ STATE / ARTIFACTS  │
                             │                    │
                             │ checkpoints        │
                             │ project state      │
                             │ files              │
                             │ evidence           │
                             │ trajectories       │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ EVOLUTION FACTORY  │
                             │                    │
                             │ failure mining     │
                             │ hypotheses         │
                             │ candidate agents   │
                             │ benchmark          │
                             │ red team           │
                             │ regression         │
                             │ shadow             │
                             │ canary             │
                             │ promotion          │
                             └─────────┬──────────┘
                                       │
                                       └──────► BETTER SYSTEM
```

---

# 3. The Six Nested Intelligence Loops

## 3.1 Action Loop

```text
OBSERVE
→ REASON
→ AUTHORIZE
→ ACT
→ OBSERVE
→ VERIFY
```

This is the minimum unit of computer autonomy.

## 3.2 Task Loop

```text
GOAL
→ PLAN
→ EXECUTE
→ VERIFY
→ COMPLETE
```

## 3.3 Recovery Loop

```text
FAIL
→ DIAGNOSE
→ RECOVER
→ REPLAN
→ CONTINUE
```

## 3.4 Supervisor Loop

```text
MONITOR
→ MEASURE
→ DETECT
→ INTERVENE
→ CONTINUE
```

## 3.5 Learning Loop

```text
EXPERIENCE
→ EXTRACT LESSON
→ STORE
→ REUSE
→ IMPROVE
```

## 3.6 Evolution Loop

```text
TRAJECTORY
→ HYPOTHESIS
→ CANDIDATE
→ EVALUATION
→ SELECTION
→ DEPLOY
→ REPEAT
```

---

# 4. Goal-Driven Core

Every incoming request becomes a structured goal.

```json
{
  "goal": "...",
  "intent": {},
  "constraints": {},
  "resources": {},
  "deadline": null,
  "risk": "medium",
  "quality_bar": {},
  "deliverables": [],
  "success_conditions": [],
  "verification_requirements": []
}
```

The system should track:

```text
desired outcome
current state
gap
available resources
constraints
dependencies
success criteria
verification criteria
```

This makes the system **goal driven rather than chat driven**.

---

# 5. Goal Hierarchy

Large objectives become hierarchical.

```text
Company objective
    ↓
Strategic objective
    ↓
Project
    ↓
Milestone
    ↓
Task
    ↓
Action
```

Example:

```text
Build SaaS
│
├── Research market
├── Design architecture
├── Build backend
│   ├── Auth
│   ├── Database
│   └── API
├── Build frontend
├── Test
├── Deploy
└── Monitor
```

The Executive manages the hierarchy.

---

# 6. Planning Engine

Use multiple planning modes.

```text
Hierarchical Planning
DAG Planning
Search Planning
Reactive Planning
Optimization Planning
Resource Planning
Risk Planning
```

The planner can create several candidates:

```text
Plan A
Plan B
Plan C
```

then score:

```text
success probability
cost
duration
risk
dependencies
complexity
```

and choose the highest expected value.

---

# 7. World Model

The world model represents the current environment:

```text
Computer
├── OS
├── applications
├── files
├── processes
├── browser
├── accounts
├── network
└── devices

Projects
├── repository
├── tasks
├── artifacts
├── deployments
└── state
```

The model is continuously updated by observations.

---

# 8. Memory Architecture

Use separate persistent memories.

```text
                 MEMORY FABRIC
                      │
       ┌──────────────┼───────────────┐
       ▼              ▼               ▼
   WORKING         EPISODIC        SEMANTIC
       │              │               │
       ▼              ▼               ▼
   current         experiences      facts
       │
       ├──────────────┐
       ▼              ▼
 PROCEDURAL        FAILURE
       │              │
       ▼              ▼
    skills          lessons
       │
       └──────────────┬───────────────
                      ▼
                 PROJECT MEMORY
                      │
                      ▼
                  WORLD MEMORY
                      │
                      ▼
                TRAJECTORY MEMORY
```

Use semantic retrieval only when needed.

Keep important project state in durable structured artifacts.

---

# 9. Memory Consolidation

A background memory process should periodically do:

```text
raw interactions
→ remove duplicates
→ identify durable facts
→ identify lessons
→ identify procedures
→ identify failures
→ update project state
→ compress obsolete history
```

This prevents indefinite context growth.

---

# 10. Context Engine

The model should receive **relevant context**, not all stored information.

```text
Memory
+
Project State
+
Current Goal
+
Relevant Skills
+
Relevant Tools
+
Recent Events
+
Environment
+
Previous Failures
        │
        ▼
Context Compiler
        │
        ▼
Model Context
```

Support:

```text
retrieval
compaction
compression
summarization
spillover files
context reset
session handoff
```

OpenClaw and Deep Agents both make context, filesystem state and persistence explicit architectural concerns rather than treating them as ordinary prompt text.

---

# 11. Session Architecture

A session must be resumable.

```text
Session
├── goal
├── state
├── transcript
├── context
├── tasks
├── agents
├── artifacts
├── checkpoints
├── evidence
├── failures
└── trajectory
```

Session operations:

```text
start
pause
resume
fork
clone
compact
reset-context
rollback
replay
```

---

# 12. Agent Core

Keep the core minimal.

```text
receive
 ↓
load state
 ↓
assemble context
 ↓
select harness/model
 ↓
infer
 ↓
tool/delegate?
 ↓
authorize
 ↓
execute
 ↓
observe
 ↓
verify
 ↓
persist
 ↓
continue/complete
```

OpenClaw's reusable Agent Core is a strong reference for this separation, with agent loop, messages, compaction, prompts, skills and session-storage contracts isolated from the broader Gateway/runtime.

---

# 13. Harness Registry

Your platform should support several execution harnesses:

```text
Native Agent Harness
Coding Harness
Research Harness
Computer Harness
Browser Harness
Science Harness
Multimodal Harness
Local Model Harness
Remote Model Harness
Experimental Harness
```

Runtime selection:

```text
task
+
model
+
tools
+
environment
+
risk
+
budget
→
best harness
```

This lets the architecture evolve without changing the overall system.

---

# 14. Model Fabric

Do not couple the harness to one model vendor.

```text
MODEL FABRIC

Reasoning models
Coding models
Vision models
Embedding models
Fast models
Local models
Evaluator models
```

Each provider implements:

```text
generate
stream
tool_call
vision
structured_output
embedding
```

The router handles capability matching.

---

# 15. Adaptive Compute

The system should dynamically choose how much intelligence to spend.

```text
C0
deterministic

C1
fast reasoning

C2
normal reasoning

C3
deep reasoning

C4
multi-agent

C5
deep research + verification

C6
evolution/search
```

When stuck:

```text
stagnation
→ increase reasoning
→ add specialists
→ change model
→ change strategy
```

---

# 16. Agent Factory

Agents should be created dynamically.

```text
Agent =
Model
+
Role
+
Goal
+
Skills
+
Tools
+
Memory scope
+
Permissions
+
Environment
+
Evaluation criteria
```

Example:

```text
Researcher
Coder
Browser operator
Security auditor
Data scientist
Test engineer
Designer
Verifier
```

---

# 17. Agent Teams

Use hierarchical orchestration.

```text
EXECUTIVE
   │
   ├── Research Manager
   │      ├── Search Worker
   │      ├── Source Worker
   │      └── Fact Checker
   │
   ├── Coding Manager
   │      ├── Backend
   │      ├── Frontend
   │      └── QA
   │
   └── Security Manager
          ├── Static Analysis
          ├── Red Team
          └── Security Review
```

DeerFlow, Hermes, Deep Agents and OpenHands all provide useful patterns for subagent isolation and orchestration.

---

# 18. Subagent Context Quarantine

Do not return every worker action to the parent.

```text
Parent
 ↓
Worker
 ├── many model calls
 ├── many tools
 ├── many files
 └── many observations
       ↓
structured result
       ↓
Parent
```

Return:

```text
result
evidence
artifacts
important decisions
recommendation
remaining blockers
```

This dramatically improves scalability.

---

# 19. Skill Fabric

Skills are reusable procedural intelligence.

```text
skills/
├── browser
├── coding
├── Git
├── research
├── database
├── spreadsheets
├── documents
├── presentation
├── image
├── video
├── CAD
├── security
├── DevOps
└── scientific
```

Each skill:

```text
SKILL.md
manifest
tools
resources
examples
permissions
tests
benchmarks
version
```

Load them progressively.

Hermes is especially valuable here because it explicitly treats skills as procedural memory, and its tool/runtime supports skill management.

---

# 20. Skill Learning

A task can generate a new reusable skill.

```text
Task
 ↓
Successful procedure
 ↓
Extract steps
 ↓
Create skill candidate
 ↓
Test
 ↓
Benchmark
 ↓
Promote
```

This creates lifelong capability growth.

Voyager provides an important conceptual reference here: automatic curriculum plus an ever-growing skill library. 

---

# 21. Tool Fabric

Every tool has a common contract.

```json
{
  "name": "tool",
  "input_schema": {},
  "output_schema": {},
  "risk": "medium",
  "permissions": [],
  "timeout": 300,
  "verification": {}
}
```

Supported tool classes:

```text
MCP
REST
GraphQL
CLI
Python
Rust
OS API
Browser
Database
Files
Custom Plugin
```

---

# 22. Universal Computer Fabric

The computer layer must support multiple interaction levels.

```text
API
 ↓
CLI
 ↓
Application structured interface
 ↓
DOM
 ↓
Accessibility
 ↓
CDP
 ↓
GUI
 ↓
Vision
```

This gives the model a universal interface to the computer.

---

# 23. Browser Engine

Support:

```text
managed browser
user browser
remote browser
isolated browser
```

Capabilities:

```text
navigation
DOM extraction
click
type
scroll
upload
download
screenshot
JavaScript
CDP
network
console
```

---

# 24. Desktop Engine

Support:

```text
Windows
Linux
macOS
```

with:

```text
Accessibility APIs
UI Automation
Win32
PowerShell
shell
keyboard
mouse
clipboard
screenshots
processes
```

Vision remains the universal fallback.

---

# 25. Device Fabric

Treat computers/devices as execution nodes.

```text
Gateway
│
├── Windows PC
├── Linux server
├── macOS
├── Android
├── iOS
└── cloud workstation
```

Each node advertises:

```text
capabilities
permissions
resources
health
location class
availability
```

---

# 26. Governor Kernel

This is the security boundary.

```text
Agent
 ↓
Action Intent
 ↓
Risk Classification
 ↓
Policy
 ↓
Capability Token
 ↓
Tool
 ↓
Sandbox
 ↓
World
```

Permission classes:

```text
P0 read
P1 reversible local
P2 workspace modification
P3 external communication
P4 sensitive account operation
P5 irreversible/high-impact
```

---

# 27. Credential Broker

Never place global credentials in model context.

```text
Agent
 ↓
Credential Request
 ↓
Policy Engine
 ↓
Scoped Temporary Credential
 ↓
Tool
```

Credentials should be:

```text
short-lived
scoped
audited
revocable
```

---

# 28. Prompt Injection Defense

Treat external information as untrusted:

```text
Web
PDF
Email
README
GitHub issue
MCP result
Database result
Shell output
```

Authority hierarchy:

```text
SYSTEM POLICY
>
USER INTENT
>
TRUSTED STATE
>
AGENT PLAN
>
TOOL DATA
>
EXTERNAL CONTENT
```

External content can inform reasoning but cannot redefine the governing objective.

OpenHands explicitly includes security validation in its agent loop, and Deep Agents exposes request-time permissions/security middleware.

---

# 29. Execution Fabric

The same interface should execute locally or in isolation.

```text
Execution Fabric
├── Local
├── Docker
├── Podman
├── WSL
├── VM
├── microVM
└── Remote sandbox
```

Capabilities:

```text
filesystem isolation
process limits
network limits
CPU limits
memory limits
time limits
credential isolation
```

---

# 30. Checkpoint System

Before significant mutation:

```text
checkpoint
 ↓
change
 ↓
test
 ↓
verify
```

For code:

```text
branch
+
worktree
+
commit/checkpoint
```

For files:

```text
snapshot
+
version
```

For agent state:

```text
session checkpoint
+
world-state checkpoint
```

Hermes' checkpoint/worktree approach is a useful reference for autonomous coding safety.

---

# 31. Observation Engine

Every executed action produces an observation.

```text
Tool result
Filesystem change
Browser state
DOM
Screenshot
Process state
Test result
Log
External event
```

Normalize all of them into:

```json
{
  "observation_id": "...",
  "type": "...",
  "source": "...",
  "timestamp": "...",
  "state_change": {},
  "evidence": []
}
```

---

# 32. Verification Engine

Never trust:

```text
"Done"
```

without evidence.

Use multiple independent validators:

```text
Functional tests
Integration tests
E2E
Visual checks
Static analysis
Source verification
Schema validation
Security checks
Independent critic
```

---

# 33. Evidence Graph

Every important result should have provenance.

```text
Goal
 ↓
Task
 ↓
Action
 ↓
Observation
 ↓
Artifact
 ↓
Test
 ↓
Evidence
 ↓
Result
```

This gives:

```text
traceability
reproducibility
confidence
auditing
debugging
```

---

# 34. Recovery Engine

Failures are objects, not just exceptions.

```text
Failure
├── task
├── action
├── model
├── environment
├── error
├── root cause
├── recovery
├── outcome
└── prevention
```

Recovery hierarchy:

```text
retry
→ reobserve
→ alternate tool
→ alternate method
→ alternate model
→ repair
→ rollback
→ delegate
→ replan
→ escalate
```

---

# 35. Stagnation Detector

Detect:

```text
same state repeatedly
same tool repeatedly
same failure repeatedly
no progress
plan divergence
state oscillation
```

Example:

```text
5 ineffective iterations
↓
STAGNATION
↓
Supervisor
↓
strategy change
```

This is one of the important ideas to take from AVO-style supervisory architectures.

---

# 36. Supervisor

The Supervisor continuously monitors:

```text
progress
quality
risk
cost
latency
failure
stagnation
resource usage
agent health
tool health
```

It can decide:

```text
continue
replan
delegate
spawn specialist
change model
increase compute
reduce compute
rollback
pause
escalate
terminate
```

---

# 37. Goal Persistence

Goals should survive application restarts.

```text
Goal Registry
├── active
├── waiting
├── blocked
├── recurring
├── scheduled
├── completed
└── abandoned
```

Each goal has:

```text
priority
deadline
dependencies
owner
status
progress
verification
```

---

# 38. Always-On Agent

Support:

```text
user-triggered
event-triggered
schedule-triggered
heartbeat-triggered
monitor-triggered
```

So:

```text
User
Email
Timer
Website
GitHub
Monitoring
Webhook
Device
       ↓
     Agent OS
```

can all wake the system.

---

# 39. Business / Company Mode

The same architecture can become a virtual organization.

```text
                      EXECUTIVE
                          │
       ┌──────────────────┼─────────────────┐
       ▼                  ▼                 ▼
      CTO             Research            COO
       │                  │                 │
   ┌───┼───┐          ┌───┼───┐         ┌───┼───┐
   ▼   ▼   ▼          ▼   ▼   ▼         ▼   ▼   ▼
 Dev  QA DevOps       R1  R2 Analyst    Ops Finance Sales
```

Each organization unit gets:

```text
goal
budget
skills
memory
KPIs
tools
permissions
```

---

# 40. Research Mode

For deep research:

```text
Research Director
      │
 ┌────┼───────┐
 ▼    ▼       ▼
Search  Source  Counter
       verifier evidence
 │       │       │
 └───────┼───────┘
         ▼
    Evidence Graph
         ▼
      Synthesis
         ▼
    Fact checking
         ▼
       Report
```

---

# 41. Coding Mode

```text
Requirement
 ↓
Repository analysis
 ↓
Architecture
 ↓
Task DAG
 ↓
Parallel worktrees
 ↓
Implementation
 ↓
Build
 ↓
Tests
 ↓
Browser/E2E
 ↓
Visual check
 ↓
Security
 ↓
Review
 ↓
Final verification
```

OpenHands, Hermes and current long-running coding-agent work provide strong reference patterns for this design.

---

# 42. Computer Automation Mode

Example:

> “Download invoices, rename them, extract values, update spreadsheet, send summary.”

Architecture:

```text
Goal
 ↓
Browser Agent
 ↓
Download
 ↓
Filesystem
 ↓
Document extraction
 ↓
Spreadsheet agent
 ↓
Validation
 ↓
Email tool
 ↓
Evidence
```

No single tool needs to handle the whole task.

---

# 43. Creative Mode

For:

```text
image
video
audio
presentation
3D
CAD
UI
```

use:

```text
specification
 ↓
generation
 ↓
render
 ↓
vision inspection
 ↓
critic
 ↓
revision
 ↓
render
 ↓
verification
```

---

# 44. Software Engineering Mode

Use separate responsibilities:

```text
Architect
Developer
Reviewer
Tester
Security
Release
```

with an integrator.

```text
Architect
   ↓
Task DAG
   ↓
Workers
   ↓
Integrator
   ↓
QA
   ↓
Security
   ↓
Release
```

---

# 45. Scientific Mode

```text
Question
 ↓
Literature search
 ↓
Hypotheses
 ↓
Experiment design
 ↓
Simulation
 ↓
Data analysis
 ↓
Counterexample search
 ↓
Verification
 ↓
Conclusion
```

This allows the same harness to operate as a research system instead of only a task executor.

---

# 46. Monitoring Mode

The agent can watch:

```text
website
server
database
repository
deployment
price
system metrics
scheduled processes
```

Pattern:

```text
Monitor
 ↓
event
 ↓
classify
 ↓
investigate
 ↓
act
 ↓
verify
 ↓
notify
```

---

# 47. Offline Mode

When there is no Internet:

```text
Local model
Local embeddings
Local memory
Local filesystem
Local tools
Local browser
Local benchmark
```

The agent continues operating on local tasks.

Network-dependent tools become unavailable capabilities rather than fatal failures.

---

# 48. Low-Compute Mode

For an ordinary laptop:

```text
small local model
+
deterministic tools
+
aggressive retrieval
+
cached results
+
low concurrency
+
small context
```

Use larger reasoning only when necessary.

---

# 49. High-Compute Mode

On larger hardware:

```text
multiple models
+
parallel agents
+
deep reasoning
+
long context
+
large-scale evaluation
+
population evolution
```

The control plane remains identical.

---

# 50. Recursive Self-Improvement Architecture

This is the main ASI-oriented extension.

```text
                    PRODUCTION SYSTEM
                           │
                      trajectories
                           ▼
                    FAILURE MINER
                           │
                           ▼
               IMPROVEMENT HYPOTHESIS
                           │
                           ▼
                   CANDIDATE BUILDER
                           │
                           ▼
                     CANDIDATE
                           │
              ┌────────────┼─────────────┐
              ▼            ▼             ▼
           Benchmark    Red Team      Regression
              │            │             │
              └────────────┼─────────────┘
                           ▼
                        SHADOW
                           │
                           ▼
                         CANARY
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                  PROMOTE       REJECT
```

The architecture supports evolution of:

```text
prompts
skills
memory policies
retrieval
tool routing
model routing
planning
verification
recovery
agent topology
harness code
```

---

# 51. Evolution Population

Do not maintain only one candidate.

```text
                BASELINE
              /    |     \
             A     B      C
            / \    |     / \
           D   E   F     G   H
```

Each candidate carries:

```text
parent
mutation
fitness
benchmark results
trajectory
security results
```

A-Evolve, HyperAgents, CORAL and DGM are particularly useful references for this population/search-oriented architecture.

---

# 52. Continual In-Task Adaptation

A separate loop can adapt during a long-running task:

```text
live task
 ↓
trajectory window
 ↓
refiner
 ↓
modify:
   prompt
   subagents
   skills
   memory
 ↓
continue same task
```

Continual Harness is an especially direct reference for this idea.

---

# 53. Source-Level Evolution

At the highest maturity level:

```text
Harness v1
 ↓
analyze failures
 ↓
generate code patch
 ↓
build candidate harness
 ↓
run benchmark
 ↓
security
 ↓
regression
 ↓
candidate v2
```

This is the DGM/MOSS-style direction.

Keep this evolution isolated from the production system.

---

# 54. Evolution Safety

The system must not do:

```text
agent edits itself
→ immediately becomes production
```

Instead:

```text
proposal
→ isolated candidate
→ benchmark
→ held-out benchmark
→ security
→ regression
→ shadow
→ canary
→ promotion
```

The need for held-out evaluation is particularly important because a self-improving system can optimize toward the visible evaluation environment instead of genuine improvement.

---

# 55. Self-Improvement Levels

Use a staged hierarchy:

```text
L1
memory improvement

L2
skill improvement

L3
planning improvement

L4
routing improvement

L5
harness improvement

L6
meta-harness improvement
```

Not everything should have equal autonomy.

---

# 56. What should be automatically modifiable

```text
HIGH AUTONOMY
Skills
Memory organization
Retrieval strategies
Prompt variants
Tool routing

MEDIUM AUTONOMY
Planning policies
Agent topology
Verification strategies

LOW AUTONOMY
Runtime modifications
Harness code
Security policy

VERY LOW / HARD GATE
Security kernel
Credential infrastructure
Host-level privileges
Irreversible deployment controls
```

---

# 57. Trajectory Store

Every completed task should optionally create:

```text
goal
context
model
plan
actions
observations
tool results
failures
recovery
verification
result
```

Trajectories become the raw material for:

```text
evaluation
debugging
training
skill creation
failure analysis
evolution
```

Hermes explicitly exposes trajectory generation for training-data workflows, making this an especially useful design pattern to borrow. 

---

# 58. Evaluation Factory

Maintain benchmark suites:

```text
coding
research
browser
desktop
memory
long-horizon
multi-agent
multimodal
recovery
tool use
security
evolution
```

Every candidate version must be tested against:

```text
current benchmark
regression suite
held-out benchmark
adversarial benchmark
```

---

# 59. Fitness Function

Do not optimize only task success.

```text
fitness =
success
+ quality
+ reliability
+ verification
+ recovery
+ efficiency
+ generalization
- cost
- latency
- failures
- security violations
- regressions
```

---

# 60. Population / Island Evolution

For highly ambitious evolution:

```text
Island A
coding strategies

Island B
planning strategies

Island C
memory strategies

Island D
tool strategies

Island E
verification strategies
```

Periodically:

```text
migration
→ exchange strong candidates
→ compare
→ continue exploration
```

CORAL's 2026 multi-island architecture is a particularly relevant reference for this design.

---

# 61. Meta-Harness

The ultimate outer layer is:

```text
META-HARNESS
     │
     ├── observes production
     ├── identifies weaknesses
     ├── proposes improvements
     ├── launches experiments
     ├── evaluates candidates
     ├── tracks lineage
     └── promotes better harness
```

So:

```text
Agent
 ↓
Harness
 ↓
Meta-Harness
 ↓
Evolution Factory
```

---

# 62. Recursive Intelligence Structure

The final recursive hierarchy is:

```text
MODEL
  ↓
AGENT
  ↓
AGENT TEAM
  ↓
SUPERVISOR
  ↓
EXECUTIVE
  ↓
META-AGENT
  ↓
META-HARNESS
  ↓
EVOLUTION FACTORY
```

The lower layers solve the task.

The upper layers improve how the lower layers solve tasks.

---

# 63. Event-Driven Architecture

All important operations become events:

```text
GoalCreated
PlanCreated
TaskCreated
AgentSpawned
ToolCalled
ToolCompleted
ObservationReceived
StateChanged
FailureDetected
RecoveryStarted
CheckpointCreated
MemoryUpdated
SkillLoaded
VerificationStarted
VerificationPassed
VerificationFailed
ArtifactCreated
SessionCompacted
SessionResumed
CandidateCreated
CandidateEvaluated
CandidatePromoted
```

This makes the system replayable.

---

# 64. Message Bus

Use event-driven communication:

```text
                    EVENT BUS
                        │
       ┌────────────────┼─────────────────┐
       ▼                ▼                 ▼
    Executive        Agents            Tools
       │                │                 │
       └────────────────┼─────────────────┘
                        ▼
                     Storage
```

No component should need direct hard-coded coupling to every other component.

---

# 65. Project Artifact Graph

Treat every output as an artifact:

```text
Code
Document
Spreadsheet
Presentation
Image
Video
Audio
Dataset
Report
CAD
Deployment
Configuration
```

Each artifact has:

```text
ID
version
parent
creator
timestamp
dependencies
tests
evidence
```

This lets agents reason over complex projects.

---

# 66. Human Control Model

Three primary modes:

### Copilot

```text
Human ↔ Agent
```

### Autonomous

```text
Human
 ↓
Goal
 ↓
Agent
 ↓
Result
```

### Evolution

```text
Agent
 ↓
Improve system
 ↓
Evaluate
 ↓
Promote candidate
```

Per-task autonomy levels should remain configurable.

---

# 67. All Major Scenarios

The architecture should handle:

```text
GENERAL
├── question answering
├── planning
├── decision support
├── knowledge work

COMPUTER
├── browse web
├── desktop automation
├── files
├── applications
├── email
├── spreadsheets
├── presentations

SOFTWARE
├── coding
├── debugging
├── testing
├── refactoring
├── deployment
├── DevOps
├── security

RESEARCH
├── web research
├── literature
├── data analysis
├── scientific investigation
├── source verification

CREATIVE
├── image
├── video
├── audio
├── UI
├── 3D
├── CAD

AUTOMATION
├── cron
├── heartbeat
├── monitoring
├── recurring workflows
├── event-triggered actions

MULTI-AGENT
├── delegation
├── teams
├── hierarchies
├── societies
├── parallel exploration

BUSINESS
├── operations
├── engineering
├── research
├── finance workflows
├── sales workflows
├── support workflows

PERSONAL
├── personal assistant
├── knowledge management
├── scheduling
├── document management

META
├── self-learning
├── skill evolution
├── planning evolution
├── harness evolution
└── recursive improvement
```

---

# 68. Failure Scenarios

The architecture must also explicitly handle:

```text
model unavailable
provider unavailable
tool unavailable
network unavailable
authentication failure
browser crash
application crash
computer reboot
context overflow
memory corruption
bad plan
wrong assumption
tool hallucination
prompt injection
security denial
agent disagreement
stagnation
resource exhaustion
partial completion
external state changed
```

No individual failure should normally terminate the entire goal.

---

# 69. Resume After Crash

The required recovery sequence:

```text
process crash
 ↓
load checkpoint
 ↓
restore session
 ↓
restore world model
 ↓
restore task graph
 ↓
inspect environment
 ↓
verify prior state
 ↓
continue
```

---

# 70. Distributed Architecture

When scaled:

```text
                CONTROL PLANE
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
   Worker Pool    Research Pool   Evolution Pool
       │             │              │
   ┌───┼───┐     ┌───┼───┐      ┌──┼───┐
   ▼   ▼   ▼     ▼   ▼   ▼      ▼  ▼   ▼
   W1  W2  W3    R1  R2  R3     E1 E2  E3
```

Use task leases and capability-aware scheduling.

---

# 71. Resource Manager

Track:

```text
CPU
RAM
GPU
disk
network
model quota
tool quota
time
concurrency
```

Resource allocation becomes part of planning.

---

# 72. Zero-Cost Deployment

The architecture should work with:

```text
Ollama
llama.cpp
vLLM
local embeddings
SearXNG
Playwright
Chromium
PostgreSQL
SQLite
NATS / Redis
Docker / Podman
WSL
local filesystem
```

External APIs remain optional.

This means:

```text
No paid API
→ still operational
```

---

# 73. Recommended Technology Stack

```text
Python
→ intelligence/orchestration

TypeScript
→ desktop/web/browser

Rust
→ security/runtime/sandbox

PostgreSQL
→ durable state

SQLite
→ local sessions

pgvector/local vector store
→ semantic retrieval

NATS / Redis Streams
→ event bus

Playwright + CDP
→ browser

OS Accessibility APIs
→ desktop

Docker/Podman/WSL/VM
→ execution

OpenTelemetry
→ telemetry

MCP
→ tools
```

---

# 74. Final Repository Structure

```text
agent-os/
│
├── apps/
│   ├── desktop/
│   ├── web/
│   └── gateway/
│
├── core/
│   ├── agent_loop/
│   ├── runtime/
│   ├── harness/
│   ├── messages/
│   ├── sessions/
│   ├── events/
│   └── contracts/
│
├── executive/
│   ├── goals/
│   ├── strategy/
│   ├── priorities/
│   └── resources/
│
├── world/
│   ├── model/
│   ├── environment/
│   └── entities/
│
├── planning/
│   ├── hierarchical/
│   ├── dag/
│   ├── search/
│   └── replanning/
│
├── supervisor/
│   ├── progress/
│   ├── stagnation/
│   ├── recovery/
│   └── compute/
│
├── models/
│   ├── providers/
│   ├── router/
│   ├── reasoning/
│   ├── coding/
│   ├── vision/
│   ├── embeddings/
│   └── evaluators/
│
├── memory/
│   ├── working/
│   ├── episodic/
│   ├── semantic/
│   ├── procedural/
│   ├── project/
│   ├── failure/
│   ├── trajectory/
│   └── retrieval/
│
├── agents/
│   ├── research/
│   ├── coding/
│   ├── browser/
│   ├── computer/
│   ├── science/
│   ├── design/
│   ├── security/
│   └── verification/
│
├── skills/
│
├── tools/
│   ├── mcp/
│   ├── api/
│   ├── cli/
│   ├── browser/
│   └── plugins/
│
├── computer/
│   ├── windows/
│   ├── linux/
│   ├── macos/
│   ├── browser/
│   ├── accessibility/
│   └── vision/
│
├── security/
│   ├── governor/
│   ├── risk/
│   ├── credentials/
│   ├── sandbox/
│   └── injection/
│
├── execution/
│   ├── local/
│   ├── docker/
│   ├── wsl/
│   ├── vm/
│   └── remote/
│
├── collaboration/
│   ├── task_bus/
│   ├── delegation/
│   ├── teams/
│   └── a2a/
│
├── verification/
│   ├── tests/
│   ├── critics/
│   ├── evidence/
│   └── replay/
│
├── evaluation/
│   ├── benchmarks/
│   ├── regression/
│   ├── heldout/
│   ├── redteam/
│   └── scoring/
│
├── evolution/
│   ├── failure_mining/
│   ├── hypotheses/
│   ├── candidates/
│   ├── population/
│   ├── lineage/
│   ├── islands/
│   ├── fitness/
│   ├── shadow/
│   ├── canary/
│   └── rollback/
│
└── observability/
    ├── events/
    ├── traces/
    ├── metrics/
    └── dashboards/
```

---

# 75. Final System Topology

```text
                         ┌──────────────────┐
                         │   META-HARNESS   │
                         │ evolves harness  │
                         └────────┬─────────┘
                                  │
                         ┌────────▼─────────┐
                         │ EVOLUTION FACTORY│
                         └────────┬─────────┘
                                  │
                         ┌────────▼─────────┐
                         │    GOVERNOR       │
                         │ safety/control    │
                         └────────┬─────────┘
                                  │
                         ┌────────▼─────────┐
                         │    EXECUTIVE      │
                         │ goals/strategy    │
                         └────────┬─────────┘
                                  │
                 ┌────────────────┼─────────────────┐
                 ▼                ▼                 ▼
             WORLD MODEL        MEMORY           MODELS
                 │                │                 │
                 └────────────────┼─────────────────┘
                                  ▼
                              PLANNER
                                  │
                                  ▼
                             SUPERVISOR
                                  │
                                  ▼
                           AGENT FABRIC
                                  │
                      ┌───────────┼───────────┐
                      ▼           ▼           ▼
                    SKILLS      TOOLS       TEAMS
                      │           │           │
                      └───────────┼───────────┘
                                  ▼
                         COMPUTER FABRIC
                                  │
                                  ▼
                            GOVERNOR
                                  │
                                  ▼
                          EXECUTION FABRIC
                                  │
                                  ▼
                                WORLD
                                  │
                                  ▼
                            OBSERVATION
                                  │
                                  ▼
                            VERIFICATION
                                  │
                                  ▼
                         STATE / TRAJECTORY
                                  │
                                  ▼
                              LEARNING
                                  │
                                  ▼
                            EVOLUTION
                                  │
                                  └────────────► META-HARNESS
```

---

# 76. The Ultimate Design Formula

The system can be understood as:

```text
GENERAL INTELLIGENCE
+
PERSISTENT MEMORY
+
WORLD MODEL
+
GOAL MANAGEMENT
+
LONG-HORIZON PLANNING
+
MULTI-AGENT COORDINATION
+
GENERAL COMPUTER USE
+
SKILL ACQUISITION
+
TOOL ECOSYSTEM
+
SECURE EXECUTION
+
VERIFICATION
+
RECOVERY
+
ADAPTIVE COMPUTE
+
CONTINUOUS OPERATION
+
TRAJECTORY LEARNING
+
EVOLUTIONARY SEARCH
+
CONTROLLED RECURSIVE SELF-IMPROVEMENT
```

The strongest public projects each contribute a different piece:

```text
OpenClaw
→ persistent runtime / sessions / context / skills / harness registry

Hermes
→ procedural memory / profiles / delegation / cron / trajectories

DeerFlow
→ super-agent / sandbox / persistent memory / subagents

Deep Agents
→ planning / filesystem state / subagents / persistence / permissions

OpenHands
→ agent loop / orchestrator / control plane / security validation

Letta
→ persistent identity / editable memory / experiential continuity

Voyager
→ lifelong skill acquisition

Agent Zero
→ full computer embodiment

CAMEL
→ agent societies

Prime/RLM
→ persistent computational context / recursive subagents

A-Evolve
→ generalized agent evolution

HyperAgents
→ self-referential meta-agent evolution

DGM
→ source-level agent evolution

Continual Harness
→ in-task continual adaptation

CORAL
→ multi-agent population/island evolution

MOSS
→ harness-level source modification

AVO
→ long-horizon evolutionary search + supervision

Astra
→ general computer/environment interaction

Fable
→ long-running multi-application autonomous work
```

The result is **not one giant agent**.

It is:

```text
                     BRAIN
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          MEMORY     PLANNER   WORLD
             │         │         │
             └─────────┼─────────┘
                       ▼
                    AGENTS
                       │
                  SKILLS/TOOLS
                       │
                       ▼
                     HANDS
                       │
                       ▼
                   GOVERNOR
                       │
                       ▼
                     WORLD
                       │
                    VERIFY
                       │
                    LEARN
                       │
                   EVOLVE
                       │
                       ▼
                 BETTER BRAIN
```

That is the **final target architecture** I would use for your project.

The most important engineering decision is to keep the **inner Agent Core stable and small**, while making the **Executive, Memory, Planner, Supervisor, Skills, Tools, Computer Fabric, Verification and Evolution Factory independently replaceable**. OpenClaw's current decomposition strongly supports that separation; OpenHands and Deep Agents reinforce the loop/orchestration/environment split; and the current self-improvement work shows why evolution must be backed by durable trajectories, isolation, scoring and held-out evaluation rather than uncontrolled self-editing.

The architecture can start entirely local/free and later scale to multiple GPUs, remote sandboxes, more agents and stronger models without changing its core contracts.