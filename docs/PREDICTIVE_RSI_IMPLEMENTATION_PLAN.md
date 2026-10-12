# Alpha Predictive RSI — Implementation Plan and Baseline Audit

**Status:** implementation blueprint verified against this worktree's commit.
**Scope:** the smallest *useful* slice of the wider Predictive RSI upgrade
(`ALPHA_PREDICTIVE_RSI_UPGRADE_PLAN.md`, an untracked local draft) — the one
piece Alpha does not already have — plus the audit that upgrade's Phase 0
demands before any code is written. This document is self-contained: it cites
only paths and symbols that exist in this repository.

**Core rule.** Alpha already has an RSI engine, an evolution plane, a held-out
regression suite, a shadow harness, a credit-assignment ledger, an evidence gate
and a promotion composer. This plan adds **no second** RSI engine, memory system,
promotion system, replay store or configuration authority. It adds the one
responsibility those components provably lack: **estimating the likely outcome of
a candidate *before* choosing it**, with explicit uncertainty and the ability to
abstain.

---

## 1. The gap, stated precisely

Every selection mechanism in `alpha/rsi/` today is **post-hoc**. Read in order:

| Component | File | What it decides | When |
|---|---|---|---|
| Acceptance rules S2–S5 | `rsi/rrsi/acceptance.py` | admissibility of a candidate | **after** both scores are measured |
| Round selection (Alg. 2) | `rsi/rrsi/selection.py` | `argmax Ŝ'` over admitted | **after** measurement |
| Promotion conjunct | `rsi/rrsi/selection.py::election_gate_for` | block-only refusal | **after** measurement |
| Holdout gate | `rsi/holdout.py` | regression verdict | **after** the suite ran |
| Evidence composer | `rsi/promotion.py` | `promoted` | last |
| Shadow harness | `rsi/shadow.py` | `improved`/`regressed`/`inconclusive` | after both sides ran |
| Cycle preview | `rsi/engine.py::run_rsi_cycle` | **nothing** — returns `evidence_kind="simulated"` | never measured |

`Measurement.measured` (`rsi/rrsi/acceptance.py:100`) is `True` only when
`evidence_kind == "measured"`, and `run_rsi_cycle` reports
`baseline_score=None, candidate_score=None` with
`not_measured_reason` (`rsi/models.py:49-68`). That is the **right** behaviour and
this plan preserves it — `tests/test_rsi_cycle.py` pins it.

The consequence is the gap:

> Every quantity that could guide a choice is unavailable at the moment the
> choice is made. The search therefore explores candidates in whatever order the
> proposer emitted them, and learns only after execution has already committed
> the budget.

`alpha.rsi.opportunity.mine` chooses *which bottleneck* to attack, and RRSI's
stall indicator `σ_t` reacts to a flat score series — but neither estimates what
a **specific candidate** will do. **There is no predictor anywhere in the tree.**

That is exactly what the upgrade plan calls *Predict before choosing*
(§2.1, Workstream C). It is the one workstream whose absence blocks the rest:
ranking needs scores, calibration needs forecasts, and promotion needs a
comparison.

---

## 2. Phase 0 audit — what is already wired

Read against this worktree's code, not against the upgrade plan's assumptions.

