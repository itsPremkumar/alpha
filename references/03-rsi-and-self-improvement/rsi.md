# RSI-Enabled AI Agent Harness — ASI-Oriented Architecture

The wording of your project can be made more precise as:

> **Build an autonomous AI-agent harness with Recursive Self-Improvement (RSI), where the agent can observe its own performance, identify capability gaps, generate and evaluate improvements to its memory, skills, tools, workflows, prompts, and eventually its own codebase, while every improvement is isolated, benchmarked, versioned, verified, and rollbackable.**

One important distinction: **this architecture is ASI-oriented, not an actual ASI architecture in the sense of already having artificial superintelligence**. It is an engineering architecture for making an agent progressively more capable and increasingly able to improve its own agentic machinery.

I researched current work around self-improving coding agents, Darwin Gödel Machine, NVIDIA AVO, AlphaEvolve, long-horizon memory, evaluator-optimizer agents, and modern agent harness engineering. There is now strong evidence for several pieces of this design, but no demonstrated system should be treated as a proven general-purpose recursively self-improving ASI. ([arXiv][1])

---

# 1. The central idea

Your harness should **not** simply be:

```text
User
  ↓
LLM
  ↓
Tools
  ↓
Answer
```

For an RSI-oriented system, it should become:

```text
                         ┌────────────────────────────┐
                         │       HUMAN / USER          │
                         └──────────────┬─────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SUPERVISOR / EXECUTIVE                       │
│                                                                 │
│  Goal Manager → Planner → Delegator → Resource Manager          │
│        │                 │                  │                   │
│        └────────────┬────┴────────────┬─────┘                   │
│                     ▼                 ▼                         │
│              Agent / Worker Pool      Research Agents            │
│                     │                 │                         │
└─────────────────────┼─────────────────┼─────────────────────────┘
                      │                 │
                      ▼                 ▼
              ┌──────────────────────────────────┐
              │          EXECUTION LAYER         │
              │                                  │
              │ code │ shell │ browser │ MCP     │
              │ API  │ files │ Git │ DB │ apps   │
              └────────────────┬─────────────────┘
                               │
                               ▼
              ┌──────────────────────────────────┐
              │      ENVIRONMENT / REALITY       │
              │                                  │
              │ outputs │ tests │ errors │ logs  │
              │ metrics │ user feedback │ state  │
              └────────────────┬─────────────────┘
                               │
                               ▼
                 ┌────────────────────────────┐
                 │   OBSERVATION / EVALUATION │
                 │                            │
                 │ Verifier                  │
                 │ Critic                    │
                 │ Judge                     │
                 │ Benchmark Engine           │
                 │ Regression Detection       │
                 └──────────────┬─────────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │       RSI ENGINE            │
                 │                            │
                 │ Failure Mining              │
                 │ Capability Gap Analysis     │
                 │ Improvement Generator       │
                 │ Experiment Designer         │
                 │ AVO / Evolution Engine      │
                 │ Candidate Selector           │
                 └──────────────┬─────────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │    ISOLATED CANDIDATE       │
                 │         HARNESS              │
                 │                            │
                 │ code / skills / prompts     │
                 │ tools / memory / workflows  │
                 └──────────────┬─────────────┘
                                │
                                ▼
                 ┌────────────────────────────┐
                 │   VALIDATION + SAFETY       │
                 │                            │
                 │ unit tests                 │
                 │ integration tests           │
                 │ benchmark suite             │
                 │ adversarial tests            │
                 │ policy checks               │
                 │ resource limits             │
                 │ regression tests             │
                 └──────────────┬─────────────┘
                                │
                       ┌────────┴────────┐
                       ▼                 ▼
                   REJECT              ACCEPT
                       │                 │
                       ▼                 ▼
                    ARCHIVE         CANDIDATE REGISTRY
                                         │
                                         ▼
                                  CANARY / SHADOW
                                         │
                                         ▼
                                     PROMOTION
                                         │
                                         ▼
                                   NEW AGENT
                                         │
                                         └──────► next RSI cycle
```

That loop is the heart of the system.

---

# 2. The architecture should have four planes

I would structure your harness around **four major planes**.

## Plane A — Cognitive / Execution Plane

This is the agent that actually performs work.

```text
Executive
   ↓
Goal Understanding
   ↓
Planner
   ↓
Task Graph
   ↓
Worker Agents
   ↓
Tools / Environment
```

It contains:

* executive supervisor
* planner
* task decomposer
* worker agents
* researcher
* coder
* analyst
* browser agent
* reviewer
* verifier
* recovery agent
* communication agent

Anthropic's published agent patterns explicitly describe orchestrator-worker architectures for dynamically decomposing complex tasks and evaluator-optimizer loops for iterative improvement. ([Anthropic][2])

---

# 3. Plane B — Memory / Knowledge Plane

Do **not** make memory a single vector database.

Your RSI harness needs different memory classes.

```text
                    MEMORY FABRIC
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
   Working          Episodic          Semantic
   Memory           Memory            Memory
       │                 │                 │
       ▼                 ▼                 ▼
   Current task     Experiences        Knowledge
   context          trajectories       facts
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                   Procedural Memory
                         │
                  skills / policies
                         │
                         ▼
                    Meta Memory
                         │
              what improves the agent
```

I recommend these seven stores:

### 3.1 Working memory

Temporary context for the current execution.

Stores:

```text
goal
subgoals
current plan
active files
tool results
observations
current errors
temporary hypotheses
```

### 3.2 Episodic memory

Stores complete experiences.

Example:

```json
{
  "task": "fix API timeout",
  "actions": [...],
  "environment": {...},
  "failure": "...",
  "solution": "...",
  "verification": {...},
  "lessons": [...]
}
```

### 3.3 Semantic memory

Facts and persistent knowledge.

Example:

```text
FastAPI project uses MySQL
Alembic migrations are authoritative
DB MCP is read-only
production database must never be directly mutated
```

### 3.4 Procedural memory

This is especially important for RSI.

It contains:

```text
skills
workflows
tool usage patterns
recovery procedures
coding strategies
research strategies
planning policies
```

### 3.5 Reflective memory

Stores:

```text
what went wrong
why it went wrong
what should change
when the lesson applies
confidence
evidence
```

This is conceptually close to Reflexion-style learning, where the agent converts feedback into reflective text and stores it for subsequent attempts instead of changing model weights. ([arXiv][3])

### 3.6 Capability memory

Maintain an explicit map of what your agent can and cannot do.

Example:

```yaml
capabilities:

  browser:
    confidence: 0.83

  coding:
    confidence: 0.91

  visual_debugging:
    confidence: 0.61

  sql_reasoning:
    confidence: 0.75

  long_horizon_planning:
    confidence: 0.58

  autonomous_recovery:
    confidence: 0.64
```

### 3.7 Meta-memory

This is the critical RSI layer.

It stores knowledge about **how to improve the agent itself**.

For example:

```text
Which planner works better?
Which verifier catches more failures?
Which memory strategy produces better reuse?
Which prompts reduce tool errors?
Which worker topology performs better?
Which tools produce the highest success gain?
```

Recent memory research is increasingly treating long-horizon memory as a first-class systems problem rather than simply appending old conversation history. AMA-Bench found that similarity-only retrieval can lose causal/objective information and explored causal graphs plus tool-augmented retrieval. ([arXiv][4])

---

# 4. Plane C — Evaluation Plane

This is the **most important component of RSI**.

Without evaluation, self-improvement becomes:

```text
AI changes itself
       ↓
AI thinks it is better
       ↓
Unknown whether it actually improved
```

That is not reliable RSI.

Instead:

```text
CURRENT AGENT
     │
     ▼
BASELINE BENCHMARK
     │
     ▼
improvement candidate
     │
     ▼
candidate benchmark
     │
     ▼
compare
     │
 ┌───┴────┐
 │        │
better   worse
 │        │
 ▼        ▼
promote  reject
```

AlphaEvolve is particularly relevant here: it combines LLM-generated candidate programs with automated evaluators and an evolutionary selection process, using objective metrics to determine which candidates survive. ([DeepMind][5])

The Self-Improving Coding Agent work similarly evaluates the current agent, stores results, modifies the agent, and evaluates the modified version again. ([GitHub][6])

---

# 5. Your evaluation system needs multiple levels

Never use only one benchmark.

Build:

```text
                 EVALUATION MATRIX

                 ┌───────────────┐
                 │ Task Success  │
                 └───────┬───────┘
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
   Accuracy          Reliability         Efficiency
       │                 │                 │
       ▼                 ▼                 ▼
   Correctness      failure rate       tokens
   completeness     recovery           latency
   factuality       regressions        cost
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                    Safety / Policy
                         │
                         ▼
                   Overall evidence
```

I would define at least:

```text
task_success
correctness
completeness
tool_success
planning_quality
recovery_rate
memory_reuse
hallucination_rate
latency
token_cost
compute_cost
regression_rate
security_score
policy_compliance
long_horizon_success
```

Do not collapse all of these immediately into one score.

Keep the individual metrics because an apparent improvement in one dimension can regress another.

---

# 6. Plane D — Evolution / RSI Plane

This is where your architecture becomes fundamentally different from a normal agent.

The RSI engine should have:

```text
              RSI ENGINE
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
  Failure Mining       Opportunity Mining
        │                    │
        └─────────┬──────────┘
                  ▼
          Capability Gap
              Analysis
                  │
                  ▼
         Improvement Hypothesis
                  │
                  ▼
           Experiment Design
                  │
                  ▼
           Candidate Generator
                  │
                  ▼
            Candidate Agent
                  │
                  ▼
             Evaluation
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
    improvement          regression
        │                    │
        ▼                    ▼
    archive              reject
        │
        ▼
   candidate registry
```

---

# 7. The most important RSI rule

Do **not** allow your main agent to immediately rewrite the running production harness.

Instead:

```text
LIVE AGENT
   │
   │ propose improvement
   ▼
EXPERIMENT BRANCH
   │
   ▼
SANDBOX
   │
   ▼
TEST
   │
   ▼
BENCHMARK
   │
   ▼
ADVERSARIAL TEST
   │
   ▼
CANARY
   │
   ▼
PROMOTION
   │
   ▼
NEW LIVE VERSION
```

This is essentially the practical lesson from self-improving coding-agent research: self-modification is feasible when modifications are evaluated as candidate versions rather than blindly overwriting the agent. SICA explicitly runs the agent, evaluates it, modifies it, and then evaluates the updated version; it also emphasizes sandboxing because the agent can execute shell commands. ([GitHub][6])

---

# 8. What exactly should your agent be allowed to improve?

Do **not** start with unrestricted source-code rewriting.

Create an improvement hierarchy.

## RSI Level 1 — Memory improvement

```text
Improve:
- memory extraction
- retrieval
- summarization
- experience ranking
- reflection storage
- memory compression
```

Low-risk.

---

## RSI Level 2 — Prompt improvement

```text
planner prompt
research prompt
coding prompt
review prompt
verification prompt
recovery prompt
```

The system can evolve these through controlled experiments.

---

## RSI Level 3 — Skill improvement

Example:

```text
git-debugging
browser-debugging
database-analysis
research
testing
refactoring
deployment
```

The agent can create new versions of skills.

---

## RSI Level 4 — Workflow improvement

Example:

```text
Old:

plan → execute → answer

New:

plan → research → execute → test → review → repair → verify
```

This can significantly change capability without changing the underlying model.

---

