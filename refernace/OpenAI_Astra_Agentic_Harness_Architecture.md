# OpenAI Astra — Publicly-Informed Agentic Harness Architecture

> **Scope and evidence boundary**
>
> This document is an engineering reconstruction of the publicly described architecture patterns surrounding **GPT-6 Astra, OpenAI's computer-use stack, Responses API, hosted computer environments, skills, context management, multi-agent orchestration, and Agents API**. OpenAI has **not** publicly disclosed the complete internal source architecture of Astra or ChatGPT's private production harness. Therefore, sections marked **[PUBLIC]** are directly supported by OpenAI material, while sections marked **[RECONSTRUCTION]** are implementation-level designs inferred from those public capabilities.

---

## 1. Executive Summary

The public Astra-era OpenAI stack is best understood as a **model + agent harness + execution environment**, rather than as a model acting directly on a computer.

The key public primitives are:

- a frontier reasoning/multimodal model (GPT-6 Astra)
- Responses API as the request/response and tool orchestration surface
- computer-use actions for graphical interaction
- shell execution and a hosted container/workspace
- persistent conversation state and compaction for long-running work
- reusable, versioned skills and supporting resources
- asynchronous tool calls
- mid-turn steering
- model reasoning configuration that can change while preserving context/cache
- multi-agent orchestration and subagent delegation
- tracing/evaluation and safety layers
- sandboxed execution and policy-controlled access to external systems

The core execution loop is:

```text
USER GOAL
   |
   v
MODEL CONTEXT ASSEMBLY
   |
   +--> conversation state
   +--> task instructions
   +--> skills
   +--> tool definitions
   +--> environment state
   +--> relevant files/artifacts
   |
   v
ASTRA REASONING
   |
   v
NEXT ACTION
   |
   +------> computer action
   +------> shell/tool call
   +------> async tool call
   +------> delegate to subagent
   +------> request/consume information
   +------> final response
   |
   v
RUNTIME EXECUTION
   |
   v
OBSERVATION / TOOL RESULT
   |
   v
CONTEXT UPDATE
   |
   v
VERIFY / REPLAN / CONTINUE
   |
   +----> context compaction when needed
   |
   +----> repeat until completion
```

The architecture below turns these public primitives into an **implementation-grade reference harness**.

---

# 2. System-Level Architecture

## 2.1 High-level diagram

```text
+--------------------------------------------------------------------------------+
|                              ASTRA-STYLE AGENT                                 |
+--------------------------------------------------------------------------------+
|                                                                                |
|  USER / API / UI / SCHEDULE / EVENT                                           |
|                 |                                                              |
|                 v                                                              |
|        +----------------------+                                                |
|        | SESSION / IDENTITY   |                                                |
|        | auth / task scope    |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | TASK / GOAL LAYER    |                                                |
|        | intent / constraints |                                                |
|        | success conditions   |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | CONTEXT ENGINE       |                                                |
|        | state / history      |                                                |
|        | compaction / recall  |                                                |
|        | files / skills       |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | ASTRA MODEL          |                                                |
|        | reasoning            |                                                |
|        | multimodal           |                                                |
|        | tool selection       |                                                |
|        | planning             |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | ACTION / ORCHESTRATOR|                                                |
|        | tool routing         |                                                |
|        | parallelism          |                                                |
|        | handoffs             |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|       +-----------+------------+-------------------------+                     |
|       |           |            |                         |                     |
|       v           v            v                         v                     |
|   COMPUTER     SHELL         SKILLS                  SUBAGENTS                 |
|   USE          EXECUTION     + RESOURCES             / AGENTS                 |
|       |           |            |                         |                     |
|       +-----------+------------+-------------------------+                     |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | SECURE RUNTIME       |                                                |
|        | container / VM       |                                                |
|        | filesystem           |                                                |
|        | network policy       |                                                |
|        | credentials          |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | COMPUTER / WEB / DATA|                                                |
|        | apps / browser       |                                                |
|        | local files          |                                                |
|        | external APIs        |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   v                                                            |
|        +----------------------+                                                |
|        | OBSERVE / VERIFY     |                                                |
|        | output / tests       |                                                |
|        | visual verification  |                                                |
|        | state validation      |                                                |
|        +----------+-----------+                                                |
|                   |                                                            |
|                   +-----------------------> CONTEXT ENGINE                     |
|                                                                                |
+--------------------------------------------------------------------------------+
```

---

# 3. Astra Model Layer

## 3.1 [PUBLIC] Model role

OpenAI describes GPT-6 Astra as a model for multistep workflows across code, browsers, professional software, computer use, browsing, software engineering, science, and professional work.

Public model guidance also describes Astra as supporting:

- computer use
- structured outputs
- streaming
- programmatic tool calling
- multi-agent orchestration
- prompt caching
- persisted reasoning
- compaction
- asynchronous tool calls
- mid-turn steering
- configurable reasoning effort