| # | Area | Verdict | Evidence |
|---|---|---|---|
| 1 | RSI cycle owner | `PRESENT_AND_WIRED` | `rsi/engine.py::RSIEngine.run_rsi_cycle`; catalogued as `rsi_engine` in `capabilities/catalog.py:201` |
| 2 | Cycle measurement | `ABSENT_BY_DESIGN` | preview returns `evidence_kind="simulated"`; a real verdict routes through `rsi/holdout.py` + `rsi/promotion.py` |
| 3 | Held-out regression suite | `PRESENT_AND_WIRED` | `rsi/holdout.py` — 5 hidden cases, `holdout_gate` passes only on `evidence_kind="measured"` |
| 4 | Admission rules | `PRESENT_AND_WIRED` | `rsi/rrsi/acceptance.py::admit` — non-compensatory S2/S3a/S3b/S5 |
| 5 | Leakage screen | `PRESENT_AND_WIRED` | `rsi/rrsi/critic.py::screen_candidate` (S1), runs before evaluation |
| 6 | Credit ledger | `PRESENT_AND_WIRED` | `rsi/rrsi/history.py::RrsiHistory` — append-only JSONL, `ΔS`/`ΔC`/`accepted` |
| 7 | Durable cross-round state | `PRESENT_AND_WIRED` | `rsi/rrsi/store.py::RrsiRoundStore` — `S*`, incumbent, round scores |
| 8 | Kill switch / freeze | `PRESENT_AND_WIRED` | `rsi/switchboard.py::rsi_frozen` — 4 sources, fail-closed, never raises |
| 9 | Evidence + promotion | `PRESENT_AND_WIRED` | `rsi/evidence_bundle.py`, `rsi/review.py`, `rsi/promotion.py::decide` (7 gates) |
| 10 | Shadow comparison | `PRESENT_AND_WIRED` | `rsi/shadow.py::run_shadow` — 3 states, offline-fixture disclosure pinned |
| 11 | Strategy statistics | `PRESENT_AND_WIRED` | `rsi/strategy_memory.py` — recorded-count rates, `None` at 0 attempts |
| 12 | Durable honesty doctrine | `PRESENT_AND_WIRED` | unreadable ⇒ `None`/`count: null`, never `0`; see `rsi/rrsi/AGENTS.md` |
| 13 | **Outcome predictor** | **`ABSENT`** | no module forecasts a candidate's outcome; nothing consumes one |
| 14 | **Calibration metrics** | **`ABSENT`** | no Brier score, coverage, or abstention rate anywhere in `alpha/rsi/` |
| 15 | **Uncertainty quantification** | **`ABSENT`** | `RrsiParams` has no confidence interval; `ΔS` is a point estimate |
| 16 | Replay reservoir | `PRESENT_NOT_WIRED` | `alpha.rsi.opportunity` ranks bottlenecks; no priority replay of *transitions* |
| 17 | Explicit abstention | `ABSENT` | unmeasurable states report `None` + reason (correct) but have no named **abstain** state a selector can act on |
| 18 | Mode gating for predictions | `ABSENT` | no `observe_only`/`shadow`/`rank_only` posture exists |

**Decision.** Reuse 1–12 and 16 verbatim. Build **only** 13–15, 17, 18 as one new
module set, and make it strictly **advisory**: it may *rank and disclose*, and it
may **never** admit, gate, promote or roll back.

---

## 3. What gets built

A new package `alpha/rsi/predictive/` — one predict/calibrate surface, six modules.

```
alpha/rsi/predictive/
├── contracts.py    OutcomePrediction, ForecastStatus, ObservedOutcome, OutcomeForecast
├── predictor.py    OutcomePredictor protocol; DeterministicPredictor (Tier 0)
│                   EmpiricalPredictor (Tier 1, shrinkage); resolve_predictor
├── calibration.py  brier_score, calibration_error, coverage, score_forecast
├── store.py        ForecastLedger — append-only JSONL of forecast→outcome pairs
├── service.py      PredictiveRSIService — the one entry point; mode-gated
└── __init__.py     package surface
```

### 3.1 Contracts — a forecast is not a measurement

`OutcomePrediction` carries `schema_version`, `prediction_id`, `candidate_id`,
`predictor_id`, `predictor_version`, `predicted_success_probability`,
`predicted_cost`, `uncertainty`, `support` (sample count), `assumptions`,
`status`, `abstain_reason`, `created_at`.

The load-bearing separation:

- **`evidence_kind` is never `"measured"`.** A forecast is stamped
  `evidence_kind="forecast"`. `holdout_gate` and `election_gate_for` only accept
  `"measured"`, so a forecast is **structurally incapable** of promoting — not
  blocked by a convention, but by the same `evidence_kind` field the existing
  gates already test.
- **`predicted_*` and `measured_*` never share a field name.** A consumer cannot
  confuse a forecast for an observation by reading a key.
- **`None` means unknown, never `0.0`.** Every numeric is `float | None`.

`ForecastStatus` is a four-state enumeration — `predicted`, `abstained`,
`invalid`, `expired`. `abstained` carries a non-empty `abstain_reason`: a
refusal that looks like a number is the failure this prevents.

### 3.2 Predictors

**Tier 0 — `DeterministicPredictor`.** Stdlib only. No model, no network, no API
key. Estimates from rules over declared features (step count, component risk
tier, edit budget saturation, structural novelty) and an explicit prior. This is
the reproducible baseline every later tier must beat.

**Tier 1 — `EmpiricalPredictor`.** Success rate per `(component, regime)`
stratum from the recorded ledger, with:

- a minimum-sample floor (`min_samples_per_stratum`) — below it, **abstain**;
- **shrinkage toward the global prior**, `p = (successes + α·prior) / (n + α)`, so
  a 1-of-1 stratum cannot report `1.0`;
- **Wilson score interval** for the reported uncertainty, because a bare point
  estimate on a small stratum reads as certainty it does not have.

Both implement one protocol so a later learned tier is a drop-in:

```python
class OutcomePredictor(Protocol):
    @property
    def identity(self) -> PredictorIdentity: ...
    def predict(self, candidate: CandidateFeatures) -> OutcomePrediction: ...
    def observe(self, outcome: ObservedOutcome) -> None: ...
    def health(self) -> PredictorHealth: ...
```

`observe()` is the **only** way empirical state advances, and it accepts an
`ObservedOutcome` that must carry `evidence_kind="measured"`. A simulated
outcome cannot train the predictor — the same rule the gates use.

### 3.3 Calibration — measure the predictor, not the system

`score_forecast(prediction, outcome) -> ForecastScore` pairs a forecast with a
later measurement and reports:

- **Brier score** `(p − y)²` — lower is better;
- **calibration error** — |predicted − observed| per stratum;
- **coverage** — how often the predictor declined to answer;
- **high-confidence failure count** — forecasts above 0.8 that then failed,
  the signal that matters most for an autonomous system.

`CalibrationReport` aggregates over a ledger and returns `None` (not `0.0`) when
no scored pair exists. **An uncalibrated predictor is reported as uncalibrated,
not as perfectly calibrated.**

### 3.4 Modes — the selector can only ever be told, never obeyed

`PredictiveMode`: `disabled` → `observe_only` → `shadow` → `rank_only` →
`canary` → `active`, defaulting to **`disabled`**.

`PredictiveRSIService.rank(candidates)` returns `RankingResult`:

```python
@dataclass(frozen=True)
class RankingResult:
    order: tuple[str, ...]             # candidate ids, best-first
    scores: dict[str, float]
    abstained: dict[str, str]          # candidate -> reason
    mode: PredictiveMode
    influenced_selection: bool         # False in every shipped mode
```

`influenced_selection` is `False` for `disabled`/`observe_only`/`shadow`, and the
constructor **refuses** to construct `True` in those modes. `rank()` therefore
cannot change a selection while the system is gathering evidence — the property
that makes the whole tier safe to ship first.

`assert_mode_transition(current, requested)` escalates **one step at a time** and
refuses `active` without an explicit promotion token. Config cannot jump
`disabled → active`.

### 3.5 Ledger

`runtime_home()/rsi/predictions.jsonl`, append-only, mirroring
`RrsiHistory`'s contract: absent ≠ corrupt; a malformed line is skipped **and
counted**; `entries is None` while unreadable; `append()` returns whether the
write landed rather than raising.

### 3.6 Wiring — why the cycle observes even though the default is `disabled`

`RSIEngine._predictive_forecast` asks the service for a forecast on every cycle
and appends the result to the evidence **before** the two closing lines, exactly
where `_rrsi_proposal_plan` inserts the RRSI plan. Four properties:

* **It forecasts; it never measures.** No outcome is recorded, because a preview
  measures nothing. Recording one would put a fabricated label into the very
  ledger that calibrates the predictor.
* **It is a forecast, not a result.** Every line says so, and the numbers are
  `predicted_*`.
* **It cannot change a verdict.** `promoted` stays `False`, the stage stays
  `preview`, `evidence_kind` stays `"simulated"`, and `evidence[-1]` keeps the
  meaning `_last_summary` has always quoted.
* **A gap is disclosed.** An unreadable ledger or a raising predictor produces
  its own line rather than an empty list.

`observe_only` is used here although the service's own constructor default is
`disabled`, because `observe_only` is **provably inert** — `influenced_selection`
is `False` in every shipped mode, pinned against every member of `MODE_ORDER`.
A predictor no live cycle ever calls can never be calibrated, so a cycle that
changes nothing and spends no token is the only way the evidence arrives. The
`disabled` default is unchanged for any other caller.

---

## 4. Invariants — each one is a test

1. **A forecast can never promote.** `evidence_kind="forecast"` is rejected by
   `holdout_gate` and refused by `election_gate_for`.
2. **`None` is never `0.0`.** Unknown success probability, uncertainty and
   calibration are `None`.
3. **Abstention is observable and reasoned.** `abstained` status ⇒ non-empty
   reason.
