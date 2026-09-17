# AVO High-End Agentic System Architecture

## Agentic Variation Operators for a General-Purpose, Long-Horizon, Self-Improving Agent Harness

**Research baseline:** 2026-09-15  
**Architecture status:** Research-informed reference architecture  
**Design intent:** Build a high-end, general-purpose agentic system inspired by NVIDIA's AVO architecture while extending it into a production-grade, domain-general harness with planning, memory, model routing, tool orchestration, evaluation, supervision, evolutionary search, multi-agent collaboration, and bounded recursive self-improvement.

---

## 0. Executive summary

NVIDIA's **Agentic Variation Operators (AVO)** changes the role of an LLM inside evolutionary search. Earlier systems commonly treated the LLM as a candidate generator inside a fixed search pipeline. AVO instead defines variation as an autonomous agent run:

\[
\operatorname{Vary}(P_t)=\operatorname{Agent}(P_t,K,f)
\]

where `P_t` is the lineage of previously committed solutions and scores, `K` is domain knowledge, and `f` is the evaluation/scoring function. The agent can inspect prior work, consult documentation, modify code, execute tests, analyze feedback, revise its approach, and only commit a candidate when the result satisfies the configured acceptance rule. NVIDIA's March 2026 paper explicitly describes this as replacing the traditional `Sample + Generate` decomposition with a self-directed agent loop. [1]

NVIDIA subsequently described the same underlying architecture as a general-purpose long-horizon agent built around **persistent memory, tools, external feedback, recovery, and supervision**. In an August 2026 report, NVIDIA reported a seven-day attention-kernel run with more than 500 explored directions and 40 committed versions, and later reported a 100.00 RHAE score on the 25-environment public ARC-AGI-3 set using the same underlying AVO architecture. NVIDIA also emphasizes that the result should be understood as a property of the whole agent system rather than the model alone. [2]

This document takes that principle and proposes a broader architecture:

> **The model supplies reasoning capacity; the AVO harness supplies persistence, action, feedback, selection, recovery, specialization, and the mechanisms by which useful reasoning compounds over time.**

The design is therefore an **AVO-native agent operating system** rather than a single agent loop.

The key extension is to make AVO hierarchical:

- **Task AVO** evolves the current solution or work product.
- **Strategy AVO** evolves the methods used to solve the task.
- **Tool/Skill AVO** evolves reusable capabilities.
- **Prompt/Policy AVO** evolves prompts, routing rules, and operating policies under tests.
- **Agent AVO** evolves agent graphs and role configurations.
- **Harness AVO** evolves the harness itself, but only inside explicit safety and governance boundaries.

The resulting platform is a **closed-loop agentic system**:

```text
Human / API Goal
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        EXECUTIVE CONTROL PLANE                      │
│ goal compiler • constraints • risk • budget • policy • mission     │
└─────────────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          AVO SEARCH PLANE                           │
│ lineage • populations • islands • archive • selection • novelty    │
└─────────────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       AGENT EXECUTION PLANE                         │
│ plan → act → observe → diagnose → revise → verify → commit        │
└─────────────────────────────────────────────────────────────────────┘
      │                       │                       │
      ▼                       ▼                       ▼
┌─────────────┐       ┌────────────────┐       ┌───────────────────┐
│ Tools / MCP │       │ Memory / KB     │       │ Evaluators / Evals│
│ shell/files │       │ episodic/semantic│       │ tests/critics/ROI │
└─────────────┘       └────────────────┘       └───────────────────┘
      │                       │                       │
      └───────────────────────┴───────────────────────┘
                              │
                              ▼
                  ┌────────────────────────────┐
                  │       SUPERVISOR           │
                  │ stagnation • anomaly      │
                  │ recovery • strategy shift │
                  └────────────────────────────┘
                              │
                              ▼
                     Commit / Reject / Fork
                              │
                              └──────► next AVO cycle
```

The remainder of this document specifies how to implement that architecture.

---

# 1. Research findings: what AVO actually establishes

## 1.1 AVO's central conceptual change

Classical evolutionary search can be represented as:

\[
P_{t+1}=\operatorname{Update}(P_t,(x_{t+1},f(x_{t+1})))
\]

with a variation step such as:

\[
\operatorname{Vary}(P_t)=\operatorname{Generate}(\operatorname{Sample}(P_t))
\]

The parent-selection procedure, candidate-generation procedure, population update logic, and evaluation protocol are largely defined by the host framework. AVO changes the role of the agent to:

\[
\operatorname{Vary}(P_t)=\operatorname{Agent}(P_t,K,f)
\]

The agent itself determines what to inspect, which prior solutions to use, which documentation to read, which tests to run, what changes to make, when to evaluate, how to interpret the result, and whether to revise its strategy. [1]

This is the architecture's foundational rule:

> **When the search procedure itself can benefit from reasoning, move the reasoning inside the search operator.**

## 1.2 AVO is not synonymous with "an LLM in a loop"

A simple LLM/tool loop is necessary but not sufficient. The publicly described AVO system adds the machinery that makes the loop persistent and evolutionary:

| AVO element | Function |
|---|---|
| Agentic variation operator | Autonomous multi-step search and implementation |
| Full lineage access | Historical context and accumulated evidence |
| Domain knowledge base | Documentation, source, architecture facts, references |
| Evaluation function | Objective external feedback |
| Correctness gate | Prevents high-performance but invalid candidates from winning |
| Persistent state | Allows long-running continuation |
| Commit policy | Converts successful work into durable lineage |
| Supervisor | Detects stagnation/unproductive cycles and redirects exploration |
| Git/state persistence | Reconstructable history and durable checkpoints |

NVIDIA's paper explicitly notes that the studied implementation uses a **single-lineage** regime to isolate the effect of the AVO operator; it states that archive- and island-based population structures are compatible but left for future work. [1]

That leaves substantial architectural room for a higher-end implementation.

## 1.3 The strongest general lesson

NVIDIA's 2026 follow-up describes the same core loop as transferring across very different environments. GPU kernel optimization provides compiler/profiler/test feedback; ARC-AGI-3 provides interactive environment feedback. The architecture remains conceptually similar: form hypotheses, act, observe evidence, preserve state, revise the internal model, recover from wrong assumptions, and continue. [2]

This suggests that **feedback architecture** is more transferable than any single domain-specific prompt.

---

# 2. Research lineage and design influences

The recommended architecture is not a literal copy of any one system. It combines several demonstrated patterns.

## 2.1 AlphaEvolve

Google DeepMind's AlphaEvolve uses an evolutionary coding-agent architecture where programs are proposed, automatically evaluated, and stored in an evolutionary database that influences subsequent candidate selection. It uses an ensemble of models to balance breadth and depth, and an automated evaluator to verify and score candidates. [3]

**Architectural lesson:** evolutionary archives, evaluator-driven selection, model specialization, and measurable objectives are powerful.

## 2.2 AVO

AVO moves the agent itself into the variation operator, allowing it to decide when to inspect, edit, evaluate, diagnose, and iterate rather than forcing the model into a fixed single-turn generator slot. [1]

**Architectural lesson:** the search controller should be partially learned/agentic when the task is open-ended and engineering-intensive.

## 2.3 Reflexion

Reflexion demonstrates that an agent can improve behavior through language-based feedback and episodic reflective memory without necessarily changing model weights. [4]

**Architectural lesson:** a memory of failures, lessons, and strategy adjustments can function as a form of test-time learning.

## 2.4 Self-Refine

Self-Refine uses iterative generation, feedback, and refinement and reports meaningful improvements over one-shot generation. [5]

**Architectural lesson:** feedback should be inside the generation loop, not bolted on after the final answer.

## 2.5 Voyager

Voyager demonstrates an open-ended agent with an automatic curriculum, an executable skill library, and iterative prompting based on environment feedback and execution errors. [6]

**Architectural lesson:** reusable executable skills are a first-class long-term memory substrate.

## 2.6 Deep Agents / durable agent runtime

The Deep Agents architecture emphasizes task planning, subagents, file systems, long-term memory, and a runtime suitable for durable execution. [7]

**Architectural lesson:** AVO should sit above a durable execution substrate rather than implementing all state handling directly in one monolithic loop.

## 2.7 Long-running agent harnesses

Anthropic's long-running-agent work highlights two recurring needs: initialize the environment explicitly and produce durable artifacts that allow future sessions to continue without reconstructing all prior context. [8]

**Architectural lesson:** context handoff is an infrastructure problem, not merely a prompt problem.

## 2.8 Agent evaluation

Anthropic's current evaluation guidance treats the complete agent harness as part of what is being evaluated. It recommends isolated trial environments, deterministic graders where possible, calibrated model-based judges when needed, multi-turn traces, partial credit, and grading outcomes rather than overly rigid action sequences. [9]

**Architectural lesson:** the evaluation harness should measure the whole agent system and should itself be treated as production infrastructure.

---

# 3. Design goals

A high-end AVO implementation should satisfy the following system-level goals.

## 3.1 Capability goals

1. Solve open-ended, multi-step tasks.
2. Operate over hours, days, or longer with durable checkpoints.
3. Use tools, code, web/research interfaces, files, databases, APIs, simulations, and external environments.
4. Learn from execution feedback.
5. Reuse prior solutions without blindly repeating failed paths.
6. Generate and test diverse strategies.
7. Route subtasks to appropriate models, agents, or tools.
8. Build and reuse skills.
9. Recover from failures autonomously when safe.
10. Continue improvement after a plausible solution is found.

## 3.2 Reliability goals

1. Never treat model confidence as external truth.
2. Prefer evidence-producing actions.
3. Separate hypotheses from validated facts.
4. Maintain explicit state and provenance.
5. Make every durable change attributable to a run, candidate, agent, and policy.
6. Use correctness gates before optimization or release gates.
7. Detect stagnation and strategy collapse.
8. Prevent infinite self-improvement loops.
9. Isolate risky side effects.
10. Permit rollback to known-good checkpoints.

