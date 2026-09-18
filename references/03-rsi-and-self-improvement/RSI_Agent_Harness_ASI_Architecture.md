# RSI Evolution Architecture for an Advanced AI Agent Harness

## Document status

- **Purpose:** Engineering blueprint for an AI agent harness with automated Recursive Self-Improvement (RSI).
- **Target:** A long-lived, tool-using, multi-agent harness that can improve memory, prompts, skills, tools, workflows, agent topology, evaluator quality, and selected harness code through controlled experiments.
- **Important scope:** This is an **ASI-oriented engineering architecture**, not a claim that the system will become artificial superintelligence. Current research demonstrates bounded self-improvement and agent/harness evolution, not reliable unrestricted general ASI.
- **Recommended initial deployment:** Local/Windows-friendly development with Docker/VM sandboxing, Git-based versioning, benchmark-driven promotion, and explicit human approval for production promotion.

---

# 1. Executive architecture

The system should be built as a set of nested feedback loops instead of a single agent loop.

```text
                         HUMAN / USER
                              |
                              v
                    +--------------------+
                    | EXECUTIVE / CEO    |
                    | Goal + policy      |
                    +---------+----------+
                              |
                              v
                    +--------------------+
                    | TASK GRAPH / PLAN   |
                    | Decompose / route   |
                    +---------+----------+
                              |
                +-------------+-------------+
                |             |             |
                v             v             v
            Research       Coding       Specialist
              Agent          Agent         Agents
                |             |             |
                +-------------+-------------+
                              |
                              v
                    +--------------------+
                    | TOOL / WORLD LAYER  |
                    | shell, files, web,  |
                    | browser, MCP, Git,   |
                    | DB, APIs, apps       |
                    +---------+----------+
                              |
                              v
                    +--------------------+
                    | OBSERVABILITY       |
                    | traces/logs/metrics |
                    | state/artifacts     |
                    +---------+----------+
                              |
                              v
                    +--------------------+
                    | EVALUATION          |
                    | tests/benchmarks    |
                    | critics/verifiers   |
                    +---------+----------+
                              |
                              v
                  +-------------------------+
                  | FAILURE / GAP MINER      |
                  | root cause + capability  |
                  | deficiency detection     |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | RSI META-AGENT           |
                  | hypotheses + experiments|
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | EVOLUTION LAB            |
                  | candidate generation     |
                  | mutation/repair/hybrid   |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | SANDBOX CANDIDATE        |
                  | isolated version         |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | PROMOTION GATE           |
                  | regression + safety +    |
                  | holdout + cost + canary  |
                  +-------+-------------+-----+
                          |             |
                       reject         accept
                          |             |
                          v             v
                       archive       lineage
                                       |
                                       v
                                next generation
```

The central engineering rule is:

> **The running production agent proposes improvements; an isolated candidate is built and objectively evaluated; only a promotion controller can replace the active version.**

---

# 2. Research projects and what to borrow

## 2.1 Exo — recursive harness architecture

Repository:
- https://github.com/exoharness/exo
- RSI design: https://github.com/exoharness/exo/blob/main/docs/RSI.md

Exo is directly relevant because it is designed as a complete agent + harness system with recursive self-improvement. Its documented design emphasizes:
- full visibility into code and runtime logs;
- incremental self-modification;
- cloning and lineage;
- immutable append-only event history;
- rewind/restart;
- a protected core outside normal agent mutation.

**Borrow:**
- immutable canonical event ledger;
- version/clone lineage;
- self-inspection of code + runtime state;
- rewind/retry;
- explicit boundary around the core substrate.

**Improve for this project:**
- stronger independent evaluation;
- sealed holdouts;
- multi-dimensional metrics;
- capability-gap mining;
- population/Pareto archive;
- benchmark generation;
- explicit production promotion pipeline.

---

## 2.2 Self-Improving Coding Agent (SICA)

Repository:
- https://github.com/MaximeRobeyns/self_improving_coding_agent
- Mirror: https://github.com/Rekhii/Self-Improving-Coding-SICA

The core loop is:
1. evaluate the current agent;
2. archive results;
3. run the agent on its own codebase;
4. modify the agent;
5. evaluate the new version;
6. keep iterating.

The project explicitly recommends container isolation because the agent can execute shell commands.

**Borrow:**
- baseline -> modify -> reevaluate;
- self-editing codebase;
- experiment archive;
- Docker isolation;
- multiple model providers.

**Improve:**
- do not rely only on a fixed benchmark;
- add hidden/held-out regression;
- track exact parent/child lineage;
- evaluate multiple dimensions;
- independently verify candidate changes.

---

## 2.3 Agent0 — self-evolving curriculum + executor

Repository:
- https://github.com/aiming-lab/Agent0
- Paper: https://arxiv.org/abs/2511.16043

Agent0 uses two co-evolving roles:
- a **curriculum agent** that creates increasingly difficult tasks;
- an **executor agent** that solves them using tools.

The important mechanism is that the benchmark/task stream itself becomes adaptive.

**Borrow:**
- self-generated curriculum;
- frontier task generation;
- tool-aware training/evaluation pressure;
- co-evolution of "what to test" and "how to solve it."

**Improve:**
- use curriculum generation for harness evaluation;
- require benchmark validity checks;
- add held-out tasks that the curriculum generator cannot inspect;
- combine task generation with production failure replay.

---

## 2.4 AlphaEvolve — evolutionary candidate population + objective evaluators

Official:
- https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/
- Impact update:
  https://deepmind.google/blog/alphaevolve-impact/

