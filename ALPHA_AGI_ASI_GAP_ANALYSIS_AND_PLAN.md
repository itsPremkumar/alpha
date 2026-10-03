# Alpha — AGI/ASI Capability Gap Analysis & Next Implementation Plan

Branch: `feature/continual-intelligence`
Companion to: [`ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md`](ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md),
[`ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md`](ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md)

**Scope.** This document answers one question with evidence: *given what the
2026 literature says separates a self-improving agent from one that merely
edits itself, what is Alpha actually missing?* Every claim below is either a
cited research finding or a `grep`/`read` result against this repository at
`3d5a7d9`. Nothing is inferred.

---

## 1. What the research says

Five 2026 sources define the current frontier. Their most useful contribution is
a **level ladder** that separates "improves itself" from "improves its own
improvement".

### 1.1 The L0–L5 autonomy ladder

From *The Last AI Built by Humans* (arXiv 2609.11873), which separates what the
AI takes over from what humans keep:

| Level | Who acts | What changes |
| --- | --- | --- |
| **B0** | — | output improves, **no system change retained** (explicitly a *non-RSI* reference point) |
| **L1** | AI executes | a **human-prescribed** improvement |
| **L2** | AI chooses *how* | picks the method, under external objectives and acceptance criteria |
| **L3** | AI chooses *what to learn from* | selects its own future learning experience |
| **L4** | AI retains | deployment experience persists under external governance |
| **L5** | AI revises | **a mechanism that governs later improvement** is itself changed |

Then the distinction that matters most:

> **Structural recursion** = an inherited change to the improvement mechanism
> governs a later round.
> **Effective recursion** = that mechanism improves *later successors* under
> *comparable budgets* and *independent assessment*.

**L5 is the only level Alpha has not reached.** Everything below is reached.

### 1.2 The execution/strategy split

*What is Missing from AI Post-Training* (arXiv 2608.19072) separates:

- **Execution-level capability** — iterating *within* an established strategy.
- **Strategy-level capability** — revising *that strategy* as evidence accumulates.

Aggregate benchmark scores cannot distinguish these. Most published
"self-improvement" is execution-level.

### 1.3 Empirical consensus: loops saturate

The most important negative result, from *Self-reference in LLMs* (arXiv
2607.04277) and MIT Technology Review (Aug 2026):

- Self-Refine plateaus **after a few iterations**.
- "Current self-improvement consistently saturates, internal verification is
  fragile, and self-modification remains surface-level."
- **The primary bottleneck is the unreliability of the internal feedback loop.**
- The NeurIPS-2026 study: agents could do *all the engineering* of open-ended AI
  research but were "unambiguously bad at carrying out the research itself" —
  judgment and taste were the gap.
- Experts quoted in CACM (Jul 2026) argue most current "RSI" is **"compression,
  not frontier expansion"** — faster work inside existing capability limits.

### 1.4 Pathway evidence

*PAST-Bench* (arXiv 2608.04003) is the sharpest instrument here:

> "Agents with the same headline gain can differ markedly in whether that gain is
> supported by evidence of the intended pathway."

They separate *later-task gain* from *save→retrieve→update pathway evidence*, and
show the two are only loosely coupled. A persistent agent can score better for
reasons unrelated to its memory system.

### 1.5 The primary risks of self-improvement

From *Self-Improvements in Modern Agentic Systems: A Survey* (arXiv 2607.13104):

> "Training on self-generated signals can **reinforce errors**, **narrow the
> range of behaviors** the model produces, or **overwrite earlier competence**
> when the new training is too narrow."

And the definitional boundary worth copying verbatim:

> "Ordinary dialogue and working notes count as **execution state, not
> self-improvement**. A debugging reflection that **rewrites a persistent
> instruction**, annotates a memory store, patches a tool, or adjusts model
> weights **does** count."

---

## 2. Alpha against the ladder

Measured against this repository, not against the brief.

| Level | Alpha's implementation | Verdict |
| --- | --- | --- |
| **B0/L1** — execute a prescribed improvement | `alpha.evolution.engine.EvolutionEngine.propose/gate/rollback`; `alpha.rsi.engine.RSIEngine.run_rsi_cycle` | **reached** |
| **L2** — choose *how* | `alpha.rsi.opportunity.mine` picks which bottleneck to attack; `alpha.avo.supervisor.AVOSupervisor` issues `StrategicPivotDirective` (`STAGNATION_PIVOT`, `OSCILLATION_BREAK`, `CORRECTNESS_BLOCKED`) on `consecutive_stagnation` | **reached** |
| **L3** — choose *what to learn from* | `alpha.intelligence.replay.ReplayReservoir.sample(required_strata=…)` | **reached (new)** |
| **L4** — retain deployment experience | `alpha.rsi.strategy_memory.StrategyMemory`; `alpha.avo.knowledge.DomainKnowledgeBase.record_positive_pattern` / `record_negative_lesson` | **reached** |
| **L5** — revise the improvement mechanism | **nothing** | **NOT reached** |

