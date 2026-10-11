# GRO — Group-Relative Orchestration (`packages/harness/alpha/gro/`)

Alpha's implementation of the GRPO-inspired orchestration plan
([`docs/ALPHA_GRPO_INSPIRED_ORCHESTRATION_PLAN.md`](../../../../../docs/ALPHA_GRPO_INSPIRED_ORCHESTRATION_PLAN.md)).
One objective, a bounded group of candidates executed under *distinct*
strategies, then selection by **hard gates first, absolute score second,
relative rank third**. It is deliberately distinct from `alpha.arena`:
arena is a bracket tournament (attack/defend/judge between pairs), GRO is a
single-wave group comparison with gate-first eligibility and a bounded
failure-directed repair round. Neither may grow selection rules the other owns.

## The one rule that decides everything

> **Absolute gates decide eligibility. Relative comparison only ranks the
> eligible.**

The candidate group might be uniformly poor, so the winner is never chosen by
z-score, majority vote, confidence or verbosity. Concretely:

- **A required gate that is `unknown` is not a pass.** A criterion the
  deterministic checker could not decide keeps its candidate ineligible
  (`GateResult.required` separates demanded gates from informational ones).
- **Relative rank (`R_i = Q_i − median(Q)`) is diagnostic.** It is written on
  `CandidateEvaluation.relative_rank` / `relative_margin` for humans and
  repair decisions; it never changes `eligible` and never touches an
  ineligible candidate's score.
- **All-fail is an explicit outcome.** `select_candidate` returns
  `no_acceptable_candidate` with `repairable` computed from real evidence
  (a completed-but-failing candidate can be repaired; a timed-out one cannot).
  The runner then either runs the bounded repair round or reports a
  `partial_result` whose text is labelled **UNVERIFIED** — never "the best of
  a bad lot, verified".
- **No acceptance criteria means an UNVERIFIED winner.** Eligibility then
  rests on execution + evidence gates only, and both the report and
  `GroReport.verified=False` say so. The engine never invents criteria.

## Module map

| File | Owns |
| --- | --- |
| `models.py` | value types, the closed state-transition table, `GroRun.transition` journaling |
| `strategies.py` | the reviewable strategy catalogue (7 coding / 5 research / 5 planning+tools), distinct-deal rule, `gro.repair_from_failure` |
| `policy.py` | task-type + risk classification (computed, never declared), single/group/approval decision |
| `evaluation.py` | hard gates, absolute score (versioned per-task-type weights), relative rank, deterministic selection |
| `coordinator.py` | bounded parallel lanes, per-candidate + run deadlines, late-result fencing |
| `adapters.py` | `ExecutionAdapter` seam: `SyncCallableAdapter` (tests/hosts) and `SubagentExecutionAdapter` (real subagents) |
| `store.py` | owner-scoped JSON persistence under `runtime_home()/gro/`, honest degradation |
| `runner.py` | `run_with_gro`: phases A→G end to end, repair loop, final re-verification, `GroReport` |
| `config.py` | `GroConfig` (`config.yaml -> gro`), default **disabled** |

## Load-bearing invariants

- **Timeout is a fence, not a kill** (CPython cannot stop a thread): a lane
  past its bound is declared `timeout`, fenced, and a result arriving after
  the fence is *discarded* — adopting it would credit work the run already
  declared late. A queued candidate whose lane was fenced never executes.
- **Bounds are refused, never clamped.** `GroBudget`, `GroConfig` bounds,
  criteria count/length, strategy counts: exceeding one raises with the bound
  named. A padded or duplicated candidate group would fake diversity.
- **Every candidate gets an outcome.** Failed, timed-out, fenced-late and
  never-started (early stop / deadline) candidates are recorded as such;
  omission would read as success to any later ranker.
- **Risk is computed, never declared.** `policy.classify_risk_tier` reads the
  goal + criteria; externally-visible or destructive work resolves to
  `human_approval_first` (nothing executes) while
  `require_approval_for_external_side_effects` is on.