The model is therefore designed to be a **general-purpose decision engine capable of continuing a workflow across tools and modalities**.

## 3.2 [RECONSTRUCTION] What the model should receive

A robust harness should construct a structured model context from:

```text
SYSTEM POLICY
USER INTENT
TASK STATE
ACTIVE PLAN
RELEVANT MEMORY
CURRENT ENVIRONMENT STATE
AVAILABLE SKILLS
AVAILABLE TOOLS
RECENT OBSERVATIONS
IMPORTANT ARTIFACTS
VERIFICATION STATE
PENDING ASYNC OPERATIONS
SAFETY / RISK STATE
```

Do not blindly inject an entire workspace into the prompt.

Use a **context compiler**.

---

# 4. Context Compiler

## 4.1 Architecture

```text
                  CONTEXT SOURCES
                       |
      +----------------+------------------+
      |                |                  |
      v                v                  v
 conversation       workspace          memory
      |                |                  |
      +----------------+------------------+
                       |
                       v
                 relevance ranker
                       |
                       v
                 policy filter
                       |
                       v
                 context budgeter
                       |
                       v
                 context assembler
                       |
                       v
                    ASTRA
```

## 4.2 Context priorities

Recommended precedence:

```text
1. safety/system policy
2. current user objective
3. explicit task constraints
4. trusted project state
5. active plan and unresolved blockers
6. relevant memory
7. tool/skill instructions
8. recent observations
9. external content
```

External documents, webpages, emails, repository content, and tool output should be treated as **data**, not as authoritative instructions.

---

# 5. Computer-Use Harness

## 5.1 [PUBLIC] Computer-use loop

OpenAI's Computer-Using Agent work describes an iterative perception-reasoning-action loop:

```text
SCREENSHOT
   |
   v
PERCEPTION
   |
   v
REASONING
   |
   v
ACTION
(click / type / scroll / keypress)
   |
   v
NEW SCREENSHOT
   |
   +----> repeat
```

The model uses screenshots and a general mouse/keyboard action space to operate graphical interfaces.

## 5.2 [RECONSTRUCTION] Production-grade computer fabric

A higher-end implementation should expose the following hierarchy:

```text
              COMPUTER FABRIC
                     |
        +------------+-------------+
        |            |             |
       WEB         DESKTOP      TERMINAL
        |            |             |
      DOM/CDP     A11Y/UIA      Shell
        |            |             |
      Browser     OS APIs       PowerShell
        |            |             |
        +------------+-------------+
                     |
                  VISION
                     |
                 screenshot
                     |
                   CUA
```

Use the strongest structured interface available before falling back to raw GUI actions:

```text
API
 > CLI
 > DOM / CDP
 > Accessibility tree
 > OS automation
 > visual GUI
 > raw coordinates
```

This is an engineering recommendation, not a claim that Astra internally uses this exact priority ordering.

---

# 6. Shell + Hosted Computer Environment

## 6.1 [PUBLIC] OpenAI architecture

OpenAI describes the shell/computer environment as an execution substrate for the agent. The model proposes commands; the runtime executes them; results return to the model for subsequent decisions.

Publicly described responsibilities include:

- shell command execution
- files and workspace state
- external access under network policy
- running arbitrary development/runtime commands
- artifact creation
- skill execution
- bounded shell output
- concurrent execution
- persistent environment context

## 6.2 Execution loop

```text
ASTRA
 |
 +--> shell command(s)
 |
 v
ORCHESTRATOR
 |
 +--> execute concurrently where safe
 |
 v
CONTAINER / SANDBOX
 |
 +--> stdout
 +--> stderr
 +--> files
 +--> processes
 +--> network results
 |
 v
STRUCTURED TOOL RESULT
 |
 v
ASTRA
```

## 6.3 Bounded output

Large logs should not automatically be injected into the full context.

Use:

```text
raw output
   |
   +--> artifact store
   |
   +--> summarizer / extractor
   |
   v
bounded model-visible result
```

The full artifact remains available for targeted inspection.

---

# 7. Persistent Workspace

## 7.1 [PUBLIC] Workspace concept

OpenAI describes the container as both an execution environment and a working context for the model. The model can organize resources there, inspect files selectively, transform data, run programs, and create durable artifacts.

## 7.2 Recommended workspace structure

```text
/workspace
  /project
  /input
  /output
  /artifacts
  /scratch
  /skills
  /state
  /tests
  /logs
```

Do not mix internal runtime metadata with user artifacts.

---

# 8. Skills Architecture

## 8.1 [PUBLIC] OpenAI skill model

OpenAI describes skills as reusable bundles containing a `SKILL.md` plus supporting resources such as API specifications and UI assets. Skill discovery can be progressive and execution happens in the agent's environment.

The public sequence is approximately:

```text
skill metadata
     |
     v
skill relevance decision
     |
     v
fetch skill bundle
     |
     v
place skill in environment
     |
     v
load instructions into context
     |
     v
execute scripts/resources as needed
```

## 8.2 Recommended skill contract

```text
skill/
  SKILL.md
  manifest.json
  references/
  assets/
  scripts/
  tests/
  evals/
```

Manifest fields:

```json
{
  "name": "spreadsheet",
  "version": "1.0.0",
  "description": "Create and validate spreadsheets",
  "permissions": ["filesystem:workspace"],
  "tools": ["python", "artifact_writer"],
  "entrypoints": ["..."],
  "evaluation": ["..." ]
}
```

---

# 9. Agent / Subagent Architecture

## 9.1 [PUBLIC] Astra delegation

OpenAI's current model guidance states that Astra is trained to divide and delegate work to subagents operating in parallel, and recommends explicitly tuning delegation for workflows where parallel work can save time or improve quality.

## 9.2 Recommended topology

```text
                     ROOT AGENT
                         |
              +----------+----------+
              |          |          |
              v          v          v
           RESEARCH    CODING    COMPUTER
              |          |          |
              +----------+----------+
                         |
                         v
                      REVIEWER
                         |
                         v
                       ROOT
```

For higher-level tasks:

```text
Executive
   |
   +-- planning agent
   +-- research agents
   +-- implementation agents
   +-- testing agents
   +-- verification agents
```

Use explicit limits:

```text
max_depth
max_agents
max_parallelism
max_cost
max_time
max_context_share
```

---

# 10. Parallel Tool Execution

## 10.1 [PUBLIC]

OpenAI's Responses API supports parallel tool calls and Astra supports asynchronous tool calling. OpenAI's computer environment work also describes concurrently executed shell sessions with independently streamed output.

## 10.2 Orchestration pattern

```text
                      ASTRA
                        |
                +-------+-------+
                |       |       |
                v       v       v
              Tool A  Tool B  Tool C
                |       |       |
                +-------+-------+
                        |
                        v
                 Result aggregator
                        |
                        v
                      ASTRA
```

Only parallelize operations whose dependencies and side effects permit concurrency.

---

# 11. Async Tool Architecture

## 11.1 [PUBLIC]

Astra supports asynchronous tool calls. The application executes the tool and returns the result using the original call identity when ready. This allows the model to continue reasoning or work on independent parts of a task while the tool runs.

## 11.2 State machine

```text
PENDING
  |
  v
RUNNING
  |
  +--> SUCCEEDED
  |
  +--> FAILED
  |
  +--> CANCELLED
```

Maintain:

```text
call_id
agent_id
task_id
start_time
status
deadline
result_location
retry_count
```

---

# 12. Mid-Turn Steering

## 12.1 [PUBLIC]

OpenAI's current Astra guidance describes mid-turn steering over a persistent connection: a new user instruction can arrive while Astra is working, preserving completed work and continuing from the updated state.

## 12.2 Recommended implementation

```text
RUNNING TASK
    |
    +------ user update ------+
    |                         |
    v                         v
current execution       steering event
    |                         |
    +-----------+-------------+
                v
          policy / planner
                |
        +-------+-------+
        |               |
   preserve work     discard plan
        |               |
        +-------+-------+
                v
             continue
```

This is critical for long-running assistants.

---

# 13. Adaptive Reasoning

## 13.1 [PUBLIC]

Astra supports changing reasoning configuration mid-conversation while preserving useful cached context.

## 13.2 [RECONSTRUCTION] Adaptive compute controller

```text
TASK
 |
 v
difficulty estimator
 |
 +--> low    -> low reasoning
 +--> medium -> medium reasoning
 +--> high   -> deep reasoning
 +--> critical -> deep + verifier + redundancy
```

The controller can also increase compute when:

- the agent is stuck
- repeated failures occur
- verification fails
- the task has high consequence
- conflicting evidence appears

---

# 14. Context Compaction

## 14.1 [PUBLIC]

OpenAI provides native conversation compaction. When context becomes too large, a compaction operation creates a compact representation intended to preserve high-value state needed for continued work.

## 14.2 Recommended long-run state model

```text
FULL TRACE
   |
   +--> raw event store
   |
   +--> artifacts
   |
   v
COMPACTION
   |
   v
ACTIVE STATE
   |
   +--> completed actions
   +--> active assumptions
   +--> important IDs
   +--> tool outcomes
   +--> unresolved blockers
   +--> next goal
```

Compaction must not delete the underlying trace. It only changes the model-visible working context.

---

# 15. Memory Architecture

The public OpenAI architecture emphasizes conversation state, persistent environment context, artifacts, and compaction. A full production harness can extend this into explicit memory classes.

```text
MEMORY
|
+-- Working memory
+-- Episodic memory
+-- Semantic memory
+-- Procedural memory / skills
+-- Failure memory
+-- Project memory
+-- Artifact memory
+-- World-state memory
```

