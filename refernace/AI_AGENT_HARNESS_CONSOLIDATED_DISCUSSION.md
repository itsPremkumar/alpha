# AI Agent Harness — Consolidated Project Discussion & Architecture

**Document purpose:** Consolidated reference for the AI-agent-harness discussions in this project, including the architecture direction, self-improvement/RSI, memory, automation, agent orchestration, research targets, and implementation principles.

**Current project:** `github.com/itsPremkumar/agent-workspace-desktop`

**Base:** ByteDance Agent Workspace / Agent Workspace 2.x

**Date:** 2026-09-16

---

## 1. Project Vision

The project is intended to become a **free/open-source, Windows-first, long-running, self-improving AI agent harness**.

The user should ideally provide the initial goal once. The harness should then:

1. Understand the objective.
2. Build a plan.
3. Research deeply when required.
4. Discover and select skills/tools/MCPs.
5. Create and coordinate subagents.
6. Execute work across local and remote environments.
7. Maintain persistent project and agent memory.
8. Verify results.
9. Detect failures and bottlenecks.
10. Recover automatically.
11. Learn from successful and failed execution.
12. Discover missing capabilities and automation opportunities.
13. Generate candidate improvements to itself.
14. Test those improvements in isolated environments.
15. Compare candidates against a baseline.
16. Promote validated improvements through shadow/canary deployment.
17. Roll back regressions.
18. Continue evolving over time.

The long-term goal is not merely a chatbot or coding assistant.

It is a **general-purpose agent operating system/harness that can improve its own ability to accomplish goals**.

---

# 2. Core Product Concept

The intended system is:

```text
User Goal
   ↓
Executive/Supervisor Agent
   ↓
Planning + Research
   ↓
Agent/Skill/Tool Selection
   ↓
Execution
   ↓
Observation
   ↓
Verification
   ↓
Memory
   ↓
Learning
   ↓
Improvement Detection
   ↓
Self-Development
   ↓
Candidate Evaluation
   ↓
Safe Promotion
   ↓
Improved Harness
   ↓
Next Goal
```

The harness should be capable of both:

- **solving user tasks**, and
- **improving the system that solves those tasks**.

---

# 3. Base Architecture Direction

The project is based on Agent Workspace because it provides useful primitives for long-running agents, including:

- subagents
- skills
- tools
- sandbox execution
- persistent memory
- context management
- orchestration
- long-horizon work

The desired system should preserve those strengths while adding a much stronger meta-layer for:

- agent hierarchy
- agent profiles
- project management
- persistent organizations/teams
- dynamic agent creation
- self-improvement
- self-evolution
- automatic automation discovery
- evaluation
- recovery
- observability
- long-running execution

---

# 4. Major Capability Areas

## 4.1 Executive Supervisor

A top-level supervisor coordinates the entire harness.

Responsibilities:

- understand user objective
- determine whether clarification is necessary
- decompose goals
- create plans
- assign work
- choose models
- choose agents
- choose skills
- choose tools
- choose MCP servers
- monitor progress
- detect blocked work
- trigger recovery
- trigger verification
- determine completion
- initiate self-improvement when useful

The supervisor should not be responsible for every low-level operation.

---

# 5. Agent Profiles

Each agent should have a structured identity/configuration.

Example:

```yaml
agent:
  id: research-agent
  name: Research Specialist

  role:
    - web research
    - evidence collection
    - source comparison

  soul: SOUL.md
  instructions: AGENTS.md

  skills:
    - deep-research
    - source-verification
    - summarization

  memory:
    project: true
    long_term: true
    episodic: true

  models:
    preferred: model-x
    fallback:
      - model-y
      - local-model

  tools:
    - browser
    - filesystem
    - search

  mcp:
    - github
    - filesystem

  permissions:
    filesystem: workspace
    network: approved
    shell: restricted
```

The system should support per-agent:

- soul
- instructions
- memory
- skills
- model
- tool access
- MCP access
- permissions
- project scope
- communication scope

---

# 6. Agent Organization / Company Model

The harness should support a persistent organization-style model.

Example:

```text
Company / Project
│
├── Executive Agent
│
├── Research Group
│   ├── Web Researcher
│   ├── Evidence Analyst
│   └── Fact Checker
│
├── Engineering Group
│   ├── Architect
│   ├── Developer
│   ├── Tester
│   └── Reviewer
│
├── Operations Group
│   ├── Monitor
│   ├── Recovery Agent
│   └── Deployment Agent
│
└── Self-Development Group
    ├── Improvement Detector
    ├── Evolution Planner
    ├── Builder
    ├── Benchmark Agent
    └── Evolution Judge
```

Capabilities should include:

- groups
- hierarchy
- delegation
- group chat
- agent-to-agent messages
- task assignment
- status
- attendance/presence
- running/idle/blocked states
- reassignment
- dynamic agent creation

---

# 7. Project Workspace

Each project should have:

```text
project/
├── tasks/
├── agents/
├── skills/
├── tools/
├── memory/
├── docs/
├── artifacts/
├── experiments/
├── benchmarks/
├── evaluations/
├── logs/
└── versions/
```

Agents should share selected project resources while maintaining appropriate isolation.

---

# 8. Task/Kanban System

Tasks should have lifecycle states:

