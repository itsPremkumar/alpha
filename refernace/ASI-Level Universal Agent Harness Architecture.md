# ASI-Level Universal Agent Harness Architecture

## 1. Mission

Build a persistent, model-agnostic, multimodal, computer-native autonomous agent system capable of:

- understanding arbitrary user goals
- decomposing goals into hierarchical plans
- researching unknown domains
- using computers, browsers, terminals, files and applications
- creating and modifying software and digital artifacts
- coordinating multiple specialized agents
- maintaining long-term memory and project state
- recovering from failures
- independently verifying its work
- operating continuously for hours, days or longer
- learning reusable skills from experience
- generating and evaluating improvements to itself
- safely deploying better versions of its own harness
- functioning entirely with self-hosted/open-source components when required

The architecture is inspired by the strongest public principles from OpenClaw, Hermes, DeerFlow, Deep Agents, OpenAI Astra, Anthropic Fable/Mythos, NVIDIA AVO and large-model systems such as Kimi, but is intentionally a new synthesis rather than a claimed reproduction of proprietary internal systems.

---

# 2. Master Architecture

```text
                                  HUMAN / WORLD
                                       │
               ┌───────────────────────┼───────────────────────┐
               │                       │                       │
             USER                    EVENT                  SCHEDULE
               │                       │                       │
               └───────────────────────┼───────────────────────┘
                                       ▼
                         ┌──────────────────────────┐
                         │      CONTROL PLANE       │
                         │                          │
                         │ Gateway / API            │
                         │ Identity                 │
                         │ Sessions                 │
                         │ Permissions              │
                         │ Scheduler                │
                         │ Event Bus                │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │     EXECUTIVE BRAIN      │
                         │                          │
                         │ Goal Compiler            │
                         │ Intent Resolver          │
                         │ Strategic Reasoner       │
                         │ Prioritizer              │
                         │ Resource Allocator       │
                         └─────────────┬────────────┘
                                       │
               ┌───────────────────────┼───────────────────────┐
               ▼                       ▼                       ▼
       ┌────────────────┐      ┌────────────────┐      ┌────────────────┐
       │   WORLD MODEL  │      │ MEMORY FABRIC  │      │ MODEL FABRIC   │
       │                │      │                │      │                │
       │ environment    │      │ working       │      │ local models   │
       │ entities       │      │ episodic      │      │ reasoning      │
       │ applications   │      │ semantic      │      │ coding         │
       │ files          │      │ procedural    │      │ vision         │
       │ processes      │      │ project       │      │ embedding      │
       │ relationships  │      │ failure       │      │ evaluator      │
       └───────┬────────┘      └───────┬────────┘      └───────┬────────┘
               │                       │                       │
               └───────────────────────┼───────────────────────┘
                                       ▼
                         ┌──────────────────────────┐
                         │      PLANNING CORE       │
                         │                          │
                         │ Goal decomposition       │
                         │ Hierarchical planning    │
                         │ DAG planning             │
                         │ Search planning          │
                         │ Resource planning        │
                         │ Risk planning            │
                         │ Replanning                │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │     SUPERVISOR CORE      │
                         │                          │
                         │ Progress monitor         │
                         │ Stagnation detector      │
                         │ Failure detector          │
                         │ Strategy switching       │
                         │ Agent management         │
                         │ Compute allocation       │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │       AGENT FABRIC        │
                         │                          │
                         │ Researcher               │
                         │ Coder                    │
                         │ Browser Agent             │
                         │ Computer Agent            │
                         │ Analyst                  │
                         │ Scientist                │
                         │ Designer                 │
                         │ Security Agent           │
                         │ Evaluator                │
                         │ Integrator               │
                         └─────────────┬────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              ┌─────────────┐   ┌─────────────┐   ┌──────────────┐
              │ SKILL FABRIC│   │ TOOL FABRIC │   │ AGENT BUS    │
              │             │   │             │   │              │
              │ skills      │   │ MCP         │   │ delegation   │
              │ workflows   │   │ APIs        │   │ task graph   │
              │ expertise   │   │ CLI         │   │ messaging    │
              │ procedures  │   │ plugins     │   │ handoffs     │
              └──────┬──────┘   └──────┬──────┘   └──────┬───────┘
                     └──────────────────┼─────────────────┘
                                        ▼
                         ┌──────────────────────────┐
                         │    COMPUTER FABRIC       │
                         │                          │
                         │ API                      │
                         │ CLI / shell              │
                         │ Browser DOM               │
                         │ CDP                      │
                         │ Accessibility             │
                         │ Desktop automation       │
                         │ Screenshot / vision      │
                         │ Keyboard / mouse          │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │      GOVERNOR KERNEL      │
                         │                          │
                         │ Policy                   │
                         │ Risk classifier          │
                         │ Permission engine        │
                         │ Prompt-injection defense │
                         │ Credential broker         │
                         │ Network policy           │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │     EXECUTION FABRIC      │
                         │                          │
                         │ Local machine             │
                         │ Containers                │
                         │ WSL                       │
                         │ VM / microVM              │
                         │ Remote sandbox             │
                         │ Remote device              │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                                  REAL WORLD
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │   OBSERVATION ENGINE      │
                         │                          │
                         │ tool results              │
                         │ files                    │
                         │ process state             │
                         │ DOM                      │
                         │ accessibility            │
                         │ screenshots               │
                         │ logs                     │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │    VERIFICATION ENGINE    │
                         │                          │
                         │ tests                    │
                         │ assertions               │
                         │ visual checks             │
                         │ critic                   │
                         │ source verification       │
                         │ artifact validation       │
                         └─────────────┬────────────┘
                                       │
                                ┌──────┴──────┐
                                ▼             ▼
                              PASS          FAIL
                                │             │
                                │             ▼
                                │       RECOVERY ENGINE
                                │             │
                                │      retry / repair
                                │      alternate tool
                                │      replan
                                │      rollback
                                │      delegate
                                │             │
                                └─────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │ STATE / ARTIFACT STORE    │
                         │                          │
                         │ checkpoints              │
                         │ trajectories             │
                         │ artifacts                │
                         │ evidence                 │
                         │ project state            │
                         └─────────────┬────────────┘
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │     EVOLUTION FACTORY     │
                         │                          │
                         │ failure mining            │
                         │ hypothesis generation     │
                         │ candidate harnesses       │
                         │ mutation                  │
                         │ benchmark                 │
                         │ adversarial evaluation    │
                         │ regression                │
                         │ shadow                    │
                         │ canary                    │
                         │ promotion                 │
                         │ rollback                  │
                         └─────────────┬────────────┘
                                       │
                                       └──────────► BETTER SYSTEM
```