## 3.3 Evolution goals

The system should be able to improve at several levels:

```text
Level 0  Output        ─ improve the current artifact
Level 1  Strategy      ─ improve how the task is solved
Level 2  Skill         ─ improve reusable procedures
Level 3  Agent         ─ improve role prompts, tools, routing, memory policy
Level 4  Workflow      ─ improve agent graph / orchestration
Level 5  Harness       ─ improve evaluation, memory, policies, tools, runtimes
Level 6  Meta-harness  ─ improve how improvements themselves are discovered
```

Only levels approved by the governance policy may modify the control plane.

---

# 4. Reference architecture

## 4.1 System planes

The architecture is divided into ten planes.

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│  P10 GOVERNANCE / TRUST PLANE                                                │
│  identity • permissions • secrets • approvals • risk budgets • audit         │
├───────────────────────────────────────────────────────────────────────────────┤
│  P9 OBSERVABILITY / EVAL PLANE                                                │
│  traces • metrics • graders • experiments • regressions • cost               │
├───────────────────────────────────────────────────────────────────────────────┤
│  P8 EVOLUTION PLANE                                                          │
│  AVO operators • populations • archives • selection • novelty • islands      │
├───────────────────────────────────────────────────────────────────────────────┤
│  P7 SUPERVISION PLANE                                                        │
│  stagnation • recovery • anomaly • intervention • strategy switching         │
├───────────────────────────────────────────────────────────────────────────────┤
│  P6 MEMORY / KNOWLEDGE PLANE                                                 │
│  working • episodic • semantic • procedural • lineage • evidence             │
├───────────────────────────────────────────────────────────────────────────────┤
│  P5 AGENT EXECUTION PLANE                                                     │
│  planning • reasoning • tool calls • subagents • reflection • verification   │
├───────────────────────────────────────────────────────────────────────────────┤
│  P4 CAPABILITY PLANE                                                         │
│  skills • tools • MCP • connectors • code execution • browsers • APIs        │
├───────────────────────────────────────────────────────────────────────────────┤
│  P3 MODEL PLANE                                                              │
│  frontier • local • specialist • judge • embedding • vision • speech         │
├───────────────────────────────────────────────────────────────────────────────┤
│  P2 RUNTIME / SANDBOX PLANE                                                  │
│  durable execution • isolated workspaces • containers • queues • snapshots   │
├───────────────────────────────────────────────────────────────────────────────┤
│  P1 ENVIRONMENT / WORLD PLANE                                                │
│  repository • web • databases • cloud • files • apps • simulators            │
└───────────────────────────────────────────────────────────────────────────────┘
```

The planes are intentionally decoupled. This permits components to be replaced independently and makes self-improvement safer.

---

# 5. Core mental model

A conventional agent often looks like:

```text
Goal → LLM → Tool → Observation → LLM → ... → Final answer
```

A high-end AVO-native system should instead look like:

```text
Goal
  ↓
Objective Contract
  ↓
Mission Graph
  ↓
Search Policy
  ↓
Select / Create Candidate Strategy
  ↓
┌─────────────────────────────────────────────────────┐
│                  AVO VARIATION RUN                  │
│                                                     │
│  Context Synthesis                                  │
│       ↓                                             │
│  Hypothesis Generation                              │
│       ↓                                             │
│  Strategy Selection                                 │
│       ↓                                             │
│  Plan / Subgoal Graph                                │
│       ↓                                             │
│  Execute via tools / subagents                      │
│       ↓                                             │
│  Observe evidence                                   │
│       ↓                                             │
│  Critique / Diagnose                                │
│       ↓                                             │
│  Repair / Re-plan                                   │
│       ↓                                             │
│  Evaluate                                            │
│       ↓                                             │
│  Verify / Adversarial check                          │
│       ↓                                             │
│  Commit, reject, fork, or archive                   │
└─────────────────────────────────────────────────────┘
  ↓
Update lineage + memory + skill library
  ↓
Supervisor evaluates trajectory
  ↓
Continue / redirect / terminate / escalate
```

The **candidate** is not necessarily source code. It can be any objectively evaluable artifact:

- a program
- a patch
- a research report
- a mathematical construction
- a prompt
- a policy
- a tool definition
- an agent graph
- an API implementation
- a model routing policy
- a database query strategy
- a product design
- a test suite
- a skill
- a workflow

That is how the AVO idea becomes general-purpose.

---

# 6. Executive Control Plane

## 6.1 Objective compiler

User language is not yet a sufficient objective for autonomous search. Convert it into a machine-readable **Objective Contract**.

### Objective Contract

```yaml
objective_id: OBJ-2026-000123
mission: "Build and verify a production-ready feature"
primary_outcome:
  description: "Feature X works in production-like environment"
  measurable: true

hard_constraints:
  - no data loss
  - no secret leakage
  - preserve backwards compatibility

soft_objectives:
  - latency
  - maintainability
  - cost
  - simplicity

success_metrics:
  correctness: 1.0
  regression_rate: 0.0
  latency_ms_p95: 200
  security_findings: 0

allowed_actions:
  - read_repo
  - modify_workspace
  - run_tests
  - use_web
  - spawn_subagents

disallowed_actions:
  - production_delete
  - credential_export

risk_class: R2
budget:
  max_wall_time_minutes: 240
  max_tokens: 2000000
  max_external_cost_usd: 10
  max_side_effect_level: 2

termination:
  stop_when:
    - all_hard_constraints_pass
    - all_required_evals_pass
    - evidence_threshold >= 0.95
  max_iterations: 100
```

## 6.2 Goal decomposition

The system should automatically determine whether the task is:

- atomic
- hierarchical
- exploratory
- optimization-oriented
- research-oriented
- implementation-oriented
- operational
- evolutionary
- adversarial

Do not force an evolutionary search for a task that has a deterministic solution.

## 6.3 Risk-aware autonomy tiers

```text
R0  Read-only
    search, summarize, inspect

R1  Reversible local change
    edit files, tests, draft output

R2  Controlled execution
    run code, install bounded dependencies, create artifacts

R3  External side effects
    APIs, tickets, cloud resources, emails, deployments

R4  High-impact / irreversible
    production data, financial, destructive, security-sensitive actions
```

Autonomy expands only when evaluation evidence is sufficient.

---

# 7. AVO Search Plane

## 7.1 Why the search plane exists

AVO is more than an agent loop. A persistent system needs a representation of what has already been attempted, what worked, what failed, what remains unexplored, and why a candidate was accepted.

The search plane therefore owns:

- populations
- lineages
- candidate identities
- ancestry
- scores
- novelty
- diversity
- islands
- archives
- mutation/variation policies
- selection policies
- exploration/exploitation balance

## 7.2 Candidate model

Every candidate is a first-class object.

```typescript
type Candidate = {
  candidateId: string;
  missionId: string;
  parentIds: string[];
  lineageId: string;
  artifactRef: string;
  strategyId: string;
  createdAt: string;
  status: "draft" | "evaluating" | "accepted" | "rejected" | "archived";

  objectiveScore: number;
  scoreVector: Record<string, number>;
  correctness: number;
  novelty: number;
  robustness: number;
  evidenceConfidence: number;
  cost: number;
  latencyMs: number;

  provenance: Provenance;
  evaluationRefs: string[];
};
```

## 7.3 Score vectors

Do not collapse every task into a single scalar too early.

Use:

\[
S(x)=[C,R,Q,E,N,T,K,V]
\]

where for example:

- `C` = correctness
- `R` = reliability/robustness
- `Q` = task quality
- `E` = evidence quality
- `N` = novelty
- `T` = time efficiency
- `K` = cost efficiency
- `V` = safety/compliance

Then define policy-specific aggregation.

### Hard-gated scoring

```text
if correctness < required:
    candidate = invalid
elif security_pass == false:
    candidate = invalid
else:
    candidate = eligible_for_optimization
```

This mirrors the key AVO correctness-gating principle reported by NVIDIA for kernel candidates. [1]

## 7.4 Pareto frontier

For multi-objective tasks, maintain a Pareto archive rather than one winner.

```text
                performance ↑
                          ●
                     ●
                ●
          ●
     ●
──────────────────────────────→ cost efficiency
```

A candidate can remain valuable because it is:

- faster
- cheaper
- safer
- more robust
- smaller
- easier to maintain
- more novel

than the current best.

## 7.5 Diversity pressure

A system that always picks the highest score can collapse onto a narrow strategy and miss better regions of the search space.

Add:

\[
U(x)=\alpha F(x)+\beta N(x)+\gamma D(x)+\delta E(x)-\lambda C(x)
\]

where:

- `F` = objective fitness
- `N` = novelty
- `D` = population diversity contribution
- `E` = evidence quality
- `C` = resource cost

Weights vary by mission.

---

# 8. Population architecture

NVIDIA's published experiment used a single lineage, but the higher-end design should support multiple population regimes while preserving the AVO operator itself. [1]

## 8.1 Single lineage

Best for:

- debugging
- deterministic optimization
- tasks with strong continuity
- low compute budgets

```text
x0 → x1 → x2 → x3 → x4
```

## 8.2 Branching lineage

Best when strategy divergence is useful.

```text
             ┌→ x2a → x3a
x1 → x2 ─────┤
             └→ x2b → x3b
```

## 8.3 Island model

Each island explores a strategy family.

```text
Island A: performance optimization
Island B: simplicity
Island C: robustness
Island D: architecture redesign
Island E: adversarial alternatives
```

Periodically perform migration.

## 8.4 Archive model

Maintain long-lived elite candidates and useful historical candidates separately.

```text
Elite Archive
   ├── Best overall
   ├── Best safety
   ├── Best cost
   ├── Best performance
   ├── Most novel
   └── Best transfer candidate

