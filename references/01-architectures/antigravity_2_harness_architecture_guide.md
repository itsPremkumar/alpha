# Antigravity 2.0-Inspired Advanced Architecture for an Agentic Harness

**Target:** Alpha-based, Windows-first autonomous agent harness
**Purpose:** Extract the strongest architectural patterns from Google Antigravity 2.0 and combine them with a production-oriented long-horizon agent runtime, multi-agent orchestration, verification, recovery, observability, and recursive self-development.
**Status:** Architecture / implementation guide
**Date:** 2026-09-16

---

## 0. Executive Summary

Google Antigravity 2.0 is best understood as an **agent command center** rather than a conventional IDE. Its current public architecture exposes synchronous/asynchronous agent execution, dynamic subagents, scheduled tasks, artifacts, Projects, Skills, MCP, JSON Hooks, browser interaction, permissions, and a public slash-command layer. The current documented orchestration commands include `/boost`, `/teamwork-preview`, `/goal`, `/plan`, `/grill-me`, `/learn`, `/schedule`, `/browser`, and `/btw`.

The most important architectural lesson for this harness is not to reproduce Antigravity's UI. It is to reproduce the **runtime pattern** behind the UI:

```text
Goal
  -> Command / Mode Router
  -> Executive Agent
  -> Planner
  -> Dynamic Agent / Subagent Fabric
  -> Skills + Tools + MCP
  -> Sandboxed Execution
  -> Artifacts + Evidence
  -> Verification
  -> Recovery / Replanning
  -> Completion Proof
  -> Observation + Evaluation
  -> Learning / RSI
  -> Candidate Improvement
  -> Benchmark
  -> Safe Deployment / Rollback
```

The intended final system is therefore not merely a chatbot and not merely a workflow graph. It is a **persistent agent operating system/runtime** around models.

Alpha is a strong base because its current architecture already frames the harness as the runtime layer for long-horizon agents, with planning, subagents, sandboxed execution, modular skills/tools, memory and context engineering. Antigravity adds a particularly useful interaction and orchestration vocabulary that can be translated into reusable harness modes.

---

# 1. Source-Derived Architecture vs Proposed Enhancements

This document deliberately separates three categories:

### A. Directly observed Antigravity patterns

- `/boost`: three-tier multi-agent reasoning hierarchy.
- `/teamwork-preview`: long-horizon collaborative agent teams.
- `/goal`: continuous autonomous execution until an objective is achieved.
- `/plan`: repository analysis followed by a reviewable implementation-plan artifact.
- `/grill-me`: requirements and edge-case interview before implementation.
- `/learn`: distillation of session feedback/corrections into Rules or Skills.
- `/schedule`: one-time and recurring autonomous work.
- `/browser`: sandboxed browser subagent.
- `/btw`: background side question without interrupting the primary run.
- Dynamic custom subagents.
- Agent Skills packaged around `SKILL.md`.
- MCP integrations.
- JSON Hooks.
- Projects spanning multiple folders with scoped settings/permissions.
- Artifacts as first-class deliverables.
- Fine-grained permission controls.
- Agent management and task monitoring.

### B. Alpha-aligned patterns

- Long-horizon runtime harness.
- Tool access.
- Skill loading.
- Sandboxed execution.
- Persistent memory.
- Context engineering.
- Parallel/isolated subagents.
- Extensible architecture around a harness boundary.

### C. Proposed extensions for this harness

The rest of this document extends those ideas with:

- Goal contracts.
- Explicit finite-state run lifecycle.
- Event-sourced execution history.
- Replay and time-travel debugging.
- Evidence graphs.
- Multi-level verification.
- Failure diagnosis and recovery planning.
- Model routing and failover.
- Agent reputation/performance data.
- Capability discovery.
- Project knowledge graphs.
- Shadow evaluation.
- Candidate/production separation for RSI.
- Canary deployment and automatic rollback.
- Self-diagnostics and environment-aware recovery.
- Long-running persistence across crashes/reboots.
- Organization/company mode.
- Dynamic agent factory.
- Goal drift and assumption tracking.
- Comprehensive policy and trust boundaries.

These extensions are design recommendations, not claims that Antigravity itself implements all of them.

---

# 2. Core Design Principle

Do not build:

```text
LLM
  -> tools
  -> answer
```

Build:

```text
                    +----------------+
                    |    USER GOAL   |
                    +-------+--------+
                            |
                            v
                 +----------------------+
                 | GOAL / INTENT ENGINE |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | COMMAND / MODE ROUTER|
                 +----------+-----------+
                            |
                +-----------+-----------+
                |           |           |
                v           v           v
             /plan       /boost       /goal
                |           |           |
                +-----------+-----------+
                            |
                            v
                 +----------------------+
                 | EXECUTIVE SUPERVISOR  |
                 +----------+-----------+
                            |
                +-----------+-----------+
                |                       |
                v                       v
        +---------------+       +---------------+
        | PLANNING FABRIC|       | AGENT FACTORY |
        +-------+-------+       +-------+-------+
                |                       |
                +-----------+-----------+
                            |
                            v
                 +----------------------+
                 | EXECUTION FABRIC     |
                 | tools / MCP / browser|
                 | terminal / files     |
                 | code / APIs / etc.   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | ARTIFACT + EVIDENCE   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | VERIFICATION ENGINE  |
                 +----------+-----------+
                            |
                     +------+------+
                     |             |
                     v             v
                   PASS          FAIL
                     |             |
                     |             v
                     |      +-------------+
                     |      | RECOVERY    |
                     |      | + REPLAN    |
                     |      +------+------+ 
                     |             |
                     +-------------+
                            |
                            v
                 +----------------------+
                 | COMPLETION PROOF     |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | OBSERVE / EVALUATE   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | RSI / SELF-DEVELOPMENT|
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | CANDIDATE -> TEST ->  |
                 | SHADOW -> CANARY ->   |
                 | DEPLOY / ROLLBACK     |
                 +----------------------+
```

---

# 3. Antigravity 2.0 Feature Extraction

## 3.1 Agent Command Center

Treat the UI as a control plane, not as the actual runtime.

### Harness translation

```text
Desktop / CLI / Web / Telegram
              |
              v
        Control Plane
              |
              v
         Runtime Kernel
```

The runtime must continue even when the UI is closed.

---

## 3.2 `/boost` -> Deep Reasoning Mode

### Observed Antigravity model

Current documentation describes `/boost` as a three-tier hierarchy:

```text
Orchestrator
    |
    +-- DeepCoder coordinator
    |      |
    |      +-- isolated workers
    |
    +-- DeepInvestigator coordinator
           |
           +-- isolated workers
```

It is designed for difficult concurrency bugs, algorithmic problems and non-trivial refactoring, with independent verification loops.

### Recommended harness implementation

Create a generic `DeepReasoningCampaign` subsystem:

```text
DeepReasoningCampaign
|
+-- ProblemNormalizer
+-- ConstraintExtractor
+-- HypothesisGenerator
+-- StrategyGenerator
+-- Parallel Worker Pool
|   +-- Investigator A
|   +-- Investigator B
|   +-- Coder A
|   +-- Coder B
|   +-- Test Designer
|   +-- Security Critic
|   +-- Performance Critic
+-- Evidence Aggregator
+-- Candidate Comparator
+-- Independent Verifier
+-- Judge
```

### Recommended invocation

```text
/boost <goal>
```

### Internal request

```yaml
mode: deep_reasoning
objective: "Fix the distributed scheduler race condition"
max_parallel_agents: 8
require_independent_verification: true
allow_code_changes: true
require_regression_tests: true
risk_level: medium
```

### Improved harness behavior

Do not simply choose the "best model". Generate multiple solution hypotheses, isolate contexts, execute safely, and compare outcomes using evidence and tests.

---

## 3.3 `/teamwork-preview` -> Long-Horizon Campaign Mode

Use this for hours-to-days work.

Recommended architecture:

```text
Sentinel / Campaign Director
|
+-- Architecture Lead
+-- Research Lead
+-- Implementation Lead
+-- QA Lead
+-- Security Lead
+-- Documentation Lead
+-- DevOps Lead
+-- Independent Verifier
```

Each role gets:

```yaml
role:
mission:
skills:
tools:
model:
permissions:
workspace:
memory_scope:
budget:
acceptance_criteria:
```

### Suggested command

```text
/teamwork <goal>
```

Keep `/teamwork-preview` as a compatibility alias if reproducing Antigravity-style commands.