Core ideas:
- LLMs generate candidate programs;
- automated evaluators verify/score them;
- an evolutionary database retains promising programs;
- fast and strong models can serve different search roles;
- candidate populations are repeatedly evolved.

**Borrow:**
- population search;
- automated objective evaluation;
- evolutionary archive;
- model specialization for breadth/depth;
- measurable improvement rather than subjective approval.

**Improve:**
- evolve complete agent/harness configurations, not just algorithms;
- add behavioral, security, cost and regression metrics;
- use Pareto selection instead of one scalar score.

---

## 2.5 AVO — Agentic Variation Operators

Paper:
- https://arxiv.org/abs/2603.24517

AVO replaces fixed mutation/crossover heuristics with an agentic variation loop. The agent can inspect lineage, knowledge and execution feedback, then propose, repair, critique and verify changes.

**Borrow:**
- variation as an agent workflow;
- lineage-aware modification;
- inspect -> edit -> run -> repair -> critique -> verify;
- domain knowledge in the variation context.

**Improve:**
- apply the operator to prompts, skills, tools, memory, topology and harness code;
- constrain mutation through typed change contracts;
- isolate evaluator and safety policy from mutation.

---

## 2.6 Darwin Gödel Machine (DGM)

Research direction:
- https://arxiv.org/abs/2505.22954

DGM-style systems maintain a population/archive of agents and explore self-modifications through branching evolutionary search.

**Borrow:**
- lineage tree;
- population/archive rather than a single linear version;
- search over self-modifications;
- reuse of successful descendants;
- diversity in candidate exploration.

**Improve:**
- objective vectors;
- strict promotion gates;
- reproducible experiment manifests;
- sealed holdout;
- canary rollout.

---

## 2.7 Adaptive Auto-Harness / A-Evolve

Repository:
- https://github.com/A-EVO-Lab/AdaptiveHarness

The project focuses on sustained self-improvement for agentic deployment on open-ended task streams.

**Borrow:**
- adaptation over continuing task streams;
- routing/evolution/adaptation separation;
- evolution as a persistent operating process rather than a one-shot benchmark trick.

**Improve:**
- formalize the task stream as production telemetry + synthetic curriculum;
- retain explicit historical baselines.

---

## 2.8 Self-improving multi-agent harness examples

Examples worth inspecting for engineering patterns:
- https://github.com/ethan-haas/self-improving-agent-harness
- https://github.com/harrystamatoukos/self-improving-agent-harness
- https://github.com/oldbulb/samsara
- https://github.com/NvlFR/self-evolving-agent
- https://github.com/lobehub/awesome-rsi

Patterns found across these projects include:
- sandboxed inner loops;
- staged promotion;
- prompt evolution;
- skill discovery;
- episodic memory;
- append-only lessons;
- statistical acceptance gates;
- architectural mapping;
- auto-generated tests;
- runtime/resource monitoring.

Treat these repositories as implementation references, not as proof that unrestricted RSI is solved.

---

# 3. Design principles

## 3.1 Evidence before promotion

Never promote a change because the model says it is better.

Required chain:

```text
proposal
  -> implementation
  -> executable test
  -> objective measurement
  -> regression analysis
  -> safety/security analysis
  -> holdout evaluation
  -> canary
  -> promotion
```

---

## 3.2 Immutable truth boundary

The RSI system needs a small substrate that the candidate agent cannot rewrite during normal evolution.

Protect at minimum:
- canonical event history;
- promotion rules;
- secret boundaries;
- sandbox boundaries;
- evaluator integrity;
- emergency stop;
- production credentials;
- rollback mechanism;
- benchmark holdout data.

This does not mean the entire system is immutable. It means the mechanism that decides whether the system improved is outside the candidate's unilateral authority.

---

## 3.3 Everything else can be versioned

Make these evolvable:
- prompts;
- policies that are explicitly marked mutable;
- skills;
- workflows;
- planner;
- routing;
- tool adapters;
- memory extraction;
- memory retrieval;
- recovery strategies;
- agent topology;
- selected harness modules;
- benchmark generators.

For higher-risk components, require increasingly strong gates.

---

## 3.4 Separate "learning" from "self-modification"

Three different things should exist:

```text
Experience learning
  memory/reflection/lessons

Behavior adaptation
  prompt/skill/workflow/policy variants

Structural self-modification
  source-code/harness changes
```

Do not let every failure immediately become a code change.

---

# 4. RSI maturity ladder

```text
RSI-0  static agent
RSI-1  reflective memory
RSI-2  skill evolution
RSI-3  prompt/workflow evolution
RSI-4  tool evolution
RSI-5  agent-topology evolution
RSI-6  harness-code evolution
RSI-7  benchmark/evaluator evolution
RSI-8  RSI-engine evolution
RSI-9  architecture evolution
RSI-10 model/training/compute co-evolution
```

**Recommended project target:**
- production-ready: RSI-1 through RSI-6;
- research mode: RSI-7 and RSI-8;
- future work: RSI-9+.

---

# 5. Core architecture

## 5.1 Immutable kernel

```text
kernel/
  identity/
  authorization/
  policy/
  sandbox/
  secrets/
  audit/
  event_log/
  promotion/
  rollback/
  emergency_stop/
  registry/
```

Responsibilities:
- identity and authorization;
- capability restrictions;
- resource limits;
- append-only history;
- candidate registration;
- promotion;
- rollback;
- audit.

---

## 5.2 Executive

```text
executive/
  goal_manager
  task_router
  resource_manager
  stop_controller
  escalation_manager
```

The executive:
- interprets user goals;
- sets task constraints;
- allocates budgets;
- decides when to delegate;
- decides when to recover/escalate/stop.