Exploration Archive
   ├── near misses
   ├── failed-but-informative
   ├── dead ends
   └── unexplored hypotheses
```

## 8.5 Failure archive

Do not erase failures.

Store:

- what was attempted
- why it failed
- evidence
- environment
- model
- tool sequence
- hypothesis
- repair attempts
- root cause
- whether the failure is reusable knowledge

A high-end system should learn not only from winners but from **structured negative evidence**.

---

# 9. The AVO Agent Runtime

## 9.1 Agent loop

```text
STATE
  │
  ▼
CONTEXT SYNTHESIS
  │
  ├─ objective
  ├─ current candidate
  ├─ lineage
  ├─ prior failures
  ├─ relevant skills
  ├─ domain knowledge
  ├─ policy/risk
  └─ budget
  │
  ▼
HYPOTHESIS
  │
  ▼
STRATEGY SELECTION
  │
  ▼
PLAN / SUBGOALS
  │
  ▼
ACT
  │
  ├─ inspect
  ├─ search
  ├─ code
  ├─ invoke tool
  └─ spawn worker
  │
  ▼
OBSERVE
  │
  ├─ stdout/stderr
  ├─ test result
  ├─ benchmark
  ├─ profiler
  ├─ API response
  └─ environment transition
  │
  ▼
DIAGNOSE
  │
  ├─ success
  ├─ failure
  ├─ partial progress
  ├─ uncertainty
  └─ hidden constraint discovered
  │
  ▼
REVISE
  │
  └─ continue until stop criterion
  │
  ▼
VERIFY
  │
  ▼
COMMIT / REJECT / FORK / ARCHIVE
```

## 9.2 Internal agent modes

The agent should not expose all modes manually to the user. The controller should select them dynamically.

```text
DISCOVER
PLAN
RESEARCH
IMPLEMENT
DEBUG
OPTIMIZE
VERIFY
ADVERSARIAL_TEST
RECOVER
REFLECT
CONSOLIDATE
MIGRATE
HANDOFF
```

A user can optionally force a mode, but normal operation is autonomous.

## 9.3 Tool selection as an internal policy

Do not hard-code a single fixed tool sequence.

Instead, define a capability utility:

\[
EU(tool)=\frac{expected\ information\ gain + expected\ progress}{cost + risk}
\]

The agent chooses the tool whose expected value exceeds the action threshold.

This follows the broader agent design principle that tools should be unambiguous, robust, and designed as part of the agent-computer interface. [10]

---

# 10. Strategy engine

The strategy engine is the most important extension beyond a simple autonomous coding agent.

## 10.1 Strategy object

```yaml
strategy_id: STRAT-482
name: "benchmark-driven-local-optimization"
class: optimization
preconditions:
  - measurable score available
steps:
  - baseline_measurement
  - bottleneck_detection
  - targeted_change
  - correctness_validation
  - performance_validation
stop_conditions:
  - no_gain_after: 8
  - regressions: 0
risk: low
expected_cost: medium
known_failures:
  - overfitting_to_single_benchmark
```

## 10.2 Strategy portfolio

Keep a dynamic portfolio instead of relying on one reasoning pattern.

Example:

```text
Portfolio
├── incremental patch
├── architectural refactor
├── rewrite
├── retrieve prior solution
├── transfer solution from adjacent domain
├── adversarial search
├── brute-force enumeration
├── simulation
├── external research
├── specialist consultation
├── parallel independent attempts
├── reverse engineering
├── hypothesis testing
├── constraint relaxation analysis
└── novel strategy synthesis
```

## 10.3 Strategy bandit

Use a contextual bandit or equivalent strategy selection policy over time.

Reward should incorporate:

```text
reward = Δobjective
       + novelty_gain
       + evidence_gain
       + reusable_knowledge_gain
       - compute_cost
       - risk_penalty
       - failure_penalty
```

Avoid over-optimizing for immediate reward; otherwise the system will stop exploring.

---

# 11. Knowledge plane

## 11.1 Knowledge hierarchy

```text
Knowledge
├── Objective knowledge
│   └── acceptance criteria / constraints
├── Environment knowledge
│   └── APIs / repo / system / world model
├── Domain knowledge
│   └── manuals / papers / code / specifications
├── Procedural knowledge
│   └── skills / runbooks / workflows
├── Historical knowledge
│   └── prior successful candidates
├── Negative knowledge
│   └── failed approaches / hazards
└── Meta knowledge
    └── which strategies work under which conditions
```

## 11.2 Just-in-time context

Do not dump the entire knowledge base into the model.

Use progressive disclosure:

1. retrieve lightweight references
2. inspect only relevant sections
3. load exact evidence when needed
4. record provenance
5. evict low-value context

This aligns with current context-engineering guidance emphasizing high-signal context, token efficiency, and just-in-time retrieval. [11]

## 11.3 Evidence object

```typescript
type Evidence = {
  evidenceId: string;
  sourceType: "tool" | "document" | "benchmark" | "test" | "human" | "memory";
  sourceRef: string;
  claim: string;
  extractedAt: string;
  confidence: number;
  provenance: string[];
  expiresAt?: string;
};
```

The agent should distinguish:

```text
FACT
HYPOTHESIS
INFERENCE
ASSUMPTION
UNVERIFIED
REJECTED
```

---

# 12. Memory architecture

A single conversation history is not enough for long-horizon work.

## 12.1 Memory tiers

```text
L0 Working memory
    current context window

L1 Episodic memory
    task episodes, observations, failures

L2 Semantic memory
    distilled facts, concepts, environment knowledge

L3 Procedural memory
    skills, tools, successful workflows

L4 Lineage memory
    candidate graph + scores + ancestry

L5 Strategic memory
    strategy success rates and applicability

L6 Organizational memory
    durable policies, architecture decisions, runbooks

L7 Meta-memory
    information about the harness's own behavior
```

## 12.2 Memory write policy

Agents should not write every token into long-term memory.

Write a memory item when at least one condition is true:

- it changes future behavior
- it captures a reusable fact
- it records a meaningful failure
- it explains a non-obvious decision
- it identifies a strategy with measured value
- it records a safety boundary
- it records a durable architecture decision

## 12.3 Memory quality score

\[
M_q = relevance \times reliability \times reuse\_probability \times recency\_factor
\]

Low-value memories should be compressed, merged, or retired.

---

# 13. Skill system

Skills should be executable assets, not merely prose instructions.

## 13.1 Skill structure

```text
skills/
  <skill-name>/
    SKILL.md
    schema.yaml
    examples/
    scripts/
    tests/
    evals/
    metadata.json
    provenance.json
```

## 13.2 Skill lifecycle

```text
Discover
  ↓
Install / enable
  ↓
Observe
  ↓
Evaluate usage
  ↓
Repair / optimize
  ↓
Version
  ↓
Canary
  ↓
Promote
  ↓
Retire
```

## 13.3 Skill evolution

A skill candidate should be treated like an evolutionary artifact:

```text
skill-v1 → skill-v2 → skill-v3
```

Each version gets:

- success rate
- failure rate
- task coverage
- cost
- latency
- safety incidents
- regression history

Voyager's executable skill library is an important precedent for treating reusable procedural capability as a compounding asset. [6]

---

# 14. Multi-agent architecture

AVO does not require a swarm, but a high-end implementation should support one.

## 14.1 Roles

```text
Executive Agent
   │
   ├── Research Agent
   ├── Planner Agent
   ├── Builder Agent
   ├── Debugger Agent
   ├── Security Agent
   ├── QA Agent
   ├── Performance Agent
   ├── Documentation Agent
   ├── Data Agent
   ├── Critic Agent
   ├── Red-Team Agent
   └── Meta-Optimizer Agent
```

## 14.2 Specialist agents are not peers by default

The executive should assign each worker:

```yaml
mission:
constraints:
allowed_tools:
budget:
expected_artifact:
acceptance_tests:
required_evidence:
report_schema:
```

This keeps the system auditable.

## 14.3 Parallelism patterns

### Sectioning

Independent subtasks execute concurrently.

### Voting

Several agents independently solve the same problem.

### Debate

One agent proposes, another attacks, a third adjudicates.

### Specialist pipeline

Research → implementation → QA → security → release.

### Island parallel search

Independent agent lineages explore different regions of the strategy space.

Anthropic's agent patterns explicitly identify parallelization, orchestrator-workers, and evaluator-optimizer structures as useful composable patterns. [10]

---

# 15. Evaluator architecture

The evaluator is as important as the generator.

## 15.1 Evaluator stack

```text
                         ┌──────────────┐
                         │ Human review │
                         └──────┬───────┘
                                │
                     ┌──────────▼──────────┐
                     │ Expert / LLM Judge  │
                     └──────────┬──────────┘
                                │
                    ┌───────────▼───────────┐
                    │ Adversarial Evaluator │
                    └───────────┬───────────┘
                                │
               ┌────────────────▼────────────────┐
               │ Deterministic Test / Benchmark │
               └────────────────┬────────────────┘
                                │
                         ┌──────▼──────┐
                         │ Environment │
                         └─────────────┘
```

## 15.2 Evaluation order

Always run the cheapest high-confidence gates first.

```text
syntax
 ↓
static checks
 ↓
unit tests
 ↓
integration tests
 ↓
security checks
 ↓
functional benchmark
 ↓
performance benchmark
 ↓
adversarial tests
 ↓
LLM judge / taste
 ↓
human checkpoint when required
```

## 15.3 Evaluator independence

The generation agent should not be the sole authority on whether its own work is correct.

Use:

- deterministic graders
- independent critics
- isolated evaluation environments
- counterexample generation
- reference solutions
- calibration tests

Anthropic's evaluation guidance specifically recommends stable isolated environments, deterministic graders where possible, calibrated model graders where necessary, and grading outcomes rather than overly rigid trajectories. [9]

---

# 16. Verification engine

## 16.1 Verification matrix

Every mission generates an evidence matrix.

| Requirement | Evidence | Status | Confidence | Independent check |
|---|---|---|---:|---|
| Requirement A | test-184 | PASS | 0.99 | yes |
| Requirement B | benchmark-55 | PASS | 0.94 | yes |
| Requirement C | manual review | PARTIAL | 0.72 | no |

## 16.2 Verification states

```text
UNKNOWN
  ↓