### 2.1 Correction to my own earlier audit

My first audit catalogued **four** evolution substrates. It missed a **fifth**:

```
alpha/avo/   3,079 lines, 14 modules
```

`alpha.avo` is arguably the most rigorous of the six subsystems now in the
repository:

- `scorer_authority.py` — `SERVER_OWNED_SURFACE`,
  `ScorerAuthorityViolation`, `CandidateTargetRefused`. **A candidate physically
  cannot author its own fitness function.** This is the direct defence against
  evaluator capture, which §1.3 names as the core bottleneck.
- `commit_gate.py` — `InvariantOracle`, `CommitGate`, `PromotionDecision`.
- `lineage_chain.py` — `seal_entry`, `verify_entries`, `ChainVerdict`.
- `honesty.py` — `assert_gains_carry_denominators`. (A gain without a denominator
  is refused.)

Wired via `avo_lineage_tool`, `variation_operator_tool`, and
`routers/projects.py`.

**Total evolution surface after this work: six subsystems, ~52k lines.**

---

## 3. The gaps

Each is a measured absence, with the search that established it.

### GAP-1 — No pathway evidence (severity: highest)

```
grep -l 'ablation|counterfactual|pathway_attribution|expected_pathway'
  over 1,822 files in alpha/          → 0 files
```

Alpha measures **whether** a candidate scored higher. It never measures
**whether the gain arrived through the mechanism the candidate was supposed to
change.** Per PAST-Bench, those are only loosely coupled.

Concretely: a routing change that raises the benchmark because it happened to
match the eval set's phrasing scores identically to one that genuinely improved
routing. Alpha cannot tell them apart, so it cannot prefer the second.

This is the single most valuable thing to build, because it is the difference
between "the loop works" and "the loop works *for the reason we think*".

### GAP-2 — No behaviour-collapse detection (severity: high)

```
grep -l 'behavioral_diversity|narrowing|entropy_of_behav|diversity_preserv|behavior_collapse'
  over 1,822 files                     → 0 files
```

(The 14 `narrowing` hits are unrelated — authority-ceiling trimming and
tool-output budget markers.)

Alpha has **overfitting detection** (train↑ while held-out flat/down → reject).
That measures *score* overfitting. It has **nothing** measuring whether the
*behaviour distribution* narrowed — the survey's second-listed primary risk.

The failure this would catch: Alpha learns that one strategy scores well,
suppresses everything else, and its aggregate score holds steady while its
capability surface quietly collapses.

### GAP-3 — No equal-budget cross-generation comparison (severity: high)

```
grep -l 'equal_budget|same_budget|matched_budget'   → 0 files
```

"Effective recursion means that mechanism improves later successors under
*comparable budgets*." Alpha has baselines, so it can say generation 7 beat
generation 3 — but **generation 7 may simply have had more attempts.** Without
a matched-budget protocol, every cross-generation claim is unfalsifiable.

This directly blocks the L5 claim. Until it exists, Alpha cannot demonstrate
that a learned mechanism made *later* learning cheaper or better.

### GAP-4 — No evaluator-stability measurement (severity: high)

```
grep -l 'evaluator_stability|stability_of_eval|repeat_measure|autocorrel|score_variance'  → 0 files
```

§1.3 says the core bottleneck is that **internal verification is fragile**.
`alpha.evolution.evidence.compare` has noise *floors* — an excellent instinct —
but a floor is a constant, not a measurement. Alpha never re-measures the
*same* candidate to learn how much its own evaluator wobbles.

Consequence: when a candidate "improves" by +0.03, Alpha cannot say whether
that is signal or evaluator noise, because it has never characterised the noise.

### GAP-5 — Six subsystems, zero shared outcome evidence (severity: high)

| Substrate | Owns | Cannot see |
| --- | --- | --- |
| `alpha.avo` | scorer authority, invariant oracle, commit gate | whether RSI/evolution saw the same result |
| `alpha.evolution.evidence` | the promotion bar | what AVO already measured |
| `alpha.rsi.promotion` | promotion routing | either |
| `alpha.config.self_tuning` | typed config change-sets | any of the above |
| `alpha.evolution.engine` | bounded surface evolution | any of the above |
| `alpha.intelligence` (new) | replay, regression, plasticity, fabric | any of the above |

