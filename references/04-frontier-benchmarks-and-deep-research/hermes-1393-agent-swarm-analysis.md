Yes. I researched the **actual Nous Research post, the 1,393-agent run, the forensic post-mortem, current Hermes architecture/docs, and several adjacent agent systems**. The important finding is that the screenshot is only the headline. The interesting part is the **harness engineering underneath it**—and, importantly, the post-mortem shows that the original run itself exposed many weaknesses in the harness.

## 1. What actually happened in the 1,393-agent Hermes run

The official post says that on **September 2, 2026**, Teknium gave Hermes a standing `/goal` to aggressively simplify the Hermes codebase. The run lasted about **19 active hours**, reached **218 concurrent agents**, dispatched **1,393 subagents**, and produced a refactor reducing non-test Python from **1,063,826 to 698,363 lines**, a **34.4% reduction**. The main run's estimated model cost was about **$19,300**, around **$25k including follow-up sessions**. ([Nous Research][1])

The blog's "nearly $2m" is **not a measured $2m cash saving**. It is a rough estimate of what a small human team might have cost over a very large range of timelines; the blog gives **$150k–$1.8M** as the rough staffing estimate. ([Nous Research][1])

The run produced very substantial structural changes:

| Metric                                   |      Before |   After |
| ---------------------------------------- | ----------: | ------: |
| Non-test Python LOC                      |   1,063,826 | 698,363 |
| Files >5,000 LOC                         |          37 |       6 |
| Functions >300 LOC                       |         192 |       2 |
| Longest `if/elif` chain                  | 92 branches |       9 |
| `gateway/run.py`                         |  34,847 LOC |   5,512 |
| Average symbol-lookup tokens             |       2,218 |     993 |
| Lookup operations needing another window |         628 |     184 |

The lookup experiment matters for your own harness: they found that **smaller code units reduced the amount of code an agent had to read**, but the authors explicitly note this does **not prove that engineering-task completion itself improved**. ([Nous Research][1])

---

# 2. The actual architecture of that run

The impressive part was not "1,393 LLM calls."

It was roughly this:

```text
                    USER
                      │
                      ▼
               Hermes /goal
                      │
                      ▼
             ROOT ORCHESTRATOR
                      │
             ┌────────┴────────┐
             │                 │
        Codebase scan      Objective / skill
             │                 │
             └────────┬────────┘
                      ▼
              36 work groups
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   Worker A        Worker B       Worker C
   worktree        worktree       worktree
       │              │              │
       ▼              ▼              ▼
   subagents      subagents       subagents
       │              │              │
       └──────────────┼──────────────┘
                      ▼
               commit / verify
                      │
                      ▼
                ROOT INTEGRATION
                      │
                      ▼
                  tests/checks
                      │
                      ▼
                PR / review
```

The root agent largely **coordinated rather than edited**. It divided the repository into 36 non-overlapping groups, wrote assignments, used git worktrees, read reports, integrated changes, and checked interfaces. Workers could delegate further, giving the tree **three levels below the original agent**. ([Nous Research][1])

They explicitly preserved interfaces such as:

* tool JSON schemas
* CLI `--help` behavior
* commits after verified work
* frozen baseline comparisons
* worktree isolation

That is a very important design pattern for your harness. ([Nous Research][1])

---

# 3. The most valuable part: the run became a self-audit of the harness

After the refactor, Hermes was turned on its **own telemetry** to find where the harness itself had caused unnecessary cost, latency, or correctness failures.

The forensic audit used:

* `state.db`
* rotated `agent.log` files
* NeMo Relay telemetry
* error logs
* Git history / AST diff
* live A/B reproductions

They used **five parallel investigative lanes**:

```text
                  POSTMORTEM
                      │
        ┌─────────────┼───────────────┐
        │             │               │
     tokens         delegation     tools
        │             │               │
        ├─────────────┼───────────────┤
        │             │               │
    goal/prompts    quality       evidence
```

Every mechanism was manually re-verified before being patched, and an independent reviewer subsequently reviewed the fixes without access to the original reasoning. ([Luca's Proxy][2])

This is the part I would copy **more aggressively than the 1,393-agent fan-out itself**.

---

# 4. The hidden failures are extremely important

The final post-mortem says the run had:

* **93,284 calls**
* **$19,302.59** estimated model cost
* average prompt around **206K tokens**
* 62% of calls above 150K context
* 218 concurrent agents
* 620 HTTP 401 failures
* a crash that killed large numbers of children
* 332 nested-delegation timeouts
* 114/121 root summaries truncated
* 546 false command-policy blocks
* 454 timeout refusals
* 661 expensive whole-file rewrites
* 1,703 public names/tests silently removed by the refactor
* a goal loop that spent **3h22m** waiting incorrectly
* a dead-wave that wasn't noticed for **66 minutes**. ([Luca's Proxy][2])

This is exactly why simply saying **"I'll spawn 1,000 agents" is not a world-class architecture.**

You need an architecture that makes those failure classes difficult or impossible.

---

# 5. What Hermes fixed afterward

The forensic fixes are basically a blueprint for what a serious autonomous-agent operating system needs.

### Context/cache layer

The run had thinking blocks being stripped from older assistant turns, breaking exact-byte cache prefixes.