HYPOTHESIS
  ↓
TESTED
  ↓
SUPPORTED
  ↓
INDEPENDENTLY VERIFIED
  ↓
RELEASE-ELIGIBLE
```

The agent must not collapse `tested` into `verified` automatically.

---

# 17. Supervisor plane

The supervisor is not a second full-time agent executing the task. Its primary job is **trajectory control**.

## 17.1 Supervisor responsibilities

- detect stagnation
- detect cycling
- detect reward hacking
- detect hallucinated success
- detect tool misuse
- detect excessive cost
- detect context degradation
- detect strategy collapse
- detect repeated failed hypotheses
- suggest alternative strategies
- trigger checkpoints
- reduce autonomy on risk increases
- request human review

## 17.2 Stagnation detection

Example signals:

\[
Stagnation = f(\Delta score, failed\_attempts, repetition, novelty, evidence)
\]

Trigger intervention when:

```text
score_delta < epsilon for N accepted iterations
OR
same failure class repeats >= K times
OR
candidate edit similarity > threshold
OR
strategy entropy collapses
OR
cost / progress exceeds threshold
```

## 17.3 Supervisor action hierarchy

```text
Observe only
   ↓
Prompt strategy reminder
   ↓
Request reflection
   ↓
Force strategy switch
   ↓
Select different parent
   ↓
Spawn independent critic
   ↓
Fork an island
   ↓
Rollback checkpoint
   ↓
Reduce permissions
   ↓
Escalate to human
```

This is directly aligned with NVIDIA's description of supervision as a mechanism for detecting stagnation or unproductive cycles and redirecting search. [1][2]

---

# 18. Anti-reward-hacking layer

An evolutionary system will exploit weak evaluators.

Therefore, design explicitly against:

- benchmark overfitting
- test tampering
- evaluator manipulation
- hidden shortcut exploitation
- stale baseline abuse
- cherry-picking environments
- changing the goal instead of solving it
- manipulating logging
- deleting failing evidence
- gaming an LLM judge

## 18.1 Immutable evaluation contract

The candidate can never modify:

- benchmark definitions
- core graders
- release criteria
- audit trail
- baseline reference artifacts

without invoking a separate change-control process.

## 18.2 Evaluator ensemble

Use diverse evaluators so a candidate cannot easily overfit one grader.

```text
Candidate
  ├─ deterministic grader
  ├─ property-based grader
  ├─ adversarial grader
  ├─ regression suite
  ├─ performance grader
  └─ semantic judge
        ↓
   consensus / policy gate
```

---

# 19. Recursive Self-Improvement (RSI) architecture

RSI should be treated as **evolution over the harness**, not unrestricted self-modification.

## 19.1 Safe hierarchy

```text
Tier A — self-reflection
    improve notes, plans, hypotheses

Tier B — memory optimization
    improve retrieval / consolidation

Tier C — skill optimization
    improve executable skills

Tier D — prompt / policy candidates
    improve agent instructions under evals

Tier E — workflow optimization
    improve orchestration graph

Tier F — tool optimization
    improve tool wrappers / interfaces

Tier G — model-routing optimization
    improve model selection policies

Tier H — harness implementation
    propose code changes to the harness itself

Tier I — control-plane changes
    human approval required
```

## 19.2 RSI candidate loop

```text
Observe harness behavior
       ↓
Identify bottleneck
       ↓
Generate improvement hypotheses
       ↓
Create candidate change
       ↓
Run isolated regression suite
       ↓
Run historical replay benchmark
       ↓
Run adversarial suite
       ↓
Compare against incumbent
       ↓
Canary
       ↓
Promote or reject
```

## 19.3 Harness replay benchmark

Every harness release should replay a fixed corpus of prior tasks and traces.

Track:

```text
success rate
quality score
regression rate
cost
latency
tool-call efficiency
recovery success
safety violations
```

The harness is eligible for self-improvement only when the candidate beats the incumbent on a predefined acceptance matrix.

---

# 20. Context engineering architecture

Long context is not equivalent to good memory.

## 20.1 Context assembly pipeline

```text
Persistent state
   ↓
Candidate state
   ↓
Current objective
   ↓
Relevant lineage slice
   ↓
Relevant prior failures
   ↓
Relevant skills
   ↓
Relevant knowledge evidence
   ↓
Current tool results
   ↓
Current plan
   ↓
Final context budget optimizer
   ↓
LLM
```

## 20.2 Context budget optimizer

Define a selection objective:

\[
\max_{C \subseteq D} \sum_{i\in C} value(i)
\]

subject to:

\[
\sum_{i\in C} tokens(i)\le B
\]

where `B` is the current context budget.

Each item gets a predicted utility based on:

- relevance
- freshness
- reliability
- causality
- dependency
- novelty
- token cost

Current context-engineering guidance emphasizes exactly this problem: identify the highest-signal information needed for the next model decision rather than repeatedly loading everything accumulated during the run. [11]

---

# 21. Research engine

Because a general-purpose AVO agent will often need external knowledge, research should be integrated as an action mode.

## 21.1 Research loop

```text
Question
 ↓
Search strategy
 ↓
Source acquisition
 ↓
Source ranking
 ↓
Claim extraction
 ↓
Cross-source verification
 ↓
Contradiction analysis
 ↓
Evidence graph
 ↓
Synthesis
 ↓
Research memory
```

## 21.2 Source hierarchy

Prefer:

1. primary research
2. official documentation
3. official datasets / source code
4. high-quality secondary analysis
5. community evidence
6. unverified claims only as leads

## 21.3 Research provenance

Every material conclusion gets:

```text
claim → source → quote/paraphrase → date → confidence → conflict set
```

This prevents the agent from treating web retrieval as unquestioned truth.

---

# 22. Tool and MCP architecture

## 22.1 Capability registry

The agent should not receive an undifferentiated list of hundreds of tools.

Maintain a registry:

```yaml
tool_id: repo.search
capability: code_search
risk: R0
input_schema: ...
output_schema: ...
latency_class: fast
cost_class: free
reliability: 0.998
examples: ...
known_failures: ...
```

## 22.2 Capability routing

```text
Goal
 ↓
Required capability detection
 ↓
Tool candidate retrieval
 ↓
Permission filtering
 ↓
Cost / latency ranking
 ↓
Risk gate
 ↓
Tool invocation
```

## 22.3 Tool learning

Record tool performance over time:

```text
tool success
schema errors
retries
latency
cost
side effects
agent misuse
```

Use this evidence to improve tool descriptions and routing.

Anthropic's tool guidance notes that tool definitions are part of the ACI and that ambiguous or bloated tools create model errors. [10][11]

---

# 23. Model routing architecture

Do not force every step through the most expensive model.

## 23.1 Model roles

```text
Frontier reasoning model
    deep planning / critical decisions

Fast general model
    routine transformations / broad exploration

Coding specialist
    implementation / debugging

Vision model
    screenshots / documents / GUI

Speech model
    audio / meetings / voice

Embedding / retrieval model
    semantic search

Judge / critic model
    independent evaluation

Local model
    private / cheap / offline / high-volume tasks
```

## 23.2 Dynamic routing score

\[
RouteScore(m)=Q(m,task)+R(m,task)-Cost(m)-Latency(m)-Risk(m)
\]

Where model quality is task-specific.

## 23.3 Escalation ladder

```text
cheap model
   ↓ confidence low / risk high
stronger model
   ↓ unresolved
specialist model / parallel agents
   ↓ unresolved
frontier model
   ↓ unresolved
human checkpoint
```

---

# 24. Runtime and isolation architecture

Long-running autonomous agents require durable execution and isolated workspaces.

## 24.1 Workspace model

Each candidate gets an isolated workspace.

```text
Mission
 ├── baseline snapshot
 ├── candidate workspace
 ├── evaluator workspace
 ├── logs
 ├── artifacts
 └── provenance manifest
```

## 24.2 Snapshot model

```text
checkpoint-0
checkpoint-1
checkpoint-2
...
checkpoint-best
```

Never depend on a mutable working directory as the only source of truth.

## 24.3 Execution sandbox

Recommended isolation layers:

- process isolation
- container/VM boundary for high-risk tasks
- filesystem policy
- network egress policy
- secret broker
- resource quotas
- timeout controls
- syscall restrictions where practical

---

# 25. Security architecture

High autonomy increases the blast radius of mistakes. Security must therefore be in the architecture, not an optional plugin.

## 25.1 Principle of least privilege

Permissions are task-specific and temporary.

```text
agent identity
   ↓
mission permissions
   ↓
tool permissions
   ↓
resource permissions
   ↓
time-bounded capability token
```

## 25.2 Secrets

Agents should never receive broad environment secrets by default.

Use a broker that issues short-lived credentials scoped to:

- resource
- action
- duration
- task

## 25.3 Sensitive actions

Require either:

- stronger evaluator evidence
- secondary agent approval
- human approval

for high-impact actions.

## 25.4 Data boundary

Every tool should declare:

```yaml
data_classification:
  read: internal
  write: internal
  export: forbidden
```

---

# 26. Observability architecture

Every meaningful decision should be traceable.

## 26.1 Trace tree

```text
Mission
 ├── AVO run
 │    ├── strategy selection
 │    ├── context retrieval
 │    ├── model call
 │    ├── tool call
 │    ├── observation
 │    ├── reflection
 │    └── evaluation
 ├── candidate
 └── commit
