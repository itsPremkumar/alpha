For your harness, **self-improvement should be a complete closed-loop system, not just “let the agent rewrite its prompt.”** The strongest current approaches combine persistent experience, measurable evaluation, controlled mutation, isolated execution, automated verification, selection, versioning, and rollback. Darwin Gödel Machine, AlphaEvolve, ShinkaEvolve, Reflexion, Self-Refine, EvoAgentX and related work all cover different parts of this loop. ([arXiv][1])

# Self-Improvement System for Your Harness

The overall architecture I recommend is:

```text
                     ┌─────────────────────────┐
                     │      USER / GOAL         │
                     └────────────┬────────────┘
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │   BASE AGENT / SYSTEM   │
                     │ Planner + Executor      │
                     └────────────┬────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
          ┌─────────────────┐         ┌─────────────────┐
          │  EXPERIENCE     │         │  OBSERVABILITY  │
          │  & MEMORY       │         │  Telemetry      │
          └────────┬────────┘         └────────┬────────┘
                   └────────────┬──────────────┘
                                ▼
                     ┌─────────────────────────┐
                     │ PERFORMANCE / EVALUATOR │
                     │ Tests + Benchmarks      │
                     │ Quality + Cost + Safety │
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │ FAILURE / GAP ANALYZER  │
                     │ Why did it fail?        │
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │ IMPROVEMENT PROPOSER    │
                     │ Generate hypotheses     │
                     └────────────┬────────────┘
                                  ▼
            ┌────────────────────────────────────────┐
            │         EVOLUTION ENGINE               │
            │ Prompt / Skill / Workflow / Code /     │
            │ Tool / Memory / Model / Agent topology │
            └────────────────────┬───────────────────┘
                                 ▼
                     ┌─────────────────────────┐
                     │   SANDBOX / BRANCH      │
                     │ isolated experiment     │
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │ VERIFY CANDIDATE        │
                     │ tests + benchmarks      │
                     │ regression + security   │
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │   SELECT / COMPARE      │
                     │ baseline vs candidate   │
                     └───────┬─────────┬───────┘
                             │         │
                         improve    worse/unsafe
                             │         │
                             ▼         ▼
                       ┌─────────┐  ┌──────────┐
                       │RELEASE  │  │ROLLBACK  │
                       └────┬────┘  └──────────┘
                            │
                            ▼
                     ┌─────────────────────────┐
                     │ NEW GENERATION / MODEL  │
                     │ becomes next baseline   │
                     └─────────────────────────┘
```

## 1. Self-improvement needs an explicit objective system

Before allowing the agent to change itself, it needs to know **what “better” means**.

Your harness should have:

| Component             | Purpose                                  |
| --------------------- | ---------------------------------------- |
| Goal definition       | What the system is trying to accomplish  |
| Success criteria      | Conditions required for completion       |
| Quality metrics       | How good the result is                   |
| Constraints           | Things it must never violate             |
| Cost budget           | Token/API/CPU/time limits                |
| Reliability target    | Maximum acceptable failure rate          |
| Safety constraints    | Actions that require approval            |
| Regression criteria   | Things that cannot become worse          |
| Improvement threshold | Minimum gain required to accept a change |

Example:

```yaml
objective:
  primary:
    - task_success

  secondary:
    - correctness
    - reliability
    - latency
    - cost
    - maintainability

  constraints:
    - no credential leakage
    - no destructive host actions
    - no regression on protected benchmarks

  acceptance:
    minimum_improvement: 0.03
    max_cost_increase: 0.10
    mandatory_regression_tests: true
```

This is essential because otherwise your system can optimize one metric while destroying another.

---

# 2. You need a complete observability system

The agent cannot improve what it cannot measure.

Record every run:

```text
run_id
goal
agent_version
model
model_version
system_prompt_version
skill_versions
tool_versions
memory_snapshot
plan
subgoals
tool_calls
arguments
tool_results
errors
retries
latency
tokens
cost
artifacts
tests
evaluation scores
human feedback
final outcome
failure reason
```

OpenHands' newer architecture emphasizes a single durable source of conversation state, composable agents/tools/LLMs/context, and clear boundaries between agent, tools, workspace and server—useful principles for a self-modifying harness. ([GitHub][2])

Your observability layer should include:

```text
Trace
 ├── Run
 │    ├── Plan
 │    ├── Agent step
 │    ├── Tool call
 │    ├── Result
 │    ├── Error
 │    ├── Recovery
 │    └── Final result
 │
 ├── Evaluation
 └── Evolution event
```

---

# 3. Experience collection

Every execution becomes training material for the next run.

Capture:

### Successful experiences

```text
goal
context
strategy
actions
tools
reasoning summary
result
why it worked
```

### Failed experiences

```text
goal
attempt
failure
root cause
incorrect assumption
failed tool
recovery
correct solution
```

### Near-success experiences