The repair made the wire representation stable and corrected compaction accounting so retained thinking was included. ([Luca's Proxy][2])

### Child-context control

The original child compression point was effectively **850K** on a 1M window, meaning almost every worker stayed huge.

The fix moved the relevant child trigger to **200K**, while leaving the parent alone. Replay modeling suggested a large prompt-volume reduction. ([Luca's Proxy][2])

### Nested delegation

Workers under the timeout ceiling were getting killed while their results were being truncated.

The revised mechanism gives nested work more appropriate deadlines and ensures the result survives. ([Luca's Proxy][2])

### Authentication

The run exposed an authentication-refresh stampede. Hundreds of agents could hit expiry around the same time.

The fix introduces keepalive behavior and correct account identity handling. ([Luca's Proxy][2])

### Failure propagation

Before, a dead child could look like a slow child.

The fixed behavior surfaces a child failure quickly instead of waiting for the entire batch. ([Luca's Proxy][2])

### Goal-loop scheduling

The `/goal` judge incorrectly observed unrelated background processes and could wait indefinitely.

The fix scopes process observation and gives waits a maximum lifetime. ([Luca's Proxy][2])

### WAIT semantics

The judge previously kept waking up when there was nothing useful to do.

The new behavior explicitly allows:

```text
WORK
WAIT
WAIT
WAIT
RESULT ARRIVES
WAKE
CONTINUE
```

instead of:

```text
WORK
POLL
POLL
POLL
POLL
POLL
POLL
...
```

([Luca's Proxy][2])

### Mechanical refactor protection

They added CI checking for accidentally deleted public symbols and tests. The check found **1,703 public names** and **126 test definitions** at PR open. ([Luca's Proxy][2])

### Independent review

The independent review caught, among other things:

* an approval bypass
* credential/account identity confusion
* loss of final batch results
* bad context estimation
* scheduler bugs
* timeout-handling bugs
* verification tool bugs
* path-resolution errors

All were fixed before/around the finalization described in the post-mortem. ([Luca's Proxy][2])

---

# 6. The biggest lesson for your architecture

Do **not** build:

```text
            SUPERVISOR
          /   / | \   \
       100 agents...
```

Build:

```text
                        ┌─────────────────────┐
                        │   HUMAN / API / UI  │
                        └──────────┬──────────┘
                                   │
                                   ▼
                     ┌─────────────────────────┐
                     │     MISSION CONTROL     │
                     │ Goal + policy + budget  │
                     └────────────┬────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
               Planner       Router        Risk Manager
                    │             │             │
                    └─────────────┼─────────────┘
                                  ▼
                        ┌──────────────────┐
                        │  WORK GRAPH/DAG  │
                        └─────────┬────────┘
                                  │
               ┌──────────────────┼──────────────────┐
               ▼                  ▼                  ▼
          Research lane       Build lane        Verify lane
               │                  │                  │
          specialists        specialists        specialists
               │                  │                  │
               └──────────────────┼──────────────────┘
                                  ▼
                         ┌────────────────┐
                         │ EVIDENCE BUS   │
                         └───────┬────────┘
                                 ▼
                         ┌────────────────┐
                         │ INDEPENDENT    │
                         │ JUDGE/CRITIC   │
                         └───────┬────────┘
                                 │
                  ┌──────────────┼──────────────┐
                  ▼              ▼              ▼
               PASS           REWORK          ESCALATE
                  │              │
                  └──────────────┘
                         │
                         ▼
                 RELEASE / DELIVERY
```

Then put an **evolution loop around the whole system**:

```text
telemetry
   ↓
failure mining
   ↓
root-cause analysis
   ↓
harness change
   ↓
replay
   ↓
A/B test
   ↓
independent review
   ↓
promote fix
   ↓
update skills/policies
   ↓
future runs
```

That is substantially more interesting than just replicating Hermes' 1,393-worker demonstration.

---

# 7. The architecture I would build

## Layer A — Mission Control

This is above the normal "main agent."

Responsibilities:

```text
goal intake
goal normalization
success criteria
risk classification
budget allocation
deadline
priority
decomposition
model selection
agent selection
execution policy
termination decision
```

Every mission should become a structured object:

```yaml
mission:
  id: M-10291
  objective: "Refactor authentication subsystem"
  success:
    - tests_pass
    - no_public_api_break
    - security_scan_clean
    - performance_not_regressed
  budget:
    max_tokens: 2000000
    max_usd: 20
  deadline: 6h
  autonomy: high
  risk: medium
```

---

# 8. Layer B — Planner

Don't let every worker invent the architecture independently.

Have a planner produce:

```text
Goal
 ├── discovery
 ├── architecture
 ├── implementation
 ├── verification
 ├── integration
 └── release
```

Then transform it into a **DAG**, not just a list.

Example:

```text
A ───► C ───► F
       │       ▲
B ─────┘       │
               │
D ─────────────┘
```

Every node gets:

```text
task_id
parent_id
dependencies
inputs
outputs
acceptance criteria
budget
model
tool permissions
workspace
timeout
retry policy
```

OpenAI's Symphony specification is particularly useful here: it emphasizes an authoritative orchestrator, bounded concurrency, deterministic workspaces, retries, reconciliation, workflow contracts, and observability. ([GitHub][3])

---

# 9. Layer C — Dynamic workforce

Instead of fixed "1,000 generic agents", use **specialists**.

For example:

```text
CEO / Mission Director
│
├── CTO / Architecture
│   ├── System Architect
│   ├── API Architect
│   ├── Data Architect
│   └── Performance Architect
│
├── Research
│   ├── Web Researcher
│   ├── Paper Researcher
│   ├── Repository Archaeologist
│   └── Competitor Researcher
│
├── Engineering
│   ├── Backend
│   ├── Frontend
│   ├── DevOps
│   ├── Database
│   └── Infrastructure
│
├── Verification
│   ├── Test Engineer
│   ├── Security Reviewer
│   ├── API Compatibility Reviewer
│   ├── Performance Reviewer
│   └── Regression Reviewer
│
└── Release
    ├── Integration Manager
    ├── Documentation
    └── Release Agent
```

Paperclip goes even further by treating agents as persistent organizational employees with roles, reporting relationships, capabilities, budgets and lifecycle state. ([GitHub][4])

Hermes itself now has persistent Bot profiles with individual role/model/memory/skills, and Kanban workers with durable task state. ([GitHub][5])

---

# 10. Layer D — Agent router

This is an area where you can go beyond the basic Hermes design.

Don't ask:

> "Which agent should I use?"

Every task should be classified along several axes:

```text
complexity
domain
risk
required tools
context size
latency requirement
reasoning requirement
cost ceiling
verification level
```

Then route dynamically.

Example:

```text
Simple text edit
 → cheap fast model

Repository exploration
 → fast coding model

Architecture
 → strong reasoning model

Security
 → independent security specialist

UI verification
 → vision-capable model

Final release decision
 → strongest available judge
```

Hermes already supports different models for delegation, but your router can make this **phase-aware and task-aware** rather than a single delegation model. ([GitHub][6])

---

# 11. Layer E — Shared blackboard

This is one of the biggest improvements I'd make over pure parent→child summaries.

Don't pass giant text between agents.

Instead:

```text
                BLACKBOARD
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
   findings        artifacts       decisions
      │              │              │
      ▼              ▼              ▼
   evidence        files          ADRs
```

Store structured objects:

```json
{
  "finding_id": "F-18",
  "agent": "security-3",
  "claim": "JWT validation accepts expired token",
  "evidence": [
    "tests/auth/test_expiry.py",
    "src/auth/jwt.py:84-108"
  ],
  "confidence": 0.94,
  "status": "unverified"
}
```

This lets downstream agents consume **evidence references**, not 100,000-token transcripts.

Hermes' own multi-agent roadmap has identified shared workflow memory/blackboards as an important distinction from isolated delegation, and current Kanban swarm support uses a structured blackboard on the root task. ([GitHub][7])

---

# 12. Layer F — Artifact-first communication

A worker should not return:

> "I finished the refactor."

It should return:

```yaml
result:
  status: completed

changed_files:
  - src/auth/jwt.py
  - tests/auth/test_expiry.py

verification:
  - pytest tests/auth -q
  - mypy src/auth

evidence:
  - commit: abc123
  - test_report: artifact://...
  - diff: artifact://...

risks:
  - legacy OAuth flow not exercised

recommended_next:
  - security_review
```

This is exactly the direction Hermes Kanban's structured handoff metadata is moving toward. ([Hermes Agent][8])

---

# 13. Layer G — Independent verifier

This is essential.

The implementation agent should **never be the final judge of itself**.

Your pipeline should be:

```text
IMPLEMENTER
    ↓
STATIC CHECKS
    ↓
TESTS
    ↓
INDEPENDENT REVIEWER
    ↓
SECURITY REVIEW
    ↓
ACCEPTANCE JUDGE
    ↓
MERGE
```

Hermes itself explored this pattern through its independent-verification architecture: separate context, baseline comparison, fail-closed verification, then a bounded auto-fix loop. ([GitHub][9])

You can make yours stricter:

```text
Verifier A → correctness
Verifier B → security
Verifier C → regression
Verifier D → requirements
Verifier E → maintainability
```

Then a meta-judge combines their **evidence**, not their prose.

---

# 14. Layer H — Consensus only when useful

Don't make 10 agents vote on everything.

Use consensus when uncertainty is high.

```text
               QUESTION
                   │
             uncertainty > τ
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
       expert1   expert2   expert3
          │        │        │
          └────────┼────────┘
                   ▼
             CONSENSUS
```

You can support:

```text
majority
supermajority
unanimous
quorum
weighted expertise
confidence-weighted
evidence-weighted
```

Hermes has an explicit architecture proposal for a structured consensus engine with these sorts of strategies. ([GitHub][10])

---

# 15. Layer I — Debate / adversarial agents

Another strong feature:

```text
Architect
    ↓
Critic
    ↓
Architect revision
    ↓
Security critic
    ↓
Final judge
```

For architecture:

```text
proposal A
proposal B
proposal C
    ↓
independent critics
    ↓
trade-off analysis
    ↓
synthesis
```

The key is that debate should be **triggered by uncertainty**, not run continuously.

Hermes' multi-agent roadmap explicitly includes adversarial debate as a separate pattern from consensus. ([GitHub][11])

---

# 16. Layer J — Automatic recovery

This is where your system can become dramatically stronger.

Every task should have a state machine:

```text
CREATED
   ↓
READY
   ↓
RUNNING
   ├──► WAITING
   ├──► BLOCKED
   ├──► FAILED
   └──► COMPLETED
             │
             ▼
          VERIFIED
             │
             ▼
           MERGED
```

Failure path:

```text
FAIL
 │
 ├─ transient? → RETRY
 │
 ├─ context problem? → COMPRESS / REPLAN
 │
 ├─ capability problem? → ROUTE TO SPECIALIST
 │
 ├─ dependency problem? → WAIT
 │
 ├─ repeated failure? → ARCHITECT REVIEW
 │
 └─ critical risk? → HUMAN ESCALATION
```

No blind `retry × 10`.

Use:

```text
attempt 1 → same strategy
attempt 2 → modified strategy
attempt 3 → alternate agent
attempt 4 → architectural review
```

Then circuit-break.

Hermes Kanban now uses retry limits, heartbeats, crash recovery, stale-worker detection, orphan reconciliation and circuit breakers. ([Hermes Agent][8])

---

# 17. Layer K — Agent health system

For hundreds of agents you need a **scheduler-grade health model**.

Track:

```text
heartbeat
CPU
RAM
token rate
API latency
error rate
tool failure rate
progress
last artifact
last useful output
retry count
stuck duration
```

Then:

```text
HEALTHY
DEGRADED
STALLED
CRASHED
ORPHANED
QUARANTINED
```

An agent that isn't progressing should be treated like a failed distributed-system worker.

Not:

> "Maybe the LLM needs another 30 minutes."

---

# 18. Layer L — Lease-based task ownership

For every task:

```text
task_id
worker_id
lease_id
lease_expiry
heartbeat
attempt
```

This prevents:

```text
worker dies
     ↓
task remains RUNNING forever
```

Instead:

```text
heartbeat expires
      ↓
lease reclaimed
      ↓
new worker
      ↓
resume from checkpoint
```

This is one of the biggest differences between a toy multi-agent system and an autonomous workforce.

---

# 19. Layer M — Checkpoint everything

Don't checkpoint only the final answer.

Checkpoint:

```text
plan
task graph
workspace
branch
commit
tool results
artifact references
verification
agent state
retry history
decision history
budget
```

A crashed machine should be able to resume from:

```text
last durable state
```

rather than:

```text
start again
```

---

# 20. Layer N — Context engineering

This is possibly the single biggest performance lever.

Use:

```text
global context
      ↓
task context
      ↓
role context
      ↓
file context
      ↓
evidence context
```

Never dump:

```text
entire conversation
entire repository
all sibling outputs
all tool logs
```

into every model.

Instead:

```text
Need X?
  → retrieve X

Need implementation history?
  → retrieve decision record

Need code?
  → retrieve relevant symbols

Need evidence?
  → retrieve evidence objects
```

I'd also implement a **context budget allocator**:

```text
Task budget = 100K

system         5K
goal           4K
repo map       8K
relevant code 35K
evidence       20K
history        10K
scratch        18K
```

The agent gets what it needs, not everything that exists.

---

# 21. Layer O — Semantic code graph

This is something I'd definitely add to your project.

Build:

```text
Repository
    ↓
AST
    ↓
symbols
    ↓
imports
    ↓
call graph
    ↓
dependency graph
    ↓
API surface
    ↓
test coverage
```

Then agents can ask:

```text
Who calls this function?
What interfaces depend on this?
Which tests cover this?
Which external symbols are public?
What modules will this change affect?
```

This is much stronger than naïve grep.

Hermes already provides AST-oriented refactoring capabilities via its `ast-grep` skill; your harness can make structural code intelligence a first-class service rather than an optional tool. ([Hermes Agent][12])

---

# 22. Layer P — Refactoring safety engine

For huge autonomous refactors, automatically calculate:

```text
public symbol diff
API schema diff
CLI help diff
config schema diff
database schema diff
dependency graph diff
test definition diff
behavioral test diff
```

Then reject:

```text
removed public API
unexpected deleted test
changed JSON schema
changed CLI contract
new dependency
security-sensitive change
```

This is directly motivated by what happened in the Hermes run. ([Luca's Proxy][2])

---

# 23. Layer Q — Mutation testing

Here's a feature I'd add that is not just copying Hermes.

When the agent claims a test protects a behavior:

```text
test passes
    ↓
mutate implementation
    ↓
test SHOULD fail
```

If it still passes:

```text
WEAK TEST
```

Then send it to a test-engineering agent.

This creates:

```text
code
 ↓
tests
 ↓
mutation
 ↓
test-strength measurement
```

That is a much stronger autonomous-quality signal than "pytest returned 0."

---

# 24. Layer R — Shadow verification

Before merging risky changes:

```text
current implementation
       │
       ├── production candidate
       │
       └── previous version
```

Run important requests through both.

Compare:

```text
output
latency
errors
API behavior
database effects
resource consumption
```

This gives you a **behavioral differential harness**.

---

# 25. Layer S — Canary agent execution

For autonomous systems, don't immediately expose a new agent/tool/prompt change to the entire fleet.

Use:

```text
new harness
   ↓
1% tasks
   ↓
shadow metrics
   ↓
5%
   ↓
25%
   ↓
100%
```

Rollback automatically on:

```text
error rate ↑
cost ↑
verification failures ↑
latency ↑
security incidents
```

---

# 26. Layer T — Cost intelligence

Hermes' audit revealed that **58% of the run's recorded spend was cache writes**, with additional cost from output and cache reads. ([Luca's Proxy][2])

Your harness should maintain a live:

```text
MISSION COST
├── planner
├── workers
├── reviewers
├── tools
├── cache writes
├── cache reads
├── retries
└── failed work
```

Then calculate:

```text
cost / successful task
cost / verified artifact
cost / merged change
cost / objective completed
cost / prevented defect
```

This is much more useful than simply displaying token usage.

---

# 27. Layer U — Budget-aware scheduling

Before spawning 20 workers:

```text
estimated_value = expected_information_gain
estimated_cost  = predicted_tokens
risk            = task risk
```

Then:

```text
priority =
    value × confidence_gain × urgency
    -------------------------------
    cost × risk
```

You don't need 100 agents to inspect a simple file.

The scheduler should be able to say:

```text
1 agent is enough
3 agents are useful
10 agents provide diminishing returns
stop fan-out
```

This makes your harness **agent-economical** rather than agent-maximal.

---

# 28. Layer V — Information-gain-driven fanout

This can be one of your differentiators.

Start with:

```text
3 researchers
```

If their answers converge:

```text
STOP
```

If they disagree:

```text
spawn 3 more specialists
```

If uncertainty remains:

```text
launch debate / experiment
```

So the swarm grows based on **uncertainty**, not a hard-coded number.

---

# 29. Layer W — Speculative execution

For difficult tasks:

```text
Planner
 ├── Strategy A
 ├── Strategy B
 └── Strategy C
```

Run them in isolated worktrees.

Then:

```text
Verifier
   ↓
compare
   ↓
select implementation
```

This can outperform forcing one agent to reason indefinitely about a difficult implementation.

---

# 30. Layer X — Git as the coordination substrate

For engineering work:

```text
main
 │
 ├── task/A
 ├── task/B
 ├── task/C
 └── task/D
```

Each task has:

```text
isolated workspace
isolated branch
checkpoint commits
verification
merge gate
```

Then integration becomes:

```text
child branch
    ↓
verification
    ↓
compatibility check
    ↓
merge
    ↓
integration tests
```

This is central to the Hermes refactor and Symphony-style autonomous workflows. ([Nous Research][1])

---

# 31. Layer Y — Persistent workforce, not disposable children

Hermes now distinguishes `delegate_task` from durable Kanban workers:

| Disposable delegation     | Persistent workforce  |
| ------------------------- | --------------------- |
| anonymous child           | named agent           |
| short-lived               | persistent            |
| result returned to parent | result stored durably |
| fragile across restart    | resumable             |
| parent-child              | task queue            |
| no durable history        | audit trail           |
| simple RPC                | state machine         |

Hermes explicitly documents this distinction. ([Hermes Agent][8])

Your architecture should support **both**.

Use:

```text
delegate → quick reasoning
workforce → real work
```

---

# 32. Layer Z — External agent federation

This is a major opportunity.

Don't limit your workers to your own agent runtime.

Support adapters:

```text
Hermes
Claude Code
Codex
OpenCode
Grok
OpenHands
custom Python agent
remote HTTP agent
MCP agent
container worker
```

Then:

```text
                    ORCHESTRATOR
                         │
      ┌──────────┬───────┼─────────┬─────────┐
      ▼          ▼       ▼         ▼         ▼
   Hermes      Codex   Claude    OpenCode   Custom
```

Hermes itself now has skills for delegating to external coding agents such as Codex and OpenCode, but its own issue tracker has also identified the gap around **parallel cross-CLI orchestration and inter-agent output routing**. ([Hermes Agent][13])

That is exactly an area your architecture can improve.

---

# 33. Layer AA — Long-running daemon

Your supervisor shouldn't depend on the chat session staying open.

Use:

```text
MISSION
  ↓
QUEUE
  ↓
WORKER
  ↓
CHECKPOINT
  ↓
RESTART
  ↓
RESUME
```

This is closer to a distributed worker platform than a chatbot.

OpenAI's Symphony design explicitly treats the system as a long-running automation service with an authoritative orchestrator, persistent workspaces, retry/reconciliation, and structured observability. ([GitHub][3])

---

# 34. Layer AB — Event-sourced history

Every action should create an event:

```text
task.created
task.claimed
agent.spawned
tool.started
tool.completed
artifact.created
verification.failed
retry.started
worker.crashed
worker.reclaimed
judge.approved
task.merged
```

Then you can reconstruct:

```text
WHAT happened?
WHEN?
WHY?
WHO?
WITH WHICH MODEL?
AT WHAT COST?
WITH WHAT EVIDENCE?
```

This also gives you replay.

---

# 35. Layer AC — Full replay system

Your harness should allow:

```bash
agentctl replay mission-1092
```

and reproduce:

```text
same task graph
same policies
same tool inputs
same checkpoints
same verification
```

with either:

```text
recorded model responses
```

or:

```text
fresh models
```

This turns production failures into eval cases.

Hermes' forensic harness now explicitly supports reproducing findings against `state.db`, and that principle is worth adopting. ([Luca's Proxy][2])

---

# 36. Layer AD — Automatic skill evolution

This is one of Hermes' strongest ideas and should be retained.

But make your learning pipeline explicit:

```text
experience
   ↓
failure / correction
   ↓
extract lesson
   ↓
candidate skill
   ↓
test skill against historical cases
   ↓
A/B evaluation
   ↓
promote
   ↓
skill registry
```

Never let an agent directly mutate its permanent instructions without evaluation.

Use:

```text
candidate skill
```

first.

Then:

```text
promoted skill
```

only after passing historical regression tests.

Hermes' official design already treats Skills as accumulated procedural knowledge rather than ordinary memory. ([Nous Research][1])

---

# 37. Layer AE — Memory architecture

Don't use one giant memory.

Use:

```text
Episodic memory
  what happened

Semantic memory
  what is known

Procedural memory
  how to do things

Project memory
  repository-specific knowledge

Agent memory
  specialist-specific experience

Mission memory
  current objective

Failure memory
  known traps

Decision memory
  why choices were made
```

Then use different retrieval rules for each.

---

# 38. Layer AF — Security architecture

This needs to be first-class.

Every agent gets:

```text
identity
role
capabilities
workspace
secrets
network permissions
filesystem permissions
approval policy
budget
```

Do not make:

```text
agent = all powerful process
```

Make:

```text
agent = capability token
```

For example:

```yaml
capabilities:
  filesystem:
    read:
      - repo/**
    write:
      - worktree/**
  network:
    - github.com
    - pypi.org
  secrets:
    github_token: scoped
  shell:
    allow:
      - pytest
      - git
      - npm
```

This is particularly important because Hermes' independent review found a real command-approval bypass during the refactoring-fix process. ([Luca's Proxy][2])

---

# 39. Layer AG — Human escalation

Autonomy does **not** mean "never ask a human."

Define escalation policies:

```text
low risk
 → autonomous

medium risk
 → autonomous + independent review

high risk
 → human approval

critical security action
 → mandatory human approval
```

And make approval explicit:

```text
APPROVAL_REQUIRED
reason:
evidence:
risk:
proposed_action:
rollback:
```

---

# 40. Layer AH — Goal verification

Do not define:

```text
DONE = model says done
```

Define:

```text
DONE =
 acceptance criteria satisfied
 + required evidence exists
 + verification passed
 + no unresolved blockers
```

This is a major principle in both Hermes Kanban's completion protocol and Symphony's proof-of-work model. ([Hermes Agent][8])

---

# 41. Layer AI — Proof-of-work protocol

Every completed task should produce:

```text
WHAT changed
WHY
FILES
TESTS
CHECKS
EVIDENCE
RISK
REMAINING WORK
```

For an engineering task:

```yaml
proof:
  commit: abc123
  changed_files: 7
  tests:
    unit: 124
    integration: 38
  lint: pass
  typecheck: pass
  security: pass
  api_compatibility: pass
  visual_check: pass
  reviewer: approved
```

---

# 42. Layer AJ — Visual verification

For frontend/UI work:

```text
build
 ↓
launch
 ↓
screenshot
 ↓
vision evaluator
 ↓
compare to requirement
 ↓
fix
```

This is especially useful for dashboards, websites, desktop apps, PDFs and presentations.

Claude's current agent capabilities similarly emphasize using vision to inspect outputs against the original goal. ([Anthropic][14])

---

# 43. Layer AK — Continuous background work

Add:

```text
heartbeats
cron
watchers
issue polling
CI watchers
repository monitors
security scanners
dependency monitors
```

Hermes has both recurring heartbeats and durable cron systems, while Symphony treats repository work as a continuously reconciled workflow. ([Hermes Agent][15])

---

# 44. Layer AL — Agent marketplace/registry

Make specialists installable:

```text
agents/
  security/
  research/
  backend/
  frontend/
  data/
  devops/
  legal-document/
  pdf/
  robotics/
```

Each agent package contains:

```text
identity
prompt
skills
tools
model policy
verification policy
budget policy
evaluation suite
```

Then you can add/remove an agent without modifying the core.

This fits Hermes' own architectural preference that the core remain a narrow waist while capabilities live at the edges. ([GitHub][16])

---

# 45. The architecture I'd target for you

I would make the system approximately:

```text
                         ┌────────────────────────┐
                         │       USER / API       │
                         └────────────┬───────────┘
                                      │
                                      ▼
                    ┌───────────────────────────────┐
                    │       MISSION CONTROL         │
                    │ Goal / Policy / Risk / Budget │
                    └──────────────┬────────────────┘
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
                  PLANNER       ROUTER       GOVERNOR
                     │             │             │
                     └─────────────┼─────────────┘
                                   ▼
                         ┌───────────────────┐
                         │   WORK GRAPH/DAG  │
                         └─────────┬─────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
       RESEARCH POOL         BUILD POOL            VERIFICATION
             │                     │                     │
       ┌─────┴────┐          ┌─────┴─────┐        ┌─────┴──────┐
       │  Agents  │          │  Agents   │        │   Judges   │
       └─────┬────┘          └─────┬─────┘        └─────┬──────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ▼
                         ┌───────────────────┐
                         │   BLACKBOARD /    │
                         │   ARTIFACT BUS    │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │ EVIDENCE ENGINE   │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │ INDEPENDENT JUDGE │
                         └───────┬───┬───────┘
                                 │   │
                         PASS ───┘   └── FAIL
                           │             │
                           ▼             ▼
                        MERGE         REPLAN/FIX
                           │             │
                           └──────┬──────┘
                                  ▼
                           DELIVERY / RELEASE
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │ TELEMETRY + COST + LOGS│
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │   EVALUATION / REPLAY   │
                     └────────────┬────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │   SELF-IMPROVEMENT      │
                     │ Skills / policies / DAGs │
                     └─────────────────────────┘
```

---

# 46. Your key differentiator should be "agent operating system", not "agent swarm"

The current ecosystem already has several pieces:

**Hermes:** persistent memory/skills, delegation, Kanban, Bot Mode, long-running tasks. ([GitHub][17])

**Symphony:** durable issue-driven orchestration, workspaces, retries, reconciliation, workflow contracts and proof-of-work. ([GitHub][3])

**Paperclip:** organization hierarchy, persistent AI employees, budgets and governance/control plane. ([GitHub][18])

**Alpha:** long-horizon super-agent runtime with subagents, memory, sandboxing and extensible skills/tools. ([GitHub][19])

**MetaGPT:** role-specialized software-company workflow with explicit SOPs for product/architecture/engineering stages. ([GitHub][20])

**AutoGen:** multiple coordination patterns such as selector-based teams and handoff/swarm designs. ([Microsoft GitHub][21])

**Grok Bot:** persistent named agents with their own computers, memory and long-running work across applications. ([SpaceXAI][22])

Your opportunity is to combine the useful ideas into a **single runtime with stronger guarantees**.

---

# 47. The feature set I would specifically build

### Core

```text
✓ hierarchical orchestration
✓ DAG workflows
✓ persistent task queue
✓ named persistent agents
✓ specialist roles
✓ dynamic model routing
✓ parallel execution
✓ worktree isolation
✓ checkpointing
✓ resumability
✓ agent heartbeats
✓ leases
✓ automatic retry
✓ circuit breakers
✓ orphan recovery
```

### Intelligence

```text
✓ adaptive task decomposition
✓ uncertainty-driven fanout
✓ multi-model routing
✓ parallel hypothesis generation
✓ adversarial debate
✓ consensus
✓ specialist escalation
✓ information-gain scheduling
✓ speculative execution
```

### Knowledge

```text
✓ blackboard
✓ artifact registry
✓ evidence graph
✓ semantic code graph
✓ project memory
✓ procedural memory
✓ failure memory
✓ decision memory
✓ session replay
```

### Verification

```text
✓ independent judge
✓ acceptance criteria
✓ baseline comparison
✓ regression tests
✓ mutation testing
✓ API compatibility
✓ public-surface diff
✓ security scan
✓ visual verification
✓ shadow execution
✓ canary rollout
✓ automatic rollback
```

### Self-improvement

```text
✓ failure mining
✓ automatic lesson extraction
✓ candidate skill generation
✓ historical replay evaluation
✓ skill promotion
✓ prompt/policy A/B testing
✓ workflow optimization
✓ model-routing optimization
```

### Operations

```text
✓ real-time dashboard
✓ cost ledger
✓ token accounting
✓ agent health
✓ task timeline
✓ distributed tracing
✓ event sourcing
✓ replay
✓ incident reports
✓ mission analytics
```

### Safety

```text
✓ capability-based permissions
✓ per-agent sandbox
✓ secret isolation
✓ filesystem scopes
✓ network scopes
✓ approval gates
✓ destructive-action confirmation
✓ audit log
✓ policy engine
```

---

# 48. The most important upgrade over the Hermes 1,393-agent experiment

The goal shouldn't be:

> **"Can my system create 1,393 subagents?"**

The goal should be:

> **"Can my system autonomously determine how many agents are necessary, assign the right specialists, preserve their work across failures, verify their output independently, prove completion, recover from faults, control cost, and learn from the run?"**

That turns the problem from **swarm generation** into **autonomous workforce control**.

And the Hermes post-mortem itself strongly supports this direction: the impressive fan-out exposed cache, scheduling, timeout, authentication, verification, security, and coordination problems that only became visible at scale. ([Luca's Proxy][2])

## 49. A practical build sequence

I would build it in this order:

```text
PHASE 1
Mission → Planner → DAG → Worker → Result

PHASE 2
Persistent queue → worktrees → checkpoints → retries

PHASE 3
Named specialist agents → router → model selection

PHASE 4
Blackboard → artifacts → evidence graph

PHASE 5
Independent verifier → acceptance gates → auto-fix

PHASE 6
Health → leases → heartbeat → orphan recovery

PHASE 7
Consensus → debate → speculative execution

PHASE 8
Replay → telemetry → cost intelligence

PHASE 9
Skill evolution → historical evaluation → A/B promotion

PHASE 10
Federated agents → remote workers → 100s/1000s scale
```

The architecture should be **plugin-first**, so you can remove or replace any individual subsystem without rewriting the core. That's also consistent with Hermes' current "narrow waist/core, capabilities at the edges" design principle. ([GitHub][16])

### The end state

```text
                    YOUR AGENT OS
                         │
             ┌───────────┴────────────┐
             │                        │
         HUMAN CONTROL          AUTONOMOUS LOOP
             │                        │
             ▼                        ▼
          MISSION                  DISCOVER
             │                        ↓
             └──────────────────► PLAN
                                      ↓
                                  DECOMPOSE
                                      ↓
                                  ROUTE
                                      ↓
                                  EXECUTE
                                      ↓
                                  OBSERVE
                                      ↓
                                  VERIFY
                                      ↓
                       ┌──────────────┴──────────────┐
                       │                             │
                     PASS                          FAIL
                       │                             │
                       ▼                             ▼
                    DELIVER                     REPLAN/FIX
                       │                             │
                       └──────────────┬──────────────┘
                                      ▼
                                   LEARN
                                      ↓
                                  EVOLVE
                                      ↓
                                   REPEAT
```

**That is the direction I'd take for a genuinely high-end Hermes-derived harness: not merely "Hermes + more subagents", but a durable, evidence-driven, self-optimizing agent operating system.**

[1]: https://nousresearch.com/refactoring-hermes-with-1393-agents/ "Refactoring Hermes with 1,393 agents - NOUS RESEARCH"
[2]: https://vpn.luca-server.com/px/aHR0cHM6Ly9naXRodWIuY29tL05vdXNSZXNlYXJjaC9oZXJtZXMtYWdlbnQvaXNzdWVzLzEwMzU2Mw "Tracking: forensic post-mortem of the 1,393-agent refactor run (#102117) — 13 harness fixes, independently reviewed · Issue #103563 · NousResearch/hermes-agent · GitHub"
[3]: https://github.com/openai/symphony/blob/main/SPEC.md?utm_source=chatgpt.com "symphony/SPEC.md at main · openai/symphony · GitHub"
[4]: https://github.com/paperclipai/paperclip/blob/master/docs/guides/board-operator/managing-agents.md?utm_source=chatgpt.com "paperclip/docs/guides/board-operator/managing-agents.md at master · paperclipai/paperclip · GitHub"
[5]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/bot-mode.md?utm_source=chatgpt.com "hermes-agent/website/docs/user-guide/bot-mode.md at main · NousResearch/hermes-agent · GitHub"
[6]: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/faq.md?utm_source=chatgpt.com "hermes-agent/website/docs/reference/faq.md at main · NousResearch/hermes-agent · GitHub"
[7]: https://github.com/NousResearch/hermes-agent/issues/377?utm_source=chatgpt.com "Feature: Shared Memory Pools Between Sub-Agents in Workflows (inspired by CAMEL-AI) · Issue #377 · NousResearch/hermes-agent"
[8]: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban?utm_source=chatgpt.com "Kanban (Multi-Agent Board) | Hermes Agent"
[9]: https://github.com/NousResearch/hermes-agent/issues/406?utm_source=chatgpt.com "Feature: Independent Code Verification & Quality Gates — Fail-Closed Review, Baseline Regression Detection, and Auto-Fix Loop (inspired by Nightwire) · Issue #406 · NousResearch/hermes-agent · GitHub"
[10]: https://github.com/NousResearch/hermes-agent/issues/412?utm_source=chatgpt.com "Feature: Consensus & Voting Engine for Multi-Agent Decision Making (inspired by AgentWorkforce/relay) · Issue #412 · NousResearch/hermes-agent · GitHub"
[11]: https://github.com/NousResearch/hermes-agent/issues/344?utm_source=chatgpt.com "Feature: Multi-Agent Architecture — Orchestration, Cooperation, Specialized Roles & Resilient Workflows · Issue #344 · NousResearch/hermes-agent"
[12]: https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/skills/optional/software-development/software-development-ast-grep?utm_source=chatgpt.com "Ast Grep — AST-aware structural code search and rewrite via ast-grep | Hermes Agent"
[13]: https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-codex?utm_source=chatgpt.com "Codex — Delegate coding to OpenAI Codex CLI (features, PRs) | Hermes Agent"
[14]: https://www.anthropic.com/claude/fable?utm_source=chatgpt.com "Claude Fable \ Anthropic"
[15]: https://hermes-agent.nousresearch.com/docs/user-guide/features/heartbeat?utm_source=chatgpt.com "Session Heartbeats | Hermes Agent"
[16]: https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md?utm_source=chatgpt.com "hermes-agent/AGENTS.md at main · NousResearch/hermes-agent · GitHub"
[17]: https://github.com/NousResearch/Hermes-Agent?utm_source=chatgpt.com "GitHub - NousResearch/hermes-agent: The agent that grows with you · GitHub"
[18]: https://github.com/paperclipai/paperclip/blob/master/doc/PRODUCT.md?utm_source=chatgpt.com "paperclip/doc/PRODUCT.md at master · paperclipai/paperclip · GitHub"
[19]: https://github.com/bytedance/agent-workspace/blob/main/frontend/src/content/en/introduction/index.mdx?utm_source=chatgpt.com "agent-workspace/frontend/src/content/en/introduction/index.mdx at main · bytedance/agent-workspace · GitHub"
[20]: https://github.com/foundationagents/metagpt?utm_source=chatgpt.com "GitHub - FoundationAgents/MetaGPT: 🌟 The Multi-Agent Framework: First AI Software Company, Towards Natural Language Programming · GitHub"
[21]: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html?utm_source=chatgpt.com "Swarm — AutoGen"
[22]: https://x.ai/news/introducing-grok-bot?utm_source=chatgpt.com "Introducing Grok Bot | SpaceXAI"