```text
BACKLOG
   ↓
PLANNED
   ↓
ASSIGNED
   ↓
RUNNING
   ↓
BLOCKED
   ↓
REVIEW
   ↓
VERIFYING
   ↓
COMPLETED
```

Other states:

```text
FAILED
CANCELLED
REASSIGNED
RECOVERING
WAITING
```

Each task should record:

- goal
- parent task
- dependencies
- assigned agent
- subagents
- deadline
- status
- evidence
- artifacts
- attempts
- failures
- verification results
- final outcome

---

# 9. Memory Architecture

The harness needs multiple memory types rather than one generic vector database.

Recommended memory layers:

## 9.1 Working Memory

Current task/context.

Short-lived.

Stores:

- current plan
- current observations
- active tool results
- current decisions

## 9.2 Episodic Memory

Specific previous events.

Examples:

- task attempt
- failure
- successful workflow
- user correction
- recovery event

## 9.3 Semantic Memory

Generalized knowledge.

Examples:

- project architecture
- coding conventions
- tool behavior
- domain facts

## 9.4 Procedural Memory

How to do something.

Examples:

- deployment procedure
- database migration procedure
- debugging workflow
- Git workflow

## 9.5 Skill Memory

References to reusable skills and when to invoke them.

## 9.6 Project Memory

Persistent project-specific context.

## 9.7 Agent Memory

Individual agent experience.

## 9.8 Organizational Memory

Knowledge shared by the agent organization.

## 9.9 Evolution Memory

Special memory for self-improvement.

Stores:

- previous experiments
- successful mutations
- failed mutations
- rejected hypotheses
- benchmark results
- fragile components
- known regressions
- successful improvement strategies

## 9.10 Failure Memory

Stores:

- failure
- root cause
- reproduction
- fix
- regression test
- recovery strategy

## 9.11 Presentation Memory

A long-lived, human-readable representation of the system's important state:

- what the system knows
- current architecture
- active projects
- important decisions
- agent organization
- learned procedures
- important historical events
- current improvement objectives

Presentation memory should not be the only source of truth. It should be generated/synchronized from structured state.

---

# 10. Memory Lifecycle

```text
Observe
  ↓
Extract
  ↓
Classify
  ↓
Deduplicate
  ↓
Validate
  ↓
Store
  ↓
Retrieve
  ↓
Use
  ↓
Evaluate usefulness
  ↓
Consolidate
  ↓
Decay / archive when appropriate
```

Memory should avoid:

- uncontrolled duplication
- stale information
- irrelevant retrieval
- context flooding
- unverified conclusions

---

# 11. Skill System

Skills should be modular and removable.

Each skill should contain:

```text
SKILL.md
instructions
examples
tool requirements
verification
dependencies
metadata
```

Skill lifecycle:

```text
DISCOVERED
 ↓
GENERATED
 ↓
TESTING
 ↓
ACTIVE
 ↓
IMPROVING
 ↓
DEPRECATED
 ↓
ARCHIVED
```

Skill metrics:

- usage
- success rate
- failure rate
- latency
- token cost
- user corrections
- dependency health
- last verified
- last used

---

# 12. Self-Generating Skills

The harness should be able to detect repeated manual procedures.

Example:

```text
Task A:
step1 → step2 → step3 → step4

Task B:
step1 → step2 → step3 → step4

Task C:
step1 → step2 → step3 → step4
```

Automation Miner:

```text
Pattern detected
      ↓
Generalize
      ↓
Generate SKILL.md
      ↓
Generate helper
      ↓
Generate tests
      ↓
Evaluate
      ↓
Register
```

This allows the system to grow its capability library automatically.

---

# 13. Tool System

Tools should also be lifecycle-managed.

```text
REQUESTED
 ↓
DESIGNED
 ↓
GENERATED
 ↓
SECURITY CHECK
 ↓
SANDBOX TEST
 ↓
ACTIVE
 ↓
MONITORED
 ↓
IMPROVED / DEPRECATED
```

The harness should detect:

- missing tools
- duplicate tools
- unreliable tools
- expensive tools
- tools that can be replaced by simpler automation

---

# 14. MCP Architecture

MCP should be treated as a capability boundary.

The harness should support:

- discovery
- installation/registration
- permission control
- per-agent access
- project-level access
- read-only vs write access
- health checks
- failure recovery
- auditing
- tool capability indexing

An agent should not automatically receive every MCP capability.

---

# 15. Subagent Architecture

Subagents should be created based on actual need.

Typical roles:

```text
researcher
planner
architect
coder
tester
reviewer
browser-agent
data-agent
security-agent
documentation-agent
recovery-agent
```

The supervisor should dynamically determine:

- whether a subagent is needed
- which model to use
- what context to give it
- what tools it can access
- when it should terminate

---

# 16. Model Router

The harness should not use one model for everything.

Possible routing:

```text
Simple task
    ↓
cheap/local model

Coding
    ↓
coding model

Deep reasoning
    ↓
reasoning model

Research
    ↓
research-capable model

Vision
    ↓
vision model

Final verification
    ↓
independent model
```

Routing itself should eventually become an optimization target.

---

# 17. Browser / Computer Automation

The harness should support computer interaction where needed:

- browser
- terminal
- filesystem
- desktop applications
- GUI workflows

Browser/computer actions should be observable and replayable where possible.

---

# 18. Long-Running Execution

The system should support goals lasting:

- hours
- days
- weeks
- potentially months

It needs:

- persistent state
- resumability
- checkpointing
- task queues
- heartbeats
- watchdogs
- retries
- recovery
- resource monitoring
- scheduled work
- event triggers

---

# 19. Always-On / Recovery Architecture

The system should monitor:

```text
agent process
worker
network
model API
MCP server
filesystem
database
CPU
RAM
disk
power
application state
```

Recovery examples:

```text
network lost
 ↓
wait
 ↓
reconnect
 ↓
resume checkpoint
```

```text
agent crash
 ↓
detect heartbeat timeout
 ↓
restart
 ↓
restore state
 ↓
resume task
```

```text
bad candidate
 ↓
detect regression
 ↓
rollback
 ↓
restore previous stable version
```

---

# 20. Observability Fabric

Everything important should generate structured telemetry.

Track:

- task
- agent
- model
- prompt version
- skill
- tool
- MCP
- action
- result
- error
- retry
- latency
- token usage
- cost
- memory retrieval
- subagent handoff
- verification
- user correction

A trace should resemble:

```json
{
  "trace_id": "trace_001",
  "task_id": "task_42",
  "agent_id": "coder",
  "model": "model-x",
  "events": [
    {
      "type": "tool_call",
      "tool": "filesystem.read",
      "latency_ms": 120
    },
    {
      "type": "test",
      "status": "failed"
    }
  ]
}
```

---

# 21. Self-Improvement / RSI

The major new subsystem is the:

# Recursive Self-Development Engine (RSDE)

or

# Recursive Evolution Engine (REE)

The objective is:

> Automatically discover weaknesses and opportunities in the harness, generate improvements, test them objectively, and safely promote validated improvements.

---

# 22. Self-Improvement Is Broader Than Code Editing

The evolution surface should include:

```text
source code
prompts
skills
tools
MCP configuration
workflow
agent topology
memory policies
retrieval
model routing
retry policies
context policies
scheduler
verification
automation
deployment
configuration
```

Therefore:

```text
RSI ≠ self-editing source code only
```

It is **system-level self-development**.

---

# 23. Improvement Detector

Create an:

# Improvement Opportunity Detector (IOD)

Inputs:

```text
execution traces
failures
tests
user corrections
latency
cost
tool usage
memory retrieval
agent trajectories
model performance
security alerts
dependency alerts
automation patterns
```

It searches for:

- repeated failure
- repeated retry
- loops
- context waste
- tool misuse
- missing tools
- missing skills
- poor memory retrieval
- memory pollution
- model mismatch
- planner failure
- reviewer failure
- test gaps
- performance regression
- cost regression
- reliability regression
- dependency problems
- repeated manual work
- missing recovery
- configuration drift
- duplicate capabilities

---

# 24. Structured Improvement Opportunity

Example:

```json
{
  "opportunity_id": "opp_01428",
  "type": "missing_automation",
  "severity": "medium",
  "confidence": 0.94,
  "evidence": [
    "workflow repeated 37 times",
    "average manual steps: 6",
    "average time: 94 seconds"
  ],
  "affected_component": "tool-system",
  "estimated_benefit": {
    "time_saved": 0.32,
    "reliability": 0.18,
    "cost": 0.11
  }
}
```

---

# 25. Automation Miner

A dedicated subsystem should mine execution traces for repeated procedures.

Example:

```text
read package.json
search dependency
run npm outdated
edit package.json
run tests
```

Repeated many times.

The system identifies:

```text
Automation Candidate:
dependency-update-verification
```

Then:

```text
generate tool
+
generate skill
+
generate tests
+
benchmark
```

This turns the harness into an **automation-discovery machine**.

---

# 26. Capability Gap Detector

When the agent cannot perform something, classify the gap:

```text
existing skill?
existing tool?
MCP capability?
workflow deficiency?
prompt deficiency?
model limitation?
code limitation?
```

Example:

```text
Need:
migration verification

Current:
can query DB
cannot compare migrations

Result:
missing capability
```

Then:

```text
research
 ↓
design
 ↓
implement
 ↓
test
 ↓
register
 ↓
benchmark
 ↓
promote
```

---

# 27. Repair Loop vs Improvement Loop

These must be separate.

## Incident Repair

```text
failure
 ↓
diagnose
 ↓
patch
 ↓
test
 ↓
deploy
```

## Capability Improvement

```text
observe
 ↓
detect inefficiency
 ↓
generate hypothesis
 ↓
generate candidates
 ↓
benchmark
 ↓
select
 ↓
promote
```

Repair makes broken things work.

Improvement makes working things better.

---

# 28. Improvement Hypothesis

Every proposed change should have an explicit hypothesis.

Example:

```yaml
hypothesis:
  problem: "planner repeats exploration unnecessarily"

  observed_evidence:
    - "41% of trajectories contain redundant research"
    - "average duplicate search count = 3.7"

  proposed_change:
    type: "workflow"
    modification: "add evidence-cache stage"

  predicted_effect:
    duplicate_searches: "-25%"
    token_usage: "-10%"

  risks:
    - stale evidence
    - cache contamination

  verification:
    required:
      - replay_100_tasks
      - freshness_test
      - regression_suite
```

---

# 29. Evolution Engine

The system should generate multiple competing candidates.

Example:

```text
Improvement:
planner reliability

Candidate A:
better prompt

Candidate B:
plan validation

Candidate C:
planner/reviewer loop

Candidate D:
dependency-aware planning

Candidate E:
historical successful plans

Candidate F:
different model routing
```