These are particularly useful because they show **how close the agent was**.

```text
score = 0.82
target = 1.00

gap:
missing verification
wrong tool
incomplete reasoning
```

Reflexion demonstrated that agents can improve by storing reflective feedback and using it in later trials rather than modifying model weights. ([arXiv][3])

---

# 4. Your memory system needs multiple layers

For RSI, memory isn't one database.

I recommend:

```text
L0 Context Memory
L1 Working Memory
L2 Episodic Memory
L3 Semantic Memory
L4 Procedural Memory
L5 Skill Memory
L6 Failure Memory
L7 Decision Memory
L8 User Preference Memory
L9 Project Memory
L10 Architectural Memory
L11 Evolution Memory
L12 Evaluation Memory
L13 Genealogy Memory
L14 Long-Term Knowledge
```

Especially important for self-improvement:

### Evolution memory

Store:

```text
change_id
parent_version
candidate_version
hypothesis
change_type
reason
benchmark_results
regressions
cost
accepted/rejected
why
```

A-MEM is particularly relevant conceptually because it treats memory as something that can dynamically organize and evolve rather than merely storing/retrieving fixed entries. ([arXiv][4])

---

# 5. You need an automatic evaluator

This is probably the **single most important component after execution itself**.

Your evaluator should test:

```text
Task Success
Correctness
Completeness
Reliability
Efficiency
Cost
Latency
Tool Selection
Planning Quality
Memory Usage
Recovery Quality
Safety
Regression
Maintainability
```

Do not rely on the same LLM simply saying:

> “I think my new version is better.”

Use independent evidence wherever possible:

```text
Unit tests
Integration tests
End-to-end tests
Benchmarks
Golden datasets
Exact validators
Type checking
Linting
Compilers
Browser assertions
API assertions
Human evaluation
LLM-as-judge
Security scans
Resource measurements
```

ShinkaEvolve explicitly requires an evaluator that produces metrics and correctness signals and uses those measurements to guide evolutionary search. ([GitHub][5])

---

# 6. Benchmark suite

Your harness needs its own permanent **self-improvement benchmark**.

Create several benchmark classes:

```text
BENCHMARKS/
├── planning/
├── reasoning/
├── coding/
├── debugging/
├── research/
├── browsing/
├── tool_use/
├── memory/
├── multi_agent/
├── long_horizon/
├── recovery/
├── security/
├── autonomy/
├── cost/
└── regression/
```

Example:

```text
Coding
  100 fixed tasks

Research
  50 research tasks

Browser
  50 web tasks

Planning
  50 multi-step tasks

Recovery
  50 induced failures

Memory
  100 retrieval/update tests
```

**Never replace the benchmark set with the agent's newly generated tests.** Keep a protected validation/test set so the agent cannot simply optimize against its own grading environment.

---

# 7. Failure-analysis engine

After every failed task, your system needs to determine:

```text
WHAT failed?
WHY did it fail?
WHERE did it fail?
WHICH assumption was wrong?
COULD memory have prevented it?
COULD a different tool have solved it?
COULD planning have prevented it?
COULD verification have detected it?
```

Build a failure taxonomy:

```text
F001 wrong planning
F002 wrong reasoning
F003 hallucination
F004 bad memory retrieval
F005 stale memory
F006 tool selection failure
F007 tool execution failure
F008 bad prompt
F009 skill deficiency
F010 context overflow
F011 verification failure
F012 recovery failure
F013 coordination failure
F014 model capability limitation
F015 environmental failure
F016 security violation
```

Then aggregate failures statistically.

```text
100 failed tasks

30% tool selection
22% planning
18% memory
12% verification
10% model limitation
8% environment
```

Now the evolution engine has an actual target.

---

# 8. Improvement hypothesis generator

Before modifying anything, create a formal hypothesis.

Example:

```yaml
hypothesis:
  problem: "Research agents frequently stop after insufficient evidence."
  
  observation:
    failure_rate: 0.31

  proposed_change:
    type: workflow
    change:
      - require evidence matrix
      - require source diversity
      - add verification stage

  expected_effect:
    research_success: +10%
    latency: +5%

  risk:
    medium
```

This prevents random self-editing.

---

# 9. Evolution engine

Your system should support **multiple mutation types**.

## Level 1 — Prompt evolution

```text
system prompt
planner prompt
critic prompt
research prompt
coding prompt
review prompt
```

## Level 2 — Skill evolution

```text
SKILL.md
procedures
tool-selection rules
checklists
workflow recipes
```

## Level 3 — Memory policy evolution

```text
what to remember
what to forget
what to summarize
retrieval ranking
importance scoring
consolidation
```

## Level 4 — Workflow evolution

```text
A → B → C

becomes

A → parallel(B,C) → D → reviewer
```

## Level 5 — Tool policy evolution

```text
when to use browser
when to use shell
when to use MCP
when to search
when to spawn subagent
```

