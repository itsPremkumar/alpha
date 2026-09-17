# RSI Architecture Plan for a Hermes-Based Agent Harness

## 0. Executive goal

Build a controlled Recursive Self-Improvement (RSI) subsystem that lets the harness improve its prompts, skills, memory policies, tool implementations, routing, planning strategies, verification strategies, and—only under stronger gates—its own source code and model configuration.

The central rule is:

> The live agent never directly rewrites the live agent.
> It produces improvement proposals. A separate evolution/evaluation pipeline creates an isolated candidate, measures it against the incumbent, and promotes it only when the evidence satisfies explicit gates.

This turns RSI into an engineering optimization loop rather than unrestricted self-modification.

---

## 1. Research principles to borrow

### 1.1 Darwin Gödel Machine (DGM)
DGM uses iterative code modification plus empirical validation and keeps an archive of agent variants, forming a tree of candidate agents. This provides the architectural idea of **population/lineage + mutation + benchmark validation**, rather than replacing one running agent blindly.

Source: https://arxiv.org/abs/2505.22954

### 1.2 A-EVOLVE / A-Evolve
A-Evolve separates the agent, evaluation data, and evolution algorithm through a filesystem/workspace contract. Its design makes prompts, skills, tools, and memory explicit evolvable layers and uses observations plus Git history for iterative evolution.

Sources:
- https://github.com/A-EVO-Lab/a-evolve
- https://github.com/A-EVO-Lab/a-evolve/blob/main/DESIGN.md

### 1.3 AlphaEvolve
AlphaEvolve combines model-generated candidate programs with automated evaluators and an evolutionary database that selects promising candidates for future mutation. The key transferable lesson is: **make improvement measurable** and let the evaluator, not the model's confidence, determine progress.

Source: https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

### 1.4 Reflexion
Reflexion shows that an agent can improve behavior without changing model weights by converting task feedback into reflective memory that influences future attempts. This is the lowest-risk RSI layer and should be implemented first.

Source: https://arxiv.org/abs/2303.11366

### 1.5 Voyager
Voyager combines automatic curriculum, an executable skill library, and iterative self-verification. For a general-purpose harness, the transferable lesson is to treat learned skills as reusable executable assets instead of retaining only natural-language memories.

Source: https://arxiv.org/abs/2305.16291

### 1.6 Sakana RSI work
Sakana AI's RSI Lab describes a research program spanning DGM, ShinkaEvolve, and optimization/self-learning systems. This is useful as a current research direction, but individual systems should not be treated as production-safe merely because they demonstrate benchmark gains.

Source: https://sakana.ai/rsi-lab/

---

## 2. Target architecture

```text
                         HUMAN / USER
                              |
                              v
                    +---------------------+
                    |   HERMES SUPERVISOR |
                    | goals / policy / UX |
                    +----------+----------+
                               |
                  +------------+-------------+
                  |                          |
                  v                          v
        +-------------------+       +-------------------+
        | EXECUTION SYSTEM  |       |   RSI CONTROLLER  |
        | planner / swarm   |       | observe / evolve  |
        | tools / memory    |       | evaluate / gate   |
        +---------+---------+       +---------+---------+
                  |                           |
                  v                           v
        +-------------------+       +-------------------+
        | TRAJECTORY/EVENT  |------>| OBSERVATION STORE |
        | telemetry         |       | failures / wins   |
        +-------------------+       +---------+---------+
                                              |
                                              v
                                    +-------------------+
                                    | DIAGNOSER          |
                                    | root cause / gaps  |
                                    +---------+---------+
                                              |
                                              v
                                    +-------------------+
                                    | IMPROVEMENT LAB    |
                                    | candidate builder  |
                                    +---------+---------+
                                              |
                           +------------------+------------------+
                           |                  |                  |
                           v                  v                  v
                     prompt mutator    skill mutator     tool/code mutator
                           |                  |                  |
                           +------------------+------------------+
                                              |
                                              v
                                    +-------------------+
                                    | CANDIDATE SANDBOX |
                                    | isolated workspace|
                                    +---------+---------+
                                              |
                                              v
                                    +-------------------+
                                    | EVALUATION MATRIX |
                                    | tests / tasks /   |
                                    | safety / quality  |
                                    +---------+---------+
                                              |
                         +--------------------+--------------------+
                         |                    |                    |
                         v                    v                    v
                     reject                review             promote
                         |                    |                    |
                         v                    v                    v
                   archive evidence      approval queue      new incumbent
                                              |
                                              v
                                    +-------------------+
                                    | VERSION / LINEAGE |
                                    | Git + manifests   |
                                    +---------+---------+
                                              |
                                              v
                                    +-------------------+
                                    | RUNTIME MONITOR   |
                                    | regression/watch  |
                                    +-------------------+
                                              |
                                              +------> next RSI cycle
```

---

## 3. Core RSI philosophy

RSI should operate at several layers with increasing risk.

### Layer 0 — learning from execution
No system mutation. Extract failures, successful strategies, and useful observations.

Examples:
- "PowerShell command failed because path quoting was wrong."
- "Before editing a DB migration, inspect schema and migration history."
- "Web research tasks need source-quality validation before synthesis."

### Layer 1 — memory evolution
Write bounded, structured lessons to episodic/semantic memory.