---

## 3.4 `/goal` -> Autonomous Completion Mode

This should mean:

> Continue executing until the acceptance contract is satisfied, blocked by an explicit external dependency, or the safety policy requires human intervention.

State machine:

```text
NEW
 -> UNDERSTAND
 -> PLAN
 -> EXECUTE
 -> VERIFY
 -> COMPLETE
```

Failure path:

```text
EXECUTE
 -> FAIL
 -> DIAGNOSE
 -> RECOVER
 -> REPLAN
 -> EXECUTE
```

Block path:

```text
BLOCKED
 -> REQUEST_HUMAN
 -> WAIT
 -> RESUME
```

### Example

```text
/goal Fix every failing test in the authentication package and leave the tree green.
```

The harness should not emit "done" merely because the code was edited. It should prove the acceptance criteria.

---

## 3.5 `/plan` -> Reviewable Planning Mode

Pipeline:

```text
Repository / Workspace
        |
        v
Structural analysis
        |
        v
Dependency analysis
        |
        v
Risk analysis
        |
        v
Implementation Plan artifact
        |
        +--> comments / revision
        |
        v
Proceed
        |
        v
Execution
```

Recommended plan artifact:

```yaml
plan_id:
objective:
requirements:
constraints:
assumptions:
files_to_change:
files_to_create:
dependencies:
risks:
test_plan:
rollback_plan:
verification_plan:
acceptance_criteria:
```

---

## 3.6 `/grill-me` -> Requirement Discovery Mode

Do this before code when requirements are ambiguous.

Question categories:

- Desired behavior.
- Non-goals.
- Constraints.
- Performance.
- Security.
- Compatibility.
- Failure handling.
- Data model.
- Rollout strategy.
- Testing.

The output should become a `GoalContract`, not just conversational text.

---

## 3.7 `/learn` -> Learning Distillation Mode

Antigravity's current docs position `/learn` as a way to distill feedback and corrections into persistent Rules or Skills.

Harness implementation:

```text
Session feedback
      |
      v
Correction extractor
      |
      v
Pattern detector
      |
   +--+--+
   |     |
   v     v
 Rule   Skill
   |     |
   +--+--+
      |
      v
Candidate persistence
      |
      v
Evaluation
```

### Critical RSI extension

Never promote every lesson directly into production. Use:

```text
observed lesson
 -> candidate rule/skill
 -> regression evaluation
 -> safety review
 -> promotion
```

---

## 3.8 `/schedule` -> Autonomous Automation Mode

Create a trigger layer:

```text
Cron
Timer
Webhook
GitHub event
File event
System event
Condition event
Monitoring alert
```

Then:

```text
Trigger
 -> create Goal
 -> select Agent Profile
 -> execute
 -> verify
 -> artifact/result
 -> notify
```

Example:

```text
/schedule "0 9 * * 1-5" "Review open pull requests and produce a summary."
```

---

## 3.9 `/browser` -> Specialized Browser Worker

Browser is a capability boundary.

```text
BrowserWorker
|
+-- navigation
+-- DOM inspection
+-- accessibility tree
+-- screenshots
+-- downloads
+-- uploads
+-- UI verification
+-- session/profile
+-- network observation
```

Web content must be considered untrusted input.

---

## 3.10 `/btw` -> Out-of-Band Query Channel

Implement a low-priority side task system:

```text
Main run
   |
   +--> SideQueryQueue
             |
             v
         ephemeral agent
             |
             v
            answer
             |
             +--> UI side panel
```

This must never silently mutate the primary task state.

---

# 4. Full Command and Mode System for the Harness

Recommended public command vocabulary:

```text
/plan                 # investigate + plan
/grill-me             # requirements interview
/boost                # deep reasoning campaign
/goal                 # autonomous goal completion
/teamwork             # long-horizon agent team
/research             # evidence-first research
/browser              # browser worker
/btw                  # background side question
/learn                # distill lessons
/schedule             # schedule recurring/one-time goals
/agents               # agent manager
/tasks                # task manager
/skills               # skill manager
/mcp                  # MCP manager
/permissions          # policy/permission manager
/model                # model router override
/context              # context inspection
/artifact             # artifact review
/runs                 # runtime history
/replay               # deterministic/semantic replay
/rewind               # rollback conversation/run state
/doctor               # system diagnostics
/health               # live health view
/memory               # memory manager
/evolve               # RSI candidate generation/evaluation
/benchmark            # evaluation suite
/policy               # safety/policy manager
```

Aliases can be supported, but the underlying system should be **mode-oriented** rather than command-oriented.

---

# 5. Harness Kernel

The core kernel should remain small.

```text
kernel/
  runtime/
  state/
  events/
  scheduler/
  lifecycle/
  persistence/
  cancellation/
  recovery/
```

## Responsibilities

- Run creation.
- Run state persistence.
- Event dispatch.
- Agent lifecycle.
- Task lifecycle.
- Cancellation.
- Resume after interruption.
- Checkpoint coordination.
- Scheduler integration.
- Runtime metadata.

The kernel should not contain business-specific skills.

---

# 6. Goal Contract

Every user request becomes a structured goal.

```yaml
GoalContract:
  id:
  original_request:
  normalized_goal:
  objectives: []
  requirements: []
  constraints: []
  preferences: []
  assumptions: []
  resources: []
  deadline:
  budget:
  quality_threshold:
  allowed_actions: []
  forbidden_actions: []
  risk_tolerance:
  evidence_requirements: []
  verification_requirements: []
  acceptance_criteria: []
  failure_conditions: []
  stop_conditions: []
```

This becomes the source of truth for long-running execution.

---

# 7. Goal Drift Protection

Maintain three views:

```text
Original Goal
      |
      v
Current Plan
      |
      v
Current Actions
```

A `GoalDriftDetector` asks:

- Does the current task support a current requirement?
- Has an assumption become invalid?
- Has the plan become obsolete?
- Is the agent doing optional work while required work is incomplete?
- Has the agent started optimizing the wrong objective?

Possible actions:

```text
CONTINUE
REPLAN
PAUSE
ESCALATE
STOP
```

---

# 8. Executive Agent

The Executive is the supervisory brain, not the only worker.

Responsibilities:

- Interpret goal.
- Select execution mode.
- Build plan.
- Discover capabilities.
- Select agent types.
- Delegate.
- Monitor.
- Replan.
- Verify.
- Resolve conflicts.
- Decide completion.
- Trigger learning.

Example:

```text
Executive
 |
 +-- Planner
 +-- Agent Factory
 +-- Tool Router
 +-- Model Router
 +-- Verification Controller
 +-- Recovery Controller
 +-- Policy Engine
 +-- Memory Manager
```

---

# 9. Dynamic Agent Factory

The system should be able to create agents at runtime.

## Agent definition

```yaml
AgentProfile:
  id:
  name:
  role:
  mission:
  soul:
  system_instructions:
  skills: []
  tools: []
  mcp_servers: []
  model:
  permissions:
  memory_scope:
  workspace_scope:
  parent_agent:
  children_allowed:
  budget:
  timeout:
  quality_target:
```

## Agent lifecycle

```text
CREATE
 -> INITIALIZE
 -> READY
 -> ASSIGNED
 -> RUNNING
 -> WAITING
 -> VERIFYING
 -> COMPLETED
```

Recovery states:

```text
FAILED
BLOCKED
RECOVERING
REASSIGNED
CANCELLED
ROLLED_BACK
```

---

# 10. Agent Hierarchy

Support multiple organizational patterns.

## Tree

```text
Executive
  +-- Research
  |    +-- Web researcher
  |    +-- Source verifier
  +-- Engineering
       +-- Backend
       +-- Frontend
       +-- QA
```

## Graph

Agents may communicate peer-to-peer when policy allows.

```text
A <-> B
| \   |
|  \  |
v   vv
C <-> D
```

## Company mode

```text
CEO / Executive
|
+-- CTO
|   +-- Architect
|   +-- Developers
|   +-- QA
|   +-- DevOps
|
+-- Research
|   +-- Researchers
|   +-- Analysts
|
+-- Product
+-- Security
+-- Operations
```

---

# 11. Subagent Isolation

A parent should pass a **task packet**, not its whole hidden context.

```yaml
SubagentTaskPacket:
  parent_run_id:
  task_id:
  objective:
  relevant_context:
  artifacts_in_scope:
  constraints:
  allowed_tools:
  permissions:
  output_contract:
  timeout:
```