Then benchmark them.

This is evolutionary search rather than single-shot self-modification.

---

# 30. Candidate Population

Maintain a population:

```text
Version 0
 ├── Candidate A
 ├── Candidate B
 └── Candidate C

Candidate B
 ├── B1
 └── B2
```

Each candidate needs:

- parent version
- mutation
- proposer
- build
- tests
- metrics
- security status
- evaluation
- decision

---

# 31. Evolution DAG

Example:

```text
gen-000
   |
   +--- gen-001a
   |
   +--- gen-001b
   |
   +--- gen-001c
            |
            +--- gen-002c1
            +--- gen-002c2
```

Example candidate metadata:

```json
{
  "generation": 17,
  "parent": "gen-016",
  "mutation": "planner-cache",
  "proposer": "model-x",
  "tests": 1482,
  "success_rate": 0.93,
  "baseline_rate": 0.89,
  "latency_delta": -0.11,
  "cost_delta": -0.07,
  "security": "passed",
  "status": "candidate"
}
```

---

# 32. Verification Farm

The candidate must be evaluated independently.

Required layers:

```text
unit tests
integration tests
end-to-end tests
task replay
benchmark
held-out evaluation
static analysis
security
performance
trajectory quality
adversarial testing
```

The system should not accept:

> "The model says the patch looks good."

It should require measurable evidence.

---

# 33. Historical Replay

Use previous tasks:

```text
100 successful tasks
50 failed tasks
20 difficult tasks
```

Run:

```text
baseline
vs
candidate
```

Compare:

```text
success
correctness
iterations
tokens
latency
tool errors
human intervention
```

---

# 34. Held-Out Evaluation

Do not evaluate only on the tasks that generated the improvement.

Maintain a held-out evaluation set:

```text
improvement/training tasks
        ↓
candidate optimization

held-out tasks
        ↓
independent evaluation
```

This reduces self-overfitting.

---

# 35. Self-Growing Benchmark

When a production failure occurs:

```text
failure
 ↓
reproduce
 ↓
generate regression test
 ↓
add to benchmark
```

Over time:

```text
Benchmark v1 = 100 cases
Benchmark v2 = 174
Benchmark v3 = 311
Benchmark v4 = 580
```

The evaluation environment becomes progressively stronger.

---

# 36. Trajectory Evaluation

Evaluate not only whether the agent succeeds, but how.

Track:

```text
tool call count
wrong tool calls
repeated commands
backtracking
context usage
subagent usage
time
cost
failed tests
retries
unnecessary file reads
```

Example:

```text
Version A:
success 92%
tokens 1.4M
tool calls 32

Version B:
success 91%
tokens 0.7M
tool calls 17
```

The decision depends on the configured objectives.

---

# 37. Multi-Objective Evaluation

Do not optimize one number blindly.

Represent performance as a vector:

```text
PerformanceVector {
    correctness
    task_success
    reliability
    latency
    cost
    token_efficiency
    security
    maintainability
    automation_coverage
    recovery_rate
}
```

A candidate must not gain one metric by causing unacceptable degradation elsewhere.

---

# 38. Independent Evolution Judge

The agent creating a modification should not be the sole judge.

Example:

```text
Builder:
"planner is improved."

Independent reviewer:
"provide evidence."

Evaluator:
"success +4.8%."

Adversarial evaluator:
"fails 7 edge cases."

Security evaluator:
"unsafe shell capability."

Decision:
reject
```

This avoids self-confirmation.

---

# 39. Adversarial Evolution Agent

Create an agent whose purpose is to break candidates.

Search for:

- regressions
- edge cases
- security weaknesses
- loops
- resource exhaustion
- prompt injection
- memory contamination
- tool abuse
- incorrect assumptions

Architecture:

```text
Creator
   ↓
Critic
   ↓
Verifier
```

---

# 40. Shadow / Canary / Production

Never directly replace production.

Deployment lifecycle:

```text
candidate
 ↓
shadow
 ↓
canary
 ↓
production
 ↓
monitor
 ↓
promote / rollback
```

Candidate environments need:

- isolated filesystem
- isolated process
- limited network
- limited credentials
- CPU/memory quota
- timeout

---

# 41. Immutable Constitution

Some components should be protected from self-modification.

Immutable core should include:

```text
security policy
promotion rules
audit ledger
rollback mechanism
permissions
resource limits
authentication
secret policy
sandbox policy
network restrictions
```

The evolving system can modify its capabilities, but not its fundamental authority boundaries.

---

# 42. Critical Rule: Evaluator Must Be More Protected

The evaluator should be harder to modify than the agent it evaluates.

Bad:

```text
agent
 ↓
edit itself
 ↓
run own tests
 ↓
declare success
 ↓
deploy
```

Better:

```text
Evolution Agent
 ↓
Candidate
 ↓
Independent Evaluator
 ↓
Promotion Gate
 ↓
Version Manager
 ↓
Canary
 ↓
Monitoring
 ↓
Rollback / Promote
```

---

# 43. Evolution Memory

Maintain dedicated memory:

```text
successful mutations
failed mutations
why changes failed
models that generate useful changes
fragile components
weak tests
previously attempted hypotheses
unresolved problems
```

Example:

```text
planner:

self-review:
+2.3% success
+14% cost
rejected

plan-compression:
-4.2% success
rejected

dependency-aware planning:
+6.1%
accepted
```

This prevents repeating failed experiments.

---

# 44. Experiment Database

Start with SQLite locally.

Suggested tables:

```text
experiments
candidates
mutations
metrics
failures
hypotheses
evaluations
promotions
rollbacks
benchmark_runs
component_versions
```

Relationships:

```text
Experiment
 ├── Hypothesis
 ├── Candidates
 ├── Evaluations
 ├── Metrics
 └── Decision
```

Can later migrate to PostgreSQL.

---

# 45. Code Transformation Strategy

Use the smallest capable mechanism.

```text
simple deterministic change
        ↓
AST / rewrite tool

complex architectural change
        ↓
coding agent

unknown problem
        ↓
research + coding agent
```

This avoids using an LLM for changes that can be made deterministically.

---

# 46. Self-Improvement Levels

## Level 0 — Manual Evolution

```text
agent proposes
 ↓
human approves
```

## Level 1 — Automatic Repair

```text
failure
 ↓
diagnose
 ↓
patch
 ↓
tests
 ↓
PR
```

## Level 2 — Automatic Capability Creation

```text
missing capability
 ↓
skill/tool generation
 ↓
evaluation
 ↓
install
```

## Level 3 — Automatic Optimization

```text
telemetry
 ↓
bottleneck
 ↓
candidate generation
 ↓
A/B benchmark
 ↓
promotion
```

## Level 4 — Recursive Self-Development

Can modify:

```text
prompts
skills
tools
workflows
memory
agent topology
routing
retry policies
planner
evaluator adapters
source code
```

## Level 5 — Evolutionary Harness

```text
candidate population
+
version lineage
+
automatic benchmark generation
+
held-out evaluation
+
mutation search
+
selection
+
canary
+
rollback
```

## Level 6 — Runtime Self-Evolution

During a real task:

```text
obstacle
 ↓
temporary capability
 ↓
test
 ↓
use
 ↓
store as improvement candidate
 ↓
later benchmark
 ↓
possibly promote
```

---

# 47. Complete Self-Development Loop

```text
REAL WORK
   ↓
OBSERVE
   ↓
DETECT PROBLEM
   ↓
FIND ROOT CAUSE
   ↓
CREATE HYPOTHESES
   ↓
GENERATE MULTIPLE FIXES
   ↓
BUILD CANDIDATES
   ↓
VERIFY
   ↓
BENCHMARK
   ↓
COMPARE TO BASELINE
   ↓
INDEPENDENT JUDGE
   ↓
PROMOTION GATE
   ↓
SHADOW
   ↓
CANARY
   ↓
PRODUCTION
   ↓
MONITOR
   ↓
ROLLBACK OR PROMOTE
   ↓
EVOLUTION MEMORY
   ↓
NEXT GENERATION
```

---

# 48. Automatic Automation Discovery Example

Observed repeatedly:

```text
CI failure
 ↓
read logs
 ↓
identify test
 ↓
find file
 ↓
patch
 ↓
rerun test
 ↓
create commit
```

After enough occurrences:

```text
Automation Miner
 ↓
detect repeated workflow
 ↓
propose CI Failure Repair skill
 ↓
generate tool
 ↓
generate workflow
 ↓
test
 ↓
benchmark
 ↓
register
```

Future CI failures can then trigger that workflow automatically.

---

# 49. Example of Full Self-Improvement

Observed problem:

```text
Repository analysis takes 31 minutes.
Context overflow occurs.
Agent repeatedly loads huge files.
```

Detector:

```text
34% of tasks exceed context threshold.
```

Root cause:

```text
filesystem strategy loads full files.
```

Candidate solutions:

```text
A: prompt improvement
B: intelligent file slicing
C: semantic retrieval
D: AST-aware extraction
```

Benchmark:

```text
A → token reduction
B → larger reduction
C → larger reduction
D → strongest measured reduction
```

Held-out testing verifies that the improvement generalizes.

Security passes.

Canary runs.

Production metrics remain healthy.

Candidate is promoted.

---

# 50. Self-Improving Planner

The harness should have a dedicated improvement planner.

Normal planner asks:

> How do I solve this user task?

Improvement planner asks:

> How do I improve the system that solves user tasks?

Possible objectives:

```text
planning reliability
tool selection
token efficiency
cost
latency
autonomous completion
memory retrieval
recovery success
coding performance
automation coverage
human intervention
```

---

# 51. Self-Maintenance

The harness should continuously inspect:

```text
dependencies
security vulnerabilities
unused skills
unused tools
broken MCPs
failed tests
performance regressions
configuration drift
dead code
duplicate workflows
stale memory
```

It should create maintenance candidates and evaluate them.

---

# 52. Dependency Maintenance

Conceptual lifecycle:

```text
dependency scan
 ↓
vulnerability/update found
 ↓
generate update
 ↓
run tests
 ↓
compatibility evaluation
 ↓
candidate
 ↓
canary
```

---

# 53. Agent Runtime Monitoring

Monitor:

```text
agent heartbeat
task progress
tool health
memory health
MCP health
network
model API
resource usage
```

If a worker becomes stuck:

```text
detect
 ↓
inspect state
 ↓
attempt recovery
 ↓
restart if needed
 ↓
restore checkpoint
 ↓
resume
```