### Layer 2 — skill evolution
Create, revise, merge, deprecate, and version `SKILL.md` files and executable skill assets.

### Layer 3 — prompt/policy evolution
Mutate system prompt fragments, planner rules, tool-selection rules, verification recipes, and agent-role instructions.

### Layer 4 — workflow/routing evolution
Change which subagents run, in what order, with what model, budget, tools, and verification depth.

### Layer 5 — tool evolution
Generate or modify helper tools, MCP adapters, scripts, parsers, test harnesses, and recovery utilities.

### Layer 6 — code evolution
Modify harness source code in a disposable candidate workspace, run comprehensive tests, then stage for promotion.

### Layer 7 — model/configuration evolution
Change model selection, fallback order, context budgets, temperature/reasoning configuration, or local/remote routing based on benchmark evidence.

Do not make all layers mutable from day one. Start with Layers 0–3, then 4–5, and only later permit 6–7.

---

## 4. Separation of control planes

Create five independent planes.

### 4.1 Execution Plane
Runs user tasks.

Components:
- Hermes supervisor
- planner
- subagent/swarm manager
- tool runtime
- memory runtime
- verifier
- task state machine

### 4.2 Observation Plane
Collects evidence without deciding changes.

Inputs:
- tool calls
- tool outputs
- failures
- retries
- test results
- user corrections
- latency
- token/cost usage
- task completion
- verification outcomes
- safety events

### 4.3 Evolution Plane
Generates candidate improvements.

Components:
- failure classifier
- root-cause analyzer
- opportunity detector
- mutation planner
- candidate generator
- candidate merger

### 4.4 Evaluation Plane
Determines whether a candidate is better.

Components:
- deterministic unit/integration tests
- benchmark suite
- replay suite
- adversarial suite
- safety checks
- task success evaluator
- regression detector
- performance/cost evaluator

### 4.5 Governance Plane
Controls promotion, permissions, rollback, and audit.

Components:
- policy engine
- change risk classifier
- approval mode
- sandbox manager
- secret boundary
- immutable audit log
- kill switch
- rollback manager

---

## 5. The RSI state machine

Every improvement must have an explicit lifecycle.

```text
OBSERVED
   |
   v
DIAGNOSED
   |
   v
IDEA_GENERATED
   |
   v
CANDIDATE_CREATED
   |
   v
STATIC_CHECKED
   |
   v
SANDBOX_TESTED
   |
   v
BENCHMARKED
   |
   +------ FAIL ------> ARCHIVED
   |
   v
REGRESSION_CHECKED
   |
   +------ FAIL ------> ARCHIVED
   |
   v
SAFETY_CHECKED
   |
   +------ FAIL ------> ARCHIVED
   |
   v
PROMOTION_ELIGIBLE
   |
   +------ approval required ------> APPROVAL_QUEUE
   |
   v
PROMOTED
   |
   v
MONITORED
   |
   +------ regression ------> ROLLBACK
   |
   v
ESTABLISHED
```

Never allow an untracked transition such as `candidate -> live`.

---

## 6. Workspace contract

Use a dedicated filesystem contract modeled on the strong parts of A-Evolve.

```text
harness/
├── core/                         # protected runtime
├── plugins/
│   ├── rsi/
│   ├── evaluator/
│   ├── memory/
│   └── ...
├── workspace/
│   └── live/
│       ├── manifest.yaml
│       ├── prompts/
│       │   ├── system.md
│       │   ├── planner.md
│       │   ├── verifier.md
│       │   └── fragments/
│       ├── skills/
│       ├── tools/
│       ├── memory/
│       ├── policies/
│       └── routing/
├── rsi/
│   ├── observations/
│   ├── diagnoses/
│   ├── proposals/
│   ├── candidates/
│   ├── evaluations/
│   ├── archive/
│   ├── lineage/
│   └── approvals/
├── benchmarks/
├── replay/
├── snapshots/
└── audit/
```

The live workspace is versioned. Every promotion produces a Git commit/tag or equivalent immutable snapshot.

---

## 7. Manifest design

Create `manifest.yaml`:

```yaml
agent:
  id: hermes-rsi
  version: 0.1.0
  entrypoint: core.supervisor:run

workspace:
  root: workspace/live

rsi:
  enabled: true
  mode: propose_only
  max_parallel_candidates: 4
  max_cycles_per_session: 3
  max_mutation_depth: 2

  evolvable_layers:
    - memory
    - skills
    - prompts
    - routing
    - tools

  protected_layers:
    - core
    - governance
    - secret_policy
    - evaluator_contract

promotion:
  min_task_success_delta: 0.02
  max_regression_delta: 0.01
  require_safety_pass: true
  require_replay_pass: true
  require_human_approval_for:
    - core_code
    - permissions
    - network_policy
    - secret_access
```

This makes the mutation boundary machine-readable.

---

## 8. Observation schema

All runtime experiences should become structured events.

```json
{
  "event_id": "evt_123",
  "task_id": "task_456",
  "session_id": "sess_789",
  "timestamp": "2026-09-16T08:00:00+05:30",
  "agent_version": "0.1.0",
  "model": "model-id",
  "goal": "complete requested task",
  "plan": ["..."],
  "actions": [
    {
      "tool": "shell",
      "input_class": "powershell",
      "success": false,
      "latency_ms": 1200,
      "error_class": "path_quoting"
    }
  ],
  "verification": {
    "passed": false,
    "tests": 8,
    "passed_tests": 6
  },
  "user_feedback": null,
  "cost": {
    "input_tokens": 0,
    "output_tokens": 0,
    "estimated_usd": 0
  },
  "outcome": "partial_failure"
}
```