---

## 5.3 Planner

```text
planner/
  decomposer
  task_graph
  dependency_analyzer
  replanner
  checkpoint_manager
```

Output should be an executable task graph:

```yaml
goal:
constraints:
acceptance_criteria:
tasks:
  - id:
    objective:
    dependencies:
    tools:
    expected_artifacts:
    verification:
checkpoints:
budgets:
```

---

## 5.4 Worker pool

Specialists:
- researcher;
- coder;
- browser/computer-use agent;
- analyst;
- data agent;
- debugger;
- architect;
- reviewer;
- verifier;
- recovery agent.

Worker agents should be replaceable plugins.

---

# 6. World model / environment interface

Build an explicit world-state interface.

```text
world/
  filesystem
  repository
  processes
  terminal
  browser
  application
  database_metadata
  network
  runtime
  agent_state
  task_state
```

The agent should be able to inspect:
- source tree;
- dependencies;
- Git history;
- test state;
- logs;
- metrics;
- traces;
- runtime state;
- screenshots/DOM where appropriate;
- DB schema via safe/read-only tooling;
- active processes.

The world model should expose facts and observations, not arbitrary unlimited context.

---

# 7. Memory fabric

Use separate memory stores.

```text
memory/
  working/
  episodic/
  semantic/
  procedural/
  reflective/
  capability/
  meta/
```

### Working
Current task state.

### Episodic
Past full trajectories.

### Semantic
Persistent facts/knowledge.

### Procedural
Reusable skills/workflows.

### Reflective
Lessons from successes/failures.

### Capability
What the agent can currently do and confidence/evidence.

### Meta
What makes the improvement process itself work better.

---

# 8. Typed experience record

Every task should create a structured episode.

```yaml
episode:
  id:
  timestamp:
  task_id:
  agent_version:
  model:
  prompt_versions: []
  skill_versions: []
  tool_versions: []
  memory_snapshot:
  environment_snapshot:
  plan:
  trajectory:
  artifacts:
  result:
  evaluation:
  failures:
  recovery:
  lessons:
  confidence:
```

---

# 9. Failure mining

Failures are the primary fuel for RSI.

Pipeline:

```text
failed trajectory
      |
      v
failure classifier
      |
      v
root-cause analysis
      |
      v
capability-gap detector
      |
      v
improvement opportunity
```

Failure classes:
- reasoning;
- planning;
- memory;
- retrieval;
- tool choice;
- tool execution;
- coding;
- environment;
- coordination;
- verification;
- context;
- knowledge;
- resource allocation;
- recovery.

---

# 10. Capability graph

Represent capabilities as a graph:

```text
coding
  |
  +-- repository discovery
  +-- dependency analysis
  +-- implementation
  +-- testing
  +-- debugging
  +-- refactoring
  +-- deployment

research
  |
  +-- search
  +-- source validation
  +-- synthesis
  +-- contradiction detection
```

Every capability record should contain:

```yaml
capability:
  id:
  confidence:
  evidence_count:
  recent_success_rate:
  recent_failure_rate:
  dependencies:
  known_failures:
  candidate_improvements:
```

---

# 11. Improvement hypothesis engine

Do not permit vague self-modification.

Every candidate begins with a contract:

```yaml
improvement_hypothesis:
  id:
  parent_version:
  observed_problem:
  evidence:
    - episode_id:
      benchmark_id:
      failure_pattern:
  suspected_root_cause:
  capability_gap:
  proposed_change:
    category:
    components:
  expected_effect:
  risks:
  cost_estimate:
  validation_plan:
  rollback_plan:
```

Example:

```yaml
observed_problem: long-horizon tasks lose state after recovery
root_cause: task state is reconstructed from summaries instead of persistent checkpoints
capability_gap: durable execution state
proposed_change:
  category: architecture
  components:
    - task_graph
    - checkpoint_manager
expected_effect:
  long_horizon_success: increase
  recovery_success: increase
risks:
  - context_storage
  - stale_checkpoints
```

---

# 12. Candidate generation

Support multiple variation operators:

```text
mutation
repair
refactor
prompt rewrite
skill synthesis
workflow redesign
tool synthesis
topology change
memory strategy change
hybridization
```

AVO-style operator:

```text
inspect lineage
   ->
inspect code/docs
   ->
inspect failures
   ->
propose change
   ->
implement
   ->
run
   ->
repair
   ->
critique
   ->
verify
```

---

# 13. Candidate types

## Type A — memory candidate
Changes retrieval/extraction/consolidation.

## Type B — prompt candidate
Changes system or role prompts.

## Type C — skill candidate
Adds or modifies reusable procedures.

## Type D — workflow candidate
Changes agent execution sequence.

## Type E — tool candidate
Adds/changes tools or adapters.

## Type F — topology candidate
Changes multi-agent composition.

## Type G — harness candidate
Changes source code.

## Type H — evaluator candidate
Changes measurement logic. **High risk.**

Evaluator candidates should never be allowed to redefine their own acceptance criteria for the same experiment.

---

# 14. Evolution population

Never maintain only a linear version chain.

Use:

```text
                         v0
                   /      |      \
                 v1       v2       v3
                / \        |      / \
              v4  v5      v6     v7 v8
                  |         \     /
                  +---------- v9
```

Store:
- parent;
- ancestors;
- modifications;
- benchmark results;
- costs;
- failures;
- safety results;
- environment;
- model;
- reproducibility metadata.

---

# 15. Candidate registry