---

# 3. The Six Core Loops

The architecture should operate as six nested loops.

## Loop 1 — Action Loop

```text
OBSERVE
→ REASON
→ AUTHORIZE
→ ACT
→ OBSERVE
→ VERIFY
```

## Loop 2 — Task Loop

```text
GOAL
→ PLAN
→ DECOMPOSE
→ EXECUTE
→ VERIFY
→ COMPLETE
```

## Loop 3 — Recovery Loop

```text
FAILURE
→ DIAGNOSE
→ RECOVER
→ REPLAN
→ CONTINUE
```

## Loop 4 — Supervision Loop

```text
MONITOR
→ MEASURE PROGRESS
→ DETECT STAGNATION
→ CHANGE STRATEGY
→ ALLOCATE MORE/LESS COMPUTE
```

## Loop 5 — Learning Loop

```text
EXPERIENCE
→ EXTRACT LESSON
→ UPDATE MEMORY
→ IMPROVE SKILL
→ REUSE
```

## Loop 6 — Evolution Loop

```text
FAILURES / TRAJECTORIES
→ HYPOTHESIS
→ CANDIDATE CHANGE
→ BENCHMARK
→ SECURITY
→ REGRESSION
→ CANARY
→ PROMOTION
```

These six loops are the heart of the system.

---

# 4. Goal Compiler

Every user request becomes a machine-readable objective.

```json
{
  "goal": "...",
  "intent": {},
  "constraints": {},
  "resources": {},
  "deliverables": [],
  "deadline": null,
  "quality_bar": {},
  "risk": "medium",
  "success_conditions": [],
  "verification_requirements": []
}
```

The compiler must determine:

```text
What is being requested?
What counts as success?
What is forbidden?
What resources are available?
How much autonomy is appropriate?
How difficult is the task?
How much compute should be spent?
What evidence is required?
```

---

# 5. Executive Brain

The Executive is the highest-level controller.

It should not directly perform every tool action.

Responsibilities:

```text
goal understanding
strategic planning
priority management
resource allocation
agent allocation
risk decisions
compute allocation
final completion decision
```

The Executive delegates execution.

---

# 6. World Model

Maintain a structured understanding of the current environment.

```text
WORLD
├── User
├── Computer
├── OS
├── Applications
├── Browser
├── Files
├── Repositories
├── Processes
├── Accounts
├── Projects
├── Tasks
├── Artifacts
└── Relationships
```