Store raw evidence separately from model-generated interpretations.

Important distinction:

- `raw_event` = what happened
- `diagnosis` = what the analyzer thinks happened
- `proposal` = what could be changed
- `evaluation` = measured candidate result

Do not overwrite raw evidence with summaries.

---

## 9. Failure taxonomy

The RSI system needs a standard vocabulary.

```text
TASK_FAILURE
├── understanding
├── planning
├── decomposition
├── tool_selection
├── tool_usage
├── environment
├── permissions
├── coding
├── testing
├── verification
├── memory_retrieval
├── hallucination
├── source_quality
├── context_management
├── timeout
├── budget_exhaustion
├── coordination
├── user_requirement_miss
└── safety_policy
```

Every repeated failure should be converted into one or more improvement hypotheses.

---

## 10. Diagnosis pipeline

For each failure:

1. Reconstruct the trajectory.
2. Identify the earliest causal divergence.
3. Separate agent error from environment error.
4. Check whether an existing skill already addresses the problem.
5. Search memory for previous occurrences.
6. Cluster similar failures.
7. Estimate frequency and impact.
8. Generate candidate interventions.
9. Assign mutation type and risk.

Example:

```text
12 failures:
  PowerShell path quoting
      |
      +--> repeated
      +--> same tool
      +--> same error family
      +--> existing skill absent
      |
      v
Proposal:
  create skill/windows-powershell-path-safety
```

Do not immediately patch code when a skill or prompt change can solve the recurring behavior.

---

## 11. Improvement opportunity scoring

Use scoring for triage only—not as a replacement for evaluation.

```text
opportunity_score =
    frequency
  × impact
  × confidence
  × reuse_potential
  × expected_gain
  ÷ mutation_risk
```

The score decides what to test first. It must never directly authorize promotion.

---

## 12. Mutation engine

Implement mutation operators as plugins.

### Prompt operators
- clarify instruction
- add missing invariant
- add precondition
- add postcondition
- add failure recovery rule
- add verification rule
- reorder instructions
- compress redundant instructions
- create specialist fragment

### Skill operators
- create skill
- revise skill
- split skill
- merge skills
- add trigger condition
- add examples
- add verification checklist
- deprecate low-value skill

### Memory operators
- extract episode
- consolidate episodes
- deduplicate
- promote recurring lesson to semantic memory
- decay stale memory
- detect contradictory memories

### Routing operators
- change subagent order
- introduce parallelism
- add specialist
- change model assignment
- alter retry policy
- alter verification depth
- add critic/reviewer stage

### Tool operators
- add wrapper
- improve parser
- add retry/backoff
- add timeout handling
- add precondition validation
- add output normalization
- add read-only inspection tool

### Code operators
- fix defect
- refactor
- add test
- optimize
- isolate dependency
- harden error handling
- improve orchestration state machine

### Meta-evolution operators
Later, allow the system to evolve the mutation strategy itself:
- change candidate count
- change mutation mix
- change exploration/exploitation ratio
- change evaluator allocation
- add new mutation operator
- retire ineffective operator

---

## 13. Candidate generation

For each opportunity, generate several diverse candidates instead of one.

Example:

```text
Failure cluster: weak verification after code edits

Candidate A:
  Add explicit verify-after-edit skill.

Candidate B:
  Add a mandatory tester subagent after edits.

Candidate C:
  Add planner policy requiring pre/post test snapshots.

Candidate D:
  Combine A + C.
```

This is where the DGM/AlphaEvolve style of search becomes useful: diversity is a feature, not noise.

Use a candidate budget such as 2–8 variants for cheap changes and 1–3 variants for expensive code changes.

---

## 14. Candidate isolation

Every candidate must have its own workspace.

```text
rsi/candidates/
  cycle-004/
    cand-001/
      workspace/
      patch.diff
      manifest.yaml
      metadata.json
    cand-002/
      ...
```

On Windows, use a disposable directory plus process/container/VM isolation appropriate to the risk. The candidate must not share write access to the live workspace.

The candidate should receive:
- a snapshot of the incumbent
- the exact mutation proposal
- the benchmark subset
- required test fixtures
- read-only historical observations when needed

It should not receive unrestricted secrets.

---

## 15. Evaluation stack

Use multiple evaluator classes.

### 15.1 Deterministic evaluator
- unit tests
- type checks
- lint
- static analysis
- schema checks
- import/startup checks

### 15.2 Task benchmark evaluator
Replay representative real tasks.

Build a fixed benchmark corpus from successful and failed historical tasks.

### 15.3 Regression evaluator
Compare candidate vs incumbent on:
- success rate
- failure rate
- verification rate
- tool errors
- latency
- token usage
- cost
- user correction rate

### 15.4 Adversarial evaluator
Try to break the candidate:
- malformed input
- contradictory requirements
- tool failures
- network outages
- timeouts
- corrupted files
- prompt injection
- context overflow
- permission denial

