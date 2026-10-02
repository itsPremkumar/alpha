# Continual Intelligence

> **What this is.** The layer that lets Alpha get measurably better at
> something, prove it, and reverse it if it did not — without ever asking a
> model whether it improved.
>
> **What this is not.** A second evolution engine. Alpha already has four
> strong ones; see [the audit](../ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md) for
> the measured version of that claim.

---

## 1. Read this first: the layer is default-off

```yaml
# config.yaml
intelligence:
  enabled: false
  mode: OBSERVE_ONLY
```

Out of the box Alpha **reads** real runtime state and **writes nothing**.
`GET /api/intelligence/*` answers from live subsystems on day one.

`mode` and `enabled` must agree. Declaring `mode: PROMOTE` with
`enabled: false` is a **config error at load**, because the write paths
correctly refuse and a configuration whose two halves disagree is exactly the
class of bug this repository's honesty rules exist to prevent.

`mode` is severity-ordered; each level implies the ones below it:

| Mode | May do |
| --- | --- |
| `OBSERVE_ONLY` | compute what *would* happen; write no learned state |
| `DRY_RUN` | additionally answer "what would change?" for given inputs |
| `EVALUATE_ONLY` | additionally run evaluations and regressions on a candidate |
| `TRIAL` | additionally let candidate experts execute on real work |
| `LEARN` | additionally record learning events and update learned state |
| `PROMOTE` | the only mode that may advance a candidate into the active set |

---

## 2. Why this exists, and what already existed

The design brief asked for an experience system, a sanitizer, a replay
reservoir, a regression harness, snapshots, a plasticity controller, an expert
fabric, paging, adaptive compute, and a self-knowledge API.

The audit found Alpha **already owns most of the pieces**:

| Requested | Alpha already had | Verdict |
| --- | --- | --- |
| Promotion gating | `alpha.rsi.promotion`, `alpha.evolution.engine` | **reuse** |
| Held-out evaluation | `alpha.rsi.holdout` (real, measured-only) | **reuse** |
| Evidence-based promotion | `alpha.evolution.evidence` (integrity, noise floors, hash chain) | **reuse** |
| Lineage | `alpha.rsi.lineage` (candidate-scoped) | **extend** |
| Atomic persistence | `alpha.persistence.storekit.atomic` | **reuse** |
| Capability-gap detection | `alpha.rsi.opportunity.mine` | **reuse** |
| Bounded budgets | `alpha.rsi.budgets`, `alpha.reasoning.budget` | **reuse** |
| Snapshots | `alpha.runtime.sentinel.checkpoint` | **reuse the pattern** |
| Experience store + hygiene | `alpha.learning.experience` | **index it, do not clone it** |
| Expert declarations | `alpha.experts.catalog`, `config.specialists` | **layer dynamics above** |

And it found these **genuinely absent**:

| Gap | Now lives in |
| --- | --- |
| Nothing ever *sampled* experience for learning | `alpha.intelligence.replay` |
| No staged sanitizer / no injection detection | `alpha.intelligence.sanitizer` |
| No train-vs-held-out *comparison* | `alpha.intelligence.regression` |
| No plasticity dial | `alpha.intelligence.plasticity` |
| No stable identity for a *learned* expert | `alpha.intelligence.expert_fabric` |
| No ranking / exploration | `alpha.intelligence.router`, `.scoring` |
| No disk→RAM→resident tier | `alpha.intelligence.paging` |
| No multi-signal difficulty estimator | `alpha.intelligence.difficulty` |
| No unified learning audit trail | `alpha.intelligence.journal` |
| No single self-knowledge view | `alpha.intelligence.self_knowledge` |

Every one of those is a **composition** gap — something that needed wiring to
what exists, not a replacement for it.

---

## 3. The replay reservoir

Alpha's experience bank *stored* records. Nothing ever selected them for
learning, so the newest domain slowly erased older knowledge.

```python
from alpha.intelligence.replay import ReplayItem, ReplayReservoir
from alpha.intelligence.models import ReplayStratum

reservoir = ReplayReservoir(capacity=512)
reservoir.add(ReplayItem(
    experience_id="exp_0421",
    strata={ReplayStratum.RARE_FAILURE, ReplayStratum.REGRESSION},
    importance=0.8, failure_value=0.9,
))

report = reservoir.sample(6, required_strata=(ReplayStratum.OLD_KNOWLEDGE,))
```