```

## 26.2 Required telemetry

### Capability metrics

- task success
- objective score
- robustness
- evidence completeness
- transfer success

### Search metrics

- candidates explored
- accepted candidates
- rejection rate
- novelty
- strategy diversity
- lineage depth
- island migration

### Agent metrics

- tool success rate
- replanning rate
- recovery rate
- hallucination indicators
- repeated-action loops
- context utilization

### Resource metrics

- tokens
- model cost
- wall time
- CPU
- GPU
- storage
- network

## 26.3 Decision ledger

Record why a major decision happened:

```json
{
  "decision": "switch_strategy",
  "from": "incremental_patch",
  "to": "architecture_rewrite",
  "reason": [
    "8 failed attempts",
    "same root cause",
    "score plateau",
    "alternative strategy showed higher expected value"
  ],
  "evidence": ["eval-882", "trace-2911"],
  "policy": "supervisor.stagnation.v3"
}
```

---

# 27. Agent state machine

```text
              ┌──────────┐
              │ CREATED  │
              └────┬─────┘
                   ↓
              ┌──────────┐
              │ DISCOVER │
              └────┬─────┘
                   ↓
              ┌──────────┐
              │  PLAN    │
              └────┬─────┘
                   ↓
          ┌────────┴────────┐
          │                 │
          ↓                 ↓
     ┌─────────┐       ┌──────────┐
     │ EXECUTE │──────►│ OBSERVE  │
     └────┬────┘       └────┬─────┘
          │                 ↓
          │            ┌──────────┐
          └────────────│ DIAGNOSE │
                       └────┬─────┘
                            │
                  ┌─────────┼─────────┐
                  │         │         │
                  ↓         ↓         ↓
               REPAIR    REPLAN    VERIFY
                  │         │         │
                  └────┬────┴─────────┘
                       ↓
                   EVALUATE
                       │
             ┌─────────┼──────────┐
             │         │          │
             ↓         ↓          ↓
           COMMIT    FORK       REJECT
             │         │          │
             └─────────┴──────────┘
                       ↓
                  SUPERVISE
                       │
                 ┌─────┴─────┐
                 ↓           ↓
              CONTINUE      STOP
```

---

# 28. Failure-recovery matrix

| Failure | Detection | Recovery |
|---|---|---|
| Tool schema error | tool rejection | repair arguments / inspect schema |
| Compile failure | deterministic test | inspect error + patch |
| Repeated compile failure | failure clustering | strategy switch |
| Score regression | evaluator | rollback / alternative parent |
| No progress | supervisor | new strategy / fork |
| Context overload | context profiler | summarize / retrieve selectively |
| Hallucinated success | independent grader | invalidate candidate |
| Tool loop | repeated action signature | force reflection / alternate tool |
| Security risk | policy engine | stop / revoke permissions |
| External outage | tool health | retry with backoff / alternate tool |
| Resource exhaustion | quota manager | reduce parallelism / switch model |
| Evaluator inconsistency | grader disagreement | calibration / human review |

---

# 29. Anti-loop and anti-delusion controls

## 29.1 Loop detector

Hash recent action tuples:

```text
(tool, normalized_input, observation_class, next_action)
```

Detect repetition over a sliding window.

## 29.2 Belief/evidence separation

Maintain separate objects:

```text
belief_state
vs.
evidence_state
```

A model-generated statement does not become a fact until an evidence-producing action supports it.

## 29.3 Completion integrity

The agent cannot declare completion by itself if the Objective Contract requires external evidence.

The system should compute:

\[
Completion=\prod_i Gate_i
\]

where required hard gates must all pass.

---

# 30. Candidate commit protocol

A candidate becomes durable only after a commit transaction.

## 30.1 Commit sequence

```text
1. Freeze candidate artifact
2. Run hard correctness gates
3. Run security gates
4. Run regression suite
5. Run task evaluator
6. Compute score vector
7. Compare against incumbent / archive
8. Verify provenance
9. Generate candidate manifest
10. Commit immutable snapshot
11. Update lineage
12. Update memory
13. Emit trace event
```

## 30.2 Candidate manifest

```json
{
  "candidate_id": "cand-001",
  "parents": ["cand-000"],
  "strategy": "strategy-19",
  "model": "model-A",
  "tools": ["repo.search", "shell.exec", "benchmark.run"],
  "environment_hash": "sha256:...",
  "code_hash": "sha256:...",
  "score_vector": {
    "correctness": 1.0,
    "quality": 0.93,
    "performance": 0.87,
    "robustness": 0.96
  },
  "evidence": ["eval-22", "test-401"],
  "decision": "accepted"
}
```

---

# 31. Novelty engine

A high-end evolutionary agent needs a formal notion of novelty.

## 31.1 Sources of novelty

- textual novelty
- code structural novelty
- tool-sequence novelty
- strategy novelty
- architecture novelty
- domain-transfer novelty
- hypothesis novelty

## 31.2 Novelty score

Approximate using multiple signals:

\[
N(x)=w_1(1-sim_{semantic})+w_2(1-sim_{AST})+w_3(1-sim_{strategy})+w_4(new\_behavior)
\]

Novelty is useful for exploration, but it must never override hard correctness constraints.

---

# 32. Transfer learning across tasks

AVO should not restart from zero on each mission.

## 32.1 Transfer pipeline

```text
new task
 ↓
retrieve similar missions
 ↓
retrieve successful strategies
 ↓
retrieve reusable skills
 ↓
retrieve failure patterns
 ↓
adapt
 ↓
validate in new environment
 ↓
store transfer result
```

## 32.2 Transfer confidence

```text
same domain       → high prior
adjacent domain   → medium prior
different domain  → exploratory prior
```

NVIDIA's AVO transfer from MHA to GQA is a concrete example of an evolved solution being adapted to a related task, with the published report describing roughly 30 minutes of additional autonomous adaptation. [1][2]

---

# 33. Autonomous curriculum engine

For tasks without a known solution path, the system should generate intermediate goals.

## 33.1 Curriculum function

Choose the next task that maximizes expected information or capability gain:

\[
Task^*=\arg\max_t \frac{ExpectedKnowledgeGain(t)+ExpectedScoreGain(t)}{Cost(t)}
\]

## 33.2 Curriculum modes

```text
EASY → CORE → EDGE CASE → ADVERSARIAL → TRANSFER → NOVEL
```

This extends the curriculum principle demonstrated in Voyager into a general-purpose environment. [6]

---

# 34. Architecture for continuous operation

A seven-day experiment changes the engineering requirements versus a ten-minute task.

## 34.1 Persistent daemon

```text
Scheduler
  ↓
Mission queue
  ↓
AVO worker pool
  ↓
Durable state store
  ↓
Supervisor
  ↓
Checkpoint store
  ↓
Observability
```

## 34.2 Lease-based workers

Every long-running worker gets:

- lease ID
- heartbeat
- checkpoint interval
- maximum runtime
- resource quota

If a worker disappears, a new worker resumes from the last checkpoint.

## 34.3 Maintenance loop

Separate from task-solving loops:

```text
memory cleanup
skill validation
stale knowledge detection
archive compaction
index maintenance
eval suite refresh
cost optimization
security patching
```

---

# 35. Recommended repository / codebase layout

A practical implementation can use the following structure:

```text
avo-harness/
├── AGENTS.md
├── README.md
├── LICENSE
├── pyproject.toml
├── config/
│   ├── models.yaml
│   ├── policies.yaml
│   ├── autonomy.yaml
│   ├── scoring.yaml
│   └── environments.yaml
│
├── core/
│   ├── objectives/
│   ├── missions/
│   ├── candidates/
│   ├── lineage/
│   ├── populations/
│   ├── archives/
│   ├── strategies/
│   └── provenance/
│
├── avo/
│   ├── operator.py
│   ├── variation.py
│   ├── selection.py
│   ├── novelty.py
│   ├── islands.py
│   ├── migration.py
│   └── policies.py
│
├── agent/
│   ├── runtime.py
│   ├── planner.py
│   ├── executor.py
│   ├── reflection.py
│   ├── diagnosis.py
│   ├── recovery.py
│   ├── context.py
│   └── routing.py
│
├── supervisor/
│   ├── supervisor.py
│   ├── stagnation.py
│   ├── anomaly.py
│   ├── strategy_switch.py
│   └── escalation.py
│
├── memory/
│   ├── working.py
│   ├── episodic.py
│   ├── semantic.py
│   ├── procedural.py
│   ├── strategy.py
│   └── consolidation.py
│
├── knowledge/
│   ├── retrieval.py
│   ├── indexing.py
│   ├── provenance.py
│   └── sources.py
│
├── tools/
│   ├── registry.py
│   ├── sandbox.py
│   ├── shell.py
│   ├── filesystem.py
│   ├── browser.py
│   ├── git.py
│   ├── database.py
│   └── mcp/
│
├── evaluators/
│   ├── contract.py
│   ├── deterministic.py
│   ├── benchmark.py
│   ├── security.py
│   ├── adversarial.py
│   ├── judge.py
│   └── ensemble.py
│
├── skills/
│   ├── registry.py
│   ├── loader.py
│   ├── evaluator.py
│   └── evolution.py
│
├── rsi/
│   ├── proposal.py
│   ├── replay.py
│   ├── regression.py
│   ├── canary.py
│   └── promotion.py
│
├── governance/
│   ├── policy.py
│   ├── permissions.py
│   ├── approvals.py
│   ├── secrets.py
│   └── audit.py
│
├── runtime/
│   ├── scheduler.py
│   ├── queue.py
│   ├── workers.py
│   ├── checkpoints.py
│   └── leases.py
│
├── observability/
│   ├── tracing.py
│   ├── metrics.py
│   ├── events.py
│   └── dashboards.py
│
├── evals/
│   ├── tasks/
│   ├── graders/
│   ├── replay/
│   └── suites/
│
└── docs/
    ├── ARCHITECTURE.md
    ├── OBJECTIVE_CONTRACT.md
    ├── SECURITY.md
    ├── EVOLUTION.md
    ├── MEMORY.md
    ├── EVALS.md
    └── OPERATIONS.md