### 15.5 Safety evaluator
Verify:
- permissions unchanged unless explicitly allowed
- secret boundaries intact
- network rules unchanged
- protected files untouched
- audit logging intact
- kill switch still works

### 15.6 Human evaluation
For high-risk changes, present a concise evidence packet rather than raw logs.

---

## 16. Evaluation matrix

Do not promote based on one metric.

```text
                 Incumbent     Candidate      Delta
Task success       0.72          0.78         +0.06
Verification       0.61          0.75         +0.14
Tool errors        0.18          0.11         -0.07
Latency            11.2s          12.0s        +0.8s
Cost               $0.021         $0.020       -$0.001
Safety             PASS           PASS          --
Replay             PASS           PASS          --
Adversarial        PASS           PASS          --
```

Use confidence intervals or repeated trials for noisy evaluations. Require a meaningful improvement on the target metric and bounded regressions elsewhere.

---

## 17. Promotion gates

Recommended default policy:

### Gate A — validity
- candidate parses
- candidate starts
- schema valid
- no protected files changed

### Gate B — correctness
- deterministic tests pass
- representative replay passes

### Gate C — improvement
- target capability improves
- no major unrelated regression

### Gate D — safety
- policy suite passes
- permissions unchanged unless authorized
- secrets isolated

### Gate E — operational quality
- latency/cost remains within budget
- memory growth remains bounded

### Gate F — approval
- low-risk: automatic promotion
- medium-risk: optional review queue
- high-risk: mandatory human approval

---

## 18. Risk classes

```text
R0: memory-only lesson
R1: new/edited skill
R2: prompt fragment
R3: routing/workflow
R4: tool implementation
R5: model/provider configuration
R6: harness source code
R7: permissions / security / governance
```

Suggested policy:

```text
R0-R2 -> automatic after evaluation
R3-R4 -> automatic only with strong regression + safety tests
R5    -> approval or restricted auto mode
R6    -> candidate testing + human approval initially
R7    -> never autonomous in the initial architecture
```

The exact thresholds should be configurable.

---

## 19. Rollback system

Every promotion gets:

```text
version
parent_version
candidate_id
change_hash
benchmark_report
safety_report
promoter
promoted_at
```

Maintain:
- latest known-good
- previous known-good
- emergency stable

Automatic rollback triggers may include:
- sharp task-success drop
- new critical safety violation
- repeated crash loop
- abnormal tool use
- cost explosion
- corruption detection

Rollback must be deterministic and fast.

---

## 20. Lineage and archive

Keep an archive of all candidates, not only the winner.

```text
             v0
          /      \
        v1        v2
       /  \         \
     v3    v4       v5
            |
            v6
```

Record:

```yaml
candidate_id: cand-006
parent: v4
mutations:
  - verify_skill
  - planner_fragment
fitness:
  task_success: 0.81
  verification: 0.84
  cost: 0.019
status: promoted
```

This enables:
- ablation
- reproduction
- rollback
- diversity preservation
- avoiding repeated failed mutations

---

## 21. Preventing evolutionary stagnation

Do not always mutate the current best agent.

Use three search modes:

```text
EXPLOITATION
  mutate current best

EXPLORATION
  mutate a diverse historical candidate

RECOMBINATION
  combine useful changes from different candidates
```

Example:

```text
Candidate A: better coding
Candidate B: better web research
Candidate C: better verification

Child D = A + B + C
```

But evaluate the child as a new candidate; never assume component improvements compose.

---

## 22. Diversity preservation

A common RSI failure mode is converging on one style that scores well on a narrow benchmark.

Track:
- strategy diversity
- skill diversity
- planner diversity
- model diversity
- benchmark diversity

Keep an archive of distinct candidates, not only the top scorer.

---

## 23. Benchmark design for your harness

Create a permanent `RSI-Bench`.

### Domain groups

```text
01 coding
02 debugging
03 repository understanding
04 terminal automation
05 web research
06 document work
07 data analysis
08 planning
09 multi-agent coordination
10 tool recovery
11 long-context tasks
12 user requirement following
13 verification
14 security boundary handling
15 autonomous long-horizon tasks
```

For each domain, maintain:
- easy tasks
- normal tasks
- hard tasks
- adversarial tasks
- regression tasks

The benchmark should contain real tasks from your own harness, not only public benchmarks.

---

## 24. Golden task set

Create approximately 100–500 replayable tasks over time.

For every task keep:

```yaml
id: coding_042
input: ...
expected_properties:
  - tests_pass
  - no_secret_leak
  - preserve_api
metrics:
  primary: success
  secondary:
    - verification
    - latency
    - cost
```

Start with 50 high-value tasks if compute is limited.

---

## 25. Learning from user feedback

User corrections are high-value evidence.

Convert:

```text
User: "I asked you to inspect, not modify."
```

into:

```text
failure_type: requirement_scope
lesson:
  distinguish read-only inspection from mutation
proposal:
  add read_only_intent_guard to planning policy
```

However, do not treat every user statement as globally applicable. Scope lessons to the relevant task family when appropriate.

---

## 26. RSI controller algorithm