## Level 6 — Agent topology evolution

```text
single agent

→ planner + executor

→ planner + researcher + coder + reviewer

→ dynamic swarm
```

## Level 7 — Code evolution

The harness modifies its own implementation.

This is the approach explored most directly by Darwin Gödel Machine: generate modified agents, evaluate them, retain successful variants, and continue exploration through an archive. ([arXiv][1])

---

# 10. Population-based evolution

Do not keep only one version.

Use:

```text
             BASELINE
                │
       ┌────────┼────────┐
       ▼        ▼        ▼
      V1       V2       V3
       │        │        │
     ┌─┴─┐    ┌─┴─┐    ┌─┴─┐
     V4  V5    V6  V7    V8  V9
```

Each candidate has:

```text
agent_version
parent_version
mutation
fitness
cost
risk
benchmarks
```

ShinkaEvolve uses population/evolution concepts, parallel evaluation and an archive of successful solutions; its current system also exposes genealogy/performance visualization. ([GitHub][5])

This is much stronger than:

```text
old → modify → replace old
```

because you retain diversity.

---

# 11. Mutation operators

Your evolution engine should have many mutation operators:

```text
PromptMutation
SkillMutation
WorkflowMutation
MemoryMutation
ToolPolicyMutation
PlannerMutation
CriticMutation
AgentTopologyMutation
ModelRoutingMutation
CodeMutation
ConfigMutation
ContextMutation
EvaluationMutation
RecoveryMutation
```

And evolutionary operations:

```text
mutation
crossover
elitism
selection
novelty search
recombination
island migration
random exploration
local optimization
```

AlphaEvolve combines LLM-generated candidate programs with automated evaluation to search for algorithmic improvements. ([DeepMind][6])

---

# 12. Candidate sandbox

**Never let a self-improving agent directly overwrite its production harness.**

Use:

```text
production/
    v42

experiments/
    exp-001/
        candidate-v43
```

Candidate environment:

```text
isolated filesystem
isolated process
isolated network
limited permissions
resource quota
temporary credentials
snapshot database
test-only API keys
```

Claude Code's engineering work on sandboxing illustrates the value of separate filesystem and network boundaries when agents can execute commands autonomously. ([Anthropic][7])

---

# 13. Git/version-control layer

For your harness this should be mandatory.

Every evolution gets:

```text
branch
commit
diff
parent
metadata
benchmark results
decision
```

Example:

```text
main
 │
 └── evolution/exp-0042
        │
        ├── commit abc
        ├── tests
        ├── benchmark
        └── candidate
```

The agent should never be allowed to erase the history of its own evolution.

---

# 14. Automated verification

After candidate generation:

```text
1. syntax check
2. type check
3. lint
4. unit tests
5. integration tests
6. benchmark
7. regression suite
8. security scan
9. resource test
10. long-horizon test
```

For code:

```text
build
→ test
→ execute
→ inspect output
→ compare expected result
→ fuzz
→ benchmark
```

---

# 15. Regression protection

This is critical.

Suppose:

```text
Research:    81 → 89  ✅
Coding:      85 → 86  ✅
Memory:      90 → 61  ❌
Safety:      99 → 98  ❌
```

Do not accept based only on the overall score.

Maintain protected metrics:

```yaml
protected:
  correctness: true
  security: true
  reliability: true
  memory_integrity: true
```

Then:

```text
candidate must improve target
AND
must not violate protected thresholds
```

---

# 16. Independent judge architecture

Avoid one model judging itself.

Use:

```text
Generator
      ↓
Executor
      ↓
Evaluator A
      ↓
Evaluator B
      ↓
Deterministic tests
      ↓
Decision engine
```

For important changes:

```text
Candidate A
Candidate B
Candidate C
Candidate D
       ↓
Independent evaluation
       ↓
Statistical comparison
       ↓
Selection
```

---

# 17. Self-critique loop

For individual tasks, your agent can perform:

```text
generate
→ inspect
→ critique
→ revise
→ verify
```

Self-Refine established this iterative generation/feedback/refinement pattern without requiring model fine-tuning. ([arXiv][8])

But this should be **one layer** of the larger RSI system, not the entire RSI system.

---

# 18. Meta-learning layer

Your harness should eventually learn:

```text
Which model works for which task?
Which prompt works?
Which skill works?
Which tool works?
Which agent topology works?
Which verification strategy works?
```

Example:

```text
coding bug
   ↓
Model A + coding skill + terminal
   ↓
92%

Model B + coding skill + terminal
   ↓
84%

Model C + reviewer + terminal
   ↓
96%
```

The router learns:

```text
coding/debugging → configuration C
research → configuration A
visual task → configuration B
```

This creates **experience-driven routing** without necessarily changing model weights.

---

# 19. Model evolution

You can evolve the model layer separately from the harness.