Benefits:

- Smaller context.
- Fewer accidental instructions.
- Better reproducibility.
- Easier evaluation.
- Lower token cost.

---

# 12. Parallel Execution Fabric

Use a DAG scheduler.

```text
                 Root Goal
                    |
          +---------+---------+
          v         v         v
        Task A    Task B    Task C
          |         |         |
          +----+----+----+----+
               v
             Task D
               |
               v
           Verification
```

Task metadata:

```yaml
task_id:
parent_id:
dependencies: []
owned_by:
priority:
status:
acceptance:
artifacts: []
evidence: []
budget:
timeout:
retry_policy:
```

---

# 13. Planning Engine

Support:

### Fast plan

Goal -> tasks -> execute.

### Deep plan

Goal -> requirements -> research -> strategies -> DAG -> execute.

### Adaptive plan

Plan -> execute -> observe -> replan.

### Recovery plan

Failure -> diagnosis -> alternatives -> recovery execution.

### Long-horizon campaign

Objective -> milestones -> teams -> gates -> repeated verification.

---

# 14. Context Engineering

Separate context into layers:

```text
L0  System policy
L1  Agent identity
L2  Goal contract
L3  Current task
L4  Current plan
L5  Relevant memory
L6  Relevant skill
L7  Tool descriptions
L8  Evidence
L9  Recent observations
L10 Conversation slices
```

Use retrieval instead of dumping all history.

## Context budget manager

```text
Need current information?
      |
      +-- retrieve
      +-- summarize
      +-- compress
      +-- discard irrelevant
```

Track context quality, not just token count.

---

# 15. Memory Architecture

Use multiple stores:

```text
memory/
  working
  short_term
  episodic
  semantic
  procedural
  project
  agent
  organizational
  decision
  failure
  evidence
  environment
```

Recommended pipeline:

```text
Experience
 -> importance scoring
 -> deduplication
 -> extraction
 -> storage
 -> indexing
 -> retrieval
 -> reranking
 -> context injection
```

Do not turn every raw conversation message into permanent memory.

---

# 16. Skill Architecture

Use a common `SKILL.md` contract.

```text
Skill folder
|
+-- SKILL.md
+-- scripts/
+-- references/
+-- examples/
+-- tests/
+```

Suggested metadata:

```yaml
name:
version:
description:
triggers: []
tools: []
permissions: []
dependencies: []
risk_level:
validation:
```

## Skill lifecycle

```text
DISCOVER
 -> INSPECT
 -> INSTALL
 -> VALIDATE
 -> ACTIVATE
 -> USE
 -> MEASURE
 -> IMPROVE
 -> VERSION
```

Skill sources:

```text
Built-in
Workspace
Project
User
Community
Agent-generated
RSI-improved
```

---

# 17. Tool Registry

Every tool should expose metadata.

```yaml
Tool:
  id:
  name:
  description:
  input_schema:
  output_schema:
  permissions:
  cost:
  latency:
  reliability:
  risk:
  environments:
  requires_network:
  supports_dry_run:
```

The Tool Router should select tools based on:

```text
Capability
+ policy
+ reliability
+ cost
+ latency
+ environment
+ current task
```

---

# 18. MCP Layer

Treat MCP as a first-class capability provider.

```text
MCP Registry
|
+-- discovery
+-- authentication
+-- health checks
+-- tool filtering
+-- resource discovery
+-- permissions
+-- lifecycle
```

Every MCP server should have:

```text
availability
latency
error rate
version
health
permissions
```

---

# 19. Browser Worker

Provide both:

```text
API/web fetch
```

and:

```text
Visual browser/computer use
```

Policy:

```text
Untrusted webpage
 -> isolate
 -> parse/extract
 -> treat embedded instructions as untrusted
 -> enforce higher-level policy
```

---

# 20. Execution Sandbox

Capabilities:

```text
filesystem
terminal
python
node
browser
git
processes
network
containers
```

Permission model:

```text
read
write
execute
network
install
delete
privileged
```

Use a default-deny strategy for dangerous capabilities.

---

# 21. Permission Engine

Suggested evaluation order:

```text
DENY
  > ASK
  > ALLOW
```

Permission resource examples:

```text
filesystem.read(path)
filesystem.write(path)
terminal.execute(command)
network.connect(host)
browser.navigate(url)
git.push(repo)
cloud.deploy(target)
database.write(schema)
```

This follows the useful fine-grained pattern documented for Antigravity CLI while keeping the exact policy language under your control.

---

# 22. Trust Hierarchy

Assign trust classes:

```text
SYSTEM POLICY
DEVELOPER POLICY
USER GOAL
TRUSTED TOOL OUTPUT
PROJECT CONTENT
EXTERNAL FILE
WEB CONTENT
EXTERNAL AGENT MESSAGE
```

Lower-trust content must never override higher-trust policy.

---

# 23. JSON Hooks / Lifecycle Hooks

Use hooks as middleware around agent actions.

```text
REQUEST
  |
  v
PRE-HOOK
  |
  v
POLICY CHECK
  |
  v
TOOL / MODEL / AGENT ACTION
  |
  v
POST-HOOK
  |
  v
VERIFY
```

Recommended hook classes:

```text
on_run_start
on_plan_created
on_agent_spawn
before_model
before_tool
after_tool
before_write
after_write
before_terminal
after_terminal
before_network
after_network
before_commit
before_deploy
on_failure
on_recovery
on_verification
on_run_complete
```

RSI hook examples:

```text
on_run_complete -> collect metrics
on_failure -> record failure pattern
on_skill_use -> collect effectiveness
on_verification -> record quality
on_candidate_promote -> require policy gate
```

---

# 24. Event-Sourced Runtime

Everything important becomes an event.

```text
RUN_CREATED
RUN_STARTED
GOAL_NORMALIZED
PLAN_CREATED
TASK_CREATED
AGENT_SPAWNED
SKILL_LOADED
MEMORY_READ
MEMORY_WRITTEN
TOOL_CALLED
TOOL_RETURNED
ARTIFACT_CREATED
CHECKPOINT_CREATED
ERROR_DETECTED
RECOVERY_STARTED
RECOVERY_COMPLETED
VERIFICATION_STARTED
VERIFICATION_PASSED
VERIFICATION_FAILED
AGENT_COMPLETED
RUN_COMPLETED
RUN_FAILED
CANDIDATE_CREATED
CANDIDATE_EVALUATED
CANDIDATE_PROMOTED
CANDIDATE_ROLLED_BACK
```

Store event metadata such as:

```yaml
event_id:
timestamp:
run_id:
agent_id:
task_id:
parent_event:
payload_hash:
policy_context:
model:
tool:
```

This becomes the foundation for replay, diagnostics and RSI.

---

# 25. State Machine

Recommended run states:

```text
NEW
INTAKE
PLANNING
RESEARCHING
EXECUTING
WAITING
BLOCKED
VERIFYING
RECOVERING
COMPLETED
FAILED
CANCELLED
ROLLED_BACK
```

A state transition must be explicit and event-backed.

---

# 26. Checkpoints

Checkpoint categories:

```text
workspace
filesystem
git
configuration
agent state
memory
skill version
database state
deployment state
```

Pattern:

```text
checkpoint
 -> risky operation
 -> verify
 -> commit checkpoint
```

Failure:

```text
rollback
 -> diagnose
 -> alternative strategy
```

---

# 27. Replay and Time-Travel Debugging

Support:

```text
record
replay
fork
compare
inspect
```

Example:

```text
Run #18271
|
+-- T+00:00 PLAN_CREATED
+-- T+00:15 TOOL_CALLED
+-- T+00:32 AGENT_SPAWNED
+-- T+01:03 TOOL_FAILED
+-- T+01:05 RECOVERY_STARTED
+-- T+01:30 VERIFICATION_PASSED
+-- T+01:35 COMPLETE
```

Allow:

```text
/replay run-18271
/replay run-18271 --from event-42
/fork run-18271 --strategy candidate-b
```

Do not promise bit-for-bit determinism for probabilistic models; support **semantic replay** using recorded inputs, tool results and state snapshots where exact determinism is impossible.

---

# 28. Artifact System

Artifacts are first-class runtime objects.

Types:

```text
report
plan
code
patch
image
video
dataset
presentation
web app
deployment
benchmark
verification report
```

Metadata:

```yaml
artifact_id:
type:
version:
created_by:
created_at:
source_task:
dependencies:
evidence:
verification:
location:
```

