# Autonomous Self-Configuring ASI-Oriented Agent Harness

## 1. Core Design Principle

The entire system should operate under one simple interface:

```text
USER
  │
  │ one goal
  ▼
UNIVERSAL AGENT OS
  │
  ├── understands intent
  ├── determines difficulty
  ├── chooses operating mode
  ├── creates plan
  ├── creates sub-goals
  ├── chooses models
  ├── discovers skills
  ├── selects tools
  ├── creates agents
  ├── chooses swarm topology
  ├── performs research
  ├── executes work
  ├── reviews itself
  ├── verifies evidence
  ├── recovers from failure
  ├── learns
  ├── improves skills
  ├── evaluates improvements
  └── evolves the harness
```

The user should generally only provide:

```text
Goal
```

and optionally:

```text
constraints
```

The rest is automatically configured.

---

# 2. The New Architecture

```text
                                      ┌─────────────────────┐
                                      │        USER         │
                                      │                     │
                                      │  "Build X"          │
                                      │                     │
                                      └──────────┬──────────┘
                                                 │
                                                 ▼
                              ┌─────────────────────────────────┐
                              │     UNIVERSAL INPUT GATEWAY      │
                              │                                 │
                              │ goal parsing                    │
                              │ intent detection                │
                              │ constraint extraction           │
                              │ ambiguity resolution            │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │       EXECUTIVE AUTOPILOT        │
                              │                                 │
                              │ difficulty detection            │
                              │ risk detection                  │
                              │ autonomy selection              │
                              │ compute allocation              │
                              │ strategy selection              │
                              │ mode selection                  │
                              └───────────────┬─────────────────┘
                                              │
                       ┌──────────────────────┼───────────────────────┐
                       ▼                      ▼                       ▼
              ┌────────────────┐     ┌────────────────┐      ┌────────────────┐
              │ GOAL COMPILER  │     │ WORLD MODEL    │      │ MEMORY SYSTEM  │
              │                │     │                │      │                │
              │ objective      │     │ environment    │      │ long-term      │
              │ success       │     │ resources      │      │ episodic       │
              │ constraints   │     │ state          │      │ semantic       │
              │ quality       │     │ dependencies   │      │ procedural     │
              └───────┬────────┘     └───────┬────────┘      │ failure        │
                      │                      │               │ project        │
                      └──────────────────────┼───────────────┘
                                             ▼
                              ┌─────────────────────────────────┐
                              │     AUTONOMOUS STRATEGIST        │
                              │                                 │
                              │ plan?                           │
                              │ deep research?                  │
                              │ coding mode?                    │
                              │ swarm?                          │
                              │ browser?                        │
                              │ loop mode?                      │
                              │ review?                         │
                              │ evolution?                      │
                              │ human approval?                 │
                              │ which models?                   │
                              │ which skills?                   │
                              │ which tools?                    │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │         DYNAMIC MODE ENGINE      │
                              │                                 │
                              │ NORMAL                          │
                              │ PLAN                            │
                              │ DEEP RESEARCH                   │
                              │ CODING                          │
                              │ COMPUTER                        │
                              │ BROWSER                         │
                              │ SWARM                           │
                              │ LOOP                            │
                              │ REVIEW                          │
                              │ VERIFY                          │
                              │ RECOVER                         │
                              │ EVOLVE                          │
                              │ REFLECT                         │
                              │ MONITOR                         │
                              │ AUTONOMOUS                      │
                              │ COMPANY                         │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │       DYNAMIC EXECUTION GRAPH    │
                              │                                 │
                              │ goal                            │
                              │   ├─ subgoal                    │
                              │   ├─ subgoal                    │
                              │   │    ├─ task                   │
                              │   │    └─ task                   │
                              │   └─ subgoal                    │
                              └───────────────┬─────────────────┘
                                              │
               ┌──────────────────────────────┼─────────────────────────────┐
               ▼                              ▼                             ▼
      ┌─────────────────┐            ┌─────────────────┐           ┌─────────────────┐
      │  SKILL ENGINE   │            │  MODEL ENGINE   │           │  TOOL ENGINE    │
      │                 │            │                 │           │                 │
      │ discover        │            │ route           │           │ discover        │
      │ load            │            │ budget          │           │ select          │
      │ compose         │            │ switch          │           │ authorize      │
      │ create          │            │ fallback        │           │ execute         │
      │ improve         │            │ ensemble        │           │ verify          │
      └────────┬────────┘            └────────┬────────┘           └────────┬────────┘
               └─────────────────────────────┼──────────────────────────────┘
                                             ▼
                              ┌─────────────────────────────────┐
                              │         AGENT FACTORY            │
                              │                                 │
                              │ spawn specialist                │
                              │ spawn manager                   │
                              │ spawn critic                    │
                              │ spawn researcher                │
                              │ spawn verifier                  │
                              │ spawn temporary worker           │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │       SWARM ORCHESTRATOR         │
                              │                                 │
                              │ sequential                      │
                              │ parallel                        │
                              │ hierarchical                    │
                              │ mesh                            │
                              │ specialist team                 │
                              │ debate                          │
                              │ tournament                      │
                              │ island evolution                │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │        AGENT EXECUTION            │
                              │                                 │
                              │ OBSERVE → REASON → ACT          │
                              │       → OBSERVE → VERIFY        │
                              └───────────────┬─────────────────┘
                                              │
                         ┌────────────────────┼─────────────────────┐
                         ▼                    ▼                     ▼
                ┌────────────────┐   ┌─────────────────┐   ┌──────────────────┐
                │ RESEARCH FABRIC│   │ COMPUTER FABRIC │   │ CODE EXECUTION   │
                │                │   │                 │   │                  │
                │ search         │   │ browser         │   │ terminal         │
                │ crawl          │   │ GUI             │   │ filesystem       │
                │ extract        │   │ DOM             │   │ git              │
                │ verify         │   │ accessibility   │   │ builds/tests     │
                │ synthesize     │   │ screenshots     │   │ containers       │
                └───────┬────────┘   └────────┬────────┘   └─────────┬────────┘
                        └──────────────────────┼──────────────────────┘
                                               ▼
                              ┌─────────────────────────────────┐
                              │        GOVERNOR / SAFETY KERNEL   │
                              │                                 │
                              │ policy                          │
                              │ permissions                     │
                              │ risk                            │
                              │ credentials                     │
                              │ prompt injection                │
                              │ rate limits                     │
                              │ external actions                │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │       SECURE EXECUTION FABRIC    │
                              │                                 │
                              │ local                           │
                              │ Docker                          │
                              │ WSL                             │
                              │ VM                              │
                              │ microVM                         │
                              │ remote sandbox                  │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                                           REAL WORLD
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │        OBSERVATION ENGINE        │
                              │                                 │
                              │ state                          │
                              │ files                          │
                              │ browser                        │
                              │ screenshots                    │
                              │ logs                           │
                              │ test results                   │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │        REVIEW ENGINE             │
                              │                                 │
                              │ plan review                    │
                              │ code review                    │
                              │ reasoning review               │
                              │ source review                  │
                              │ security review                │
                              │ visual review                  │
                              │ completeness review            │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │       VERIFICATION ENGINE        │
                              │                                 │
                              │ tests                          │
                              │ assertions                     │
                              │ evidence                       │
                              │ source validation              │
                              │ artifact checks                │
                              │ independent critic             │
                              └───────────────┬─────────────────┘
                                              │
                                      ┌───────┴───────┐
                                      ▼               ▼
                                    PASS            FAIL
                                      │               │
                                      │               ▼
                                      │       RECOVERY ENGINE
                                      │               │
                                      │         retry
                                      │         repair
                                      │         replan
                                      │         rollback
                                      │         delegate
                                      │         switch model
                                      │               │
                                      └───────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │        STATE / MEMORY UPDATE     │
                              │                                 │
                              │ results                        │
                              │ lessons                        │
                              │ failures                       │
                              │ skills                         │
                              │ project state                  │
                              │ world state                    │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │      CONTINUOUS LEARNING LOOP     │
                              │                                 │
                              │ reflect                         │
                              │ extract lessons                 │
                              │ improve skills                  │
                              │ update memory                   │
                              │ improve routing                 │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────────────┐
                              │        EVOLUTION FACTORY          │
                              │                                 │
                              │ trajectory mining               │
                              │ failure mining                  │
                              │ hypothesis generation           │
                              │ candidate generation            │
                              │ benchmark                       │
                              │ red-team                        │
                              │ held-out evaluation             │
                              │ shadow                          │
                              │ canary                          │
                              │ promotion / rollback             │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                                      BETTER AGENT SYSTEM
```