Example:

```json
{
  "computer": {
    "os": "Windows",
    "browser": "Chromium",
    "network": "online"
  },
  "project": {
    "path": "D:/project",
    "git": true,
    "branch": "feature-x"
  }
}
```

The agent should reason about **state transitions**, not simply text.

---

# 7. Memory Architecture

Use separate memory systems.

```text
Working Memory
    ↓
Current task/context

Episodic Memory
    ↓
What happened before

Semantic Memory
    ↓
Facts and relationships

Procedural Memory
    ↓
How to do things

Failure Memory
    ↓
What went wrong before

Project Memory
    ↓
Long-term project state

World Memory
    ↓
Current environment state

Artifact Memory
    ↓
Files / outputs / evidence

Trajectory Memory
    ↓
Complete action histories
```

Deep Agents explicitly makes long-term memory and skills first-class, while Hermes combines persistent memory, session recall and procedural Skills.

---

# 8. Context Engine

Context must be dynamically constructed.

```text
Raw History
     ↓
Relevance
     ↓
Deduplication
     ↓
Memory Retrieval
     ↓
Project State
     ↓
Skill Selection
     ↓
Tool Selection
     ↓
Context Assembly
     ↓
Model
```

Support:

```text
compaction
eviction
summarization
retrieval
just-in-time loading
large-output spillover
checkpointing
context reset
```

OpenClaw's Agent Core and Deep Agents both treat context management as an architectural subsystem rather than simply increasing model context length.

---

# 9. Persistent Session Architecture

Each task has a durable session.

```text
Session
├── session_id
├── goal
├── state
├── transcript
├── context
├── memory references
├── tasks
├── agents
├── artifacts
├── checkpoints
├── evidence
└── trajectory
```

A session can be:

```text
paused
resumed
compacted
forked
rolled back
replayed
cloned
```

---

# 10. Agent Core

The inner runtime should stay extremely clean.

```text
receive input
    ↓
load state
    ↓
assemble context
    ↓
select runtime
    ↓
model inference
    ↓
tool call?
   ├── no → output
   └── yes
        ↓
     authorize
        ↓
      execute
        ↓
      observe
        ↓
      verify
        ↓
      return to model
```

This mirrors the strongest part of OpenClaw's current reusable `agent-core` architecture.

---

# 11. Harness Registry

Do not bind the entire application to one agent runtime.

```text
Harness Registry
├── Native Harness
├── Coding Harness
├── Research Harness
├── Computer-Use Harness
├── Deep Research Harness
├── Local Model Harness
├── Remote Model Harness
└── Experimental Harness
```

Runtime selection:

```text
task
+
model capability
+
tools
+
environment
+
risk
+
budget
→ best harness
```

OpenClaw explicitly separates model/provider/runtime and uses a harness registry/selection layer.

---

# 12. Model Fabric

Make the intelligence layer replaceable.

```text
                    MODEL FABRIC
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
      LOCAL            REMOTE           SPECIALIST
        │                │                 │
   Ollama/vLLM      optional APIs      vision/coding
   llama.cpp                         embeddings/eval
```

Models should expose capabilities:

```text
reasoning
coding
vision
long_context
tool_use
computer_use
structured_output
speed
cost
```

Then the router can dynamically choose.

Kimi-class large models are best treated as examples of the **model layer**—large context, multimodality and high-capacity inference—not as the entire agent architecture.

---

# 13. Adaptive Compute Engine

The system should not use maximum reasoning all the time.

```text
difficulty
    ↓
estimate
    ↓
compute policy
```

Levels:

```text
C0 deterministic
C1 fast reasoning
C2 normal reasoning
C3 deep reasoning
C4 multi-agent
C5 deep search + verification
C6 evolution/search
```

When the agent struggles:

```text
stagnation
→ more reasoning
→ more context
→ specialist
→ parallel agents
→ alternate model
```

---

# 14. Agent Fabric

Dynamic workers should be instantiated from:

```text
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
ResearchAgent
CodeAgent
BrowserAgent
DataAgent
VisualAgent
SecurityAgent
VerifierAgent
IntegratorAgent
```

Do not keep every specialist running permanently.

Spawn them when useful.

---

# 15. Hierarchical Multi-Agent System

Use three levels:

```text
EXECUTIVE
    │
    ▼
MANAGER
    │
    ▼
WORKER
```

Example:

```text
Executive
   │
   ├── Research Manager
   │      ├── Search Worker
   │      ├── Source Worker
   │      └── Fact Checker
   │
   ├── Coding Manager
   │      ├── Backend Worker
   │      ├── Frontend Worker
   │      └── Test Worker
   │
   └── Security Manager
          ├── Static Analysis
          ├── Threat Model
          └── Red Team
```

DeerFlow, Deep Agents and Hermes all reinforce the usefulness of subagent isolation and hierarchical delegation.

---

# 16. Context Quarantine

A worker should not pollute the parent context with every intermediate action.

```text
Parent
  │
  ▼
Worker
  ├── 50 tool calls
  ├── 100 observations
  ├── research
  └── analysis
       ↓
   structured result
       ↓
Parent
```

Only send:

```text
result
evidence
artifacts
important decisions
next recommendation
```

This is one of the best long-horizon patterns in Deep Agents.

---

# 17. Skills = Procedural Intelligence

Skills should be dynamically installable.

```text
skills/
├── browser
├── coding
├── git
├── research
├── databases
├── spreadsheet
├── presentation
├── PDF
├── image
├── video
├── CAD
├── security
└── deployment
```

Each skill contains:

```text
manifest
instructions
tools
examples
resources
permissions
tests
benchmarks
```

The agent:

```text
discover
→ load
→ execute
→ evaluate
→ improve
→ save
```

Hermes explicitly treats Skills as procedural memory and supports creating/updating skills from experience.

---

# 18. Tool Fabric

All tools should have a common contract.

```json
{
  "name": "...",
  "description": "...",
  "input_schema": {},
  "output_schema": {},
  "risk": "medium",
  "permissions": [],
  "timeout": 300,
  "verification": {}
}
```

Tool sources:

```text
MCP
REST
GraphQL
CLI
Python
Rust
browser
OS APIs
plugins
custom services
```

---

# 19. Universal Computer Fabric

This is essential for your “do anything on the computer” goal.

```text
COMPUTER FABRIC
│
├── Filesystem
├── Terminal
├── Browser
├── DOM
├── CDP
├── Accessibility
├── GUI
├── Keyboard
├── Mouse
├── Clipboard
├── Screenshot
└── Desktop applications
```

Use a hierarchy:

```text
API
 ↓
CLI
 ↓
DOM / structured app interface
 ↓
Accessibility
 ↓
CDP
 ↓
GUI
 ↓
Vision fallback
```

Astra demonstrates the importance of real computer environments for forms, CRM, calendars, software installation, websites, scientific workflows, Power BI and other applications.

---

# 20. Browser Architecture

Separate:

```text
Managed Browser
```

from:

```text
User Browser
```

Managed:

```text
isolated profile
isolated cookies
sandbox
```

User:

```text
existing login
user profile
explicit authorization
```

Your browser abstraction should expose:

```text
navigate
snapshot
click
type
scroll
extract
download
upload
evaluate
console
network
screenshot
```

---

# 21. Desktop Architecture

For Windows:

```text
Computer Agent
├── Windows UI Automation
├── Win32
├── PowerShell
├── Terminal
├── browser/CDP
├── accessibility
└── vision
```

For portability:

```text
Windows adapter
Linux adapter
macOS adapter
Android adapter
iOS adapter
```

---

# 22. Device / Node Fabric

Take the OpenClaw idea further.

```text
                   GATEWAY
                      │
       ┌──────────────┼───────────────┐
       ▼              ▼               ▼
    Windows          Linux          Mobile
       │              │               │
     files          server          camera
     browser        GPU             screen
     apps           shell           sensors
```

A device is simply another execution node with declared capabilities.

---

# 23. Governor / Policy Kernel

The model should never directly own authority.

```text
MODEL
 ↓
ACTION INTENT
 ↓
RISK CLASSIFIER
 ↓
POLICY ENGINE
 ↓
CAPABILITY TOKEN
 ↓
TOOL
 ↓
SANDBOX
```

Permission levels:

```text
P0 read
P1 reversible local
P2 workspace mutation
P3 external communication
P4 sensitive account/action
P5 irreversible/high-impact
```

Anthropic and NVIDIA both emphasize that security needs to sit in the surrounding agent stack, not solely inside the model.

---

# 24. Credential Broker

Never put broad credentials in model context.

Use:

```text
Agent
 ↓
Credential Request
 ↓
Policy
 ↓
Scoped Credential
 ↓
Tool
```

Example:

```text
GitHub
repository=X
scope=read/write
duration=15m
```

not:

```text
GLOBAL_GITHUB_TOKEN
```

---

# 25. Prompt Injection Defense

Treat all external information as data:

```text
web page
PDF
email
README
GitHub issue
database
MCP output
shell output
```

Authority ordering:

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

An external document can never rewrite the agent's governing policy merely because it contains instructions.

---

# 26. Secure Execution

The preferred execution topology:

```text
Agent
 ↓
Policy
 ↓
Sandbox
 ↓
Process
 ↓
Filesystem
 ↓
Network
```

Support:

```text
local dev mode
container mode
WSL
VM
microVM
remote sandbox
```

The same execution API should work across them.

---

# 27. Checkpointing

Before dangerous or large mutations:

```text
checkpoint
→ execute
→ verify
```

For software:

```text
Git branch
+
worktree
+
checkpoint
```

For files:

```text
snapshot
+
version history
```

For agent state:

```text
session checkpoint
+
world-state checkpoint
```

---

# 28. Verification Engine

The model is never the sole judge of success.

Verification should be independent wherever practical:

```text
Implementation
 ↓
Tests
 ↓
Static Analysis
 ↓
Runtime Tests
 ↓
Visual Tests
 ↓
Requirements Check
 ↓
Independent Critic
 ↓
PASS/FAIL
```

For research:

```text
claim
→ source
→ primary source check
→ contradiction search
→ citation audit
```

For GUI:

```text
expected state
→ actual state
→ screenshot/accessibility/DOM
→ verifier
```

For generated media:

```text
render
→ vision
→ compare to target
→ revise
```

Fable's public positioning specifically highlights tests and visual checking of outputs against goals.

---

# 29. Evidence Graph

Every important result should have provenance.

```json
{
  "claim": "...",
  "evidence": [
    "source:123",
    "artifact:456",
    "test:789",
    "screenshot:abc"
  ],
  "confidence": 0.96,
  "verified_by": "verifier-2"
}
```

This transforms:

```text
"Done."
```

into:

```text
"Done, because these observations/tests/evidence establish success."
```

---

# 30. Recovery Engine

Failures are first-class objects.

```text
Failure
├── task
├── action
├── model
├── environment
├── error
├── suspected_cause
├── recovery
├── outcome
└── future_prevention
```

Recovery ladder:

```text
retry
→ re-observe
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

# 31. Stagnation Detector

Track:

```text
progress/time
progress/actions
repeated actions
repeated failures
same tool loop
plan divergence
state oscillation
```

Example:

```text
same state 5 times
→ STAGNATION
→ supervisor
→ strategy change
```

This is essential for long-horizon autonomy and maps directly onto the supervisory idea in AVO.

---

# 32. Supervisor Core

Supervisor watches the entire system.

```text
Supervisor
├── progress
├── quality
├── risk
├── cost
├── latency
├── failures
├── stagnation
├── agent health
├── tool health
└── environment health
```

Possible decisions:

```text
continue
replan
delegate
spawn specialist
change model
increase reasoning
reduce reasoning
rollback
pause
ask human
terminate
```

---

# 33. Event Bus

Every significant operation becomes an event.

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
VerificationStarted
VerificationPassed
VerificationFailed
ArtifactCreated
SkillLoaded
MemoryUpdated
SessionCompacted
SessionResumed
CandidateCreated
CandidateEvaluated
CandidatePromoted
```

This becomes the backbone of:

```text
observability
replay
debugging
training
evaluation
evolution
```

---

# 34. Trajectory Recorder

Record:

```text
goal
context
model
plan
actions
observations
tool outputs
failures
recovery
verification
final result
```

Store trajectories separately from user-visible chat history.

AVO's results demonstrate why preserving long-horizon state and execution feedback matters.

---

# 35. AVO-Style Evolution Factory

This is your biggest addition beyond ordinary OpenClaw/Hermes/DeerFlow.

```text
REAL TASKS
   ↓
TRAJECTORIES
   ↓
FAILURE MINER
   ↓
IMPROVEMENT HYPOTHESIS
   ↓
CANDIDATE GENERATOR
   ↓
ISOLATED BENCHMARK
   ↓
REGRESSION TEST
   ↓
SECURITY TEST
   ↓
RED TEAM
   ↓
SHADOW RUN
   ↓
CANARY
   ↓
PROMOTE / ROLLBACK
```

The system can evolve:

```text
prompts
skills
tool selection
model routing
context strategies
planning policies
verification policies
recovery policies
agent topology
harness code
```

NVIDIA's public AVO research is the clearest current example of turning the agent itself into an autonomous variation mechanism operating over a long search trajectory.

---

# 36. Recursive Self-Improvement