---

# 29. Evidence System

Create an evidence graph.

```text
Requirement
   |
   v
Task
   |
   v
Action
   |
   v
Evidence
   |
   v
Verification
   |
   v
Conclusion
```

Claim record:

```yaml
claim:
source:
evidence:
timestamp:
agent:
confidence:
verification_status:
```

For research, evidence must be linked to the source rather than merely copied into memory.

---

# 30. Verification Engine

Never use a single "looks good" check.

```text
Verification
|
+-- requirement verifier
+-- factual verifier
+-- code verifier
+-- test verifier
+-- artifact verifier
+-- source verifier
+-- security verifier
+-- regression verifier
+-- integration verifier
+-- acceptance verifier
```

### Final completion gate

```text
ALL mandatory acceptance criteria satisfied?
    |
   yes -> COMPLETE
    |
   no  -> RECOVER / REPLAN / ESCALATE
```

---

# 31. Independent Critics

Separate production agents from approval authority where practical.

```text
Main Worker
    |
    v
Candidate Result
    |
 +--+--+--+--+
 |  |  |  |  |
 v  v  v  v  v
Code Fact Goal Sec Perf
Critic Critic Critic Critic Critic
    |  |  |  |  |
    +--+--+--+--+
           |
           v
         Judge
```

The reviewer should have an independent context and should be able to reject the result.

---

# 32. Failure Detection

Detect:

- Repeated command failure.
- Repeated identical tool calls.
- No-progress windows.
- Excessive token growth.
- Contradictory decisions.
- Tool misuse.
- Goal drift.
- Agent deadlock.
- Subagent deadlock.
- Authentication errors.
- Network outage.
- Environment mismatch.
- Regression after change.

Use a `FailureClassifier`:

```text
TRANSIENT
DEPENDENCY
AUTH
NETWORK
LOGIC
TOOL
MODEL
PLANNING
STATE
SECURITY
RESOURCE
UNKNOWN
```

---

# 33. Recovery Engine

Do not implement recovery as "retry three times".

```text
Failure
  |
  v
Classify
  |
  +-- transient -> retry
  +-- tool issue -> alternate tool
  +-- model issue -> alternate model
  +-- planning issue -> replan
  +-- environment issue -> repair environment
  +-- bad change -> rollback
  +-- permission issue -> request approval
  +-- unknown -> isolate + escalate
```

Recommended recovery ladder:

```text
1. Local retry
2. Parameter correction
3. Alternate tool
4. Alternate skill
5. Alternate model
6. New subagent
7. Replan
8. Rollback
9. Human escalation
```

---

# 34. Model Router

Use a provider-agnostic model abstraction.

```text
Model Router
|
+-- fast model
+-- reasoning model
+-- coding model
+-- vision model
+-- research model
+-- local model
```

Routing inputs:

```text
task type
complexity
latency target
budget
quality target
availability
context size
privacy constraints
```

Providers may include any configured APIs and local runtimes. The architecture should not hard-code one vendor.

---

# 35. Model Failover

Explicitly record failover.

```text
Primary model fails
      |
      v
Failure classifier
      |
      v
Alternate provider/model
      |
      v
Continue
```

Never silently hide provider changes from observability.

---

# 36. Capability Discovery

Before planning, inspect the environment.

```text
Machine
|
+-- OS
+-- CPU
+-- RAM
+-- GPU
+-- Disk
+-- Network
+-- Git
+-- Docker
+-- Python
+-- Node
+-- Browsers
+-- Ollama/local models
+-- MCP servers
+-- credentials/providers
```

Output:

```yaml
capabilities:
  browser: true
  docker: true
  git: true
  gpu:
    available: false
  local_llm: true
  internet: true
```

The planner should adapt to this state.

---

# 37. Environment Health Manager

Every component should expose:

```text
health
version
availability
latency
error_rate
dependencies
```

Example:

```text
Browser service: DEGRADED
MCP database: HEALTHY
Docker: UNAVAILABLE
```

The planner should avoid assigning tasks to unavailable capabilities.

---

# 38. Self-Diagnostics

Implement:

```text
/doctor
```

Checks:

```text
Kernel
Persistence
Scheduler
Models
Tools
MCP
Browser
Sandbox
Memory
Storage
Network
Plugins
Skills
Permissions
Secrets
```

Return:

```text
PASS
WARN
FAIL
```

Each failure should include an actionable repair path.

---

# 39. Resource Manager

Track:

```text
CPU
RAM
GPU
Disk
Network
Tokens
API calls
Cost
Runtime
Concurrency
```

Budget layers:

```text
per-tool
per-agent
per-task
per-project
per-run
global
```

---

# 40. Dynamic Concurrency

Do not maximize agent count.

Use:

```text
parallelism = f(
  task decomposition,
  independent branches,
  available resources,
  deadline,
  budget,
  failure risk
)
```

The system should prefer fewer agents when parallelism adds no value.

---

# 41. Scheduler and Trigger Engine

Unified event-to-goal architecture:

```text
Trigger
  |
  v
Goal factory
  |
  v
Policy check
  |
  v
Agent selection
  |
  v
Run
  |
  v
Verification
  |
  v
Notification
```

Trigger classes:

```text
cron
timer
webhook
GitHub event
file change
database event
system event
monitor alert
agent event
```

---

# 42. Notification System

Channels:

```text
desktop
CLI
Telegram
Discord
Slack
email
web
mobile
```

Events worth notifying:

```text
run started
run blocked
approval requested
security alert
major discovery
failure
recovery
completed
rollback
RSI candidate ready
```

---

# 43. Project Model

A Project should be broader than a single repository.

```text
Project
|
+-- repositories
+-- folders
+-- documents
+-- agents
+-- skills
+-- MCP configuration
+-- memory
+-- evidence
+-- artifacts
+-- policies
+-- schedules
+-- benchmarks
```

Use scoped permissions per project.

---

# 44. Project File Structure

Recommended:

```text
project/
|
+-- PROJECT.md
+-- GOALS.md
+-- ARCHITECTURE.md
+-- DECISIONS.md
+-- TASKS.md
+-- POLICIES.md
+-- agents/
+-- skills/
+-- memory/
+-- evidence/
+-- artifacts/
+-- benchmarks/
+-- runs/
+-- hooks/
+-- config/
```

---

# 45. Decision Log

Record major decisions:

```yaml
decision_id:
question:
options: []
selected_option:
rationale:
evidence: []
agent:
timestamp:
confidence:
reversible:
```

This is valuable for both auditability and RSI.

---

# 46. Assumption Registry

```yaml
assumption_id:
statement:
source:
created_at:
status: active | validated | invalidated
impact:
dependent_tasks: []
```

When invalidated:

```text
Assumption invalidated
 -> find dependent plans/tasks
 -> invalidate affected decisions
 -> replan
```

---

# 47. Contradiction Detection

Compare information from:

```text
memory
project files
web research
tool results
agents
user instructions
```

When conflicts appear:

```text
CONTRADICTION
   |
   v
Evidence comparison
   |
   v
Resolve / request clarification / retain uncertainty
```

Do not overwrite historical evidence merely to make data consistent.

---

# 48. Knowledge Architecture

Use hybrid retrieval:

```text
Vector search
+
Keyword/BM25
+
Metadata filtering
+
Graph traversal
+
Recency
+
Importance
```

Data flow:

```text
Research
 -> source extraction
 -> claims
 -> evidence
 -> knowledge
 -> memory
 -> graph
```

---

# 49. Knowledge Graph

Suggested entities:

```text
Project
Repository
File
Agent
Skill
Tool
Task
Decision
Artifact
Evidence
Source
Requirement
Environment
Model
Run
Failure
```

Relations:

```text
agent OWNS task
task DEPENDS_ON task
artifact PRODUCED_BY agent
artifact VERIFIED_BY verifier
decision SUPPORTED_BY evidence
skill USED_BY agent
failure AFFECTED task
```

---

# 50. Agent Performance / Reputation

Measure agents using data, not personality claims.

```text
successful tasks
failed tasks
verification pass rate
rework rate
average cost
latency
tool efficiency
recovery success
human correction count
security incidents
```

Use it for delegation and scheduling, not as a simplistic single-score ranking.

---

# 51. Long-Running Runtime

A run must survive:

```text
UI close
process restart
machine reboot
network outage
model outage
agent crash
```

Persistence pattern:

```text
Event store
+
Run state snapshot
+
Task state
+
Agent state
+
Artifact store
```

