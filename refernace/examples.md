Yes. For your **DeerFlow-based harness**, I would make this a first-class subsystem rather than calling it simply “self-improvement.”

# Recursive Self-Development Engine — RSD Engine

A good formal name is:

> **Recursive Self-Development Engine (RSDE)**

I prefer **Self-Development** over just **Self-Improvement** because your system is intended to evolve not only its prompts, but its **skills, tools, memory, planning strategies, agent topology, workflows, evaluators, and eventually its own software implementation**.

The key idea is:

```text
                    YOUR HARNESS
                         │
                  ┌──────▼──────┐
                  │   OBSERVE   │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │   MEASURE   │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │  DIAGNOSE   │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │   IDEATE    │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │  GENERATE   │
                  │  CANDIDATE  │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │   SANDBOX   │
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │   EVALUATE  │
                  └──────┬──────┘
                         │
              ┌──────────┴──────────┐
              │                     │
           REGRESSION             BETTER
              │                     │
           REJECT                 ACCEPT
              │                     │
              │              ┌──────▼──────┐
              │              │   DEPLOY    │
              │              └──────┬──────┘
              │                     │
              └──────────────┬──────┘
                             │
                      ┌──────▼──────┐
                      │   MONITOR   │
                      └──────┬──────┘
                             │
                             └──────► NEXT GENERATION
```

That is the core of what you are describing.

---

# 1. First: what does "recursive self-development" actually mean?

There are several increasingly powerful levels.

### Level 0 — Static agent

```text
Human designs agent
       ↓
Agent executes
```

No self-development.

### Level 1 — Reflection

```text
Agent
 ↓
review own result
 ↓
try again
```

This is **not really RSI**. It is self-critique.

### Level 2 — Experience improvement

```text
experience
 ↓
extract lesson
 ↓
memory
 ↓
future behavior improves
```

Now the agent learns procedurally.

### Level 3 — Skill evolution

```text
failed workflow
 ↓
diagnose
 ↓
modify skill
 ↓
test
 ↓
new skill version
```

The agent can improve its procedures.

### Level 4 — Strategy evolution

```text
planning strategy
 ↓
alternative strategies
 ↓
benchmark
 ↓
select better strategy
```

Now the agent can improve **how it thinks/acts**, without modifying its core code.

### Level 5 — Architecture evolution

```text
Agent architecture
 ↓
candidate architecture
 ↓
benchmark
 ↓
better architecture
```

For example:

```text
Planner A
   ↓
Planner B + critic
   ↓
Planner B + critic + verifier
   ↓
Planner C + parallel planning
```

### Level 6 — Code evolution

```text
Current harness
       ↓
AI proposes code modification
       ↓
sandbox
       ↓
tests
       ↓
benchmark
       ↓
candidate
       ↓
production
```

This is where you start approaching genuine **recursive self-development**.

### Level 7 — Self-improving self-improvement

The important part:

```text
Agent improves itself
       ↓
new agent becomes better at
improving itself
       ↓
next generation creates better improvements
       ↓
...
```

This recursive property is central to the **Darwin Gödel Machine (DGM)** research. DGM maintains an evolving archive of agents, modifies agent code, evaluates candidates, and allows improvements to also improve the system's ability to make future improvements. ([arXiv][1])

---

# 2. Your RSDE should NOT be one giant agent

This is extremely important.

Don't build:

```text
"AI, improve yourself."
```

Instead build a **Self-Development Operating System**.

I would architect it as:

```text
RSDE
│
├── 01 Observation Engine
├── 02 Telemetry Engine
├── 03 Performance Analyzer
├── 04 Failure Miner
├── 05 Opportunity Detector
├── 06 Improvement Planner
├── 07 Candidate Generator
├── 08 Mutation Engine
├── 09 Experiment Engine
├── 10 Sandbox
├── 11 Benchmark Engine
├── 12 Verification Engine
├── 13 Regression Engine
├── 14 Selection Engine
├── 15 Deployment Engine
├── 16 Rollback Engine
├── 17 Evolution Archive
├── 18 Knowledge/Memory Engine
├── 19 Strategy Library
├── 20 Skill Evolution
├── 21 Architecture Evolution
└── 22 Meta-Evolution Engine
```

---

# 3. The master architecture

For your harness, I would make the entire system look like this:

```text
┌─────────────────────────────────────────────────────────────┐
│                     USER / WORLD                            │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    HARNESS KERNEL                           │
│                                                             │
│ runtime • scheduler • state • events • lifecycle            │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    AGENT OPERATING LAYER                    │
│                                                             │
│ Executive • Planner • Agents • Skills • Tools • MCP         │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                    EXECUTION LAYER                          │
│                                                             │
│ Browser • Terminal • Code • Files • APIs • Sandbox          │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                 OBSERVABILITY / MEMORY                      │
│                                                             │
│ traces • events • memories • artifacts • decisions          │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
              ┌──────────────────────────┐
              │      RSDE ENGINE         │
              │                          │
              │ Observe                  │
              │ Diagnose                 │
              │ Generate                 │
              │ Experiment               │
              │ Evaluate                 │
              │ Select                   │
              │ Deploy                   │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │     EVOLUTION ARCHIVE    │
              │                          │
              │ v1 v2 v3 v4 ...         │
              │ branches                 │
              │ mutations                │
              │ experiments              │
              │ scores                   │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │     BETTER HARNESS       │
              └──────────────────────────┘
                           │
                           └──────► repeat
```

---

# 4. The most important concept: everything becomes an evolutionary object

Don't restrict evolution to source code.

Define:

```text
EvolutionTarget
```

with types:

```text
PROMPT
SKILL
TOOL_POLICY
TOOL_ROUTER
PLANNER
MEMORY_RETRIEVER
MEMORY_CONSOLIDATOR
MODEL_ROUTER
AGENT_PROFILE
AGENT_TOPOLOGY
WORKFLOW
VERIFIER
RECOVERY_STRATEGY
CONTEXT_STRATEGY
CODE_MODULE
PLUGIN
CONFIGURATION
EVALUATION_STRATEGY
```

Eventually:

```text
HARNESS_ARCHITECTURE
```

can become an evolution target.

---

# 5. Evolution Genome

This is one of the advanced things I strongly recommend.

Represent an agent/harness configuration as a **genome**.

For example:

```yaml
genome:
  planner:
    algorithm: adaptive_dag
    max_depth: 5

  executor:
    parallelism: 4

  memory:
    retrieval: hybrid
    reranking: true

  verifier:
    critics: 3

  model_router:
    strategy: cost_quality

  recovery:
    retries: 3

  context:
    compression: hierarchical
```

Then evolution can mutate:

```text
planner.algorithm
planner.max_depth
executor.parallelism
memory.retrieval
verifier.critics
model_router.strategy
```

This lets you evolve **architectures**, not merely files.

---

# 6. Mutation Engine

The Mutation Engine generates alternatives.

Mutation operators could be:

```text
ADD
REMOVE
REPLACE
MODIFY
COMBINE
SPLIT
REORDER
PARAMETER_TUNE
PROMPT_MUTATE
SKILL_MUTATE
TOPOLOGY_MUTATE
MODEL_SWAP
TOOL_SWAP
STRATEGY_SWAP
```

For example:

```text
Parent:

Researcher
  ↓
Writer
  ↓
Verifier
```

Mutation:

```text
Researcher
  ↓
Researcher-A
Researcher-B
  ↓
Consensus
  ↓
Writer
  ↓
Verifier
```

Candidate gets benchmarked.

---

# 7. Evolutionary population

This is where DGM/Shinka/AlphaEvolve become extremely relevant.

Instead of maintaining:

```text
current_agent
```

maintain:

```text
Population

Agent-A
Agent-B
Agent-C
Agent-D
Agent-E
```

Each has:

```yaml
id:
parent:
generation:
genome:
code_version:
skills:
benchmark_scores:
cost:
latency:
reliability:
novelty:
```

The system can explore multiple paths.

DGM specifically uses an archive/tree of evolving agent variants rather than only replacing one agent with the next version. ([arXiv][1])

---

# 8. Evolution tree

Visualize:

```text
                    v0
                   /  \
                 v1    v2
                / \      \
              v3  v4      v5
                  |
                  v6
                 /  \
               v7    v8
```

This is much more powerful than:

```text
v1 → v2 → v3
```

because sometimes a seemingly bad branch contains an idea that becomes useful later.

---

# 9. Island evolution

Take this further.

```text
Island A
Coding agents

Island B
Research agents

Island C
Memory strategies

Island D
Planning strategies

Island E
Multi-agent architectures
```

Each island evolves independently.

Periodically:

```text
knowledge exchange
       ↓
migration
       ↓
cross-breeding
```

ShinkaEvolve is especially relevant here: it combines LLM mutation with evolutionary search, maintains successful-program archives, supports parallel evaluation, and uses multiple evolutionary islands for knowledge transfer. ([GitHub][2])

---

# 10. Novelty engine

Do NOT select candidates only by benchmark score.

Otherwise the system can converge prematurely.

Use:

```text
Fitness
+
Novelty
+
Diversity
```

Example:

```text
Candidate A
score = 90
novelty = 20

Candidate B
score = 88
novelty = 90
```