Three properties make forgetting structurally hard:

1. **Bounded.** Capacity is a ceiling, not a target. Eviction is by *lowest
   composite priority*, and every eviction is recorded in
   `stats()["recent_evictions"]`.
2. **Stratified.** `RECENT` and `OLD_KNOWLEDGE` are separate strata with
   separate weights, so a burst of new work cannot consume the whole budget.
3. **Required strata win first.** `required_strata` are honoured *before* the
   weighted draw — that is the mechanism, not a hope.

Priority is recency + importance + failure value + regression value + diversity,
using **half-life** rather than a cliff. A hard recent/not-recent boundary is
what makes a reservoir forget everything the moment the domain changes.

`oldest_age_seconds` is the direct measurement of whether retention is real. A
reservoir whose oldest item is minutes old is not a reservoir.

> The reservoir holds **references** (`experience_id` + ranking fields), never a
> second copy of the content. The bank stays the system of record.

---

## 4. The sanitizer

```
raw experience
  → secret detection      (delegates to alpha.learning.experience)
  → sensitive-data filter (same screen, metadata surface)
  → prompt-injection detection
  → provenance validation
  → quality validation
  → deduplication
  → experience candidate
```

**One denylist.** `find_secret_shape` from
`alpha.learning.experience.store` is called directly, so a pattern added to the
bank's screen protects this path too.

**Refusal is always total.** There is no "strip the secret and keep the rest"
path: redacting a credential out of a lesson produces a lesson that teaches
something slightly wrong, and storing it as clean is worse than refusing it.

**Metadata is walked value-first.** The shared assignment patterns match
`password=hunter2` but *not* `"password": "hunter2"` — the closing quote before
the colon defeats `\s*[:=]`. Screening the serialised document alone would let
the most common metadata shape through, so the walk reconstructs the
key/value pair before screening. Still one rule set, two surfaces.

**A skipped stage is not a pass.** With no digest index supplied, deduplication
reports `SKIPPED` with a reason. `PASSED` is reserved for a stage that ran.

Why injection detection exists: an experience is *data about* past work, and a
replayed experience is by construction fed back to a model. Instruction-shaped
text in stored content is an attack surface on the replay path.

---

## 5. Regression, held-out, and the promotion gate

The standing suite is 32 model-free property checks covering **all 20 required
capability categories** — atomic-write crash safety, protected-expert refusal,
trial routing exclusion, pinned-expert eviction, held-out id concealment, and so
on. `GET /api/intelligence/regressions` reports the coverage inventory.

```python
from alpha.intelligence.regression import evaluate_gate, CaseResult

outcome = evaluate_gate(candidate_run, baseline_run, heldout=heldout_run,
                        min_heldout_score=0.8, max_regression_delta=0.0)

outcome.decision   # PROMOTE | KEEP_TRIAL | REJECT | ROLLBACK
outcome.gates      # which gate refused, by name
outcome.reason     # the real blocker, not a list to interpret
```

Gates are evaluated in severity order and the **first refusal wins**, so
`reason` names the actual blocker:

1. candidate measured?
2. held-out regressions within tolerance?
3. held-out floor?
4. regression floor?
5. overfitting?
6. strictly better than baseline?

**Fail-closed by construction.** There is no parameter that lets an unverified
candidate promote. An empty result set is `score=0.5,
evidence_kind="unverified"` — neutral, never a pass — which mirrors the
contract already established in `alpha.rsi.holdout`.

**Overfitting is computed, not asserted.** Train rising while held-out is flat
or falling, with a gap over the configured bar, yields `possible_overfitting`
and a `REJECT`.

**Held-out ids never leak.** `to_candidate_payload()` withholds hidden case ids
and reports only their count. A candidate that can read the held-out case list
can optimise against it.

> **Scope note on "hidden".** The mechanism exists and is tested, but the 32
> shipped cases declare **none** of themselves hidden. They are property checks
> over Alpha's own invariants — the honest answer to "does Alpha still enforce
> its own rules" — and a property check has nothing to hide, because the
> implementation being checked is the answer. The genuinely hidden suite lives
> in `alpha.rsi.holdout` and is unchanged. If you want task-level held-out cases,
> declare them with `hidden=True`; `to_candidate_payload()` will withhold them.

---

## 6. Plasticity

*More self-modification is not more intelligence.*

