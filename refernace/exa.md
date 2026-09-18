Yes. What you are describing is a step beyond a normal coding agent:

> **The harness itself continuously observes its behavior, discovers where it is weak or wasting effort, proposes changes to its own code/config/prompts/skills/tools/workflows, creates isolated candidate versions, evaluates them against real workloads and regression suites, and promotes only changes that produce measured improvement.**

That architecture is now supported by several important research/codebases, but the pieces are fragmented. The best approach for your Agent Workspace-based harness is to combine them into a dedicated **Recursive Self-Development Engine (RSDE)** rather than letting the main agent arbitrarily rewrite itself.

The most directly relevant work I found is **Darwin Gödel Machine**, which iteratively modifies its own agent code and evaluates the resulting agents; **Live-SWE-agent**, which evolves its scaffold during runtime while solving software tasks; **AlphaEvolve**, which combines LLM-generated program mutations with automated evaluators and evolutionary selection; and **Exo**, which explicitly treats the entire agent harness—including prompts, memory, tools and policies—as an evolutionary object. ([GitHub][1])

## 1. The architecture I recommend for your harness

Call the subsystem something like:

**Self-Development Engine**
or
**Recursive Evolution Engine (REE)**

The overall architecture should be:

```text
                         ┌────────────────────────────┐
                         │       USER / GOALS         │
                         └─────────────┬──────────────┘
                                       │
                                       ▼
                         ┌────────────────────────────┐
                         │      SUPERVISOR AGENT      │
                         │ planning / orchestration   │
                         └─────────────┬──────────────┘
                                       │
                 ┌─────────────────────┼─────────────────────┐
                 │                     │                     │
                 ▼                     ▼                     ▼
          Task Execution        Memory / Skills       Tool / Agent System
                 │                     │                     │
                 └─────────────────────┼─────────────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │     OBSERVABILITY LAYER    │
                         │ traces / failures / costs  │
                         │ latency / tool use / tests  │
                         │ user corrections / outcomes│
                         └─────────────┬──────────────┘
                                       │
                                       ▼
                         ┌────────────────────────────┐
                         │   IMPROVEMENT DETECTOR     │
                         │ "What is limiting us?"     │
                         └─────────────┬──────────────┘
                                       │
                        ┌──────────────┼──────────────┐
                        ▼              ▼              ▼
                 Bug / Failure     Bottleneck     Missing
                 Detection         Detection       Capability
                        │              │              │
                        └──────────────┼──────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │  IMPROVEMENT OPPORTUNITY   │
                         │       GENERATOR             │
                         └─────────────┬──────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │    CHANGE STRATEGIST       │
                         │ code / prompt / skill /    │
                         │ memory / tools / workflow  │
                         └─────────────┬──────────────┘
                                       ▼
                    ┌─────────────────────────────────────────┐
                    │         EVOLUTION WORKSPACE             │
                    │                                         │
                    │ candidate A   candidate B   candidate C│
                    │      │             │             │      │
                    │      ▼             ▼             ▼      │
                    │   patch        patch         patch      │
                    └──────────────────┬──────────────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │      VERIFICATION FARM     │
                         │ unit / integration / e2e   │
                         │ replay / benchmarks       │
                         │ security / performance    │
                         │ adversarial tests         │
                         └─────────────┬──────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │     EVOLUTION JUDGE        │
                         │ baseline vs candidate      │
                         │ multi-objective scoring    │
                         └─────────────┬──────────────┘
                                       ▼
                              ┌─────────────────┐
                              │ PROMOTE / REJECT│
                              └───────┬─────────┘
                                      │
                       ┌──────────────┼──────────────┐
                       ▼              ▼              ▼
                    Reject         Shadow         Canary
                       │              │              │
                       │              │              ▼
                       │              │          Production
                       │              │              │
                       │              └──────────────┤
                       │                             ▼
                       │                      Monitor outcome
                       │                             │
                       └─────────────────────────────┘
```

The critical idea is that **the production agent never directly edits and reloads itself**.

Instead:

```text
Production Harness
       │
       ▼
Observation
       │
       ▼
Improvement Proposal
       │
       ▼
Clone / Candidate
       │
       ▼
Test
       │
       ▼
Compare
       │
       ▼
Promote
       │
       ▼
New Version
```

That separation is one of the most important design decisions.

---

# 2. Your harness should improve more than its source code

A common mistake is defining RSI as:

> "Agent edits Python/TypeScript files."

That is too narrow.

Your harness should have an **evolution surface**.

### Evolution Layer A — Code

The agent can improve:

```text
orchestrator
planner
scheduler
retry logic
tool execution
memory manager
subagent manager
MCP integration
sandbox manager
context manager
model router
observability
UI
installation
recovery system
```