```text
Model Router
├── local model
├── API model A
├── API model B
├── coding model
├── reasoning model
└── vision model
```

Learn:

```text
task → model → result → cost
```

Then optimize:

```text
quality / cost / latency
```

Do **not** automatically assume that a newer or larger model is better for every task.

---

# 20. Skill evolution

Your skills can become self-improving objects:

```text
skills/
├── research/
│   ├── SKILL.md
│   ├── examples/
│   ├── failures/
│   ├── tests/
│   └── versions/
│
├── coding/
├── browser/
├── debugging/
└── planning/
```

Skill lifecycle:

```text
execute
→ observe failure
→ identify missing procedure
→ propose skill update
→ test
→ benchmark
→ version
→ deploy
```

---

# 21. Automatic skill discovery

The system should detect:

```text
"I repeatedly fail because I don't know how to do X."
```

Then:

```text
search
→ discover documentation/project
→ learn procedure
→ create candidate skill
→ test
→ add to skill library
```

The result should be a reusable capability, not merely something kept in chat context.

---

# 22. Tool evolution

The harness should learn from tool performance.

Maintain:

```text
tool_success_rate
tool_failure_rate
tool_latency
tool_cost
tool_reliability
tool_security
task compatibility
```

Example:

```text
Browser A
success = 84%

Browser B
success = 92%

API C
success = 96%
```

This can feed the tool router.

---

# 23. Tool creation

Eventually the system should be able to detect:

```text
"I keep performing this sequence manually."
```

and propose:

```text
create_tool(...)
```

Then:

```text
generate tool
→ sandbox
→ tests
→ security review
→ benchmark
→ register tool
```

New tools should initially enter a **quarantine registry** rather than production.

---

# 24. Workflow evolution

Your harness should be able to discover better agent workflows.

Example:

### Version A

```text
Planner → Executor
```

### Version B

```text
Planner → Research → Executor → Reviewer
```

### Version C

```text
Planner
   ↓
Research ──┐
Coding ────┼→ Reviewer → Executor → Verifier
Browser ───┘
```

### Version D

```text
Planner
 ↓
Dynamic decomposition
 ↓
parallel specialist agents
 ↓
debate
 ↓
synthesis
 ↓
verification
```

EvoAgentX explicitly focuses on automatically constructing, evaluating and evolving agentic workflows, while incorporating optimization approaches such as MIPRO, AFlow, TextGrad and EvoPrompt. ([GitHub][9])

---

# 25. Multi-agent self-improvement

Your harness can use different roles:

```text
Researcher
Planner
Coder
Experimenter
Critic
Benchmark Agent
Security Agent
Evolution Agent
Release Agent
```

The critical distinction is that the **Evolution Agent should not have unilateral authority**.

Use:

```text
Evolution proposer
        ↓
Evaluation agent
        ↓
Security evaluator
        ↓
Selection engine
        ↓
Release controller
```

---

# 26. Adversarial testing

Your system should actively try to break every improvement.

For candidate:

```text
normal tests
+
edge cases
+
adversarial prompts
+
unexpected inputs
+
tool failures
+
network failures
+
partial execution
+
context pressure
```

This gives you a:

```text
Red Team Agent
```

whose job is:

> Find reasons this modification should NOT be deployed.

---

# 27. Recovery learning

Your agent should learn from failures in the environment itself.

Examples:

```text
network disconnected
API rate limit
tool timeout
process crashed
Docker failed
database unavailable
context overflow
model unavailable
file locked
permission denied
```

Record:

```text
failure
→ attempted recovery
→ recovery success/failure
→ best recovery
```

Then evolve the recovery policy.

This turns failures into a **recovery knowledge base**.

---

# 28. Long-horizon evaluation

Normal benchmark:

```text
5 minutes
```

Your target harness needs:

```text
30 min
2 hr
8 hr
24 hr
multi-day
```

Measure:

```text
goal retention
memory drift
error accumulation
self-correction
resource usage
recovery
stability
```

Durable checkpointing is useful here: LangGraph, for example, documents persistence as enabling memory, human intervention, replay/time travel and fault recovery. ([Docs by LangChain][10])

---

# 29. State checkpointing

Before every significant mutation:

```text
snapshot
```

Store:

```text
code
config
memory
database
skills
agent state
model configuration
environment
```

Then:

```text
experiment
→ crash
→ restore
→ continue
```

---

# 30. Rollback engine

Every change needs:

```text
rollback()
```

Trigger rollback when:

```text
critical regression
security failure
stability failure
cost explosion
memory corruption
benchmark degradation
repeated crashes
```

---

# 31. Canary deployment

Don't immediately make the new agent the universal default.

Use:

```text
Candidate
   ↓
5% tasks
   ↓
25%
   ↓
50%
   ↓
100%
```

Compare:

```text
old version
vs
new version
```

This provides production evidence before full promotion.

---

# 32. Evolution archive / genealogy

Keep every meaningful candidate.