Startup recovery:

```text
load unfinished runs
 -> validate state
 -> inspect environment
 -> inspect dependencies
 -> resume / recover / pause
```

---

# 52. Human-in-the-Loop Gates

Suggested risk bands:

```text
LOW
 -> automatic

MEDIUM
 -> notify or policy-dependent

HIGH
 -> approval

CRITICAL
 -> mandatory explicit approval
```

Potential approval actions:

```text
production deploy
credential use
bulk deletion
financial action
privileged command
external message
irreversible migration
```

---

# 53. Secrets Architecture

Never inject raw API keys into ordinary prompts.

Use:

```text
OS secret store
Encrypted vault
Environment secrets
OAuth tokens
Short-lived credentials
Per-project credentials
Per-tool credentials
```

Agents request capability, not the raw secret whenever possible.

---

# 54. Prompt Injection Defense

Treat external content as data, not instructions.

```text
Web / file / email
      |
      v
Untrusted input boundary
      |
      v
Extraction / classification
      |
      v
Policy enforcement
      |
      v
Agent
```

This is mandatory for autonomous browser/research agents.

---

# 55. Git-Native Coding

Recommended development flow:

```text
create branch
 -> inspect
 -> modify
 -> test
 -> lint
 -> security scan
 -> review
 -> commit
 -> optional PR
```

Use checkpoints and rollback around risky transformations.

---

# 56. Deployment Manager

Support pluggable targets:

```text
local
Docker
VPS
cloud
Vercel/other PaaS
```

Pipeline:

```text
build
 -> test
 -> package
 -> deploy
 -> smoke test
 -> monitor
 -> rollback if regression
```

Deployment permissions must be explicit.

---

# 57. Simulation / Dry-Run Mode

Before real-world action:

```text
PLAN
 -> SIMULATE
 -> VERIFY
 -> REAL ACTION
```

Examples:

```text
infra changes
production deploy
data migrations
system administration
bulk file operations
robotics commands
```

---

# 58. Completion Proof

The final runtime record should include:

```yaml
goal:
requirements:
requirements_completed:
requirements_failed:
evidence:
tests:
artifacts:
remaining_risks:
verification_status:
rollback_point:
run_id:
```

Do not treat the natural-language phrase "done" as proof.

---

# 59. Research Engine

Pipeline:

```text
Question
 -> plan
 -> search
 -> fetch
 -> parse
 -> deduplicate
 -> rank
 -> cross-check
 -> extract claims
 -> attach evidence
 -> synthesize
 -> verify
```

Recommended research agent roles:

```text
Search researcher
Source verifier
Contradiction checker
Domain analyst
Synthesizer
Citation checker
```

---

# 60. Browser Validation Loop

For web applications:

```text
build
 -> launch
 -> browser navigate
 -> inspect DOM
 -> screenshot
 -> interact
 -> test
 -> record evidence
```

The browser worker should feed structured observations back into the task rather than flooding the parent context with raw page content.

---

# 61. Observability

Record:

```text
run
agent
model
prompt metadata
tool calls
tool results
memory retrievals
skill activation
subagents
handoffs
errors
latency
cost
verification
artifacts
```

Dashboard views:

```text
live run
agent tree
task DAG
tool activity
memory activity
cost
errors
verification
artifacts
```

---

# 62. Trajectory Evaluation

Evaluate the path, not just the final answer.

Metrics:

```text
tool selection quality
planning quality
delegation quality
recovery quality
evidence quality
policy compliance
wasted work
latency
cost
final quality
```

Store benchmark trajectories to measure system evolution.

---

# 63. Evaluation Harness

Permanent benchmark suites:

```text
planning
research
coding
browser
memory
tool use
multi-agent
recovery
security
long-horizon
artifact quality
```

Each harness release should run regression evaluation.

---

# 64. RSI / Recursive Self-Development Engine

This is the main extension beyond standard Antigravity-style orchestration.

## Core loop

```text
                    +---------+
                    | OBSERVE |
                    +----+----+
                         |
                         v
                    +---------+
                    | MEASURE |
                    +----+----+
                         |
                         v
                    +---------+
                    | DIAGNOSE|
                    +----+----+
                         |
                         v
                 +----------------+
                 | GENERATE      |
                 | CANDIDATES    |
                 +-------+--------+
                         |
                         v
                 +----------------+
                 | EXPERIMENT     |
                 | IN SANDBOX     |
                 +-------+--------+
                         |
                         v
                 +----------------+
                 | EVALUATE       |
                 +-------+--------+
                         |
                 +-------+-------+
                 |               |
                PASS            FAIL
                 |               |
                 v               v
          SHADOW/CANARY       DISCARD
                 |
                 v
             PROMOTE
                 |
                 v
          MONITOR / ROLLBACK
```

## Improvement targets

```text
system prompt
skill
agent profile
planning strategy
tool routing
memory retrieval
context compression
model routing
verification strategy
recovery policy
agent topology
runtime code
configuration
```

---

# 65. RSI Candidate Model

Never edit the only production copy.

```text
Production baseline
        |
        +--> Candidate branch A
        +--> Candidate branch B
        +--> Candidate branch C
```

Candidate metadata:

```yaml
candidate_id:
base_version:
change_type:
change_set:
hypothesis:
expected_gain:
risk:
benchmark_suite:
created_by:
created_at:
```

---

# 66. RSI Experiment Engine

Use A/B or controlled evaluation.

```text
Baseline
   |
   +---------> benchmark
   |
Candidate
   |
   +---------> benchmark
   |
Compare
```

Metrics:

```text
quality
success
cost
latency
reliability
security
rework
```

Do not promote based on one favorable run.

---

# 67. Shadow Mode

Run a candidate against the same task stream without granting production side effects.

```text
Real Task
   |
 +--+----------------+
 |                   |
 v                   v
Production       Candidate
 |                   |
 +--------+----------+
          |
          v
      Comparator
```

This is one of the safest ways to validate self-improvement.

---

# 68. Canary Deployment

Promotion stages:

```text
candidate
 -> lab benchmark
 -> replay benchmark
 -> shadow
 -> canary
 -> production
```

Canary metrics should be compared with baseline before wider rollout.

---

# 69. Automatic Rollback

Rollback triggers:

```text
quality regression
verification failure
cost spike
latency spike
error spike
security policy violation
unexpected side effects
```

Rollback should return the system to the last known-good immutable version.

---

# 70. RSI Safety Model

The RSI subsystem must not be fully trusted simply because it is the subsystem doing the improvement.

Create independent gates:

```text
Candidate Generator
      |
      v
Sandbox Evaluator
      |
      v
Independent Reviewer
      |
      v
Policy Engine
      |
      v
Promotion Controller
```

High-risk changes require human approval.

---

# 71. Self-Learning Taxonomy

Separate lessons into:

```text
FACT
RULE
PROCEDURE
SKILL
POLICY
CONFIGURATION
CODE CHANGE
```

Examples:

```text
FACT:
API endpoint is X.

RULE:
Always validate schema before migration.

PROCEDURE:
Use this 7-step deployment sequence.

SKILL:
Database migration workflow.

POLICY:
Production deletion requires approval.

CONFIG:
Use model Y for browser tasks.

CODE CHANGE:
Improve retry state machine.
```

This prevents every observed pattern from becoming uncontrolled code mutation.

---

# 72. Antigravity `/learn` -> Better RSI `/learn`

Recommended sequence:

```text
Session
 -> corrections
 -> failures
 -> successful trajectories
 -> pattern extraction
 -> candidate lesson
 -> classify
 -> evaluate
 -> promote to Rule/Skill/Policy/Config
```

Only after repeated evidence should the engine consider changing runtime code.

---

# 73. Recursive Capability Loop

A particularly strong design is:

```text
Need capability
     |
     v
Skill discovery
     |
     v
Skill installation
     |
     v
Task execution
     |
     v
Skill effectiveness measurement
     |
     v
Skill improvement
     |
     v
Benchmark
     |
     v
New skill version
```

The harness therefore grows by improving reusable procedures, not just by changing the model.

---

# 74. Agent-to-Agent Communication Bus

Internal bus:

```text
Agent A
  |
  v
Message Bus
  |
  +--> Agent B
  +--> Agent C
  +--> Supervisor
  +--> Monitor
```

Message schema:

```yaml
message_id:
from:
to:
type:
request:
context:
evidence:
artifacts:
priority:
expires_at:
```

