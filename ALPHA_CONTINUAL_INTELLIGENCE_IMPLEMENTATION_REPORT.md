# Alpha — Continual Intelligence Implementation Report

Branch: `feature/continual-intelligence`
Base: `6ebd170` (main)
Commits: `df1f60f` (implementation), `5736970` (documentation), plus the
self-knowledge import fix described in §7.

This report separates **implemented**, **partially implemented**, and **future**
throughout. Nothing below claims more than was measured.

---

## 1. The headline

**The design brief's premise was inverted, and the honest response was to fill
the measured gaps rather than build the requested architecture from scratch.**

Alpha already owned roughly 46,000 lines across four independently-hardened
evolution subsystems (`alpha.rsi` 6,357 / `alpha.evolution` 7,190 /
`alpha.evolution.evidence` ~127 kB / `alpha.config.self_tuning` ~150 kB), plus an
experience bank, an evidence gate, a real held-out suite, atomic persistence with
fault injection, capability-gap mining, protected paths, budgets, cooldowns, a
Sentinel, and Git-guarded source updates.

Of the brief's ~78 numbered requirements, the audit classified the large majority
as **already implemented and reusable**. What was genuinely absent were ten
*composition* gaps — things that needed wiring to what exists.

Building the brief literally would have produced a second evolution engine, a
second experience store, a second memory, a second model router, and a second
atomic-write helper — which the brief's own rule 78 and the repository's
orphan-module CI gate both forbid.

---

## 2. What Alpha already had (and what was kept)

Nothing below was modified or removed. Each is *reused* by the new layer.

| Existing subsystem | Location | How the new layer uses it |
| --- | --- | --- |
| Experience bank | `alpha.learning.experience.{models,store,hygiene}` | indexed by the reservoir; sanitised by the new pipeline; its secret screen is called, not duplicated |
| Promotion routing | `alpha.rsi.promotion.route_promotion_decision` | the gate this layer feeds |
| Held-out suite | `alpha.rsi.holdout` | its `evidence_kind="measured"`-only contract is adopted verbatim |
| Evidence gate | `alpha.evolution.evidence` | untouched; still default-off and unwired, as its own docstring states |
| Evolution engine | `alpha.evolution.engine` | untouched |
| Atomic writes | `alpha.persistence.storekit.atomic` | the single writer for every new document |
| Benchmark runner | `alpha.benchmarks.runner` | the regression suite registers on it |
| Capability matching | `alpha.capabilities.eligibility` | left alone; ranking is layered above it |
| Reasoning budget | `alpha.reasoning.budget`, `loopguard` | untouched |
| Capability-gap mining | `alpha.rsi.opportunity.mine` | untouched |
| Expert catalogue | `alpha.experts.catalog`, `.registry` | declarations stay authoritative; dynamics layer above |
| Specialists | `config.yaml -> specialists` | untouched |
| Sentinel, trace context, event bus, Git worktrees | `alpha.runtime.*`, `alpha.trace_context`, `alpha.evolution.git_source` | untouched |

**Preservation check:** the diff adds files and one field to `AppConfig`, one
router to `app.py`, one section to `config.example.yaml`, and one entry to the
docs classifier. No existing module was edited other than those three
registration points.

---

## 3. Files changed

### New — `backend/packages/harness/alpha/intelligence/` (15 modules)