```yaml
candidate:
  id:
  parent_id:
  generation:
  branch:
  artifact_digest:
  changes:
  tests:
  benchmark_results:
  holdout_results:
  safety_results:
  resource_results:
  status:
```

Status:

```text
PROPOSED
BUILDING
TESTING
EVALUATING
REJECTED
ARCHIVED
CANARY
PROMOTED
ROLLED_BACK
```

---

# 16. Evaluation architecture

Use multiple test families.

```text
evaluation/
  unit/
  integration/
  regression/
  capability/
  long_horizon/
  adversarial/
  security/
  cost/
  latency/
  reliability/
  holdout/
```

Core metrics:

```text
task_success
correctness
completeness
tool_success
planning_success
recovery_success
memory_reuse
hallucination/error rate
regression rate
latency
token cost
compute cost
security violations
policy violations
long-horizon completion
```

Do not immediately flatten these into one score.

---

# 17. Pareto selection

Treat the candidate result as a vector:

```text
[accuracy,
 reliability,
 recovery,
 cost,
 latency,
 memory efficiency,
 safety,
 regression]
```

Keep a Pareto archive.

Example:

```text
Candidate A: highest accuracy, expensive
Candidate B: lower cost, slightly lower accuracy
Candidate C: strongest recovery
```

The executive can choose the appropriate member of the Pareto frontier for a deployment context.

---

# 18. Sealed holdout

Maintain test sets unavailable to the improvement loop.

```text
PUBLIC / DEVELOPMENT EVAL
        |
        v
candidate tuning

SEALED HOLDOUT
        |
        v
final selection check
```

Critical rule:

> The candidate may inspect its development benchmark results, but must not gain access to the answers, hidden tests, or acceptance logic of the sealed holdout.

The holdout should be re-seeded periodically so the system cannot overfit permanently.

---

# 19. Adversarial evaluator

Run separate tests for:
- unsafe tool requests;
- privilege escalation attempts;
- secret leakage;
- path traversal;
- destructive shell actions;
- prompt injection;
- evaluator manipulation;
- benchmark gaming;
- false claims of success;
- rollback bypass;
- policy bypass.

The candidate must not be able to disable this evaluator.

---

# 20. Benchmark generation

Add a self-generated curriculum subsystem:

```text
production failures
       +
capability gaps
       +
weak benchmark areas
       |
       v
curriculum generator
       |
       v
new test tasks
       |
       v
task validator
       |
       v
benchmark registry
```

Use two pools:

```text
development curriculum
sealed evaluation curriculum
```

Generated tests should be checked for:
- determinism where needed;
- objective verifiability;
- non-triviality;
- duplicate detection;
- leakage;
- evaluator stability.

---

# 21. Production failure replay

Every meaningful production failure should become a regression artifact.

```text
real failure
   ->
reproduce
   ->
freeze reproduction
   ->
add to regression suite
   ->
candidate must not reintroduce it
```

This creates a growing institutional memory.

---

# 22. Recovery system

Use explicit recovery states:

```text
ERROR
 |
 v
CLASSIFY
 |
 +-- transient -> retry
 |
 +-- tool-specific -> alternate tool
 |
 +-- planning -> replan
 |
 +-- knowledge -> research
 |
 +-- capability gap -> specialist
 |
 +-- repeated failure -> rollback
 |
 +-- unsafe/unknown -> stop/escalate
```

Every recovery attempt should be recorded.

---

# 23. Long-horizon execution

Never rely solely on context.

Persist:

```yaml
task_checkpoint:
  task_id:
  current_state:
  completed_steps:
  pending_steps:
  dependencies:
  artifacts:
  evidence:
  last_verified_state:
  next_actions:
```

Use checkpoint/replay so a six-hour task can continue after a restart.

---

# 24. Agent topology evolution

Treat team structure as an evolvable object.

Candidate A:

```text
planner -> coder -> verifier
```

Candidate B:

```text
researcher -> architect -> coder -> reviewer -> tester
```

Candidate C:

```text
planner
  |
  +-- researcher A
  +-- researcher B
  +-- code analyst
  |
  v
synthesizer
  |
  v
executor
  |
  v
independent verifier
```

Evaluate topology candidates on:
- success;
- latency;
- cost;
- failure recovery;
- communication overhead.

---

# 25. Tool evolution

The RSI system should detect repeated tool failure patterns.

Example:

```text
Repeated problem:
text search misses structural dependencies

       ->
capability gap:
semantic repository navigation

       ->
candidate tool:
AST/dependency graph tool

       ->
benchmark

       ->
promote if evidence improves
```

Tool candidates need:
- schema;
- permissions;
- timeout;
- resource budget;
- audit trail;
- tests;
- rollback.

---

# 26. Prompt evolution

Prompt candidates should be first-class versioned artifacts:

```yaml
prompt:
  id:
  parent:
  task_scope:
  text_digest:
  metrics:
  failure_clusters:
```

Evolution algorithm:

```text
select parent prompts
      ->
generate variants
      ->
evaluate
      ->
archive
      ->
promote
```

For high-value prompts, test under many task seeds to avoid overfitting.

---

# 27. Skill evolution

Skills are reusable executable knowledge.

```text
skills/
  skill-name/
    SKILL.md
    examples/
    tests/
    metadata.yaml
```

Metadata:

```yaml
skill:
  id:
  version:
  purpose:
  prerequisites:
  tools:
  triggers:
  benchmark_tasks:
  success_rate:
  known_failures:
```

The RSI engine can:
- discover a recurring successful procedure;
- synthesize a skill;
- test it;
- add it to the skill registry.

---

# 28. Memory evolution