Message types:

```text
TASK_REQUEST
TASK_ACCEPTED
PROGRESS
RESULT
EVIDENCE
WARNING
FAILURE
BLOCKED
ESCALATION
CANCEL
```

---

# 75. Agent Kanban

Recommended columns:

```text
BACKLOG
PLANNED
READY
RUNNING
WAITING
BLOCKED
REVIEW
DONE
FAILED
```

Each card maps to an actual persistent task/run.

---

# 76. Live Agent Dashboard

Display:

```text
agent
role
status
current task
current tool
progress
runtime
cost
tokens
errors
parent
children
```

Use the agent tree and task DAG as the primary visualization.

---

# 77. Presentation Memory

For long-term human interaction, keep a curated presentation layer separate from raw memory.

```text
Raw experiences
      |
      v
Consolidation
      |
      v
Curated facts/rules
      |
      v
Human-readable project memory
```

Useful files:

```text
MEMORY.md
USER.md
PROJECT.md
DECISIONS.md
LESSONS.md
```

The exact memory implementation can be database-backed while maintaining these human-readable summaries.

---

# 78. Autonomous Company Mode

Support persistent organizational roles.

```text
Executive
|
+-- Strategy
+-- Research
+-- Engineering
+-- QA
+-- Security
+-- Marketing
+-- Operations
```

Every role should have:

```text
mission
KPIs
tools
budget
authority
memory
reports
handoff rules
```

This is a natural extension of `/teamwork` and dynamic agent creation.

---

# 79. Goal-to-Business Workflow Example

User says:

```text
Build and launch a SaaS landing page for product X.
```

Harness:

```text
1. Normalize goal
2. /grill-me if ambiguous
3. /plan architecture
4. Create research + design + engineering agents
5. Research market and requirements
6. Generate implementation plan
7. Spawn parallel workers
8. Build site
9. Browser-test UI
10. Run unit/integration tests
11. Security review
12. Generate artifact report
13. Deploy only if policy permits
14. Smoke test deployment
15. Monitor
16. Record lessons
17. Update reusable skills when justified
```

---

# 80. Deep-Reasoning Example

User:

```text
/boost Optimize a slow database query while preserving correctness.
```

Execution:

```text
Orchestrator
  |
  +-- Investigator A: execution plan analysis
  +-- Investigator B: schema/index analysis
  +-- Investigator C: workload analysis
  +-- Coder A: optimization candidate
  +-- Coder B: alternate optimization
  +-- Test Agent: correctness/performance benchmark
  |
  v
Candidate comparison
  |
  v
Independent verifier
  |
  v
Patch + benchmark + evidence
```

---

# 81. Long-Horizon Example

User:

```text
/teamwork Migrate a monolithic application into services while preserving functionality.
```

Execution:

```text
Campaign Director
|
+-- Discovery milestone
+-- Architecture milestone
+-- Migration milestone
+-- Test parity milestone
+-- Performance milestone
+-- Security milestone
+-- Deployment milestone
```

Each milestone has:

```text
entry criteria
exit criteria
deliverables
owner agents
verification gate
rollback plan
```

---

# 82. Recommended Repo Architecture

For `agent-workspace-desktop`, add a harness layer around the existing Alpha capability boundary.

```text
src/
|
+-- kernel/
|   +-- runtime/
|   +-- state/
|   +-- events/
|   +-- lifecycle/
|   +-- persistence/
|   +-- scheduler/
|
+-- executive/
|   +-- executive-agent/
|   +-- command-router/
|   +-- mode-manager/
|
+-- planning/
|   +-- goal-contract/
|   +-- planner/
|   +-- dag/
|   +-- replanner/
|   +-- drift-detector/
|
+-- agents/
|   +-- registry/
|   +-- factory/
|   +-- profiles/
|   +-- hierarchy/
|   +-- communication/
|   +-- health/
|
+-- orchestration/
|   +-- boost/
|   +-- teamwork/
|   +-- goal-runner/
|   +-- campaign-manager/
|   +-- delegation/
|
+-- skills/
|   +-- registry/
|   +-- discovery/
|   +-- loader/
|   +-- validation/
|   +-- evolution/
|
+-- tools/
|   +-- registry/
|   +-- router/
|   +-- policy/
|   +-- mcp/
|
+-- execution/
|   +-- sandbox/
|   +-- terminal/
|   +-- filesystem/
|   +-- browser/
|   +-- process/
|   +-- git/
|
+-- memory/
|   +-- working/
|   +-- episodic/
|   +-- semantic/
|   +-- procedural/
|   +-- project/
|   +-- graph/
|   +-- consolidation/
|
+-- knowledge/
|   +-- research/
|   +-- retrieval/
|   +-- evidence/
|   +-- graph/
|
+-- verification/
|   +-- requirements/
|   +-- tests/
|   +-- critics/
|   +-- evidence/
|   +-- acceptance/
|   +-- regression/
|
+-- resilience/
|   +-- retries/
|   +-- recovery/
|   +-- checkpoints/
|   +-- rollback/
|   +-- failover/
|
+-- observability/
|   +-- events/
|   +-- traces/
|   +-- metrics/
|   +-- replay/
|   +-- audit/
|
+-- security/
|   +-- policy/
|   +-- permissions/
|   +-- secrets/
|   +-- injection-defense/
|   +-- trust/
|
+-- evaluation/
|   +-- benchmarks/
|   +-- trajectory/
|   +-- experiments/
|   +-- regression/
|
+-- rsi/
|   +-- observer/
|   +-- diagnosis/
|   +-- candidate-manager/
|   +-- experimenter/
|   +-- evaluator/
|   +-- promotion/
|   +-- canary/
|   +-- rollback/
|
+-- integrations/
|   +-- telegram/
|   +-- github/
|   +-- slack/
|   +-- discord/
|   +-- api/
|
+-- ui/
    +-- agents/
    +-- tasks/
    +-- runs/
    +-- traces/
    +-- artifacts/
    +-- memory/
    +-- skills/
    +-- rsi/
    +-- settings/
```

Keep actual Alpha internals isolated where possible. New functionality should preferably sit above, beside, or behind stable interfaces rather than creating a giant fork of the entire core.

---

# 83. Recommended Runtime Interfaces

## AgentRuntime

```ts
interface AgentRuntime {
  startRun(input: RunInput): Promise<RunHandle>;
  resumeRun(runId: string): Promise<RunHandle>;
  cancelRun(runId: string): Promise<void>;
  getState(runId: string): Promise<RunState>;
}
```

## AgentFactory

```ts
interface AgentFactory {
  create(profile: AgentProfile, task: AgentTask): Promise<AgentHandle>;
  spawnTransient(spec: TransientAgentSpec): Promise<AgentHandle>;
  stop(agentId: string): Promise<void>;
}
```

## SkillRegistry

```ts
interface SkillRegistry {
  discover(query: SkillQuery): Promise<SkillManifest[]>;
  install(skill: SkillSource): Promise<SkillHandle>;
  validate(skillId: string): Promise<ValidationResult>;
}
```

## VerificationEngine

```ts
interface VerificationEngine {
  verify(input: VerificationRequest): Promise<VerificationResult>;
}
```

## RSIEngine

```ts
interface RSIEngine {
  observe(runId: string): Promise<ObservationBundle>;
  propose(input: ImprovementRequest): Promise<Candidate[]>;
  evaluate(candidateId: string): Promise<EvaluationResult>;
  promote(candidateId: string): Promise<PromotionResult>;
  rollback(versionId: string): Promise<void>;
}
```

---

# 84. Suggested Database Model

A relational store is useful for operational state.

Core tables/entities:

```text
runs
run_events
run_snapshots
projects
agents
agent_profiles
agent_relationships
tasks
task_dependencies
skills
skill_versions
tools
mcp_servers
artifacts
evidence
claims
decisions
assumptions
failures
recoveries
benchmarks
benchmark_runs
models
provider_health
permissions
policies
schedules
triggers
rsi_candidates
rsi_experiments
rsi_promotions
```

Use vector/graph systems as supporting stores, not as the only source of truth for operational state.

---

# 85. Event Schema Example

```json
{
  "event_id": "evt_01",
  "event_type": "TOOL_CALLED",
  "run_id": "run_1001",
  "agent_id": "agent_research_2",
  "task_id": "task_42",
  "timestamp": "2026-09-16T00:00:00Z",
  "payload": {
    "tool": "web.search",
    "query": "example"
  },
  "policy": {
    "decision": "allow",
    "reason": "read-only web search"
  }
}
```