| Outcome | Condition |
| --- | --- |
| `INCREASE` | held-out improving, no regression, acceptable failure rate |
| `MAINTAIN` | stable |
| `DECREASE` | no improvement, or a mild regression |
| `FREEZE` | forgetting, or a possible-overfitting signal |
| `ROLLBACK` | regression worse than `rollback_regression_delta` |

**A trend, not a point.** `decide()` refuses below `window` observations and
reports `insufficient_observations`. The reason is arithmetic, not caution: one
point has no slope.

**Tiers increase with how little history a surface has** — deliberately
backwards from "protect what is stable":

| Tier | Default | Why |
| --- | --- | --- |
| `core` | 0.05 | catastrophic forgetting lands here |
| `router` | 0.25 | cheap to adapt; regressions are local |
| `expert` | 0.60 | has lineage, can be pruned back |
| `new_expert` | 1.00 | a trial expert has no history to forget |

**Forgetting is measured against a recorded peak**, so it is a drop from a real
measurement rather than an arbitrary line — and `peak_heldout` only moves on an
actual held-out score.

---

## 7. The expert fabric

### Identity

```json
{ "expert_id": "expert_000421", "generation": 7, "parents": ["expert_000211"], "reason": "repeated backend debugging failures" }
```

Ids come from a **persisted monotonic counter** — `expert_%06d`, never an array
index. They survive restart, paging eviction, archival and reinstatement,
because none of those touch the counter.

### Lifecycle

```
PROPOSED → INITIALIZING → TRIAL → EVALUATING → PROMOTED → ACTIVE
                                                      ↓
                                        UNDERUSED → PRUNE_CANDIDATE
                                                      ↓
                                    ARCHIVED ⇄ PRUNED
```

`ExpertRecord.transition` refuses any edge not in the machine, naming both
states and the legal targets. **A dynamically created expert is born into
`TRIAL`, never `ACTIVE`** — the gate *is* the transition.

### Pruning is never silent

`evaluate_prune` checks six clauses — lifecycle, protection, contribution,
recent usefulness, grace period, replacement — and reports **all of them**,
including the ones that passed. A boolean would hide "it is too young" behind
`False`.

Every prune archives first, records the reason, and refuses outright on a
protected expert. `PRUNED` is terminal but reversible through `ARCHIVED`;
reinstatement goes back to `TRIAL`, because the evidence that justified the
original promotion is gone too.

---

## 8. Routing and exploration

```
candidates → ranking → exploration → admission
```

Only `ACTIVE` experts are routable in production. `TRIAL` requires
`allow_trial=True`. `PROPOSED`, `ARCHIVED` and `PRUNED` are never candidates —
an archived expert is not "available with a lower score".

Exploration is a **separate additive term**, capped by config, and it is
**structurally zero when `exploring=False`** — there is no code path where it can
influence production routing. Keeping it separate means every
`RouteDecision` reports `exploration_bonus`, so a reader can see how much of a
ranking came from curiosity.

The scorer is injected (`UtilityScorer` protocol). The shipped formula:

```
utility = quality_weight × quality_gain × reliability × reuse
          / (1 + compute_cost)  −  regression_weight × regression_impact
```

The `+ 1` is load-bearing. Without it, an **unobserved** expert (`0/0`) would
either divide by zero or score infinitely — making "nobody has ever used this"
look like the best expert in the pool. Unobserved experts also get a *neutral*
reliability of 0.5, not a perfect 1.0 and not a zero.

---

## 9. Paging

```
Disk (ExpertStore) → RAM cache (ExpertCache) → Resident (LRUResidentSet)
```

Two invariants make it safe:

1. **An executing expert is never evicted.** `hold()` pins for the caller's whole
   block; eviction skips pinned entries.
2. **Eviction is a cache miss, not data loss.** Nothing here deletes a record.

When every resident slot is pinned, admission is **refused with a reason** rather
than evicting a running expert or deadlocking.

> **No real VRAM accounting.** Alpha's experts are prompt/policy records, not GPU
> weights, so `max_resident` models "actively materialised", not bytes on a GPU.
> Wiring this to a real accelerator is local to `alpha.intelligence.paging`.

---

## 10. Adaptive compute

Seven optional signals: declared complexity, novelty, uncertainty, dependency
count, tool breadth, verification difficulty, historical failure rate.