```python
while rsi_enabled:
    observations = observer.collect_recent()

    clusters = diagnoser.cluster(observations)
    opportunities = opportunity_detector.find(clusters)

    opportunities = prioritize(opportunities)

    for opportunity in budget(opportunities):
        proposals = proposer.generate_diverse(opportunity)

        for proposal in proposals:
            candidate = evolver.materialize(
                incumbent=current_snapshot(),
                proposal=proposal,
            )

            if not validity_gate(candidate):
                archive(candidate, reason="invalid")
                continue

            report = evaluator.run(candidate)

            decision = promotion_gate(
                incumbent=current_metrics(),
                candidate=report,
                policy=governance_policy,
            )

            archive(candidate, report, decision)

            if decision.promote:
                promote(candidate)
                monitor(candidate)
```

The live execution loop should not block on every RSI cycle. RSI can run periodically or asynchronously, subject to resource budgets.

---

## 27. Asynchronous architecture

Use two modes.

### Online adaptation
Low-risk changes that can safely affect future tasks:
- memory lessons
- skill suggestions
- bounded routing hints

### Offline evolution
Expensive changes:
- prompt evolution
- workflow evolution
- tool code
- source code
- model configuration

```text
Live task loop ---> event queue ---> observation store
                                  |
                                  v
                           offline RSI worker
                                  |
                           candidate archive
                                  |
                             promotion queue
```

This prevents the agent from destabilizing itself during important user work.

---

## 28. Multi-agent RSI laboratory

Because your harness already targets multiple agents, dedicate agents to distinct RSI roles.

```text
                    RSI SUPERVISOR
                          |
       +------------------+-------------------+
       |                  |                   |
       v                  v                   v
   OBSERVER            SCIENTIST           CRITIC
       |                  |                   |
       v                  v                   v
   DATA AGENT         PROPOSER             RED TEAM
                          |
                          v
                       BUILDER
                          |
                          v
                       TESTER
                          |
                          v
                     BENCHMARKER
                          |
                          v
                       CURATOR
```

Role descriptions:

**Observer** — facts only.

**Scientist** — diagnose why performance is poor and propose hypotheses.

**Proposer** — generate diverse mutations.

**Builder** — materialize candidates.

**Tester** — deterministic verification.

**BenchmarkER** — run replay and capability benchmarks.

**Red Team** — actively search for regressions and unsafe behavior.

**Curator** — compare evidence and produce a promotion recommendation.

**RSI Supervisor** — enforces the lifecycle and never bypasses governance.

---

## 29. Avoid a single-model echo chamber

Do not let the same model:

```text
propose -> evaluate -> approve
```

with no independent signal.

Better:

```text
Generator model
      |
      v
Candidate
      |
  +---+----+
  |        |
  v        v
Deterministic evaluator   Critic model
  |        |
  +---+----+
      |
      v
Independent promotion gate
```

For high-risk changes, use model diversity where practical.

---

## 30. Self-evolving evaluator: later phase

The evaluator itself can eventually become evolvable, but this is a major governance boundary.

Safe progression:

```text
Phase 1
fixed evaluator

Phase 2
new evaluation heuristics proposed, human approved

Phase 3
shadow evaluators run in parallel

Phase 4
evaluator ensembles compare themselves

Phase 5
bounded meta-evolution of evaluator components
```

Do not let an agent modify the only evaluator and then use that modified evaluator to certify itself.

Maintain at least one trusted external/frozen evaluation path.

---

## 31. Trusted evaluator principle

Keep a small immutable `evaluator_core` outside the evolvable workspace.

It owns:
- benchmark runner
- safety invariants
- protected file list
- promotion contract
- rollback mechanism
- audit writer

The agent may propose changes to the evaluator but cannot replace the trusted evaluator automatically.

---

## 32. Security boundaries

Especially for a coding/terminal agent, RSI must not grant itself new authority simply because the task appears useful.

Protected resources:

```text
credentials
SSH keys
browser sessions
cloud tokens
system administrator access
production databases
billing systems
private repositories
network firewall rules
security policies
RSI governance code
```

Use capability-based permissions so the runtime receives only the tools required for a candidate.

---

## 33. Secret handling

Never put secrets into candidate workspaces.

Use:

```text
candidate -> capability proxy -> approved service
```

rather than:

```text
candidate -> raw secret
```

All secret-bearing actions should be auditable.

---

## 34. Resource governor

RSI can otherwise spend all available compute recursively.

Implement budgets for:
- wall-clock time
- model calls
- tokens
- concurrent candidates
- disk space
- CPU
- memory
- network requests
- benchmark repetitions

Example:

```yaml
budget:
  cycle_minutes: 30
  max_candidates: 8
  max_candidate_minutes: 5
  max_model_calls: 200
  max_disk_mb: 2048
```

Every child process should inherit a bounded budget.

---

## 35. Stop conditions

An RSI cycle must stop when:

```text
no promising opportunities remain
OR
budget exhausted
OR
improvement plateau detected
OR
safety anomaly detected
OR
regression detected
OR
maximum evolution depth reached
```

Do not make "keep improving forever" the default runtime behavior.

---

## 36. Improvement plateau detection

Keep a rolling history.

```text
cycle 1: +5.2%
cycle 2: +2.1%
cycle 3: +0.6%
cycle 4: +0.1%
cycle 5: -0.1%
```

When gains fall below a threshold for several cycles, switch from exploitation to:
- broader exploration
- benchmark expansion
- root-cause re-analysis
- new mutation operators
- human review