### Evolution Layer B — Agent configuration

It should be able to evolve:

```text
SOUL.md
AGENTS.md
system prompts
planning prompts
review prompts
model selection
model routing
temperature
context budgets
retry policies
timeout policies
tool policies
subagent policies
```

### Evolution Layer C — Skills

It can:

```text
discover missing skill
create SKILL.md
improve SKILL.md
combine skills
split large skill
add executable helper
add verification procedure
deprecate bad skill
```

Agent Workspace already has a strong foundation for this: skills are modular capability packages loaded progressively, and its 2.0 architecture includes memory, skills, tools, sandboxes and subagents. ([GitHub][2])

### Evolution Layer D — Tools

The agent can recognize:

> "I keep manually doing this operation."

Then create:

```text
new tool
MCP server
CLI wrapper
API integration
automation script
browser workflow
database helper
```

### Evolution Layer E — Workflow

This is particularly important.

The system should learn:

```text
current workflow:
research → plan → execute → review

observed:
research is repeated 4 times

candidate:
research → cache evidence → plan → execute → review
```

Or:

```text
planner
   ↓
coder
   ↓
tester
   ↓
reviewer
   ↓
coder
```

becomes:

```text
planner
   ↓
parallel investigators
   ↓
candidate implementation
   ↓
automated verifier
   ↓
review only if necessary
```

### Evolution Layer F — Memory

The system should identify:

```text
repeated mistakes
successful strategies
failed strategies
tool reliability
model reliability
project-specific conventions
user corrections
environment-specific fixes
```

and convert those observations into durable memory.

This relates directly to the Reflexion idea of using feedback and reflective memory instead of changing model weights. ([arXiv][3])

---

# 3. The most important component: Improvement Detector

This is the part that makes your idea fundamentally different.

Your harness should continually ask:

> **"Where did the system fail to behave as efficiently or effectively as it could?"**

Create an **Improvement Opportunity Detector (IOD)**.

It consumes:

```text
execution traces
agent messages
tool calls
errors
test results
CI results
user corrections
time
token usage
cost
retry counts
subagent behavior
memory retrieval
model selection
task success
task quality
security alerts
dependency alerts
performance metrics
```

OpenAI's current Agents SDK, for example, explicitly records traces for LLM generations, tool calls, handoffs, guardrails and other workflow events, which is exactly the type of telemetry your system needs. ([OpenAI GitHub][4])

---

# 4. What problems should it automatically discover?

Build detectors for at least these categories.

| Detector               | Example                                                |
| ---------------------- | ------------------------------------------------------ |
| Repeated failure       | Same tool fails 5 times                                |
| Repeated retry         | Agent repeatedly retries identical strategy            |
| Loop                   | Agent keeps reading/editing the same files             |
| Context waste          | Huge amount of irrelevant context                      |
| Tool misuse            | Wrong tool repeatedly selected                         |
| Missing tool           | Repeated shell workaround indicates missing capability |
| Missing skill          | Same workflow repeated without reusable procedure      |
| Bad prompt             | Agents consistently misunderstand a step               |
| Bad memory             | Relevant memory exists but isn't retrieved             |
| Memory pollution       | Huge irrelevant memories retrieved                     |
| Model mismatch         | Cheap model fails tasks requiring reasoning            |
| Model overuse          | Expensive model used for trivial work                  |
| Planner failure        | Plans repeatedly miss dependencies                     |
| Reviewer failure       | Bugs discovered after reviewer                         |
| Test gap               | Bugs escape current test suite                         |
| Performance regression | Same task becomes slower                               |
| Cost regression        | Token/API cost rises                                   |
| Reliability regression | More failures after new version                        |
| Dependency problem     | Vulnerability/outdated package                         |
| Automation opportunity | Human repeatedly performs same action                  |
| Missing recovery       | Same environment failure requires manual intervention  |
| Configuration drift    | Different agents diverge unnecessarily                 |
| Skill duplication      | Multiple skills solve nearly same problem              |

The detector should produce a structured object:

```json
{
  "opportunity_id": "opp_01428",
  "type": "missing_automation",
  "severity": "medium",
  "confidence": 0.94,
  "evidence": [
    "workflow repeated 37 times",
    "average manual steps: 6",
    "average time: 94 sec"
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

# 5. The harness should discover automation opportunities itself

This is one of the strongest additions I'd make to your project.

Create:

## `Automation Miner`

It mines its own execution history.

Example:

```text
Observed traces:

Task 1:
read package.json
search dependency
run npm outdated
edit package.json
run tests

Task 2:
read package.json
search dependency
run npm outdated
edit package.json
run tests