**Unmeasured signals are excluded and the mean renormalised** — never defaulted
to zero. The alternative treats "unknown" as "easy", which is how a task with
seven measured complexity dimensions and no failure history gets routed as
trivial. The returned `coverage` says what fraction of signal weight was
actually measured; supplying nothing yields `coverage: 0.0`.

Bands allocate **hard ceilings**, and the point is that easy work gets a small
one:

| Band | Agents | Reviews | Independent approaches |
| --- | --- | --- | --- |
| `easy` | 1 | 0 | 1 |
| `medium` | 1 | 1 | 1 |
| `hard` | 3 | 2 | 1 |
| `very_hard` | 5 | 3 | 2 |

`should_continue_reasoning()` returns `STOP | CONTINUE | CHANGE_APPROACH |
DELEGATE | VERIFY`, checked in an order where **depth/budget come first** — a
controller that can answer `CONTINUE` after the budget is exhausted is not
bounded, whatever it says about confidence.

---

## 11. The learning journal

Append-only JSONL, hash-linked (`prev_hash` + `entry_hash`), tamper-detecting.

`verify_chain()` reports the **first broken index**, not a boolean, so a reader
can say which line is untrustworthy. A corrupt line is retained and counted,
never silently dropped.

**The journal is written even in `OBSERVE_ONLY`.** Observing is an event too: a
system that only journals its writes cannot be audited for the decisions it
*declined* to make.

---

## 12. Snapshots and bounded recovery

```python
result = snapshots.run_with_rollback(
    "candidate-label",
    apply=apply_candidate, evaluate=measure, restore=restore_snapshot,
    max_retries=2,
)
```

On budget exhaustion this **raises** `RetryBudgetExhausted`. It does not return
`False`, because a caller receiving `False` could not distinguish "rolled back
cleanly" from "gave up after twenty attempts". An exception from `apply()` is
also caught, restored, and counted as a failed attempt — a half-applied
candidate must not become the new baseline.

---

## 13. Self-knowledge and the API

Every field is read from a live subsystem. Nothing is inferred from a prompt or
filled in from a default that would read as a fact.

| Endpoint | Answers |
| --- | --- |
| `GET /api/intelligence/state` | everything below, one document |
| `GET /api/intelligence/mode` | effective mode + what it permits |
| `GET /api/intelligence/capabilities` | tools, skills, MCP servers, models |
| `GET /api/intelligence/experts` | declared + learned, labelled by source |
| `GET /api/intelligence/experts/weak` | weak vs **unmeasured** |
| `GET /api/intelligence/experts/{id}` | metrics + provenance |
| `GET /api/intelligence/experts/{id}/lineage` | ancestry, descendants, reason |
| `GET /api/intelligence/experts/{id}/prune-eligibility` | every clause, explained |
| `GET /api/intelligence/experts/graph` | node/edge projection |
| `GET /api/intelligence/learning` | in-flight + plasticity dial |
| `GET /api/intelligence/journal` | tail + chain integrity |
| `GET /api/intelligence/replay` | occupancy, strata, oldest age |
| `GET /api/intelligence/regressions` | suite coverage |
| `GET /api/intelligence/investigations` | ranked research proposals + refusals (Phase G) |
| `GET /api/intelligence/snapshots` | stored snapshots |
| `GET /api/intelligence/paging` | tiers and residency |
| `GET /api/intelligence/difficulty` | estimate + compute plan |

**Read-only by design.** No route mutates intelligence state. The learning
actions the brief describes (`dry-run`, `evaluate`, `snapshot`, `rollback`) are
library calls in `alpha.intelligence`, where the mode gate and the journal
actually live. Wiring them to HTTP without an authz and CSRF decision would add
a mutation surface nobody asked for.

Unavailable subsystems answer `{"available": false, "reason": "..."}`. One
broken optional subsystem must not make the whole endpoint useless — and a
fabricated capability claim is the most damaging thing an agent can get wrong
about itself.

---

## 14. What is deliberately not here

| Not implemented | Why |
| --- | --- |
| Full-model continual training | not needed for capability improvement; the brief's §56 agrees |
| A neural `ContinualLearner` | no current learner to put behind the seam |
| Real VRAM accounting | experts are records, not weights (see §9) |
| Cross-process journal | process-local, same as the swarm/workflow sinks — declared, not implied |
| HTTP mutation routes | see §13 |
| RSI promotion-path wiring | the evidence gate's own docstring marks this as unlanded; kept out of this change |
| Frontend section | `pnpm test` globs `src/lib/*.test.mjs` only; a section without its own script + CI step silently never runs. The API is complete; the UI is not built. |