You may retain both.

This helps prevent **evolutionary stagnation**.

---

# 11. Fitness function

You need a multidimensional fitness function.

For example:

```text
Fitness =
    task_success
  + quality
  + reliability
  + verification
  + efficiency
  + cost
  + latency
  + safety
  + generalization
  + novelty
```

Not:

```text
accuracy only
```

For your harness:

```text
F =
w1 GoalSuccess
+
w2 Quality
+
w3 Reliability
+
w4 Verification
+
w5 Generalization
+
w6 Efficiency
+
w7 Safety
-
w8 Cost
-
w9 Latency
```

Weights should be task-dependent.

---

# 12. Multi-objective evolution

This is even better.

Instead of one score:

```text
Candidate A
quality 95
cost 100
```

maintain a Pareto frontier:

```text
Quality
  ↑
  │       A
  │    B
  │
  │ C
  └──────────────→ Cost
```

Possible candidates:

```text
Ultra Quality
Balanced
Ultra Cheap
Ultra Fast
Ultra Reliable
```

Then the runtime selects according to the current task.

---

# 13. Benchmark Engine

This is the **heart of RSDE**.

Without good evaluation:

> self-improvement becomes self-deception.

Create:

```text
Benchmark Suite
│
├── planning
├── coding
├── research
├── browser
├── memory
├── tool-use
├── reasoning
├── multi-agent
├── recovery
├── long-horizon
├── security
├── cost
├── latency
└── goal-completion
```

---

# 14. Holdout benchmarks

Very important.

Suppose an agent evolves against:

```text
Benchmark A
```

It might overfit.

So use:

```text
TRAIN / EVOLUTION SET
        ↓
Candidate improvement

VALIDATION SET
        ↓
select candidate

HELD-OUT TEST SET
        ↓
final verification
```

This concept is particularly important if you want the RSDE to be credible rather than simply optimizing a benchmark it has already seen.

---

# 15. Regression protection

Every improvement must answer:

```text
Did it improve?
```

but also:

```text
What did it break?
```

For example:

```text
Planning       +12%
Coding         +7%
Research       +4%
Memory         -2%
Security       -8%
Cost            +30%
```

Reject the candidate if the regression violates policy.

---

# 16. Improvement budget

RSDE should have:

```yaml
max_iterations: 100
max_cost: 10 USD
max_runtime: 6h
max_parallel_candidates: 8
max_code_changes: 50
```

Without budgets, autonomous experimentation can run indefinitely.

---

# 17. Improvement hypothesis

Every candidate should have a hypothesis.

Example:

```yaml
hypothesis:
  problem: "Research agents repeatedly duplicate web searches"

  proposed_change:
    type: "memory"
    change: "add semantic search-result cache"

  expected_effect:
    duplicate_searches: -30%
    cost: -20%
    latency: -15%

  risks:
    stale_information: medium
```

Then the experiment tests the hypothesis.

---

# 18. Experiment record

Every experiment should produce:

```yaml
experiment_id:
parent:
candidate:
hypothesis:
dataset:
baseline:
metrics:
results:
regressions:
cost:
duration:
decision:
evidence:
```

This becomes the **scientific memory of the harness**.

---

# 19. Self-development loop

Your final loop should look like:

```text
                         ┌──────────────┐
                         │ LIVE RUNS    │
                         └──────┬───────┘
                                ↓
                         OBSERVATION
                                ↓
                         FAILURE MINING
                                ↓
                       OPPORTUNITY MINING
                                ↓
                       IMPROVEMENT IDEA
                                ↓
                         HYPOTHESIS
                                ↓
                    ┌───────────┴───────────┐
                    ↓                       ↓
             Candidate A              Candidate B
                    ↓                       ↓
                 Sandbox                 Sandbox
                    ↓                       ↓
                Benchmark              Benchmark
                    └───────────┬───────────┘
                                ↓
                         Comparative Eval
                                ↓
                         Safety / Policy
                                ↓
                         Holdout Testing
                                ↓
                         Candidate Selection
                                ↓
                        Canary / Shadow Mode
                                ↓
                             DEPLOY
                                ↓
                           MONITOR
                                ↓
                      NEW EXPERIENCE DATA
                                │
                                └──────────► repeat
```

---

# 20. Meta-RSI

Now comes the really advanced part.

Your system shouldn't only improve the **agent**.

It should improve the **improvement engine**.

```text
RSDE v1
  ↓
improves Agent
  ↓
Agent becomes better
  ↓
Agent improves RSDE
  ↓
RSDE v2
  ↓
better candidate generation
  ↓
better evaluation
  ↓
better agents
  ↓
...
```