---

# 54. No Silent Failure

The harness should explicitly distinguish:

```text
completed
verified
partially completed
blocked
failed
recovered
rolled back
```

Never silently treat:

```text
"could not do it"
```

as:

```text
"done"
```

---

# 55. Completion Verification

Completion should be evidence-based.

Example:

```text
Goal:
fix authentication bug

Required evidence:
- code changed
- unit tests pass
- integration tests pass
- reproduction no longer fails
- no relevant regression
```

The agent should stop only when its completion contract is satisfied.

---

# 56. Research Before Action

For complex tasks, the supervisor should:

```text
understand
 ↓
research
 ↓
collect evidence
 ↓
compare approaches
 ↓
plan
 ↓
execute
 ↓
verify
```

Research should not be repeated unnecessarily; evidence should be cached and linked to decisions.

---

# 57. LLM Council / Debate

A future capability can use multiple models for difficult decisions.

Example:

```text
Proposal
 ├── Architect
 ├── Security reviewer
 ├── Performance reviewer
 ├── Coding reviewer
 └── Independent critic
        ↓
     Synthesis
```

Use this selectively because it increases cost and latency.

---

# 58. Swarm Intelligence

For difficult research/engineering tasks:

```text
Supervisor
   ↓
parallel specialists
   ├── researcher A
   ├── researcher B
   ├── researcher C
   └── verifier
   ↓
synthesis
```

The swarm should be task-driven rather than permanently spawning large numbers of agents.

---

# 59. Recursive Self-Improvement vs Weight Training

The initial system should focus on **harness-level RSI** rather than trying to retrain foundation-model weights.

It can improve:

```text
code
prompts
skills
tools
workflows
memory
routing
evaluation
automation
```

This is substantially easier to run with limited resources.

Model fine-tuning can be considered later as a separate experimental subsystem.

---

# 60. Resource-Aware Design

The target environment includes:

- Windows-first
- local machine
- limited RAM/storage
- free/open-source preference
- optional cloud resources
- local models where useful
- paid model APIs only when needed

Therefore:

```text
local model
→ cheap automation
→ deterministic tools
→ cached results
→ remote model only when useful
```

The self-development engine should itself be resource-aware.

---

# 61. Cost-Aware Evolution

Before running an expensive experiment:

```text
estimate:
  model cost
  runtime
  CPU
  RAM
  storage
```

Then decide:

```text
cheap static test
→ if promising
→ small replay
→ if promising
→ large replay
→ held-out evaluation
```

This creates an **experiment funnel**.

---

# 62. Evolution Experiment Funnel

```text
Idea
 ↓
Static validation
 ↓
Small benchmark
 ↓
Medium benchmark
 ↓
Large benchmark
 ↓
Held-out benchmark
 ↓
Security
 ↓
Canary
 ↓
Production
```

Do not spend large resources on obviously bad candidates.

---

# 63. Suggested Repository Structure

```text
agent-workspace-desktop/
│
├── harness/
│   ├── agents/
│   ├── tools/
│   ├── memory/
│   ├── skills/
│   ├── sandbox/
│   └── orchestration/
│
├── evolution/
│   ├── detector/
│   │   ├── failure_detector
│   │   ├── bottleneck_detector
│   │   ├── automation_miner
│   │   ├── capability_gap_detector
│   │   ├── regression_detector
│   │   └── anomaly_detector
│   │
│   ├── diagnosis/
│   │   ├── root_cause
│   │   ├── evidence
│   │   └── hypothesis
│   │
│   ├── generators/
│   │   ├── code_mutator
│   │   ├── prompt_mutator
│   │   ├── skill_generator
│   │   ├── tool_generator
│   │   ├── workflow_generator
│   │   └── config_optimizer
│   │
│   ├── experiments/
│   │   ├── workspace_manager
│   │   ├── candidate_manager
│   │   ├── lineage_manager
│   │   └── experiment_runner
│   │
│   ├── evaluation/
│   │   ├── unit
│   │   ├── integration
│   │   ├── e2e
│   │   ├── replay
│   │   ├── benchmark
│   │   ├── security
│   │   ├── performance
│   │   └── trajectory
│   │
│   ├── selection/
│   │   ├── baseline
│   │   ├── scorer
│   │   ├── pareto
│   │   └── promotion_gate
│   │
│   ├── deployment/
│   │   ├── shadow
│   │   ├── canary
│   │   ├── versioning
│   │   └── rollback
│   │
│   ├── memory/
│   │   ├── evolution_memory
│   │   ├── experiment_memory
│   │   └── failure_memory
│   │
│   └── policy/
│       ├── constitution
│       ├── permissions
│       ├── resource_limits
│       └── immutable_rules
│
├── benchmarks/
├── experiments/
├── evaluations/
├── evolution_runs/
└── versions/
```

---

# 64. Suggested Core Services

A practical implementation can start with these services:

```text
Supervisor
Task Manager
Agent Manager
Skill Manager
Tool Manager
MCP Manager
Memory Manager
Model Router
Execution Manager
Sandbox Manager
Observability Manager
Recovery Manager

Self-Development Engine
Improvement Detector
Automation Miner
Capability Gap Detector
Root Cause Analyzer
Hypothesis Generator
Candidate Builder
Benchmark Runner
Evaluation Judge
Promotion Manager
Rollback Manager
Evolution Memory
```