Experiment with:
- chunking;
- retrieval strategy;
- reranking;
- temporal weighting;
- causal links;
- confidence;
- memory consolidation;
- duplicate removal;
- lesson expiration;
- task-specific memory.

Do not let memory grow without garbage collection.

---

# 29. Meta-RSI

A higher-level loop should improve the process that performs self-improvement.

```text
RSI engine
   |
   +-- Which mutation strategies work?
   +-- Which evaluators detect regressions?
   +-- Which benchmarks are predictive?
   +-- Which memories help?
   +-- Which candidate selection policy works?
   +-- Which model/router produces best improvement per cost?
```

This is **RSI-of-RSI**.

Keep it gated separately from ordinary capability improvement.

---

# 30. Scientific experiment manager

Every experiment should be reproducible.

```yaml
experiment:
  id:
  timestamp:
  parent_agent:
  candidate_agent:
  model:
  prompt_versions:
  skill_versions:
  tool_versions:
  benchmark_version:
  environment_digest:
  seeds:
  resource_budget:
  results:
  decision:
```

Required outputs:
- logs;
- metrics;
- diffs;
- artifacts;
- exact commands;
- evaluation results;
- failure analysis.

---

# 31. Promotion policy

Suggested gate:

```text
candidate build passes
AND
unit tests pass
AND
integration tests pass
AND
no critical regression
AND
security tests pass
AND
policy tests pass
AND
holdout threshold passes
AND
cost within budget
AND
latency within budget
AND
reproducibility check passes
THEN
candidate eligible for canary
```

Then:

```text
CANARY
  ->
shadow tasks
  ->
small live slice
  ->
monitor
  ->
PROMOTE or ROLLBACK
```

---

# 32. Human control levels

Use risk-tiered autonomy.

### Tier 0 — automatic
Memory writes, non-critical reflections, reversible local experiments.

### Tier 1 — automatic with tests
Prompt/skill/workflow evolution.

### Tier 2 — automatic candidate creation, manual production promotion
Tool changes and topology changes.

### Tier 3 — research-only
Core harness architecture or evaluator changes.

### Tier 4 — prohibited by default
Secret boundary changes, production credential changes, disabling safety/promotion/rollback, unrestricted self-replication.

---

# 33. Security architecture

Use defense in depth:

```text
Agent
 |
 v
Tool permission layer
 |
 v
Sandbox/container/VM
 |
 v
OS permission boundary
 |
 v
Network policy
 |
 v
Secret broker
 |
 v
External systems
```

Never inject long-lived secrets directly into model context where avoidable.

Use short-lived scoped credentials.

---

# 34. Resource governor

Track:

```text
tokens
time
CPU
RAM
GPU
network
API usage
parallel agents
tool calls
disk
```

Set:
- per-task budget;
- per-candidate budget;
- per-generation budget;
- daily budget;
- emergency shutdown threshold.

---

# 35. Event ledger

Create an append-only event stream.

Examples:

```text
GOAL_CREATED
PLAN_CREATED
TASK_STARTED
TOOL_CALLED
TOOL_FAILED
TASK_COMPLETED
EVALUATION_COMPLETED
FAILURE_DETECTED
GAP_DETECTED
HYPOTHESIS_CREATED
CANDIDATE_CREATED
CANDIDATE_TESTED
CANDIDATE_REJECTED
CANDIDATE_PROMOTED
ROLLBACK
POLICY_BLOCK
HUMAN_APPROVAL
```

The event ledger is the canonical history.

Never rewrite historical events.

---

# 36. Lineage graph

Every candidate needs lineage:

```text
parent
children
mutation
repair
hybrid
benchmark deltas
```

The system should answer:

```text
Why does version 42 exist?
Which version produced this skill?
Which experiment justified this change?
Which failure caused it?
What did it improve?
What did it regress?
```

---

# 37. Architecture knowledge base

Repository layout:

```text
docs/
  architecture/
  design-decisions/
  capabilities/
  skills/
  benchmarks/
  execution-plans/
  failures/
  research/
  runbooks/
  evolution/
```

Keep the entry file short:

```text
AGENTS.md
ARCHITECTURE.md
```

These should map the agent to authoritative documentation rather than become giant instruction blobs.

---

# 38. Recommended repository structure

```text
rsi-harness/
|
+-- core/
|   +-- executive/
|   +-- planner/
|   +-- orchestrator/
|   +-- state/
|   +-- scheduler/
|   +-- runtime/
|
+-- agents/
|   +-- researcher/
|   +-- coder/
|   +-- analyst/
|   +-- architect/
|   +-- reviewer/
|   +-- verifier/
|   +-- recovery/
|   +-- rsi/
|
+-- tools/
|   +-- shell/
|   +-- filesystem/
|   +-- browser/
|   +-- git/
|   +-- search/
|   +-- database/
|   +-- mcp/
|
+-- world/
|   +-- repository/
|   +-- runtime/
|   +-- processes/
|   +-- application/
|   +-- database/
|
+-- memory/
|   +-- working/
|   +-- episodic/
|   +-- semantic/
|   +-- procedural/
|   +-- reflective/
|   +-- capability/
|   +-- meta/
|
+-- evaluation/
|   +-- unit/
|   +-- integration/
|   +-- regression/
|   +-- capability/
|   +-- long_horizon/
|   +-- adversarial/
|   +-- security/
|   +-- holdout/
|   +-- metrics/
|
+-- rsi/
|   +-- observer/
|   +-- failure_miner/
|   +-- capability_gap/
|   +-- hypotheses/
|   +-- experiments/
|   +-- candidate_builder/
|   +-- mutation/
|   +-- repair/
|   +-- critic/
|   +-- verifier/
|   +-- selector/
|   +-- promoter/
|
+-- evolution/
|   +-- population/
|   +-- lineage/
|   +-- archive/
|   +-- pareto/
|   +-- curriculum/
|
+-- safety/
|   +-- policy/
|   +-- permissions/
|   +-- sandbox/
|   +-- secrets/
|   +-- audit/
|   +-- rollback/
|   +-- emergency_stop/
|
+-- registry/
|   +-- agents/
|   +-- prompts/
|   +-- skills/
|   +-- tools/
|   +-- evaluators/
|   +-- models/
|   +-- versions/
|
+-- experiments/
|   +-- candidates/
|   +-- runs/
|   +-- artifacts/
|   +-- results/
|
+-- docs/
|   +-- architecture/
|   +-- failures/
|   +-- capabilities/
|   +-- benchmarks/
|   +-- evolution/
|
+-- AGENTS.md
+-- ARCHITECTURE.md
+-- RSI.md
```

