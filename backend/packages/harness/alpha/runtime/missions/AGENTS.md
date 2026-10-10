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

**Steering is a record, never a control; the resume checkpoint beats the drift of
compaction.** Two Codex techniques land here as durable state. A `steer` is a live
operator constraint surfaced every turn (`## Operator steer`) so a course correction
survives the next compaction instead of being spoken once. A `reject` becomes a
`## Do not repeat` line so a post-compaction agent does not re-attempt a dead end,
and a `defer` parks a good idea (`## Deferred`) without it leaking into the current
work. The `ResumeCheckpoint` is the fix for the documented "successful compaction
that resumes from the wrong point" loop: a *structured* `current_phase` /
`next_action` / `stop_condition` / `rejected_paths` / `do_not_repeat` the run reads
after compaction, so it continues from where it is rather than restarting earlier
work. None of these is a prompt rewrite; they are records the agent re-reads, exactly
as a steer is a record in APEX.

**The loop brake is fail-closed, and it does not trust the agent.** The one lesson
every Codex loop controller shares (and Codex issue #37937 makes concrete) is that
an autonomous loop must not run forever: a repeated block with no new information
should *stop once and return control*, not burn another turn until a quota dies.
`decide_mission()` reads only durable state and returns exactly `continue`, `done`,
or `park`. `done` requires every milestone `VERIFIED` on measured evidence — never
a summary. `park` fires on an explicit `block`, on the same attempt *signature*
repeating past the threshold (no new information), or on `no_progress_cycles`
crossing the threshold. It is model-free so the "why did it stop" answer is
reproducible, and it has no "keep going anyway" path — the failure mode it exists
to prevent. Alpha already has node-level stagnation and APEX's acceptance gate;
this is the *mission-scoped* brake that composes with the human-readable anchor and
has no equivalent here.

**Where things live**:
- `milestones.py` — `Milestone`, `MilestonePlan`, `MilestoneStatus`, `InvalidMilestonePlan`, stop-and-fix transitions.
- `scratchpad.py` — `Scratchpad` bounded reasoning ring.
- `manager.py` — `MissionStack` value object (spec/plan/status/scratchpad/steers/rejected/deferred/checkpoint) + `MissionManager` path-safe atomic store.
- `anchor.py` — `render_anchor()` — the compact, bounded per-turn anchor callers project.
- `checkpoint.py` — `ResumeCheckpoint` + `render_resume_checkpoint()` — the structured continuation checkpoint.
- `verify.py` — `verify_milestone()`, `MilestoneVerdict`, `MilestoneVerification` — a measured evidence record becomes a verdict (`met`/`not_met`/`unverified`).
- `brakes.py` — `decide_mission()`, `LoopDecision`, `MissionAction` — the model-free loop brake: `continue` / `done` / `park`.
- Tests: `tests/test_mission_memory.py` (plan verify/advance, stop-and-fix, scope refusal, corrupt fail-open, evidence→verdict, resume-checkpoint render, ring bounds, anchor blocks, loop brake), `tests/test_mission_memory_wiring.py` (tool actions incl. steer/checkpoint/reject/defer/brief/block/tick/decide + middleware injection + real test/artifact evidence + honesty).