---

# 65. Core Data Model

Important entities:

```text
User
Project
Goal
Task
Agent
AgentGroup
Skill
Tool
MCPServer
Model
Execution
Trace
Memory
Failure
Recovery
Experiment
Hypothesis
Candidate
Mutation
Benchmark
Evaluation
Promotion
Rollback
Version
```

Relationships:

```text
Project
 ├── Goals
 ├── Tasks
 ├── Agents
 ├── Skills
 ├── Tools
 ├── Memories
 ├── Experiments
 └── Versions
```

---

# 66. Version Management

Every production harness version should be immutable.

Example:

```text
harness-v1
harness-v2
harness-v3
...
```

Never mutate production in place.

A candidate is always a versioned artifact.

---

# 67. Rollback

Rollback should be one operation:

```text
current = v43
previous = v42

regression
 ↓
restore v42
 ↓
restart
 ↓
restore state
```

Rollback must not depend on the same newly changed component that caused the failure.

---

# 68. Audit Ledger

Record every self-development action:

```text
who proposed it
why
evidence
what changed
files changed
tools created
tests run
metrics
security results
decision
promoter
rollback information
```

This makes evolution inspectable.

---

# 69. Self-Development Safety Gates

Minimum gates:

```text
Build passes
Tests pass
Security passes
No protected files modified
Resource limits pass
Regression threshold pass
Held-out evaluation pass when required
Canary pass
Rollback available
```

---

# 70. Protected Paths

Define protected paths that the self-development engine cannot modify automatically.

Examples:

```text
security/
identity/
secrets/
promotion/
rollback/
audit/
immutable-policy/
```

Changes to those areas should require explicit human approval.

---

# 71. Human-in-the-Loop Policy

Not every change needs human approval.

Suggested policy:

### Automatic

Low-risk:

```text
prompt optimization
skill wording
cache policy
non-critical workflow
test additions
documentation
safe dependency patch
```

### Approval required

Higher-risk:

```text
permissions
network access
secret handling
sandbox boundaries
identity/authentication
production infrastructure
core evaluator
promotion policy
resource limits
```

---

# 72. Three Modes

## Safe Mode

All self-modifications require approval.

## Autonomous Mode

Low/medium-risk changes can automatically promote after validation.

## Research Mode

More experimental evolution is allowed in isolated environments, but cannot bypass immutable security/promotion rules.

---

# 73. Important Research Projects / Concepts to Study

## Darwin Gödel Machine

Relevant for recursive self-modification of coding agents.

Key architectural idea:

```text
agent
 ↓
modify agent
 ↓
evaluate
 ↓
retain useful version
```

Use it as inspiration for recursive agent evolution.

---

## Live-SWE-agent

Relevant for runtime self-evolution while solving real software engineering tasks.

Important idea:

```text
real task
 ↓
agent encounters obstacle
 ↓
agent modifies scaffold/capability
 ↓
continues task
```

---

## AlphaEvolve

Relevant for:

- candidate generation
- evolutionary population
- automated evaluators
- selection
- program improvement

Core concept:

```text
generate
→ evaluate
→ select
→ mutate
→ repeat
```

---

## Reflexion

Relevant for:

- reflective feedback
- episodic memory
- learning from failures

The system can improve behavior without modifying model weights.

---

## Agent Workspace

Relevant as the project foundation for:

- skills
- tools
- memory
- subagents
- sandbox
- long-horizon orchestration

---

## DeepAgents

Relevant for:

- agent architecture
- planning
- skills
- filesystem
- subagents
- context management

---

## mini-SWE-agent

Relevant for the principle:

> Keep the core agent simple when simple interfaces are sufficient.

---

## Agentless

Relevant for structured:

```text
localization
→ candidate repair
→ validation
```

rather than unconstrained editing.

---

## GitHub Agentic Workflows

Relevant for:

- agentic automation
- permissions
- sandboxing
- safe outputs
- review
- CI integration
- security

---

## OpenRewrite

Relevant for deterministic automated refactoring.

---

## Dependabot

Relevant for automated dependency/security maintenance.

---

# 74. Architecture Principles

## Principle 1

**Observe before changing.**

## Principle 2

**Measure before promoting.**

## Principle 3

**Generate multiple hypotheses.**

## Principle 4

**Separate creator from evaluator.**

## Principle 5

**Never directly mutate production.**

## Principle 6

**Keep rollback trivial.**

## Principle 7

**Turn failures into regression tests.**

## Principle 8

**Turn repeated workflows into automation.**

## Principle 9

**Keep an evolution memory.**

## Principle 10

**Protect the evaluator and security core.**

## Principle 11

**Use the smallest capable mechanism.**

## Principle 12

**Optimize the whole system, not just model output.**

---

# 75. Final Target Architecture