---

# 39. Candidate lifecycle

```text
DISCOVER
   |
   v
DIAGNOSE
   |
   v
HYPOTHESIZE
   |
   v
GENERATE
   |
   v
BUILD
   |
   v
STATIC CHECK
   |
   v
UNIT TEST
   |
   v
INTEGRATION TEST
   |
   v
BENCHMARK
   |
   v
ADVERSARIAL TEST
   |
   v
SEALED HOLDOUT
   |
   v
RESOURCE TEST
   |
   v
CANARY
   |
   +---- reject -> ARCHIVE
   |
   v
PROMOTE
   |
   v
RECORD LESSON
   |
   v
NEXT GENERATION
```

---

# 40. Main RSI loop pseudocode

```python
while system_running:

    goal = executive.next_goal()

    world = world_model.snapshot()

    plan = planner.make_plan(
        goal=goal,
        world=world,
        memory=memory.retrieve(goal),
    )

    trajectory = executor.run(plan)

    evidence = verifier.verify(
        goal=goal,
        trajectory=trajectory,
        world=world_model.observe(),
    )

    evaluation = evaluator.evaluate(
        goal=goal,
        trajectory=trajectory,
        evidence=evidence,
    )

    memory.record(
        trajectory=trajectory,
        evidence=evidence,
        evaluation=evaluation,
    )

    if evaluation.requires_recovery:
        recovery.execute(goal, trajectory, evaluation)

    failures = failure_miner.extract(
        trajectory=trajectory,
        evaluation=evaluation,
    )

    gaps = capability_detector.find(failures)

    opportunities = rsi_engine.rank_opportunities(
        failures=failures,
        gaps=gaps,
        history=memory,
    )

    for opportunity in opportunities:

        hypothesis = hypothesis_engine.create(opportunity)

        candidate = evolution.generate(
            parent=current_agent,
            hypothesis=hypothesis,
        )

        candidate_result = experiment_lab.run(candidate)

        evaluation = candidate_evaluator.full_eval(
            parent=current_agent,
            candidate=candidate,
            result=candidate_result,
        )

        archive.store(candidate, evaluation)

        if promotion_gate.eligible(evaluation):
            canary_result = canary.run(candidate)

            if promotion_gate.canary_pass(canary_result):
                registry.promote(candidate)
                current_agent = candidate
```

---

# 41. RSI experiment pseudocode

```python
def improve_agent(parent, opportunity):

    hypothesis = create_hypothesis(opportunity)

    candidates = variation_engine.generate(
        parent=parent,
        hypothesis=hypothesis,
        count=K,
    )

    survivors = []

    for candidate in candidates:

        build(candidate)

        if not static_checks(candidate):
            archive.reject(candidate, "static failure")
            continue

        run_unit_tests(candidate)
        run_integration_tests(candidate)

        result = benchmark(candidate)

        if regression_detected(result):
            archive.reject(candidate, "regression")
            continue

        adversarial = run_adversarial_suite(candidate)
        holdout = run_sealed_holdout(candidate)

        if not security_ok(adversarial):
            archive.reject(candidate, "security")
            continue

        if not holdout_ok(holdout):
            archive.reject(candidate, "holdout")
            continue

        survivors.append(candidate)

    ranked = pareto_select(survivors)

    return ranked
```

---

# 42. Higher-order RSI loop

The RSI engine itself becomes an object of evaluation:

```text
META-EVALUATION
   |
   +-- improvement yield
   +-- false-positive rate
   +-- regressions introduced
   +-- compute per successful improvement
   +-- benchmark predictiveness
   +-- candidate diversity
   +-- recovery quality
   +-- archive quality
```

Then:

```text
RSI engine v1
   ->
observe weakness
   ->
create RSI-engine candidate
   ->
evaluate improvement process itself
   ->
promote RSI engine v2
```

Do this only after ordinary capability evolution is stable.

---

# 43. Anti-gaming requirements

The harness should actively test whether a candidate is gaming evaluation.

Examples:
- hard-code benchmark outputs;
- detect benchmark task names;
- modify test harness;
- weaken verification;
- suppress errors;
- fake tool outputs;
- alter metrics;
- delete failures from logs;
- train against visible test examples;
- overfit to development tasks.

Use:
- hidden tests;
- fresh task seeds;
- independent evaluators;
- environment-side verification;
- source/diff inspection;
- reproducibility;
- cross-run evaluation.

---

# 44. Failure-resistant promotion

A candidate can be "better" on average and still be unacceptable.

Example:

```text
task success:  +8%
latency:        +40%
security:       worse
regression:     2 critical failures
```

Reject it.