Recommended retrieval flow:

```text
current task
   |
   v
memory query
   |
   v
relevance ranking
   |
   v
policy filter
   |
   v
context compiler
   |
   v
model
```

---

# 16. Artifact Architecture

Every durable output should become an artifact:

```text
code
file
spreadsheet
presentation
image
video
dataset
report
screenshot
build
log
```

Artifact metadata:

```json
{
  "artifact_id": "art_001",
  "type": "source_code",
  "path": "/workspace/project/app.py",
  "version": 7,
  "created_by": "agent-42",
  "parent": "art_000",
  "verified": true,
  "tests": ["run-81"]
}
```

This turns a transient conversation into a durable working system.

---

# 17. Verification Architecture

OpenAI's model guidance emphasizes calibrating testing and verification and says Astra tends to be thorough in verification for coding work.

A high-end harness should make verification explicit and independent:

```text
                     RESULT
                       |
             +---------+---------+
             |         |         |
             v         v         v
            TEST     STATE     VISUAL
             |      CHECK       CHECK
             |         |         |
             +---------+---------+
                       |
                       v
                 VERIFICATION
                       |
              +--------+--------+
              |                 |
             PASS              FAIL
              |                 |
              v                 v
            DONE              REPLAN
```

Verification levels:

```text
L0 schema validation
L1 deterministic checks
L2 unit tests
L3 integration tests
L4 end-to-end tests
L5 visual/UI verification
L6 independent critic
L7 adversarial / red-team verification
```

---

# 18. Safety and Authorization

## 18.1 [PUBLIC] Layered safety

OpenAI's Computer-Using Agent work describes layered safeguards across the model, agent system, and deployment processes. The Operator system card covers misuse, model mistakes, and prompt injection.

Publicly described mitigations include:

- refusals
- blocked websites/use cases
- moderation
- offline detection and review
- user confirmation for sensitive external side effects
- prompt-injection mitigations

## 18.2 [RECONSTRUCTION] Policy kernel

The model should never itself be the final security boundary.

```text
MODEL INTENT
    |
    v
RISK CLASSIFIER
    |
    v
POLICY ENGINE
    |
    +--> allow
    +--> deny
    +--> require confirmation
    +--> require stronger verification
    |
    v
EXECUTOR
```

Recommended action classes:

```text
R0 read-only
R1 local reversible
R2 workspace mutation
R3 external communication
R4 consequential external side effect
R5 irreversible / privileged
```

---

# 19. Credentials

Use a brokered credential architecture:

```text
Agent
 |
 | request capability
 v
Credential Broker
 |
 v
Policy Check
 |
 v
Short-lived Scoped Credential
 |
 v
Tool / API
```

Never expose unrestricted credentials in ordinary model context.

---

# 20. Prompt Injection Boundary

Treat all of these as potentially untrusted:

```text
web pages
PDFs
emails
Git repositories
README files
issues
user-uploaded files
MCP output
shell output
database content
```

Authority hierarchy:

```text
SYSTEM POLICY
   > USER INTENT
   > TRUSTED TASK STATE
   > PLAN
   > TOOL CONTRACT
   > EXTERNAL CONTENT
```

External content cannot promote itself to a higher authority tier.

---

# 21. Agent State Machine

Use a durable state machine instead of a loose while-loop.

```text
CREATED
  |
  v
PLANNING
  |
  v
READY
  |
  v
EXECUTING
  |
  +----> WAITING_TOOL
  |           |
  |           v
  |        RECEIVED
  |           |
  +-----------+
  |
  +----> VERIFYING
  |           |
  |        +--+--+
  |        |     |
  |       PASS  FAIL
  |        |     |
  |        v     v
  |      DONE  RECOVERY
  |              |
  |              v
  +---------- REPLANNING
                 |
                 +----> EXECUTING
```

This makes crash recovery and resumption possible.

---

# 22. Event-Driven Harness

Recommended event types:

```text
SessionCreated
GoalCreated
PlanCreated
PlanUpdated
SkillLoaded
ToolRequested
ToolStarted
ToolFinished
AsyncToolStarted
AsyncToolFinished
ComputerObserved
ComputerAction
ShellStarted
ShellFinished
ArtifactCreated
VerificationStarted
VerificationPassed
VerificationFailed
CompactionStarted
CompactionCompleted
SubagentSpawned
SubagentCompleted
UserSteered
PolicyBlocked
CredentialIssued
TaskFailed
TaskRecovered
TaskCompleted
```

Store these events durably.

Benefits:

- replay
- debugging
- audit
- analytics
- evaluation
- recovery
- training-data generation
- evolution research

---

# 23. Trace Architecture

```text
             AGENT RUN
                |
        +-------+-------+
        |       |       |
      events  tools  state
        |       |       |
        +-------+-------+
                |
                v
          TRACE STORE
                |
        +-------+-------+
        |       |       |
      replay  eval    debug
```