Task 3:
read package.json
search dependency
run npm outdated
edit package.json
run tests
```

The system identifies:

```text
Pattern frequency: 3+
Automation candidate:
"dependency-update verification"
```

Then it generates:

```text
SKILL.md
+
tool
+
tests
```

and evaluates it.

This means your agent can move from:

**doing tasks**

to:

**learning which tasks should become automation.**

---

# 6. Separate "repair" from "improvement"

You need two different loops.

## Loop 1 — Incident Repair

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

Example:

```text
MCP connection fails
→ diagnose timeout bug
→ patch reconnect logic
→ integration test
→ canary
```

## Loop 2 — Capability Improvement

```text
observe
 ↓
discover inefficiency
 ↓
generate improvement hypothesis
 ↓
create candidates
 ↓
benchmark
 ↓
promote
```

Example:

```text
100 tasks analyzed

planner causes 23% unnecessary iterations

→ test new planning policy
→ benchmark
→ select candidate
→ promote
```

This distinction prevents your RSI engine from confusing:

> "fix something broken"

with

> "make something already working better."

---

# 7. Add an actual Evolution Engine

This is where AlphaEvolve and Darwin Gödel Machine become especially relevant.

AlphaEvolve uses LLMs to generate candidate programs, automatically evaluates them, and maintains an evolutionary population/database from which later prompts select promising candidates. ([DeepMind][5])

DGM similarly iteratively modifies the agent's own code and empirically validates each change with coding benchmarks. ([GitHub][1])

Your engine should therefore maintain:

```text
Version 0
   │
   ├── Candidate A
   ├── Candidate B
   └── Candidate C

Candidate A
   ├── A1
   └── A2

Candidate B
   ├── B1
   └── B2
```

This is a **version lineage**, not a single mutable copy.

---

# 8. Never use a single improvement candidate

Suppose the agent concludes:

> "Planner needs improvement."

Do not ask one model:

> "Improve planner."

Instead generate several competing hypotheses.

### Candidate 1

```text
Improve planner prompt
```

### Candidate 2

```text
Add plan validation
```

### Candidate 3

```text
Introduce planner/reviewer loop
```

### Candidate 4

```text
Add dependency-aware planning
```

### Candidate 5

```text
Use a different model for planning
```

### Candidate 6

```text
Use historical successful plans as retrieval context
```

Then benchmark all of them.

That gives you evolutionary search instead of single-shot self-modification.

---

# 9. Use multiple optimizers

Your self-development engine shouldn't depend on one model.

Use:

```text
Optimizer A → primary coding model
Optimizer B → independent reasoning model
Optimizer C → cheap exploration model
Optimizer D → specialized code model
```

They can propose different mutations.

For example:

```text
                    Improvement Goal
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      Model A           Model B           Model C
       proposal          proposal          proposal
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                    Candidate Pool
```

This is especially compatible with the evolutionary structure demonstrated by AlphaEvolve's use of multiple model capabilities. ([DeepMind][5])

---

# 10. Add a "Verifier First" architecture

This is arguably more important than the optimizer.

Your system should never say:

> "The new version looks better."

It must say:

> "The new version passed objective tests that the old version also passed, plus it improved the target metric."

Create:

```text
Verifier Registry
```

with:

```text
unit tests
integration tests
end-to-end tests
task replay
benchmark tests
static analysis
security checks
performance benchmarks
memory checks
cost checks
behavioral checks
agent trajectory checks
```

The latest research is moving in this direction: SBCO proposes verifier-grounded harness optimization, where agent behavior is improved from graded feedback rather than relying solely on unrestricted self-modification. ([arXiv][6])

---

# 11. Your evaluation system should have three levels

## Level 1 — Fast verification

Runs every candidate.

```text
lint
typecheck
unit tests
basic integration
security scan
```

## Level 2 — Replay evaluation

Take historical tasks:

```text
100 previous successful tasks
50 previous failures
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
iterations
tokens
latency
tool errors
human intervention
```

## Level 3 — Held-out evaluation

This is critical.

Don't evaluate only on the tasks that inspired the change.

Keep a private/held-out set:

```text
training/improvement tasks
        ↓
candidate optimization

held-out tasks
        ↓
real evaluation
```

Otherwise your system can overfit its own benchmark.

SWE-bench-Live is particularly interesting as a model for continuously updated software-engineering evaluation; its dataset is automatically curated and supports multi-language and Windows tasks. ([GitHub][7])

---

# 12. Your benchmark should continuously grow

Don't make the evaluation suite static.

Your harness should convert real failures into new tests.

```text
production failure
       ↓
failure extractor
       ↓
reproduction
       ↓
regression test
       ↓
benchmark database
```

After a few months:

```text
Benchmark v1
  100 cases

Benchmark v2
  174 cases

Benchmark v3
  311 cases

Benchmark v4
  580 cases