| File | Lines | Role |
| --- | --- | --- |
| `regression.py` | 681 | 32-case suite, comparison, promotion gate |
| `expert_fabric.py` | 575 | stable ids, lineage, lifecycle, safe pruning |
| `replay.py` | 508 | bounded, stratified replay reservoir |
| `__init__.py` | 451 | `:pep:`562` lazy exports |
| `models.py` | 427 | `LearningMode`, `ExpertIdentity`, `ExpertRecord`, `ExpertMetrics`, `ExperienceTelemetry`, `LearningEvent`, lifecycle state machine |
| `paging.py` | 395 | disk/RAM/resident tiers + eviction |
| `sanitizer.py` | 392 | 6-stage sanitisation pipeline |
| `plasticity.py` | 387 | trend-driven tiered controller |
| `difficulty.py` | 339 | multi-signal estimator + compute plan |
| `self_knowledge.py` | 311 | live read-only projection |
| `journal.py` | 298 | hash-linked learning journal |
| `snapshots.py` | 279 | bounded snapshot/rollback |
| `router.py` | 263 | candidates → ranking → exploration → admission |
| `config.py` | 149 | the `intelligence:` config section |
| `scoring.py` | 110 | replaceable utility scorer |
| **Total** | **5,565** | |

### New — elsewhere

- `backend/app/gateway/routers/intelligence.py` — 203 lines, 16 read-only routes
- `backend/tests/test_intelligence_layer.py` — 168 tests
- `ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md` — the audit
- `ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md` — this file
- `docs/CONTINUAL_INTELLIGENCE.md` — the operator guide

### Modified — registration points only

- `backend/packages/harness/alpha/config/app_config.py` — +1 field, +1 import
- `backend/app/gateway/app.py` — +1 import, +1 `include_router`
- `config.example.yaml` — +104 lines, `config_version` 57 → 58
- `contracts/feature_manifest.json` — regenerated (routers 63 → 64, engines 113 → 114)
- `scripts/generate_docs_index.py` — +1 `FILE_OVERRIDES` entry
- `docs/INDEX.md` — regenerated
- `README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md`, `docs/COMPARISON.md`, `AGENTS.md` — capability counts

---

## 4. Mini-AGI concepts adopted, and where

Taken deliberately; the toy language model, byte tokenizer, hyperparameters,
expert counts and thresholds were **rejected**.

| Concept | Adopted as | Where |
| --- | --- | --- |
| Prioritised replay across strata | `ReplayReservoir.sample(required_strata=…)` | `replay.py` |
| Plasticity control | five outcomes driven by a trend, never a point | `plasticity.py` |
| Train/held-out separation + gap | `compare()` computes `overfitting_gap` | `regression.py` |
| Stable expert identity + generation counter | `expert_%06d` from a persisted counter | `expert_fabric.py` |
| Expert growth / trial / promotion / pruning | `TRIAL`-born lifecycle, archive-before-prune | `expert_fabric.py` |
| Disk-backed pool with eviction | `ExpertStore` / `ExpertCache` / `LRUResidentSet` | `paging.py` |
| Adaptive recurrent computation | `should_continue_reasoning()`, budget-first ordering | `difficulty.py` |
| Explicit rollback on degradation | `run_with_rollback` that *raises* when spent | `snapshots.py` |
| Self-reporting | live-read-only `SelfKnowledgeService` | `self_knowledge.py` |

---

## 5. Requirement-by-requirement status

### Implemented and tested

| Brief § | Requirement | Status | Test |
| --- | --- | --- | --- |
| 5 | Experience stream | **partial** — `ExperienceTelemetry` added as an *optional* block; existing `ExperienceRecord` shape untouched | `test_legacy_record_without_telemetry_still_loads` |
| 6 | Experience sanitisation | **implemented** — 6 stages, one denylist | 11 tests |
| 7 | Replay reservoir | **implemented** — bounded, 12 strata, half-life recency | 14 tests |
| 8 | Regression harness | **implemented** — 32 cases, all 20 categories | `test_every_required_category_is_covered` |
| 9 | Before/after evaluation | **implemented** — 7 axes + overfitting gap | 8 tests |
| 10 | Promotion decisions | **implemented** — 6 named gates, first-refusal-wins | 8 tests |
| 11 | Snapshot + rollback | **implemented** — bounded, raises when spent | 13 tests |
| 12 | Plasticity controller | **implemented** — 5 outcomes, trend required | 14 tests |
| 13 | Learning tiers | **implemented** — 4 tiers as config | `test_tier_multipliers_are_increasing…` |
| 15 | Stable expert ids | **implemented** — persisted counter | 3 tests |
| 16 | Expert lineage | **implemented** — ancestry/descendants/reason | 2 tests |
| 17 | Capability router | **implemented** — 4 stages | 9 tests |
| 18 | Exploration | **implemented** — structurally zero unless exploring | 3 tests |
| 19 | Dynamic creation | **implemented** — `propose()` requires a reason | 3 tests |
| 20 | Expert lifecycle | **implemented** — 10 states, enforced machine | 3 tests |
| 21 | Expert scoring | **implemented** — replaceable protocol | 4 tests |
| 22 | Expert pruning | **implemented** — 6 clauses, archive-first, protection absolute | 6 tests |
| 23 | Paging | **partial** — 3 tiers + eviction; no real VRAM accounting | 11 tests |
| 25 | Atomic persistence | **implemented** — via `storekit.atomic` | 3 tests |
| 26 | Adaptive compute | **implemented** — 7 signals, 4 bands, ceilings | 8 tests |
| 27 | Ponder/continue | **implemented** — 5 outcomes, budget-first | 8 tests |
| 28 | Bounded reasoning | **implemented** — depth/budget checked first | 2 tests |
| 29–31 | Replay+regression, train/held-out split, overfitting | **implemented** | 4 tests |
| 32–33 | Self-knowledge + API | **implemented** — 16 read-only routes | 14 tests |
| 34 | Learning journal | **implemented** — hash-linked, tamper-detecting | 7 tests |
| 45 | Crash recovery | **implemented** — corrupt state fails loudly | 3 tests |
| 51 | No silent fallback | **implemented** — every refusal carries a reason | throughout |
| 54 | Configuration | **implemented** — nothing hardcoded | 7 tests |
| 64 | Version everything | **implemented** — schema_version on every doc | — |

### Partially implemented

| Brief § | Requirement | What is missing |
| --- | --- | --- |
| 5 | Experience stream | no automatic capture hook from the run worker; `ExperienceTelemetry` must be attached by a caller. Wiring `runtime/runs/worker.py` was out of scope for a layer that ships default-off |
| 23 | Paging | no real VRAM/byte accounting. Experts are prompt records, not GPU weights; `max_resident` models "materialised", not bytes |
| 44 | Resource-aware learning | `alpha.config.self_tuning.dynamics.ResourceGovernor` exists and is reusable, but the intelligence layer does not yet gate itself on it |
| 46 | Experiment isolation | `experiment_id` is carried on `LearningEvent`, but no per-experiment directory layout exists |
| 50 | Learning explainability | `LearningEvent` records before/after/regression/decision/reason; there is no dedicated "explain this promotion" endpoint |

### Not implemented, and why

| Brief § | Requirement | Why |
| --- | --- | --- |
| 33 (part) | `POST` intelligence mutation routes | Deliberately withheld. No route mutates learned state; the brief's `POST intelligence/evaluate|promote|rollback` would need an authz + CSRF decision nobody requested. Actions are library calls behind the mode gate |
| 35 | Sentinel integration | The Sentinel exists and is untouched. Learning events reach `GET /api/intelligence/journal`, but no new `Signal` kind is emitted. Deliberate: a default-off layer emitting signals would create noise |
| 36 | RSI promotion-path wiring | `alpha.evolution.evidence`'s own docstring states it is "not wired into any promotion path yet". Wiring it is real work with real risk and belongs in its own change |
| 48–50 | Frontend UI section | Not built. `pnpm test` globs `src/lib/*.test.mjs` only, so a section without its own script + CI step silently never runs. The API is complete and documented; the UI is honestly absent |
| 56–57 | Neural `ContinualLearner` | No current learner to put behind the seam. Adding an abstraction with one no-op implementation is scaffolding, and the report refuses to call scaffolding complete |
| 63 | Multi-machine intelligence sync | Deliberately out of scope; the journal is process-local and says so |
| 37 | Git/worktree integration for learned state | Learned state is deliberately **not** source code. Committing it would violate the separation the brief also asks for |
| 38 | Multi-agent role capture | `ExperienceTelemetry.role_ledger()` exists and is tested, but nothing populates it automatically |

---

## 6. Tests

### New suite

```
cd backend
uv run pytest tests/test_intelligence_layer.py -q
→ 168 passed
```

### Existing gates re-run

| Gate | Result |
| --- | --- |
| `ruff check` (intelligence + router + tests + app_config) | clean |
| `ruff format --check` | clean |
| `tests/test_feature_manifest_wiring.py` | pass |
| `tests/test_docs_index.py` | pass (3) |
| `tests/test_docs_claim_honesty.py` | pass |
| `tests/test_infra_audit_docs_index_classification.py` | pass |
| `tests/test_self_documentation_index.py` | pass |

### Pre-existing failure, verified out of scope

`tests/test_no_orphan_modules.py::test_no_orphan_modules` fails on
`alpha.tools.discovery.code_mode`.

**Verified pre-existing:** the change set was stashed with `git stash
--include-untracked` and the test was re-run against the clean tree. It fails
identically. The module was last touched by commit `1dde8eb`, which is not in
this branch. Left alone deliberately — fixing an unrelated orphan scan is a
different change.

### Live route verification

All 16 `/api/intelligence/*` routes were exercised through a `TestClient` with a
real `config.example.yaml`. All returned 200. Representative results:

```
/api/intelligence/mode      → {'enabled': False, 'mode': 'OBSERVE_ONLY',
                               'permits': {all False}}
/api/intelligence/experts   → declared_count 8, learned_count 0, status_counts {}
/api/intelligence/replay    → size 0, capacity 512, oldest_age_seconds None
/api/intelligence/regressions → case_count 32, categories 20/20, complete True
/api/intelligence/difficulty?declared_complexity=0.9&historical_failure_rate=0.8
                             → band very_hard, coverage 0.65
/api/intelligence/difficulty (no signals)
                             → coverage 0.0   ← unmeasured is not easy
/api/intelligence/experts/expert_999999 → 404 with the real reason
```

---

## 7. Three real bugs the tests caught

Recording these because they were found by the tests, not by inspection.

**1. The router contradicted itself.** `RouteDecision.considered` and
`RouteDecision.admitted` were built from separate copies of each candidate, so
an admitted expert appeared in `considered` with `admitted=False` and no reason.
A reader checking `considered` for exclusion reasons would find an admitted
expert with an empty explanation. Fixed by rewriting admitted entries in place so
`considered` is one consistent view.

**2. The sanitizer's metadata stage was unreachable.** The prose stage used
`record_text()`, which *already includes* `metadata` — so a credential in
metadata was always caught at stage 1, and stage 2 could never fire. Split into
`prose_text()` (prose only) and a separate metadata walk. One denylist, two
reachable surfaces.

**3. The secret denylist could not see JSON keys.** The shared patterns match
`password=hunter2` but not `"password": "hunter2"` — the closing quote before
the colon defeats `\s*[:=]`. The metadata walk therefore reconstructs the
key/value pair before screening. Still one rule set; the gap is closed at the
call site rather than by forking the bank's denylist.

Plus **three wrong imports in `self_knowledge.py`**, found by a live route check
rather than by the unit tests: `alpha.tools.get_builtin_tools`,
`alpha.skills.list_skills`, and `alpha.mcp.cache.get_cached_tools` do not exist.
The honest-failure wrapper turned all three into permanently-`available: false`
blocks — precisely the "capability exists but is always unavailable" reading this
service exists to prevent. Fixed to the real accessors
(`get_available_tools`, `storage.load_skills(enabled_only=True)`,
`get_cached_mcp_tools`) and pinned by
`test_capability_projections_read_live_state`.

---

## 8. Performance

No benchmark claims. What is structurally true:

- **Nothing runs in the request path.** Every `/api/intelligence/*` route is
  read-only and offloads blocking work through `asyncio.to_thread`.
- **No learning loop was added.** The supervisor still declares the same 9 loops.
  A default-off layer must not add background work.
- **Writes are O(1) amortised.** The journal append reads only its last line;
  the reservoir evicts one item per overflow rather than rewriting the pool.
- **Documents are bounded.** Replay `capacity`, snapshot `retain` (20), journal
  unbounded *by design* (it is the audit trail) but append-only.
- The 168-test suite runs in ~85 s on this machine; the slowest single test is
  `test_max_retries` at under a second. No test sleeps to pass.

**Not measured:** CPU/RAM/VRAM/disk footprint of a populated fabric, because no
fabric was populated in a long-running process. A 64-expert fabric at ~1 kB per
record is ~64 kB of JSON — small enough that measuring it would be noise.

---

## 9. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Learning actions are library calls with no HTTP surface | medium | Intentional. Anyone needing automation must call from a background loop behind `autonomy.loops`, which is the pattern the repo already uses |
| Process-local journal and reservoir | low | Same declared limitation as the swarm/workflow sinks. Stated in every module docstring, not implied |
| `ExpertFabric` corrupt file fails the whole constructor | low | Deliberate — a corrupt registry must be visible, not silently reset. `ExpertFabricUnreadable` names the path and the reason |
| Exploration could degrade routing if enabled carelessly | low | Structurally zero unless `exploring=True`, and capped by config |
| The suite's 32 cases are model-free property checks, not task execution | medium | Stated plainly. They prove Alpha's invariants hold; they do not prove task quality. The real per-category behavioural suites are the existing subsystem tests |
| No frontend section | low | Documented as not-implemented; the API is complete |

---

## 10. What comes next

**Immediate (highest value, lowest risk)**

1. **Wire experience capture to the run worker.** `ExperienceTelemetry` is
   tested and ready; attaching it in `runtime/runs/worker.py` makes the reservoir
   populate automatically. This is the single change that would make the layer
   useful end-to-end.
2. **Wire the existing evidence gate in** as the pre-promotion bar. Its own
   docstring says it is unwired; the promotion gate here is the natural seam.
3. **Gate learning on `ResourceGovernor`.** `self_tuning.dynamics` already computes
   resource signals; refusing to learn under pressure is a small, honest change.

**Next**

4. A frontend section *with* its own test script and CI step — never without.
5. Per-experiment isolation directories.
6. An `explain(promotion_id)` endpoint over the existing journal.

**Research mode, explicitly deferred**

7. A neural `ContinualLearner` behind the `UtilityScorer`-style protocol — only
   when there is a real learner to put behind it.

**Not recommended**

8. Full-model continual training. The brief's own §56 agrees, and nothing
   measured here suggests capability is limited by model weights rather than by
   retrieval, routing, and evidence.

---

## 11. Honesty statement

- No benchmark improvement is claimed. **None was measured.**
- No model was trained. The layer does not touch model weights.
- The audit's premise correction is stated plainly because it is the most
  important finding: Alpha was not missing an evolution engine, and building one
  would have been the wrong move.
- Six requirements from the brief are **not implemented** (§33 mutations, §35
  Sentinel, §36 RSI wiring, §48–50 UI, §56–57 neural learner, §63 multi-machine).
  Each is listed with the reason rather than quietly omitted.
- The pre-existing orphan-module failure is reported as pre-existing and
  verified as such, not claimed as fixed.