4. **A low-sample stratum abstains** rather than reporting a confident number.
5. **Shrinkage moves a small stratum toward the prior**, never to `0.0`/`1.0`.
6. **An unreadable ledger reports unknown**, and the service abstains rather than
   predicting from nothing.
7. **A simulated outcome cannot train the predictor.**
8. **`rank()` changes no selection** in every shipped mode.
9. **Mode escalation is one step at a time**; `active` needs a token.
10. **Bounds are refused, never clamped** — inherited house rule.
11. **An uncalibrated predictor reports `None`**, not a flattering number.

---

## 5. Explicit non-goals

Not in this slice, and not to be read as pending:

- **No execution behaviour changes.** No tool call, run, task or config value is
  altered by anything here.
- **No new tool, route, loop, or capability entry.** The model-facing surface is
  unchanged, so `contracts/feature_manifest.json` and the counts in `README.md`,
  `llms.txt`, `llms-full.txt`, `docs/FAQ.md`, `docs/COMPARISON.md` are untouched.
- **No new promotion authority.** `GATE_ORDER` in `rsi/promotion.py` is unchanged.
- **No second memory or replay store** — the ledger is a forecast/outcome log,
  read only by the predictor and the calibration report.
- **No learned model.** Tier 2+ requires measured evidence that Tier 0/1 is the
  binding constraint.
- **No claim of improved task outcomes.** Building a calibrated predictor is not
  evidence that ranking by it helps. That needs the frozen held-out comparison in
  the upgrade plan's Phase 4, which this slice does not claim.

---

## 6. Files

| Path | Change | Why required |
|---|---|---|
| `alpha/rsi/predictive/contracts.py` | new | forecast/outcome separation; the four-state status |
| `alpha/rsi/predictive/predictor.py` | new | Tier 0/1 behind one protocol; abstention |
| `alpha/rsi/predictive/calibration.py` | new | Brier/calibration/coverage; `None` when unscored |
| `alpha/rsi/predictive/store.py` | new | durable forecast→outcome pairs, honest on corruption |
| `alpha/rsi/predictive/service.py` | new | mode gate; the single entry point |
| `alpha/rsi/predictive/__init__.py` | new | package surface |
| `alpha/rsi/__init__.py` | edit | re-export, as `rrsi` already is |
| `alpha/rsi/predictive/AGENTS.md` | new | normative contract for the subpackage |
| `backend/tests/test_rsi_predictive.py` | new | the eleven invariants |
| `scripts/generate_docs_index.py` | edit | `FILE_OVERRIDES` entry for this document |
| `docs/INDEX.md` | regenerated | 148 documents, +1 |
| `ENGINE_INVENTORY.md` | regenerated | 126 packages (unchanged); `alpha/rsi` 34 → 40 files |

**Deliberately not created:** no tool, route, supervisor loop, `CapabilitySpec`,
config key or manifest entry. The feature manifest and every hardcoded capability
count are therefore untouched — a reviewer can confirm this by running
`scripts/generate_feature_manifest.py` and observing no diff.

## 7. Verification

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_rsi_predictive.py -v
PYTHONPATH=. uv run pytest tests/test_rsi_cycle.py tests/test_rrsi_proposal_side.py \
  tests/test_rrsi_selection_side.py tests/test_rrsi_store.py tests/test_rsi_promotion.py \
  tests/test_rsi_holdout.py -v
PYTHONPATH=. uv run pytest tests/test_harness_boundary.py tests/test_no_orphan_modules.py \
  tests/test_feature_manifest_wiring.py -v
make lint && make format
```

### Bug this slice caught in its own code

Worth recording, because it is the failure mode the package is built to prevent.
`ForecastLedger.append` originally called `_ensure_loaded()` (which reads the
line just written back off disk) *and then* adopted the record into memory —
counting every appended record **twice** for the life of the process, inflating
the predictor's sample support and the calibration denominator. The test
`test_an_appended_record_is_counted_exactly_once` pins it.

## 8. What this does not claim

- **Not that Alpha predicts better now.** It can *forecast* and it can be
  *measured*, in `disabled` (default) and `observe_only`. Whether ranking by the
  forecast beats the incumbent is a question for a frozen held-out comparison on
  real tasks, which this slice does not run and does not answer.
- **Not that a learned world model is warranted.** Tier 2+ needs measured
  evidence that Tier 0/1 is the binding constraint.
- **Not cross-process.** The ledger is single-process and restart-recoverable for
  one Gateway — never exactly-once across workers.