Each trace should capture:

```text
run_id
session_id
agent_id
model
reasoning configuration
tool calls
observations
latency
errors
artifacts
verification
authorization decisions
```

Do not store hidden chain-of-thought as ordinary application logs. Store the structured decisions, tool calls, observations, outcomes, and evaluation signals needed for reproducibility.

---

# 24. Multi-Agent Coordination

Recommended task bus:

```text
             ROOT AGENT
                 |
                 v
             TASK BUS
        +--------+--------+
        |        |        |
        v        v        v
      Agent A  Agent B  Agent C
        |        |        |
        +--------+--------+
                 |
                 v
            RESULT BUS
                 |
                 v
              REVIEW
                 |
                 v
             ROOT AGENT
```

Task message:

```json
{
  "task_id": "task_101",
  "parent": "task_100",
  "objective": "Analyze API compatibility",
  "inputs": ["repo://project"],
  "constraints": {},
  "required_evidence": ["tests", "source_refs"],
  "deadline": null,
  "permissions": ["filesystem:read"]
}
```

---

# 25. Long-Running Work

The harness should survive:

```text
context exhaustion
process restart
computer restart
network failure
tool timeout
model failure
authentication expiry
partial completion
user steering
```

Use:

```text
checkpoint
   |
   +--> task state
   +--> plan
   +--> artifacts
   +--> pending calls
   +--> memory updates
   +--> verification state
```

Resume from the latest valid checkpoint.

---

# 26. Background Execution

For long jobs:

```text
SUBMIT
  |
  v
BACKGROUND JOB
  |
  +--> progress events
  +--> async tools
  +--> checkpointing
  +--> failure recovery
  |
  v
COMPLETION EVENT
```

A UI can reconnect later and reconstruct the job from durable state and events.

---

# 27. World-State Model

The public environment concept can be extended into a structured state model:

```text
WORLD
|
+-- computer
|    +-- OS
|    +-- processes
|    +-- windows
|    +-- devices
|
+-- browser
|    +-- tabs
|    +-- URLs
|    +-- page state
|
+-- workspace
|    +-- files
|    +-- repos
|    +-- builds
|
+-- external systems
     +-- APIs
     +-- databases
     +-- cloud services
```

This allows planning against actual environment state rather than relying only on textual history.

---

# 28. Generalized Action Model

Every tool/computer operation should have an explicit contract:

```json
{
  "action_id": "act_77",
  "intent": "Open browser settings",
  "mechanism": "computer.click",
  "target": {},
  "risk": "low",
  "preconditions": {},
  "expected_state": {},
  "verification": {}
}
```

This supports state-aware execution and post-action verification.

---

# 29. Tool Registry

```text
TOOL REGISTRY
|
+-- shell
+-- computer
+-- browser
+-- filesystem
+-- code execution
+-- database
+-- search
+-- file parser
+-- artifact writer
+-- subagent
+-- custom MCP tools
```

Tool metadata:

```text
name
description
schema
risk
auth scope
environment
timeout
parallel-safe
side-effects
verification method
```

---

# 30. Model Router

For a generalized harness supporting multiple models:

```text
                 TASK
                  |
                  v
            CAPABILITY MAP
                  |
        +---------+---------+
        |         |         |
      coding    vision    reasoning
        |         |         |
        +---------+---------+
                  |
                  v
             MODEL ROUTER
```

Criteria:

```text
capability
latency
context capacity
cost
local availability
reliability
risk
```

Astra can be treated as one model adapter; the harness remains model-agnostic.

---

# 31. Planner

Use hierarchical planning:

```text
GOAL
 |
 +--> Phase 1
 |      +--> Task 1
 |      +--> Task 2
 |
 +--> Phase 2
 |      +--> Task 3
 |      +--> Task 4
 |
 +--> Phase 3
        +--> Verification
```

Each task has:

```text
objective
inputs
constraints
dependencies
owner agent
required skills
required tools
success criteria
verification
```

---

# 32. Replanning

A high-end harness should replan whenever:

```text
new user instruction
world-state change
failed action
unexpected observation
verification failure
new evidence
resource unavailable
risk escalation
```

Flow:

```text
OBSERVE
 |
 v
STATE DIFF
 |
 +--> unchanged -> continue
 |
 +--> changed -> re-evaluate plan
                          |
                          v
                     new plan
```

---

# 33. Research Architecture

For knowledge-work tasks:

```text
Research Director
 |
 +--> search
 +--> source retrieval
 +--> document extraction
 +--> evidence validation
 +--> contradiction search
 +--> synthesis
 +--> citation verification
```

Each major conclusion should map to evidence.

---

# 34. Coding Architecture