# 3. User Interaction Contract

The ideal interface is:

```text
USER:
"Build me an open-source video generation platform."
```

The user does not need to specify:

```text
plan
deep research
coding
subagents
browser
skills
models
tools
review
testing
deployment
```

The system determines them itself.

The user may optionally add constraints:

```text
Use only free/open-source tools.
Windows.
No paid API.
Finish as completely as possible.
```

The agent automatically converts this into an execution policy.

---

# 4. Autonomous Intent Compiler

The first internal subsystem is:

```text
PROMPT
 ↓
INTENT
 ↓
OBJECTIVE
 ↓
CONSTRAINTS
 ↓
QUALITY BAR
 ↓
SUCCESS CONDITIONS
 ↓
RISK
 ↓
COMPUTE BUDGET
 ↓
EXECUTION STRATEGY
```

Example:

```text
"Research and build X"

becomes:

Goal:
Build X

Subgoals:
1. Research
2. Requirements
3. Architecture
4. Implementation
5. Testing
6. Deployment
7. Verification

Modes:
research + coding + browser + review

Agent topology:
research team + coding team + verifier

Completion:
all requirements verified
```

---

# 5. Autonomous Mode Selection

The agent should have a **Mode Router**.

```text
                    USER GOAL
                        │
                        ▼
                  MODE CLASSIFIER
                        │
       ┌────────────────┼────────────────┐
       ▼                ▼                ▼
   simple          complex           extremely
    task             task              complex
       │                │                │
      NORMAL          PLAN             STRATEGIC
                       │                  │
              ┌────────┼───────┐         │
              ▼        ▼       ▼         ▼
           research   coding   browser  swarm
```