Promotion should therefore use:
- hard constraints;
- Pareto analysis;
- severity-weighted regressions;
- minimum sample sizes.

---

# 45. Reproducibility

For every experiment record:

```text
Git commit/digest
candidate hash
model identifier
prompt hashes
skill hashes
tool versions
benchmark version
environment digest
seed
resource limits
exact command
logs
outputs
```

Without this, the system cannot know whether improvement is real or noise.

---

# 46. Statistical gate

For noisy tasks, avoid promoting on a single run.

Use:
- multiple seeds;
- repeated trials;
- confidence intervals where appropriate;
- minimum effect size;
- regression confidence;
- holdout replay.

Conceptual rule:

```text
promote only when

observed_gain
  >
measurement_noise
  +
minimum_effect_threshold
```

For high-risk components, increase the evidence threshold.

---

# 47. Cost-aware RSI

Track:

```text
improvement_gain / compute_cost
```

A candidate that improves 1% using 20x the compute may not be useful.

Add an optimization objective:

```text
maximize:
  capability
  reliability
  recovery

while controlling:
  latency
  tokens
  API spend
  CPU/RAM/GPU
  tool calls
```

---

# 48. Model routing for RSI

Use a model ensemble where useful:

```text
cheap model:
  broad candidate generation

strong model:
  architecture proposals

specialist model:
  code review

independent judge:
  evaluation

local model:
  routine classification / summarization
```

Do not require the same model to generate, verify and approve every change.

---

# 49. Observability

Collect:

```text
traces
logs
metrics
tool calls
state transitions
memory accesses
retrieval results
candidate diffs
benchmark results
promotion decisions
rollbacks
```

Build a dashboard with:
- current agent version;
- capability map;
- active experiments;
- recent failures;
- top improvement opportunities;
- benchmark trend;
- cost;
- latency;
- rollback history;
- lineage tree.

---

# 50. Operational commands

Recommended control surface:

```text
/rsi status
/rsi observe
/rsi failures
/rsi gaps
/rsi propose
/rsi experiment
/rsi candidates
/rsi benchmark
/rsi holdout
/rsi lineage
/rsi archive
/rsi promote
/rsi rollback
/rsi pause
/rsi resume
/rsi kill
/rsi compare
/rsi explain
```

Useful agent commands:

```text
/plan
/replan
/research
/delegate
/verify
/review
/recover
/checkpoint
/trace
/memory
/skills
/tools
/capabilities
```

---

# 51. Recommended implementation phases

## Phase 0 — Baseline harness

Build:
- Hermes/existing agent integration;
- Git;
- tool registry;
- state manager;
- structured task record;
- basic evaluator.

Exit criterion:
- repeatable task execution.

---

## Phase 1 — Observability

Add:
- event ledger;
- traces;
- logs;
- metrics;
- trajectory recorder;
- artifact store.

Exit criterion:
- replay any important run.

---

## Phase 2 — Memory

Add:
- episodic;
- procedural;
- reflective;
- capability;
- meta memory.

Exit criterion:
- past failures can influence future task execution.

---

## Phase 3 — Verification

Add:
- evidence matrix;
- regression suite;
- independent verifier;
- task-specific evaluators.

Exit criterion:
- system can objectively say whether a task passed.

---

## Phase 4 — Failure mining

Add:
- root-cause classifier;
- capability-gap graph;
- opportunity ranking.

Exit criterion:
- repeated failures turn into explicit improvement targets.

---

## Phase 5 — Candidate lab

Add:
- Git branches/worktrees;
- Docker sandbox;
- candidate registry;
- automated build/test.

Exit criterion:
- agent can safely create isolated candidate versions.

---

## Phase 6 — RSI v1

Enable:
- prompt evolution;
- skill evolution;
- workflow evolution;
- memory evolution.

Exit criterion:
- automated candidates can improve benchmark performance without production mutation.

---

## Phase 7 — Structural evolution

Enable:
- tool evolution;
- topology evolution;
- selected harness source-code modifications.

Exit criterion:
- candidate code modifications can pass complete verification.

---

## Phase 8 — Evolutionary population

Add:
- lineage;
- archive;
- Pareto frontier;
- multi-candidate search;
- AVO-style variation.

Exit criterion:
- system can explore multiple improvement branches.

---

## Phase 9 — Self-generated curriculum

Add:
- failure-derived benchmarks;
- frontier task generation;
- benchmark validation;
- sealed holdout generation.

Exit criterion:
- test suite grows from real system weaknesses.

---

## Phase 10 — Meta-RSI

Enable controlled evolution of:
- mutation strategy;
- candidate selector;
- evaluator selection;
- benchmark strategy;
- memory strategy;
- RSI orchestration.

Exit criterion:
- improvement process itself demonstrates measurable gains on held-out tests.

---

# 52. Minimum viable RSI stack

For a first implementation, do not build everything.

Start with:

```text
Hermes
 +
Git worktree
 +
Docker sandbox
 +
trajectory recorder
 +
SQLite/Postgres experiment DB
 +
pytest/Jest/Playwright or task-specific evaluators
 +
benchmark registry
 +
failure miner
 +
candidate builder
 +
promotion gate
 +
rollback
```

Then layer in:
- memory evolution;
- AVO;
- population search;
- curriculum generation.

---

# 53. Example end-to-end improvement

Suppose the agent repeatedly fails software tasks because it edits the wrong files.