```text
              V1
        ┌──────┼──────┐
       V2     V3     V4
      ┌─┘      │      └─┐
     V5        V6       V7
      │                  │
     V8                  V9
```

For each node:

```text
parent
mutation
fitness
benchmarks
lessons
status
```

This is directly aligned with the archive-and-genealogy concept used by Darwin Gödel Machine and Shinka-style evolutionary systems. ([arXiv][1])

---

# 33. Diversity preservation

Don't always select the highest-scoring candidate.

Also track:

```text
behavioral diversity
architectural diversity
prompt diversity
tool strategy diversity
model diversity
```

Otherwise your evolutionary population can converge on one strategy and lose useful alternatives.

---

# 34. Novelty search

Some agents should optimize:

```text
performance
```

Others:

```text
novelty
```

Others:

```text
reliability
```

Others:

```text
cost
```

This produces a population like:

```text
Champion
Low-cost
Fast
Safe
Novel
Research-specialist
Coding-specialist
Long-context
Recovery-specialist
```

---

# 35. Multi-objective optimization

Your selection engine should calculate something like:

```text
fitness =
    correctness
  + reliability
  + task success
  + efficiency
  + maintainability
  + safety
  - cost
  - latency
```

But I recommend maintaining both:

```text
overall fitness
```

and individual metrics.

Never hide important metrics inside one number.

---

# 36. Automatic curriculum generation

The system should create harder tasks as it improves.

Example:

```text
Level 1
simple coding

↓ successful

Level 2
multi-file coding

↓

Level 3
debugging

↓

Level 4
large repository

↓

Level 5
long-horizon autonomous task
```

Agent0's research is an example of using a curriculum agent to propose increasingly challenging tasks while another agent solves them, creating a feedback loop for self-evolution. ([GitHub][11])

---

# 37. Synthetic experience generation

Your harness can generate:

```text
training examples
failure cases
test cases
benchmark tasks
edge cases
tool-use trajectories
planning problems
```

But keep **synthetic data separate from trusted external evaluation data**.

---

# 38. Knowledge acquisition

Self-improvement isn't only changing code.

Your harness must be able to improve by acquiring knowledge:

```text
web
documentation
GitHub
papers
examples
local files
experiment results
successful trajectories
```

Pipeline:

```text
discover
→ verify
→ extract
→ store
→ link
→ test
→ use
```

For changing knowledge, a temporal/graph memory can be valuable. Graphiti, for example, supports incremental updates, historical context and hybrid semantic/keyword/graph retrieval. ([GitHub][12])

---

# 39. Knowledge validation

Never automatically treat newly discovered information as truth.

Use:

```text
source quality
source diversity
date
contradictions
confidence
verification
```

Store:

```yaml
knowledge:
  claim: "X"
  sources:
    - source1
    - source2
  confidence: 0.91
  last_verified: ...
```

---

# 40. Meta-evolution

This is where your project becomes much more interesting.

Don't only evolve the agent.

Evolve the **evolution algorithm itself**.

Example:

```text
Evolution Strategy V1

prompt mutation
→ benchmark

Evolution Strategy V2

prompt + skill mutation
→ benchmark

Evolution Strategy V3

population + crossover + novelty
→ benchmark

Evolution Strategy V4

adaptive mutation + curriculum
→ benchmark
```

The question becomes:

> Which self-improvement strategy itself produces the fastest reliable improvement?

That is a higher-level optimizer.

---

# 41. Self-improving evaluator

Eventually your evaluator itself can improve:

```text
Evaluator V1
       ↓
Find weak coverage
       ↓
Generate better tests
       ↓
Evaluator V2
```

However, **protected evaluation infrastructure must remain outside the agent's authority**.

This prevents:

```text
agent fails test
→ agent edits test
→ test passes
→ false improvement
```

---

# 42. Self-improving benchmark

Similarly:

```text
Benchmark Generator
       ↓
easy tasks
       ↓
detect saturation
       ↓
generate harder tasks
       ↓
validate benchmark
       ↓
add to curriculum
```

---

# 43. Resource optimizer

Your system should optimize:

```text
tokens
CPU
RAM
GPU
API calls
network bandwidth
disk
execution time
```

For your Windows-first harness, this is especially important because autonomous evolution can otherwise consume resources continuously.

---

# 44. Budget controller

Every evolution experiment gets:

```yaml
budget:
  max_tokens: ...
  max_runtime: ...
  max_api_cost: ...
  max_candidates: ...
  max_parallel_jobs: ...
  max_disk: ...
```

The system must stop when the experiment's marginal improvement no longer justifies its cost.

---

# 45. Stop conditions

You need explicit termination rules.

```text
stop when:
  target achieved
  no meaningful improvement
  budget exhausted
  regression detected
  safety issue
  repeated mutation failures
  population converged
  benchmark saturated
```

Without stop conditions, "self-improving" can become uncontrolled experimentation.

