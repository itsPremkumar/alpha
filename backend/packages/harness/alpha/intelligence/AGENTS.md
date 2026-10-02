# `alpha.intelligence` — continual-intelligence layer

**Scope.** The layer that lets Alpha get measurably better at something, prove
it, and reverse it if it did not. It is **default-off** (`enabled: false`,
`mode: OBSERVE_ONLY`) and **additive**.

Operator guide: [`docs/CONTINUAL_INTELLIGENCE.md`](../../../../../docs/CONTINUAL_INTELLIGENCE.md).
Audit: [`ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md`](../../../../../ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md).

## What this package is not

It is **not** a second evolution engine, a second experience store, a second
memory, a second model router, or a second logging framework. Alpha already owns
strong implementations of all five:

| Do not build here | Already owned by |
| --- | --- |
| a promotion gate | `alpha.rsi.promotion`, `alpha.evolution.engine` |
| a held-out suite | `alpha.rsi.holdout` |
| an evidence/verdict bar | `alpha.evolution.evidence` |
| an atomic writer | `alpha.persistence.storekit.atomic` |
| an experience store | `alpha.learning.experience` |
| capability-gap detection | `alpha.rsi.opportunity` |
| budgets / cooldowns | `alpha.rsi.budgets`, `alpha.reasoning.budget` |
| a capability router | `alpha.capabilities.eligibility` |
| a benchmark runner | `alpha.benchmarks.runner` |

What was genuinely missing was **composition**: ways to sample stored experience,
compare a candidate against a held-out suite, hold a learned entity's identity,
page a pool, and audit the decisions. If you are about to add one of the rows
above, you are duplicating a subsystem, not filling a gap.

## The three invariants

Every module here obeys these. They are the reason the package is trustworthy.

1. **Unmeasured is not zero, and unmeasured is not a pass.**
   `ExpertMetrics.success_rate` is `None` before any observation.
   `EvaluationRun` with no executed cases is `score=0.5,
   evidence_kind="unverified"`. `Comparison.cost_delta` is `None`, never `0.0`,
   because cost is not tracked on `CaseResult` — a zero would read as "this
   change is free".

2. **A skipped stage is not a passed stage.**
   `SanitizerReport` distinguishes `PASSED` from `SKIPPED`. Deduplication with
   no digest index reports `SKIPPED` with a reason, so "we screened for
   duplicates" is never claimed for a check that did not run.

3. **Every refusal names a reason and the clause that refused.**
   `PruneDecision.reasons` reports all six clauses including the passing ones.
   `RouteDecision.excluded` carries a per-expert reason.
   `EvaluationOutcome.gates` names which gate declined. A boolean without a
   reason is not an acceptable return value here.

## Load-bearing details

**Lifecycle edges are enumerated, not implied.** `ExpertRecord.transition`
refuses any edge not in `_LEGAL_TRANSITIONS`, naming both states and the legal
targets. A dynamic expert is born into `TRIAL`, never `ACTIVE` — the gate *is*
the transition. `mark_trial_ready()` walks `PROPOSED → INITIALIZING → TRIAL`;
do not add a direct `PROPOSED → TRIAL` edge or the `INITIALIZING` node becomes
decorative.

**One secret denylist.** `sanitizer.py` calls
`alpha.learning.experience.store.find_secret_shape` directly. Do **not** add a
second pattern list. Note the JSON-quoting gap the shared assignment patterns
have (`password=hunter2` matches, `"password": "hunter2"` does not) — the
metadata walk reconstructs the key/value pair to close it at the call site.

**Exploration is structurally zero in production.** `exploration_bonus()` returns
`0.0` for every candidate when `exploring=False`. Keep it a separate additive
term rather than folding it into the utility score, so a reader can tell
curiosity from competence.

**A pinned expert is never evicted.** `PagingManager.hold()` owns the pin
window. When every resident slot is pinned, admission is *refused with a reason*
rather than evicting a running expert or deadlocking.

**The journal is written even in `OBSERVE_ONLY`.** Observing is an event; a
system that only journals its writes cannot be audited for the decisions it
*declined*. `_tail()` reads only the last line so append stays O(1) in journal
length; a corrupt final line starts a fresh segment (link → `GENESIS_HASH`) so
the break stays *visible* to `verify_chain()` instead of being healed invisibly.

**`run_with_rollback` raises.** On budget exhaustion it raises
`RetryBudgetExhausted`. Returning `False` would leave a caller unable to
distinguish "rolled back cleanly" from "gave up after twenty attempts".

**`mode` and `enabled` must agree.** `IntelligenceConfig` refuses
`mode > OBSERVE_ONLY` with `enabled: false` at load, because the write paths
correctly refuse and a config whose halves disagree is the class of bug this
repo's honesty rules exist to prevent.

## Boundaries

- **Read-only API.** `app/gateway/routers/intelligence.py` exposes **no**
  mutation route. The brief's `POST intelligence/evaluate|promote|rollback` are
  library calls behind the mode gate; wiring them to HTTP needs an authz + CSRF
  decision that was not made.
- **No background loop.** This package registers none. The supervisor still
  declares the same 9 loops; a default-off layer must not add background work.
- **Process-local.** The journal and reservoir hold an `RLock`, not a
  cross-process lease. Same declared limitation as the swarm/workflow sinks.
  Multi-worker deployments need these on the shared SQL event store.
