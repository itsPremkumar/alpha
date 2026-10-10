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