# NVIDIA AVO Architecture & High-End Harness Blueprint

## Purpose

This document reconstructs the **publicly described NVIDIA Agentic Variation Operators (AVO) architecture** and turns it into an implementation-grade harness blueprint for a general-purpose autonomous agent.

It deliberately separates:

1. **Publicly verified AVO mechanisms** described by NVIDIA and the AVO paper.
2. **Architecture inferred from those mechanisms**.
3. **Recommended extensions** for a broader, general-purpose agent harness.

The exact internal implementation of NVIDIA's private agent system is not publicly available, so this is **not a claim to reproduce proprietary internal code**. It is a technically grounded architecture based on NVIDIA's public paper and technical blog, plus NVIDIA OpenShell's public runtime architecture.

---

# 1. Executive Summary

NVIDIA's central AVO idea is unusually important for advanced agent harness design:

> The language model is not merely a candidate generator. The autonomous agent becomes the variation operator itself.

Classical evolutionary search can be represented as:

```text
Vary(P_t) = Generate(Sample(P_t))
```

AVO instead defines:

```text
Vary(P_t) = Agent(P_t, K, f)
```

where:

- `P_t` = accumulated lineage of prior solutions and scores
- `K` = domain-specific knowledge base
- `f` = evaluation/scoring function
- `Agent(...)` = autonomous agent that decides what to inspect, modify, test, evaluate and revise

NVIDIA reports that the AVO system ran continuously for seven days on NVIDIA B200 GPUs, explored more than 500 optimization directions and produced 40 committed kernel versions. NVIDIA reports gains of up to 3.5% over cuDNN and up to 10.5% over FlashAttention-4 in the evaluated MHA configurations. NVIDIA later reported that the same general-purpose AVO architecture achieved a 100.00 RHAE score on the 25-environment ARC-AGI-3 public set using Claude Opus 5, solving 183 levels in 6,624 environment actions. NVIDIA explicitly frames these as **system-level harness results**, not merely model capability results. [1][2]

The public AVO architecture has five essential properties:

```text
1. Autonomous iterative agent loop
2. Persistent state / memory
3. Full lineage awareness
4. Grounded execution feedback
5. Supervisory intervention on stagnation
```

For a world-class general-purpose harness, these should be expanded into:

```text
Executive
  -> Planner
  -> Agent Runtime
  -> Tools / Skills
  -> Secure Environment
  -> Evaluation
  -> Memory / Lineage
  -> Supervisor
  -> Evolution Factory
```

---

# 2. What NVIDIA Publicly Says AVO Is

NVIDIA describes AVO as a general-purpose coding agent architecture for sustained autonomous operation over long horizons. The main agent can inspect and edit code, execute commands, consult documentation and validate its work. [1]

The original AVO paper says prior LLM-based evolutionary systems usually leave parent sampling, evaluation and population management to a fixed framework. AVO replaces that fixed variation pipeline with a self-directed agent run that can choose when to consult prior solutions, reference material and evaluation utilities, what to change, and when to evaluate. [2]

NVIDIA's published AVO formulation is:

```text
Vary(P_t) = Agent(P_t, K, f)
```

where the complete lineage `P_t` is available to the agent and the scoring function can be multi-dimensional. In the kernel experiment, the score included both numerical correctness and throughput. Candidates that failed correctness were assigned zero score regardless of throughput. [2]

A crucial implementation detail is that unsuccessful intermediate attempts can remain in the agent's internal trajectory without entering the committed lineage. A new committed version is persisted when it passes correctness and matches or improves the best committed benchmark score. [2]

The architecture therefore separates:

```text
internal exploration
        !=
committed lineage
```

That distinction is essential for reliable long-horizon evolution.

---

# 3. Public AVO Architecture

The public architecture can be reconstructed as follows:

```text
                 DOMAIN GOAL
                     |
                     v
             +----------------+
             | CURRENT STATE  |
             | + FULL LINEAGE |
             +-------+--------+
                     |
                     v
            +-------------------+
            | AUTONOMOUS AGENT  |
            |                   |
            | inspect           |
            | reason            |
            | plan              |
            | edit              |
            | execute           |
            | evaluate         |
            | diagnose          |
            | revise            |
            +---------+---------+
                      |
              execution/evaluation
                    feedback
                      |
                      v
             +------------------+
             | SCORE FUNCTION f |
             | correctness      |
             | performance      |
             | other metrics    |
             +--------+---------+
                      |
              commit if good
                      |
                      v
             +------------------+
             | COMMITTED        |
             | LINEAGE          |
             +--------+---------+
                      |
                      v
             +------------------+
             | SUPERVISOR       |
             |                  |
             | stagnation       |
             | repeated cycles  |
             | strategy reset   |
             +--------+---------+
                      |
                      +-----> redirect agent
```

This diagram captures the major public mechanisms without pretending NVIDIA has published its complete private software stack.

---

# 4. AVO Anatomy of One Variation Step

A single variation step is **not** one LLM call.