```text
                         USER
                           │
                           ▼
                 ┌───────────────────┐
                 │ EXECUTIVE AGENT   │
                 └─────────┬─────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │ PLAN / RESEARCH / DELEGATE│
              └────────────┬────────────┘
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
          Agents         Skills         Tools/MCP
             │             │              │
             └─────────────┼──────────────┘
                           ▼
                    EXECUTION FABRIC
                           │
                           ▼
                  OBSERVABILITY FABRIC
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
          Memory       Verification     Outcomes
             │             │              │
             └─────────────┼──────────────┘
                           ▼
              SELF-DEVELOPMENT ENGINE
                           │
       ┌───────────────────┼────────────────────┐
       ▼                   ▼                    ▼
 Problem Detection   Automation Mining    Capability Gaps
       │                   │                    │
       └───────────────────┼────────────────────┘
                           ▼
                  ROOT-CAUSE ANALYSIS
                           │
                           ▼
                 HYPOTHESIS GENERATOR
                           │
                           ▼
                  CANDIDATE GENERATORS
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
           Code          Skill          Tool
         Mutation      Generation     Generation
             │             │             │
             └─────────────┼─────────────┘
                           ▼
                  EVOLUTION POPULATION
                           │
                           ▼
                   VERIFICATION FARM
                           │
       ┌───────────────────┼─────────────────────┐
       ▼                   ▼                     ▼
     Tests              Replay              Security
       │                   │                     │
       └───────────────────┼─────────────────────┘
                           ▼
                   HELD-OUT BENCHMARK
                           │
                           ▼
                  INDEPENDENT JUDGE
                           │
                           ▼
                    PROMOTION GATE
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
              Reject              Shadow
                                      │
                                      ▼
                                    Canary
                                      │
                                      ▼
                                  Production
                                      │
                                      ▼
                                  Monitoring
                                      │
                              ┌───────┴───────┐
                              ▼               ▼
                           Rollback         Promote
                                              │
                                              ▼
                                       Evolution Memory
                                              │
                                              ▼
                                      Next Generation
```

---

# 76. Recommended Implementation Order

Do not attempt to build the complete Level-6 system at once.

## Phase 1 — Foundation

Implement:

```text
observability
task state
agent state
persistent memory
sandbox
versioning
rollback
```

## Phase 2 — Verification

Implement:

```text
benchmark registry
task replay
regression tests
independent evaluator
security gate
```

## Phase 3 — Automatic Repair

Implement:

```text
failure detector
root-cause analyzer
patch generator
automatic test generation
candidate validation
```

## Phase 4 — Automation Discovery

Implement:

```text
trace clustering
repeated workflow detection
automation miner
skill generator
tool generator
```

## Phase 5 — Self-Optimization

Implement:

```text
bottleneck detection
hypothesis generation
multi-candidate mutation
baseline comparison
multi-objective evaluation
```

## Phase 6 — Evolution

Implement:

```text
population
lineage
mutation search
evolution memory
held-out benchmarks
canary
automatic promotion
```

## Phase 7 — Runtime Self-Development

Implement:

```text
temporary runtime capabilities
live experimentation
automatic capability extraction
promotion into permanent capabilities
```

---

# 77. Minimum Viable RSI

If resources are limited, the first useful RSI version can be surprisingly small:

```text
Trace Collector
      ↓
Failure Detector
      ↓
Improvement Planner
      ↓
Coding Agent
      ↓
Sandbox
      ↓
Tests
      ↓
Benchmark
      ↓
Evaluator
      ↓
Git Branch
      ↓
Human Approval
```

Once this is reliable, automate the promotion stages.

---

# 78. Ultimate Objective

The long-term system should be able to do this:

```text
USER:
"Build X."

HARNESS:
understands objective
 ↓
researches
 ↓
plans
 ↓
creates agents
 ↓
executes
 ↓
tests
 ↓
delivers X

Then:

HARNESS:
"What prevented me from doing this faster/better?"
 ↓
finds repeated bottleneck
 ↓
discovers missing capability
 ↓
creates improvement hypothesis
 ↓
generates several candidates
 ↓
tests candidates
 ↓
rejects failures
 ↓
canaries successful candidate
 ↓
promotes it
 ↓
records what happened
 ↓
updates benchmark
 ↓
continues working
```

The final system therefore becomes a **closed-loop autonomous engineering and improvement platform** rather than a static agent framework.

---

# 79. One-Sentence Definition

> **A self-developing AI harness is an agent operating system that continuously observes its own execution, identifies failures and automation opportunities, creates and evaluates competing improvements, and safely evolves its code, skills, tools, workflows, memory and agent architecture under independently verified promotion gates.**

---

# 80. Final Mental Model

Think of the entire project as five nested loops:

```text
LOOP 1 — TASK
Solve the user's goal.

LOOP 2 — RECOVERY
Recover when execution fails.

LOOP 3 — LEARNING
Learn from successful and failed executions.

LOOP 4 — OPTIMIZATION
Find ways to make existing behavior better.

LOOP 5 — EVOLUTION
Modify the harness itself and validate the new generation.
```

Combined:

```text
                 ┌─────────────────────────────┐
                 │       EVOLUTION LOOP        │
                 │                             │
                 │   improve the harness       │
                 │       ┌──────────────┐      │
                 │       │ OPTIMIZATION │      │
                 │       │     LOOP     │      │
                 │       │   ┌──────┐   │      │
                 │       │   │LEARN │   │      │
                 │       │   │ LOOP │   │      │
                 │       │   │ ┌──┐ │   │      │
                 │       │   │ │  │ │   │      │
                 │       │   │ │TASK│   │      │
                 │       │   │ │LOOP│   │      │
                 │       │   │ └──┘ │   │      │
                 │       │   └──────┘   │      │
                 │       └──────────────┘      │
                 └─────────────────────────────┘
```

The objective is not uncontrolled self-modification. It is **measured, reversible, evidence-driven self-development**.