## RSI Level 5 — Tool improvement

Your agent can detect:

```text
"I repeatedly fail because grep is insufficient."
```

Then create:

```text
smart_code_search
tree_sitter_query
AST_refactor
dependency_graph
semantic_diff
```

---

## RSI Level 6 — Agent topology improvement

Your system could discover:

```text
single worker
```

is inferior for a task to:

```text
researcher
     ↓
architect
     ↓
coder
     ↓
reviewer
     ↓
tester
```

Or:

```text
planner
 ↓
parallel researchers
 ↓
synthesizer
 ↓
executor
 ↓
independent verifier
```

The topology itself becomes evolvable.

---

# 9. RSI Level 7 — Self-code improvement

This is where you start approaching the architecture described in self-improving coding-agent and Darwin Gödel Machine research.

The agent should be able to modify:

```text
planner.py
executor.py
memory.py
tool_router.py
retrieval.py
verification.py
scheduler.py
agent prompts
skill loader
orchestrator
```

But through:

```text
branch
→ build
→ tests
→ benchmark
→ adversarial evaluation
→ compare
→ archive
→ promote
```

The Darwin Gödel Machine goes beyond a single sequential self-edit loop by maintaining an archive of generated agents and exploring multiple evolutionary branches. Its published experiments reported improvements on coding benchmarks and specifically described improvements to tools, context management, and peer-review mechanisms. ([arXiv][7])

---

# 10. Your harness should use an evolutionary archive

Instead of:

```text
v1 → v2 → v3 → v4
```

use:

```text
                     v0
                  /  |  \
                 /   |   \
               v1   v2   v3
              / \    |   / \
             v4 v5   v6 v7 v8
                  \    /
                   v9
```

Each candidate has:

```yaml
agent_id:
parent_id:
generation:
changes:
capabilities_changed:
benchmark_results:
regressions:
cost:
latency:
safety_results:
evidence:
status:
```

This is directly inspired by the open-ended archive approach in DGM and the candidate/evaluator population approach of AlphaEvolve. ([arXiv][7])

---

# 11. Add AVO-style agentic evolution

This is especially relevant to the architecture you have been researching.

NVIDIA's 2026 AVO work treats the agent itself as the **variation operator**, rather than using a fixed mutation rule. The agent can inspect lineage, domain knowledge, execution feedback, then propose, repair, critique, and verify changes. ([arXiv][8])

For your system:

```text
                EVOLUTION POPULATION
                         │
                         ▼
                   Parent Agent
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
         mutate       redesign      hybridize
            │            │            │
            └────────────┼────────────┘
                         ▼
                  Agentic Operator
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       inspect        implement       repair
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                      critique
                         │
                         ▼
                      verify
                         │
                         ▼
                     candidate
```

The important thing is:

> **Your agent is not merely generating code; it is controlling the improvement experiment.**

That is a much more powerful concept.

---

# 12. Your RSI engine needs an Improvement Hypothesis system

Never let the agent make vague changes such as:

> "I think this makes the planner smarter."

Require:

```yaml
hypothesis:
  problem: "planner loses dependencies across long tasks"

  evidence:
    - benchmark: long_horizon_07
      failure_rate: 0.38
    - trace_pattern: "plan context dropped after recovery"

  proposed_change:
    type: "architecture"
    component: "planning_state"

  expected_effect:
    planning_success: "+10%"
    recovery_success: "+8%"

  risks:
    - increased_context
    - latency

  validation:
    required:
      - regression_suite
      - long_horizon_suite
      - adversarial_suite
```

This converts self-improvement from arbitrary modification into **scientific experimentation**.

---

# 13. Create a Capability Gap Detector

This component continuously asks:

```text
Why did we fail?
```

But divide failure into categories.

```text
FAILURE
  │
  ├── Reasoning failure
  ├── Planning failure
  ├── Memory failure
  ├── Retrieval failure
  ├── Tool failure
  ├── Environment failure
  ├── Code failure
  ├── Verification failure
  ├── Context failure
  ├── Coordination failure
  └── Knowledge failure
```

Then map:

```text
failure
   ↓
root cause
   ↓
capability gap
   ↓
potential intervention
```

Example:

```text
5 failures involving database tasks

       ↓

agent guesses schema

       ↓

knowledge/retrieval gap

       ↓

create DB-schema introspection skill

       ↓

benchmark

       ↓

promote
```

---

# 14. Build a Failure Knowledge Graph

This is something I strongly recommend for your system.

Instead of saving only text:

```text
"planner failed"
```

store:

```text
Task
 ↓
Failure
 ↓
Cause
 ↓
Missing Capability
 ↓
Candidate Improvement
 ↓
Experiment
 ↓
Result
 ↓
Learned Policy
```

Example:

```text
Task: fix payment bug
        │
        ▼
Failure: changed wrong service
        │
        ▼
Cause: dependency graph not inspected
        │
        ▼
Missing capability:
architecture discovery
        │
        ▼
Candidate:
dependency_graph_tool
        │
        ▼
Experiment:
20 benchmark tasks
        │
        ▼
Result:
+14% task success
        │
        ▼
Promoted skill
```

This gives the RSI engine causal information rather than merely semantic similarity.

That direction is consistent with recent work arguing that agent memory systems need causal and objective information, not just similarity retrieval. ([arXiv][4])

---

# 15. The verifier should be independent

One dangerous architecture is:

```text
Agent:
"I fixed it."

Same Agent:
"Yes, looks good."
```

Instead:

```text
Worker
  ↓
Independent verifier
  ↓
Tests
  ↓
Runtime observation
  ↓
Benchmark
```

Use multiple evaluators for difficult changes:

```text
Evaluator A → correctness
Evaluator B → security
Evaluator C → architecture
Evaluator D → regression
Evaluator E → user objective
```