---

# 86. Agent Task Packet Example

```yaml
agent_task:
  task_id: task_42
  objective: "Investigate failing database test"
  acceptance:
    - "Root cause identified"
    - "Repro case documented"
    - "Fix proposal validated"
  context:
    files:
      - src/db/query.ts
      - tests/db/query.test.ts
  tools:
    - filesystem.read
    - terminal.execute
    - git.diff
  permissions:
    filesystem.write: false
    terminal.execute: sandboxed
  output:
    type: verification_report
```

---

# 87. Recommended Agent Profiles

Start with a small set.

```text
Executive
Planner
Researcher
Deep Investigator
Coder
Browser Worker
Test Engineer
Security Reviewer
Performance Analyst
Documentation Writer
Verifier
Recovery Agent
RSI Observer
RSI Experimenter
```

Then allow dynamic specialization.

---

# 88. Recommended Built-In Skills

```text
deep-research
repository-analysis
coding
refactoring
unit-testing
integration-testing
browser-validation
git-workflow
debugging
security-review
performance-analysis
documentation
release-management
database-migration
api-testing
data-analysis
artifact-generation
incident-recovery
rsi-analysis
```

---

# 89. Mode Selection Matrix

| User need | Harness mode |
|---|---|
| Ambiguous feature request | `/grill-me` |
| Need reviewable design | `/plan` |
| Difficult bug/algorithm | `/boost` |
| Large migration/campaign | `/teamwork` |
| Fully autonomous bounded objective | `/goal` |
| Web research/UI validation | `/browser` |
| Reusable lesson extraction | `/learn` |
| Recurring automation | `/schedule` |
| Quick side query | `/btw` |
| Research-heavy deliverable | `/research` |
| System self-improvement | `/evolve` |

---

# 90. Mode Escalation Logic

Do not make the user manually select every mode.

The Executive should estimate:

```text
ambiguity
complexity
parallelism
risk
time horizon
research intensity
verification burden
```

Then choose a mode.

Example:

```text
Simple task
 -> normal agent

Ambiguous task
 -> grill/plan

Complex bug
 -> boost

Large campaign
 -> teamwork

Must finish autonomously
 -> goal

Recurring task
 -> schedule
```

Still expose manual commands for expert users.

---

# 91. Automatic Mode Composition

The best design is to compose modes.

Example:

```text
User asks for large system rewrite
      |
      v
/grill-me
      |
      v
/plan
      |
      v
/teamwork
      |
      v
/boost for hard subproblems
      |
      v
/goal for individual milestones
      |
      v
/browser for UI verification
      |
      v
/learn for justified lessons
```

These should be internal orchestration primitives, not isolated commands.

---

# 92. Supervisor Policy Loop

Every execution cycle:

```text
OBSERVE
  |
  v
UPDATE STATE
  |
  v
CHECK GOAL
  |
  v
CHECK BLOCKERS
  |
  v
CHECK RESOURCE BUDGET
  |
  v
CHECK POLICY
  |
  v
SELECT NEXT ACTION
  |
  v
EXECUTE
```

This is the runtime heartbeat.

---

# 93. Stop Conditions

Explicit stop conditions prevent runaway autonomy.

```text
goal satisfied
mandatory requirement impossible
budget exhausted
deadline reached
security boundary hit
human approval required
system unavailable
no safe recovery exists
```

Optional work should not prevent completion once acceptance criteria are met.

---

# 94. No-Silent-Fallback Rule

The harness should distinguish:

```text
fallback
```

from:

```text
silent behavior change
```

Every fallback should create an event:

```text
MODEL_FAILOVER
TOOL_FAILOVER
AGENT_REASSIGNED
STRATEGY_CHANGED
```

The user-facing summary can state that a fallback occurred without exposing unnecessary internal detail.

---

# 95. Safety Boundaries for Autonomous Code Changes

Agent code mutation should use:

```text
candidate branch
checkpoint
sandbox test
benchmark
review
promotion
```

Avoid:

```text
agent modifies itself directly in production
```

For RSI:

```text
Production
   |
   +--> Candidate branch
          |
          +--> Test
          +--> Benchmark
          +--> Security review
          +--> Human gate if high risk
          |
          v
       Promotion
```

---

# 96. How Antigravity-Inspired Features Map to RSI

| Antigravity concept | Harness extension |
|---|---|
| `/boost` | Deep experiment/candidate generation engine |
| `/teamwork` | Long-horizon improvement campaigns |
| `/goal` | Autonomous completion contract |
| `/plan` | Change proposal / architecture plan |
| `/grill-me` | Requirement extraction |
| `/learn` | Lesson -> Rule/Skill candidate pipeline |
| Skills | Procedural memory |
| Dynamic subagents | Specialized experiment workers |
| MCP | Capability expansion |
| JSON Hooks | Observation/policy insertion points |
| Artifacts | Experiment evidence/results |
| Scheduling | Continuous improvement jobs |
| Permissions | RSI safety gate |
| Projects | Long-term improvement domain |
| Browser worker | External research/evidence acquisition |

---

# 97. Continuous Self-Development Example

Suppose the harness repeatedly fails browser verification.

```text
1. Observe failed browser runs.
2. Aggregate failure patterns.
3. Detect dynamic-page timing as a likely root cause.
4. Generate candidate skill changes.
5. Create a sandbox experiment.
6. Run historical browser benchmark set.
7. Compare against baseline.
8. Run security/policy checks.
9. Shadow candidate.
10. Canary candidate.
11. Promote or discard.
12. Record the lesson.
```

The important property is **evidence-backed improvement**, not unrestricted self-modification.

---

# 98. Continuous Research Example

Scheduled job:

```text
Every day at 09:00:
  research recent agent-harness developments
  cross-check primary sources
  extract new techniques
  compare against current capability map
  create candidate enhancement report
  avoid changing production automatically
```

Then:

```text
Candidate Enhancement
 -> review
 -> benchmark
 -> implementation
```

This separates discovery from deployment.

---

# 99. Recommended UI Sections

For the desktop application:

```text
HOME
|
+-- Active Runs
+-- Agents
+-- Tasks
+-- Projects
+-- Artifacts
+-- Memory
+-- Skills
+-- MCP
+-- Schedules
+-- Benchmarks
+-- Traces
+-- RSI Lab
+-- Settings
```

## RSI Lab

Display:

```text
Current version
Candidates
Experiments
Benchmark delta
Shadow runs
Canary status
Promotion history
Rollback controls
```

---

# 100. Build Order

Do not implement the entire architecture in one pass.

## Phase 1 — Runtime foundation

Implement:

```text
runtime state
persistent runs
event bus
task DAG
cancellation
resume
basic scheduler
```

## Phase 2 — Antigravity-style orchestration

Implement:

```text
command router
/plan
/goal
/boost
/teamwork
/btw
```

## Phase 3 — Capability fabric

Implement:

```text
agent factory
skill registry
tool registry
MCP
browser worker
sandbox
permissions
```

## Phase 4 — Verification and resilience

Implement:

```text
verification engine
critics
failure classifier
recovery
checkpoints
rollback
```

## Phase 5 — Observability

Implement:

```text
events
traces
metrics
artifact registry
replay
```

## Phase 6 — Research and knowledge

Implement:

```text
evidence graph
hybrid retrieval
knowledge graph
research orchestration
```

## Phase 7 — RSI

Implement:

```text
observer
diagnosis
candidate manager
sandbox experiments
benchmarking
shadow
canary
promotion
rollback
```

## Phase 8 — Organization mode

Implement:

```text
persistent teams
roles
budgets
KPIs
live agent operations
```

---

# 101. Minimum Viable Version of the Advanced Architecture

If implementation time is limited, prioritize these components:

```text
1. GoalContract
2. RuntimeState
3. EventStore
4. TaskDAG
5. ExecutiveAgent
6. AgentFactory
7. SkillRegistry
8. ToolRegistry
9. Sandbox
10. VerificationEngine
11. RecoveryEngine
12. ArtifactStore
13. PermissionEngine
14. ModelRouter
15. BasicScheduler
16. EvaluationSuite
17. RSI CandidateManager
```

Do not postpone verification and observability until after autonomy. They are part of autonomy.

---

# 102. Design Rules

### Rule 1
The model is replaceable; the runtime is not.

### Rule 2
Agents are runtime objects, not just prompts.