```

This creates a **self-growing evaluation substrate**.

---

# 13. Add trajectory evaluation

Normally coding agents evaluate only:

> Did the task succeed?

Your harness should additionally evaluate:

```text
How did it solve it?
```

Track:

```text
tool call count
wrong tool calls
repeated commands
backtracking
context consumption
subagent usage
time
cost
number of failed tests
number of retries
number of unnecessary file reads
```

Example:

```text
Version A
success = 92%
tokens = 1.4M
tool calls = 32

Version B
success = 91%
tokens = 0.7M
tool calls = 17
```

Whether B is preferable depends on your objective function, but your harness now has enough information to make that comparison.

---

# 14. Create a Multi-Objective Fitness Function

Don't optimize only success.

Use something like:

```text
fitness =
    0.40 × task_success
  + 0.15 × correctness
  + 0.10 × reliability
  + 0.10 × automation_gain
  + 0.08 × latency
  + 0.07 × cost_efficiency
  + 0.05 × security
  + 0.05 × maintainability
```

But I would actually implement this as a **vector**, not a single scalar:

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

Then use Pareto-style selection.

This prevents:

> "Agent became 5% faster by becoming 20% less reliable."

from being mistakenly accepted.

---

# 15. Add an Improvement Hypothesis layer

Every proposed self-change should have a hypothesis.

For example:

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

That is much safer than:

```text
AI decided to rewrite planner.py
```

---

# 16. Create an immutable "Constitution"

Your system needs a part that self-improvement cannot modify.

Think:

```text
                    ┌────────────────────┐
                    │ IMMUTABLE CORE      │
                    │                    │
                    │ security policy    │
                    │ evaluator rules    │
                    │ rollback rules     │
                    │ audit ledger       │
                    │ promotion policy   │
                    │ resource limits    │
                    └─────────┬──────────┘
                              │
                              ▼
                    Self-Development Engine
                              │
                    may modify everything
                    EXCEPT immutable core
```

The immutable area should include:

```text
promotion gates
secret access policy
sandbox policy
audit ledger
rollback mechanism
owner permissions
resource ceilings
network policy
identity/authentication
```

This is conceptually similar to current agentic workflow systems that combine agent freedom with deterministic permissions, safe outputs, sandboxing and security gates. GitHub's Agentic Workflows now use read-only defaults, safe outputs, sandboxing and dedicated threat detection around agent changes. ([The GitHub Blog][8])

---

# 17. Never let the evolving agent own the promotion authority

This is a crucial architectural rule.

Bad:

```text
Agent
 ↓
modify code
 ↓
run tests
 ↓
declare success
 ↓
deploy itself
```

Better:

```text
Evolution Agent
      ↓
candidate
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

The evaluator should be **outside the direct optimization context**.

That's the difference between:

**self-claimed improvement**

and

**externally verified improvement**.

---

# 18. Build a shadow environment

Every self-modification happens first in:

```text
/sandbox/evolution/<experiment-id>/
```

or a container/VM.

Example:

```text
production/
    harness-v41

evolution/
    exp-001/
        candidate-a/
        candidate-b/
        candidate-c/

    exp-002/
        candidate-a/
        candidate-b/
```

Each candidate gets:

```text
isolated filesystem
isolated process
limited network
limited credentials
CPU/memory quota
execution timeout
```

OpenAI's current agent tooling exposes sandboxed workspaces and tool/guardrail mechanisms, while Agent Workspace explicitly treats sandboxed execution as a core part of its long-horizon architecture. ([OpenAI GitHub][9])

---

# 19. Give every candidate a version identity

For example:

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

Store:

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

This becomes your **Evolution DAG**.

---

# 20. Add an Evolution Memory

Your normal agent memory is not enough.

Create a separate:

## `Evolution Memory`

It should remember:

```text
successful mutations
failed mutations
why mutations failed
models that generated good changes
models that generated bad changes
which components are fragile
which tests are weak
which modifications have already been tried
which hypotheses are unresolved
```

Example:

```text
planner:
  mutation "self-review":
    result = +2.3% success
    cost = +14%
    rejected because cost threshold

  mutation "plan-compression":
    result = -4.2% success
    rejected

  mutation "dependency-aware planning":
    result = +6.1%
    accepted
```

Now future evolution doesn't keep rediscovering the same dead ends.

---

# 21. Add an experiment database

I strongly recommend:

```text
Evolution DB
```

Tables:

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

Relationship:

```text
Experiment
    │
    ├── Hypothesis
    ├── Candidates
    ├── Evaluations
    ├── Metrics
    └── Decision
```

SQLite is enough for your initial local Windows version.

Later:

```text
SQLite
→ PostgreSQL
```

---

# 22. Use an "Improvement Planner"

This is different from your normal task planner.

Normal planner:

```text
How do I solve the user's problem?
```

Improvement planner:

```text
How do I improve the system that solves problems?
```

It should generate objectives such as:

```text
Improve planning reliability
Improve tool selection
Reduce token cost
Reduce retries
Increase autonomous completion
Improve memory retrieval
Increase recovery success
Improve coding benchmark score
Improve automation coverage
Reduce human intervention
Improve installation reliability
Improve Windows stability
```

---

# 23. Add a Capability Gap Detector

This is exactly suited to your idea.

When the agent repeatedly encounters:

```text
"I don't know how to do X."
```

it shouldn't simply fail.

It should ask:

```text
Is X:
  an existing skill?
  an existing tool?
  an MCP capability?
  a missing workflow?
  a prompt deficiency?
  a model limitation?
  a code limitation?
```

Then classify the gap.

Example:

```text
Task requires:
database migration verification

Current system:
can query DB
cannot compare migrations

Conclusion:
missing capability
```

Then:

```text
research
 ↓
design skill
 ↓
implement tool
 ↓
write tests
 ↓
register skill
 ↓
benchmark
 ↓
promote
```

---

# 24. Make skills self-generating

Your harness should eventually be able to do:

```text
task
 ↓
manual procedure discovered
 ↓
generalize procedure
 ↓
generate SKILL.md
 ↓
generate helper files
 ↓
generate tests
 ↓
evaluate
 ↓
install skill
```

This is especially compatible with the modular skill systems in Agent Workspace and DeepAgents, where skills are explicit capability packages and are dynamically discovered/loaded rather than hardcoding everything into the base prompt. ([GitHub][2])

---

# 25. Skill lifecycle

Give every skill a lifecycle:

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

And collect:

```text
usage_count
success_rate
failure_rate
avg_latency
token_cost
user_rating
dependencies
last_used
last_verified
```

Then the system can automatically detect:

> "This skill is never used and provides no measurable benefit."

and deprecate it.

---

# 26. Tool lifecycle

Do the same thing for tools.

```text
requested
   ↓
generated
   ↓
security validation
   ↓
sandbox test
   ↓
production
   ↓
usage analysis
   ↓
optimization
```

A tool should be removable if:

```text
usage ≈ 0
OR
error rate high
OR
duplicate capability
OR
security problem
```

---

# 27. Dependency self-maintenance

This is one part you don't need to invent.

Dependabot already automates security and version dependency updates, and GitHub documents automating their validation and merge workflows. ([GitHub Docs][10])

Your harness should integrate that concept into its own maintenance engine:

```text
dependency scan
      ↓
vulnerability?
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

# 28. Code transformation engine

For deterministic code changes, don't always use the LLM.

Use specialized transformation systems where possible.

OpenRewrite, for example, has structured recipes for automated refactoring and supports JavaScript, YAML, Docker, CI workflows and many other formats. ([OpenRewrite Docs][11])

So your self-development engine should choose:

```text
Simple deterministic modification
        ↓
AST / rewrite tool

Complex architectural change
        ↓
Coding agent

Unknown problem
        ↓
LLM investigation + coding agent
```

This reduces hallucinated edits.

---

# 29. Use the smallest capable automation

You can borrow an important idea from mini-SWE-agent.

The current mini-SWE-agent architecture intentionally stays extremely simple, with a minimal core and bash as its main interface, while still achieving strong SWE-bench performance. ([GitHub][12])

Your meta-system should therefore ask:

> "What's the simplest mechanism that fixes this problem?"

instead of automatically creating another complicated agent.

Example:

```text
problem:
repeatedly run 3 commands

solution:
shell script

NOT:
new subagent + MCP server + planner
```

This will keep your self-growing system from becoming enormous and fragile.

---

# 30. Self-development levels

I would implement your project in these stages.

## Level 0 — Manual evolution

Agent proposes:

```text
problem
solution
patch
tests
```

Human approves.

---

## Level 1 — Automatic repair

```text
failure
 ↓
diagnosis
 ↓
patch
 ↓
tests
 ↓
PR
```

---

## Level 2 — Automatic capability creation

```text
missing capability
 ↓
skill/tool generation
 ↓
evaluation
 ↓
install
```

---

## Level 3 — Automatic optimization

```text
telemetry
 ↓
performance bottleneck
 ↓
candidate generation
 ↓
A/B benchmark
 ↓
promotion
```

---

## Level 4 — Recursive self-development

Now the system can change:

```text
its own
  prompts
  skills
  tools
  workflows
  memory
  agent topology
  model routing
  retry policy
  planner
  evaluator adapters
  source code
```

---

## Level 5 — Evolutionary harness

Now use:

```text
candidate populations
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

This is the closest practical architecture to the DGM/AlphaEvolve direction. ([GitHub][1])