---

## 37. Preventing benchmark gaming

The harness may learn to optimize the benchmark instead of capability.

Countermeasures:

- hidden test set
- rotating tasks
- held-out task families
- adversarial tests
- real-world task replay
- multiple evaluators
- user correction rate
- post-promotion monitoring

The live benchmark should not be fully visible to the mutation agent.

---

## 38. Regression sentinels

Maintain a fixed sentinel suite covering foundational capabilities.

Example:

```text
sentinel_01: basic terminal use
sentinel_02: read-only repo inspection
sentinel_03: safe code edit
sentinel_04: test verification
sentinel_05: web source validation
sentinel_06: user requirement adherence
sentinel_07: secret handling
sentinel_08: recovery from tool failure
```

Any candidate that breaks a sentinel cannot be promoted automatically.

---

## 39. Memory architecture for RSI

Use three levels.

### Episodic memory
Raw task experiences.

### Semantic memory
Generalized reusable lessons.

### Procedural memory
Skills, workflows, and executable routines.

The promotion path is:

```text
experience
   -> repeated pattern
   -> generalized lesson
   -> candidate skill/policy
   -> benchmark validation
   -> established procedure
```

Do not promote a single accidental observation directly into a permanent policy.

---

## 40. Contradiction handling

Self-improvement creates competing lessons.

Example:

```text
Lesson A: parallelize agents for speed
Lesson B: sequential reasoning avoids dependency errors
```

Store both with conditions.

Prefer conditional rules:

```text
WHEN tasks are independent -> parallelize
WHEN tasks have dependencies -> sequence
```

The contradiction resolver should search for the context dimension that explains the disagreement.

---

## 41. Skill quality model

Every generated skill should contain:

```yaml
---
name: verify_before_after_edit
description: Use for code changes where regressions are possible
version: 1
source: rsi
confidence: provisional
status: active
---

# Trigger

# Preconditions

# Procedure

# Verification

# Failure recovery

# Examples
```

Skills should move through:

```text
PROPOSED -> EXPERIMENTAL -> ACTIVE -> ESTABLISHED -> DEPRECATED
```

---

## 42. Prompt evolution strategy

Do not mutate one giant system prompt.

Break it into fragments:

```text
prompt stack
├── identity
├── goal handling
├── safety
├── planning
├── tools
├── coding
├── verification
├── web research
├── memory
└── recovery
```

Each fragment gets independent experiments.

This makes attribution easier:

```text
success gain -> verification fragment v12
```

rather than:

```text
success gain -> enormous prompt v22
```

---

## 43. Routing evolution

Represent routing as data.

```yaml
workflow: coding_task
steps:
  - planner
  - repository_explorer
  - implementer
  - tester
  - reviewer
policy:
  parallel_exploration: true
  review_threshold: medium
```

RSI may test alternatives such as:

```text
planner -> explorer -> implementer -> tester
planner -> explorers[3] -> synthesizer -> implementer -> tester
planner -> implementer -> tester -> reviewer
```

Evaluate the whole workflow, not individual agents only.

---

## 44. Model routing evolution

Your harness can evolve model allocation without changing the core agent.

Example:

```yaml
planner: strong_model
coding: local_or_mid_model
test_generation: cheap_model
critic: independent_strong_model
summarizer: cheap_model
```

RSI can experiment with model assignment under a cost/latency budget.

For local Windows operation, include local-model candidates where practical and fall back to remote models only when allowed.

---

## 45. Self-generated tests

A strong RSI system should not only fix code; it should create tests that would have caught the failure.

Pipeline:

```text
failure
  -> diagnosis
  -> regression test
  -> candidate fix
  -> run old + new tests
```

Store the newly created regression test permanently.

This creates a growing protection layer.

---

## 46. Self-generated tools

When a recurring gap appears:

```text
agent repeatedly fails to inspect X
          |
          v
RSI identifies missing capability
          |
          v
propose tool
          |
          v
implement in sandbox
          |
          v
security/static tests
          |
          v
benchmark
          |
          v
register tool if useful
```

Tool registration should require a schema, permission scope, timeout, error policy, and tests.

---

## 47. Meta-learning from retries

Retry should not mean "do the same thing again".

Use:

```text
attempt 1
  |
  +--> failure analysis
  |
  +--> strategy mutation
  |
  v
attempt 2
```

This is the online RSI kernel.

At minimum, retries should change one of:
- strategy
- tool
- prompt fragment
- context
- verification method

---

## 48. Self-debugging loop

For a failed code task:

```text
FAIL
 |
 v
collect traceback + diff + tests
 |
 v
failure classifier
 |
 v
root-cause candidate set
 |
 +--> memory search
 +--> skill search
 +--> similar-failure search
 |
 v
repair strategies x N
 |
 v
sandbox execution
 |
 v
verification
 |
 v
select repair
```

Only after the local task succeeds should the reusable lesson be considered for permanent RSI promotion.

---

## 49. RSI journal

Create a human-readable journal:

```text
rsi/journal/2026-09-16.md
```

Each entry:

```markdown
## Cycle 17

Observed:
Repeated test failures after database schema changes.

Diagnosis:
Agent edits application code before inspecting migration state.

Hypothesis:
Add migration-state inspection to coding workflow.

Candidates:
- skill-only
- planner rule
- mandatory preflight tool

Evaluation:
skill-only + planner rule reduced replay failure rate.

Decision:
PROMOTED candidate-017b.

Rollback:
Available as v0.3.4.
```

This is extremely useful for debugging the RSI engine itself.

---

## 50. Observability dashboard

Track:

### Agent quality
- task success
- first-pass success
- verification success
- user correction rate

### RSI productivity
- proposals generated
- candidates evaluated
- promotion rate
- average improvement
- cycles to improvement
- rejected candidate reasons

### Stability
- regressions
- rollbacks
- crashes
- safety failures

### Efficiency
- tokens per successful task
- seconds per task
- compute per accepted improvement

---

## 51. RSI-specific metrics

Useful derived metrics:

```text
proposal_yield
= accepted_candidates / proposals

improvement_efficiency
= capability_gain / evaluation_cost

regression_rate
= promoted_candidates_that_trigger_rollback / promoted_candidates

learning_velocity
= capability_gain / cycle

mutation_productivity
= useful_mutations / attempted_mutations
```

Use these to improve the RSI engine itself.

---

## 52. Two-loop architecture

Your harness should have both loops.

### Inner loop — task adaptation

```text
observe -> reflect -> retry -> verify
```

Fast and local.

### Outer loop — harness evolution

```text
collect -> diagnose -> mutate -> benchmark -> promote
```

Slow and evidence-driven.

This separation is one of the most important design choices.

---

## 53. Three-loop architecture for advanced mode

Later add:

```text
Loop 1: task execution
Loop 2: harness evolution
Loop 3: evolution-of-evolution
```

Loop 3 asks:

- Which mutation operators work?
- Which evaluators predict real success?
- How many candidates should be generated?
- Which tasks are most informative?
- When should exploration increase?

Keep Loop 3 highly constrained until the first two loops are stable.

---

## 54. Research / experiment mode

Run experiments as named jobs:

```text
experiment/exp-2026-09-16-verify-policy/
├── hypothesis.md
├── baseline.json
├── candidate-001/
├── candidate-002/
├── candidate-003/
├── results.json
└── decision.md
```

Every experiment must be reproducible from:
- incumbent version
- mutation seed
- model configuration
- benchmark version
- evaluator version

---

## 55. Deterministic reproducibility

Where possible, freeze:
- benchmark version
- prompt snapshot
- skill snapshot
- model config
- environment
- tool versions
- random seeds

For non-deterministic LLMs, run repeated trials and report distributions rather than treating one lucky run as proof.

---

## 56. Candidate comparison modes

Use:

### A/B
Incumbent vs candidate.

### Pairwise tournament
Candidate A vs B vs C.

### Population benchmark
Evaluate a pool and keep diverse winners.

### Ablation
Candidate with and without each mutation component.

Ablation is especially important when combining multiple self-improvements.

---

## 57. Example RSI cycle

```text
User task
  |
  v
Hermes completes task but 3 tests fail
  |
  v
Observer stores trajectory
  |
  v
Failure cluster detects repeated missing verification
  |
  v
Scientist proposes:
  A = verifier skill
  B = planner gate
  C = reviewer subagent
  |
  v
3 isolated candidates
  |
  v
Replay 50 coding tasks
  |
  v
A: +3%
B: +5%
C: +4%
A+B: +8%
  |
  v
A+B passes safety + regression
  |
  v
Promote A+B
  |
  v
Monitor next 100 tasks
  |
  v
No regression
  |
  v
mark ESTABLISHED
```

That is recursive improvement at the harness level without requiring model-weight training.

---

## 58. What the harness should NOT do initially

Do not start with:

```text
agent can rewrite any source file
agent can change its permissions
agent can modify evaluator and certify itself
agent can access all secrets
agent can create unlimited child agents
agent can run unlimited experiments
agent can change network policy
agent can delete failed history
```

Those remove the very evidence and constraints needed to tell whether the system is actually improving.

---

## 59. Recommended implementation order

### Phase 1 — Foundation
Implement:
- event/trajectory logging
- immutable raw observations
- failure taxonomy
- structured memory
- benchmark/replay runner
- Git snapshots

### Phase 2 — Safe self-learning
Implement:
- reflection
- lesson extraction
- memory consolidation
- skill proposal generation
- skill evaluation
- automatic low-risk skill promotion

### Phase 3 — Prompt evolution
Implement:
- modular prompt fragments
- mutation operators
- candidate sandbox
- A/B replay
- promotion gates

### Phase 4 — Workflow evolution
Implement:
- routing as data
- planner strategy variants
- subagent topology variants
- cost/latency evaluation

### Phase 5 — Tool evolution
Implement:
- generated helper tools
- schema checks
- permission scopes
- tool benchmark suite

### Phase 6 — Source-code evolution
Implement:
- disposable code candidates
- full unit/integration tests
- static analysis
- protected-core policy
- mandatory review initially

### Phase 7 — Open-ended evolution research
Implement:
- candidate archive
- lineage tree
- diversity preservation
- recombination
- automatic experiment scheduling

### Phase 8 — Meta-evolution
Only after the previous phases are stable:
- evolve mutation selection
- evolve candidate budgets
- evolve exploration/exploitation strategy
- evolve benchmark sampling