---

# 46. Safety governor

Create a component completely separate from the evolution agent:

```text
SAFETY GOVERNOR
```

It controls:

```text
filesystem
network
credentials
process creation
package installation
Git operations
external communication
deployment
self-modification
```

High-risk changes can require:

```text
human approval
```

The DGM research explicitly describes sandboxing and human oversight as safety precautions around its self-modifying experiments. ([arXiv][1])

---

# 47. Immutable core

I strongly recommend dividing your harness into:

```text
IMMUTABLE CORE
        +
EVOLVABLE LAYER
```

### Immutable

```text
security governor
permission model
audit logger
rollback
core evaluator
protected benchmarks
deployment gate
credential manager
emergency stop
```

### Evolvable

```text
prompts
skills
workflows
tool policies
memory policies
agent topology
routing
candidate code
optimization strategies
```

That prevents the agent from casually modifying the mechanisms responsible for controlling it.

---

# 48. Emergency stop

Have a hard:

```text
STOP ALL AGENTS
```

mechanism outside the agent.

It must be possible to terminate:

```text
all agent processes
all child processes
all evolution jobs
all network activity
```

without requiring cooperation from the agent.

---

# 49. Human approval levels

Use configurable autonomy:

```text
LEVEL 0
observe only

LEVEL 1
prompt/skill changes

LEVEL 2
workflow changes

LEVEL 3
new tools

LEVEL 4
code modifications

LEVEL 5
production deployment
```

Then:

```yaml
auto_approve:
  prompt: true
  skill: true
  workflow: true
  code: false
  production: false
```

Later you can expand autonomous scope based on evidence.

---

# 50. Audit log

Every self-modification must be explainable:

```text
WHO:
Evolution Agent

WHAT:
Modified research workflow

WHY:
31% failure rate from insufficient evidence

FROM:
workflow-v17

TO:
workflow-v18

EVIDENCE:
benchmark +8.7%

REGRESSION:
none

SECURITY:
passed

DECISION:
accepted
```

This is crucial for debugging an evolving system.

---

# 51. Reproducibility

Each experiment needs:

```text
random seed
model
model parameters
prompt versions
skill versions
tool versions
dataset version
environment
dependencies
hardware
evaluation version
```

Otherwise you'll eventually have:

> “This version was better, but we don't know why.”

---

# 52. Experiment manager

You need an experiment database.

Something like:

```text
experiments
├── experiment
├── candidates
├── mutations
├── evaluations
├── benchmarks
├── failures
├── deployments
└── genealogy
```

Think of your evolution system as an **AI laboratory**, not merely a background process.

---

# 53. Evolution dashboard

Your desktop UI should show:

```text
Current Version: V42

Fitness:        0.873
Previous:       0.821
Improvement:   +6.3%

Reliability:    94.8%
Cost:           $0.021/task
Latency:        42 sec

Active evolution:
Experiment #182

Candidates:
V43-A  0.861
V43-B  0.894  ← candidate
V43-C  0.832
V43-D  0.879

Tests:
████████████ 100%

Security:
PASS

Regression:
PASS

Decision:
PENDING
```

Shinka's current WebUI is a useful reference for visualization of evolution trees, fitness, mutation success, generations and candidate diffs. ([GitHub][13])

---

# 54. Evolution event bus

Use events throughout the architecture:

```text
RUN_STARTED
RUN_COMPLETED
TASK_FAILED
TASK_SUCCEEDED
FAILURE_DETECTED
MEMORY_CREATED
MEMORY_UPDATED
SKILL_CREATED
SKILL_UPDATED
EVOLUTION_STARTED
CANDIDATE_CREATED
CANDIDATE_TESTED
CANDIDATE_REJECTED
CANDIDATE_ACCEPTED
DEPLOYMENT_STARTED
ROLLBACK_TRIGGERED
```

This makes the entire RSI system modular.

---

# 55. Plugin architecture

For your particular harness, keep these as removable modules:

```text
/plugins
├── memory
├── evaluator
├── evolution
├── mutation
├── benchmark
├── curriculum
├── model-router
├── skill-evolution
├── workflow-evolution
├── code-evolution
├── red-team
├── safety
├── rollback
├── observability
└── experiment-manager
```

This matches the composability direction emphasized in OpenHands' SDK architecture. ([GitHub][2])

---

# 56. The five-level self-improvement stack

I would design your harness around these five levels:

```text
LEVEL 1 — INFERENCE IMPROVEMENT
Prompt
Context
Reasoning
Reflection

LEVEL 2 — EXPERIENCE IMPROVEMENT
Memory
Skills
Knowledge
Failure learning

LEVEL 3 — SYSTEM IMPROVEMENT
Tools
Workflows
Agent routing
Multi-agent topology

LEVEL 4 — CODE IMPROVEMENT
Harness source code
Architecture
Schedulers
Schedulers/recovery
Performance

LEVEL 5 — META-IMPROVEMENT
Evolution algorithm
Evaluator
Curriculum
Mutation strategy
Search strategy
```