Architecture:

```text
                ┌─────────────────────┐
                │     HARNESS v1      │
                └──────────┬──────────┘
                           ↓
                     RSDE v1
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
        Improve Agent             Improve RSDE
              ↓                         ↓
        Agent v2                    RSDE v2
              └────────────┬────────────┘
                           ↓
                       HARNESS v2
                           ↓
                          ...
```

This is much closer to the idea of **recursive self-development**.

---

# 21. But there should be a hard boundary

I strongly recommend:

```text
SELF-DEVELOPMENT
        │
        ▼
Candidate Environment
        │
        ▼
Evaluation
        │
        ▼
Safety Gate
        │
        ▼
Human/Policy Approval
        │
        ▼
Production
```

Never:

```text
Production
   ↓
arbitrary self-modification
   ↓
immediately replace itself
```

The DGM repository itself warns that it executes model-generated code and recommends strong safety precautions; its research setup used sandboxing and human oversight. ([GitHub][3])

---

# 22. Capability levels for your RSDE

I would implement explicit levels.

## RSD-0

Observation only.

```text
collect telemetry
```

## RSD-1

Reflection.

```text
identify mistakes
```

## RSD-2

Memory improvement.

```text
learn from failures
```

## RSD-3

Skill improvement.

```text
modify skills
```

## RSD-4

Workflow improvement.

```text
modify planning/execution strategies
```

## RSD-5

Agent evolution.

```text
generate alternative agents
```

## RSD-6

Architecture evolution.

```text
modify architecture/configuration
```

## RSD-7

Code evolution.

```text
modify source code
```

## RSD-8

Recursive evolution.

```text
improve the system that improves the system
```

## RSD-9

Open-ended research/evolution.

```text
discover new algorithms
discover new architectures
discover new strategies
```

For your first release, **RSD-0 → RSD-4** is realistic. RSD-5/6 should come after you have excellent evaluation and rollback infrastructure.

---

# 23. Existing projects you should study

There isn't one existing project that gives you everything.

Instead, there are several extremely useful architectural pieces.

## 1. Darwin Gödel Machine

**Most directly relevant to your idea.**

DGM recursively modifies its own agent code and empirically validates changes, maintaining an archive/tree of evolving agents. The published experiments reported improvement on SWE-bench from 20.0% to 50.0% and Polyglot from 14.2% to 30.7%. ([arXiv][1])