NVIDIA describes it as a general-purpose coding-agent loop that may perform many internal actions. The agent can inspect multiple prior implementations, compare profiling characteristics, consult documentation, implement a candidate, run evaluation, diagnose failure, revise and repeat. [2]

The conceptual loop is:

```text
START
  |
  v
Load current best
  |
  v
Inspect lineage
  |
  v
Inspect knowledge base
  |
  v
Form hypothesis
  |
  v
Plan change
  |
  v
Edit implementation
  |
  v
Run correctness test
  |
  +---- fail ----> diagnose ----> revise ----+
  |                                          |
  +------------------------------------------+
  |
  v
Run performance / task evaluation
  |
  +---- no improvement ----> diagnose/revise
  |
  v
Commit candidate
  |
  v
Persist lineage + metrics
  |
  v
Continue long-horizon search
```

This is the foundational design pattern to preserve.

---

# 5. Full General-Purpose Harness Inspired by AVO

For a general-purpose agent, extend the public AVO architecture into a layered system:

```text
+-----------------------------------------------------------------------+
|                         USER / EXTERNAL WORLD                         |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                           CONTROL PLANE                               |
| identity | sessions | budgets | scheduler | policy | audit            |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         EXECUTIVE / SUPERVISOR                        |
| goal compiler | state manager | planner | monitor | replanner          |
+-----------------------+-------------------+-----------------------------+
                        |                   |
                        |                   v
                        |          +--------------------+
                        |          | WORLD / TASK MODEL |
                        |          +--------------------+
                        |
                        v
+-----------------------------------------------------------------------+
|                            AGENT RUNTIME                              |
| observe -> retrieve -> reason -> act -> verify -> persist             |
+-----------------------+--------------------+--------------------------+
                        |                    |
                        v                    v
               +----------------+     +-------------------+
               | MEMORY FABRIC  |     | MODEL FABRIC      |
               | episodic       |     | local models      |
               | semantic       |     | reasoning         |
               | procedural     |     | coding            |
               | failure        |     | vision            |
               | lineage        |     | evaluator         |
               +-------+--------+     +---------+---------+
                       |                        |
                       +------------+-----------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         AGENT / SKILL FABRIC                          |
| workers | task DAG | specialist agents | MCP | APIs | CLI | skills     |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                          COMPUTER / TOOL FABRIC                       |
| browser | DOM | CDP | accessibility | shell | files | desktop | APIs  |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                       POLICY + SECURITY KERNEL                        |
| risk classifier | permissions | credential broker | prompt defense     |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         SECURE EXECUTION                              |
| container | Podman/Docker | WSL | VM/microVM | network isolation       |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                            ENVIRONMENT                                |
| code | web | applications | cloud | databases | files | OS             |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                      EVALUATION + EVIDENCE                            |
| tests | graders | benchmarks | visual checks | state checks | replay    |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         LINEAGE / EVENT STORE                         |
| actions | observations | candidates | scores | failures | artifacts     |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|                         EVOLUTION FACTORY                             |
| hypotheses | candidates | AVO loop | benchmark | red-team | canary      |
| promotion | rollback | strategy discovery | skill evolution              |
+-----------------------------------------------------------------------+
```

---

# 6. Executive Layer

The Executive is above individual task agents.

Responsibilities:

```text
Goal interpretation
Requirement extraction
Priority assignment
Risk classification
Resource allocation
Task decomposition
Strategy selection
Completion judgment
Escalation
```

Input:

```json
{
  "goal": "Optimize this workload",
  "constraints": {},
  "quality_bar": {},
  "deadline": null,
  "budget": {},
  "permissions": []
}
```

Output:

```json
{
  "objective": {},
  "success_conditions": [],
  "plan": {},
  "verification": [],
  "risk_policy": {},
  "resource_policy": {}
}
```

The Executive does not directly execute every tool call. It allocates work to the runtime.

---

# 7. Agent Runtime

The universal runtime primitive should be:

```text
OBSERVE
  -> UPDATE STATE
  -> RETRIEVE CONTEXT
  -> PLAN
  -> SELECT ACTION
  -> POLICY CHECK
  -> EXECUTE
  -> OBSERVE RESULT
  -> VERIFY
  -> WRITE MEMORY
  -> CONTINUE / REPLAN / RECOVER / FINISH
```

Pseudo-interface:

```python
class AgentRuntime:
    async def run(self, goal):
        while not self.done():
            state = await self.observe()
            context = await self.memory.retrieve(state)
            decision = await self.reason(state, context)
            action = await self.select_action(decision)
            await self.policy.authorize(action)
            result = await self.execute(action)
            verification = await self.verify(result, state)
            await self.persist(state, action, result, verification)
            await self.recover_or_replan_if_needed()
```

---

# 8. Supervisor

This is one of the most important AVO mechanisms.

NVIDIA says AVO's supervisor detects when the agent stalls or enters unproductive cycles and can intervene by reviewing the broader trajectory and steering the search toward alternative directions. [1][2]

Recommended supervisor signals:

```text
progress rate
score slope
failure frequency
repeated edits
repeated tool calls
strategy repetition
context pressure
cost
latency
verification failures
```

Example:

```text
                TRAJECTORY
                    |
                    v
             +--------------+
             | SUPERVISOR   |
             +------+-------+
                    |
        +-----------+-----------+
        |                       |
        v                       v
   progressing               stagnating
        |                       |
      continue             diagnose pattern
                                |
                        generate directions
                                |
                    +-----------+-----------+
                    |     |     |     |
                    v     v     v     v
                   D1    D2    D3    D4
                    |
                    v
               redirect agent
```

The supervisor should not micromanage every action. It should intervene when the trajectory becomes unproductive.

---

# 9. Lineage System

AVO's lineage is not just version control; it is an **evolutionary memory structure**.

Each committed candidate should contain:

```text
candidate_id
parent_id
branch_id
commit_hash
objective
score_vector
correctness
performance
changeset
reasoning_summary
tests
artifacts
environment
model
runtime
```

Example:

```json
{
  "candidate_id": "c_0040",
  "parent_id": "c_0039",
  "score": {
    "correctness": 1.0,
    "performance": 0.984
  },
  "commit": "abc123",
  "status": "committed"
}
```

Maintain two stores:

```text
INTERNAL TRAJECTORY
- failed attempts
- experiments
- hypotheses
- intermediate observations

COMMITTED LINEAGE
- validated candidates
- scores
- parent relationships
- durable artifacts
```

This is directly aligned with the public AVO design.

---

# 10. Knowledge Base

The knowledge base `K` is a first-class input to the agentic variation operator.

For general agents it should include:

```text
Documentation
Source repositories
Reference implementations
API specifications
Domain papers
Benchmarks
Known failure patterns
Internal design notes
Tool documentation
Environment constraints
```

Recommended structure:

```text
knowledge/
  domain/
  docs/
  references/
  examples/
  benchmarks/
  failures/
  policies/
```

The agent should retrieve only relevant pieces instead of blindly injecting the full corpus.

---

# 11. Scoring / Evaluation Function

The AVO score function `f` is a critical architectural boundary.

Do not use one vague boolean called `success`.

Use a score vector:

```text
f(candidate) =
[
  correctness,
  performance,
  quality,
  robustness,
  security,
  cost,
  latency,
  maintainability
]
```

For optimization tasks, define domain-specific dimensions.

For software:

```text
correctness
performance
memory
security
test coverage
regression count
```

For research:

```text
source quality
claim accuracy
coverage
contradiction handling
citation completeness
```

For computer-use:

```text
task success
state correctness
action count
recovery count
safety
```

The evaluator must be mechanically grounded wherever possible.

---

# 12. Correctness Gate

AVO's kernel setup gives correctness precedence over performance: failing correctness means the candidate receives zero score regardless of throughput. [2]

Generalized rule:

```text
if correctness < threshold:
    reject candidate
else:
    evaluate optimization dimensions
```

This prevents reward hacking through a fast but incorrect solution.

---

# 13. Candidate Lifecycle

```text
PROPOSED
   |
   v
RUNNING
   |
   +---- error ----> DEBUGGING
   |                    |
   |                    +----> RUNNING
   |
   v
CORRECT
   |
   v
BENCHMARKED
   |
   +---- worse ------> REJECTED
   |
   v
QUALIFIED
   |
   v
COMMITTED
   |
   v
LINEAGE
```

Do not commit every intermediate attempt.

---

# 14. AVO Evolution Loop

The high-level loop is:

```text
                    CURRENT BEST
                         |
                         v
                 AGENTIC VARIATION
                         |
              +----------+----------+
              | inspect lineage     |
              | inspect knowledge   |
              | diagnose bottleneck |
              | formulate strategy  |
              | modify candidate     |
              | test                 |
              | evaluate             |
              | repair               |
              +----------+----------+
                         |
                         v
                    NEW CANDIDATE
                         |
                         v
                     SCORE f(x)
                         |
               +---------+----------+
               |                    |
             worse               equal/better
               |                    |
            discard             commit
                                    |
                                    v
                              updated lineage
                                    |
                                    v
                                 repeat
```

---

# 15. Strategy Diversity

One limitation of simple single-lineage evolution is getting trapped in one local strategy.

AVO's paper says its presented experiment uses a single-lineage continuous instantiation and leaves population-level branching and archive management as future extensions. [2]

For a larger general-purpose harness, add optional branches:

```text
                   BEST
                    |
        +-----------+-----------+
        |           |           |
      Branch A   Branch B    Branch C
        |           |           |
      local      radically    alternative
    optimization  different   hypothesis
        |           |           |
        +-----------+-----------+
                    |
                    v
                 EVALUATE
                    |
                    v
                  SELECT
```

Possible selection strategies:

```text
best-score
best-by-dimension
Pareto frontier
diversity-aware
novelty-aware
risk-adjusted
cost-adjusted
```

---

# 16. Generalized Evolution Factory

