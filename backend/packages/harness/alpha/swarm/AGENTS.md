# Swarm subsystem guidance

Scope: this file owns implementation contracts for `alpha.swarm`. The parent
[`../AGENTS.md`](../AGENTS.md) covers shared harness rules; this guide owns swarm
lifecycle, admission, decomposition, topology, telemetry, and verification detail.

## Swarm v2 runtime contract

`alpha.swarm` is the lifecycle owner for autonomous swarm plans. Plans are explicit
DAGs with atomic JSON checkpoints, ordered JSONL audit events, bounded blackboard
messages, lease-fenced task attempts, retry/backoff state, measured token/tool
budgets, deterministic reflection, bounded consecutive-failure circuit breaking
and optional evidence-backed consensus. A late worker result is accepted only
while its lease still matches; cancellation, budget exhaustion, dependency
failure and pause/resume are separate states. `auto_replan` is a bounded
deterministic repair-task expansion redirecting blocked dependents — it never
converts a failed task into success. The synchronous model tool may use the
runner's daemon-thread fallback when no event loop is available.

**Pre-launch cost projection.** `SwarmTelemetry.cost_projection(plan)` answers
the only question that decides whether to start: given this decomposition, will
it fit the ceiling already declared? `SwarmCoordinator.create_swarm` refuses a
plan whose predicted makespan exceeds its `max_wall_seconds` before registering,
checkpointing, or publishing it — a doomed plan must not remain visible as
"running" after a failed spawn. The `swarm` tool converts the validation error
to an actionable response and includes the projection for accepted plans. The
rule is self-consistent rather than configurable, so it cannot drift from the
budget it checks. **Only wall-clock is projected.** There is no per-task token or
tool-call estimate anywhere in the plan, so `projected_tokens` and
`projected_tool_calls` are `None` — "unmeasured", never `0` and never an
invented figure. A projection that quietly guessed would be worse than no
projection. `SwarmBudget` still bounds the run while it runs and `budget_pressure`
still reports the ratio after spend; the projection is the third, earlier answer.

**Plan-time admission advisory (disclosure, never a gate).**
`swarm/admission.py` writes `plan.metrics["admission_advisory"]` at
`create_swarm`, after the projection refusal and before registration, because
the plan's declared `max_concurrency` (up to
`limits.MAX_SWARM_PLAN_CONCURRENCY`) is not what a deployment can realize:
every swarm worker executes through `SubagentExecutor` — the pool is inherited
from the process-wide admission controller (`swarm/worker.py` passes no
`execution_capacity`), so realized parallelism is bounded by the armed
`subagent_runtime` schedule (default 3 running, 64 queued, `queue` policy,
300s queue timeout). The block carries `max_running`, `max_queued`,
`admission_policy`, `queue_timeout_seconds`, `expected_realized_concurrency`
(`min(plan_concurrency, max_running)`), `inflight_capacity`
(pool + queue under `queue`; pool alone under `reject`), `wide_plan_risk`
(true when the declared width exceeds the in-flight ceiling — such bursts are
refused admission and retried, not executed faster) and a bounded `note` for
model- and human-facing surfaces (`swarm` tool spawn response includes both
lines). Three rules hold: the advisory is **advisory** — a wide plan is still
accepted, and the wall-budget refusal above is untouched; an **unresolvable
schedule is `None`, never `0`**, the same unknown ≠ absent rule telemetry
follows; and the figures come from the **startup schedule**
(`alpha.subagents.capacity.configured_subagent_admission_schedule()`, a copy —
disclosure must never re-arm the process), not the live busy/queued count and
not a hot-reloadable config read. The projection keys are unchanged: the
projection answers the declared budget; the advisory answers the process.
Tests: `tests/test_swarm_admission_advisory.py`.

**Specification lint (refusal before registration).** `swarm/spec_lint.py` runs
inside `SwarmCoordinator.create_swarm` after the wall-budget refusal and before
the plan is registered, checkpointed or published — a refused specification must
never remain visible as a running swarm. The rules are deterministic and
model-free; the report is ordered refusals-then-warnings with tasks in `task_id`
order, and a report truncated at the bound discloses the truncation rather than
shortening itself silently:

- `empty_objective` — **refuse**.
- `acceptance_missing` — **refuse** when the task declares `output_artifacts`
  and no acceptance criteria: an artifact nobody checks is the shape of a
  fabricated delivery. It is a **warn** when the task declares no artifact —
  such a task can be executed but never accepted (`acceptance_status` stays
  `not_required`), and a blanket refusal there would refuse every ordinary
  plan, because the decomposer itself sets neither criteria nor artifacts.
- `acceptance_waived` — **warn**: the explicit waiver
  (`create_swarm(..., acceptance_waiver=True)`, passed through by the `swarm`
  tool) is recorded on the plan (`metrics["spec_lint"]`), so a waivered plan is
  never indistinguishable from a compliant one.
- `vague_directive` — **warn** on a fixed vocabulary, suppressed when the task
  declares an artifact or criteria (the done-state is defined elsewhere).
- `duplicate_directive` — **warn**: two tasks with one directive is step
  repetition, not parallelism.
- `dangling_reference` — **warn** on a cited path that does not resolve under
  the project or repository root (a planned file may be fine; a stale citation
  is not). It reuses `grounding.ground_claims`, so the two surfaces cannot
  disagree.
- `no_declared_write_scope` — **warn** when *declared* (never inferred)
  capability tags are not all read-only and the task names no output artifact
  or worktree path.
- `findings_truncated` — **warn**.

A refusal raises `SwarmPlanValidationError` with the stable prefix
`swarm was refused before registration: specification lint found N
refuse-severity finding(s)`. The `swarm` tool's `lint` action dry-runs the same
rules against a decomposition without registering anything (or re-lints a
registered plan, honoring a recorded waiver). Tests:
`tests/test_swarm_spec_lint.py`.

**Claim grounding (deterministic provenance for worker claims).**
`swarm/grounding.py` re-resolves the file-shaped claims inside a worker's own
result summary — workspace-relative tokens with a recognized source extension,
optionally `:LINE` / `:LINE-END` or `::test_ref`, plus absolute extension paths
— against the real workspace with no model call. It is bounded throughout (64
claims, a 200k-character summary head, 240-character tokens; line claims above
4MB and symbol scans above 512KB are `unverifiable`) and deliberately
conservative, so prose like `client/server` never becomes a citation.
`resolved` proves existence (the path exists, the line is inside the file, the
test symbol is defined), never correctness; `missing` / `out_of_range` /
`absent` are claim contradictions and gate; `unverifiable` and virtual-sandbox
paths stay visible but never count as failures; no extractable claims is
`not_checked` — never a vacuous pass. The payload carries `GROUNDING_BASIS`
(`deterministic_workspace_resolution_v1`), and every consumer renders a verdict
only for the current basis. `SwarmAggregator`'s acceptance overlay turns an
unresolved claim into `acceptance_status="failed"` with the
`grounding_unresolved` count and reason (the overlay only runs when the task
declared acceptance criteria — grounding never invents a criterion); the team
report renders the row and counts unresolved claims as an unknown. The runner
computes grounding from the internal worker outcome only, after the completion
commit and off the event loop, and writes it under a following state lock —
running it while the node still read `RUNNING` widened the watchdog's straggler
window (a healthy worker could be classified as slow and a duplicate provider
attempt launched), so it must not precede the commit again. Same discipline as
the citation audit: never external `complete_task` (there is no request field)
and never before lease acceptance. Tests:
`tests/test_swarm_claim_grounding.py`.

**The governor does not route models.** `SwarmResourceGovernor` adapts concurrency
after 429s and reports which models a swarm used. It used to carry a `MODEL_TIERS`
table of four hardcoded names plus a `resolve_model_for_role` substring matcher;
neither was called, because the runner resolves models through
`resolve_subagent_model_name(..., app_config=...)` and `task_node.model_override`.
The defect was that `get_status()` still *published* those names, and it is served
by `GET /api/swarms/governor/status` and by the swarm tool's own `governor` action
— so an operator surface, and the model itself, were told the swarm routes to
models Alpha does not use, which also breaks the root rule that every model name
lives in `config.yaml`, never in code. `get_status(plan)` now reports two measured
lists: `models_requested` (distinct non-`None` `model_override`) and `models_used`
(the `model` field each worker stamps into its evidence via `_model_label`). A
requested model that was never reached is therefore never reported as used. With no
plan both are `None` — "not measured yet", not a fallback name.