A candidate can pass `alpha.intelligence.evaluate_gate` and be unknown to
`alpha.avo`'s invariant oracle. Nothing reconciles them, so **the union of
evidence is never assembled** — only the last gate consulted.

### GAP-6 — The research's hardest lesson is absent: open-ended research

The NeurIPS study's finding was that agents do the *engineering* fine and fail
at *the research* — judgment, taste, knowing which question is worth asking.

Alpha has strong `critic`, `council`, `deliberation`, `epistemics`. What it
lacks is any mechanism that decides **which problem is worth attacking**,
rather than ranking problems it was handed. `alpha.rsi.opportunity.mine`
prioritises *given* signals; it does not *choose* what to investigate.

This is the hardest gap to close and the most valuable long-term. It is listed
last deliberately: it needs the measurement infrastructure from GAP-1 through
GAP-4 to be trustworthy first.

### GAP-7 — No intelligence dashboard

`/api/intelligence/*` answers per-subsystem. Nothing answers
**"is the loop working?"** — the one question an operator actually has. §1.3's
saturation finding makes this the question that matters.

---

## 4. Implementation plan

Phases ordered by **dependency**, then by value. Each is independently
mergeable and independently revertible.

---

### Phase A — Pathway evidence (GAP-1)

**The idea.** When a candidate is promoted, record which mechanism it was
*supposed* to change, then run a cheap check that the change actually took.
A routing change must alter routing decisions; a prompt change must alter
prompts; a memory-policy change must alter what gets recalled.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/pathway.py` | `PathwayHypothesis`, `PathwayEvidence`, `PathwayVerdict` |
| `intelligence/pathway_probes.py` | one probe per mechanism type |
| `tests/test_intelligence_pathway.py` | ~30 tests |

**Interface**

```python
class PathwayProbe(Protocol):
    mechanism: str
    def assert_engaged(self, before: Any, after: Any) -> PathwayEvidence:
        """Did the mechanism this candidate claims to change actually change?"""
```

**Honesty rules (the whole point)**

- A probe that cannot run returns `engaged=None` with a reason — never `True`.
- `PathwayVerdict` is one of `ENGAGED`, `NOT_ENGAGED`, `INCONCLUSIVE`.
- **`NOT_ENGAGED` + improved score is the alarming case** and must be logged to
  the journal as its own event kind. It means "this number went up and I do not
  know why", which is the exact condition that lets a broken loop look healthy.
- `INCONCLUSIVE` never promotes and never silently passes.

**Why this is first.** It is cheap, it is measurable, and it converts every later
phase's claim from "the number moved" to "the number moved *for the stated
reason*".

**Exit criteria:** a promoted candidate with `NOT_ENGAGED` appears in
`GET /api/intelligence/journal`; tests cover all three verdicts and every
inconclusive path.

---

### Phase B — Evaluator stability (GAP-4)

**The idea.** Characterise Alpha's own verifier before trusting it. Measure the
same candidate twice, cheaply, and report the delta distribution. Replace the
constant noise floors with *measured* ones.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/evaluator_stability.py` | `StabilityProbe`, `StabilityReport`, `measured_noise_floor()` |
| `tests/test_intelligence_evaluator_stability.py` | ~25 tests |

**Interface**

```python
def measure_noise_floor(
    probe: Callable[[], EvaluationRun],
    repeats: int = 3,
    *,
    min_repeats: int = 2,
) -> NoiseFloor:   # .floor, .samples, .observed, .reason
```

`observed=False` with a real reason when repeats < `min_repeats` — a floor
estimated from one sample is not a floor.

**Integration:** `alpha.intelligence.config.RegressionConfig` gains
`noise_floor_source: measured | declared | max_of_both` (default
`max_of_both`, so a measured floor can only ever make the gate *stricter*).

**Exit criteria:** `GET /api/intelligence/regressions` reports whether each
metric's floor is measured or declared. A candidate improving by less than the
measured floor is rejected with that floor named.

---

### Phase C — Behavioural diversity (GAP-2)