---

# 31. Level 6 — Runtime self-evolution

Live-SWE-agent is particularly relevant here because its reported approach evolves its agent scaffold while solving real software problems instead of relying only on offline optimization. ([arXiv][13])

For your harness:

```text
User task
   ↓
Agent detects obstacle
   ↓
Improvement opportunity
   ↓
Create temporary capability
   ↓
Test capability in current task
   ↓
If successful:
   ↓
store as candidate improvement
   ↓
later benchmark
   ↓
possibly promote globally
```

So the agent can evolve **during actual work**.

But promotion should remain separate from runtime experimentation.

---

# 32. Your ultimate loop

The complete loop should be:

```text
                  ┌─────────────────────────┐
                  │       REAL WORK         │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │       OBSERVE           │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │      DETECT PROBLEM     │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │     FIND ROOT CAUSE     │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │  CREATE HYPOTHESES      │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │ GENERATE MULTIPLE FIXES │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │     BUILD CANDIDATES    │
                  └────────────┬────────────┘
                               ▼
             ┌─────────────────┼────────────────┐
             ▼                 ▼                ▼
          Candidate A       Candidate B      Candidate C
             │                 │                │
             └─────────────────┼────────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │    VERIFY / BENCHMARK   │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │   COMPARE TO BASELINE   │
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │  INDEPENDENT EVALUATOR  │
                  └────────────┬────────────┘
                               ▼
                         PASS GATE?
                         /       \
                       NO         YES
                       │           │
                       ▼           ▼
                    Archive      Canary
                                    │
                                    ▼
                                Production
                                    │
                              monitor outcome
                               /          \
                           regression    improvement
                              │              │
                              ▼              ▼
                           rollback       promote
                                             │
                                             ▼
                                  Evolution Memory
                                             │
                                             ▼
                                    next generation
```

That is the core of what I would build.

---

# 33. The architecture inside your existing Agent Workspace-based harness

Since your project is already based on Agent Workspace 2.0, you don't need to throw away the existing foundation.

Agent Workspace already provides the pieces that are especially useful here: subagents, persistent memory, sandbox execution, skills, tools, context management and long-horizon orchestration. Its current architecture also explicitly separates the reusable harness layer from the application layer, which is useful for making your self-development system a clean subsystem rather than coupling it to UI/channel code. ([GitHub][2])

I would add:

```text
agent_workspace/
│
├── harness/
│
│   ├── agents/
│   ├── tools/
│   ├── memory/
│   ├── skills/
│   ├── sandbox/
│   └── orchestration/
│
├── evolution/
│   │
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

# 34. The most important agents in the system

Don't create 50 agents immediately.

Start with these:

```text
1. Observer
2. Improvement Detector
3. Root-Cause Analyst
4. Improvement Planner
5. Code/Skill/Tool Builder
6. Test Generator
7. Independent Reviewer
8. Benchmark Runner
9. Evolution Judge
10. Promotion Manager
11. Rollback Manager
```

Then add specialist agents later.

---

# 35. The "Independent Reviewer" is very important

The agent generating the change should not be the only agent evaluating it.

Example:

```text
Builder:
"my planner modification improves reasoning."

Reviewer:
"show evidence."

Evaluator:
"success +4.8%."

Adversarial evaluator:
"fails on 7 cases."

Security evaluator:
"introduces unrestricted shell capability."

Decision:
REJECT
```

This prevents self-confirmation.

---

# 36. Add an adversarial evolution agent

This agent's only goal is:

> **Break the candidate.**

It should search for:

```text
regressions
edge cases
security weaknesses
loops
resource exhaustion
prompt injection
memory contamination
tool abuse
incorrect assumptions
```

That gives your system:

```text
creator
vs
critic
vs
verifier
```

rather than:

```text
creator
vs
nothing
```

---

# 37. Add "automatic test generation"

Whenever the agent fixes a bug:

```text
bug
 ↓
fix
 ↓
generate regression test
 ↓
add to permanent benchmark
```

Example:

```text
Bug:
Telegram reconnect causes duplicated worker.

Patch:
worker lifecycle fixed.

Generated test:
simulate reconnect × 50
verify only one worker exists.
```

Now the system is becoming harder to break with every generation.

---

# 38. Make production behavior feed development

This creates your closed loop:

```text
                PRODUCTION
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
       logs                   outcomes
        │                       │
        └───────────┬───────────┘
                    ▼
             observation DB
                    │
                    ▼
              RSI engine
                    │
                    ▼
               new version
                    │
                    ▼
               evaluation
                    │
                    ▼
              production
```

The system therefore learns from its actual workload rather than only synthetic benchmarks.

---

# 39. Automatic problem → automatic fix example

Imagine your harness has this problem:

```text
Task: "Analyze large repository"

