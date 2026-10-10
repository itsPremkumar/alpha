### Durable mission memory (`runtime/missions/`)

`runtime/missions/` is the **cognition of durability**: the re-anchor that stops a
run which has survived a compaction or a restart from drifting off its objective.
It is memory plus a completion contract, and nothing else -- it is not a second
lifecycle owner, it does not dispatch, and it runs no command.

**The gap it closes.** Alpha already keeps the *process* durable (sessions, network,
side-effect ledger, supervisor, planned shutdown) -- see
[docs/architecture/durable-runtime.md](../../../../../docs/architecture/durable-runtime.md).
What neither a checkpointed `RunManager` run nor a summarized context can supply is
the objective itself once it has scrolled out of the window. This package holds that
objective as durable, owner+thread-scoped state and re-reads the parts that matter
before every model turn.

**The spec/plan/status/scratchpad stack** is one :class:`MissionStack` per
``(owner, thread_id)`` under ``Paths.user_dir(owner)/missions/<thread>.json``:

| File field | Owns | Re-read each turn as |
|---|---|---|
| `spec_objective` / `spec_constraints` / `spec_done_when` | the frozen target and its bar | `## Objective` + `## Hard constraints` + `## Done when` |
| `plan` (:class:`MilestonePlan`) | the checkpoints | `## Current milestone (i/n)` with acceptance + verification |
| `implement_runbook` | how to operate | carried in the stack; the live anchor stays compact |
| `status_lines` | append-only audit log | `## Recent status` (tail) |
| `scratchpad` (:class:`Scratchpad`) | durable bounded reasoning | `## Scratchpad` (tail) |

**Evidence decides a milestone, never intent.** A milestone becomes `VERIFIED` only
through :meth:`MilestonePlan.verify` recording a *measured* result with evidence.
The model may not mark a milestone done by narrating it.

**Stop-and-fix is structural.** :meth:`MilestonePlan.advance` refuses to move the
active pointer until the current milestone is `VERIFIED`; a `FAILED` milestone must
be repaired and re-verified first, so a broken checkpoint cannot be left behind a
"complete" plan.

**The plan is refused, never clamped.** Bad input (empty objective, duplicate ids,
more than `MAX_MILESTONES`, an out-of-range index, an unknown persisted status)
raises :class:`InvalidMilestonePlan` naming the field.

**Storage is atomic and fsync-backed; the scope is server-owned.** `MissionManager.save`
writes a temp file, fsyncs, and `os.replace`s, so a crash mid-write leaves the prior
state rather than a torn one. The scope is `(owner, thread_id)` from the trusted
runtime -- a value escaping the owner's mission directory is refused, not sanitized
(`_SAFE_TOKEN`). A corrupt file loads as an empty stack carrying `load_error` and a
WARN: losing a note is survivable, inventing content a file did not hold is not.

**`UNKNOWN` never appears here.** An unverified milestone is `ACTIVE`, not a shrug;
`verified` means "the named check held", never "the work was correct".

**Where the pieces fit** (consumers live outside this package):

- `agents/middlewares/mission_memory_middleware.py` injects `render_anchor()` as a
  hidden `<system_memory>` block before each model turn, format-aware and idempotent.
- `tools/builtins/mission_memory_tool.py` is the model-facing surface that records
  spec/plan/status/scratch into the stack and advances or verifies milestones.
- Config: `alpha/config/mission_memory_config.py` (`config.yaml -> mission_memory`).

**Evidence decides, and the collectors already exist.** A milestone's `verify`
is not "the model says so": the tool boundary collects a real
`alpha.mission.acceptance.EvidenceRecord` through the sanctioned readers
(`collect_test_exit_report` reads a JSON exit report the host already produced;
`collect_artifact_digest` confirms a file inside a confined root with a SHA-256)
and `verify_milestone` folds its boolean `measured` into the plan. A missing,
unreadable or malformed source yields no record, which is `UNVERIFIED` — the
milestone is left untouched, never quietly passed. This package never runs a
command and never imports `alpha.mission.acceptance` at module scope (the caller
imports it lazily), so the acceptance plane stays the single evidence authority and
there is no cycle.

**Where things live**:
- `milestones.py` — `Milestone`, `MilestonePlan`, `MilestoneStatus`, `InvalidMilestonePlan`, stop-and-fix transitions.
- `scratchpad.py` — `Scratchpad` bounded reasoning ring.
- `manager.py` — `MissionStack` value object + `MissionManager` path-safe atomic store.
- `anchor.py` — `render_anchor()` — the compact, bounded per-turn anchor callers project.
- `verify.py` — `verify_milestone()`, `MilestoneVerdict`, `MilestoneVerification` — a measured evidence record becomes a verdict (`met`/`not_met`/`unverified`).
- Tests: `tests/test_mission_memory.py` (plan verify/advance, stop-and-fix, scope refusal, corrupt-file fail-open, evidence→verdict), `tests/test_mission_memory_wiring.py` (tool actions + middleware injection + real test-exit/artifact evidence + honesty).