Do not allow unrestricted recursive rewriting.

Use generations.

```text
Generation 0
    ↓
observe weaknesses
    ↓
Generation 1 candidate
    ↓
benchmark
    ↓
Generation 2
    ↓
benchmark
    ↓
...
```

Each generation gets:

```text
parent version
change set
reason
benchmark results
security results
regression results
fitness
```

Maintain lineage:

```text
        G0
       /  \
     G1    G2
    / \     \
   G3  G4    G5
         \
          G6
```

The best candidate becomes the new production baseline only after evaluation.

---

# 37. Evolution Fitness

Use a multi-objective score:

```text
fitness =
  task_success
+ reliability
+ efficiency
+ quality
+ recovery
+ verification
- latency
- compute
- security violations
- regressions
```

Do not optimize only for task completion.

A system that gets the right answer but destroys its environment is not a better agent.

---

# 38. Skill Evolution

Skills should evolve independently of the core.

```text
Skill v1
 ↓
experience
 ↓
failure
 ↓
Skill v2 candidate
 ↓
benchmark
 ↓
Skill v2
```

This allows the system to become increasingly specialized without rewriting the entire agent.

---

# 39. Memory Consolidation

Run a background consolidation process:

```text
raw events
 ↓
episode extraction
 ↓
lessons
 ↓
semantic facts
 ↓
procedures
 ↓
failure patterns
 ↓
memory compression
```

Then:

```text
sleep/dream cycle
```

can inspect:

```text
What did I learn?
What should be remembered?
What knowledge is redundant?
Which skills are missing?
Which failures repeat?
```

---

# 40. Project Brain

Each major project should have its own durable state.

```text
project/
├── goal
├── requirements
├── architecture
├── decisions
├── tasks
├── state
├── artifacts
├── failures
├── checkpoints
├── tests
├── evidence
├── memory
└── evolution
```

The agent can leave for a week and return without reconstructing the project from scratch.

---

# 41. 24/7 Autonomy

Add an operating-system scheduler:

```text
Immediate
Scheduled
Recurring
Background
Monitoring
Maintenance
Research
Evolution
```

Example:

```text
08:00 → daily research
09:00 → project monitoring
12:00 → build/test
18:00 → failure analysis
23:00 → memory consolidation
02:00 → evolution benchmarks
```

Heartbeat events can periodically ask:

```text
Is anything important changing?
Are there failed jobs?
Are there pending goals?
Is a monitored system unhealthy?
Is a scheduled task due?
```

---

# 42. Autonomous Company Mode

Your architecture can eventually model a virtual organization.

```text
                    CEO / EXECUTIVE
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          CTO Agent    Research Lead  Operations
             │            │            │
        ┌────┼────┐    ┌──┼────┐    ┌──┼────┐
        ▼    ▼    ▼    ▼  ▼    ▼    ▼  ▼    ▼
       Dev  QA  DevOps R1 R2  Analyst Ops Finance
```

Each department gets:

```text
goals
budget
skills
tools
memory
permissions
KPIs
```

The Executive coordinates them.

This is where OpenClaw Bot Mode, Hermes profiles, agent teams and DeerFlow-style subagent orchestration become useful building blocks.

---

# 43. Human Interaction Modes

Three modes:

## Copilot

```text
Human ↔ Agent
```

## Autonomous

```text
Human
 ↓
Goal
 ↓
Agent
 ↓
Result
```

## Evolution

```text
Agent
 ↓
Analyze itself
 ↓
Generate candidate
 ↓
Evaluate
 ↓
Improve
```

Switch per task.

---

# 44. Human-in-the-loop

Use approval only when needed:

```text
low risk
→ automatic

medium risk
→ policy-based

high risk
→ confirmation

critical/irreversible
→ mandatory approval
```

The user should not have to approve 50 harmless operations during one task.

---

# 45. Free-First Architecture

Everything should work without paid APIs.

```text
LOCAL FIRST
│
├── Ollama
├── llama.cpp
├── vLLM
├── local embeddings
├── local vector/search
├── SearXNG
├── Playwright
├── Chromium
├── PostgreSQL
├── SQLite
├── Redis/NATS
├── Docker/Podman
└── local filesystem
```

Optional remote providers can be added later.

The architecture should never assume:

```text
paid API = intelligence
```

Instead:

```text
model adapter = replaceable
```

---

# 46. Recommended Core Technologies