For your own system, place AVO inside a broader R&D loop:

```text
REAL TASKS
   |
   v
TRAJECTORY DATA
   |
   v
FAILURE MINING
   |
   v
IMPROVEMENT HYPOTHESES
   |
   v
CANDIDATE HARNESS / SKILL / STRATEGY
   |
   v
AVO SEARCH LOOP
   |
   v
BENCHMARK
   |
   v
SECURITY / RED TEAM
   |
   v
REGRESSION SUITE
   |
   v
SHADOW TEST
   |
   v
CANARY
   |
   +---- fail ----> ROLLBACK
   |
   v
PROMOTE
```

Potentially evolvable components:

```text
planner strategy
agent prompts
skills
memory retrieval
context compaction
model routing
tool routing
verification policies
recovery strategies
subagent topology
evaluation strategy
runtime code
```

This extends the AVO concept from optimizing a program to optimizing the harness that solves programs/tasks.

---

# 17. Computer-Use Fabric

AVO itself is primarily described around software engineering and interactive environments. For a universal computer agent, add a computer-use layer beneath the runtime.

Recommended capability hierarchy:

```text
DIRECT API
   |
   v
CLI / SHELL
   |
   v
APP AUTOMATION
   |
   v
DOM / CDP
   |
   v
ACCESSIBILITY TREE
   |
   v
VISION
   |
   v
RAW POINTER / KEYBOARD
```

Use the highest-level reliable interface available.

For example:

```text
Spreadsheet edit
  -> Python/structured API
  -> app automation
  -> accessibility
  -> GUI vision
```

This reduces fragile GUI actions.

---

# 18. Browser Layer

Recommended stack:

```text
Browser Agent
   |
   +-- Playwright
   +-- Chromium
   +-- CDP
   +-- DOM inspection
   +-- accessibility tree
   +-- screenshots
   +-- network observation
```

Every action should contain:

```json
{
  "intent": "submit form",
  "method": "dom_click",
  "target": {},
  "expected_state": {},
  "verification": {}
}
```

---

# 19. Secure Runtime: NVIDIA OpenShell-Inspired Layer

NVIDIA OpenShell provides a useful public implementation model for the security half of an autonomous-agent harness.

NVIDIA describes three stable runtime components:

```text
CLI / SDK / TUI
       |
       v
    Gateway
       |
       v
   Supervisor
       |
       v
    Sandbox
```

The gateway is the control plane for API access, state, policy, settings, provider/inference configuration and relay coordination. The supervisor lives inside each sandbox and enforces local process, filesystem, network and credential controls. [3]

NVIDIA also documents compute-driver boundaries so Docker, Podman, Kubernetes and MicroVM-style runtimes can be used behind a stable sandbox contract. [3][4]

Recommended architecture for your harness:

```text
CONTROL PLANE
   |
   +-- policy
   +-- credentials
   +-- state
   +-- scheduler
   +-- lifecycle
   |
   v
SANDBOX SUPERVISOR
   |
   +-- process policy
   +-- filesystem policy
   +-- network policy
   +-- inference routing
   +-- credential injection
   |
   v
AGENT PROCESS
```

---

# 20. Security Policy Model

NVIDIA OpenShell documents static and dynamic controls. Static controls include filesystem, Landlock and process settings that are established at sandbox creation. Network policies and network middleware can be updated dynamically. [5]

Use the same conceptual split:

```text
STATIC
- sandbox identity
- filesystem roots
- privilege model
- syscall restrictions

DYNAMIC
- network destinations
- API endpoints
- credential grants
- inference routing
- temporary permissions
```

Default deny should be the baseline.

---

# 21. Credential Broker

Never expose broad credentials to the agent.

Instead:

```text
AGENT REQUEST
      |
      v
CREDENTIAL BROKER
      |
      v
POLICY CHECK
      |
      v
SHORT-LIVED SCOPED TOKEN
      |
      v
TOOL / API
```

Example:

```text
GitHub
repo = project-A
scope = contents:write
TTL = 10 minutes
```

not:

```text
GITHUB_TOKEN = unrestricted
```

---

# 22. Policy Advisor Pattern

NVIDIA OpenShell also documents a policy-advisor mechanism in which a sandboxed agent can propose a narrow policy change after a request is denied. The proposal can be reviewed externally, and accepted network policy can be hot-reloaded. The important design principle is that the default-deny posture remains intact. [6]

For your system:

```text
ACTION DENIED
    |
    v
AGENT EXPLAINS NEED
    |
    v
POLICY PROPOSAL
    |
    v
PROVER / POLICY CHECK
    |
    +---- unsafe ----> deny
    |
    v
approval / auto-approval policy
    |
    v
HOT-RELOAD NARROW CAPABILITY
```

---

# 23. Memory Architecture

For long-horizon general-purpose operation, use multiple memory tiers:

```text
WORKING MEMORY
    |
    v
EPISODIC MEMORY
    |
    v
SEMANTIC MEMORY
    |
    v
PROCEDURAL MEMORY
    |
    v
FAILURE MEMORY
    |
    v
LINEAGE MEMORY
    |
    v
WORLD STATE
```