```text
10 failed trajectories
       |
       v
failure clustering
       |
       v
root cause:
poor repository mapping
       |
       v
capability gap:
architecture/repository discovery
       |
       v
hypotheses:
H1: stronger repo prompt
H2: dependency graph skill
H3: architecture index
H4: mandatory pre-edit map
       |
       v
generate 4 candidates
       |
       v
run benchmark
       |
       v
candidate 3 wins on
success + regression + cost
       |
       v
sealed holdout
       |
       v
canary
       |
       v
promote
       |
       v
record:
"repository mapping before edit improves multi-file task success"
```

The important property is that the lesson is connected to:
- evidence;
- change;
- benchmark;
- lineage;
- promotion.

---

# 54. What should NOT be copied blindly

Do not blindly implement:
- unrestricted self-replication;
- unrestricted secret access;
- evaluator self-modification;
- direct production mutation;
- infinite autonomous loops;
- self-deletion of historical logs;
- hidden backdoors for bypassing promotion;
- benchmark-only optimization.

The strongest shared theme across useful systems is not "give the agent unlimited power." It is **give the agent a controlled experimental environment in which better versions can be discovered and measured.**

---

# 55. Final target

The final system should behave like an autonomous engineering organization:

```text
                         USER GOAL
                            |
                            v
                      EXECUTIVE AGENT
                            |
                  +---------+---------+
                  |                   |
                  v                   v
              PLAN/GOVERN         RESEARCH
                  |                   |
                  +---------+---------+
                            |
                            v
                     MULTI-AGENT WORK
                            |
                            v
                         TOOLS
                            |
                            v
                       REAL WORLD
                            |
                            v
                      OBSERVABILITY
                            |
                            v
                       EVALUATION
                            |
                            v
                  FAILURE / GAP MINING
                            |
                            v
                    RSI META-AGENT
                            |
             +--------------+--------------+
             |              |              |
             v              v              v
          prompts         skills        code
             |              |              |
             +--------------+--------------+
                            |
                            v
                   CANDIDATE POPULATION
                            |
                            v
                       SANDBOX LAB
                            |
                            v
                TEST / SECURITY / HOLDOUT
                            |
                            v
                     PARETO SELECTION
                            |
                            v
                        CANARY
                            |
                            v
                       PROMOTION
                            |
                            v
                      NEW VERSION
                            |
                            +----> next cycle
```

---

# 56. Final architecture equation

```text
Advanced RSI Harness
=
Executive
+ Task Graph
+ Multi-Agent Runtime
+ World Model
+ Tools
+ Persistent State
+ Multi-Layer Memory
+ Reflection
+ Failure Mining
+ Capability Graph
+ Experiment Manager
+ Agentic Variation
+ Candidate Population
+ Lineage Archive
+ Objective Evaluation
+ Sealed Holdouts
+ Adversarial Testing
+ Pareto Selection
+ Promotion/Canary
+ Rollback
+ Immutable Event Ledger
+ Resource Governor
+ Security Kernel
+ Meta-RSI
```

---

# 57. Final design doctrine

### Principle 1
**Act, observe, measure, learn, modify, verify, then promote.**

### Principle 2
**No self-improvement without a measurable objective.**

### Principle 3
**No production self-editing without an isolated candidate.**

### Principle 4
**The evaluator must not be fully controlled by the candidate being evaluated.**

### Principle 5
**Keep the entire lineage and event history.**

### Principle 6
**Every real failure should become future evaluation data when it is reproducible and useful.**

### Principle 7
**Improve the harness, not only the prompt.**

### Principle 8
**Treat memory, skills, tools, workflows, topology and code as separate evolution domains.**

### Principle 9
**Use population search rather than assuming one mutation path is correct.**

### Principle 10
**When the system cannot establish evidence of improvement, it must not claim improvement.**

---

# 58. Primary research references

1. Exo — recursive AI agent harness:
   https://github.com/exoharness/exo

2. Exo — A Systems View of Recursive Self Improvement:
   https://github.com/exoharness/exo/blob/main/docs/RSI.md

3. Self-Improving Coding Agent:
   https://github.com/MaximeRobeyns/self_improving_coding_agent

4. SICA mirror:
   https://github.com/Rekhii/Self-Improving-Coding-SICA

5. Agent0:
   https://github.com/aiming-lab/Agent0

6. Agent0 paper:
   https://arxiv.org/abs/2511.16043

7. AlphaEvolve:
   https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

8. AlphaEvolve impact:
   https://deepmind.google/blog/alphaevolve-impact/

9. AVO:
   https://arxiv.org/abs/2603.24517

10. Darwin Gödel Machine:
    https://arxiv.org/abs/2505.22954

11. Adaptive Auto-Harness:
    https://github.com/A-EVO-Lab/AdaptiveHarness

12. Self-improving harness example:
    https://github.com/ethan-haas/self-improving-agent-harness

13. Self-improving harness example:
    https://github.com/harrystamatoukos/self-improving-agent-harness

14. Samsara:
    https://github.com/oldbulb/samsara

15. SEED self-evolving agent:
    https://github.com/NvlFR/self-evolving-agent

16. Awesome RSI research map:
    https://github.com/lobehub/awesome-rsi

17. OpenAI Harness Engineering:
    https://openai.com/index/harness-engineering/

18. Anthropic — Building effective agents:
    https://www.anthropic.com/engineering/building-effective-agents

19. Reflexion:
    https://arxiv.org/abs/2303.11366

---

# 59. Recommended project name

A useful repository-level name is:

```text
Hermes RSI Harness
```

or, if you want the architecture to be model-independent:

```text
Agentic RSI Evolution Harness
```

The key is to keep the **RSI substrate model-agnostic** so Hermes can be the executive today while different models can be swapped into worker, researcher, critic and evaluator roles later.

