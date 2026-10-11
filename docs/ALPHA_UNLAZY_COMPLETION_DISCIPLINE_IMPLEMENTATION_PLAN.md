# Alpha AI Agent — Unlazy-Inspired Completion Discipline and Evidence-Driven Execution Plan

**Purpose:** A Python-first implementation plan for selectively adapting the useful ideas in [`Leonxlnx/unlazy`](https://github.com/Leonxlnx/unlazy) to Alpha, Alpha's multi-agent/bot/subagent/swarm workflows, and its existing LangChain/LangGraph-based orchestration.

**Assessment date:** 10 October 2026  
**Document type:** Design and implementation plan only — no source code changes are implied by this document.

---

## 1. Executive decision

**Recommendation: implement the concept. Do not port the repository wholesale.**

Unlazy is best understood as a **completion-discipline and verification layer**, not a standalone autonomous-agent framework. Its central pattern is to define what “done” means before execution, split substantial work into independently verifiable leaves, coordinate agents with explicit ownership/state, execute reviewed checks, re-verify evidence, and prevent the final response from claiming more than the evidence proves. The repository contains a `SKILL.md`, gate/plan templates, a gate checker, optional Claude Code Stop-hook support, orchestration/dispatch rules, tests, and a security model. Its current source describes target version `2.1.0` as unreleased, so Alpha should pin any future reference to an exact commit rather than treating that version as a published release. See the [README](https://github.com/Leonxlnx/unlazy), [SKILL.md](https://github.com/Leonxlnx/unlazy/blob/main/SKILL.md), and [CHANGELOG.md](https://github.com/Leonxlnx/unlazy/blob/main/CHANGELOG.md).

This fits Alpha especially well because Alpha is intended to execute real tasks through tools and coding agents, not merely produce plausible answers. A user-visible answer must not be mistaken for completed work; tool invocation must not be mistaken for tool success; a successful subagent must not be mistaken for successful end-to-end integration; and a check that passed before later edits must not be treated as current proof.

### Proposed result

Build an **Alpha Completion Engine** in Python with these core guarantees:

1. Every substantial task has explicit acceptance criteria before implementation begins.
2. Each criterion maps to an observable artifact, state change, test, or human-reviewed result.
3. The planner creates a dependency-aware task tree/DAG with defined owners and non-conflicting file scopes when agents work in parallel.
4. A task can only become verified when its defined checks and evidence pass.
5. Any change to an artifact or gate definition invalidates stale evidence and triggers re-verification where required.
6. The root task cannot become complete while a required child, integration check, dispatch operation, or final request-reconciliation item remains unfinished or abandoned.
7. Alpha reports **verified, partially complete, blocked, failed, or awaiting approval** honestly instead of emitting an unsupported “done.”
8. Dangerous checks and side effects use explicit policy/approval; approval is not treated as a sandbox.
9. The feature remains Python-first, host-agnostic, resumable after process restart, observable in Alpha's UI, and compatible with existing agent execution modes.

### What not to copy literally

- Do not assume task effort multiplies productively just by increasing tree depth. Decomposition is useful; unlimited fan-out wastes tokens, CPU, RAM, and time.
- Do not import a Node-based helper as Alpha's primary runtime. Reimplement the selected contract in Python and keep JavaScript/Node only for an optional host adapter if one is later needed.
- Do not run arbitrary shell commands just because they appear in a generated gate file. Unlazy itself explicitly treats approval as consent, not sandboxing; commands inherit the process's filesystem, credentials, environment, and network access. Alpha needs its own command policy and optional isolation.
- Do not block all chat turns or all agents until every possible check passes. Make this a task mode/policy used for substantial work, not a global interaction tax.
- Do not install an editor-specific Stop hook as the core implementation. Alpha needs a framework-level completion contract that works in the UI, API, bot, background task, and coding-agent paths.

---

## 2. What Unlazy does and what Alpha should learn from it

### 2.1 Core ideas worth adapting

| Unlazy concept | What it solves | Alpha adaptation | Priority |
|---|---|---|---|
| Acceptance/gate ledger before work | Prevents changing the definition of success midway through a task | Persist task-specific acceptance criteria and required observations before tool execution | P0 |
| Depth Tree decomposition | Turns broad work into scoped tasks with verifiable deliverables | Bounded task DAG/tree, with leaf-level output contracts and parent integration gates | P0 |
| Runnable gates | Replaces subjective “looks done” with observable checks | Typed Python check registry: test, command, file, API, database read-only, UI, and manual-review checks | P0 |
| Re-verification | Detects stale success after subsequent edits | Evidence version/fingerprint tracking and required re-run policy | P0 |
| Parent/root integration verification | Prevents a set of individually successful tasks from being mistaken for a functioning whole | Parent acceptance checks, end-to-end tests, original-request reconciliation | P0 |
| Explicit task states | Makes in-progress, blocked, abandoned, and verified work visible | Persisted task/check/dispatch state machine exposed in Alpha UI and event log | P0 |
| Agent ownership and path leases | Reduces parallel agents overwriting the same files | Per-agent write scope, changed-file validation, collision warnings, Git worktrees for true isolation | P1 |
| Rolling parallel dispatch | Starts newly unblocked work without waiting for unrelated tasks | Scheduler starts only ready DAG nodes and respects configurable concurrency/resource limits | P1 |
| Audited dispatch waves | Makes partial or failed subagent launches visible | Record each requested agent, host job/session ID, launch result, and terminal state | P1 |
| Evidence-bound gates | Detects stale/changed gate definitions and recorded evidence | Gate-definition digest + check output/status fingerprint + run metadata; re-run on invalidation | P1 |
| Gate linting | Warns when a check is too weak to prove its title | Static/advisory lint for weak commands, vague expectations, unmeasured values, and false-positive checks | P1 |
| Completion/Stop hook | Keeps an agent from exiting while required gates remain incomplete | Alpha orchestration guard at task-finalization boundary; UI may still allow an explicit partial report | P1 |
| Token/attention discipline | Limits wasteful exploration and unnecessary parallelism | Model/tool budgets, stop conditions, progressive context loading, and risk-based verification | P1 |
| Fail-closed parsing and explicit abandonment | Avoids malformed state becoming success | Invalid ledgers, missing outputs, and abandoned required work must never count as passing | P0 |

**Source basis:** [repository README and gate contract](https://github.com/Leonxlnx/unlazy), [orchestration reference](https://github.com/Leonxlnx/unlazy/blob/main/references/orchestration.md), [dispatch reference](https://github.com/Leonxlnx/unlazy/blob/main/references/dispatch.md), [parallel work reference](https://github.com/Leonxlnx/unlazy/blob/main/references/parallel.md), and [security model](https://github.com/Leonxlnx/unlazy/blob/main/SECURITY.md).

### 2.2 The most important conceptual shift

Alpha should separate these states and events instead of treating them as equivalent:

- **Planned:** acceptance criteria and work decomposition are recorded.
- **Dispatched:** an agent/job launch was requested.
- **Running:** the host confirms the agent/job is active.
- **Returned:** the worker sent a result; this is not verification.
- **Locally verified:** its deliverable and leaf gates pass.
- **Integrated:** parent-level interfaces and cross-component behavior pass.
- **Root verified:** all required criteria in the user's current request have current evidence.
- **Reported:** Alpha explains the outcome and evidence to the user.

A model response that says “completed” can only be a **claim to check**, never the completion signal itself.

### 2.3 Interpret the “Depth Tree” carefully

The name is useful, but Alpha must implement the disciplined decomposition rather than an unlimited effort multiplier. Increase depth only where each level adds a meaningful boundary, acceptance contract, dependency, or review point. More levels and agents do not guarantee better outcomes.

Use:

- A shallow tree for a small feature or one-file fix.
- A dependency DAG for tasks with shared prerequisites or cross-cutting integration.
- A deeper tree only where separate specialist work can be integrated and checked independently.
- A strict total resource budget even when every leaf inherits the root's *maximum available deadline*; do not give each child an unlimited token, wall-clock, model-call, or subprocess budget.

The upstream project itself notes that its research motivates failure modes, not a guaranteed fixed improvement. Alpha should measure its own outcomes rather than promise a benchmark uplift.

---

## 3. Fit for Alpha's existing architecture

Alpha is a Python agent system with multi-agent modes, specialist/subagents/swarm behavior, tool execution, and LangChain/LangGraph use. This feature should be added as an **orchestration and verification subsystem**, not as a replacement for the existing executor or a new agent framework.

### Recommended integration boundaries

1. **Request/intent layer** — decides whether completion discipline is appropriate and chooses a verification policy.
2. **Planner** — drafts task tree/DAG and acceptance contract; a validator checks for missing outcomes and ambiguous gates.
3. **Completion Engine** — persists tasks, states, ownership, gates, evidence, budgets, and audit events.
4. **Scheduler/dispatcher** — runs ready work through Alpha's existing agent/subagent adapters.
5. **Check registry** — executes only registered, policy-approved checks.
6. **Evidence store** — records command/test results, artifacts, fingerprints, provenance, timestamps, and re-verification status.
7. **Finalization guard** — checks the entire scope before Alpha is allowed to label it complete.
8. **UI/API/event layer** — displays live state, failures, evidence, retry counts, blocked reasons, and user-approval requests.

### Integration with LangGraph

Use LangGraph to represent the execution and recovery flow, while keeping persisted state and verification semantics in Alpha's own Python domain layer. A graph node must not independently decide to mark its whole task complete simply because its LLM call returned.

Suggested graph stages:

`classify_request → define_contract → validate_contract → build_dag → dispatch_ready_nodes → collect_returns → run_leaf_gates → verify_leaf → unlock_dependents → integrate_parent → reverify_scope → reconcile_request → finalize_report`

Conditional edges should route to `repair_contract`, `retry_check`, `recover_worker`, `request_approval`, `mark_blocked`, or `handoff_required` as appropriate. Persist enough identifiers and state to resume a graph run after an Alpha restart without duplicating side effects.

### Avoid a parallel “truth source” problem

If Alpha already has a task table, event bus, agent registry, or job manager, extend those systems rather than creating duplicate state stores. The Completion Engine should define the contract and evidence rules; existing components should continue to own their established responsibilities. Before implementation, inspect the current Alpha repository and map every proposed module to the actual codebase. The filenames below are conceptual suggestions, not claims about current paths.

---

## 4. Proposed Python module layout

Adapt names to Alpha's real structure after repository inspection.

```text
alpha/
  completion/
    __init__.py
    models.py              # Pydantic/domain models: scope, task, gate, evidence
    policy.py              # mode selection, risk tier, budget and permission policy
    contract_builder.py    # request -> acceptance contract
    contract_validator.py  # completeness, ambiguity, duplicate/missing outcomes
    decomposer.py          # bounded tree/DAG and dependency discovery
    state_machine.py       # legal state transitions only
    repository.py          # persistence interfaces
    sqlite_repository.py   # starter persistence adapter if Alpha has none
    scheduler.py           # ready-node scheduling and bounded concurrency
    dispatch.py            # adapter to Alpha's existing agent launch system
    ownership.py           # declared write scopes and conflict detection
    gates.py               # gate registry and execution policy
    evidence.py            # artifact IDs, fingerprints, provenance and freshness
    verifier.py            # leaf, branch, and root verification
    reconciliation.py      # original-request-to-current-contract audit
    finalization.py        # blocks unsupported full-completion status
    budget.py              # model/token/tool/time/parallel-work budgets
    events.py              # structured event definitions
    errors.py              # typed errors and terminal/blocking reasons
    api.py                 # internal/API boundary if needed
    adapters/
      langgraph.py          # graph node/edge adapter
      coding_agent.py       # adapters to coding agents already supported by Alpha
      shell.py              # restricted subprocess executor
      browser.py            # browser-result observation adapter
      api_check.py          # API health/contract checks
  tests/
    completion/
      test_contract_validation.py
      test_state_machine.py
      test_gate_execution.py
      test_evidence_freshness.py
      test_scheduler_dependencies.py
      test_ownership_conflicts.py
      test_finalization_guard.py
      test_restart_recovery.py
```

### Python technology guidance

- Reuse Alpha's existing Pydantic version and conventions if present.
- Use `asyncio` only where the existing orchestration is already async; avoid mixing event-loop patterns.
- Use SQLite as a pragmatic local persistence option only if Alpha lacks an existing durable store. If Alpha already persists tasks/events, add tables or records there instead.
- Use a database transaction or atomic state update for each state transition and evidence write.
- Do not add a large new dependency solely to parse the ledger. JSON/Pydantic domain objects or a minimal strict YAML/Markdown front end are sufficient.
- Avoid executing free-form Python snippets from the model. Check types should map to reviewed functions or policy-controlled command invocations.

---

## 5. Core domain model and data contract

The implementation should have one canonical, persisted representation of the work contract. Markdown can be a human-readable export/import format, but it must not be the only source of truth for concurrent execution.

### 5.1 Scope / run

Represents one user's task and all children under it.

Required properties:

- `scope_id`: unique stable identifier.
- `request_snapshot`: exact original user request or safe normalized representation, preserving required constraints.
- `request_revision`: increases when the user changes the task or the contract is amended.
- `mode`: `normal`, `thorough`, `autopilot`, `coding`, `research`, etc., mapped to policies available in Alpha.
- `status`: `planning`, `running`, `verifying`, `complete`, `partial`, `blocked`, `failed`, `cancelled`, `handoff_required`.
- `created_at`, `updated_at`, `started_at`, `finished_at`.
- `budget`: time, model calls/tokens where measurable, tool invocations, child count, concurrency.
- `root_contract_version`: version/digest of current acceptance contract.
- `last_verified_revision`: lets Alpha detect that new edits occurred after verification.
- `final_report_id`: reference to the report generated after finalization, if any.

### 5.2 Acceptance criterion / gate

Each gate should prove one independently meaningful outcome or explicitly mark it as a human/manual observation.

Suggested fields:

```text
gate_id
scope_id
task_id
title
required: bool
kind: command | pytest | file | api | browser | database_readonly | artifact | manual_review | custom_registered
check_ref: name of an allowlisted check or registered check implementation
parameters: validated JSON payload (no arbitrary executable expression)
expectation: structured expected result, not only vague prose
owner: agent ID or human
status: pending | running | passed | failed | stale | blocked | waived | abandoned
evidence_ids: list
risk_tier: low | medium | high | critical
approval_policy: automatic | ask_user | denied
created_from_request_revision
last_definition_digest
```

Do not treat `waived` or `abandoned` as passed. A waiver is a visible owner decision; it should normally make the final scope `partial` unless the criterion is explicitly optional or the user approves a contract revision that removes it.

### 5.3 Task node

Suggested states, influenced by upstream's orchestration model:

- `WAITING`: dependencies are incomplete.
- `READY`: dependencies pass and the task can be dispatched.
- `IN_FLIGHT`: one or more worker handles are active.
- `RETURNED`: a worker returned a result, not yet trusted.
- `VERIFYING`: gates/evidence are being checked.
- `VERIFIED`: own required gates pass at the current input/artifact revision.
- `BLOCKED`: cannot continue without a dependency, approval, credential, unavailable service, or human decision.
- `ABANDONED`: explicitly stopped without success, with a reason.
- `FAILED`: task or check failed and has no remaining permitted retry.

Legal transitions must be centrally validated. An LLM or individual subagent must not write arbitrary status strings directly into the database.

### 5.4 Evidence record

Evidence is first-class, not just a line in chat.

Store:

- evidence ID and related scope/task/gate;
- evidence type and check implementation version;
- result status and structured output/summary;
- artifact references (logs, test reports, file hashes, screenshots, API responses) with size limits;
- start/end times and elapsed time;
- exit code or structured result;
- stdout/stderr fingerprints and truncated safe preview;
- relevant file/directory/artifact hashes;
- gate definition digest and check configuration digest;
- execution context such as working directory, selected shell, Python executable, relevant environment keys (never secret values), dependency revision, and repository commit when available;
- agent/worker identity and tool invocation IDs;
- approval record ID, if applicable;
- `freshness`: `current`, `stale`, `unknown`;
- reason for invalidation or failure.

### 5.5 Contract inventory

For each request requirement, record:

- requirement ID and exact intent;
- whether it is mandatory or optional;
- current owner/task;
- observable used to verify it;
- its status and evidence reference;
- request revision in which it was introduced;
- notes for accepted amendments/removals.

This closes a common failure path: the agent implements the easiest visible portion of a broad request and quietly forgets a secondary constraint.

---

## 6. Feature P0 — Acceptance contract before execution

### Objective

Make the agent describe the proof of completion before it starts expensive or risky work.

### Workflow

1. Receive the original request and classify scope/risk.
2. Identify all independently required outcomes and explicit constraints (platform, language, no feature removal, file-output requirement, online/offline constraints, budget, user permissions).
3. Convert each into a gate with an observable.
4. Mark requirements that cannot be objectively checked as `manual_review` and say why.
5. Verify that every required acceptance item is assigned to a task/owner and has a validation observation.
6. Reject or repair contracts with vague gates such as “works correctly,” “is advanced,” “finish implementation,” or “tests pass” without naming which tests/outcomes.
7. Persist the contract and show it in the UI for substantial work, possibly in compact form.
8. Only then allow task execution to begin.

### Automatic contract validation

Implement deterministic checks for:

- no acceptance gates at all;
- duplicate IDs;
- required outcome not assigned to any task;
- required gate with no verification method;
- command gate without a registered check or approved command policy;
- missing expected result;
- vague success language without measurable observation;
- task has no expected output/artifact;
- parent has children but no integration criterion;
- declared feature-removal prohibition is not preserved as a root constraint;
- contract changed but revision/digest did not change.

Use model-based review to improve the contract, but use deterministic validation for invariants. The model should not be the sole validator of its own plan.

### User experience

For small asks, Alpha can create the contract silently and keep working. For a multi-file implementation, research project, long task, database mutation, or multi-agent swarm, display a collapsible plan/gate summary and ask the user only for truly consequential decisions or permissions. If the user has enabled autopilot, Alpha may proceed with safe, low-risk choices, but it should never infer blanket permission for destructive operations.

### Acceptance tests

- A task like “fix the bug and verify it” produces at least one implementation criterion and a real regression gate.
- A task requiring a downloadable `.md` creates a file-existence/size/non-empty check and a linkable artifact record.
- A request containing multiple explicit requirements produces one or more gates for every requirement.
- A vague model-produced plan is rejected or routed for correction rather than immediately dispatched.

---

## 7. Feature P0 — Bounded task tree/DAG and specialist allocation

### Objective

Turn planning into independently executable work without letting decomposition create overhead or conflicts.

### Decomposition contract

Every node must define:

- objective and why it is a separate unit;
- inputs and dependencies;
- concrete expected outputs;
- acceptance gates;
- skill/specialist/model/tool requirements;
- allowed tools and side-effect tier;
- target and hard budget (wall-clock, model calls/tokens if known, tool calls, retries);
- ownership scope for file-writing tasks;
- integration owner and parent gate;
- recovery policy.

### Scheduler rules

1. Only nodes whose dependencies are verified become `READY`.
2. A node may run in parallel only if its work is independent enough and it has disjoint write scope or a deliberate isolation strategy.
3. Use a configurable concurrency limit; default conservatively for Alpha's 8 GB RAM environment and permit per-task override.
4. Use rolling scheduling: whenever a leaf becomes verified, re-evaluate dependency readiness and start newly ready work if resources permit.
5. Continue unrelated safe tasks after one leaf fails, but do not let failed prerequisites be silently bypassed.
6. Retry transient failures under a bounded policy; do not retry a deterministic failure indefinitely.
7. A worker's summary is a candidate output. Its parent reads the actual artifact and runs its own integration gate.
8. For tasks that touch a shared file, use serialized execution, explicit merge tasks, or Git worktrees; a path lease alone is not isolation.
9. If a launch wave partially fails, persist launched handles and failed launches. Do not fabricate missing worker handles. Require recovery or explicit abandonment before declaring the wave terminal.
10. Keep scheduling state outside model context so a context reset does not erase the actual status.

### Choosing agents

Do not create subagents solely to increase agent count. Dispatch based on task needs, e.g. research, implementation, independent code review, test design, security check, UI verification. Each agent gets only the context it needs plus the contract and shared conventions. If two agents independently produce competing solutions, a parent/resolver task compares them against the same gate contract and chooses/merges based on evidence, not confidence.

### Tree depth and fan-out safeguards

- Enforce a default maximum tree depth and branching factor, both configurable.
- Stop decomposing a node when a single worker can complete and validate it efficiently.
- Avoid leaves with outputs that overlap or cannot be objectively distinguished.
- Track planning overhead versus execution benefit.
- Do not allocate each child the full uncapped parent budget. Every child shares a scope-level hard budget with per-node caps.

---

## 8. Feature P0 — Verifiable gates and honest completion states

### Main rule

A gate is complete only if its registered check ran successfully under the current definition and its expected structured result matched. A model's natural-language assertion is not gate evidence.

### Initial check types

Implement in this order:

1. `pytest` / test runner check.
2. `command` check against a named registered command and validated arguments.
3. `file` check: expected path exists, is regular file, size is nonzero, parseable content, optional content requirements/hash.
4. `artifact` check: generated artifact opens/parses and conforms to requested type.
5. `api` check: request/response schema, expected status and assertions; keep secrets out of logs.
6. `http/browser` check: target result/DOM/screenshot observation, when Alpha has a stable browser adapter.
7. `database_readonly` check: execute only approved, read-only queries against configured connections.
8. `manual_review` check: explicit structured checklist and reviewer identity; never pretend it was automated.

Each check reports more than a bool: `passed`, `failed`, `blocked`, `timed_out`, `not_run`, or `invalid_configuration`, along with output/evidence. `not_run` is never a pass.

### Command checks and safety boundary

Unlazy's check format allows shell code and warns that the command inherits local permissions. Alpha should be stricter by default:

- Prefer a named check such as `python -m pytest tests/test_foo.py -q` registered in policy rather than raw shell text from the LLM.
- Validate executable and arguments separately; avoid command-string concatenation and shell interpolation.
- Use `subprocess` argument arrays with `shell=False` for ordinary execution.
- Enforce working-directory boundaries, timeout, output limits, process-tree cleanup, and cancellation.
- Use per-check allowlists for filesystem read/write scope, network access, environment variables, and executable paths.
- For arbitrary repository scripts, require explicit review/approval unless the workspace trust policy already permits them.
- Consider a container or restricted OS account for untrusted code. A Python subprocess is not automatically a sandbox.
- Never record secret values in approval digests or log output. Only include safe environment identity metadata.
- Revoke/invalidate approval if a check's command, arguments, working directory, toolchain, or policy changes.
- Warn before checks that perform network access, delete files, change databases, install packages, write to external services, or alter system settings.

### Expected result definition

An expectation should be structured when possible:

```json
{
  "exit_code": 0,
  "required_markers": ["3 passed"],
  "forbidden_markers": ["ERROR", "FAILED"],
  "artifact_exists": "reports/pytest.xml"
}
```

This is an illustrative shape only; design a strict schema and check-specific contract. Avoid “success” markers that a command prints before assertions complete. A check should emit its pass marker only after all assertions have run.

### Final completion invariant

A scope can be `complete` only when:

- each mandatory contract requirement maps to current passing evidence;
- each required leaf is verified;
- all parent integration checks pass;
- no required dispatch wave is unresolved;
- no mandatory gate is `pending`, `running`, `failed`, `stale`, `blocked`, `waived`, or `abandoned`;
- no requested output is missing;
- the original request has been reconciled against the current contract and final artifacts;
- all assertions in the final report are grounded in recorded evidence.

A blocked/abandoned required task leads to `partial`, `blocked`, or `handoff_required`, never `complete`.

---

## 9. Feature P1 — Evidence freshness and re-verification

### Problem

A test can pass at 10:00, code can change at 10:02, and Alpha can still report “all tests passed” at 10:03 even though that evidence refers to an older version of the code. Gate definitions can also change without their prior evidence meaning the same thing.

### Proposed model

Create a versioned digest over the canonical gate definition and relevant execution identity. At minimum bind:

- gate kind and check implementation version;
- canonical check parameters / command and expectation;
- working directory and policy ID;
- relevant file inputs or declared artifact identities;
- toolchain identity/version where available;
- current task/contract revision.

Record result details after the run:

- exit status / structured outcome;
- bounded output digest or fingerprint;
- artifact fingerprints;
- timestamps and duration;
- environment metadata safe to record;
- evidence schema version.

A definition digest detects definition drift; it does **not** prove that every transitive dependency is unchanged. For a test command, a high-assurance run should either hash the relevant source/dependency inputs or simply rerun the test. Avoid representing a hash of a command string as proof that the code it executes is identical.

### Staleness policy

Evidence becomes stale if:

- the gate definition or expected result changes;
- the checked artifact or relevant source changes after the check;
- the check implementation/toolchain version changes in a way that affects the result;
- an input is unknown or cannot be tied to the evidence with adequate confidence;
- the run was interrupted or the evidence payload was truncated/invalid;
- a required parent integration changed after verification.

When evidence is stale, return the gate to `pending`/`stale` and rerun it. Never convert malformed or missing evidence to success. Store old evidence for history instead of deleting it.

### Reverify modes

- `status`: non-executing status display; shows staleness but does not claim to prove tests still pass.
- `verify`: run eligible approved checks that are not current or have not passed.
- `reverify`: rerun all required runnable gates at the relevant verification boundary, including previously passed checks.
- `scope verify`: verify leaves, then branches, then root, with the original contract reconciliation last.

### Efficient verification policy

For fast iteration, use incremental validation during implementation. At milestone/finalization boundaries, rerun all mandatory gates that are safe and available. If full re-verification is too costly, Alpha must state the exact checks not rerun and downgrade the completion status accordingly; it cannot imply a full verification pass.

---

## 10. Feature P1 — File ownership, agent coordination, and reliable dispatch

### Ownership contract

Before two coding agents run concurrently, require each to declare a repository-relative `OWNS:` scope that lists complete file/path boundaries, for example:

```text
OWNS:
  - alpha/completion/gates.py
  - tests/completion/test_gate_execution.py
```

Then:

- normalize paths and reject paths escaping the workspace;
- detect exact and conservative parent/child path conflicts;
- refuse overlapping writes unless the work is isolated or deliberately serialized;
- re-check changed files after the worker returns (because declared ownership can be violated);
- track claims with leases and expirations to recover after a crashed worker;
- release leases only through an auditable transition;
- treat leases as coordination, not security boundaries.

For broader coding tasks, prefer a dedicated Git worktree per worker/branch and a separate merge/integration task. Don't let several agents write to one working tree simply because their prompts asked them not to overlap.

### Dispatch record

For each launch attempt, persist:

- dispatch wave ID;
- scope/task/agent ID;
- exact input-contract revision;
- host session/job handle once received;
- requested and actual start time;
- launch result;
- attempt number and retry reason;
- worker terminal state and returned artifact references;
- cancellation/timeout details;
- whether the result was verified and integrated.

A task may not be considered `IN_FLIGHT` merely because the parent requested a launch. The executor/host adapter must confirm that it actually started or failed.

### Recovery

At Alpha startup/reconnect:

1. Query persisted non-terminal tasks and dispatch records.
2. Ask host adapters whether known sessions are active, finished, or unknown.
3. Reconcile artifacts and event history.
4. Retry only tasks that are safe/idempotent or explicitly marked retryable.
5. Treat unknown side-effect status conservatively; do not duplicate payment, deletion, deployment, or other non-idempotent actions automatically.
6. Mark lost/irrecoverable tasks `BLOCKED`/`HANDOFF REQUIRED` with a reason, not `VERIFIED`.

---

## 11. Feature P1 — Final request reconciliation and completion guard

This is one of the highest-value features for Alpha. It directly addresses the difference between “the model returned a message” and “the user asked for work and the work actually succeeded.”

### Final reconciliation algorithm

Before producing the final response:

1. Re-read the original user request snapshot and latest approved amendments.
2. Enumerate all explicit deliverables, non-negotiable constraints, and requested validation items.
3. Compare these against the versioned contract inventory.
4. Flag request requirements missing from the inventory.
5. Verify every inventory item has a current owner/status/evidence or an explicit non-completion reason.
6. Validate every claimed output (file path, API action, code change, message sent, test run) against actual evidence.
7. Run required end-to-end and regression gates that are safe and configured.
8. Determine the truthful outcome status.
9. Generate the response from this outcome and evidence, not from the worker's self-assessment.

### Final status semantics

- `COMPLETE / VERIFIED`: all mandatory criteria have current evidence.
- `PARTIAL`: useful work is done, but a required item is incomplete or not verified.
- `BLOCKED`: a dependency, permission, unavailable service, or decision prevents safe continuation.
- `FAILED`: execution or required checks failed with no remaining permitted retry.
- `HANDOFF REQUIRED`: a worker/dispatch/gate was abandoned or state is unrecoverable and needs human intervention.
- `CANCELLED`: user or policy explicitly cancelled it.

Do not report a full completion badge for `PARTIAL`, `BLOCKED`, `FAILED`, or `HANDOFF REQUIRED`.

### Honest response renderer

The response renderer should tell the user:

- what was changed/created;
- what was actually verified;
- which checks ran and their outcomes;
- what remains unverified or blocked;
- any user approval still required;
- links to produced artifacts and logs that are safe to expose.

Never say “all tests pass” if only one selected test ran. Never say “deployed” because a deployment command was started; require a health probe or deployment result. Never say “file created” unless the final path exists and passes a content/format check.

---

## 12. Feature P1 — Completion hook / exit guard for Alpha

Unlazy has optional host-specific Stop-hook behavior. Alpha should implement the principle at a platform-neutral boundary instead of depending on a Claude Code hook.

### Placement

Make the finalization service the only component authorized to promote a scope to `COMPLETE`. Agent adapters may return a completion candidate, but the finalization service checks gates and evidence before accepting it.

### Guard logic

- If mandatory gates are incomplete, return a structured “completion blocked” result to the orchestrator.
- The orchestrator may continue work, retry checks, request approval, or produce a partial response.
- Do not loop indefinitely. Apply a bounded retry/no-progress policy and explain when the agent cannot make progress.
- Detect **semantic** progress, not cosmetic edits: changes to gate status/evidence/dispatch state count; rewriting comments or reformatting a plan does not.
- If a bounded number of attempts produce no meaningful progress, emit a `HANDOFF REQUIRED` diagnostic or honest partial result instead of trapping the user in an endless agent loop.
- Support explicit cancellation and user's choice to receive a partial report.

Do not blindly copy an exact “six blocked stops” threshold from Unlazy. Choose a configurable policy based on Alpha's task timeout, risk tier, and model budget, and test that it prevents both premature exits and infinite loops.

---

## 13. Feature P1 — Budgeting, attention discipline, and resource-aware execution

Alpha needs completion discipline without wasting its limited RAM, CPU, tokens, API quotas, or user's time.

### Required budgets

At scope and child levels track where available:

- wall-clock deadline;
- model calls, input/output tokens and API spend (only if provider reports them reliably);
- tool calls;
- browser requests;
- retries per task/check;
- concurrent agents;
- process CPU/memory and output size limits;
- number/depth of generated tasks;
- user approval boundaries.

Not all providers expose exact token or cost metrics. Record `unknown` instead of inventing an exact figure.

### Model/tool policy

- Use cheaper/local models for routine decomposition, checklist generation, log summarization, and lightweight checks when quality is adequate.
- Reserve higher-capability models for complex planning, ambiguous acceptance contracts, difficult debugging, and final independent review.
- Use independent verifier agents selectively for high-risk/high-impact changes, not for every simple action.
- Prefer deterministic tests and programmatic checks over repeated LLM self-critique where such tests exist.
- Load only the relevant reference/context into child workers and avoid sending the whole conversation and entire repository to every agent.
- Set a maximum repair loop count; after it is exhausted, capture artifacts and hand off honestly.
- Stop exploration when the required evidence is obtained, new investigations have diminishing value, or budget/deadline is reached.

### Resource behavior for Alpha's modest Windows machine

- Default to low concurrency and increase only when measured CPU/RAM/network constraints permit.
- Prefer process reuse and event-driven status checks over rapid polling.
- Stream/rotate logs rather than retaining unlimited stdout in memory.
- Use bounded outputs and store large artifacts on disk with references in the database.
- Ensure cancellation kills owned child processes where possible, not merely the Python wrapper.

---

## 14. Security and failure handling

### Trust boundaries

Treat model-generated plans, shell commands, file paths, test output, web pages, repository content, plugin responses, and worker summaries as untrusted input until validated. A gate system can itself become a tool-execution attack surface if the agent writes arbitrary commands into the acceptance file.

### Required protections

- Strict schema validation and fail-closed handling for malformed contract/gate state.
- Allowlisted check types and constrained command parameter schemas.
- Canonical path resolution and workspace-boundary enforcement.
- Subprocess timeouts, output byte limits, process-tree cleanup, and cancellation.
- Separate approval from execution; persist who/what approved and exactly which check definition was approved.
- Approval invalidation when bound command/context/policy changes.
- Secret redaction for logs and user-visible tool diagnostics; avoid capturing whole environment variables.
- Safe display of untrusted stdout/stderr and artifact names; strip terminal control sequences when needed.
- Restrict external network and write side effects according to tool/risk policy.
- Optional Docker/OS account/worktree isolation for untrusted repository commands.
- Atomic persistence and serialized updates so two agents cannot corrupt the same ledger state.
- Explicit audit events for manual waiver, gate abandonment, retry, requeue, cancellation, and permission changes.

### Failure classes and required behavior

| Failure | Alpha behavior |
|---|---|
| Model failed to produce a usable plan | Repair contract once or twice within budget, otherwise ask/hand off; no task dispatch on invalid contract |
| Worker returned prose but no artifact | Mark returned but unverified; ask worker to produce artifact or fail leaf |
| Check exited non-zero | Capture evidence, fail gate, route to repair/retry policy |
| Check output exceeded limit | Reject/mark invalid evidence; do not truncate into false success |
| Gate/parser format invalid | Fail closed and show diagnostic; never interpret invalid input as all-passed |
| Stale check evidence | Re-run or downgrade final status |
| Agent wrote outside declared paths | Flag violation; isolate/review; don't silently accept it |
| Dispatch launch failed | Record failed launch and handle; reschedule only under allowed retry policy |
| Process crashed mid-task | Reconcile persisted state and host sessions; resume idempotent tasks only |
| Same file modified by concurrent agents | Pause/serialize/merge in isolated worktree; execute integration tests afterward |
| User approval denied | Mark blocked/denied and give safe alternatives, not failed or complete |
| Retry budget exhausted | Preserve evidence and expose handoff/partial status |
| No progress loop | Terminate safely with diagnostics and explicit incomplete status |
| Final request contains an omitted condition | Add it to reconciliation findings; do not claim full completion |

---

## 15. User interface and observability

Alpha already aims to make agent activity and failures visible. Completion discipline should strengthen that system rather than create a separate hidden debug interface.

### Task detail panel

Display:

- task objective and status;
- parent/child task tree or DAG;
- assigned agent and host handle where safe;
- dependency blockers;
- declared file scope and conflicts;
- progress based on verified gates, not tokens emitted;
- gate list with expected result, latest status, evidence freshness, runtime, retry count;
- last failure and safe expandable output;
- explicit approval request if needed;
- current budget/limits when available;
- final integration state and remaining work.

### Event model

Emit structured events such as:

- `contract.created`, `contract.revised`, `contract.validation_failed`;
- `task.ready`, `task.dispatched`, `task.started`, `task.returned`, `task.verified`;
- `dispatch.launch_failed`, `dispatch.abandoned`;
- `gate.started`, `gate.passed`, `gate.failed`, `gate.stale`, `gate.blocked`;
- `evidence.created`, `evidence.invalidated`;
- `ownership.claimed`, `ownership.conflict`, `ownership.released`;
- `scope.reconciliation_failed`, `scope.finalized`, `scope.handoff_required`.

Each event should have `event_id`, `scope_id`, optional `task_id`/`gate_id`, timestamp, producer, schema version, and redacted structured payload. The existing Alpha logger should receive these events. UI, Sentinel/monitoring agents, and diagnostic tools should read from the same canonical event history rather than each inventing different status.

### Metrics

Track:

- percentage of `COMPLETE` tasks that pass a post-completion audit;
- false-completion incidents (claimed complete without full current evidence);
- percentage of task requirements represented by gates;
- gate pass/fail/stale rates;
- regression escapes after claimed completion;
- task restart/recovery rate;
- worker/dispatch failures and recovery time;
- median and p95 task duration by task class;
- cost/model calls/bytes/tool calls per successful task when measurable;
- extra verification cost compared with the rate of defects caught.

Use these metrics to tune strictness rather than optimizing for raw agent count or number of tool calls.

---

## 16. Phased implementation roadmap

Implement in small, testable increments. Keep existing Alpha features and routes intact; add the subsystem behind a feature flag, and do not force all requests through it until the core has passed regression testing.

### Phase 0 — Repository audit and baseline (required first)

**Goal:** Know Alpha's actual integration surfaces before making changes.

Work:

1. Map request handling, planner, LangGraph flow, tools, task persistence, subagent launch, cancellation, and final answer generation.
2. Identify the current canonical logger/event system and existing debug/failure history.
3. Find all places where a model response or worker return currently becomes “done.”
4. Map current test runners, artifact storage, state schema, and feature flags.
5. Identify existing retry loops and stop conditions to avoid duplicate retry systems.
6. Record baseline false-success cases and regression test coverage.
7. Produce a compatibility map from each module in this plan to the actual Alpha codebase.

**Exit gates:**

- Architecture map reviewed.
- Existing task state transitions documented.
- No duplicate persistence/event system proposed without a reason.
- Baseline tests recorded and reproducible.

### Phase 1 — Contract and state machine (P0)

Implement:

- Pydantic/domain schemas for scope/task/gate/evidence;
- contract builder and deterministic validator;
- central legal-transition state machine;
- persistence/repository integration;
- structured completion events;
- unit tests for malformed input, duplicate IDs, missing gates, illegal transitions, and restart-safe persistence.

**Exit gates:** Invalid or incomplete contract cannot be dispatched; a task returned by a worker cannot become verified without gate evaluation.

### Phase 2 — Minimal runnable gate registry (P0)

Start with Python tests, file/artifact checks, and registered command checks. Add timeouts/output limits, cancellation, structured results, safe logs, and explicit command policy. Do not begin with arbitrary shell execution from Markdown.

**Exit gates:**

- Tests can run and capture fresh evidence.
- A nonzero exit cannot pass.
- Missing or malformed evidence cannot pass.
- Over-limit output cannot be truncated into a successful match.
- A generated `.md` or other requested file can be checked for existence, size, and parseability.
- Gate command/config changes make prior evidence stale.

### Phase 3 — Completion guard and request reconciliation (P0)

Implement the finalization service as the only path to `COMPLETE`. Add original-request reconciliation, mandatory/optional criteria, truthful final statuses, and artifact verification.

**Exit gates:** A test worker that returns “done” without passing checks remains unverified; an omitted request condition prevents complete status; partial results can still be reported accurately.

### Phase 4 — LangGraph orchestration integration (P0/P1)

Add graph nodes/edges for plan validation, gate execution, repair, verification, finalization, and handoff. Ensure graph checkpoints contain stable IDs and references, not unbounded test output. Support graceful cancellation and continuation after restart.

**Exit gates:** A run resumes from persisted state without losing verified evidence or blindly repeating non-idempotent work. Existing normal chat flow remains intact.

### Phase 5 — Multi-agent DAG scheduling and ownership (P1)

Add dependency readiness, bounded fan-out, launch-wave records, ownership/path collision detection, lease expiry, rolling dispatch, parent integration, and structured abandonment.

**Exit gates:** A dependency cannot run before its prerequisites; conflicting writes are rejected/serialized/isolated; failed launch waves are visible; a parent requires integration gates.

### Phase 6 — Evidence freshness and reverify modes (P1)

Add gate definition digests, artifact identity/fingerprints, staleness detection, `status`, `verify`, `reverify`, and root re-verification. Implement tests that deliberately change code, gate parameters, or artifacts after an initial pass.

**Exit gates:** Stale evidence never satisfies a current mandatory gate; status is non-executing; reverify actually reruns all specified checks.

### Phase 7 — UI, monitoring, and diagnostics (P1)

Add task tree, gates, evidence, logs, approvals, budgets, failure states, and final reconciliation results to Alpha's current UI and telemetry system. Hook Sentinel/monitoring agents to event data. Ensure all tool and subagent failure paths emit consistent, correlated event IDs.

**Exit gates:** User can identify exactly why the run is incomplete and what happened at the last failed step; no sensitive environment or credential value leaks in UI/logs.

### Phase 8 — Security hardening and performance tuning (P1/P2)

Exercise path traversal, shell injection, secret redaction, symlink/path identity issues, simultaneous gate writes, process-tree termination, lease expiry, task restart and stale evidence. Benchmark with and without the layer on representative real Alpha tasks.

**Exit gates:** All security/regression tests pass; resource consumption is bounded; the system can be disabled with a feature flag without breaking existing agents.

### Phase 9 — Optional skill/command integration (P2)

Only after the core works, add user-facing aliases such as `/verify`, `/thorough`, `/task-status`, `/reverify`, or a reusable `UNLAZY.md`/skill instruction if Alpha already supports skills and slash commands. Commands must call the same Completion Engine; do not duplicate business logic in prompts.

---

## 17. Suggested first-release scope

Keep the first release deliberately small and valuable:

1. Acceptance contract and contract validation.
2. Persisted task/gate/evidence state.
3. `pytest`, file/artifact, and registered-command gates.
4. A finalization guard so only verified root tasks become `COMPLETE`.
5. Original-request reconciliation.
6. Stale-evidence invalidation and rerun.
7. Structured events and visible failure explanations.
8. Feature flag and tests proving existing Alpha workflows still function.

Do **not** start by building every feature in one PR: dynamic autonomous swarm generation, a huge visual graph editor, arbitrary shell DSL, distributed worker cluster, a new database service, advanced benchmarking UI, and multiple host-specific hooks can wait. First prove that Alpha stops falsely claiming completion and can explain exactly which gate failed.

---

## 18. Test strategy and acceptance matrix

### Unit tests

- Contract parsing/normalization and duplicate identifiers.
- Requirement-to-gate completeness checks.
- Illegal state transitions and status invariants.
- Check registry argument validation.
- Timeout/exit handling/output-limit enforcement.
- Gate-definition digest determinism.
- Evidence freshness/invalidation after a file or contract change.
- Safe redaction and control-character stripping.
- Path canonicalization and workspace escape rejection.
- Scheduler readiness and dependency cycle rejection.
- Ownership conflict matching and lease expiry.
- Final report claim filtering.

### Integration tests

1. **False completion:** worker says task is done but required artifact is missing → scope is not complete.
2. **Regression check:** code defect is fixed, a test passes, code is then modified back to defective state → old evidence becomes stale and root cannot complete.
3. **Request omission:** one of several explicit user requirements is absent from the plan → contract validation or final reconciliation fails.
4. **Parent integration failure:** all child tasks pass independently but their interface does not work → parent/root fails.
5. **Subagent launch failure:** second worker fails to start after first starts → dispatch wave is recorded accurately and root cannot claim all work was launched/finished.
6. **Concurrent write conflict:** two agents claim overlapping paths → system rejects, serializes, or isolates them before launch.
7. **Restart recovery:** Alpha restarts while a worker runs → state reconciles and does not duplicate unsafe side effects.
8. **Approval changed:** command parameters or working directory change after approval → approval is invalidated.
9. **Command injection:** malicious path/argument cannot break out of the registered check or workspace boundary.
10. **No-progress loop:** model repeatedly edits the plan without passing any gate → bounded stop with useful handoff.
11. **Downloadable output:** required `.md` file is absent/empty/invalid → gate fails; a verified file produces a real artifact reference.
12. **Partial report:** one optional or required piece is blocked → Alpha describes what did and did not finish without labeling it complete.
13. **Cancellation:** user cancels; active child process is terminated where possible and scope transitions to `CANCELLED` or an honest terminal partial state.
14. **Output too large:** test output exceeds configured limit → evidence is invalid/failed and cannot pass by truncation.
15. **Legacy workflow regression:** Alpha's simple chat, tool calls, bot mode, and existing coding workflow continue to work when the feature flag is disabled and in the selected enabled modes.

### End-to-end evaluation

Create a small but varied benchmark suite from real Alpha task classes: coding bug fix, multi-file feature, research/report, generated file, API check, browser workflow, and multi-agent implementation. Run the same tasks before/after the feature.

Evaluate:

- false-completion rate;
- requirement coverage;
- escaped defects;
- recovery from failed/stale gates;
- task success rate;
- elapsed time and model/tool calls;
- user-visible diagnostic usefulness;
- compute/memory cost.

Do not claim a performance improvement solely because more gates or more agents ran. Report the actual measurement, the sample size, and the limitations.

---

## 19. Rollout, compatibility, and maintenance

- Ship behind `ALPHA_COMPLETION_ENGINE_ENABLED` or the existing feature-flag convention.
- Start in **observe-only mode**: generate contracts, record evidence, and surface what would block completion without forcing all current tasks into the new workflow.
- Compare observed false-completion candidates with actual outputs and tune noisy checks.
- Enable enforcing mode for coding, file generation, research deliverables, and explicit autopilot tasks first.
- Roll out to other tools only after their results can be tied to reliable evidence.
- Ensure the finalization guard cannot be bypassed by alternate final-answer routes; audit UI chat, bot, API, background runner, and subagent parent flows.
- Version schemas and provide a migration strategy for interrupted active tasks.
- Preserve historical evidence; avoid silently converting old unverified tasks to verified.
- Add CI coverage for supported Python versions and Windows behavior because Alpha is Windows-first.
- Keep docs close to code: module ownership, state transitions, check authoring rules, safety model, recovery playbook, and examples.
- Pin any upstream concepts/reference copy to a specific commit. The upstream README currently says the source targets `2.1.0` but does not identify that as a tagged release; see [README version status](https://github.com/Leonxlnx/unlazy#version-status) and [releases page](https://github.com/Leonxlnx/unlazy/releases).

---

## 20. Design decisions to make during Phase 0

These should be answered by inspecting the actual Alpha repository, not guessed in advance:

1. Does Alpha already persist task states and LangGraph checkpoints in a durable store? Extend that store if possible.
2. What component is the only reliable point through which every final answer passes? This is where the finalization guard must live.
3. Which agent runners provide a trustworthy host task/session handle and terminal state?
4. Which checks are currently safe to auto-run on Windows, and how are Python/test executables resolved?
5. Does Alpha use shared working trees or Git worktrees for parallel coding agents today?
6. How are secrets and provider keys stored and redacted in logs?
7. Which task types can be identified deterministically enough for automatic verification, and which require explicit manual review?
8. What can the current UI display without a large redesign?
9. What restart/resume guarantees already exist, and which side effects must be idempotent?
10. Which default time/token/concurrency budgets fit the actual machine and model providers?

---

## 21. Decision matrix: what to borrow, adapt, or skip

| Decision | Recommendation | Reason |
|---|---|---|
| Gate-before-work discipline | **Borrow** | High leverage against incomplete implementations and vague “done” claims |
| Decompose substantial tasks into verified leaves | **Borrow/adapt** | Matches Alpha's subagent/swarm architecture |
| Parent and root integration verification | **Borrow** | Prevents individually passing components from hiding integration failures |
| Re-run stale or all required gates before finalization | **Borrow** | Makes evidence current and claims defensible |
| Explicit states, audited failures, and handoff | **Borrow** | Supports Alpha's monitoring and restart-recovery goals |
| File ownership and dependency scheduling | **Borrow/adapt** | Useful for concurrent coding agents; leases alone are not isolation |
| Arbitrary Markdown `CHECK:` commands | **Adapt strongly** | Use a typed gate registry and allowlisted arguments by default |
| Node scripts as runtime | **Skip as core** | Alpha's runtime is Python; avoid a second runtime for the same logic |
| Claude Code-specific Stop hook | **Skip as core** | Use an Alpha-wide finalization guard; optional adapter can come later |
| Exact upstream no-progress threshold | **Adapt** | Configure and test to Alpha's lifecycle; prevent infinite loops without arbitrary blocking |
| Unbounded “effort multiplier” interpretation of tree depth | **Skip** | Can multiply token/time cost without proportional benefit |
| Assuming a green test proves all behavior | **Skip** | Every gate proves only the check it actually performs |
| Treating approval as a sandbox | **Never adopt** | Approval does not restrict ambient OS permissions |
| Benchmark or research claims without retained artifacts | **Skip** | Alpha should make only reproducible, evidence-backed improvement claims |

---

## 22. Definition of done for the Alpha implementation itself

This plan is implemented successfully only when all the following are true:

- [ ] Every substantial run can persist a validated acceptance contract before work begins.
- [ ] Contract validation finds missing/ambiguous requirements and prevents dispatch when the contract is invalid.
- [ ] The task DAG uses legal state transitions and survives restart.
- [ ] Agent returns do not automatically mean verified completion.
- [ ] Required gates run through safe, bounded, policy-controlled executors.
- [ ] Gate results create structured evidence and useful failure diagnostics.
- [ ] Artifact/gate changes invalidate stale evidence.
- [ ] Parent/root integration checks and original-request reconciliation run before finalization.
- [ ] The finalization service is the only route to the `COMPLETE` status.
- [ ] Required abandoned, blocked, failed, or unverified work can never be represented as fully complete.
- [ ] Parallel work is dependency-aware and can detect overlapping file ownership.
- [ ] Dispatch failures and partial launch waves are auditable and recoverable.
- [ ] No-progress and retry loops are bounded.
- [ ] Secrets, command execution, paths, output capture, and approval state have security tests.
- [ ] The Alpha UI/API explains failed and stale gates with correlated logs/evidence.
- [ ] Existing workflows pass regression tests with the feature disabled and in the intended enabled modes.
- [ ] A before/after evaluation measures accuracy, cost, and latency without unsupported improvement claims.

---

## 23. Upstream references

Primary source: [Leonxlnx/unlazy](https://github.com/Leonxlnx/unlazy) (MIT-licensed repository; inspect its current `LICENSE` and pin a commit before copying code).

- [README / overview, gate contract, shell behavior and orchestration](https://github.com/Leonxlnx/unlazy#readme)
- [SKILL.md — completion discipline and routing](https://github.com/Leonxlnx/unlazy/blob/main/SKILL.md)
- [CHANGELOG.md — version/status notes](https://github.com/Leonxlnx/unlazy/blob/main/CHANGELOG.md)
- [Gate specification](https://github.com/Leonxlnx/unlazy/blob/main/references/gates.md)
- [Depth Tree method](https://github.com/Leonxlnx/unlazy/blob/main/references/method.md)
- [Orchestration states and integration](https://github.com/Leonxlnx/unlazy/blob/main/references/orchestration.md)
- [Dispatch and launch waves](https://github.com/Leonxlnx/unlazy/blob/main/references/dispatch.md)
- [Parallel coordination limits](https://github.com/Leonxlnx/unlazy/blob/main/references/parallel.md)
- [Token economy guidance](https://github.com/Leonxlnx/unlazy/blob/main/references/token-economy.md)
- [Security model](https://github.com/Leonxlnx/unlazy/blob/main/SECURITY.md)
- [Validation/research limitations](https://github.com/Leonxlnx/unlazy/tree/main/research)
- [Releases](https://github.com/Leonxlnx/unlazy/releases)

## Final recommendation

Build the **Alpha Completion Engine** as a reusable Python layer around Alpha's current planner, LangGraph execution, agent dispatch, tool system, logging, and UI. Start with contract validation, typed runnable gates, evidence freshness, request reconciliation, and a hard finalization guard. Add DAG scheduling, file ownership, dispatch-wave recovery, and deeper resource/security controls only after the first release proves that the basic completion contract works.

The core success criterion is simple: **Alpha must not claim that a task is complete unless the task's required outcomes have been checked against current, recorded evidence.**
