# Prime-inspired capability audit

> Status: audit of the Alpha codebase against a private design note ("Alpha
> prime") describing the "Prime Agent" architecture: durable event streams,
> evidence-gated acceptance, worktree-isolated execution, multi-agent
> orchestration with a shared handle registry, and a continual
> self-improvement harness. The note is a working document and is **not part
> of this repository**, so it is described here rather than linked; each
> section restates the idea it checks against so this file stands alone.
>
> This document answers one question per capability: **is the idea actually
> implemented, partly implemented, or only documented?** Every
> `VERIFIED_PRESENT` verdict names a Python symbol *and* a test that exercises
> it, so the claim is checkable rather than asserted. Verdicts that cannot link
> both are `PARTIAL`, `DOCUMENTED_ONLY`, or `MISSING` by construction.

Related reading: [PRODUCTION_READINESS_INVENTORY](PRODUCTION_READINESS_INVENTORY.md)
is the authority on what ships; this file is the capability-by-capability
comparison against one specific design note.

## Verdict vocabulary

| Verdict | Meaning |
| --- | --- |
| `VERIFIED_PRESENT` | The behavior exists in code **and** a test in `backend/tests/` (or `frontend/`) exercises it. |
| `PARTIAL` | A seam exists and works for some cases, but the design note's full contract is not met. |
| `DOCUMENTED_ONLY` | The capability is described in docs/design notes but no runtime path implements it. |
| `MISSING` | Neither implemented nor documented as available. |
| `NOT_APPLICABLE` | The idea presumes a deployment shape Alpha does not have. |

## Summary

| # | Capability (from the design note) | Verdict |
| --- | --- | --- |
| 1 | Durable event stream, monotonic sequence, replay with gap detection | `VERIFIED_PRESENT` |
| 2 | Run/task state machine with terminal precedence and lease fencing | `VERIFIED_PRESENT` |
| 3 | Budget reserve/settle around side effects | `PARTIAL` |
| 4 | Structured tool-result envelope (`ok` / `error` / retryability) | `PARTIAL` (retry hint added) |
| 5 | Acceptance gate over evidence, not self-report | `VERIFIED_PRESENT` |
| 6 | **Automatic evidence collectors** (run a command, measure the result) | `VERIFIED_PRESENT` (this branch) |
| 7 | Per-run worktree isolation | `VERIFIED_PRESENT` |
| 8 | Planner / Implementer / Reviewer / Verifier role taxonomy | `PARTIAL` |
| 9 | `code_change` / `bug_fix` workflow templates | `VERIFIED_PRESENT` (this branch) |
| 10 | Versioned A2A envelope with `schema_version` | `PARTIAL` |
| 11 | Child handle API (`find_agents` / `collect` / `close`) | `PARTIAL` |
| 12 | Single depth/fan-out limit authority | `VERIFIED_PRESENT` (this branch) |
| 13 | Continual-learning harness (reflect → refine → snapshot) | `PARTIAL` |
| 14 | Artifact index with search | `PARTIAL` (path-addressed only) |
| 15 | Context compaction | `VERIFIED_PRESENT` (server-side; no model-facing tool) |
| 16 | Sandboxed Python REPL workbench | `VERIFIED_PRESENT` (default-off) |
| 17 | Task benchmark suite (`task_eval`) | `VERIFIED_PRESENT` |
| 18 | Retry classification and no-progress detection | `VERIFIED_PRESENT` |

---

## 1. Durable event stream, sequence numbers, replay — `VERIFIED_PRESENT`

The design note asks for an append-only event log with a monotonic sequence,
replay for late subscribers, and explicit gap detection when a reader fell
behind. Alpha's run/event plane implements exactly that.

- **Code:** `alpha.runtime.runs` (event append, sequence assignment, replay),
  surfaced over HTTP by the Gateway run routers.
- **Tests:** `backend/tests/test_run_event_store.py`,
  `test_run_event_stream_contract.py`, `test_run_events_endpoint.py`,
  `test_run_event_store_pagination.py`, `test_run_event_store_filter.py`,
  `test_run_event_store_by_run_index.py`, `test_durable_replay.py`.

The "gap" contract (a reader that skipped events learns *that* it skipped, not
silently missing history) is covered by the sequence assertions in those
suites. This is a genuine match with the design note, not a lookalike.

## 2. Run/task state machine, terminal precedence, lease fencing — `VERIFIED_PRESENT`

- **Code:** `alpha.mission.state_machine` (transition table and terminal
  precedence), `alpha.runtime.runs.manager` (lease ownership — a status
  update after the lease is lost is skipped rather than persisted).
- **Tests:** `backend/tests/test_mission_state_machine.py`,
  `test_durable_session_state_machine.py`, `test_run_manager.py`,
  `test_multi_worker_run_ownership.py`,
  `test_gateway_run_terminal_status.py`. Lease *fencing* at the attempt level
  (a late result from a reclaimed lease is discarded) is pinned by
  `test_workflow_leases.py::test_reclaiming_an_expired_lease_fences_out_its_late_result`
  and the admission fencing ticket in `test_fleet_control_enforcement.py`.

Terminal states are unreachable except through the acceptance path (see §5),
which is the same invariant the design note calls "no zombie runs".

## 3. Budget reserve/settle — `PARTIAL`

The design note wants a two-phase *reserve → spend → settle* around each side
effect, so a run cannot exceed its budget mid-flight.

- **Code:** `alpha.avo.budgets` implements budget accounting and guards
  (`BudgetLedger`: reserve, charge, release, reconcile — no `reset`, no
  `grant_more`).
- **Gap:** there is no reserve/commit protocol at the *tool call* boundary —
  budgets are checked, not held, so two concurrent effects can both pass a
  check before either settles.
- **Tests:** `backend/tests/test_avo_governance.py`,
  `test_avo_governance_layer.py` (the ledger), plus run-level token ceilings
  in `test_token_budget_scopes.py`.

## 4. Tool-result envelope — `PARTIAL` (improved on this branch)

The design note specifies one envelope carrying `ok`, a machine-readable error
code, and a `retry_after` hint, so callers never parse prose.

- **Code:** `alpha.agents.middlewares.tool_result_meta` stamps `status`,
  `error_type`, `duration_ms`, and — **new on this branch** —
  `retry_after_seconds`, measured from provider responses (`Retry-After`
  header, `retry after N seconds` prose) and clamped to
  `MAX_RETRY_AFTER_SECONDS`. The hint is present **only when measured**, so an
  absent field never means "zero seconds".
- **Tests:** `backend/tests/test_tool_result_meta.py` (including the
  retry-hint cases), `test_command_tool_result_semantics.py`,
  `test_web_fetch_error_shell_meta.py`.
- **Deliberate omission:** no separate `ok` boolean. `status` is already the
  envelope's single ok-fact; a derivable twin could disagree with it when a
  hand-written meta dict is constructed elsewhere in the codebase. The
  envelope is `PARTIAL` rather than `VERIFIED_PRESENT` for that reason: the
  design note's `ok` + `error_code` pair is represented as `status` +
  `error_type`.

## 5. Acceptance gate over evidence — `VERIFIED_PRESENT`

- **Code:** `alpha.mission.acceptance` — `evaluate_trusted_acceptance`,
  `EvidenceRecord`, `EvidenceKind`, `collect_artifact_digest`. A run's
  terminal success is written only after evidence passes; the model cannot
  self-report completion.
- **Tests:** `backend/tests/test_apex_acceptance_evidence.py`,
  `test_mission_acceptance_and_live_feed.py`,
  `test_run_acceptance_criteria.py`.

## 6. Automatic evidence collectors — `VERIFIED_PRESENT` (added by this branch)

The design note's central gap: acceptance needs evidence, *something* has to
produce it, and until now every evidence record was hand-written. This branch
adds `alpha.mission.collectors`.

- **Code:**
  - `Measurement` — either an `EvidenceRecord` (something was measured) or a
    `reason` with no record (`UNVERIFIED`); the `.measured` property means
    "carries a record", never "measured true".
  - `EvidenceCollector` (runtime-checkable `Protocol`) — any object with a
    `criterion: str` and a `measure() -> Measurement`; `collect_evidence`
    refuses anything that does not satisfy it, so a stray object cannot be
    skipped silently.
  - `CommandEvidenceCollector` — runs a `CommandCheck` through
    `alpha.mission.goalloop.gates.GateRunner` (reusing the existing
    `QualityGate`/`GateRunner` seam) with an injectable `ProcessRunner`, so
    tests never shell out.
  - `ArtifactEvidenceCollector` — measures an artifact digest via
    `collect_artifact_digest`.
  - `collect_evidence` — runs every collector once, refuses a malformed batch
    whole, and splits the outcome into `records`/`notes` without
    short-circuiting; `collect_and_evaluate` assembles those records into the
    `AcceptanceReport` `evaluate_trusted_acceptance` already accepts.
- **Honesty rules (pinned by tests):**
  - exit `0` → `measured=True`; a real non-zero exit → `measured=False`;
  - `TIMEOUT`/`ERROR`/empty command/no exit code/exit `126`/`127` →
    `UNVERIFIED` (the shell never ran, so "failed" would be a lie);
  - a collector that raises yields a note plus `UNVERIFIED`, never an
    exception that fails the run;
  - a non-`Measurement` return is a `NOTE_NOT_A_MEASUREMENT`, not evidence.
- **Tests:** `backend/tests/test_mission_evidence_collectors.py` (32 cases).

**Still open:** nothing *automatically* wires a collector into a run. The
seam exists and is exercised; hooking it to a workflow's acceptance criteria
is follow-up work (see "Deliberately not built").

## 7. Per-run worktree isolation — `VERIFIED_PRESENT`

- **Code:** `alpha.sandbox.worktrees`, `alpha.sandbox.worktree_strategy`,
  `alpha.workflow.worktrees`.
- **Tests:** `backend/tests/test_managed_worktrees.py`,
  `test_workflow_worktrees.py`, `test_worktree_strategy.py`,
  `test_workflow_worktree_limits.py`.

## 8. Planner / Implementer / Reviewer / Verifier — `PARTIAL`

The design note wants one role vocabulary shared by prompts, runtimes, and the
frontend. Alpha has **several overlapping vocabularies** with no single
reconciling type:

- `alpha.apex.agents.ApexAgentRole` (a `StrEnum`: `PLANNER`, `CODE_REVIEWER`,
  `SECURITY_REVIEWER`, `UX_REVIEWER`, …) — the APEX specialist roster;
- `alpha.enterprise.models.CSuiteRole` — the enterprise C-suite taxonomy;
- free-text role slugs recommended by `alpha.bots.organization`
  (`"architect"`, `"coder"`, `"reviewer"`, `"tester"`, …) and stored on bot
  profiles;
- `alpha.workflow.dynamic_decomposer` `assigned_role: str` — an untyped string
  on a decomposed task;
- frontend role chips (`frontend/src/lib/projects.ts` `roles: ["architect",
  "coder", "reviewer"]`, `enterprise.ts` `reviewer_bot`,
  `project-inspector.ts` `verifier_bot`).

Each is internally consistent and tested in its own suite, so none is wrong —
but a "reviewer" in APEX is not a type-compatible "reviewer" in a project
template, which is what the note is asking for. **No `VERIFIED_PRESENT`
verdict is possible**: there is no one symbol to link. Unifying them touches
`alpha.apex`, `alpha.enterprise`, `alpha.bots`, `alpha.workflow` and the
frontend, deliberately left out of this branch.

## 9. `code_change` / `bug_fix` workflow templates — `VERIFIED_PRESENT` (this branch)

- **Code:** `alpha.workflow.builtin_templates` ships both blueprints as
  `code_change` (plan → implement → test → review → accept) and `bug_fix`
  (reproduce → diagnose → fix → regression-test → review → accept), with a
  bounded repair loop, a declared `verification_cmd`, and a `GOAL_GATE`
  acceptance criterion written in the safe-AST expression form the runtime
  evaluates. They are DRAFT templates whose `provenance.source` is `builtin`,
  and `get_template_store()` seeds them on first construction.
- **Tests:** `backend/tests/test_builtin_workflow_templates.py` pins the graph
  shapes, the bounded retry, the provenance round-trip, and the lifecycle rule
  that separates a builtin draft (instantiable, tagged `[builtin blueprint,
  unverified]`) from a captured draft (refused) — plus seeding idempotence and
  the no-overwrite guarantee. The pre-existing captured-template lifecycle
  (`draft -> verified -> promoted`) stays covered by
  `tests/test_workflow_templates_and_improvement.py` and is unchanged.
- **Why a builtin draft may start but a captured one may not:** a captured
  draft is a graph taken from a run that has not been re-checked; starting it
  would lend a never-executed graph the appearance of authority. A builtin
  blueprint is code reviewed and unit-tested here, so it may start — while its
  description still says it is unverified, and reaching `promoted` remains
  evidence-gated for both (a blueprint cannot promote itself).

## 10. Versioned A2A envelope — `PARTIAL`

The design note requires `schema_version` on every agent-to-agent payload.
Alpha versions its **contracts** — `contracts/subagent_status_contract.json`
carries `"version": 2`, `contracts/run_event_stream_contract.json` `"version": 1`,
and the skill-review schemas require `schema_version` — and those shapes are
pinned by tests (`tests/test_subagent_status_contract.py::test_status_values_match_contract`,
`test_run_event_stream_contract.py`). What is missing is the version **on the
wire**: `alpha.subagents.status_contract` stamps no version field into a
payload, so a consumer cannot tell v1 from v2 at runtime, and there is no
compatibility negotiation. `PARTIAL` — the schema is versioned, the envelope
is not.

## 11. Child handle API (`find_agents` / `collect` / `close`) — `PARTIAL`

The design note wants a registry-scoped handle API over spawned children.
`SubagentLifecycleManager` (`alpha/subagents/lifecycle.py`) already offers
much of it: `get_subagent(id)`, `list_subagents(parent_id=…, status=…, limit=…)`
— which *is* a `list_children` — plus `pause_subagent` / `resume_subagent` /
`cancel_subagent` and `children_ids` maintained on the parent record. What is
absent: `find_agents` by arbitrary attribute (only parent/status filters
exist), and `collect`/`close` — there is no "await everything this handle
spawned" or "shut this subtree down" call; callers poll `task_*` events or the
`task` tool instead. Verdict stays `PARTIAL` because two of the four named
operations have no equivalent. Tests: `tests/test_subagent_control_plane.py`,
`test_managed_subagent_registry.py`.

## 12. Single depth/fan-out limit authority — `VERIFIED_PRESENT` (this branch)

The design note wants one place that answers "how deep / how wide may this run
go". That is now **`alpha.config.limits`** — a stdlib-free module that owns
every depth/fan-out/fan-in ceiling a subsystem enforces, so two modules that
must agree *cannot* disagree: there is only one number to change.

- **Delegation axis** — `MAX_DELEGATION_DEPTH` (3),
  `MAX_CHILDREN_PER_PARENT` (10), `MAX_FANOUT_PER_NODE` (4),
  `MAX_DELEGATION_HOPS` (6) are read by `subagents/lifecycle.py`,
  `bots/autonomy_guard.py` (`AutonomyBounds`) and `bots/delegation.py`
  (`DelegationLimits`), which previously re-stated `3` three times, `10`
  twice and `4` twice.
- **Native subagent admission** — `DEFAULT_CONCURRENT_NATIVE_SUBAGENTS` (3)
  and `MAX_CONCURRENT_NATIVE_SUBAGENTS_HARD` (64) are read by
  `subagents/executor.py` and `config/subagent_runtime_config.py`.
- **Workflow wave axis** — `DEFAULT_WAVE_CONCURRENCY` (4) and
  `MAX_WAVE_CONCURRENCY_CEILING` (32) are shared by the wave executor
  (`workflow/execution.py`) and the `SWARM` node
  (`workflow/swarm/execution.py`): a workflow node and a swarm member group are
  the same fan-out mechanism wearing different names, so they now cannot drift.
- **Group axis** — `MAX_GROUP_NESTING_DEPTH` (4), `MAX_GROUP_RELAY_HOPS` (8)
  and `MAX_GROUP_PARALLEL_MEMBERS` (3) are read by `groups/scope.py`,
  `groups/runner.py` and the Gateway router's own `max_parallel` request field
  (whose `le=` bound is now the same constant the runner enforces, rather than
  a re-typed `3`).
- **Swarm plan axis** — `MAX_SWARM_PLAN_CONCURRENCY` (64) pairs the Gateway
  router's `max_concurrency` `le=` refusal with `SwarmCoordinator.create_swarm`'s
  downstream `min(..., …)` clamp. These are two sites that must agree: if they
  drift, a request the router *accepts* is silently clamped later instead of
  being refused with the bound named — exactly the honesty failure the authority
  exists to prevent. This is the `alpha.swarm` *plan* engine's per-plan worker
  budget, a separate domain from the workflow `SWARM` node (which shares the
  wave ceiling above), so the two numbers legitimately differ.

**Policy is not mechanics, and the authority says so.** An APEX autonomy
contract's `max_delegation_depth` is a *profile* value intersected against the
engine ceiling at read time by `apex/agents.py::resolve_delegation_limits` —
so a policy depth of 5 still resolves to the engine's 3, and the refusal names
both numbers. Merging that knob into the authority would turn a tunable policy
setting into a hard engine refusal (or the reverse), so the two stay separate
and a test pins the intersection.

**The values are unchanged.** This is a pure centralisation: every constant
above is the exact value the consuming module enforced before the move, so no
behaviour changes with it. Changing a ceiling is now a one-file,
reviewable edit. Drift is pinned by
`backend/tests/test_limit_authority_consistency.py`, which asserts every
consumer site's value equals the constant imported from the authority, that
the authority module stays stdlib-free, and that the APEX intersection still
holds.

## 13. Continual-learning harness — `PARTIAL`

- **Code:** `alpha.harness.continual.state` (`HarnessState`, scopes
  `local`/`global`), `.snapshots` (`HarnessSnapshotManager`, rollback),
  `.refine` (`ContinualRefinementEngine.refine_from_events`).
- **What works:** the engine really does analyze events — it scans
  `tool_error`/`tool_result` payloads for file-not-found, permission and
  size-limit failures and distills them into memory entries or prompt
  directives, taking a snapshot first, and a run with no matches returns
  `None` rather than a fabricated refinement.
- **Gap:** the reflect → refine loop does not close on real runs. The
  model-facing `harness_refine` tool's `refine` action calls
  `refine_from_events([], trigger="agent_manual_refine")` — with **no events**
  (the source comment says events "could be loaded from thread event store if
  runtime provided"), so from the tool path it can only ever report "no new
  failure rules"; only a caller that already holds events gets real
  analysis. Scope is also limited to `local`/`global`.
- **Tests:** `backend/tests/test_continual_harness.py`
  (`test_continual_refinement_engine` feeds real events and asserts the
  distilled rules; `test_harness_refine_tool` covers the tool path).

## 14. Artifact index / search — `PARTIAL`

Artifacts are addressed by path and digest and are individually tested
(`test_artifacts_router.py`, `test_artifact_lineage.py`,
`test_artifact_archive.py`), but there is no index answering "what artifacts
exist and what do they contain" — the design note's searchable memory of
outputs. **No symbol to link for an index** → `PARTIAL`.

## 15. Context compaction — `VERIFIED_PRESENT`

Server-side compaction and micro-compaction are implemented and tested
(`backend/tests/test_context_compaction.py`,
`test_micro_compaction_engine.py`). The design note's model-facing `compact`
tool does not exist — compaction is automatic — which is *better* than the
note asks for, so the capability itself is `VERIFIED_PRESENT` with that
difference recorded.

## 16. Sandboxed Python REPL workbench — `VERIFIED_PRESENT`

Implemented behind `sandbox.allow_in_process_repl`, **default-off** (fail
closed — `SandboxConfig(...).allow_in_process_repl is False` is asserted
directly). Tests: `backend/tests/test_python_repl_boundary.py`,
`test_persistent_repl.py`, `test_interactive_repl_dap.py`, and the off-loop
config reads in `tests/blocking_io/test_repl_session_offloop.py`. Matches the
note's "arbitrary analysis in a sandbox" with a safer default than the note
specifies.

## 17. Task benchmark suite — `VERIFIED_PRESENT`

`alpha.benchmarks` provides the evaluation harness the note's `task_eval`
refers to: `BenchmarkRunner`/`BenchmarkSuite`/`BenchmarkCase` with registered
suites (`test_cron_safety.py` runs real suites through
`get_benchmark_runner()`, `test_workforce_intelligence.py` builds suites
directly), plus the fail-closed promotion gate in `test_release_gate.py`.
The note's `hillclimb` optimizer is **absent** (no symbol anywhere in the
tree) — recorded here so the absence is a decision, not an oversight.

## 18. Retry classification and no-progress detection — `VERIFIED_PRESENT`

- **Code:** `alpha.workflow.failures` (error → retryable/permanent
  classification), plus the stagnation/no-progress detector used by long
  runs.
- **Tests:** `backend/tests/test_workflow_failures.py`, and the
  `StagnationDetector` tests alongside it.
- Combined with §4's `retry_after_seconds`, a caller can now decide *whether*
  to retry and *when*.

---

## What this branch changed

1. **Added `alpha.mission.collectors`** (§6) — the first automatic evidence
   producers in the codebase, plus its 32-test suite. This closes the design
   note's "who writes the evidence?" gap.
2. **Added `retry_after_seconds` to the tool-result envelope** (§4) —
   measured, clamped, present only when measured; documented in
   `backend/packages/harness/alpha/agents/middlewares/AGENTS.md`.
3. **Corrected stale claims** — several docs asserted "no automatic evidence
   collectors exist"; they now describe the seam and name what is still not
   wired.
4. **Shipped the two workflow-template blueprints** (§9) —
   `alpha/workflow/builtin_templates.py` declares `code_change` and `bug_fix`
   as reviewable, unit-tested code (18-test suite), seeded into the template
   store on first construction; see `docs/DYNAMIC_WORKFLOWS.md`.
5. **Centralised the depth/fan-out ceilings** (§12) —
   `alpha/config/limits.py` is now the single authority every mechanically
   enforced depth/fan-out number reads from (15-test drift suite); see
   `backend/packages/harness/alpha/config/AGENTS.md`.
6. **Published this audit.**

## Deliberately not built

- **No `ok` twin on the tool envelope.** `status` is the envelope's single
  ok-fact; a second, hand-constructible boolean could disagree with it.
- **No automatic wiring of collectors into runs.** The seam exists; deciding
  which acceptance criteria get which collector is a product decision that
  belongs with the workflow authors, not bolted on here.
- **No `schema_version`, handle-registry, or role-taxonomy unification**
  (§8, §10, §11) — cross-cutting refactors beyond a focused evidence branch.
- **No manifest regeneration.** `alpha/mission/collectors.py` is reached via
  the existing `alpha.mission` package export, so the generated
  `contracts/feature_manifest.json` is unchanged and no counts move.