```

---

# 36. Core interfaces

## 36.1 AVO operator

```python
class AVOOperator(Protocol):
    async def vary(
        self,
        population: Population,
        knowledge: KnowledgeContext,
        objective: ObjectiveContract,
        evaluator: Evaluator,
        budget: Budget,
    ) -> CandidateResult:
        ...
```

## 36.2 Supervisor

```python
class Supervisor(Protocol):
    async def inspect(
        self,
        trajectory: Trajectory,
        population: Population,
        objective: ObjectiveContract,
    ) -> SupervisorDecision:
        ...
```

## 36.3 Evaluator

```python
class Evaluator(Protocol):
    async def evaluate(
        self,
        candidate: Candidate,
        objective: ObjectiveContract,
    ) -> EvaluationResult:
        ...
```

## 36.4 Memory

```python
class MemoryStore(Protocol):
    async def retrieve(self, query: MemoryQuery) -> list[MemoryItem]:
        ...

    async def write(self, item: MemoryItem) -> str:
        ...

    async def consolidate(self, scope: MemoryScope) -> None:
        ...
```

---

# 37. Mission orchestration pseudocode

```python
async def run_mission(objective):
    contract = compile_objective(objective)
    policy = policy_engine.resolve(contract)
    baseline = await initialize_environment(contract)

    population = await population_store.load_or_initialize(baseline)

    while not termination.should_stop(contract, population):
        context = await context_engine.build(
            contract=contract,
            population=population,
            memory=memory,
            knowledge=knowledge,
        )

        supervisor_state = await supervisor.inspect(
            trajectory=population.trajectory,
            population=population,
            objective=contract,
        )

        if supervisor_state.requires_intervention:
            population = await supervisor.apply(
                supervisor_state,
                population,
                contract,
            )

        strategy = await strategy_engine.select(context)

        candidate = await avo_operator.vary(
            population=population,
            knowledge=context.knowledge,
            objective=contract,
            evaluator=evaluator,
            budget=budget_manager.current(),
        )

        result = await evaluator.evaluate(candidate, contract)

        if result.hard_gates_passed:
            decision = selection_policy.decide(
                candidate=candidate,
                result=result,
                population=population,
            )
        else:
            decision = Reject(reason=result.failed_gates)

        await provenance.record(candidate, result, decision)

        if decision.accept:
            await checkpoint.commit(candidate, result)
            await population.accept(candidate, result)
            await memory.learn_from_success(candidate, result)
        else:
            await memory.learn_from_failure(candidate, result)

    return await finalizer.finalize(contract, population)
```

---

# 38. Strategy switching pseudocode

```python
async def choose_next_strategy(state):
    features = extract_strategy_features(state)

    if state.same_failure_count >= 5:
        return strategy_portfolio.diversified_alternative(features)

    if state.score_plateau >= 8:
        return strategy_portfolio.exploration_strategy(features)

    if state.evidence_confidence < 0.7:
        return strategy_portfolio.information_gathering(features)

    if state.correctness_pass and state.performance_regression:
        return strategy_portfolio.profiling_strategy(features)

    return strategy_bandit.select(features)
```

---

# 39. Multi-island AVO architecture

A high-end search system should support independent islands.

```text
                          GLOBAL ARCHIVE
                                │
             ┌──────────────────┼──────────────────┐
             │                  │                  │
             ▼                  ▼                  ▼
        ┌─────────┐        ┌─────────┐        ┌─────────┐
        │ Island A│        │ Island B│        │ Island C│
        │ exploit │        │ explore │        │ rewrite │
        └────┬────┘        └────┬────┘        └────┬────┘
             │                  │                  │
          AVO×N              AVO×N              AVO×N
             │                  │                  │
             └───────────┬──────┴──────┬───────────┘
                         ▼             ▼
                    migration     competition
                         │             │
                         └──────┬──────┘
                                ▼
                          GLOBAL ARCHIVE
```

Migration should happen on policy, not continuously.

Possible migration triggers:

- island stagnation
- high-quality novel candidate
- domain transfer opportunity
- periodic exchange

---

# 40. AVO operator family

The system should support multiple operator styles.

## 40.1 Incremental variation operator

Small change relative to parent.

## 40.2 Structural variation operator

Change architecture or decomposition.

## 40.3 Crossover-inspired operator

Extract useful mechanisms from multiple parents.

## 40.4 Repair operator

Focus on fixing a failing candidate.

## 40.5 Transfer operator

Adapt a known solution from another problem.

## 40.6 Adversarial operator

Search for counterexamples or failure cases.

## 40.7 Exploratory operator

Deliberately pursue high novelty.

## 40.8 Compression operator

Simplify a working solution while preserving score.

## 40.9 Cost-reduction operator

Optimize tokens, latency, compute, or financial cost.

## 40.10 Meta-operator

Change the strategy used to produce candidates.

The important AVO principle is that these need not be fixed functions. The agent can choose among, combine, or invent them under constraints.

---

# 41. Evaluation of the harness itself

The harness must be benchmarked as a system.

## 41.1 Core benchmark dimensions

| Dimension | Metric |
|---|---|
| Task success | % missions completed |
| Quality | objective score |
| Reliability | regression-free rate |
| Recovery | % failures recovered autonomously |
| Search efficiency | score gained per unit compute |
| Diversity | unique strategy coverage |
| Memory value | retrieval utility |
| Transfer | success on related tasks after prior learning |
| Tool efficiency | successful tool actions / calls |
| Safety | policy violations / mission |
| Cost | dollars or tokens per successful mission |
| Long-horizon continuity | success after context/session boundaries |

## 41.2 Benchmark suites

Build at least:

```text
Suite A: deterministic coding
Suite B: debugging
Suite C: research
Suite D: multi-step automation
Suite E: optimization
Suite F: interactive environment
Suite G: adversarial safety
Suite H: long-running continuation
Suite I: transfer learning
Suite J: self-improvement
```

---

# 42. Experiment management

Treat every AVO run as an experiment.

```text
experiment_id
├── hypothesis
├── control configuration
├── treatment configuration
├── seed
├── model versions
├── tool versions
├── environment hash
├── evaluator versions
├── metrics
├── traces
└── conclusion
```

Never compare scores across runs without checking evaluator and environment compatibility.

---

# 43. Canary promotion

Self-improvement candidates should first operate in shadow mode.

```text
candidate harness
      ↓
replay suite
      ↓
shadow traffic
      ↓
small canary
      ↓
comparison
      ↓
promotion / rollback
```

Use automatic rollback when:

- hard safety gate fails
- quality drops below threshold
- cost explodes
- latency exceeds threshold
- unexplained behavior divergence appears

---

# 44. Governance of self-modification

The architecture should explicitly separate three categories.

## 44.1 Self-observation

Always allowed within policy.

## 44.2 Self-proposal

Agent can propose changes to itself.

## 44.3 Self-promotion

Controlled by evaluation and governance.

This avoids the unsafe design where the system can directly rewrite its own control plane and immediately trust the result.

---

# 45. Recommended persistence stack

A practical implementation can use different stores for different workloads.

```text
Git / object store
    candidate artifacts + lineage snapshots

SQL database
    mission / candidate / score / provenance metadata

Vector or hybrid search index
    semantic memory + knowledge retrieval

Blob/object store
    logs / datasets / binaries / traces

Time-series store
    metrics

Event log
    append-only decisions / state transitions
```

Do not put the entire state of the world into one vector database.

---

# 46. Event-driven architecture

Emit durable events such as:

```text
MissionCreated
ObjectiveCompiled
ContextRetrieved
StrategySelected
CandidateCreated
ToolInvoked
ObservationRecorded
EvaluationStarted
EvaluationPassed
EvaluationFailed
CandidateAccepted
CandidateRejected
SupervisorTriggered
StrategySwitched
CheckpointCreated
MemoryWritten
SkillPromoted
RSIProposalCreated
CanaryStarted
CanaryFailed
CanaryPromoted
MissionCompleted
```

Consumers can independently build:

- dashboards
- audit logs
- memory
- analytics
- replay datasets
- training data

---

# 47. Replay engine

Replayability is crucial.

Given a candidate run, the system should be able to reproduce as much as possible:

```text
objective
+ environment snapshot
+ tool versions
+ model configuration
+ prompt/context manifest
+ random seeds
+ evaluator versions
+ artifact hashes
```

Some nondeterministic model behavior cannot be reproduced byte-for-byte, but the system should preserve sufficient provenance to compare behavior at the semantic level.

---

# 48. Prompt architecture

Avoid one enormous prompt.

Use layered context.

```text
CORE CONSTITUTION
  ↓
TASK CONTRACT
  ↓
ROLE
  ↓
CURRENT STRATEGY
  ↓
RELEVANT KNOWLEDGE
  ↓
CURRENT STATE
  ↓
AVAILABLE TOOLS
  ↓
EVALUATION CONTRACT
  ↓
OUTPUT / ACTION SCHEMA
```

Prompts should state invariants and interfaces, not micromanage every implementation step. Current harness-design guidance similarly recommends durable repository instructions and structured artifacts rather than an ever-growing monolithic instruction file. [12]

---

# 49. Agent constitution

A useful system constitution can be summarized as:

```text
1. Optimize for the objective, not for appearing successful.
2. Treat external evidence as the authority for world state.
3. Distinguish facts, hypotheses, assumptions, and guesses.
4. Prefer reversible actions before irreversible actions.
5. Verify before claiming completion.
6. Preserve useful state and provenance.
7. Learn from failures without repeating them blindly.
8. Search broadly when progress stalls.
9. Prefer simpler reliable solutions when scores are comparable.
10. Never weaken a hard constraint merely to improve a score.
11. Ask for permission only when policy requires it.
12. Improve the harness only through measurable, reversible, tested changes.
```

---

# 50. Cost-aware orchestration

A high-end system can become expensive quickly.

## 50.1 Cost controller

Track cost in real time.

\[
RemainingBudget = Budget - (tokens + compute + external\_cost)
\]

## 50.2 Cheap-first pattern

```text
cheap heuristic
 ↓