### Working memory

Current task state.

### Episodic memory

What happened during past tasks.

### Semantic memory

Facts and relationships.

### Procedural memory

Successful reusable workflows.

### Failure memory

Failures and recovery strategies.

### Lineage memory

Historical candidates and scores.

### World state

Current computer/application/project state.

---

# 24. Event Store

Use an event-sourced core.

Recommended events:

```text
GoalCreated
TaskCreated
TaskStarted
PlanCreated
PlanChanged
AgentSpawned
ToolCalled
ToolCompleted
ObservationReceived
StateChanged
CandidateCreated
CandidateTested
CandidateRejected
CandidateCommitted
FailureDetected
SupervisorIntervened
RecoveryStarted
ArtifactCreated
VerificationCompleted
EvolutionStarted
CandidatePromoted
RollbackExecuted
```

Every event should include:

```text
id
timestamp
session_id
project_id
agent_id
parent_event
model
environment
risk
payload
```

This enables replay, analytics and self-improvement.

---

# 25. Verification Architecture

The executing agent should not be the sole judge of success.

```text
EXECUTION
   |
   v
RESULT
   |
   +------------+------------+
   |            |            |
   v            v            v
Tests       State Check   Independent Critic
   |            |            |
   +------------+------------+
                |
                v
           VERIFICATION
                |
         +------+------+
         |             |
        PASS          FAIL
         |             |
       commit      recovery/replan
```

Verification sources:

```text
unit tests
integration tests
E2E tests
browser state
visual inspection
numerical comparison
benchmark score
security scans
schema validation
artifact existence
source/citation validation
```

---

# 26. Failure and Recovery Engine

Long-horizon agents must assume failure.

Failure categories:

```text
timeout
wrong assumption
wrong tool
invalid state
authentication
network
environment
incorrect output
security rejection
stagnation
repeated loop
unknown failure
```

Recovery options:

```text
retry
alternate tool
alternate model
re-observe
rollback
checkpoint restore
re-plan
delegate
change environment
increase compute
escalate
terminate
```

Each recovery should become training/evolution evidence.

---

# 27. Adaptive Compute

Use a dynamic reasoning budget:

```text
simple
  -> fast local model

medium
  -> stronger reasoning

complex
  -> deep reasoning + tools

critical
  -> multiple agents + independent verification

stagnation
  -> supervisor + strategy search
```

This is especially important for a free/self-hosted system because compute is the main scarce resource.

---

# 28. Model-Agnostic Fabric

Do not couple the harness to one vendor.

```text
                 MODEL ROUTER
                      |
       +--------------+---------------+
       |              |               |
       v              v               v
    reasoning        coding          vision
       |              |               |
       +--------------+---------------+
                      |
                 evaluator
```

Providers can include local/open models and optional external providers.

The architecture should remain fully functional with:

```text
Ollama
llama.cpp
vLLM
other OpenAI-compatible local servers
```

Paid providers can be adapters, not dependencies.

---

# 29. Multi-Agent Fabric

A general-purpose version should support a dynamic task graph:

```text
                         EXECUTIVE
                             |
                   +---------+---------+
                   |         |         |
                Research   Coding   Computer
                   |         |         |
                   +---------+---------+
                             |
                         Synthesizer
                             |
                         Verifier
                             |
                          Result
```

Workers should receive narrow context rather than full global state.

Use a task bus for:

```text
task creation
handoff
result delivery
dependency management
parallel work
cancellation
priority
```

---

# 30. Generalized AVO for Any Work

The most useful abstraction is to make the AVO pattern domain-neutral:

```python
class EvolutionTarget:
    current_lineage: list
    knowledge_base: KnowledgeBase
    evaluator: Evaluator

class AgenticVariationOperator:
    async def vary(self, target: EvolutionTarget):
        while True:
            state = await self.inspect(target)
            plan = await self.form_hypothesis(state)
            candidate = await self.modify(plan)
            result = await self.evaluate(candidate)

            if not result.correct:
                await self.diagnose(result)
                continue

            if self.should_commit(result):
                return await self.commit(candidate, result)
```

This same abstraction can operate over:

```text
code
algorithms
workflows
prompts
agent plans
skills
tool routing
model routing
scientific hypotheses
UI designs
CAD designs
video pipelines
optimization problems
```

---

# 31. General-Purpose Evolution of the Harness Itself

A higher-level system can place the AVO operator above its own agent configuration.

```text
                   HARNESS V0
                       |
                       v
                 REAL TASKS
                       |
                       v
                 TRAJECTORIES
                       |
                       v
                 FAILURE MINER
                       |
                       v
               IMPROVEMENT IDEA
                       |
                       v
                 CANDIDATE V1
                       |
                       v
                 BENCHMARKS
                       |
                       v
                 SECURITY TEST
                       |
                       v
                 SHADOW TEST
                       |
                  +----+----+
                  |         |
               better     worse
                  |         |
               promote   discard
                  |
                  v
                HARNESS V1
```