**The idea.** Measure whether the behaviour distribution narrowed. Track the
*space of actions taken*, not just the score.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/diversity.py` | `BehaviorTrace`, `DiversityReport`, `collapse_detector` |
| `tests/test_intelligence_diversity.py` | ~30 tests |

**Interface**

```python
@dataclass(frozen=True)
class DiversityReport:
    action_space: int              # distinct (tool, normalized-arg-shape) tuples
    coverage_delta: float          # vs the pre-change baseline
    policy_entropy: float | None   # None when too few samples, not 0.0
    collapsed: bool
    reasons: list[str]
```

**Detection.** `collapse_detected` when coverage drops below an absolute floor
**and** the drop exceeds the measured evaluator noise floor from Phase B. Both
conditions, deliberately — either alone produces false alarms constantly.

**Integration.** `evaluate_gate` gains a `diversity` gate that **overrides a
score improvement**. This is the one place in the package where something
loses despite scoring better, and it is deliberate: a narrowed agent that scores
the same is worse than one that scores the same and can still do something else.

**Exit criteria:** a candidate that improves score while losing >X% action-space
coverage is rejected, and the test proves the rejection cites diversity rather
than score.

---

### Phase D — Equal-budget protocol (GAP-3)

**The idea.** Make cross-generation claims falsifiable. Compare candidates at
matched attempt budgets, not matched wall-clock.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/budget_protocol.py` | `BudgetUnit`, `MatchedBudgetRun`, `compare_matched()` |
| `tests/test_intelligence_budget_protocol.py` | ~25 tests |

**Interface**

```python
@dataclass(frozen=True)
class BudgetUnit:
    """The unit two candidates must be compared in.

    `attempts` (not seconds, not tokens): the research is explicit that
    improvement *under comparable budgets* is the standard, and attempt count is
    the only unit both a human and a loop can count honestly.
    """

    attempts: int
    tools: int
    tokens: int | None = None   # None when not instrumented; never 0.0
```

`compare_matched(generation_a, generation_b)` refuses to produce a verdict when
budgets differ, and says by how much. **Refusing is the feature** — it is what
stops an unfalsifiable "later generations are better" claim from being made.

**Exit criteria:** an unmatched comparison raises rather than reporting a delta.

---

### Phase E — Cross-subsystem evidence ledger (GAP-5)

**The idea.** One place that knows what every subsystem measured about a
candidate. This is the piece that makes the six subsystems a system rather than
six tools.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/evidence_ledger.py` | `SubsystemVerdict`, `EvidenceLedger`, `ConvergedVerdict` |
| `tests/test_intelligence_evidence_ledger.py` | ~35 tests |

**Interface**

```python
class Subsystem(StrEnum):
    AVO = "avo"
    EVOLUTION_EVIDENCE = "evolution_evidence"
    RSI_PROMOTION = "rsi_promotion"
    SELF_TUNING = "self_tuning"
    INTELLIGENCE = "intelligence"

def converge(ledger, candidate_id) -> ConvergedVerdict:
    """One verdict across every subsystem that has an opinion.

    Disagreement is the interesting output. `PARTIAL` with
    `unreconciled=[...]` is correct and expected when a subsystem has not
    evaluated the candidate — it must never be rounded to APPROVED.
    """
```

**Consistency rules**

- A subsystem that has *not evaluated* a candidate is `NOT_EVALUATED`, never
  `PASS`.
- `REJECT` from any single subsystem is final. There is no averaging across
  subsystems: a candidate that breaks an invariant oracle has not passed, and a
  5–1 vote does not change that.
- The ledger records which subsystems were consulted, so "promoted" always
  answers "passed what, exactly?"

**Exit criteria:** a promotion is impossible unless every *enabled* subsystem
either passed or is explicitly listed as not consulted, with that list recorded
in the promotion event.

---

### Phase F — The loop-health dashboard (GAP-7)

**The idea.** Answer "is the loop working?" — the question §1.3 says matters
most, and the one no existing endpoint answers.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/loop_health.py` | `LoopHealthReport`, `SaturationDetector` |
| `tests/test_intelligence_loop_health.py` | ~30 tests |

**Endpoint** `GET /api/intelligence/health`

```json
{
  "regime": "improving | stable | saturating | regressing | insufficient_data",
  "consecutive_non_improving": 4,
  "gain_per_attempt_trend": 0.004,
  "pathway_alignment": 0.83,
  "action_space_coverage_delta": -0.11,
  "measured_noise_floor": 0.019,
  "subsystems_agreeing": 5,
  "subsystems_unreconciled": ["avo"],
  "bottleneck": "evaluator noise exceeds candidate deltas",
  "recommended_action": "raise regression.repeats before trusting any promotion"
}
```