```text
REPOSITORY DISCOVERY
       |
       v
ARCHITECTURE ANALYSIS
       |
       v
TASK DECOMPOSITION
       |
       v
IMPLEMENTATION
       |
       v
BUILD
       |
       v
UNIT TESTS
       |
       v
INTEGRATION TESTS
       |
       v
E2E / BROWSER TESTS
       |
       v
VISUAL VERIFICATION
       |
       v
SECURITY REVIEW
       |
       v
RELEASE
```

---

# 35. Office / Artifact Automation

A universal computer agent should be able to combine:

```text
structured APIs
+
shell
+
Python
+
GUI
+
vision
```

Example spreadsheet flow:

```text
source data
 |
 v
python transform
 |
 v
write spreadsheet
 |
 v
formula validation
 |
 v
open in spreadsheet application
 |
 v
visual verification
 |
 v
final artifact
```

This reflects the public OpenAI emphasis on combining tools, skills, shell, files, and artifact generation rather than forcing every workflow through the GUI.

---

# 36. Evolution / Self-Improvement Boundary

**Astra public material does not disclose an autonomous self-modifying harness comparable to NVIDIA AVO.** Therefore, an AVO-style evolution system is an extension rather than a documented Astra internal component.

Recommended architecture:

```text
REAL RUNS
   |
   v
FAILURE MINING
   |
   v
IMPROVEMENT HYPOTHESIS
   |
   v
CANDIDATE HARNESS / SKILL
   |
   v
ISOLATED BENCHMARK
   |
   v
REGRESSION + SECURITY
   |
   v
SHADOW DEPLOYMENT
   |
   v
CANARY
   |
   +--> PASS -> PROMOTE
   |
   +--> FAIL -> ROLLBACK
```

The production agent should not be allowed to silently replace its own security-critical runtime.

---

# 37. Evaluation Harness

Evaluate trajectories, not only answers.

```text
              RUN
               |
       +-------+-------+
       |       |       |
     task    actions  state
       |       |       |
       +-------+-------+
               |
               v
            GRADERS
               |
       +-------+-------+
       |       |       |
     success safety reliability
       |       |       |
       +-------+-------+
               |
               v
          SCORECARD
```

Recommended metrics:

```text
completion rate
verification pass rate
recovery rate
average action count
latency
cost
intervention rate
policy violations
regression rate
```

---

# 38. Observability

Use OpenTelemetry-compatible traces and metrics.

```text
Agent
 |
 +--> Trace
 |     +--> plan
 |     +--> model call
 |     +--> tool call
 |     +--> environment
 |     +--> verification
 |
 +--> Metrics
 |     +--> latency
 |     +--> success
 |     +--> failures
 |     +--> cost
 |
 +--> Events
```

---

# 39. Failure and Recovery Engine

Failure classes:

```text
tool failure
network failure
environment mismatch
wrong assumption
verification failure
authentication failure
policy denial
prompt injection
model error
stagnation
unknown
```

Recovery policies:

```text
retry
alternate tool
alternate model
re-observe
restore checkpoint
replan
delegate
request user input
terminate safely
```

Use bounded retries.

---

# 40. Complete Runtime Algorithm

```text
function run(goal):

    session = create_session(goal)
    state = initialize_world_state()

    while not terminal(session):

        state = observe_environment(state)

        context = compile_context(
            goal=goal,
            state=state,
            memory=retrieve_relevant_memory(goal, state),
            skills=discover_relevant_skills(goal, state),
            tools=available_tools(state),
            artifacts=relevant_artifacts(goal, state)
        )

        if context.needs_compaction:
            context = compact_context(context)

        action = model_decide(context)

        if action.type == "delegate":
            result = run_subagent(action)
            record(result)
            continue

        if action.type == "async_tool":
            start_async_tool(action)
            continue

        if action.type == "final":
            verification = verify_final_result(action, state)
            if verification.pass:
                return finalize(action, verification)
            else:
                record_failure(verification)
                continue

        authorization = policy_check(action, state)

        if authorization.denied:
            record_policy_event(action, authorization)
            continue

        result = execute(action)

        record_event(action, result)

        state = update_state(state, result)

        verification = verify_action(action, result, state)

        if not verification.pass:
            recovery = select_recovery(action, result, state)
            execute_recovery(recovery)
            continue

        persist_checkpoint(session, state)

    return recover_or_escalate(session)
```

---

# 41. Recommended Internal Services

A serious implementation should separate responsibilities into services/modules:

```text
agent-core
context-engine
planner
supervisor
model-router
computer-runtime
shell-runtime
skill-manager
tool-registry
subagent-manager
memory-service
artifact-service
policy-engine
credential-broker
state-store
event-store
trace-service
evaluation-service
recovery-engine
scheduler
```

---

# 42. Suggested Repository Layout

