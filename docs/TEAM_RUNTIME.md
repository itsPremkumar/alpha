# Team runtime: a team of declared specialists

A *team* is not a new lifecycle, a new plan type, or a new run record. It is an
ordinary [`SwarmPlan`](../backend/packages/harness/alpha/swarm/models.py) whose
task nodes carry a **declared capability requirement** and an assignment to a
**named specialist**. Everything else — the DAG, the leases, the retry and
backoff policy, the measured budget, the event journal, the checkpoint, the
aggregator, the terminal states — is the existing swarm runtime, reached
through the entry points that already exist.

This document is deliberately answer-first about what the runtime **cannot** do,
because the largest risk in this subsystem is a capability that reads as more
than it is. See [What this cannot do](#what-this-cannot-do).

## The four steps, and where each one lives

| Step | Owner | Code |
| --- | --- | --- |
| Goal → decomposition | `SwarmTaskDecomposer` (existing) | `swarm/decomposer.py` — each node now also declares the capability it requires |
| → assignment | **new** | `swarm/team.py::assign_specialists` — hard capability filter, recorded with reasons |
| Execution, honestly failed | `AsyncSwarmRunner` (existing) + **new** worker | `swarm/runner.py`, `swarm/worker.py::SpecialistSubagentWorker` |
| Collection & reconciliation | `SwarmAggregator` (existing) + **new** report | `swarm/aggregator.py::compose_team`, `swarm/team.py::build_team_report` |

Composition runs **inside** `SwarmCoordinator.create_swarm`, before leader
election and before `SwarmScheduler.validate_graph`. There is therefore no new
entry point: a plan created by the `swarm` model's `spawn`, by
`POST /api/swarms`, by `planning/bridge.py`, or by `AutonomousWorkTrigger` is
composed automatically, and `plan.metrics["team"]` plus the team section of
`plan.final_result` are already visible on `GET /api/swarms/{id}` and
`swarm(action="status")` because `_plan_to_wire` copies `metrics` verbatim.

## What makes a specialist a specialist

Not the name. A `SpecialistProfile` offers capability tags through the existing
[`alpha.capabilities.eligibility`](../backend/packages/harness/alpha/capabilities/eligibility.py)
vocabulary, and it executes through the real
[`SubagentExecutor`](../backend/packages/harness/alpha/subagents/executor.py) as
a named `SubagentConfig` — its own system prompt, its own tool allowlist, its
own turn budget. `agent_type` names a key of
`alpha.subagents.builtins.BUILTIN_SUBAGENTS` (`deep-architect`, `deep-debugger`,
`deep-security`, `deep-test-synthesizer`, `deep-performance`,
`deep-code-reviewer`, `bash`, `general-purpose`). An `agent_type` that names no
builtin **raises** rather than falling back to `general-purpose`: a specialist
that silently runs as a generalist is the exact claim this subsystem exists to
stop making.

Contrast with the pre-existing `EphemeralSubagentWorker`, which makes a single
model call with **no tools**. Whatever specialist name is on a task changes
nothing about what runs.

### Declared vs inferred requirements — and why the difference matters

`SwarmTaskNode` carries `capability_source`:

- **`declared`** — a caller or operator stated `capability_tags`. **Strict**:
  a specialist that does not cover *all* of them is refused, and the task goes
  unassigned with the missing tags named.
- **`inferred`** — the tags were read from the node's own directive by the
  keyword vocabulary. A **hint**: a specialist must cover at least one, and
  coverage then ranks the survivors.
- **`none`** — unconstrained. Not the same as "matches everything", and never
  reported as if it were a match.

The distinction is load-bearing and was not hypothetical. Inferred tags were
originally written into the declaration field, so a four-tag keyword hint read
as a hard conjunction and **zero of five nodes could be assigned**. See
[Measured behaviour](#measured-behaviour).

## The roster seam (what this expects from the `specialists:` catalogue)

The declarative `specialists:` configuration section is owned by the config
layer, so `alpha/swarm/team.py` does **not** read it. It defines the interface:

```python
from alpha.swarm.team import register_roster_provider

register_roster_provider(lambda: load_specialists_from_config(), source="config")
```

`provider` is any zero-argument callable returning a roster, as any of:

- `list[SpecialistProfile]`
- `list[dict]` — raw config entries, so the config layer need not import this module
- `dict[str, dict]` — a mapping keyed by id

A recognised entry key is `name` (or `id`), `role`/`title`, `mission`/`description`,
`capabilities` (list of `CAPABILITY_KEYWORDS` ids), `skills`, `status`
(default `active`; only `active`/`sleeping` are assignable),
`agent_type` (a `BUILTIN_SUBAGENTS` key), `system_prompt`, `tools`,
`model` (a name, or `inherit`), and `max_tasks`.

**What the catalogue must supply, and what happens if it does not:**

- A provider that **raises** → the plan is still constructed; the failure is
  recorded in `metrics["team"]["roster_provenance"]["error"]` with the reason.
- **No provider registered** (the state today) → every task keeps its existing
  swarm worker, `assigned == 0`, and the report says
  `no_roster_registered` under **What Remains Unknown**. This is the
  pre-team behaviour, and it is disclosed rather than silently presented as a
  team.
- A roster **larger than 256** → truncated, with the overflow listed.
- A **duplicate name** → the duplicate is dropped and named. Not merged: two
  entries claiming one identity would make the team a function of list order.
- A **nameless or malformed entry** → that entry is dropped with a reason; the
  rest of the roster survives.
- A **second owner registering** → `RosterConflictError`. Two configuration
  owners silently overwriting each other is how a team ends up executing under a
  roster nobody is looking at. Re-registering the *same* `source` is
  idempotent, so a config hot-reload is not punished.

`unregister_roster_provider()` exists so a test that registers a roster cannot
poison every test after it.

## Honest failure

`SpecialistSubagentWorker` raises, and the runner's existing exception path
does the rest: `mark_failed` (honouring `max_attempts` and retry backoff), an
incident in `SwarmIncidentManager`, and a `TASK_FAILED` / `TASK_RETRY_SCHEDULED`
event. It raises on:

- no assembled tool pool (and on a recorded assembly error, naming it);
- a non-`COMPLETED` status, carrying the real status, `stop_reason`
  (`token_capped` / `turn_capped` / `loop_capped`) and provider error text;
- a `COMPLETED` status with an empty result;
- an `agent_type` that names no builtin.

Token usage comes from the **child's** `token_usage_records` and is labelled
`usage_source: subagent_reported` or `unreported_by_provider`, so an absent
provider report never reads as a measured zero. Tool receipts travel as
evidence.

Preserved from the existing aggregator, and pinned by tests: a **downstream**
node whose dependency failed is failed by `fail_unrunnable_tasks` with an
`unrunnable: dependency failed…` message, which is deliberately *not* the
provider's error. Attributing a 500 to a node that never called a provider
would be its own kind of lie.

## The report

`build_team_report(plan)` is a **pure projection** — no state, no lifecycle,
recomputable at any time — answering, in order: who was on the team, what each
was asked, what each returned, what was accepted, and what remains unknown.

Two different lists, because they answer different questions and conflating
them is how a plan claims coverage it does not have:

- `tasks_without_a_specialist` — **derived from plan state**, always correct.
- `unassigned_at_composition` — the assignment decision's own record, with the
  required tags, the eligible pool, every rejected candidate and why. Empty
  when composition never ran, and the report then says
  `composition_not_recorded` rather than implying a decision was made.

`unknown` is enumerated, never inferred from the absence of complaints:
`no_specialists_executed`, `no_specialist_completed_work`,
`composition_not_recorded`, `no_roster_registered`, `failed_task`,
`acceptance_unverified`, `consensus_not_evaluated`, `consensus_not_approved`,
`unreconciled_conflicts`.

The Markdown rendering is a separate function from the projection, so a
rendering bug can never change what the report says. The rendered team section
is appended to `plan.final_result` and **not** to `deliverable_text`, which is
the input to the keyword quality gate — letting a new section move that gate
would silently move an existing status decision.

## Load bounds

`DEFAULT_MAX_TASKS_PER_SPECIALIST = 4`. A specialist is one agent context;
handing it thirty objectives is not parallelism, it is a single long serial run
wearing a team costume. A specialist may declare a tighter `max_tasks`, and the
tighter of the two wins. When a task cannot be placed because every eligible
specialist is at its cap, it is reported as unassigned **with the reason**,
never queued forever and never handed to a non-capable specialist.

## What this cannot do

Read this section before relying on any of the above.

1. **The HTTP routes are mounted, but reachability is not the same as
   usefulness.** `app/gateway/routers/teams.py` is registered in
   `app/gateway/app.py` beside the swarms mount, so `/api/teams/*` answers over
   HTTP. It was previously dead code, and the pin that said so has been inverted
   to `tests/test_team_routes.py::test_the_team_routes_are_mounted` so the mount
   cannot rot back. Note what that does *not* buy you: with no `specialists:`
   catalogue registered (item 3), the roster is empty and a report describes a
   plan that assigned nothing. A reachable route returning an honest empty
   result is not the same as a working team.
2. **No `swarm` tool action was added.** `swarm_tool.py` is not owned by this
   change, so there is no `swarm(action="team")`. The team is visible through
   the existing `status` / `metrics` actions and `GET /api/swarms/{id}`.
3. **No specialist exists until a catalogue registers one.** The `specialists:`
   config section does not exist yet. With nothing registered, a plan runs
   exactly as it did before this change.
4. **Nothing here is verified.** A completed task means a worker returned a
   result. `accepted` reflects only the deterministic acceptance overlay
   (`verify_acceptance_criteria`) — never a model's opinion of itself. The
   report never uses the word "verified".
5. **Requirements are keyword-derived and imperfect.**
   `infer_capability_tags` matches by **substring**, not word boundary, so
   `"ui_design"` is inferred from "req**ui**rements" and `"react"` from
   "**component**" — both measured. The vocabulary's own docstring accepts the
   false-positive direction as safe, so it was used unchanged rather than
   imposing a second lexical policy on another owner's module. The failure mode
   is a task going unassigned with a spurious tag named, never a
   mis-assignment.
6. **The real delegation stack is untested.** `tests/conftest.py` replaces
   `sys.modules["alpha.subagents.executor"]` with a MagicMock to break an
   import cycle, so the real `SubagentExecutor` / `SubagentResult` /
   `SubagentStatus` are not importable anywhere in the test suite. The worker
   is tested by substituting those module-level symbols; **no test exercises a
   real `SubagentExecutor`**, and a live gateway was not available to confirm
   it (see [Verification](#verification)).
7. **One specialist per run, shared tool pool.** A run assembles the tool pool
   once and threads it through every specialist; a catalogue entry cannot have
   its own pool.
8. **Sequestration, not collaboration.** Specialists are independent: they do
   not message each other mid-run, negotiate, or hand work to each other.
   Collaboration in this codebase is the message bus, the war room, and
   deliberation, not this subsystem.
9. **A takeover is not reported as a takeover.** Succession recovery
   (`SwarmIncidentManager`) can reassign a failed task to a bot and flip
   `worker_type` to `permanent_bot`; the report then files that task under
   `tasks_without_a_specialist`. The `SWARM_INCIDENT_RECOVERED` event carries
   the successor, not the team report.
10. **Single-process, exactly as before.** Composition adds no persistence. A
    `SwarmPlan` remains a local JSON checkpoint plus a JSONL journal, restart
    recoverable for one Gateway and not a shared lease repository.

## Mounting the team routes

**Done.** Two lines in `backend/app/gateway/app.py`:

```python
# 1. in the `from app.gateway.routers import (...)` block, beside `swarms`
    teams,

# 2. beside `app.include_router(swarms.router)`
app.include_router(teams.router)
```

The mount is pinned positively by
`tests/test_team_routes.py::test_the_team_routes_are_mounted`, which asserts the
`include_router` call is still present. The pin previously asserted the opposite
— that the router was *not* mounted — and was inverted when the lines landed.
A router module with no `include_router` call is the defect class this repository
keeps measuring, so un-mounting it now fails a test rather than silently turning
`/api/teams/*` into 404s.

## Measured behaviour

`roster` = six specialists drawn from the `CAPABILITY_KEYWORDS` vocabulary
(`researcher`, `architect`, `coder`, `tester`, `web`, `scribe`). Goal
*"Implement a sql migration and audit the backend service"*, `mode=hierarchical`
(the same mode as before this change):

```
task-research  ui_design                 -> web        cov=1
task-arch      react, system_design      -> architect  cov=1
task-worker-a  code_generation           -> coder      cov=1
task-worker-b  code_generation, ui_design-> coder      cov=1
task-qa        code_audit                -> tester     cov=1
```

Five of five nodes assigned, four distinct specialists. Before the
goal-stripping fix the same run assigned **0 of 5**: every node had inherited
the goal's four tags as a hard conjunction.

## Verification

```
cd backend; $env:PYTHONPATH="."
.venv/Scripts/python.exe -m pytest -q tests/test_team_composition.py    -> 31 passed
.venv/Scripts/python.exe -m pytest -q tests/test_team_execution.py      -> 21 passed
.venv/Scripts/python.exe -m pytest -q tests/test_team_routes.py         ->  7 passed
.venv/Scripts/python.exe -m pytest -q tests/test_swarm_engine.py \
  tests/test_swarm_advanced_features.py tests/test_swarm_v2_runtime.py \
  tests/test_harness_boundary.py tests/test_swarm_worker_execution.py \
  tests/test_swarm_fault_injection.py                                   -> 66 passed
.venv/Scripts/python.exe -m pytest -q tests/test_swarm_adaptive_topology.py \
  tests/test_swarm_deliberation.py tests/test_swarm_stigmergy_telemetry.py \
  tests/test_subagent_prompt_security.py tests/test_lead_agent_prompt.py \
  tests/test_subagent_routing_prompt.py                                 -> 156 passed
.venv/Scripts/python.exe -m ruff format --check <changed files>          -> 29 files already formatted
.venv/Scripts/python.exe -m ruff check <changed files>                   -> All checks passed
```

**Not verified:** no live gateway was reachable (`127.0.0.1:2026`, `:8001` and
`:3000` all refused connections in this worktree), so no HTTP request and no
`swarm` tool call was exercised end to end. Every result above is in-process.

## Invariants this change is responsible for

- **`plan.mode` == `plan.metrics["strategy"]["mode"]`.** Composition runs after
  `SwarmTaskDecomposer.decompose` and never writes `plan.mode`,
  `plan.metrics["strategy"]`, or any dependency. `_declare_capability_requirements`
  is called from `_build_plan`, so it re-runs on a strategy rebuild instead of
  being lost by one. Pinned by
  `test_assignment_does_not_touch_the_dag_or_the_recorded_strategy`.
- **The DAG the router measured is the DAG the scheduler validates.** Pinned by
  the same test plus `test_the_scheduler_is_untouched_by_composition` and
  `test_the_scheduler_still_validates_the_dag_before_a_team_run`.
- **Execution ≠ acceptance.** `assign_specialists` writes only `assigned_worker`,
  `worker_type`, `model_override` and capability fields — never `state`,
  `acceptance_status` or `verification`. Pinned by
  `test_assignment_never_writes_execution_or_acceptance_state` and
  `test_a_completed_task_whose_acceptance_fails_is_not_reported_as_accepted`.
- **Duplicate voters cannot produce a clean approval.** Untouched, and pinned
  by `test_duplicate_voters_cannot_produce_a_clean_approval`.

## Tests

- `tests/test_team_composition.py` — requirement derivation, the hard filter,
  the roster seam, coordinator integration, the report.
  `test_team_guards_bite` demonstrates each guard rejecting its input.
- `tests/test_team_execution.py` — failure honesty through the real runner and
  aggregator, the worker, and the reconciliation invariants.
- `tests/test_team_routes.py` — owner scoping, handler behaviour, and the
  not-mounted fact.