- **Final verification demotes, never promotes.** With
  `require_final_verification` the winner's gates are re-checked against the
  live state; a re-check failure moves `SELECTED → NO_ACCEPTABLE_CANDIDATE`
  with the failing gate named.
- **Store honesty:** absent file → `None` (healthy); corrupt file →
  `is_degraded`/`load_error` set, `None` returned — never read as "no runs".
  `save()` returns whether the write landed and stamps `run.save_error`.
  The `FileLock` is advisory and filesystem-local: single-process JSON,
  restart-recoverable for one Gateway, **never cross-process exactly-once**.
- **Strategy is data, not generated shell**: approaches are bounded lines in a
  versioned catalogue; a duplicate strategy in one group is refused.

## Integration surface

- **Model-facing:** the existing `swarm` tool gains two actions —
  `compete` (run a GRO tournament for `goal`) and `compete_status`
  (`swarm_id` = GRO run id; omitted, it lists the caller's runs newest
  first). Actions on an existing tool keep `BUILTIN_TOOLS`,
  `contracts/feature_manifest.json` and the capability counts untouched.
  The `compete` surface is deliberately minimal: `goal` plus `items_json`,
  whose entries are the **acceptance criteria** every candidate must prove;
  task type, risk tier and group size are *policy-computed* from the goal
  and criteria (`policy.decide_mode`) — a caller may not declare its way to
  a lower tier or a bigger group. There is deliberately no `agents_json`:
  how many candidates run is the engine's decision, bounded by
  `gro.max_candidates_per_task` (refused, never clamped). `compete` refuses
  with the reason when `gro.enabled` is false; `compete_status` stays
  readable while disabled (it is read-only).
- **Subagent mode:** candidate lanes execute through the real
  `SubagentExecutor` (`SubagentExecutionAdapter`), inheriting the
  process-wide subagent admission controller, sandbox leases, receipts and
  usage accounting. The bounded tool pool is assembled once per run via
  `run_assembly(get_available_tools, …)` and the `swarm` tool is stripped
  from the pool by name, so a candidate cannot launch a nested tournament.
  An empty pool is a refusal before any lane runs, never a silent tool-less
  candidate (that would score worse for having fewer instruments and poison
  the comparison).
- **Swarm mode:** `swarm` tool callers (lead or swarm) drive GRO through the
  same actions; GRO candidate subagents take real subagent slots, so enabling
  GRO cannot exceed the operator's process ceiling.
- **Persistence timing:** the event journal is appended **live** (every
  `emit` calls `append_event`), but the run *document* is written at
  completion (and on the approval-park path). A run interrupted mid-flight
  therefore has a journal tail and no document — `compete_status` reads the
  tail and says so rather than reporting "not found". Events flow through an
  injectable `on_event` callback and the best-effort JSONL journal;
  `alpha.events.bus` integration is future work and not claimed.

## Config (`config.yaml -> gro`)

`enabled` (default false), `default_mode` (single/group/adaptive),
`max_candidates_per_task`, `max_active_candidates_global`,
`max_repair_rounds`, `early_stop_on_verified_success`,
`candidate_timeout_seconds`, `run_timeout_seconds`,
`require_final_verification`, `relative_rank_is_tiebreaker_only` (pinned
true by design; the field documents the invariant), and
`require_approval_for_external_side_effects`.

## Deliberate v1 boundaries (not claims)

- No pause/resume state (`PAUSED` is absent from the table; a synchronous
  runner cannot pause honestly).
- File-scoped acceptance criteria degrade to `unchecked` in this path (no
  sandbox runtime crosses the tool boundary); `tests_passed:` criteria work
  through harvested bash executions.
- Token spend is recorded (`usage`) but never enters the score: no per-task
  absolute token scale exists, so a token term would be a fabricated metric.
- `alpha.reasoning_bank` feedback (phase H of the plan) is not wired.

Tests: `backend/tests/test_gro_core.py`, `test_gro_evaluation.py`,
`test_gro_runner.py`, `test_gro_tool.py`.