[Darwin Gödel Machine — research paper](https://arxiv.org/abs/2505.22954?utm_source=chatgpt.com)

[Darwin Gödel Machine — reference implementation](https://github.com/jennyzzt/dgm?utm_source=chatgpt.com)

**Take from it:**

* agent population
* mutation
* code modification
* archive
* evolutionary tree
* benchmark selection
* open-ended search
* sandboxing

---

# 24. ShinkaEvolve

This may be **one of the most directly reusable components** for your project.

It combines:

```text
LLMs
+
Evolutionary Algorithms
+
Program Mutation
+
Archive
+
Parallel Evaluation
+
Novelty
+
Evolutionary Islands
```

It is open-source and has 2026 updates including a unified runner, WebUI, local-model support, async evolution, and coding-agent skills. ([GitHub][2])

[ShinkaEvolve GitHub](https://github.com/SakanaAI/ShinkaEvolve?utm_source=chatgpt.com)

I would seriously investigate whether parts of its evolution machinery can become an **RSDE Evolution Backend** in your harness.

---

# 25. AlphaEvolve

Google DeepMind's AlphaEvolve is highly relevant to the **candidate → evaluator → evolutionary selection** architecture.

It combines multiple LLMs with automated evaluators and evolutionary search. Google reports applications to data-center scheduling, chip design, AI training infrastructure and mathematical/algorithmic problems. ([DeepMind][4])

[AlphaEvolve — Google DeepMind](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/?utm_source=chatgpt.com)

Architecture to borrow conceptually:

```text
Prompt sampler
      ↓
LLM candidates
      ↓
Programs
      ↓
Automated evaluator
      ↓
Score
      ↓
Evolution database
      ↓
Next generation
```

This is almost exactly the core of your proposed **candidate evolution subsystem**.

---

# 26. FunSearch

FunSearch is another foundational project.

It uses:

```text
LLM
 ↓
program generation
 ↓
automatic execution
 ↓
evaluation
 ↓
retain high-quality programs
 ↓
feed them back
```

It also explicitly uses diversity mechanisms and parallel evolution to avoid stagnation. ([DeepMind][5])

[FunSearch — Google DeepMind](https://deepmind.google/blog/funsearch-making-new-discoveries-in-mathematical-sciences-using-large-language-models/?utm_source=chatgpt.com)

This is especially useful for designing your:

**Mutation + Evaluation + Archive** layer.

---

# 27. AI Scientist

The AI Scientist is important for another reason.

It demonstrates an autonomous research loop:

```text
idea generation
 ↓
literature search
 ↓
experiment design
 ↓
code
 ↓
experiments
 ↓
visualization
 ↓
paper
 ↓
review
 ↓
improvement
```

Sakana describes the system as an open-ended iterative process where reviews feed back into future research generations. ([Sakana AI][6])

[AI Scientist GitHub](https://github.com/SakanaAI/AI-Scientist?utm_source=chatgpt.com)

This gives your RSDE a model for:

> **"research itself as an agentic loop."**

---

# 28. Voyager

Voyager is not a self-modifying harness, but its architecture contains a concept you absolutely want:

**ever-growing procedural skill library.**

It continuously explores, learns executable skills, stores them, retrieves them and reuses them in new situations. ([GitHub][7])

[Voyager GitHub](https://github.com/MineDojo/Voyager?utm_source=chatgpt.com)

For your harness:

```text
Experience
 ↓
Skill extraction
 ↓
Skill verification
 ↓
Skill library
 ↓
Future agents
```

This becomes your **procedural evolution layer**.

---

# 29. DiscoPOP / LLM²

Sakana's LLM² work is relevant because it moves beyond:

> AI uses an algorithm

toward:

> AI discovers algorithms for improving AI.

DiscoPOP is an example of LLM-driven discovery of preference-optimization algorithms. ([Sakana AI][8])

[DiscoPOP GitHub](https://github.com/SakanaAI/DiscoPOP?utm_source=chatgpt.com)

This is relevant to your **meta-evolution** layer.

---

# 30. Your RSDE should combine these ideas

Think:

```text
                 YOUR RSDE
                    │
      ┌─────────────┼─────────────┐
      │             │             │
      ▼             ▼             ▼
    DGM        AlphaEvolve    ShinkaEvolve
      │             │             │
 agent evolution  evaluator     population
      │             │             │
      └─────────────┼─────────────┘
                    │
             ┌──────▼──────┐
             │    RSDE     │
             └──────┬──────┘
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
   Voyager     AI Scientist   FunSearch
       │            │            │
     skills      research     program
       │            │         evolution
       └────────────┼────────────┘
                    ▼
             YOUR HARNESS
```

---

# 31. The "Self-Development Genome" I would use

I would define:

```yaml
SelfDevelopmentGenome:

  identity:
    agent_version:
    harness_version:
    parent:

  cognition:
    system_prompt:
    reasoning_strategy:
    planning_strategy:
    reflection_strategy:

  memory:
    retrieval:
    ranking:
    consolidation:
    compression:

  skills:
    enabled:
    routing:
    skill_selection:

  tools:
    enabled:
    selection_policy:
    fallback_policy:

  agents:
    topology:
    delegation:
    communication:
    concurrency:

  models:
    primary:
    fallback:
    routing:

  execution:
    sandbox:
    browser:
    terminal:
    filesystem:

  verification:
    validators:
    critics:
    test_strategy:

  recovery:
    retry:
    rollback:
    failover:
    escalation:

  evolution:
    mutation:
    selection:
    novelty:
    population:
```

Now the harness can evolve this object.

---

# 32. Candidate lifecycle

Every candidate should go through:

```text
CREATED
   ↓
STATIC_ANALYSIS
   ↓
BUILD
   ↓
UNIT_TEST
   ↓
SANDBOX_TEST
   ↓
TASK_BENCHMARK
   ↓
TRAJECTORY_EVALUATION
   ↓
SECURITY_EVALUATION
   ↓
REGRESSION_TEST
   ↓
HELDOUT_TEST
   ↓
SHADOW
   ↓
CANARY
   ↓
PRODUCTION
```

And potentially:

```text
ROLLBACK
```

at any point.

---

# 33. Candidate database

Use a database rather than filesystem-only storage.

Conceptually:

```text
candidates
experiments
benchmarks
metrics
mutations
parents
artifacts
evaluations
deployments
rollbacks
```

Relationships:

```text
candidate
   │
   ├── parent
   ├── mutations
   ├── experiments
   ├── benchmark results
   ├── artifacts
   ├── deployment
   └── descendants
```

This gives you the evolutionary graph.

---

# 34. Improvement provenance

For every improvement:

```text
Why was this change created?

What failure caused it?

What hypothesis motivated it?

What model generated it?

What evidence supported it?

Which benchmark improved?

What regressed?

Who/what approved it?

Which version deployed it?
```

That gives you:

**Evolution provenance.**

---

# 35. Self-development memory

Create a separate memory namespace:

```text
RSI_MEMORY/
│
├── failures/
├── successful_mutations/
├── failed_mutations/
├── hypotheses/
├── experiments/
├── benchmarks/
├── strategies/
├── discovered_patterns/
├── regressions/
└── evolution_history/
```

This is different from normal agent memory.

---

# 36. Failure Mining Engine

This is one of the highest-value components.

After every task:

```text
trajectory
 ↓
failure detector
 ↓
failure classification
```

Categories:

```text
planning_failure
reasoning_failure
tool_failure
memory_failure
retrieval_failure
context_failure
delegation_failure
verification_failure
recovery_failure
model_failure
environment_failure
skill_failure
security_failure
```

Then:

```text
failure
 ↓
root cause
 ↓
possible improvement
```

---

# 37. Root Cause Analysis

Don't simply say:

> Agent failed.

Determine:

```text
Why?

Planner selected wrong strategy
       ↓
because memory retrieval missed project constraint
       ↓
because retrieval ranking ignored recency
       ↓
because metadata wasn't indexed
```

Then the improvement candidate becomes:

```text
Add metadata-aware retrieval.
```

That's a meaningful self-development cycle.

---

# 38. Opportunity Miner

Not only failures.

Look for:

```text
repeated manual work
repeated tool calls
repeated reasoning
repeated corrections
high latency
high cost
unused capability
frequent human intervention
```

Example:

```text
100 runs
 ↓
37 repeatedly perform same search
 ↓
candidate:
persistent research cache
```

This is how your harness discovers improvements organically.

---

# 39. Human correction mining

This is extremely valuable.

If you correct the agent:

```text
Agent:
"I used approach A."

Human:
"No. Use B because..."
```

Capture:

```text
human correction
 ↓
reason
 ↓
pattern
 ↓
candidate skill/policy
```

Repeated corrections can become:

```text
new skill
new policy
new verifier
new benchmark
```

---

# 40. The harness should create its own tests

This is a major advanced feature.

When a failure occurs:

```text
failure
 ↓
generate regression test
 ↓
add test to benchmark suite
```

Therefore:

```text
Failure
 ↓
test
 ↓
future protection
```

Over time, your harness builds its own **immune system**.

---

# 41. Self-generated benchmark expansion

Even more advanced:

```text
discover weakness
 ↓
generate adversarial task
 ↓
add benchmark
 ↓
future candidates must pass
```

So your benchmark suite itself evolves.

That is **meta-evaluation**.

---

# 42. Self-development flywheel

Your eventual system becomes:

```text
More tasks
     ↓
More experiences
     ↓
More failures detected
     ↓
More benchmarks
     ↓
More improvement opportunities
     ↓
More candidates
     ↓
Better harness
     ↓
Better task performance
     ↓
More useful experiences
     ↓
...
```

That is your actual **recursive development flywheel**.

---

# 43. The most advanced version

Eventually:

```text
                  ┌─────────────────────┐
                  │       WORLD         │
                  └──────────┬──────────┘
                             ↓
                     ┌──────────────┐
                     │    AGENTS    │
                     └──────┬───────┘
                            ↓
                     ┌──────────────┐
                     │ EXPERIENCES  │
                     └──────┬───────┘
                            ↓
                ┌───────────┴───────────┐
                ↓                       ↓
          MEMORY MINING           FAILURE MINING
                │                       │
                └───────────┬───────────┘
                            ↓
                     OPPORTUNITIES
                            ↓
                     HYPOTHESES
                            ↓
                  CANDIDATE GENERATOR
                            ↓
                  ┌─────────┴─────────┐
                  ↓                   ↓
             MUTATION A          MUTATION B
                  ↓                   ↓
               SANDBOX             SANDBOX
                  ↓                   ↓
              EVALUATOR            EVALUATOR
                  └─────────┬─────────┘
                            ↓
                    EVOLUTION ARCHIVE
                            ↓
                    SELECTION ENGINE
                            ↓
                     HOLDOUT TEST
                            ↓
                       CANARY
                            ↓
                      PRODUCTION
                            ↓
                     MONITORING
                            ↓
                    NEW EXPERIENCE
                            │
                            └──────────────►
```

And simultaneously:

```text
RSDE itself
     ↓
is evaluated
     ↓
RSDE improvements proposed
     ↓
new RSDE candidates
     ↓
benchmark
     ↓
deploy
```

That is the **recursive** part.

---

# 44. What I would actually build in your DeerFlow project

Given your current architecture, I would add:

```text
deer-flow/
│
├── backend/
│
│   ├── harness/
│   │
│   ├── rsde/
│   │   │
│   │   ├── observer/
│   │   ├── telemetry/
│   │   ├── analyzer/
│   │   ├── failure_miner/
│   │   ├── opportunity_miner/
│   │   ├── hypothesis/
│   │   ├── candidate/
│   │   ├── mutation/
│   │   ├── population/
│   │   ├── evolution/
│   │   ├── evaluator/
│   │   ├── benchmark/
│   │   ├── regression/
│   │   ├── holdout/
│   │   ├── deployment/
│   │   ├── rollback/
│   │   ├── archive/
│   │   └── meta_rsi/
│   │
│   ├── agents/
│   ├── memory/
│   ├── skills/
│   ├── tools/
│   ├── sandbox/
│   ├── verification/
│   ├── observability/
│   └── security/
│
└── frontend/
    └── evolution/
```

---

# 45. UI you should build

Your dashboard should have an **Evolution Center**.

### Evolution Overview

```text
RSDE STATUS: ACTIVE

Current Harness: v0.8.3
Generation: 27

Candidates:
  14 running
  8 accepted
  31 rejected

Performance:
  +18.7% task success
  -12.4% cost
  -8.2% latency

Regressions:
  0 critical
```

### Evolution Tree

```text
                 v0
              /      \
           v1          v2
         /   \          \
       v3     v4         v5
              |
              v6 ★
```

### Candidate comparison

```text
                 BASELINE   CANDIDATE
Success            81%         89%
Quality            83%         91%
Cost              $1.20       $1.05
Latency            42s         38s
Failures           11%          6%
Security           PASS        PASS
```

### Why was it created?

```text
Detected failure:

"Research agent repeatedly duplicated
queries across 17 runs."

Proposed improvement:

Semantic research-result cache.

Expected:
Cost -15%
Latency -12%
```

This makes self-development **observable instead of magical**.

---

# 46. What NOT to do

Do **not** start with:

```text
"Let the AI rewrite the entire harness."
```

That is the wrong architecture.

Start:

```text
observe
 ↓
benchmark
 ↓
small candidate
 ↓
sandbox
 ↓
verify
 ↓
compare
 ↓
deploy
```

Then progressively expand the scope.

---

# 47. Your implementation roadmap

## Phase 1 — Foundation

Build:

```text
Event Store
Run Store
Telemetry
Benchmark Engine
Artifact Store
Versioning
Checkpointing
```

Without this, don't start RSI.

---

## Phase 2 — Learning

Add:

```text
Failure Mining
Experience Mining
Human Correction Mining
Memory
Skill Library
Regression Test Generation
```

---

## Phase 3 — Controlled improvement

Add:

```text
Hypothesis Engine
Candidate Generator
Mutation Engine
Sandbox
Evaluator
Candidate Database
```

---

## Phase 4 — Evolution

Add:

```text
Population
Evolution Tree
Novelty
Selection
Islands
Migration
Pareto optimization
```

This is where ShinkaEvolve becomes particularly useful as architectural inspiration. ([GitHub][2])

---

## Phase 5 — Agent evolution

Allow:

```text
prompt evolution
skill evolution
planner evolution
tool-routing evolution
memory evolution
agent-topology evolution
```

---

## Phase 6 — Code evolution

Allow:

```text
source modification
 ↓
branch
 ↓
build
 ↓
tests
 ↓
benchmark
 ↓
holdout
 ↓
canary
```

This is where the DGM architecture becomes particularly relevant. ([arXiv][1])

---

## Phase 7 — Meta-RSI

Finally:

```text
RSDE
 ↓
improves itself
 ↓
better RSDE
 ↓
better candidate generation
 ↓
better evaluation
 ↓
better harness
```

This should be the **last** stage, not the first.

---

# 48. One critical architectural principle

I would define this rule permanently:

> **No self-generated improvement is considered an improvement until an independent evaluation demonstrates that it is better than the current baseline under predefined acceptance criteria.**

That single principle prevents:

```text
self-improvement
```

from becoming:

```text
self-modification
```

And that distinction is crucial.

AlphaEvolve's design demonstrates this pattern particularly clearly: LLM-generated candidate programs are executed and scored by automated evaluators, with evolutionary selection favoring promising candidates. ([DeepMind][4])

---

# 49. What makes your concept different from ordinary "self-improving agents"

A normal agent:

```text
Experience
 ↓
Reflection
 ↓
Try again
```

Your intended system:

```text
Experience
 ↓
Telemetry
 ↓
Failure mining
 ↓
Root-cause analysis
 ↓
Improvement hypothesis
 ↓
Candidate generation
 ↓
Population evolution
 ↓
Sandbox experiments
 ↓
Benchmark
 ↓
Independent verification
 ↓
Regression
 ↓
Holdout
 ↓
Selection
 ↓
Canary
 ↓
Deployment
 ↓
Monitoring
 ↓
New experience
 ↓
New improvements
```

And eventually:

```text
                    ┌──────────────────────┐
                    │     HARNESS vN       │
                    └──────────┬───────────┘
                               ↓
                        Self-observation
                               ↓
                       Self-diagnosis
                               ↓
                     Self-experimentation
                               ↓
                      Self-evaluation
                               ↓
                       Self-modification
                               ↓
                     Self-verification
                               ↓
                      Self-deployment
                               ↓
                    Better Harness vN+1
                               │
                               └─────────►
```

That is the architecture I would call a genuine **Recursive Self-Development Engine**.

---

## My recommended terminology for your project

I would use these names consistently:

| Component                 | Recommended name                             |
| ------------------------- | -------------------------------------------- |
| Overall subsystem         | **Recursive Self-Development Engine (RSDE)** |
| Improvement cycle         | **Self-Development Loop**                    |
| Candidate system          | **Evolution Engine**                         |
| Candidate representation  | **Development Genome**                       |
| Candidate collection      | **Evolution Population**                     |
| Historical candidates     | **Evolution Archive**                        |
| Candidate lineage         | **Evolution Tree**                           |
| Improvement proposal      | **Development Hypothesis**                   |
| Candidate modification    | **Mutation**                                 |
| Candidate testing         | **Evolution Experiment**                     |
| Quality measurement       | **Fitness Evaluation**                       |
| Improvement selection     | **Selection Engine**                         |
| Safety                    | **Development Guard**                        |
| Candidate execution       | **Evolution Sandbox**                        |
| Final testing             | **Holdout Verification**                     |
| Production transition     | **Canary Deployment**                        |
| Failure recovery          | **Evolution Rollback**                       |
| Improving the RSDE itself | **Meta-RSI**                                 |
| Complete feedback loop    | **Recursive Development Flywheel**           |

### The strongest overall naming

**Recursive Self-Development Engine (RSDE)**

with:

> **Evolution Engine + Development Genome + Experiment Engine + Verification Engine + Evolution Archive + Meta-RSI**

as its major internal components.

That gives your DeerFlow project a substantially more precise concept than simply calling it an "RSI agent."

And the existing work suggests you should **combine ideas rather than copy one project**: DGM gives you recursive agent-code evolution; AlphaEvolve/FunSearch give you evaluator-driven evolutionary program search; ShinkaEvolve gives you an open-source evolutionary framework with archives/islands; Voyager gives you persistent procedural skill acquisition; and AI Scientist gives you an open-ended research/experiment/review loop. ([arXiv][1])

One especially important current development is that Sakana AI has now formally created an **RSI Lab** and describes a trajectory from agent-native systems → AI Scientist → recursive self-improvement → broader autonomous AI development. Their 2026 overview explicitly groups LLM², DGM, ShinkaEvolve, ALE-Agent, Digital Red Queen and AI Scientist as milestones in this direction. ([Sakana AI][8])

So for your project, I would treat **RSDE as a major architectural pillar of the harness**, alongside the Runtime Kernel, Agent System, Memory System, Execution Fabric, Verification System and Observability System—not as another ordinary agent skill.

[1]: https://arxiv.org/abs/2505.22954?utm_source=chatgpt.com "Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents"
[2]: https://github.com/SakanaAI/ShinkaEvolve?utm_source=chatgpt.com "GitHub - SakanaAI/ShinkaEvolve: ShinkaEvolve: Towards Open-Ended and Sample-Efficient Program Evolution 🧬 · GitHub"
[3]: https://github.com/jennyzzt/dgm?utm_source=chatgpt.com "GitHub - jennyzzt/dgm: Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents · GitHub"
[4]: https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/?utm_source=chatgpt.com "AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms — Google DeepMind"
[5]: https://deepmind.google/blog/funsearch-making-new-discoveries-in-mathematical-sciences-using-large-language-models/?utm_source=chatgpt.com "FunSearch: Making new discoveries in mathematical sciences using Large Language Models — Google DeepMind"
[6]: https://sakana.ai/ai-scientist/?utm_source=chatgpt.com "The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery"
[7]: https://github.com/MineDojo/Voyager/blob/main/README.md?utm_source=chatgpt.com "Voyager/README.md at main · MineDojo/Voyager · GitHub"
[8]: https://sakana.ai/rsi-lab/?utm_source=chatgpt.com "Introducing Sakana AI’s Recursive Self-Improvement (RSI) Lab"