Anthropic describes multiple evaluators and voting as useful patterns for specialized evaluation and review, alongside the evaluator-optimizer workflow. ([Anthropic][2])

---

# 16. Your verification system should have evidence, not opinions

Every completed task should produce an:

```yaml
evidence_matrix:
  objective:
  acceptance_criteria:

  evidence:
    - criterion:
      status:
      command:
      output:
      artifact:
      confidence:

  unresolved:
    - ...

  regression:
    - ...
```

Example:

```text
Requirement:
API response < 800ms

Evidence:
load test → 643ms

Requirement:
no regression

Evidence:
182/182 regression tests passed

Requirement:
UI works

Evidence:
browser workflow completed

Requirement:
database unchanged

Evidence:
read-only DB diff = empty
```

This is much stronger than:

```text
"Everything looks good."
```

OpenAI's recent harness-engineering work emphasizes making application behavior observable and legible to agents, including access to logs, metrics, traces, UI state and executable plans. ([OpenAI][9])

---

# 17. Make the environment agent-legible

This is extremely important for your harness.

Your agent should be able to inspect:

```text
source code
architecture
documentation
logs
metrics
traces
screenshots
DOM
terminal
git history
tests
benchmarks
dependency graph
database schema
environment state
```

OpenAI's 2026 harness-engineering report describes this exact direction: agents are made more capable by exposing application UI, logs, metrics, traces and repository knowledge directly to them, while enforcing architecture through mechanical constraints. ([OpenAI][9])

So your harness should expose an:

```text
WORLD MODEL
```

containing:

```text
filesystem state
repository state
runtime state
process state
network state
application state
database metadata
agent state
task state
historical state
```

---

# 18. Repository knowledge should become your agent's operating system

Do not put the whole architecture into one giant prompt.

Use:

```text
AGENTS.md
ARCHITECTURE.md

docs/
 ├── principles/
 ├── architecture/
 ├── skills/
 ├── plans/
 ├── research/
 ├── benchmarks/
 ├── runbooks/
 ├── decisions/
 ├── failures/
 ├── capabilities/
 └── evolution/
```

This matches a key lesson in OpenAI's harness work: a short entry document can function as a map into a structured knowledge base rather than becoming a huge instruction manual; they also describe mechanically checking documentation and architectural invariants. ([OpenAI][9])

---

# 19. Add architectural invariants

The RSI system must not be allowed to destroy the architecture while "improving" one benchmark.

Create rules such as:

```text
executor cannot directly modify safety kernel

workers cannot access secrets

evaluation environment cannot access production

memory writes must be typed

tool calls require schemas

irreversible actions require authorization

candidate agents cannot modify evaluator definitions

candidate agents cannot modify safety policy

promoted agents must retain rollback capability
```

This is how you make the system **evolvable without becoming structurally chaotic**.

OpenAI describes strict architectural boundaries and mechanically enforced invariants as important in high-throughput agent-generated software. ([OpenAI][9])

---

# 20. Separate the immutable kernel from the evolvable layer

This is one of the most important design decisions.

## Immutable Core

```text
Safety kernel
Identity
Authorization
Sandbox policy
Secret management
Audit logging
Version registry
Rollback
Promotion policy
Resource limits
Emergency stop
Evaluator integrity
```

## Evolvable Layer

```text
Planner
Worker topology
Prompts
Skills
Tools
Memory
Retrieval
Reflection
Recovery strategies
Task decomposition
Routing
Optimization strategies
Agent code
```

Therefore:

```text
                   HARNESS
                      │
          ┌───────────┴───────────┐
          │                       │
     IMMUTABLE CORE         EVOLVABLE SYSTEM
          │                       │
      cannot self-edit       can self-edit
```

This is much safer than allowing the agent to modify everything.

---

# 21. Promotion pipeline

Every RSI candidate should go through:

```text
PROPOSE
   ↓
STATIC CHECK
   ↓
BUILD
   ↓
UNIT TEST
   ↓
INTEGRATION TEST
   ↓
REGRESSION BENCHMARK
   ↓
CAPABILITY BENCHMARK
   ↓
ADVERSARIAL EVAL
   ↓
RESOURCE EVAL
   ↓
SECURITY EVAL
   ↓
SHADOW RUN
   ↓
CANARY
   ↓
PROMOTION
```

And:

```text
promotion != "model says it is good"
```

Promotion must be based on evidence.

---

# 22. Candidate scoring

Don't use only one scalar.

Keep a vector:

```python
CandidateScore = {
    "task_success": ...,
    "correctness": ...,
    "recovery": ...,
    "memory": ...,
    "tool_reliability": ...,
    "latency": ...,
    "cost": ...,
    "security": ...,
    "regression": ...,
}
```

Then use constraints:

```text
Required:
security >= threshold
regression <= threshold
correctness >= threshold
```

Then optimize secondary objectives:

```text
success
↑
cost
↓
latency
↓
recovery
↑
memory
↑
```

This prevents an agent from "improving" by sacrificing critical capabilities.

---

# 23. Add a Pareto archive

Instead of keeping only one winner:

```text
Candidate A
high accuracy / high cost

Candidate B
medium accuracy / low cost

Candidate C
high recovery / medium latency
```

Keep all useful non-dominated candidates.

Then the system can select based on the environment.

This is especially valuable for your future multi-agent/company architecture.

---

# 24. Add automatic experiment generation

Eventually the RSI engine should be able to say:

```text
Observed:

long-horizon tasks fail after ~20 steps.

Hypotheses:

H1: context loss
H2: bad memory retrieval
H3: planner reset
H4: recovery loop failure

Experiments:

E1 → increase state summarization
E2 → causal memory
E3 → persistent task graph
E4 → explicit recovery state
```

Then automatically run them.

That creates:

```text
Observe
  ↓
Hypothesize
  ↓
Experiment
  ↓
Measure
  ↓
Learn
  ↓
Modify
  ↓
Re-test
```

This is the real foundation of RSI.

---

# 25. Recursive self-improvement loop

Your production RSI loop should therefore be:

```text
┌─────────────────────────────────────────────────┐
│                  RSI CYCLE                      │
└─────────────────────────────────────────────────┘

     1. OBSERVE
          ↓
     2. COLLECT TRAJECTORIES
          ↓
     3. EVALUATE PERFORMANCE
          ↓
     4. MINE FAILURES
          ↓
     5. IDENTIFY CAPABILITY GAPS
          ↓
     6. GENERATE IMPROVEMENT HYPOTHESES
          ↓
     7. SELECT EXPERIMENT
          ↓
     8. MODIFY CANDIDATE
          ↓
     9. RUN CANDIDATE
          ↓
    10. VERIFY
          ↓
    11. BENCHMARK
          ↓
    12. ADVERSARIAL TEST
          ↓
    13. COMPARE AGAINST PARENT
          ↓
    14. ARCHIVE
          ↓
    15. CANARY
          ↓
    16. PROMOTE / REJECT
          ↓
    17. UPDATE MEMORY
          ↓
    18. START NEXT CYCLE
```

---

# 26. Then make RSI itself improve

There is another level.

Normal RSI:

```text
Agent improves its capabilities.
```

Higher-order RSI:

```text
Agent improves its ability to improve itself.
```

For example:

```text
v1:
benchmark manually designed

v2:
agent generates additional benchmarks

v3:
agent identifies weak benchmark areas

v4:
agent designs better evaluators

v5:
agent designs better improvement experiments

v6:
agent improves the RSI engine
```

This is one of the key ideas in DGM: self-improvements can improve not only task capabilities but also the agent's ability to modify itself. ([arXiv][7])

---

# 27. Recursive improvement levels

I would define your system as:

```text
RSI-0
No self-improvement

RSI-1
memory improvement

RSI-2
prompt / skill improvement

RSI-3
workflow improvement

RSI-4
tool improvement

RSI-5
agent topology improvement

RSI-6
harness code improvement

RSI-7
evaluation improvement

RSI-8
RSI-engine improvement

RSI-9
architecture evolution

RSI-10
model/training optimization
```

Do not attempt RSI-10 first.

Your initial architecture should target approximately:

```text
RSI-1 → RSI-6
```

with a carefully isolated path toward RSI-7/8.

---

# 28. Multi-agent architecture

Your supervisor should not perform everything itself.

Use an organization such as:

```text
                    SUPREME EXECUTIVE
                          │
             ┌────────────┼─────────────┐
             ▼            ▼             ▼
         STRATEGY      RESEARCH       OPERATIONS
             │            │             │
             │            │        ┌────┴────┐
             │            │        ▼         ▼
             │            │      CODER     TOOL AGENT
             │            │
             │        ┌───┼────┐
             │        ▼   ▼    ▼
             │      WEB  PAPERS DATA
             │
             ▼
        PLANNING AGENT
             │
      ┌──────┼──────┐
      ▼      ▼      ▼
    worker worker worker
      │      │      │
      └──────┼──────┘
             ▼
        REVIEW BOARD
             │
      ┌──────┼──────┐
      ▼      ▼      ▼
   verifier security critic
      │      │      │
      └──────┼──────┘
             ▼
         PROMOTION
```

Your Hermes-based system can use Hermes as the executive/supervisory layer, while specialized agents perform research, coding, analysis, validation and evolution.

---

# 29. Add an Agent Registry

Every agent should be a versioned object:

```yaml
agent:
  id: coding-agent
  version: 17

  model:
    provider: ...

  prompt:
    version: 42

  skills:
    - git
    - python
    - testing
    - debugging

  tools:
    - shell
    - filesystem
    - browser

  memory:
    policy: hybrid-v4

  planner:
    version: 11

  verifier:
    version: 8

  capabilities:
    coding: 0.91
    debugging: 0.84
```

Now your entire agent becomes reproducible.

---

# 30. Every task becomes a reproducible trajectory

Store:

```text
task_id
agent_version
prompt_version
skill_versions
tool_versions
memory_snapshot
environment_snapshot
model
temperature
actions
observations
tool outputs
failures
recovery attempts
final result
evaluation
```

Without this, you cannot properly determine whether an improvement worked.

---

# 31. Create a "world simulator" for RSI

Before modifying the live system:

```text
candidate
   ↓
synthetic tasks
   ↓
historical failures
   ↓
real benchmark
   ↓
stress tests
```

Your own historical failures become a permanent test suite.

This is extremely valuable.

Every real failure should create:

```text
failure
  ↓
reproduction
  ↓
regression test
```

So your system becomes stronger over time.

---

# 32. Benchmark generation should eventually become self-improving

Your RSI engine should eventually maintain:

```text
Benchmark Corpus
       │
       ├── coding
       ├── research
       ├── reasoning
       ├── browser
       ├── tool use
       ├── memory
       ├── planning
       ├── recovery
       ├── long horizon
       └── self-improvement
```

Then add:

```text
Benchmark Generator
        ↓
Find weak area
        ↓
Generate task
        ↓
Validate task
        ↓
Add to benchmark
```

The SICA project itself explicitly identifies automatically curating/building more benchmarks as a direction for further work. ([GitHub][6])

That makes benchmark evolution part of RSI.

---

# 33. Your harness should learn from every failure

The pipeline should be:

```text
Failure
  ↓
Root Cause Analysis
  ↓
Generate Lesson
  ↓
Validate Lesson
  ↓
Store Experience
  ↓
Update Skill / Policy
  ↓
Regression Test
```

Do not immediately convert every failure into a rule.

Otherwise your memory becomes polluted.

Use:

```text
Candidate Lesson
      ↓
Repeated Evidence
      ↓
Generalization
      ↓
Promotion
```

---

# 34. Confidence-aware learning

Every learned item should contain:

```yaml
lesson:
  statement: "Use dependency graph before multi-file refactor"

  evidence_count: 17

  successful_reuse: 13

  failures_after_learning: 2

  confidence: 0.87

  scope:
    language: python
    task_type: refactoring
```

This prevents one unusual event from permanently changing the agent.

---

# 35. Add a "don't learn" mechanism

The harness should be able to say:

```text
This outcome was caused by:
- random external failure
- temporary network issue
- invalid user input
- one-off environment bug
- evaluator instability
```

Therefore:

```text
NOT ALL EXPERIENCES → LEARNING
```

You need a causal learning filter.

---

# 36. Long-horizon execution architecture

For your ASI-oriented harness, don't rely on one enormous context window.

Use:

```text
Goal
 ↓
Task Graph
 ↓
Persistent State
 ↓
Checkpoint
 ↓
Continue
```

Each task has:

```text
state
dependencies
artifacts
evidence
next_actions
```

Example:

```yaml
task:
  id: T17
  status: executing

  dependencies:
    - T11
    - T14

  completed:
    - inspect_repo
    - reproduce_bug

  next:
    - patch_service

  artifacts:
    - reproduction.log
    - diagnosis.md
```

This lets the agent survive very long tasks.

Recent long-horizon memory research is moving toward bounded, typed retrieval and explicit memory contracts instead of blindly accumulating full transcripts. ([arXiv][10])

---

# 37. Recovery should be a first-class subsystem

Don't make failure equal termination.

Use:

```text
Failure
 ↓
Classify
 ↓
Retry?
 ↓
Change strategy?
 ↓
Change worker?
 ↓
Research?
 ↓
Rollback?
 ↓
Escalate?
```

Example:

```text
Tool failure
   ↓
retry

same failure
   ↓
alternative tool

still failure
   ↓
research

still failure
   ↓
spawn specialist

still failure
   ↓
architect review

still failure
   ↓
human escalation
```

---

# 38. The agent should know when to stop

You need a formal termination controller:

```text
GOAL ACHIEVED?
     │
   yes ─────────────► verify → final
     │
     no
     ▼
PROGRESS?
     │
   yes ─────────────► continue
     │
     no
     ▼
CHANGE STRATEGY?
     │
   yes ─────────────► recovery
     │
     no
     ▼
ESCALATE / STOP
```

This is important because an RSI system without stop conditions can burn compute indefinitely. Anthropic explicitly notes stopping conditions such as maximum iterations as an important control for autonomous agent loops. ([Anthropic][2])

---

# 39. Cost and resource awareness

Your executive should have a resource manager:

```text
token budget
time budget
CPU
RAM
GPU
API budget
parallel workers
tool calls
network budget
```

Then planning becomes:

```text
Expected value / cost
```

For example:

```text
Simple task:
small model + few workers

Complex research:
large model + parallel researchers

Self-improvement:
strong model + many evaluators
```

---

# 40. Model routing

Don't tie the whole system to one LLM.

Use:

```text
                 MODEL ROUTER
                     │
      ┌──────────────┼───────────────┐
      ▼              ▼               ▼
   cheap          balanced          strong
   model            model            model
      │              │               │
 routine tasks    normal tasks    architecture/
                                   RSI
```

You can also use:

```text
planner model
worker model
critic model
judge model
research model
code model
```

This is consistent with AlphaEvolve's use of different Gemini models for breadth versus depth in the search process. ([DeepMind][5])

---

# 41. The RSI controller itself should be an agent

Eventually:

```text
                    META-AGENT
                       │
      ┌────────────────┼─────────────────┐
      ▼                ▼                 ▼
 Observe            Diagnose          Experiment
      │                │                 │
      └────────────────┼─────────────────┘
                       ▼
                  Improve Agent
                       │
                       ▼
                  Evaluate
                       │
                       ▼
                 Update Strategy
```

The meta-agent should not directly replace the live agent.

It creates candidate versions.

---

# 42. The ultimate recursive loop

Your target architecture should look like this:

```text
                    USER GOAL
                       │
                       ▼
                EXECUTIVE AGENT
                       │
                       ▼
                 TASK EXECUTION
                       │
                       ▼
                  ENVIRONMENT
                       │
                       ▼
                 OBSERVATIONS
                       │
                       ▼
                    EVALS
                       │
                       ▼
                   FAILURES
                       │
                       ▼
               CAPABILITY GAPS
                       │
                       ▼
              RSI META-AGENT
                       │
                       ▼
             IMPROVEMENT HYPOTHESIS
                       │
                       ▼
               AGENTIC EVOLUTION
                       │
                       ▼
               CANDIDATE AGENTS
                       │
                       ▼
                 BENCHMARK LAB
                       │
                       ▼
                 SAFETY CHECK
                       │
                       ▼
                   ARCHIVE
                       │
                       ▼
                   PROMOTION
                       │
                       ▼
                 BETTER AGENT
                       │
                       └─────────────┐
                                     │
                                     ▼
                           NEXT GENERATION
                                     │
                                     ▼
                              MORE CAPABLE RSI
```

This is the architecture I would build toward.

---

# 43. AVO + DGM + SICA + AlphaEvolve synthesis

The most useful way to think about the current research is:

| Research direction               | What to take into your harness                                                      |
| -------------------------------- | ----------------------------------------------------------------------------------- |
| **SICA**                         | Evaluate → modify agent → evaluate again                                            |
| **Reflexion**                    | Store useful reflective experience                                                  |
| **Self-Refine**                  | Iterative generation → critique → refinement                                        |
| **AlphaEvolve**                  | Candidate population + objective evaluator + evolutionary selection                 |
| **Darwin Gödel Machine**         | Archive of agent versions + branching self-improvement                              |
| **AVO**                          | Agent becomes the variation operator; inspect → modify → repair → critique → verify |
| **Modern harness engineering**   | Agent-legible repo, observability, executable plans, mechanical architecture rules  |
| **Long-horizon memory research** | Typed memory, causal information, bounded context, tool-assisted retrieval          |

