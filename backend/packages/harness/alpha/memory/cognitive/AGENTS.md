# Cognitive memory owner-scoped contract

- `engine.py:41` resolves production storage through `Paths.user_dir(owner)` to
  `users/{owner}/cognitive_memory`; no owner means failure, not a shared default.
  Gateway and tool entry points must supply trusted server/runtime identity,
  never HTTP/model-provided owner or storage-path overrides.
- `engine.py:342` caches by owner and directory under a process lock. Use
  `system.operation()` for read/mutate/save sequences: its instance `RLock`
  serializes operations and restores in-memory state on exceptions. Explicit
  `storage_dir` calls return independent instances outside the owner cache;
  supplying both `storage_dir` and `user_id` is invalid.
- `engine.py:117` saves via same-directory temporary file, flush, file `fsync`
  and atomic replacement. Write errors propagate; corrupt-state parse/load
  errors fail closed without bootstrapping over the file. This is not exhaustive
  schema validation or a directory-fsync guarantee.
- Persist all retained persistent records, not display pages. Skills, events,
  traces, episodes, edges and associations use complete retained collections;
  semantic persistence still caps at 2,000, matching `semantic_graph.py:23`'s
  default enforced capacity. Raising capacity requires updating that snapshot
  limit. Working memory remains ephemeral and owner-scoped, not thread-isolated.
- Single-process contract only: no cross-worker cache coherence or file locking.
  Independent instances targeting the same directory do not share a lock; atomic
  replacement alone cannot prevent lost updates. Legacy shared snapshots are
  not automatically imported; migration and broader tool exposure remain open.
- Entry points: `backend/app/gateway/routers/memory.py:579` and
  `backend/packages/harness/alpha/tools/builtins/cognitive_memory_tool.py:29`.
  Regressions: `backend/tests/test_cognitive_memory_isolation.py` and
  `backend/tests/test_cognitive_memory.py`.

## Procedural-skill lifecycle (`skill_lifecycle.py`)

`ProceduralSkill.success_rate` returns **1.0 when nothing has ever been
measured**, and that fabricated perfect score used to drive recall, capacity
trimming, and the persisted snapshot. `success_rate` is therefore a display
convenience only; ranking and eviction must use `strength`
(`smoothed_effectiveness`), and any caller that needs to know whether a number
exists must read `effectiveness`, which is `None` while `evidence_count` is 0.

- `evidence_count` is **derived** (`success_count + failure_count`), never
  stored, so a restored snapshot cannot drift from its own counts and there is
  no migration.
- `skill_lifecycle.evaluate()` derives the state the measurements support and
  reports why; `transition()` records a reason on the skill and **refuses**
  illegal moves rather than clamping them. A never-verified skill cannot be
  promoted, so one lucky run cannot reach `promoted`.
- `ProceduralSkillMemory.rank_for_recall()` is the honest-scoring twin of
  `find_matching_skills()`; `lifecycle_summary()` counts derived states, so a
  skill stored as `promoted` that has since failed every run is counted where its
  evidence puts it, with the stored value left visible for the disagreement.
- `_enforce_capacity` evicts by `retirement_priority()`: weakest evidence first,
  and a skill that is the **only** one able to serve a pattern is held to the
  end, because dropping it removes a capability instead of freeing a slot.
- `record_outcome` moves `proposed -> verified` on the first outcome and nothing
  more; promotion and deprecation are decisions with a sample-size floor that
  belong to an explicit `evaluate` + `transition` pair.

Regression coverage: `backend/tests/test_cognitive_skill_lifecycle.py`.

## Reconsolidation (`reconsolidation.py`)

Two mechanisms this stack was missing, both from the memory literature:

- **Retrieval is not read-only.** `CognitiveMemorySystem.recall()` now folds
  each retrieved semantic fact back into the store (access count, and the
  spacing effect that makes a retrieval after a long gap worth more than one
  immediately after the last). `recall(..., reconsolidate=False)` is the explicit
  read-only path for probes, previews and tests that must not mutate.
  `record_retrieval_outcome()` / `reconsolidation_disclosure()` are the write and
  read sides of the usefulness verdict, which only the caller can supply.
- **Rest replays what worked.** `CognitiveConsolidationEngine` now runs
  `replay_strengthen` inside Deep Sleep, bounded by `REPLAY_BUDGET` (8) over a
  `REPLAY_SCAN_LIMIT` (200) trace window, most-salient first. A trace whose
  outcome is `UNKNOWN` is skipped rather than assumed successful, and salience
  is capped so repeated replay cannot manufacture importance.

Invariants, all pinned by `tests/test_cognitive_reconsolidation.py`:

- `retention_probability()` is the **single** implementation of the Ebbinghaus
  curve that `CognitiveConsolidationEngine` already applies; reconsolidation
  reuses it rather than growing a second decay model. It returns `None` when a
  node has never been accessed, because there is no measured interval to decay.
- `useful` is a caller assertion, never an inference: `None` is a third state
  and is never coerced to `False`.
- `ConsolidationReport` gained `reconsolidation_records`, `replayed_traces`,
  `replayed_budget` and `tier_disclosure`, all defaulted so every existing
  construction stays valid.
- `tier_disclosure` sums only tiers that reported a number. A tier reporting
  `None` keeps `None` rather than being folded in as a zero.