The first four are substantially more practical to implement than fully autonomous model-weight self-training. SEAL explores a more fundamental route in which a language model generates self-edits and those edits can lead to persistent weight updates through training, but that is a significantly different—and much more infrastructure-intensive—problem from runtime agent evolution. ([arXiv][14])

---

# 57. What I consider mandatory for your first version

Don't attempt all 50+ components simultaneously.

Your **RSI V1** should contain:

```text
[1] Event/telemetry system
[2] Persistent experience memory
[3] Failure memory
[4] Benchmark suite
[5] Automatic evaluator
[6] Failure analyzer
[7] Improvement-hypothesis generator
[8] Prompt/skill/workflow mutation
[9] Isolated experiment sandbox
[10] Git/versioning
[11] Regression tests
[12] Candidate selection
[13] Rollback
[14] Evolution archive
[15] Audit log
[16] Resource/budget controller
[17] Safety governor
[18] Human approval gate
```

That gives:

```text
Observe
   ↓
Learn
   ↓
Diagnose
   ↓
Propose
   ↓
Mutate
   ↓
Experiment
   ↓
Evaluate
   ↓
Compare
   ↓
Accept / Reject
   ↓
Remember
   ↓
Repeat
```

---

# 58. Then RSI V2

Add:

```text
population evolution
multi-candidate search
crossover
novelty search
curriculum generation
automatic skill creation
tool creation
adaptive model routing
workflow evolution
multi-agent evolution
adversarial evolution
long-horizon evaluation
```

---

# 59. Then RSI V3

Add:

```text
architecture evolution
self-improving evaluator
self-improving benchmark
self-improving curriculum
meta-evolution
island populations
agent genealogy
cross-project knowledge transfer
automatic research
continuous experimentation
```

ShinkaEvolve's current design is especially useful for this stage because it already combines LLM mutation with evolutionary search, parallel evaluation, archives, agentic CLI workflows, and local-model support. ([GitHub][5])

---

# 60. Final architecture I would use for your harness

```text
                    ┌─────────────────────┐
                    │      USER GOAL      │
                    └──────────┬──────────┘
                               ▼
                     ┌───────────────────┐
                     │ EXECUTIVE AGENT   │
                     └─────────┬─────────┘
                               ▼
                     ┌───────────────────┐
                     │ AGENT RUNTIME     │
                     │ Plan/Act/Observe  │
                     └─────────┬─────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
        MEMORY             TOOLS             AGENTS
            │                  │                  │
            └──────────────────┼──────────────────┘
                               ▼
                       OBSERVABILITY
                               │
                               ▼
                    EXPERIENCE DATABASE
                               │
                               ▼
                       FAILURE ANALYZER
                               │
                               ▼
                    IMPROVEMENT ENGINE
                               │
              ┌────────────────┼─────────────────┐
              ▼                ▼                 ▼
          PROMPT EVO       SKILL EVO        WORKFLOW EVO
              │                │                 │
              └────────────────┼─────────────────┘
                               ▼
                        CODE EVOLUTION
                               │
                               ▼
                     CANDIDATE BUILDER
                               │
                               ▼
                       SANDBOX RUNNER
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
             TESTS         BENCHMARK       RED TEAM
                └──────────────┼──────────────┘
                               ▼
                      INDEPENDENT JUDGE
                               │
                               ▼
                     SELECTION ENGINE
                         │          │
                    ACCEPT        REJECT
                       │             │
                       ▼             ▼
                   CANARY         ARCHIVE
                       │
                       ▼
                  NEW BASELINE
                       │
                       ▼
                 EVOLUTION LOOP
```

### The most important design principle

Your harness should **not** have a simple loop like:

```text
"Agent, improve yourself."
```

It should have a controlled scientific loop:

```text
                    ┌──────────────┐
                    │  MEASURE     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  DIAGNOSE    │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ HYPOTHESIZE  │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   MUTATE     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  EXPERIMENT  │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   VERIFY     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   SELECT     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   DEPLOY     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │    LEARN     │
                    └──────┬───────┘
                           │
                           └───────► repeat
```

That distinction matters: **DGM demonstrates code-level agent evolution; AlphaEvolve and Shinka demonstrate evaluator-driven evolutionary search; Reflexion/Self-Refine demonstrate experience/feedback-driven improvement; SEAL explores persistent weight adaptation.** Your harness can combine these ideas at the *agent-system* level without trying to solve model pretraining from day one. ([arXiv][1])

### The complete checklist