small model
 ↓
specialist model
 ↓
frontier model
```

But skip escalation when an earlier result already satisfies the objective with sufficient evidence.

## 50.3 Value of information

Before an expensive action, estimate:

\[
VOI = \frac{Expected\ improvement\ or\ uncertainty\ reduction}{Cost}
\]

Do not spend large amounts on actions with low expected value.

---

# 51. Stopping theory

Never define termination as simply "the model says done."

Use a stopping function:

\[
Stop = HardGatesPass \land EvidenceThreshold \land Stability \land BudgetPolicy
\]

Optional additional conditions:

- no material regression
- no unresolved high-risk findings
- score plateau with diminishing returns
- better candidates not discovered within exploration budget
- human approval requirement satisfied

## 51.1 Search should stop for either success or evidence of diminishing returns

This avoids endless optimization after the practical optimum is already achieved.

---

# 52. Distinguishing AVO from standard agent loops

| Architecture | Agent's role | Memory | Evolution | Supervisor |
|---|---|---|---|---|
| Basic tool agent | execute task | usually short/optional | no | limited |
| Workflow | fixed stages | explicit | no | deterministic |
| Multi-agent | specialist workers | shared/explicit | no | orchestrator |
| AlphaEvolve-style | candidate proposer inside evolutionary framework | evolutionary database | yes | framework-driven |
| AVO | variation operator | persistent lineage + memory | yes | trajectory supervision |
| Proposed architecture | variation operator + strategy optimizer + bounded meta-evolution | multi-tier | hierarchical | dedicated supervisor + governance |

The proposed system is therefore best described as:

> **A general-purpose agent operating system whose central search primitive is an AVO-style autonomous variation operator.**

---

# 53. Recommended technology decomposition

This architecture intentionally does not require one framework.

## Runtime

- Python for orchestration and AI tooling
- optional TypeScript control/UI layer
- async task execution
- event-driven state transitions

## Agent framework

Use a minimal, inspectable agent runtime or a durable graph runtime. Framework choice should remain replaceable.

## Execution

- containers/VMs for isolation
- worktree snapshots
- queues
- background workers

## State

- SQL for authoritative metadata
- object storage for large artifacts
- hybrid retrieval for knowledge
- Git for code lineage when appropriate

## Integration

- MCP for tool interoperability where suitable
- standard HTTP/gRPC interfaces for internal services

## Observability

- structured event logs
- distributed traces
- metrics
- artifact/provenance registry

---

# 54. Minimum Viable AVO implementation

Do not build everything at once.

The smallest useful AVO core is:

```text
1. Objective contract
2. Candidate object
3. Lineage store
4. Agent loop
5. Tool interface
6. External evaluator
7. Correctness gate
8. Commit policy
9. Persistent checkpoint
10. Stagnation supervisor
11. Failure memory
```

This already captures the core AVO concept.

---

# 55. Production AVO implementation

Add next:

```text
12. Strategy portfolio
13. Novelty scoring
14. Pareto archive
15. Multiple islands
16. Specialist agents
17. Context optimizer
18. Skill library
19. Research engine
20. Tool registry
21. Model router
22. Adversarial evaluator
23. Replay benchmark
24. Observability
25. Cost controller
26. Permission broker
```

---

# 56. Frontier AVO implementation

Finally add:

```text
27. Hierarchical AVO
28. Strategy evolution
29. Agent-graph evolution
30. Skill evolution
31. Harness replay-based RSI
32. Automated canary promotion
33. Transfer learning archive
34. Autonomous curriculum
35. Cross-domain strategy mining
36. Meta-memory
37. Population islands with adaptive migration
38. Search policy evolution
39. Evaluator evolution under governance
40. Continuous benchmark generation
```

The last group is where the system becomes an experimental platform for agentic self-improvement rather than only a task-solving agent.

---

# 57. Full reference architecture

```text
                                         ┌──────────────────────┐
                                         │       HUMAN/API      │
                                         └──────────┬───────────┘
                                                    │
                                                    ▼
                                  ┌──────────────────────────────┐
                                  │   OBJECTIVE COMPILER         │
                                  │ goals • constraints • risk  │
                                  │ metrics • budget • stop     │
                                  └──────────────┬───────────────┘
                                                 │
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │       EXECUTIVE AGENT        │
                                  │ decomposition • routing     │
                                  │ mission graph • priorities  │
                                  └──────────────┬───────────────┘
                                                 │
                           ┌─────────────────────┼─────────────────────┐
                           │                     │                     │
                           ▼                     ▼                     ▼
                  ┌────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                  │ KNOWLEDGE      │   │ MEMORY          │   │ CAPABILITY      │
                  │ DOMAIN KB      │   │ multi-tier      │   │ registry        │
                  │ research       │   │ strategy memory │   │ skills / MCP    │
                  └───────┬────────┘   └───────┬─────────┘   └────────┬────────┘
                          │                    │                      │
                          └────────────────────┼──────────────────────┘
                                               │
                                               ▼
                              ┌─────────────────────────────────┐
                              │         AVO SEARCH PLANE        │
                              │ populations • lineage • archive│
                              │ selection • novelty • islands  │
                              └────────────────┬────────────────┘
                                               │
                                               ▼
                              ┌─────────────────────────────────┐
                              │       AVO VARIATION AGENT       │
                              │                                 │
                              │ context → hypothesis → plan    │
                              │ → act → observe → diagnose      │
                              │ → revise → verify → commit      │
                              └───────────────┬─────────────────┘
                                              │
                    ┌─────────────────────────┼────────────────────────┐
                    │                         │                        │
                    ▼                         ▼                        ▼
             ┌────────────┐           ┌──────────────┐        ┌──────────────┐
             │ TOOL RUNTIME│           │ SUBAGENTS    │        │ ENVIRONMENT   │
             │ shell/code  │           │ specialists  │        │ repo/web/API │
             │ browser/MCP │           │ critics/etc. │        │ DB/simulator │
             └──────┬──────┘           └──────┬───────┘        └──────┬───────┘
                    │                         │                       │
                    └─────────────────────────┼───────────────────────┘
                                              │
                                              ▼
                                  ┌─────────────────────────────┐
                                  │        EVALUATOR STACK      │
                                  │ correctness • regression   │
                                  │ security • benchmark       │
                                  │ adversarial • semantic     │
                                  └──────────────┬──────────────┘
                                                 │
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │       SCORE / EVIDENCE      │
                                  │ vector • provenance        │
                                  │ confidence • novelty       │
                                  └──────────────┬──────────────┘
                                                 │
                              ┌──────────────────┼──────────────────┐
                              │                  │                  │
                              ▼                  ▼                  ▼
                         ┌─────────┐       ┌─────────┐       ┌─────────────┐
                         │ COMMIT  │       │ REJECT  │       │ ARCHIVE/FORK│
                         └────┬────┘       └────┬────┘       └──────┬──────┘
                              │                  │                   │
                              └──────────────────┼───────────────────┘
                                                 │
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │        SUPERVISOR            │
                                  │ stagnation • anomalies      │
                                  │ reward hacking • recovery   │
                                  │ strategy switching          │
                                  └──────────────┬───────────────┘
                                                 │
                          ┌──────────────────────┼──────────────────────┐
                          │                      │                      │
                          ▼                      ▼                      ▼
                     CONTINUE               REDIRECT               ESCALATE
                          │                      │                      │
                          └──────────────────────┼──────────────────────┘
                                                 │
                                                 ▼
                                    NEXT AVO VARIATION CYCLE

                 ┌─────────────────────────────────────────────────────┐
                 │               GOVERNANCE / SECURITY                  │
                 │ permissions • secrets • sandbox • audit • policy    │
                 └─────────────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────────────┐
                 │            RSI / META-EVOLUTION PLANE               │
                 │ replay • proposal • regression • canary • promote   │
                 └─────────────────────────────────────────────────────┘

                 ┌─────────────────────────────────────────────────────┐
                 │              OBSERVABILITY / REPLAY                  │
                 │ traces • metrics • events • artifacts • experiments │
                 └─────────────────────────────────────────────────────┘