- **No real VRAM accounting.** Experts are prompt/policy records, not GPU
  weights, so `max_resident` models "actively materialised". Wiring this to a
  real accelerator is local to `paging.py`.
- **No Sentinel integration yet.** Journal events are readable at
  `GET /api/intelligence/journal`; no `Signal` kind is emitted. Emitting signals
  from a default-off layer would create noise.

## Phases A–G — measuring whether the loop works

Added in `ALPHA_AGI_ASI_GAP_ANALYSIS_AND_PLAN.md`. These are **measurement**
modules, not new capability. Each closes a gap that was a measured absence.

| Phase | Module | Closes |
| --- | --- | --- |
| A | `pathway.py` | did the change arrive by the claimed route? |
| B | `evaluator_stability.py` | measure the verifier's own noise |
| C | `diversity.py` | did the action space survive? |
| D | `budget_protocol.py` | are cross-generation claims falsifiable? |
| E | `evidence_ledger.py` | six subsystems, one verdict |
| F | `loop_health.py` | is the loop working? |
| G | `investigation.py` | what is worth investigating? |

### Phase-specific load-bearing rules

**A — a probe that cannot run must never return `True`.** It returns
`engaged=None` → `INCONCLUSIVE`, which blocks promotion. `NOT_ENGAGED` combined
with an improved score is `is_anomalous_improvement` — "the number went up and I
cannot say why" — and is the condition that lets a broken loop look healthy.

**B — a noise floor estimated from one sample is not a floor.** Fewer than
`min_repeats` observations, or all-identical observations, yield
`observed=False`. `resolve_noise_floor(source="max_of_both")` (the default) can
only make the gate **stricter**, so a measurement can never become a licence to
accept a smaller improvement.

**C — coverage is measured against the BASELINE**, not against the trace's own
distinct set (that is `|S|/|S|` = 1.0, always). `normalize_action` reduces
**per value**, not per key: keeping only keys would make `bash(cmd=ls)` and
`bash(cmd=rm -rf)` the same behaviour, which is backwards for an action-space
measure. Both clauses (below-floor **and** material-drop) are required.

**D — `compare_matched` RAISES on a budget mismatch.** Returning a delta would
reintroduce exactly the unfalsifiable claim this phase exists to prevent.
`BudgetUnit.tokens` is `None` when uninstrumented, never `0.0`.

**E — absence is not approval, and a rejection is not outvoted.** An unevaluated
subsystem yields `PARTIAL`, never `PASS`. There is no averaging: subsystems are
gates, not voters.

**F — `insufficient_data` is a first-class regime.** Never report `stable` for a
loop with no scored attempts. The bottleneck is chosen by **severity**, not
iteration order, so identical inputs always name the same limiting factor.

**G — `falsifiable_by` is required and must name a measurement.** A proposal
failing it is an enthusiasm, not a research direction. Selection is itself gated
by Phase E convergence, because choosing what to investigate is the judgment the
research says agents are worst at.

### Phase A and C reach the promotion gate

`evaluate_gate(..., pathway=..., diversity=..., noise_floor=...)`. Absent evidence
does not block (an unchecked mechanism is not a refusal). `diversity` is the
**only** gate that can reject a candidate that scored better — deliberately.

### Phase H — curiosity and the trust gate

Added from the SI-Agents survey taxonomy. `alpha.agency.curiosity.CuriosityScorer`
already existed but had **zero production callers** and an in-memory novelty map,
so novelty reset to `1.0` on every restart. `curiosity.py` makes it durable,
adds **verifier disagreement** as the third intrinsic signal, and wires it to the
reservoir as a capped ordering term.

**The degeneracy guard is load-bearing.** High curiosity + zero competence is
refused with `EXPLORATION_WITHOUT_COMPETENCE` — a decision *not* to explore.
Ranking breaks ties by **competence before curiosity**. The literature's warning
is that a pure novelty bonus rewards the strangest available thing, and strange
is not informative.

**The trust gate is off by default.** `Stage.TRUST_VALIDATION` reports
`SKIPPED` (never `PASSED`) while `admit_untrusted=True`, because every existing
caller supplies `evidence` and no source attribution. Do not make it mandatory
without migrating those callers first. When enabled, an unrecognised level is
refused, not assumed safe.

## Tests

```bash
cd backend
uv run pytest tests/test_intelligence_layer.py tests/test_intelligence_phases.py -q
# 168 + 116 tests
```

The class names map to the audit's "genuinely absent" list and then to the
plan's phases, so a failure names the gap it covers: `TestConfigContract`,
`TestExpertFabric`, `TestPruningSafety`, `TestRouterAndScoring`, `TestPaging`,
`TestReplayReservoir`, `TestSanitizer`, `TestRegressionAndGate`,
`TestPlasticity`, `TestDifficulty`, `TestContinueDecision`,
`TestJournalAndSnapshots`, `TestSelfKnowledge`, `TestExperienceTelemetry`;
then `TestPhaseAPathway`, `TestPhaseBEvaluatorStability`, `TestPhaseCDiversity`,
`TestPhaseDBudgetProtocol`, `TestPhaseEEvidenceLedger`, `TestPhaseFLoopHealth`,
`TestPhaseGInvestigation`, `TestGateIntegration`.

`test_capability_projections_read_live_state` is not decorative: the
self-knowledge projections were originally written against three import paths
that do not exist in this repository, and the honest-failure wrapper turned them
into permanently-unavailable blocks. It pins the real accessors.