This is a practical path toward controlled recursive improvement without allowing an agent to modify production freely.

---

# 32. Recommended Data Model

### Task

```json
{
  "id": "task_123",
  "goal_id": "goal_01",
  "objective": "...",
  "constraints": {},
  "status": "running",
  "priority": 5,
  "risk": "medium",
  "success_conditions": []
}
```

### Action

```json
{
  "id": "action_123",
  "task_id": "task_123",
  "intent": "edit file",
  "tool": "shell",
  "parameters": {},
  "risk": "low",
  "expected_state": {},
  "verification": {}
}
```

### Candidate

```json
{
  "id": "candidate_40",
  "parent": "candidate_39",
  "branch": "main",
  "artifact": "git:abc123",
  "score": {},
  "correctness": true,
  "status": "committed"
}
```

### Failure

```json
{
  "id": "failure_7",
  "task": "task_123",
  "type": "stagnation",
  "context": {},
  "recovery": {},
  "resolved": true
}
```

---

# 33. Recommended Storage

A free/self-hosted implementation can use:

```text
PostgreSQL
  -> durable structured state

pgvector
  -> semantic retrieval

Filesystem / object store
  -> artifacts

Git
  -> code/candidate lineage

NATS or Redis Streams
  -> event bus

SQLite
  -> lightweight local caches
```

This is an implementation recommendation, not a claim that NVIDIA uses precisely this stack.

---

# 34. Recommended Free-First Stack

```text
LANGUAGE
Python        agent intelligence
TypeScript    web/desktop/browser integration
Rust          secure low-level runtime (optional but recommended)

MODELS
Ollama
llama.cpp
vLLM

ORCHESTRATION
custom event-driven runtime
optional LangGraph / DeepAgents components where useful

TOOLS
MCP
REST
GraphQL
CLI
Python adapters
OS APIs

BROWSER
Playwright
Chromium
CDP

MEMORY
PostgreSQL
pgvector
SQLite
Git
filesystem

EVENTS
NATS / Redis Streams

SANDBOX
Docker / Podman
WSL
VM / microVM where practical

OBSERVABILITY
OpenTelemetry
structured logs
local trace UI

EVALUATION
pytest
Playwright
benchmark runner
property tests
security regression suite
```

No paid API should be required for the architecture itself.

---

# 35. Desktop Architecture

A desktop product should expose:

```text
+--------------------------------------------------------------+
|                    AGENT CONTROL CENTER                       |
+--------------------------------------------------------------+
| Projects | Tasks | Agents | Browser | Terminal | Artifacts   |
+---------------------+----------------------+------------------+
|                     WORKSPACE              | SUPERVISOR     |
|                                            |                |
| chat / code / browser / logs / files       | progress       |
|                                            | strategy       |
|                                            | workers        |
|                                            | failures       |
|                                            | policy         |
+--------------------------------------------+----------------+
| Events | Memory | Evaluation | Security | Evolution         |
+--------------------------------------------------------------+
```

It should feel like an **agent IDE / operating system**, not only a chat window.

---

# 36. Recommended Repository Layout

```text
avo-agent-os/
│
├── apps/
│   ├── desktop/
│   ├── web/
│   └── gateway/
│
├── executive/
│   ├── goal.py
│   ├── strategy.py
│   ├── planner.py
│   └── scheduler.py
│
├── runtime/
│   ├── loop.py
│   ├── state.py
│   ├── session.py
│   ├── checkpoint.py
│   └── recovery.py
│
├── supervisor/
│   ├── monitor.py
│   ├── stagnation.py
│   ├── strategy_switch.py
│   └── intervention.py
│
├── agents/
│   ├── factory.py
│   ├── worker.py
│   ├── researcher.py
│   ├── coder.py
│   └── computer.py
│
├── models/
│   ├── adapters/
│   ├── router.py
│   ├── capabilities.py
│   └── evaluator.py
│
├── memory/
│   ├── working/
│   ├── episodic/
│   ├── semantic/
│   ├── procedural/
│   ├── failure/
│   └── lineage/
│
├── skills/
│
├── tools/
│   ├── mcp/
│   ├── api/
│   ├── shell/
│   └── browser/
│
├── computer/
│   ├── browser/
│   ├── desktop/
│   ├── accessibility/
│   ├── vision/
│   └── terminal/
│
├── security/
│   ├── policy/
│   ├── credentials/
│   ├── sandbox/
│   └── prompt_injection/
│
├── evolution/
│   ├── avo.py
│   ├── lineage.py
│   ├── candidates.py
│   ├── benchmarks.py
│   ├── hypotheses.py
│   ├── redteam.py
│   └── promotion.py
│
├── evaluation/
│   ├── graders/
│   ├── benchmarks/
│   ├── replay/
│   └── regression/
│
├── events/
│
└── observability/
```

---

# 37. Implementation Phases

## Phase 1 — AVO Core

Build:

```text
agent loop
shell
filesystem
knowledge base
scoring function
git lineage
commit gate
basic supervisor
```

Goal:

```text
agent can continuously improve a measurable target
```

## Phase 2 — Durable Long-Horizon Runtime

Add:

```text
checkpointing
event store
persistent memory
resume after restart
failure recovery
trajectory viewer
```

## Phase 3 — Universal Tools

Add:

```text
MCP
browser
APIs
terminal
file editing
structured application adapters
```

## Phase 4 — Secure Execution

Add:

```text
sandbox
policy engine
credential broker
network controls
process controls
```

## Phase 5 — Multi-Agent

Add:

```text
task DAG
worker factory
parallel execution
agent communication
evaluator agents
```

## Phase 6 — Evolution Factory

Add:

```text
failure mining
strategy generation
candidate harnesses
benchmark suites
red-team
shadow
canary
rollback
```

## Phase 7 — Universal Computer Agent

Add:

```text
browser automation
desktop control
accessibility
vision
GUI fallback
cross-platform computer abstraction
```

---

# 38. AVO-Inspired Control Plane vs Execution Plane

Keep two major planes:

```text
CONTROL PLANE
------------------------------------------------
planning
supervision
policy
scheduler
memory
lineage
model routing
evaluation orchestration
evolution

EXECUTION PLANE
------------------------------------------------
shell
browser
filesystem
applications
sandboxes
remote machines
external tools
```

This prevents agent reasoning from becoming coupled to one execution environment.

---

# 39. The Four Nested Loops

The final system should have four nested loops.

## Loop 1 — Action

```text
observe -> reason -> act -> verify
```

## Loop 2 — Task

```text
goal -> plan -> execute -> complete
```

## Loop 3 — Supervisor

```text
monitor -> detect stagnation -> redirect -> recover
```

## Loop 4 — Evolution

```text
collect trajectory
 -> hypothesize improvement
 -> build candidate
 -> benchmark
 -> promote/rollback
```

This is the most important conceptual synthesis of the architecture.

---

# 40. Benchmark Architecture

Build benchmark suites for every capability.

```text
computer-use
browser
coding
research
reasoning
memory
recovery
security
long-horizon
self-improvement
```

Track:

```text
success rate
pass@1
pass@k
action count
recovery count
cost
latency
verification coverage
security violations
regression rate
```

For evolution, maintain a fixed hidden regression set so an optimization cannot simply overfit visible evaluations.

---

# 41. What Should Be Copied from AVO

Strongly adopt:

```text
agent-as-variation-operator
full lineage awareness
persistent knowledge
execution-grounded feedback
iterative repair
correctness-gated commits
long-running operation
supervisor intervention
```

These are explicitly supported by the public AVO research. [1][2]

---

# 42. What Should NOT Be Treated as Public AVO Internals

Do not claim NVIDIA publicly disclosed:

```text
its exact proprietary model prompts
private agent source code
all internal memory algorithms
exact supervisor implementation
private orchestration code
private model routing
private benchmark harness internals
```

The public sources establish the mechanisms and high-level architecture, not every internal implementation detail.

---

# 43. Recommended Advanced Extensions Beyond AVO

For a broader agent OS, add:

```text
world model
computer-use abstraction
browser/desktop agents
MCP/A2A integration
model router
adaptive compute
multi-agent DAG
credential broker
prompt-injection defense
independent verification
artifact graph
project memory
scheduled jobs
continuous monitoring
```

AVO should be the **evolutionary core**, not the entire product.

---

# 44. Ultimate Architecture

```text
                              USER / EVENT
                                   |
                                   v
                       +-------------------------+
                       |      CONTROL PLANE      |
                       | identity / policy /     |
                       | scheduler / budget      |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |   EXECUTIVE / STRATEGY   |
                       | goal / plan / priorities |
                       +-----------+-------------+
                                   |
                 +-----------------+-----------------+
                 |                 |                 |
                 v                 v                 v
          +------------+     +------------+    +------------+
          | WORLD MODEL|     | MEMORY      |    | MODEL      |
          | state      |     | episodic    |    | FABRIC     |
          | entities   |     | semantic    |    | reasoning  |
          | relations  |     | procedural  |    | coding     |
          +-----+------+     | failure     |    | vision     |
                |            | lineage     |    +------+-----+
                |            +------+------+
                +-------------------+-------------------+
                                    |
                                    v
                       +-------------------------+
                       |       AGENT RUNTIME     |
                       | observe / reason / act  |
                       | verify / persist       |
                       +-----------+-------------+
                                   |
                     +-------------+-------------+
                     |             |             |
                     v             v             v
                 SPECIALISTS    SKILLS        TOOLS
                     |             |             |
                     +-------------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |    COMPUTER FABRIC      |
                       | API / CLI / DOM / A11Y  |
                       | browser / desktop / GUI |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |     SECURITY KERNEL     |
                       | policy / credentials    |
                       | prompt-defense / risk  |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |     SECURE RUNTIME      |
                       | sandbox / VM / network  |
                       +-----------+-------------+
                                   |
                                   v
                              ENVIRONMENT
                                   |
                                   v
                       +-------------------------+
                       | EVALUATION + EVIDENCE    |
                       | tests / graders / state |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       | EVENT + LINEAGE STORE    |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |       SUPERVISOR         |
                       | progress / stagnation    |
                       +-----------+-------------+
                                   |
                                   v
                       +-------------------------+
                       |     EVOLUTION FACTORY    |
                       | AVO variation operator   |
                       | hypothesis / benchmark  |
                       | red-team / canary        |
                       +-----------+-------------+
                                   |
                                   +------> BETTER SYSTEM
```