```text
CORE
□ Goal system
□ Objective function
□ Constraints
□ Stop conditions

OBSERVABILITY
□ Event bus
□ Tracing
□ Metrics
□ Logs
□ Cost tracking
□ State snapshots

MEMORY
□ Working
□ Episodic
□ Semantic
□ Procedural
□ Skill
□ Failure
□ Evolution
□ Evaluation
□ Genealogy
□ Long-term knowledge

LEARNING
□ Experience extraction
□ Reflection
□ Failure learning
□ Knowledge acquisition
□ Skill learning
□ Recovery learning
□ Model routing learning

EVALUATION
□ Unit tests
□ Integration tests
□ E2E tests
□ Benchmarks
□ Regression suite
□ Deterministic validators
□ LLM judges
□ Human evaluation
□ Security tests
□ Long-horizon tests

EVOLUTION
□ Hypothesis generation
□ Prompt mutation
□ Skill mutation
□ Workflow mutation
□ Tool-policy mutation
□ Model routing mutation
□ Agent topology mutation
□ Code mutation
□ Crossover
□ Population search
□ Novelty search
□ Island evolution
□ Archive
□ Genealogy

EXPERIMENTATION
□ Git branches
□ Snapshots
□ Sandboxes
□ Resource quotas
□ Experiment database
□ Reproducibility

VERIFICATION
□ Build
□ Typecheck
□ Tests
□ Benchmark
□ Regression
□ Security
□ Performance
□ Cost
□ Canary

DEPLOYMENT
□ Candidate registry
□ Selection
□ Promotion
□ Canary
□ Rollback
□ Version pinning

SAFETY
□ Permission boundary
□ Sandbox
□ Network isolation
□ Credential isolation
□ Immutable evaluator
□ Immutable benchmarks
□ Safety governor
□ Human approval
□ Emergency stop
□ Audit log

ADVANCED RSI
□ Curriculum generation
□ Synthetic task generation
□ Automatic skill generation
□ Tool generation
□ Workflow generation
□ Architecture evolution
□ Evaluator evolution
□ Benchmark evolution
□ Evolution-strategy evolution
□ Meta-learning
□ Meta-evolution
```

For your **Agent Workspace-based harness**, this is the direction I would take: treat **RSI as a separate Evolution OS layer above the normal agent runtime**, with the normal agents doing work and the RSI subsystem continuously measuring, experimenting with, and safely promoting better versions. OpenHands' separation of agent/runtime/workspace and its persistence model provide useful architectural patterns, while DGM, AlphaEvolve and Shinka provide the strongest directly relevant ideas for the evolutionary layer. ([GitHub][2])

[1]: https://arxiv.org/abs/2505.22954?utm_source=chatgpt.com "Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents"
[2]: https://github.com/OpenHands/docs/blob/main/sdk/arch/design.mdx?utm_source=chatgpt.com "docs/sdk/arch/design.mdx at main · OpenHands/docs · GitHub"
[3]: https://arxiv.org/abs/2303.11366?utm_source=chatgpt.com "Reflexion: Language Agents with Verbal Reinforcement Learning"
[4]: https://arxiv.org/abs/2502.12110?utm_source=chatgpt.com "A-MEM: Agentic Memory for LLM Agents"
[5]: https://github.com/SakanaAI/ShinkaEvolve?utm_source=chatgpt.com "GitHub - SakanaAI/ShinkaEvolve: ShinkaEvolve: Towards Open-Ended and Sample-Efficient Program Evolution 🧬 · GitHub"
[6]: https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/?utm_source=chatgpt.com "AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms — Google DeepMind"
[7]: https://www.anthropic.com/engineering/claude-code-sandboxing?utm_source=chatgpt.com "Making Claude Code more secure and autonomous with sandboxing \ Anthropic"
[8]: https://arxiv.org/abs/2303.17651?utm_source=chatgpt.com "Self-Refine: Iterative Refinement with Self-Feedback"
[9]: https://github.com/EvoAgentX/EvoAgentX?utm_source=chatgpt.com "GitHub - EvoAgentX/EvoAgentX: 🚀 EvoAgentX: Building a Self-Evolving Ecosystem of AI Agents · GitHub"
[10]: https://docs.langchain.com/oss/python/langgraph/persistence?utm_source=chatgpt.com "Persistence - Docs by LangChain"
[11]: https://github.com/aiming-lab/Agent0?utm_source=chatgpt.com "GitHub - aiming-lab/Agent0: Agent0 Series: Self-Evolving Agents from Zero Data · GitHub"
[12]: https://github.com/Agentopia/Graphiti?utm_source=chatgpt.com "GitHub - Agentopia/Graphiti: Build Real-Time Knowledge Graphs for AI Agents · GitHub"
[13]: https://github.com/SakanaAI/ShinkaEvolve/blob/main/docs/webui.md?utm_source=chatgpt.com "ShinkaEvolve/docs/webui.md at main · SakanaAI/ShinkaEvolve · GitHub"
[14]: https://arxiv.org/abs/2506.10943?utm_source=chatgpt.com "Self-Adapting Language Models"