---

## 60. Minimal first version for your Hermes harness

Do not build the full system first.

Build this exact MVP:

```text
Hermes
  |
  +--> Event logger
  |
  +--> Reflection worker
  |
  +--> Memory store
  |
  +--> Skill proposer
  |
  +--> Candidate workspace
  |
  +--> Replay benchmark (50 tasks)
  |
  +--> Diff/evaluator
  |
  +--> Promotion gate
  |
  +--> Git snapshot + rollback
```

Initial mutation types:

```text
1. memory lesson
2. skill addition
3. skill revision
4. prompt fragment
5. verification rule
```

Keep source-code mutation disabled while this stabilizes.

---

## 61. Suggested module layout

```text
plugins/rsi/
├── plugin.py
├── controller.py
├── observer.py
├── taxonomy.py
├── diagnosis.py
├── opportunities.py
├── proposer.py
├── mutations/
│   ├── base.py
│   ├── memory.py
│   ├── skill.py
│   ├── prompt.py
│   ├── routing.py
│   ├── tool.py
│   └── code.py
├── candidate.py
├── sandbox.py
├── evaluator.py
├── benchmarks.py
├── regression.py
├── safety.py
├── promotion.py
├── archive.py
├── lineage.py
├── rollback.py
├── budget.py
├── monitor.py
└── schemas/
    ├── observation.json
    ├── proposal.json
    ├── candidate.json
    └── evaluation.json
```

---

## 62. Interfaces

Keep interfaces simple and stable.

```python
class Observer:
    def collect(self, event) -> None: ...

class Diagnoser:
    def diagnose(self, observations): ...

class Proposer:
    def propose(self, opportunity): ...

class Mutation:
    def apply(self, workspace, proposal): ...

class Evaluator:
    def evaluate(self, candidate, benchmark): ...

class PromotionGate:
    def decide(self, incumbent, candidate, policy): ...

class Archive:
    def save(self, candidate, report, decision): ...

class RollbackManager:
    def rollback(self, version): ...
```

This lets every RSI component remain a removable plugin.

---

## 63. Recommended data stores

For a low-cost local-first system:

```text
JSONL       raw events / observations
SQLite      indexed metadata / benchmark results
Git         workspace/version lineage
filesystem  candidate workspaces and artifacts
optional vector DB  semantic memory retrieval
```

Do not make a vector database the source of truth for experiment history. Keep immutable structured records.

---

## 64. Integration with your existing Hermes architecture

### Hermes remains the top supervisor
It owns:
- user goal
- global task state
- delegation
- high-level policy

### RSI is a subordinate control system
It may propose changes to:
- skills
- prompts
- workflows
- tools
- memory policies

### Agent execution stays replaceable
Your coding worker, research worker, browser worker, terminal worker, and other specialists can all participate in the same RSI benchmark ecosystem.

### Every plugin becomes potentially evolvable
But only the explicit `evolvable_layers` from the manifest may be mutated.

---

## 65. Long-term architecture

```text
                    HERMES SUPERVISOR
                           |
          +----------------+----------------+
          |                |                |
       EXECUTION        MEMORY            RSI
          |                |                |
     multi-agent       learning       evolution lab
          |                |                |
          +----------------+----------------+
                           |
                           v
                    EVALUATION FABRIC
                           |
          +----------------+----------------+
          |                |                |
      BENCHMARKS       REPLAY           RED TEAM
          |                |                |
          +----------------+----------------+
                           |
                           v
                     CANDIDATE ARCHIVE
                           |
                    lineage / diversity
                           |
                           v
                  PROMOTION GOVERNANCE
                           |
                           v
                    LIVE HARNESSES
                           |
                           +----> telemetry
                                    |
                                    +----> RSI
```

The end state is not simply an agent that rewrites code. It is an **empirical learning-and-evolution platform for the harness itself**.

---

## 66. Definition of done for RSI v1

The first release should satisfy all of these:

- Every task generates structured observations.
- Repeated failures are clustered.
- The system can extract lessons.
- The system can create a candidate skill or prompt change.
- Candidates are isolated from the live system.
- A fixed replay suite evaluates them.
- Candidates are compared with the incumbent.
- Safety and regression gates run automatically.
- Promotion is versioned.
- Rollback is one command/action.
- Failed candidates remain archived.
- A human can inspect why a change was promoted.
- The system can stop itself on budget or safety thresholds.

---

## 67. Definition of advanced RSI

Advanced RSI should eventually be able to:

```text
observe its own failures
      -> discover capability gaps
      -> generate multiple hypotheses
      -> build diverse candidates
      -> test candidates in parallel
      -> learn which mutation operators work
      -> preserve useful lineages
      -> discover new skills/tools/workflows
      -> improve verification
      -> improve its search strategy
      -> continuously measure real-world performance
      -> safely promote only evidence-backed changes
```

That is the architecture to target rather than unrestricted self-editing.

---

## 68. Final design rule

The critical invariant is:

> **No self-improvement without an independently measured before/after comparison.**

Everything else—reflection, memory, mutation, skills, code generation, evolutionary search, multi-agent science roles, and recursive experimentation—should feed this invariant.

That gives the harness a path from ordinary agent memory/reflection to controlled recursive self-improvement while retaining reproducibility, rollback, and human governance.