---

# 45. Final Design Principle

The most important takeaway from NVIDIA AVO is not a specific tool or framework.

It is this system principle:

```text
MODEL
  |
  v
AGENT
  |
  +-- memory
  +-- tools
  +-- execution feedback
  +-- lineage
  +-- verification
  +-- supervisor
  |
  v
LONG-HORIZON CAPABILITY
```

And for an advanced general-purpose system:

```text
TASK SOLVING
      +
TRAJECTORY MEMORY
      +
SUPERVISION
      +
MEASURABLE EVALUATION
      +
CONTROLLED EVOLUTION
      =
A SERIOUS SELF-IMPROVING AGENT HARNESS
```

The resulting system is not simply an LLM wrapper. It is a **persistent experimental machine** that can repeatedly act, observe, evaluate, repair, retain, and improve.

---

# 46. Sources

[1] NVIDIA Technical Blog, **“NVIDIA AVO Reaches 100% on ARC-AGI-3, Demonstrating a Frontier-Level General-Purpose Architecture for Long-Horizon Autonomous Agents,”** August 21, 2026.
https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3-demonstrating-a-frontier-level-general-purpose-architecture-for-long-horizon-autonomous-agents/

[2] Terry Chen et al. (NVIDIA), **“AVO: Agentic Variation Operators for Autonomous Evolutionary Search,”** arXiv:2603.24517, March 25, 2026.
https://arxiv.org/abs/2603.24517

[3] NVIDIA OpenShell documentation, **“How OpenShell Works.”**
https://docs.nvidia.com/openshell/about/how-it-works

[4] NVIDIA OpenShell documentation, **“Sandbox Compute Drivers.”**
https://docs.nvidia.com/openshell/dev/reference/sandbox-compute-drivers

[5] NVIDIA OpenShell documentation, **“Customize Sandbox Policies.”**
https://docs.nvidia.com/openshell/dev/sandboxes/policies

[6] NVIDIA OpenShell documentation, **“Use Policy Advisor.”**
https://docs.nvidia.com/openshell/dev/sandboxes/policy-advisor

---

# 47. Evidence Notes

### Verified from AVO paper

- AVO replaces fixed mutation/crossover-style variation pipelines with an autonomous agent operator.
- The agent has access to prior lineage, domain knowledge and the scoring function.
- A variation step can include many internal inspect/plan/edit/test/diagnose/revise actions.
- Correctness can gate the score.
- New committed versions are persisted with scores.
- A supervisor can intervene during stagnation or unproductive cycles.
- The reported kernel experiment ran seven days, explored more than 500 optimization directions and produced 40 committed versions. [2]

### Verified from NVIDIA's 2026 AVO announcement

- AVO is described as a general-purpose long-horizon autonomous-agent architecture.
- NVIDIA reports 100.00 RHAE on the ARC-AGI-3 public set using Claude Opus 5.
- The public-set result covered 25 environments and 183 levels.
- NVIDIA reports 6,624 environment actions, compared with 7,542 in the cited VISTA configuration using the same model, while cautioning that this is not a controlled ablation.
- NVIDIA explicitly frames memory, tools, feedback and recovery as system-level determinants of long-horizon capability. [1]

### Verified from OpenShell public documentation

- OpenShell separates gateway control-plane functions from sandbox-local enforcement.
- The supervisor enforces process, filesystem, network, inference and credential controls.
- Docker, Podman, Kubernetes and MicroVM-style compute drivers can exist behind a stable runtime contract.
- Static and dynamic security policy classes are separated.
- A policy advisor can propose narrow policy changes while preserving default-deny semantics. [3][4][5][6]

---

## Conclusion

A high-end agent harness inspired by NVIDIA AVO should treat **the agent itself as an adaptive search process**, not as a one-shot text generator. The minimum credible architecture is:

```text
persistent memory
+ lineage
+ execution tools
+ grounded evaluation
+ iterative repair
+ supervisor
```

A broader general-purpose agent OS should then place those AVO mechanisms inside:

```text
Executive
+ Model Fabric
+ Agent Fabric
+ Computer Fabric
+ Security Kernel
+ Evaluation
+ Event/Lineage Store
+ Evolution Factory
```

That gives a practical architecture for a self-hosted, model-agnostic, continuously improving autonomous harness while staying honest about which parts are publicly documented NVIDIA mechanisms versus engineering extensions.