Observed:
planner repeatedly loads huge files
context overflow occurs
agent retries
task takes 31 minutes
```

The improvement engine detects:

```text
problem:
context inefficiency

evidence:
34% tasks exceed context threshold
```

Root cause:

```text
filesystem strategy loads full files
```

Candidate generation:

```text
A: better prompt
B: intelligent file slicing
C: semantic retrieval
D: AST-aware extraction
```

Evaluation:

```text
A → -7% tokens
B → -18%
C → -24%
D → -31%
```

Held-out benchmark:

```text
D:
success +2.8%
tokens -29%
latency -19%
```

Security:

```text
PASS
```

Canary:

```text
10% of workloads
```

Production:

```text
success remains improved
```

Promotion:

```text
version 42 → version 43
```

That is genuine self-development.

---

# 40. And it should discover automation, not only optimize

Imagine it observes:

```text
Every time GitHub CI fails:

agent:
  inspect logs
  identify failing test
  find file
  patch
  rerun test
  create commit
```

After 30 occurrences:

```text
Automation Miner
       ↓
pattern detected
       ↓
propose:
"CI Failure Repair Automation"
```

It generates:

```text
skill:
ci-failure-repair

tool:
ci_failure_context()

workflow:
detect → diagnose → patch → test → PR
```

Then the next failure automatically triggers the workflow.

This is the mechanism that gradually turns your harness into an **automation-discovery machine**.

---

# 41. Important external projects to study

These are the projects/papers I'd put at the top of your research list:

### Darwin Gödel Machine

Most directly relevant for **recursive self-modification of the coding agent itself**. It modifies its own code and evaluates the resulting systems. ([GitHub][1])

### Live-SWE-agent

Most relevant for **runtime self-evolution while doing real software-engineering tasks**. ([arXiv][13])

### AlphaEvolve

Most relevant for **population/evolution + candidate generation + automated objective evaluation**. ([DeepMind][5])

### Exo

Worth studying for the architecture idea of treating the **whole harness**—prompts, memory, tools, policies and implementation—as an object of recursive evolution. It is a research/community project rather than a benchmark-backed industry standard, so I would use it for architecture ideas rather than treating its claims as established results. ([GitHub][14])

### DeepAgents

Useful for your modular substrate: planning, filesystem, subagents, permissions, skills and context management. ([GitHub][15])

### Agent Workspace 2.0

Useful because it is already your base and provides the long-horizon harness infrastructure you need. ([GitHub][2])

### mini-SWE-agent

Useful as an example of keeping the agent core simple instead of endlessly adding framework machinery. ([GitHub][12])

### Agentless

Useful for structured localization → multiple repair candidates → validation/reranking rather than blindly letting one agent edit everything. ([GitHub][16])

### GitHub Agentic Workflows

Useful for the operational pattern of natural-language automation backed by Actions, permissions, sandboxing, safe outputs, review and security controls. ([The GitHub Blog][8])

### Reflexion

Useful for learning from failures via reflective memory rather than changing model weights. ([arXiv][3])

---

# 42. My recommended final system

For your particular harness, I would make the architecture:

```text
                         ┌─────────────────────┐
                         │   USER OBJECTIVE    │
                         └──────────┬──────────┘
                                    ▼
                         ┌─────────────────────┐
                         │   SUPER AGENT       │
                         └──────────┬──────────┘
                                    ▼
                   ┌────────────────────────────────┐
                   │       NORMAL EXECUTION          │
                   │ tools / memory / skills /       │
                   │ subagents / sandbox / MCP       │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │      OBSERVABILITY FABRIC       │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │       SELF-DEVELOPMENT         │
                   │                                │
                   │ problem detection              │
                   │ anomaly detection              │
                   │ capability-gap detection       │
                   │ automation mining              │
                   │ root-cause analysis             │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │        EVOLUTION LAB            │
                   │                                │
                   │ code mutations                 │
                   │ prompt mutations               │
                   │ skill generation               │
                   │ tool generation                │
                   │ workflow mutation              │
                   │ model routing optimization      │
                   │ memory optimization            │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │       CANDIDATE POPULATION      │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │       VERIFICATION FARM         │
                   │                                 │
                   │ tests                           │
                   │ replay                          │
                   │ benchmarks                      │
                   │ held-out tasks                  │
                   │ security                        │
                   │ performance                     │
                   │ adversarial testing             │
                   └───────────────┬────────────────┘
                                   ▼
                   ┌────────────────────────────────┐
                   │    INDEPENDENT EVOLUTION JUDGE  │
                   └───────────────┬────────────────┘
                                   ▼
                          ┌───────────────────┐
                          │ PROMOTION GATE    │
                          └───────┬───────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │ Shadow → Canary → Prod  │
                     └───────────┬─────────────┘
                                 ▼
                         ┌──────────────────┐
                         │ MONITOR + LEARN  │
                         └────────┬─────────┘
                                  ▼
                         Evolution Memory
                                  │
                                  └──────→ next generation