**Duration-weighted makespan in candidate scoring.** `score_plan` computes its
parallelism term from a *seconds* critical path when the plan carries one, and
from hop counts otherwise. The hop lower bound treats every task as equal, so a
plan with one 600-second task and nine 5-second tasks previously scored
identically to its inverse — and `SwarmTaskNode.estimated_seconds` existed to
break exactly that tie while nothing read it. The decomposer already computed
`critical_path_seconds` (`estimate_method =
"dependency_critical_path_plus_capacity_v1"`), but only on the *final* plan,
after selection, so every candidate still carried the default `0.0` at scoring
time. It now runs per candidate, before `build_candidate`. With uniform
durations the two measures agree exactly, so this is a strict improvement rather
than a reweighting. The hop-based fields (`critical_path`, `exposed_width`,
`lower_bound_rounds`) are retained: they answer a different question.

**Degeneracy signals (advisory, never gating).** `parallelism_realised` sweeps
the recorded `started_at`/`completed_at` intervals for peak overlap and flags
*serial collapse* — a swarm that spawned many subagents and ran them one at a
time. `subtask_completion_ratio` flags *spurious parallelism* — a wide fan-out
that completes little. Both are the pathologies Kimi's `r_parallel` and
`r_finish` reward terms exist to suppress, and both map onto MAST failure modes
(step repetition; ignored other agent's input). A flagged run is **reported,
never aborted**: a serial run that succeeded is still a success, just an
expensive one, and the operator is the one who should see that. Missing
timestamps yield `state: "unknown"`, never a fabricated `collapsed`.

`SwarmCoordinator` serializes state transitions and restores interrupted plans as
paused with fresh-lease requirements; `SwarmScheduler` validates DAGs before
mutation, assigns lease ids, renews live attempts, applies backoff and fences stale
results; `AsyncSwarmRunner` records measured usage, publishes bounded task-result
messages, enforces the consecutive-failure circuit and reports
`budget_exhausted`/`stalled` rather than fabricating completion. `SwarmAggregator`
keeps execution status separate from acceptance: duplicate voters and unverified
evidence cannot produce a clean approval.

**Restart recovery (phase 3.1).** A restart parks every non-terminal in-flight
plan as `paused` with `terminal_reason = alpha.swarm.recovery.RESTART_TERMINAL_REASON`
(the single authority for that string: the coordinator writes it at load, the
recovery module decides what counts as recovered, and the frontend banner keys on
the same value) and requeues RUNNING/STRAGGLING tasks lease-free — the next
scheduler tick claims **fresh** lease ids, so a dead process's lease can never
fence a new attempt. `swarm/recovery.py` owns the vocabulary after that parking:
`scan_recoverable` (owner-scoped, bounded, truncation reported rather than
hidden), `resume` (refuses anything not restart-recovered by name; re-parks any
task still holding a lease before flipping the plan), `abandon` (cancel with a
named reason), and the one-shot startup pass `auto_resume_on_start`, gated by
`config.yaml -> swarm.resume_on_start` (`none` default — cost-bearing resume is
opt-in per plan §11.3; `owned` resumes only the installation default owner's
plans; `all` resumes every recovered plan; `none` constructs no coordinator at
all). The pass runs in `app.py`'s lifespan and a failure there never blocks
startup. `GET /api/swarms?status=` filters by one known status and refuses an
unknown value with 422 rather than silently returning everything. Surfaces split
deliberately: the read-only chat `SwarmRunBoard` renders a recovery banner
naming the server's `terminal_reason` (`lib/swarm-run-board.ts::
swarmRecoveryBanner`), while the Resume/Abandon controls live in Team Ops beside
every other lifecycle verb (`isRestartRecoverable` in `lib/teamops.ts`).
Tests: `backend/tests/test_swarm_recovery.py` (a real two-coordinator restart
over one storage dir), `frontend/src/lib/teamops.test.mjs`,
`frontend/src/lib/swarm-run-board.test.mjs`.

Specialist swarm tasks must run the same advisory receipt-citation audit as
ordinary delegated tasks when the executor harvested receipts. Surface
uncorroborated action claims in the team report and task UI. A resolved receipt
proves only that a matching call was recorded as successful; it never proves
the claim or delivery is correct and must not be promoted to acceptance. The
verdict lives in the dedicated ``SwarmTaskNode.citation_audit`` field, written
only by the runner from a validated internal worker outcome after lease
acceptance — external task completion supplies generic ``evidence`` and can
never populate it, the request contract has no such field, and a report
renders a citation status only for an audit carrying the receipt-citation
contract (an audit that never ran renders nothing, never a fabricated
verdict). Regressions: ``tests/test_swarm_v2_runtime.py``,
``tests/test_swarm_api_v2.py``, ``tests/test_team_composition.py``,
``tests/test_swarm_worker_execution.py``; UI projection:
``frontend/src/lib/swarm-structure-view.ts``.

Gateway swarm reads and mutations are owner-scoped (`SwarmPlan.owner_id`); the
`swarm` tool derives the owner from request context and cannot self-assert a
verified blackboard message. Model-visible blackboard content is bounded data in the
human input channel, never an elevated system instruction. Worktree paths are
relative metadata; the worker always uses the server project root.
Specialist requirements are inferred from each task directive using
`alpha.capabilities.eligibility.CAPABILITY_KEYWORDS` and matched against the
live `specialists:` roster with a hard capability floor. Keep the inferred tags
aligned to the shipped specialist capability vocabulary and pin end-to-end
composition in `backend/tests/test_team_composition.py`; a completed swarm with
zero specialist assignments is not evidence of specialist execution.
`communication.py`, `consensus.py`, `reflection.py` are harness-layer and never
import `app.*`.

The persistence adapter is deliberately local and process-scoped: atomic and
restart-recoverable for a single Gateway, never a substitute for a shared SQL
lease repository in a multi-worker deployment — never call local JSON checkpoints
cross-process exactly-once. v2 API/tool surfaces expose metrics, task leases,
messages, leader election, consensus and acceptance-verdict fields so operators
can distinguish execution success from acceptance success. Regression coverage:
`backend/tests/test_swarm_engine.py`, `test_swarm_advanced_features.py`,
`test_swarm_worker_execution.py`, `test_swarm_v2_runtime.py`.

**Read-only worker tool isolation (G7).** A specialist task whose capability
tags are all read-only (`alpha.sandbox.worktree_strategy._READ_ONLY_CAPABILITY_TAGS`)
runs on a worker that physically cannot write: `read_only_tool_pool` filters the
specialist tool pool before the executor binds it, dropping every tool that is
class-mutating under the governance registry (`write`/`execute`/`destructive`,
operator `tool_governance` entries authoritative, declared `governance_risk_class`
metadata next), the structural denylist (`WORKSPACE_WRITE_TOOLS` plus
`python_repl`), and every untrusted-source (MCP) tool — an MCP tool may not
classify itself, exactly as `GovernanceRegistry` already refuses its
self-declared metadata. Every dropped name also lands on the worker's
`disallowed_tools`, so the executor gate independently refuses calls even if a
pool change re-introduced an object. `External` tools stay: a research fan-out
that could not reach the web would not be a fan-out. The sampled derivation is
the same single `task_signals_for()` the worktree branch uses — never a second
opinion that could disagree with the isolation decision. `tool_pool_error`
stays authoritative when set: a pool that failed to assemble is never rescued
by a filtered subset. Regression coverage:
`backend/tests/test_swarm_read_only_worker_tool_isolation.py`.

### Adaptive mode selection, deliberation, and failure-aware telemetry

`SwarmMode.AUTO` is resolved by `alpha.swarm.strategy.resolve_strategy`, never
hardcoded. Precedence is **explicit > topology (measured DAG shape) > heuristic
(goal text) > fallback**; an explicit mode is honored untouched — a named
topology is a requirement, not a suggestion. `SwarmBenefitEstimator.estimate`
stays byte-identical; structural layers are added on top, never substituted.

**The plan and its recorded strategy must agree.** `plan.metrics["strategy"]` is
written for *every* plan and `plan.mode` must equal `strategy["mode"]`. The first
pass cannot see structure — no plan exists yet — so it routes on goal text; that
estimate then *builds* a graph whose own shape can route elsewhere (a heuristic
picking `parallel` for a phased goal still produces a research/architect/review
chain, measuring `hierarchical`). `SwarmTaskDecomposer._settle` therefore
rebuilds the plan from the measured route: bounded, each rebuild adopting the
mode just resolved, so the pair agrees by construction on exit. Never "optimize"
this away — recording the second answer without rebuilding leaves a plan
labelled `parallel` while its own metrics say `hierarchical`.

- `topology.route_topology` never reads goal text and never infers
  `ENSEMBLE`/`DEBATE` from shape — redundancy is a claim about *intent* a graph
  cannot answer, so those stay semantic or explicit. `max_concurrency` is
  accepted and deliberately unused: routing is about shape; capacity belongs to
  `planner.score_plan`, where makespan is scored.
- **The single-sink fan-in rule must precede the coupling rule.** A reduce is
  the densest acyclic shape there is, so `coupling >= COUPLING_THRESHOLD` is a
  measurement artifact for `depth == 2 and leaf_count == 1`, not a structural
  finding; the coupling verdict should catch a genuine mesh like K2,2 (two
  sinks). Getting the order wrong hands a 2-item batch a 5-node
  leader-sequenced pipeline. `COUPLING_THRESHOLD = 0.30` — a DAG's acyclic
  ceiling is ~0.333 at K2,2; 0.50 would be a dead rule.
- `planner.score_plan` reports `exposed_width` (instant parallelism, capped by
  the plan's own concurrency) separately from `lower_bound_rounds` (idealised
  makespan floor): a 16-way fan-out on 4 slots exposes the same width as a 4-way
  fan-out but needs 4 rounds, and conflating them scores a wider plan as more
  parallel than it can be.

Complexity tiers (`DIRECT`/`SIMPLE`/`ADAPT`/`MEDIUM`/`FULL`) gate machinery
rather than enabling it globally: candidate planning and stigmergy at `MEDIUM`+,
deliberation at `FULL`. `StrategyResolution` gates compare `tier.rank`, **never
the enum member** — `ComplexityTier` is a `StrEnum`, so
`tier >= ComplexityTier.FULL` compares strings and `"full" >= "medium"` is
`False`, silently inverting every gate on the most complex plans.

`deliberation.run_deliberation` applies Wald's sequential test over the same
evidence `evaluate_consensus` sees. With the defaults (`target 0.75 ± 0.10`,
`alpha 0.05`, `beta 0.10`, `max_rounds 5`) ~11 unanimous approvals accept the
hypothesis, 3 unanimous rejections the alternative; anything less reports
`unresolved` with the reason. **A small undecided panel must say "unresolved",
never "approved".**

- Guards may only **block**. Domination, sycophancy, evidence-free approval and
  any disagreement between the sequential test and the explicit policy downgrade
  a result to `manual_review`; nothing can raise a refusal to an approval.
  `SwarmAggregator._apply_deliberation` attaches the full report either way so
  the disagreement stays visible; a malformed payload discloses an `error`
  instead of raising.
- Domination is measured as `max_share × voter_count` (1.0 = an even panel), not
  raw share — a raw share flags every healthy two-voter panel, which holds 50%
  each by definition.
- Sycophancy counts only flips that moved *toward* the prior plurality and
  added no new evidence.

`stigmergy.StigmergicTraceStore` is **advisory context and never a gate**: it
ranks what later workers see and decides nothing, bounded in entries, payload,
key and provenance; amplification is sub-linear and capped, so one worker cannot
farm its own strength by repeating a deposit. `decayed_at` stays separate from
`updated_at` because every read path evaporates: measuring decay from
`updated_at` without advancing it re-applies the window on every read, killing a
popular trace in proportion to how often it was consulted.

`SwarmTelemetry` reports `None`/`unknown` wherever a signal is genuinely not
measurable — a plan with no declared tool outcomes reports
`tool_error_rate = None`, not a reassuring `0.0`. `deliberation_rounds_saved` is
a **round count**; no round-level token meter exists, so never convert it into a
cost figure.

The model surface is the existing `swarm` tool with three new **actions** —
`strategy` (the recorded choice, never a re-derivation that could disagree with
the plan), `telemetry`, `trace` (deposit when `message` is set, otherwise list) —
never new tools, so `BUILTIN_TOOLS`, `contracts/feature_manifest.json` and the
capability counts are unaffected. Regression coverage: `test_swarm_engine.py`
(pinned estimator output), `test_swarm_adaptive_topology.py` (topology, planner,
tiers), `test_swarm_deliberation.py` (SPRT, guards),
`test_swarm_stigmergy_telemetry.py` (traces, telemetry, coordinator wiring).