---

## 15. Tests

```bash
cd backend
uv run pytest tests/test_intelligence_layer.py -q      # 166 tests
```

Covering: expert creation from a capability gap, promotion, rejection, prune
eligibility, protected-expert refusal, illegal-transition refusal, id survival
across prune/reinstate, paging load/eviction/pin-safety, replay old-knowledge
retention under flood, sanitizer refusals, unmeasured-never-passes, overfitting
detection, trend-required plasticity, unmeasured-is-not-easy difficulty,
journal tamper detection, bounded rollback.

**Tests:** `backend/tests/test_intelligence_layer.py`
**Config:** the `intelligence:` block in `config.example.yaml`
**Audit:** [`ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md`](../ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md)
---

## 16. Phases A–G: is the loop actually working?

Added per [`ALPHA_AGI_ASI_GAP_ANALYSIS_AND_PLAN.md`](../ALPHA_AGI_ASI_GAP_ANALYSIS_AND_PLAN.md).
These are **measurement** modules. The premise: more self-modification is not
more intelligence; Alpha's deficit is not insufficient self-modification but
insufficient measurement of whether it did what it claimed.

| Phase | Module | The question it can now answer |
| --- | --- | --- |
| A | `pathway.py` | Did the change arrive *by the route it claimed*? |
| B | `evaluator_stability.py` | How noisy is our own evaluator? |
| C | `diversity.py` | Did the action space survive the change? |
| D | `budget_protocol.py` | Can a cross-generation claim survive scrutiny? |
| E | `evidence_ledger.py` | Do all six subsystems agree? |
| F | `loop_health.py` | Is the loop improving, stable, or saturating? |
| G | `investigation.py` | What is worth investigating, and can that be falsified? |

### The three behaviours that matter most

**An unexplained score gain is refused.** A candidate claiming to change routing
whose routing decisions did not change, *whose score nevertheless rose*, is
rejected as `anomalous improvement`. That is "the number went up and I cannot say
why" — the condition that lets a broken loop look healthy indefinitely.

**A behaviour collapse overrides a score improvement.** This is the only gate in
the package that can reject a candidate that scored *better*. A narrowed agent
holding the same score is worse than one that can still do something else.
Detection needs **both** an absolute coverage floor and a material drop; either
alone fires constantly.

**An unmatched-budget comparison raises.** `compare_matched` refuses rather than
returning "close enough", which is the only thing that makes "generation 7 beats
generation 3" falsifiable — generation 7 may simply have had more attempts.

### Endpoint

`GET /api/intelligence/health` returns the regime, the bottleneck, and **one**
recommended action:

```json
{
  "regime": "insufficient_data",
  "scored_attempts": 0,
  "bottleneck": "not enough scored attempts to judge the loop",
  "recommended_action": "collect at least 3 scored attempts before acting on any loop-health signal",
  "required_subsystems": ["avo", "evolution_evidence", "rsi_promotion", "intelligence"]
}
```

`insufficient_data` is a **first-class regime**, not an empty response: a loop
with no scored attempts has not been measured, and reporting `stable` there would
be fabricated reassurance.

`GET /api/intelligence/investigations` completes the picture: what Alpha
currently thinks is worth investigating, and why anything was refused. It ranks
through the *same* `select_investigations()` the admission gate uses, so the
ordering an operator sees is the ordering that would be applied. With
`investigation_admission: false` (the default) it never commits budget.

### Config

```yaml
intelligence:
  regression:
    repeats: 3                    # Phase B
    noise_floor_source: max_of_both
    diversity_floor: 0.5          # Phase C
    diversity_min_drop: 0.1
    require_pathway_engaged: false # Phase A
    reject_anomalous_improvement: true
  required_subsystems: ["avo", "evolution_evidence", "rsi_promotion", "intelligence"]  # Phase E
  investigation_min_gain: 0.1     # Phase G
  investigation_admission: false  # dry-run: rank, spend nothing
```

An entry in `required_subsystems` naming an unknown subsystem is a **load error**,
because a typo would silently shorten the quorum and nothing would report it.

### Tests

```bash
cd backend
uv run pytest tests/test_intelligence_phases.py -q      # 116 tests
```