### Rule 3
Skills are procedural memory.

### Rule 4
Memory is not the same thing as context.

### Rule 5
Tools are capabilities; policies decide whether capabilities may be used.

### Rule 6
No important result without verification evidence.

### Rule 7
No dangerous action without an explicit permission path.

### Rule 8
No long-running task without persistent state.

### Rule 9
No self-improvement without a baseline and rollback path.

### Rule 10
No autonomous loop without explicit stop conditions.

### Rule 11
No silent model/tool/agent fallback.

### Rule 12
Prefer composable subsystems over a giant monolith.

---

# 103. Reference High-Level Architecture

```text
                             USER
                              |
                              v
                    +---------------------+
                    | CONTROL PLANE       |
                    | Desktop / CLI / API |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | COMMAND/MODE ROUTER |
                    +----------+----------+
                               |
       +-----------------------+------------------------+
       |                       |                        |
       v                       v                        v
    /PLAN                    /BOOST                   /GOAL
       |                       |                        |
       +-----------------------+------------------------+
                               |
                               v
                    +---------------------+
                    | EXECUTIVE SUPERVISOR|
                    +----------+----------+
                               |
        +----------------------+------------------------+
        |                      |                        |
        v                      v                        v
   GOAL ENGINE            PLANNER/DAG             AGENT FACTORY
        |                      |                        |
        +----------------------+------------------------+
                               |
                               v
                    +---------------------+
                    | AGENT ORCHESTRATOR  |
                    +----------+----------+
                               |
       +-----------------------+---------------------------+
       |            |             |           |             |
       v            v             v           v             v
   Research       Coding       Browser      QA          Security
       |            |             |           |             |
       +------------+-------------+-----------+-------------+
                               |
                               v
                    +---------------------+
                    | CAPABILITY FABRIC   |
                    | Tools / MCP / Skills|
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | POLICY + SANDBOX    |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | EXECUTION ENV       |
                    +----------+----------+
                               |
              +----------------+----------------+
              |                                 |
              v                                 v
         ARTIFACTS                           EVIDENCE
              |                                 |
              +----------------+----------------+
                               |
                               v
                    +---------------------+
                    | VERIFICATION ENGINE  |
                    +----------+----------+
                               |
                    +----------+----------+
                    |                     |
                   PASS                  FAIL
                    |                     |
                    v                     v
               COMPLETION            RECOVERY
                    |                     |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | OBSERVABILITY       |
                    | Events / Traces     |
                    | Metrics / Replay    |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | EVALUATION ENGINE   |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    | RSI ENGINE          |
                    | Observe             |
                    | Diagnose            |
                    | Generate            |
                    | Experiment          |
                    | Evaluate            |
                    | Promote             |
                    | Rollback            |
                    +---------------------+
```

---

# 104. Final Target: The Harness as an Agent Operating System

The final architecture should feel like:

```text
                   AGENT OPERATING SYSTEM

+----------------------------------------------------------+
|                    CONTROL PLANE                         |
| Desktop | CLI | API | Telegram | Web                     |
+----------------------------------------------------------+
|                  EXECUTIVE LAYER                         |
| Goal | Planning | Modes | Delegation | Decisions         |
+----------------------------------------------------------+
|                  AGENT FABRIC                            |
| Profiles | Dynamic Agents | Teams | A2A | Lifecycle       |
+----------------------------------------------------------+
|                  CAPABILITY FABRIC                       |
| Skills | Tools | MCP | Browser | Terminal | APIs         |
+----------------------------------------------------------+
|                  POLICY / EXECUTION                      |
| Permissions | Sandbox | Secrets | Trust | Hooks           |
+----------------------------------------------------------+
|                  MEMORY / KNOWLEDGE                      |
| Working | Episodic | Semantic | Procedural | Graph       |
+----------------------------------------------------------+
|                  ARTIFACT / EVIDENCE                     |
| Files | Reports | Patches | Evidence Graph | Citations   |
+----------------------------------------------------------+
|                  VERIFICATION                            |
| Tests | Critics | Goal Proof | Security | Regression    |
+----------------------------------------------------------+
|                  RESILIENCE                              |
| Checkpoints | Recovery | Failover | Rollback            |
+----------------------------------------------------------+
|                  OBSERVABILITY                           |
| Events | Traces | Metrics | Replay | Audit               |
+----------------------------------------------------------+
|                  EVALUATION                              |
| Benchmarks | Trajectories | Experiments | Regression    |
+----------------------------------------------------------+
|                  RSI / SELF-DEVELOPMENT                  |
| Observe | Diagnose | Generate | Experiment | Promote     |
| Shadow | Canary | Rollback                                  |
+----------------------------------------------------------+
```

The essential design goal is:

> **The agent should not merely answer the user's prompt. The harness should continuously manage the full lifecycle of goal understanding, planning, capability selection, autonomous execution, delegation, verification, recovery, persistence, learning, and controlled improvement.**

---

# 105. Official / Primary References

Use these as the authoritative starting points and re-check them before implementing vendor-specific integrations because command names, plan availability and product behavior can change.

1. Google Antigravity 2.0 product overview  
   https://antigravity.google/product/antigravity-2

2. Google Antigravity 2.0 overview  
   https://antigravity.google/docs/overview

3. Google Antigravity slash commands  
   https://antigravity.google/docs/slash-commands/

4. Google Antigravity subagents  
   https://antigravity.google/docs/subagents/

5. Google Antigravity CLI reference  
   https://antigravity.google/docs/cli/reference/

6. Google Antigravity CLI permissions  
   https://antigravity.google/docs/cli/permissions

7. Google Antigravity Skills documentation  
   https://www.antigravity.google/docs/ide/skills/

8. Google Antigravity 2.0 launch post  
   https://www.antigravity.google/blog/introducing-google-antigravity-2

9. Google Antigravity feature deep dive  
   https://antigravity.google/blog/google-io-2026-feature-deep-dive

10. Alpha introduction / harness concepts  
    https://github.com/bytedance/agent-workspace/blob/main/frontend/src/content/en/introduction/index.mdx

11. Alpha core concepts  
    https://agent_workspace.tech/en/docs/introduction/core-concepts

12. Hermes Agent tools and orchestration  
    https://github.com/hermes-agent-org/hermes/blob/main/website/docs/user-guide/features/tools.md

13. Hermes Agent persistent memory  
    https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md

---

# 106. Implementation Interpretation for `agent-workspace-desktop`

Recommended strategy:

```text
KEEP
  Alpha runtime strengths
  + existing tool/skill/sandbox/memory infrastructure

ADD
  Antigravity-inspired command/mode layer
  + dynamic agent factory
  + deep reasoning campaigns
  + long-horizon team campaigns
  + goal contracts
  + event-sourced state
  + evidence/verification
  + recovery
  + evaluation
  + RSI lab

AVOID
  copying product-specific UI assumptions
  hard-coding one LLM provider
  direct production self-modification
  a monolithic supervisor that performs every task
```

The intended result is a **Alpha-based universal harness with an Antigravity-inspired orchestration layer and a stronger verification/resilience/RSI subsystem**.

---

# 107. Definition of Done for the Harness

The harness is meaningfully mature when it can do all of the following without being redesigned for each project:

```text
[ ] Understand an open-ended user goal
[ ] Convert it to a GoalContract
[ ] Select an appropriate execution mode
[ ] Plan a DAG
[ ] Spawn specialized agents
[ ] Run agents concurrently when useful
[ ] Load skills dynamically
[ ] Discover and use MCP/tools
[ ] Use a sandbox
[ ] Use a browser worker
[ ] Persist state
[ ] Survive restart
[ ] Checkpoint risky work
[ ] Detect failures
[ ] Recover and replan
[ ] Verify results independently
[ ] Produce artifacts
[ ] Produce evidence
[ ] Record decisions
[ ] Monitor cost/resources
[ ] Trace every important action
[ ] Replay a run semantically
[ ] Evaluate trajectories
[ ] Schedule future goals
[ ] Learn reusable rules/skills
[ ] Generate RSI candidates
[ ] Test candidates safely
[ ] Shadow candidates
[ ] Canary candidates
[ ] Roll back regressions
[ ] Preserve security/permission boundaries
[ ] Keep the core modular and replaceable
```

When those properties exist, the project is no longer merely an "AI coding agent". It is a general-purpose **agentic harness/runtime** capable of supporting long-horizon, multi-agent, tool-using and self-improving workflows.

---

## End