```

---

# 58. The most important architectural rules

## Rule 1 — Make the evaluator external to the generator

The agent should be grounded in world feedback, not self-reported success. [1][2][9]

## Rule 2 — Make history operational

Past work must influence future work through lineage, memory, and failure archives.

## Rule 3 — Make failure useful

A failed candidate should become evidence, not disappear.

## Rule 4 — Make stagnation observable

The supervisor should detect when exploration has collapsed.

## Rule 5 — Optimize for evidence, not verbosity

The next action should be chosen for expected progress or uncertainty reduction.

## Rule 6 — Keep hard constraints outside the optimizer

The agent should not be able to negotiate away non-negotiable requirements.

## Rule 7 — Separate candidate space from control space

Task artifacts may evolve freely within task policy. Harness/control-plane changes require stronger gates.

## Rule 8 — Make every durable change replayable

No opaque state mutations.

## Rule 9 — Prefer modularity over monoliths

Every capability should be replaceable.

## Rule 10 — Allow simple operation when complex search is unnecessary

The system should be able to recognize when a normal deterministic workflow is sufficient. This is consistent with broader agent engineering guidance: complexity should be introduced only when it measurably improves outcomes. [10]

---

# 59. What should NOT be copied blindly from current AVO descriptions

The following distinctions matter.

### AVO is not the same as a fully general AGI architecture

The published AVO paper is a research architecture for autonomous evolutionary search. Its central contribution is the agentic variation operator. [1]

### The published study does not establish every extension in this document

Multi-island populations, hierarchical self-improvement, skill evolution, evaluator evolution, model-routing evolution, and harness-level RSI are architectural proposals here, not claims that NVIDIA's published AVO implementation already contains all those capabilities.

### The ARC-AGI-3 result is not proof that a single architecture solves general intelligence

NVIDIA reports 100.00 RHAE on the 25-environment public set and 183 completed levels, but it also explicitly cautions that comparisons with other systems are not controlled ablations and that the result should not be interpreted as isolating the contribution of AVO itself. [2]

### The objective function remains critical

AVO can only optimize what the evaluator can measure. Weak or incomplete evaluation can produce apparently excellent but invalid solutions.

---

# 60. Recommended implementation roadmap

## Phase 0 — Foundation

Implement:

- objective contracts
- tool registry
- agent loop
- workspace isolation
- deterministic evaluation
- candidate objects
- lineage persistence
- basic supervisor

**Exit criterion:** one agent can improve a measurable artifact over multiple iterations without losing state.

## Phase 1 — True AVO core

Implement:

- autonomous parent inspection
- autonomous tool selection
- autonomous evaluation timing
- failure memory
- commit/reject logic
- stagnation redirection

**Exit criterion:** no externally imposed `sample → generate → evaluate` sequence is required inside the variation step.

## Phase 2 — Search expansion

Add:

- archives
- novelty
- strategy portfolio
- branching
- Pareto scoring
- islands

**Exit criterion:** system can explore multiple strategies without collapsing too early.

## Phase 3 — General-purpose capability

Add:

- research mode
- browser/web
- code execution
- database adapters
- skills
- specialist agents
- dynamic model routing

**Exit criterion:** same AVO engine can solve different domain tasks by swapping tools/evaluators.

## Phase 4 — Long-horizon reliability

Add:

- durable checkpoints
- context compaction
- session handoffs
- replay
- cost governance
- robust observability

**Exit criterion:** multi-hour/multi-day runs resume after worker restarts without losing mission state.

## Phase 5 — Bounded RSI

Add:

- harness benchmark suite
- replay corpus
- improvement proposal generator
- canary harnesses
- automatic rollback

**Exit criterion:** harness can discover and safely promote measured improvements to non-critical components.

## Phase 6 — Meta-evolution

Add:

- strategy evolution
- skill evolution
- workflow evolution
- routing optimization
- meta-memory
- cross-domain transfer

**Exit criterion:** the system can produce measurable improvements to how it solves future tasks while maintaining safety and regression gates.

---

# 61. Final architecture thesis

The most important idea to preserve is not a particular prompt, framework, or model.

It is this transformation:

```text
OLD

Model
  ↓
Generate candidate
  ↓
External framework evaluates
  ↓
Framework chooses next candidate

NEW

Agent
  ↕
Inspect
  ↕
Reason
  ↕
Act
  ↕
Observe
  ↕
Evaluate
  ↕
Revise
  ↕
Remember
  ↕
Select
  ↕
Explore
  ↕
Recover

with a supervisor continuously asking:

"Is the search still productive, safe, and evidence-grounded?"
```

The complete high-end design is therefore best understood as a **closed-loop evolutionary operating system for agentic work**:

\[
\boxed{
\text{Goal}
\rightarrow
\text{Objective Contract}
\rightarrow
\text{AVO}
\rightarrow
\text{Actions}
\rightarrow
\text{Evidence}
\rightarrow
\text{Evaluation}
\rightarrow
\text{Selection}
\rightarrow
\text{Memory}
\rightarrow
\text{Supervision}
\rightarrow
\text{Evolution}
}
\]

And at the next level:

\[
\boxed{
\text{AVO}_{task}
\rightarrow
\text{AVO}_{strategy}
\rightarrow
\text{AVO}_{skill}
\rightarrow
\text{AVO}_{agent}
\rightarrow
\text{AVO}_{harness}
}
\]

with a strict governance boundary around every transition toward greater system autonomy.

This architecture preserves the core insight demonstrated by NVIDIA—**system design can unlock long-horizon capability that is not visible from model benchmarks alone**—while extending the idea into a modular, measurable, multi-agent, continuously evaluated, and bounded self-improving platform. [2]

---

# 62. Sources and further reading

1. **Chen et al., NVIDIA — “AVO: Agentic Variation Operators for Autonomous Evolutionary Search.”** arXiv:2603.24517, March 25, 2026. Describes the formal AVO operator, lineage access, knowledge base, evaluation function, single-lineage experiment, correctness-gated score, persistent evolution, and supervisory intervention.
   - https://arxiv.org/abs/2603.24517

2. **NVIDIA Developer Blog — “NVIDIA AVO Reaches 100% on ARC-AGI-3, Demonstrating a Frontier-Level General-Purpose Architecture for Long-Horizon Autonomous Agents.”** August 21, 2026. Describes persistent memory, supervision, transfer across GPU optimization and ARC-AGI-3, and the reported 100.00 RHAE public-set result.
   - https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3-demonstrating-a-frontier-level-general-purpose-architecture-for-long-horizon-autonomous-agents/

3. **Novikov et al., Google DeepMind — “AlphaEvolve: A coding agent for scientific and algorithmic discovery.”** 2025.
   - https://arxiv.org/abs/2506.13131
   - https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

4. **Shinn et al. — “Reflexion: Language Agents with Verbal Reinforcement Learning.”** 2023.
   - https://arxiv.org/abs/2303.11366

5. **Madaan et al. — “Self-Refine: Iterative Refinement with Self-Feedback.”** 2023.
   - https://arxiv.org/abs/2303.17651

6. **Wang et al. — “Voyager: An Open-Ended Embodied Agent with Large Language Models.”** 2023.
   - https://arxiv.org/abs/2305.16291

7. **LangChain — Deep Agents overview.** Describes planning, subagents, file systems, long-term memory, and durable execution using LangGraph runtime components.
   - https://docs.langchain.com/oss/python/deepagents/overview

8. **Anthropic — “Effective harnesses for long-running agents.”** November 26, 2025.
   - https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

9. **Anthropic — “Demystifying evals for AI agents.”** January 9, 2026.
   - https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

10. **Anthropic — “Building effective agents.”** December 19, 2024. Covers workflow/agent distinctions, parallelization, orchestrator-workers, evaluator-optimizer, and agent/tool design.
   - https://www.anthropic.com/engineering/building-effective-agents

11. **Anthropic — “Effective context engineering for AI agents.”** Context selection, progressive disclosure, tool design, and just-in-time retrieval.
   - https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

12. **OpenAI Codex documentation / repository context conventions.** Example of durable repository guidance through `AGENTS.md` and structured project artifacts.
   - https://github.com/openai/codex/blob/main/docs/agents_md.md
   - https://github.com/openai/openai-cookbook/blob/main/examples/codex/iterating-development-workflows-with-codex.md

13. **ARC Prize — ARC-AGI-3 competition and technical documentation.** Useful for understanding interactive long-horizon evaluation, exploration, modeling, goal-setting, planning, and action efficiency.
   - https://arcprize.org/competitions/2026/arc-agi-3
   - https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf

14. **Yuksekgonul et al. — “Learning to Discover at Test Time.”** 2026. Relevant to test-time learning beyond frozen-model evolutionary search.
   - https://arxiv.org/abs/2601.16175

15. **Ye et al. — “ReEvo: Large Language Models as Hyper-Heuristics with Reflective Evolution.”** 2024.
   - https://arxiv.org/abs/2402.01145

---

# Appendix A — Suggested configuration skeleton

```yaml
system:
  name: avo-harness
  version: 0.1.0

agent:
  mode: autonomous
  context_budget_tokens: 120000
  max_internal_iterations: 50
  reflection_every: 8

objective:
  hard_gates:
    correctness: 1.0
    security: pass
  soft_weights:
    quality: 0.35
    performance: 0.30
    robustness: 0.20
    cost_efficiency: 0.15

search:
  population_mode: islands
  islands: 4
  archive_size: 500
  exploration_ratio: 0.35
  novelty_weight: 0.15
  migration_every: 20

supervisor:
  enabled: true
  stagnation_window: 8
  repeated_failure_threshold: 5
  strategy_collapse_threshold: 0.15
  max_redirects: 3

memory:
  episodic: true
  semantic: true
  procedural: true
  strategic: true
  consolidation_interval: 20

security:
  default_autonomy: R1
  production_actions: approval_required
  secret_broker: true
  sandbox: true

rsi:
  enabled: true
  allowed_levels:
    - reflection
    - memory
    - skills
    - prompts
    - routing
  harness_code_changes: gated
  control_plane_changes: human_approval

budget:
  max_wall_time_minutes: 240
  max_tokens: 2000000
  max_external_cost_usd: 10

termination:
  max_iterations: 100
  evidence_threshold: 0.95
  minimum_improvement: 0.01
```

---

# Appendix B — Suggested first prototype milestone

A strong first prototype should not attempt full RSI or a swarm. Build this exact slice:

```text
User goal
  ↓
Objective compiler
  ↓
Single AVO agent
  ↓
Repo/filesystem/shell tools
  ↓
Deterministic evaluator
  ↓
Correctness gate
  ↓
Git-backed lineage
  ↓
Failure memory
  ↓
Stagnation supervisor
  ↓
Commit / reject
  ↓
Next iteration
```

Once this works reliably, add islands, skills, specialist agents, research, dynamic routing, and finally bounded RSI.

---

# Appendix C — One-sentence architecture definition

> **A high-end AVO system is a durable, evidence-grounded, evaluator-driven agent runtime in which an autonomous agent controls the variation process over a persistent population/lineage, while memory, tools, supervisors, selection, and governance convert repeated interaction into measurable long-horizon improvement.**