```text
Python
→ agent intelligence/orchestration

TypeScript
→ desktop/web/browser layer

Rust
→ security/runtime/sandbox components

PostgreSQL
→ durable state

SQLite
→ local session state

pgvector / local vector engine
→ semantic retrieval

NATS / Redis Streams
→ event bus

Playwright + CDP
→ browser

OS accessibility APIs
→ desktop

Docker/Podman/WSL/VM
→ execution

OpenTelemetry
→ traces

MCP
→ tool ecosystem
```

LangGraph/Deep Agents can be used selectively for durable execution and subagent orchestration, but I would keep **your own core interfaces** above them so your architecture is not permanently locked to one framework. Deep Agents itself uses LangGraph for durable execution, streaming, persistence and human-in-the-loop.

---

# 47. Core Repository Architecture

```text
universal-agent-os/
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
│   ├── state/
│   └── contracts/
│
├── executive/
│   ├── goal_compiler/
│   ├── strategic_reasoner/
│   ├── resource_manager/
│   └── priority_engine/
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
│   ├── vision/
│   ├── coding/
│   ├── embeddings/
│   └── evaluators/
│
├── memory/
│   ├── working/
│   ├── episodic/
│   ├── semantic/
│   ├── procedural/
│   ├── failure/
│   ├── project/
│   ├── world/
│   └── retrieval/
│
├── agents/
│   ├── researcher/
│   ├── coder/
│   ├── browser/
│   ├── computer/
│   ├── analyst/
│   ├── scientist/
│   ├── designer/
│   ├── security/
│   └── verifier/
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
│   ├── policy/
│   ├── risk/
│   ├── credentials/
│   ├── sandbox/
│   └── injection/
│
├── runtime/
│   ├── local/
│   ├── docker/
│   ├── wsl/
│   ├── vm/
│   └── remote/
│
├── collaboration/
│   ├── task_bus/
│   ├── delegation/
│   └── a2a/
│
├── evaluation/
│   ├── tests/
│   ├── benchmarks/
│   ├── critics/
│   ├── replay/
│   └── redteam/
│
├── evolution/
│   ├── trajectories/
│   ├── failures/
│   ├── hypotheses/
│   ├── candidates/
│   ├── lineage/
│   ├── fitness/
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

# 48. Prompt-to-Completion Flow

For a difficult user request:

```text
1. RECEIVE
   ↓
2. IDENTIFY USER + SESSION
   ↓
3. COMPILE GOAL
   ↓
4. LOAD PROJECT / WORLD STATE
   ↓
5. RETRIEVE MEMORY
   ↓
6. ESTIMATE DIFFICULTY
   ↓
7. SELECT MODEL + HARNESS
   ↓
8. BUILD PLAN
   ↓
9. CREATE TASK DAG
   ↓
10. SPAWN SPECIALISTS
   ↓
11. EXECUTE
   ↓
12. OBSERVE
   ↓
13. VERIFY
   ↓
14. UPDATE STATE
   ↓
15. DETECT FAILURE/STAGNATION
   ↓
16. RECOVER/REPLAN
   ↓
17. CHECKPOINT
   ↓
18. FINAL VERIFICATION
   ↓
19. STORE EXPERIENCE
   ↓
20. UPDATE SKILLS/MEMORY
   ↓
21. REPORT RESULT + EVIDENCE
```

That is your universal execution pipeline.

---

# 49. The Meta-Harness

The final layer is the most ambitious.

Instead of only solving tasks:

```text
Agent
```

build:

```text
Meta-Harness
 ↓
observes agent performance
 ↓
identifies bottleneck
 ↓
proposes architecture change
 ↓
creates candidate
 ↓
tests candidate
 ↓
chooses better candidate
```

This produces:

```text
Agent
 ↓
Harness
 ↓
Meta-Harness
 ↓
Evolution Factory
```

The agent is solving problems.

The harness is improving execution.

The meta-harness is improving the harness.

---

# 50. Recursive Improvement Boundary

Use explicit layers:

```text
Layer 0
Task

Layer 1
Agent strategy

Layer 2
Skills/tools

Layer 3
Harness

Layer 4
Meta-harness
```

Permit automatic modification progressively:

```text
Skills
      automatic

Prompts
      automatic + evaluation

Routing
      automatic + benchmark

Planner policies
      automatic + benchmark

Harness code
      isolated candidate + strong evaluation

Security kernel
      never unrestricted self-edit