```text
astra-style-harness/
|
+-- apps/
|   +-- desktop/
|   +-- web/
|   +-- gateway/
|
+-- core/
|   +-- agent/
|   +-- planner/
|   +-- supervisor/
|   +-- state/
|   +-- context/
|
+-- models/
|   +-- providers/
|   +-- router/
|   +-- vision/
|
+-- tools/
|   +-- registry/
|   +-- shell/
|   +-- computer/
|   +-- browser/
|   +-- filesystem/
|   +-- mcp/
|
+-- skills/
|
+-- agents/
|   +-- definitions/
|   +-- handoffs/
|   +-- workers/
|
+-- memory/
|   +-- working/
|   +-- episodic/
|   +-- semantic/
|   +-- procedural/
|   +-- failure/
|
+-- runtime/
|   +-- sandbox/
|   +-- containers/
|   +-- credentials/
|   +-- network/
|
+-- evaluation/
|   +-- benchmarks/
|   +-- graders/
|   +-- replay/
|
+-- observability/
|   +-- events/
|   +-- traces/
|   +-- metrics/
|
+-- evolution/
|   +-- experiments/
|   +-- candidates/
|   +-- canary/
|
+-- schemas/
+-- docs/
```

---

# 43. Data Stores

Recommended:

```text
PostgreSQL
  -> task state
  -> sessions
  -> metadata
  -> events

pgvector / vector store
  -> semantic retrieval

Object storage / filesystem
  -> artifacts
  -> screenshots
  -> large logs

Redis / NATS
  -> events
  -> queues
  -> async jobs
```

For a single-machine prototype, SQLite + filesystem + local vector index is sufficient.

---

# 44. Minimal vs High-End Deployment

## Minimal local

```text
Python
+ local model runtime
+ SQLite
+ filesystem
+ Playwright
+ local sandbox
```

## High-end self-hosted

```text
Python/TypeScript/Rust
+ vLLM/Ollama/llama.cpp
+ PostgreSQL
+ pgvector
+ NATS
+ Playwright/CDP
+ VM/container runtime
+ OpenTelemetry
+ distributed workers
```

## Frontier-scale

```text
multi-GPU inference
+ distributed workers
+ isolated compute pools
+ dedicated computer-use environments
+ high-throughput event bus
+ durable object storage
+ benchmark farm
+ canary/evolution infrastructure
```

The logical architecture remains the same.

---

# 45. What Is Actually Public vs Inferred

## Directly supported by OpenAI public material

- Astra is designed for multistep workflows across code, browsers, professional software, science, and professional work.
- Responses API is a primary interaction surface.
- Computer use exists as a tool/action interface.
- Shell execution can be orchestrated in an agent loop.
- Hosted container/workspace can provide persistent execution context.
- Skills are reusable bundles with instructions/resources.
- Context compaction exists for long-running work.
- Async tool calling exists.
- Mid-turn steering exists.
- Parallel tool calls exist.
- Multi-agent orchestration exists.
- Astra supports configurable reasoning effort.
- OpenAI uses layered safety controls for computer-use systems.
- OpenAI provides Agents API capabilities around managed sandboxes, tools, skills, state, and long-session context management.

## Not publicly disclosed in full

- Astra's private neural architecture and training pipeline.
- The complete internal ChatGPT/Astra production orchestrator implementation.
- Exact internal supervisor algorithms.
- Exact production memory ranking algorithms.
- Exact hidden computer-use policy implementation.
- Exact internal prompt/system instruction set.
- Proprietary infrastructure topology.

Therefore, this document should be used as a **publicly-informed architecture target**, not as a claim that it reproduces OpenAI's private source code.

---

# 46. Reference Comparison of Public OpenAI Agent Primitives

| Primitive | Role in harness |
|---|---|
| GPT-6 Astra | general reasoning / multimodal decision engine |
| Responses API | model interaction + tool orchestration |
| Computer use | graphical computer interaction |
| Shell | general execution / file / process / data operations |
| Hosted container | isolated persistent workspace |
| Skills | reusable workflows and resources |
| Conversation state | continuity across turns |
| Compaction | long-session context management |
| Async tools | concurrent long-running operations |
| Mid-turn steering | user intervention during execution |
| Multi-agent | delegation and parallel work |
| Tracing/evals | runtime quality measurement |
| Safety layers | control of risky computer actions |
| Agents API | managed agent runtime and sandbox abstraction |

---

# 47. The Core Astra-Style Design Pattern

The strongest abstraction is:

```text
                 MODEL
                   |
          decides / reasons
                   |
                   v
               HARNESS
                   |
        +----------+----------+
        |          |          |
      context    tools      agents
        |          |          |
        +----------+----------+
                   |
                   v
              ENVIRONMENT
                   |
                   v
              OBSERVATION
                   |
                   +---------> MODEL
```

The model is not the entire agent.

The **harness provides the persistence, execution, context management, tool orchestration, and environment interface that turn model capability into an autonomous workflow.**

---

# 48. Engineering Target for an Astra-Inspired Open Harness

If implementing your own open version, aim for:

```text
GENERAL REASONING
+
LONG-RUNNING SESSIONS
+
PERSISTENT WORKSPACE
+
COMPUTER USE
+
SHELL
+
SKILLS
+
PARALLEL TOOLS
+
ASYNC TOOLS
+
SUBAGENTS
+
MID-TURN STEERING
+
ADAPTIVE REASONING
+
MEMORY
+
ARTIFACTS
+
VERIFICATION
+
RECOVERY
+
SECURITY
+
TRACE / EVALUATION
+
OPTIONAL EVOLUTION LAYER
```

This is the practical architecture to reproduce the **capability shape** of the public Astra-era agent stack without pretending to reproduce proprietary internals.

---

# 49. Recommended Build Order

## Phase 1 — Core agent loop

```text
model adapter
+ tool registry
+ state machine
+ event log
+ shell
```

## Phase 2 — Computer environment

```text
browser
+ Playwright/CDP
+ screenshot
+ keyboard/mouse
+ desktop automation
```

## Phase 3 — Long-running work

```text
persistent workspace
+ checkpoints
+ compaction
+ artifact store
```

## Phase 4 — Skills

```text
SKILL.md
+ skill registry
+ progressive discovery
+ executable resources
```

## Phase 5 — Multi-agent

```text
agent factory
+ task bus
+ delegation
+ parallel workers
```

## Phase 6 — Safety

```text
policy engine
+ risk classifier
+ credential broker
+ confirmation system
```

## Phase 7 — Verification

```text
test runner
+ visual verification
+ evidence graph
+ independent critic
```

## Phase 8 — Evolution

```text
trajectory mining
+ candidate generation
+ benchmark
+ regression
+ canary
+ rollback
```

---

# 50. Source Notes

1. OpenAI, **GPT-6 Astra: A new generation of intelligence**. Describes Astra's capability in computer use, browsing, software engineering, science, professional work, alignment, and use with generic computer-use harnesses. https://openai.com/index/gpt-6-astra/

2. OpenAI, **Model guidance — Using GPT-6 Astra**. Documents Astra's current API capabilities and guidance around async tool calling, mid-turn steering, configurable reasoning, multi-agent orchestration, computer use, compaction, delegation, testing, and instruction handling. https://developers.openai.com/api/docs/guides/latest-model

3. OpenAI, **From model to agent: Equipping the Responses API with a computer environment**. Describes the shell/tool loop, container environment, filesystem context, concurrent execution, bounded output, skills, and compaction used to construct longer-running agents. https://openai.com/index/equip-responses-api-computer-environment/

4. OpenAI, **Computer-Using Agent**. Describes CUA's perception/reasoning/action loop, screenshot-based computer interaction, action space, multi-step planning, error handling, and layered safety. https://openai.com/index/computer-using-agent/

5. OpenAI, **Operator System Card**. Describes safety mitigations for computer-use agents including misuse, model mistakes, prompt injection, confirmations, and layered deployment safeguards. https://openai.com/index/operator-system-card/

6. OpenAI, **Introducing the Agents API**. Describes managed sandboxes, files, packages, skills, plugins, long-session context management, and maintaining the harness alongside model improvements. https://openai.com/index/introducing-the-agents-api/

7. OpenAI, **Responses API reference**. Documents tool calls, parallel tool calls, computer actions, shell/code outputs, configuration updates, persisted response state, and compaction primitives. https://developers.openai.com/api/docs/

---

# Final Architecture

```text
USER
 |
v
SESSION / IDENTITY
 |
v
GOAL + CONSTRAINTS
 |
v
CONTEXT ENGINE
 |
+--> conversation state
+--> memory
+--> project state
+--> files / artifacts
+--> skills
+--> tool catalog
+--> environment state
 |
v
ASTRA / REASONING MODEL
 |
v
ORCHESTRATOR
 |
+--> planner
+--> subagents
+--> parallel tools
+--> async tools
+--> computer actions
+--> shell commands
 |
v
POLICY / SAFETY KERNEL
 |
v
SECURE ENVIRONMENT
 |
+--> browser
+--> desktop
+--> shell
+--> filesystem
+--> APIs
+--> applications
 |
v
OBSERVATION
 |
v
VERIFICATION
 |
+--> tests
+--> state validation
+--> visual check
+--> evidence
 |
v
STATE + MEMORY + ARTIFACT UPDATE
 |
v
CHECKPOINT / COMPACTION
 |
+--------------------------+
|                          |
| continue                  |
| replan                    |
| recover                   |
| delegate                  |
| user steer                |
| complete                  |
+--------------------------+
```

**Bottom line:** the public Astra architecture is best reproduced as a **reasoning model wrapped by a persistent agent harness with context compilation, tool orchestration, computer execution, durable workspace, skills, multi-agent delegation, adaptive compute, compaction, verification, and layered safety**. The private internal implementation is not public, so the design above deliberately separates documented facts from engineering reconstruction.