```

## 43. The key philosophical change

Your current concept is probably:

```text
AI agent + lots of tools
```

The next version should be:

```text
AI agent
+
environment
+
memory
+
tools
+
skills
+
observability
+
automatic problem discovery
+
automatic automation discovery
+
candidate generation
+
verification
+
evolution
+
deployment
+
rollback
+
evolution memory
```

So the harness doesn't just **execute intelligence**.

It becomes a system that continually asks:

```text
What am I doing?
↓
Where am I failing?
↓
Why?
↓
What capability is missing?
↓
Can this be automated?
↓
What changes could solve it?
↓
Which candidate actually works?
↓
Did it improve the whole system?
↓
Can I safely make it the new version?
↓
What did I learn from this experiment?
↓
What should I improve next?
```

That is the architecture I would target for your **agent-workspace-desktop next generation**.

One especially important design principle is to make **the evaluator harder to modify than the agent being evaluated**. Otherwise your system can evolve toward becoming better at convincing itself that it improved rather than actually improving.

The current research landscape strongly supports combining recursive self-modification (DGM/Live-SWE), evolutionary candidate search (AlphaEvolve), runtime feedback/memory (Reflexion), modular skills/subagents/sandboxes (Agent Workspace/DeepAgents), and deterministic automation/security gates (GitHub/OpenRewrite/Dependabot) rather than relying on a single RSI technique. ([GitHub][1])

I would make this **RSDE layer a first-class subsystem of your harness**, not just another agent/skill.

[1]: https://github.com/jennyzzt/dgm?utm_source=chatgpt.com "GitHub - jennyzzt/dgm: Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents · GitHub"
[2]: https://github.com/bytedance/agent-workspace/blob/main/README.md?utm_source=chatgpt.com "agent-workspace/README.md at main · bytedance/agent-workspace · GitHub"
[3]: https://arxiv.org/abs/2303.11366?utm_source=chatgpt.com "Reflexion: Language Agents with Verbal Reinforcement Learning"
[4]: https://openai.github.io/openai-agents-python/tracing/?utm_source=chatgpt.com "Tracing - OpenAI Agents SDK"
[5]: https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/?utm_source=chatgpt.com "AlphaEvolve: A Gemini-powered coding agent for designing advanced algorithms — Google DeepMind"
[6]: https://arxiv.org/abs/2608.10157?utm_source=chatgpt.com "SBCO: Self-Supervised, Verifier-Grounded Harness Optimization For Planning Agents"
[7]: https://github.com/microsoft/swe-bench-live?utm_source=chatgpt.com "GitHub - microsoft/SWE-bench-Live: [NeurIPS 2025 D&B] 🚀 SWE-bench Goes Live! · GitHub"
[8]: https://github.blog/changelog/2026-06-11-github-agentic-workflows-is-now-in-public-preview/?utm_source=chatgpt.com "GitHub Agentic Workflows is now in public preview - GitHub Changelog"
[9]: https://openai.github.io/openai-agents-js/?utm_source=chatgpt.com "OpenAI Agents SDK TypeScript | OpenAI Agents SDK"
[10]: https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/dependabot-quickstart?utm_source=chatgpt.com "Dependabot quickstart guide - GitHub Docs"
[11]: https://docs.openrewrite.org/concepts-and-explanations/recipes?utm_source=chatgpt.com "Recipes | OpenRewrite Docs"
[12]: https://github.com/swe-agent/mini-swe-agent?utm_source=chatgpt.com "GitHub - SWE-agent/mini-swe-agent: The 100 line AI agent that solves GitHub issues or helps you in your command line. Radically simple, no huge configs, no giant monorepo—but scores >74% on SWE-bench verified! · GitHub"
[13]: https://arxiv.org/abs/2511.13646?utm_source=chatgpt.com "Live-SWE-agent: Can Software Engineering Agents Self-Evolve on the Fly?"
[14]: https://github.com/exoharness/exo?utm_source=chatgpt.com "GitHub - exoharness/exo: Exo is an agent + harness architecture that is fully recursive, able to safely edit all aspects of itself at runtime to get better at your tasks. · GitHub"
[15]: https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md?utm_source=chatgpt.com "deepagents/libs/ARCHITECTURE.md at main · langchain-ai/deepagents · GitHub"
[16]: https://github.com/dojian/Agentless_Adv?utm_source=chatgpt.com "GitHub - dojian/Agentless_Adv: Agentless🐱: an agentless approach to automatically solve software development problems · GitHub"