- `semantic_graph.add_belief` seeds `access_count=1`, so a fresh node reports
  one access before any retrieval. Reconsolidation does not hide that: the first
  recall takes it to 2 and the disclosure says which accesses were retrievals.
- **The RRSI link is deliberately a named gap, not an invented mapping.**
  Consolidation knows which tier moved and how many items, which is not a
  measured evolve-set score delta per component (`g_t`). `evidence_gap_reason()`
  states that instead of producing a plausible-looking mapping. Do not "fix"
  this by asserting one.
## Improvement verification (`improvement_verification.py`)

A self-improving subsystem that reports it works is worth nothing, and a verifier
that only ever passes the cases it was written for is the same fiction. Arize's
`Self-improving agents: what changes, what persists, and how to prove it` (2026)
frames the proof as **four gates**, each catching a different failure mode, and
arXiv:2607.13104 §8 backs the same protocol. The "Fragility of Self-Improving
Agents" re-evaluation (arXiv:2608.18066) is why gate 2 exists: ReasoningBank
improved pass@1 by **+1.5** under a benchmark's default task order and **degraded
by -4.5** when the order was shuffled, and run-to-run variance exceeded the
no-memory baseline in 17 of 24 domain-level comparisons.

`ProceduralSkillMemory.verify_self_improvement()` runs all four and returns a
`SkillImprovementReport`:

| Gate | Question | Fails when |
| --- | --- | --- |
| `target` | does recall follow measured effectiveness? | a weaker-evidenced skill is recalled above a stronger one |
| `repetition_order` | does the ordering survive a shuffled input? | `trials` replays produce a different evidence ordering |
| `held_out` | does measured still beat unproven on a held-out context? | an unproven skill outranks a measured one off the selection context |
| `regression` | do prior successes survive? | a newcomer demotes the best-proven skill, or eviction prefers proven over weak evidence |

Invariants:

- **A gate is tri-state.** `passed is None` means the gate could not run and
  reports `not_run` with what was missing; it is never a pass.
- **The verdict is earned.** `verified` requires every *runnable* gate to pass and
  at least one to have run. With nothing to check the verdict is `unverified`.
- **Gate 2 compares evidence strengths, not names.** Two skills with identical
  evidence are interchangeable, so a tie swapping them between runs is a stable-sort
  artefact; flagging it would be a false positive. Names are still reported.
- **The verifier reads, it does not score.** Gates consume the memory's own
  recorded outcomes; nothing re-derives a score the thing under test also writes,
  which keeps the eval outside the boundary the literature says must hold: the
  system being evaluated cannot rewrite the test that certifies it.
- **`assert_no_worse_than_baseline` refuses to call a tie an improvement.**
  Identical orderings are `unchanged`; `improved` requires every baseline entry
  to survive at its original rank *or better* with the ordering extended.
  Anything else is `degraded`, including a legitimate-looking reorder, because
  the comparator cannot see the *why*.

Callers should run this before treating a skill library as improved, and should
report the verdict plus `trials` rather than only the outcome — a pass over 3
shuffled orderings is weaker evidence than one over 20, and the number is what
lets a reader weigh it. Regression coverage: `tests/test_cognitive_improvement_verification.py`.
## Autonomous upkeep (`autonomous_upkeep.py`)

Skills and memory were **on-demand**: nothing promoted or deprecated a skill and
consolidation only ran when a caller asked. `run_upkeep_pass()` is the driver that
runs unattended — ingest what the work did, reconcile every skill's lifecycle
against its measured evidence, replay what worked, then verify.

`CognitiveMemorySystem.run_upkeep()` is the owner-scoped entry point, and
`memory_upkeep_tick` (registered in `register_default_loops()` under
`autonomy.loops.memory_upkeep`, default 900s, model-free) sweeps every live owner
system. A loop supplies no work outcomes on purpose: a background tick cannot know
whether a recall was actually useful, and inventing that verdict would feed the
lifecycle fabricated evidence.

### The load-bearing safety property

**A `degraded` verification blocks the pass from acting.** The four gates run
*before* any transition is applied, and when the verdict is `degraded` the pass
reports what it would have done — marked `blocked:` — and applies nothing. This is
the boundary the self-improvement literature insists on (Arize 2026): the
component that discovers a failure must not also hold the authority to deploy its
own fix, and the system being evaluated must not rewrite the test that certifies it.

Invariants:

- Reconciliation walks **one legal step at a time**. `transition` refuses to skip
  verification, so a skill carrying conclusive evidence goes `proposed -> verified
  -> promoted` in two recorded actions rather than having the promotion forced
  through. A blocked pass shadows the immediate legal step only.
- **Demotion is real.** A promoted skill that falls below the promotion bar again
  demotes to `verified` (`promoted -> deprecated -> verified`, the recovery move
  added to `_TRANSITIONS` for exactly this). Leaving it promoted because it was
  promoted once is the stale state this loop exists to fix.
- **Two retirements, two reasons.** A deprecated-and-idle skill is retired for
  having failed its bar; a never-executed one is retired for reclaiming a slot,
  with the reason saying the idea was never judged. They are not the same event.
- A skill already in the state its evidence supports produces **no** action — a
  no-op reported as work is the smallest available over-claim.
- `ingested` counts records applied; an outcome naming an untracked skill is
  reported in `reasons` rather than silently dropped.
- "upkeep pass had nothing to do" is the honest report for a pass with no work and
  no warranted change.

Regression coverage: `backend/tests/test_cognitive_autonomous_upkeep.py`.