```

This is much safer and more useful than allowing the model to rewrite everything.

---

# 51. Evaluation Matrix

Every new version should be tested on:

```text
General reasoning
Coding
Long-horizon tasks
Computer use
Browser
Research
Memory
Recovery
Multi-agent
Tool use
Vision
Artifact quality
Security
Efficiency
Cost
Latency
```

Track:

```text
success rate
pass@1
pass@k
recovery rate
verification rate
task length
action efficiency
cost/task
latency/task
failure rate
security violations
regressions
```

---

# 52. Golden Benchmark Suite

Create your own benchmark repository.

```text
benchmarks/
├── coding/
├── research/
├── browsing/
├── computer/
├── documents/
├── spreadsheets/
├── multimodal/
├── long_horizon/
├── multi_agent/
├── memory/
├── recovery/
└── evolution/
```

Every improvement must beat the previous baseline.

---

# 53. Production / Research Separation

Maintain:

```text
production harness
research harness
experimental candidates
```

Never let experimental self-improvement directly replace production.

```text
Production
    │
    ▼
Trajectory
    │
    ▼
Research Candidate
    │
    ▼
Evaluation
    │
    ▼
Shadow
    │
    ▼
Canary
    │
    ▼
Production
```

---

# 54. What each researched system contributes

```text
OpenClaw
→ Agent Core
→ Runtime abstraction
→ Session architecture
→ Context Engine
→ Gateway
→ Skills
→ Nodes
→ channels
→ automation

Hermes
→ procedural Skills
→ persistent memory
→ profiles
→ Bot Mode
→ delegation
→ cron
→ checkpoints
→ trajectories

DeerFlow 2.0
→ super-agent runtime
→ sandbox
→ long-horizon orchestration
→ subagents
→ skills
→ memory
→ message gateway

Deep Agents
→ planning
→ filesystem context
→ subagent context isolation
→ long-term memory
→ permissions
→ durable execution

OpenAI Astra
→ general computer use
→ shell
→ files
→ browser
→ visual interaction
→ persistent environment
→ real-world knowledge work

Fable
→ multi-hour execution
→ multi-application workflows
→ autonomous coding
→ recovery
→ testing
→ visual verification
→ long-running managed agents

Mythos-style architecture
→ stronger capability/risk separation
→ deployment-specific policy
→ restricted environments

NVIDIA AVO
→ persistent evolutionary search
→ supervisor
→ trajectory feedback
→ lineage
→ autonomous variation
→ long-horizon optimization

Kimi-class model architecture
→ large context
→ multimodality
→ high-capacity model layer

Letta-style architecture
→ durable memory
→ persistent agent state
→ memory as part of cognition
```

---

# 55. Final Architecture

The complete conceptual hierarchy becomes:

```text
                         ┌─────────────────────┐
                         │     META-HARNESS     │
                         │ evolves the system   │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │    GOVERNANCE        │
                         │ policy / safety      │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      EXECUTIVE       │
                         │ goals / strategy     │
                         └──────────┬──────────┘
                                    │
             ┌──────────────────────┼─────────────────────┐
             ▼                      ▼                     ▼
        WORLD MODEL              MEMORY                MODELS
             │                      │                     │
             └──────────────────────┼─────────────────────┘
                                    ▼
                               PLANNER
                                    │
                                    ▼
                              SUPERVISOR
                                    │
                                    ▼
                            AGENT FABRIC
                                    │
                     ┌──────────────┼──────────────┐
                     ▼              ▼              ▼
                   SKILLS         TOOLS          AGENTS
                     │              │              │
                     └──────────────┼──────────────┘
                                    ▼
                             COMPUTER FABRIC
                                    │
                                    ▼
                              GOVERNOR KERNEL
                                    │
                                    ▼
                            SECURE EXECUTION
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
                               PERSISTENCE
                                    │
                                    ▼
                               TRAJECTORY
                                    │
                                    ▼
                           EVOLUTION FACTORY
                                    │
                                    └──────► BETTER SYSTEM
```

---

# 56. The Most Important Design Rule

The architecture should never become:

```text
ONE GIANT AGENT
+
1000 TOOLS
+
GIANT PROMPT
```

Instead:

```text
SMALL AGENT CORE
+
EXECUTIVE
+
WORLD MODEL
+
MEMORY
+
PLANNER
+
SUPERVISOR
+
AGENT FABRIC
+
SKILLS
+
TOOLS
+
COMPUTER FABRIC
+
SECURITY
+
VERIFICATION
+
RECOVERY
+
EVOLUTION
```

The **Agent Core** remains stable.

The **Model** can change.

The **Skills** can evolve.

The **Tools** can be replaced.

The **Workers** can be spawned dynamically.

The **Harness** can improve.

The **Meta-Harness** can search for better harnesses.

That is the architecture I would use as the target for your project.