`insufficient_data` is a first-class regime, not an empty response.

**Integration.** Wires Phases A–E together: each contributes one or more fields.
Nothing here computes anything new; it composes what the earlier phases already
measure. That is deliberate — a dashboard with its own logic would be a second
source of truth.

**Exit criteria:** `SaturationDetector` fires on a synthetic plateau of N
non-improving attempts and reports `recommended_action` naming the *actual*
limiting factor (noise floor vs pathway misalignment vs coverage loss), not a
generic string.

---

### Phase G — Research-direction selection (GAP-6)

**The hardest, and last.** Not because it is low-value but because it is
untrustworthy without A–F.

**The idea.** Rank not *known* problems but *candidate investigations* — where
the expected information gain justifies the spend.

**New files**

| File | Purpose |
| --- | --- |
| `intelligence/investigation.py` | `InvestigationProposal`, `InformationGain`, `select_investigation()` |
| `tests/test_intelligence_investigation.py` | ~35 tests |

**Interface**

```python
@dataclass(frozen=True)
class InvestigationProposal:
    question: str
    resolves_gap: str                  # GAP-1..GAP-7
    expected_information_gain: float
    cost_estimate: BudgetUnit
    falsifiable_by: str                # the measurement that would refute it
```

Three rules make this honest rather than a novelty generator:

1. **`falsifiable_by` is required** and must name a *measurement*, not an
   opinion. A proposal with no refutation condition is refused.
2. **Priority is expected information gain ÷ cost**, with cost taken from Phase
   D's `BudgetUnit` so it is comparable across proposals.
3. **The selection is itself gated.** Choosing what to investigate is exactly
   the judgment the NeurIPS study found agents lack, so the chosen
   investigation must pass the Phase E convergence check before it runs.

Explicitly **out of scope:** generating novel research directions from model
weights. Alpha should rank investigations it can *falsify*, not invent ones it
cannot.

---

## 5. Sequencing

```
Phase A  pathway evidence ......... no dependencies      ← highest value
Phase B  evaluator stability ..... needs A (shared journal)
Phase C  behavioural diversity .... needs B (uses the noise floor)
Phase D  equal-budget protocol ... independent
Phase E  evidence ledger ......... needs A–D (converges their verdicts)
Phase F  loop-health dashboard ... needs A–E (composes them)
Phase G  investigation selection . needs E (its proposals pass the gate)
```

A–D are independent of each other and can land in any order or in parallel.
E–G are strictly sequential.

**Rough sizing:** A ~400 lines + 30 tests, B ~350 + 25, C ~500 + 30,
D ~300 + 25, E ~450 + 35, F ~400 + 30, G ~600 + 35. Total ≈ 3,000 lines of
implementation and 210 tests — roughly 60% of the size of the layer just landed,
which is a reasonable increment rather than a rewrite.

---

## 6. What this plan deliberately does not do

| Not doing | Why |
| --- | --- |
| Training model weights | §1.3: gains come from the scaffold, and Alpha's constraint is retrieval/routing/evidence, not weights |
| Reproducing STOP / Gödel Agent / DGM | Alpha's `alpha.evolution.engine` + `alpha.avo.commit_gate` already provide code-level self-modification behind a stronger gate (server-owned scorer) |
| An autonomous research loop | GAP-6 is real, but Phase G's `falsifiable_by` rule plus the Phase E gate are what keep it from being a novelty generator. Shipping it before A–F would make it untrustworthy |
| Anything that removes a gate to "improve throughput" | The whole audit history of this repo is gates being added, never removed |
| A new evolution substrate | Six already. A seventh is the failure mode this plan exists to prevent |

---

## 7. The thesis

The research is unusually clear about one thing, and it should shape every
decision above:

> **Nobody has demonstrated reliable recursive self-improvement.** Loops
> saturate, internal verification is fragile, the "capability frontier expansion"
> claim is widely called marketing, and the strongest empirical result is that
> agents can do the engineering of research but not the research.

Alpha already has what most systems do not: a **server-owned scorer** that
candidates cannot touch, an **integrity check that detects tampering with the
evaluator itself**, a **real held-out suite that refuses unmeasured evidence**,
and a **commit gate**. That is a better safety position than the literature's
average self-improving agent.

The gap is therefore **not more self-modification.** It is better
*measurement of whether the self-modification did what it claimed* — which is
precisely Phases A–E. Alpha's next meaningful gain is more likely to come from
knowing *which* of its six subsystems is lying to it than from a seventh
subsystem that also cannot tell.