But modes are **not mutually exclusive**.

A single goal may dynamically become:

```text
PLAN
→ RESEARCH
→ SWARM
→ CODING
→ COMPUTER
→ REVIEW
→ VERIFY
→ EVOLVE
```

---

# 6. Internal Slash Commands

The system may internally represent operations as virtual commands:

```text
/plan
/replan
/research
/deep-research
/parallel
/swarm
/delegate
/find-skill
/load-skill
/create-skill
/review
/verify
/test
/browser
/computer
/code
/debug
/loop
/monitor
/checkpoint
/rollback
/reflect
/learn
/evolve
/red-team
/benchmark
/compare
/ask
```

These should **not normally require the user to type them**.

The Executive invokes them automatically.

For example:

```text
User
"Investigate this new technology and build a working implementation."

Internal:

/deep-research
/plan
/find-skill
/parallel
/swarm
/code
/test
/review
/verify
```

---

# 7. Dynamic Control Plane

The system should have an internal **Autonomy Controller**:

```text
Autonomy Controller
├── Mode selection
├── Plan selection
├── Model selection
├── Skill selection
├── Tool selection
├── Agent selection
├── Swarm topology
├── Compute allocation
├── Review depth
├── Verification depth
├── Recovery strategy
└── Evolution decision
```

This becomes the brain that decides **which architecture to activate at each moment**.

---

# 8. Dynamic Planning

Plans are not static.

```text
Plan V1
 ↓
execute
 ↓
observation
 ↓
world changed
 ↓
Plan V2
 ↓
execute
 ↓
failure
 ↓
Plan V3
```

The plan is a living object.

```json
{
  "goal": "...",
  "version": 7,
  "status": "active",
  "tasks": [],
  "dependencies": [],
  "risks": [],
  "evidence": [],
  "next_best_action": "..."
}
```

---

# 9. Dynamic Subgoals

The agent can generate subgoals at runtime.

```text
Goal
│
├── SG1
│   ├── Task A
│   └── Task B
│
├── SG2
│   ├── Task C
│   └── Task D
│
└── SG3
```

But it can also create new subgoals while running:

```text
Task C
 ↓
unexpected problem
 ↓
new subgoal
"Investigate dependency failure"
 ↓
resolve
 ↓
return to Task C
```

This is critical for general-purpose autonomy.

---

# 10. Dynamic Skill Discovery

The agent should automatically determine:

```text
What capability do I need?
```

Then:

```text
search skill registry
 ↓
rank skills
 ↓
load best skill
 ↓
compose multiple skills
 ↓
execute
```

Example:

```text
"Create a SaaS dashboard"

automatically discovers:

frontend
database
authentication
testing
browser
Git
deployment
UI/UX
security
```

---

# 11. Skill Composition

Instead of using one skill:

```text
Skill A
```

the agent can compose:

```text
Skill A + Skill B + Skill C
```

Example:

```text
Research
+
Python
+
Data Analysis
+
Visualization
+
Report Generation
```

The resulting workflow becomes a temporary composite skill.

---

# 12. Automatic Skill Creation

When no appropriate skill exists:

```text
unknown task
 ↓
extract procedure
 ↓
generate skill candidate
 ↓
test
 ↓
benchmark
 ↓
install
```

Future execution then improves automatically.

---

# 13. Dynamic Model Routing

The user should never need to say which model to use.

The Model Router sees:

```text
task
difficulty
risk
context
vision need
coding need
speed
available hardware
cost constraints
```

and chooses:

```text
local fast model
local reasoning model
local coding model
local vision model
optional external model
ensemble
critic model
```

The model is another dynamically selected resource.

---

# 14. Model Escalation

```text
cheap model
 ↓
uncertain
 ↓
stronger local model
 ↓
still uncertain
 ↓
multi-agent
 ↓
deep reasoning
 ↓
independent verification
```

This means difficult jobs automatically receive more compute.

---

# 15. Dynamic Swarm

Do not have one fixed swarm topology.

The system chooses:

```text
SEQUENTIAL
PARALLEL
HIERARCHICAL
MESH
DEBATE
TOURNAMENT
SPECIALIST TEAM
ISLAND EVOLUTION
```

based on the problem.

Example:

```text
simple:
1 agent

research:
5 parallel agents

large software project:
manager + 10 specialists

optimization:
population