These are complementary rather than mutually exclusive. ([arXiv][3])

---

# 44. Recommended directory structure for your project

For your Hermes-based harness, I would structure the repository approximately like this:

```text
asi-harness/
│
├── core/
│   ├── executive/
│   ├── planner/
│   ├── orchestrator/
│   ├── scheduler/
│   ├── state/
│   └── runtime/
│
├── agents/
│   ├── researcher/
│   ├── coder/
│   ├── analyst/
│   ├── reviewer/
│   ├── verifier/
│   ├── recovery/
│   └── specialist/
│
├── tools/
│   ├── filesystem/
│   ├── shell/
│   ├── browser/
│   ├── git/
│   ├── database/
│   ├── search/
│   └── mcp/
│
├── memory/
│   ├── working/
│   ├── episodic/
│   ├── semantic/
│   ├── procedural/
│   ├── reflective/
│   ├── capability/
│   └── meta/
│
├── knowledge/
│   ├── architecture/
│   ├── skills/
│   ├── research/
│   ├── decisions/
│   └── runbooks/
│
├── evaluation/
│   ├── benchmarks/
│   ├── regression/
│   ├── adversarial/
│   ├── judges/
│   ├── metrics/
│   └── evidence/
│
├── rsi/
│   ├── observer/
│   ├── failure_miner/
│   ├── capability_gap/
│   ├── hypothesis/
│   ├── experiment/
│   ├── mutation/
│   ├── candidate_builder/
│   ├── evaluator/
│   ├── archive/
│   └── promoter/
│
├── evolution/
│   ├── population/
│   ├── lineage/
│   ├── selection/
│   ├── crossover/
│   ├── mutation/
│   └── pareto/
│
├── safety/
│   ├── policy/
│   ├── permissions/
│   ├── sandbox/
│   ├── secrets/
│   ├── audit/
│   ├── rollback/
│   └── emergency_stop/
│
├── observability/
│   ├── traces/
│   ├── logs/
│   ├── metrics/
│   └── dashboards/
│
├── registry/
│   ├── agents/
│   ├── skills/
│   ├── tools/
│   ├── models/
│   └── versions/
│
└── experiments/
    ├── candidates/
    ├── runs/
    ├── results/
    └── archives/
```

---

# 45. Core control loop pseudocode

At a conceptual level:

```python
while system_running:

    task = executive.receive_goal()

    state = world_model.observe()

    plan = planner.create_plan(
        task=task,
        state=state,
        memory=memory.retrieve(task)
    )

    result = executor.run(plan)

    evidence = verifier.collect_evidence(result)

    evaluation = evaluator.evaluate(
        task=task,
        result=result,
        evidence=evidence
    )

    memory.record(
        task=task,
        result=result,
        evaluation=evaluation
    )

    if evaluation.failed:

        diagnosis = failure_miner.analyze(
            trajectory=result,
            evaluation=evaluation
        )

        gap = capability_detector.detect(diagnosis)

        hypothesis = rsi.generate_hypothesis(
            gap=gap,
            history=memory.retrieve_similar(gap)
        )

        candidate = evolution.create_candidate(
            hypothesis=hypothesis
        )

        candidate_result = experiment.run(candidate)

        candidate_eval = benchmark.compare(
            parent=current_agent,
            candidate=candidate,
            result=candidate_result
        )

        if promotion_policy.accept(candidate_eval):

            archive.store(candidate)
            canary.run(candidate)
            registry.promote(candidate)

        else:
            archive.reject(candidate)
```

---

# 46. The RSI state machine

Use an explicit state machine rather than a huge prompt.

```text
IDLE
 ↓
OBSERVE
 ↓
PLAN
 ↓
EXECUTE
 ↓
VERIFY
 ├──────────────► COMPLETE
 │
 ▼
FAILURE
 ↓
DIAGNOSE
 ↓
RECOVER
 ├──────────────► EXECUTE
 │
 ▼
CAPABILITY_GAP
 ↓
GENERATE_HYPOTHESIS
 ↓
BUILD_CANDIDATE
 ↓
EVALUATE
 ├──────────────► REJECT
 │
 ▼
CANARY
 ↓
PROMOTE
 ↓
LEARN
 ↓
IDLE
```

---

# 47. Your most important interfaces

Keep interfaces clean.

```text
Executive
   ├── Planner
   ├── Memory
   ├── AgentRegistry
   ├── ToolRouter
   └── Evaluator

RSI
   ├── Observer
   ├── FailureMiner
   ├── CapabilityDetector
   ├── ExperimentEngine
   ├── EvolutionEngine
   ├── BenchmarkEngine
   └── PromotionController

Safety
   ├── PermissionEngine
   ├── SandboxManager
   ├── AuditLogger
   ├── RollbackManager
   └── EmergencyStop
```

---

# 48. One architectural principle I would make non-negotiable

### The agent should never be the sole authority for judging its own improvement.

Use:

```text
agent proposes
        ↓
environment executes
        ↓
tests measure
        ↓
independent evaluators judge
        ↓
policy decides
        ↓
registry promotes
```

That is the difference between:

**self-modification**

and

**measurable recursive self-improvement**.

---

# 49. What I would implement first in your Hermes harness

Given your existing direction around Hermes, multi-agent orchestration, tools, skills, memory, verification and AVO/RSI, I would build it in this order:

```text
PHASE 1
Observability
    ↓
trajectory recorder
    ↓
evaluation framework

PHASE 2
Memory
    ↓
episodic + procedural + reflective
    ↓
experience retrieval

PHASE 3
Verifier
    ↓
evidence matrix
    ↓
regression system

PHASE 4
Failure Miner
    ↓
capability-gap detector

PHASE 5
RSI Proposal Engine
    ↓
hypothesis generation
    ↓
experiment design

PHASE 6
Candidate Harness
    ↓
Git branch / isolated environment

PHASE 7
Automated Benchmarking
    ↓
parent vs candidate

PHASE 8
Agentic Evolution
    ↓
AVO-like mutation/repair/critique

PHASE 9
Population Archive
    ↓
DGM-like lineage tree

PHASE 10
Automatic Benchmark Generation

PHASE 11
Self-improving RSI engine

PHASE 12
Architecture-level evolution
```

This sequence is much more practical than immediately giving Hermes unrestricted access to rewrite itself.

---

# 50. Your final target architecture

The system you ultimately want is best thought of as five nested loops:

```text
┌───────────────────────────────────────────────────────┐
│ LOOP 5 — ARCHITECTURE EVOLUTION                       │
│                                                       │
│    Improve the architecture itself                    │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │ LOOP 4 — RSI EVOLUTION                         │  │
│  │                                                 │  │
│  │  Improve the way the system improves            │  │
│  │                                                 │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │ LOOP 3 — AGENT EVOLUTION                 │  │  │
│  │  │                                          │  │  │
│  │  │ improve code / workflows / topology      │  │  │
│  │  │                                          │  │  │
│  │  │ ┌────────────────────────────────────┐  │  │  │
│  │  │ │ LOOP 2 — EXPERIENCE LEARNING       │  │  │  │
│  │  │ │                                    │  │  │  │
│  │  │ │ memory / reflection / skills       │  │  │  │
│  │  │ │                                    │  │  │  │
│  │  │ │ ┌──────────────────────────────┐  │  │  │  │
│  │  │ │ │ LOOP 1 — TASK EXECUTION      │  │  │  │  │
│  │  │ │ │                              │  │  │  │  │
│  │  │ │ │ plan → act → observe → test  │  │  │  │  │
│  │  │ │ └──────────────────────────────┘  │  │  │  │
│  │  │ └────────────────────────────────────┘  │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
```

That is the **ASI-oriented harness architecture** I recommend you pursue: not simply a smarter prompt, but a system that can **act → observe → evaluate → learn → design experiments → modify itself → verify the modification → preserve successful versions → and repeat**, with the evaluation and safety machinery outside the agent's unilateral control.

The current research supports many of these individual mechanisms. SICA demonstrates autonomous self-editing of a coding agent; DGM adds open-ended lineage/archive evolution; AlphaEvolve demonstrates evolutionary candidate generation with automated evaluation; AVO pushes the idea further by making an agent the variation operator; and modern harness work emphasizes persistent repository knowledge, observability, strict boundaries and feedback loops. ([arXiv][11])

### The key design equation

```text
ASI-oriented Harness

=
Executive Intelligence
+ Multi-Agent Coordination
+ Tools
+ World Model
+ Persistent Memory
+ Reflection
+ Ground-Truth Feedback
+ Independent Verification
+ Evaluation
+ Evolution
+ Candidate Archive
+ RSI Engine
+ Architecture Governance
+ Sandboxed Self-Modification
+ Rollback
```

And the fundamental RSI equation is:

```text
CAPABILITY GAIN
    =
    better execution
  + better memory
  + better tools
  + better workflows
  + better coordination
  + better evaluation
  + better recovery
  + better self-improvement
```

—not merely a better underlying model.

### Sources

OpenAI's 2026 harness-engineering report: ([OpenAI][9])

Google DeepMind AlphaEvolve: ([DeepMind][5])

Darwin Gödel Machine: ([arXiv][7])

NVIDIA AVO: ([arXiv][8])

Self-Improving Coding Agent: ([OpenReview][12])

Anthropic agent architecture patterns: ([Anthropic][2])

Reflexion / self-reflective learning: ([arXiv][3])

Long-horizon agent memory: ([arXiv][4])

[1]: https://arxiv.org/abs/2504.15228?utm_source=chatgpt.com "A Self-Improving Coding Agent"
[2]: https://www.anthropic.com/engineering/building-effective-agents "Building Effective AI Agents \ Anthropic"
[3]: https://arxiv.org/abs/2303.11366?utm_source=chatgpt.com "Reflexion: Language Agents with Verbal Reinforcement Learning"
[4]: https://arxiv.org/abs/2602.22769 "[2602.22769] AMA-Bench: Evaluating Long-Horizon Memory for Agentic Applications"
[5]: https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/?utm_source=chatgpt.com "AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms — Google DeepMind"
[6]: https://github.com/MaximeRobeyns/self_improving_coding_agent?utm_source=chatgpt.com "GitHub - MaximeRobeyns/self_improving_coding_agent: A coding agent framework, that works on its own codebase. · GitHub"
[7]: https://arxiv.org/abs/2505.22954 "[2505.22954] Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents"
[8]: https://arxiv.org/abs/2603.24517 "[2603.24517] AVO: Agentic Variation Operators for Autonomous Evolutionary Search"
[9]: https://openai.com/index/harness-engineering/ "Harness engineering: leveraging Codex in an agent-first world | OpenAI"
[10]: https://arxiv.org/abs/2607.02255?utm_source=chatgpt.com "AgenticSTS: A Bounded-Memory Testbed for Long-Horizon LLM Agents"
[11]: https://arxiv.org/abs/2504.15228 "A Self-Improving Coding Agent"
[12]: https://openreview.net/pdf?id=rShJCyLsOr&utm_source=chatgpt.com "Published as a workshop paper at ICLR 2025 SSI-FM"



