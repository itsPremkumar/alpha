# Predictive RSI (`alpha/rsi/predictive/`)

Scope: this file covers `packages/harness/alpha/rsi/predictive/`. It is
stricter than the two guides above it and wins where they are looser.

This subpackage is a **subsystem of the existing RSI engine**, not a fork of it.
`alpha.rsi` owns runs, stages and promotion. This one owns the single thing
those stages lacked: **an estimate of what a candidate will do, made before it
is chosen**. It adds no notion of a run, no second promotion authority and no
second loop.

Design record and the audit that justified it:
[`docs/PREDICTIVE_RSI_IMPLEMENTATION_PLAN.md`](../../../../../../docs/PREDICTIVE_RSI_IMPLEMENTATION_PLAN.md).

## The gap this fills

Every selection mechanism in `alpha/rsi` is **post-hoc**: S2–S5 admission
(`rrsi/acceptance.py`), Algorithm 2's `argmax` (`rrsi/selection.py`), the
holdout gate (`holdout.py`) and the evidence composer (`promotion.py`) all run
*after* both scores exist. The search therefore explores candidates in emission
order and learns only once the budget is spent.

## One source per fact

| Module | Owns |
| --- | --- |
| `contracts.py` | `OutcomePrediction`, `ObservedOutcome`, `CandidateFeatures`, `ForecastStatus`, `PredictorIdentity`, `PredictorHealth` |
| `predictor.py` | the `OutcomePredictor` protocol, `DeterministicPredictor` (Tier 0), `EmpiricalPredictor` (Tier 1), `PredictorParams`, `wilson_interval`, `resolve_predictor` |
| `calibration.py` | `score_forecast`, `brier_score`, `calibration_error`, `coverage`, `CalibrationReport`, `calibration_report` |
| `store.py` | `ForecastLedger` — the append-only forecast/outcome JSONL |
| `service.py` | `PredictiveMode`, `assert_mode_transition`, `RankingResult`, `PredictiveRSIService` |
| `__init__.py` | the package surface and nothing else |

`FORECAST_EVIDENCE_KIND` is declared once in `contracts.py` and pinned on
`OutcomePrediction` with `init=False`, so no second spelling can drift.

## Invariants

**A forecast is not a measurement, and this is enforced structurally.** Every
forecast carries `evidence_kind="forecast"`, a label the RSI gates do not
accept. `holdout_gate` and `election_gate_for` therefore refuse a forecast
because they test the *same* field `Measurement.measured` tests — not because a
convention says forecasts must not be passed to gates. The field is `init=False`
so a caller cannot relabel a forecast as `"measured"`.

**Predicted and measured quantities never share a field name.** `predicted_*`
versus `measured_*`. There is no `score` key a consumer could read without
knowing which side it came from. This is deliberately more verbose than the
surrounding `Ŝ`/`ΔS` notation, which is fine while everything is measured and
becomes dangerous the moment forecasts and measurements share a namespace.

**Unknown is `None`, never `0.0`.** Absent probability, absent outcome,
uncalibrated report, missing sample support, unreadable ledger. Every aggregate
that has no evidence behind it is `None` — `brier=None` means *no evidence*, not
*no error*. A `CalibrationReport` with nothing scored says `UNKNOWN` in its
`detail`; it never reads as a good result.

**Abstention is a first-class, reasoned answer.** `ForecastStatus` has four
states, and the last three are kept distinct because they demand different
fixes: `ABSTAINED` (the predictor chose not to answer), `INVALID` (the request
was malformed), `EXPIRED` (too old). Every non-`PREDICTED` status **must** carry
a non-empty `abstain_reason` — and the dataclass refuses to construct one
without it, which is what makes "the predictor declined" impossible to
confuse with "the predictor ranked it worst".

**A non-predicted forecast cannot carry a number.** Enforced in
`OutcomePrediction.__post_init__`, so a stale probability cannot survive on an
object whose status says it was refused.

**An abstained candidate is reported, not ranked last.** `RankingResult.abstained`
maps candidate id to reason. Silently sorting a refusal to the bottom would
turn "no evidence" into a judgement about the candidate.

**Shrinkage, not a rate.** The empirical tier reports
`(successes + α·prior) / (n + α)`. A 1-of-1 stratum with `α=4` and prior `0.5`
reports `0.6`, never `1.0`. Below `min_samples_per_stratum` it **abstains**
instead, because a single observation is a memory rather than a rate.
Uncertainty is a **Wilson interval** width — the normal approximation is
meaningless at the sample sizes this system actually has.

**Nothing unmeasured trains the predictor.** `EmpiricalPredictor.observe`
requires `ObservedOutcome.measured` (`evidence_kind == "measured"`), mirroring
`Measurement.measured`. Otherwise a preview's placeholder numbers would become
the training set of the thing meant to predict reality. A `simulated` outcome is
still written to the ledger for the audit trail and excluded by
`ForecastLedger.measured_outcomes` from anything that calibrates.

**No shipped mode influences a selection.** `RankingResult.influenced_selection`
is `False` in **every** mode, including `rank_only`/`canary`/`active`, which
exist in the ladder so a later evidence-gated phase has a named destination.
`RankingResult.assert_no_influence()` raises if a result ever claims otherwise.
A test pins this against every member of `MODE_ORDER` rather than trusting the
prose.

**Escalation is one rung at a time.** `assert_mode_transition` refuses a jump
naming both modes, and `active` additionally requires an explicit `token` — it
is not reachable by configuration alone. De-escalating is always allowed:
backing down must never be the operation that needs a permission. Compare
`.rank`, never the enum member: `PredictiveMode` is a `str` enum, so
`mode >= MODE_ORDER[3]` compares strings and inverts every gate.

**A degraded ledger abstains before predicting.** The store is checked *before*
the forecast, not after recording it, so an unreadable ledger produces an
abstention with the real error rather than a forecast nobody could later
calibrate.

**Bounds are refused, never clamped**, and a boolean is never accepted as a
number (`True` is not a probability of 1). Clamping `min_samples_per_stratum=0`
would turn "abstain below a floor" into "never abstain" — the opposite of what
the operator wrote. `resolve_predictor` refuses an unknown backend **by name**
rather than falling back to Tier 0.

## Durable state

`runtime_home()/rsi/predictions.jsonl`, append-only, resolving `ALPHA_HOME` per
call. It follows `rrsi/store.py` and `rrsi/history.py`: an unreadable ledger
reports unknown (`entries is None`) rather than empty; absent is a first run, not
a fault; a malformed line is skipped **and counted**; `append` returns a
`LedgerWrite` saying whether the write landed rather than raising.

Single-process and restart-recoverable for one Gateway — **never**
cross-process exactly-once. Do not describe it as a shared ledger.

## Wiring

Three call sites, all additive and none of them a gate:

1. **`alpha/rsi/engine.py` `_predictive_forecast`** — observes every cycle and
   appends the forecast to the evidence before the two closing lines, so
   `evidence[-1]` keeps the meaning `_last_summary` has always quoted.
2. **`alpha/rsi/promotion.py` `_holdout_gate`** — calls
   `record_holdout_verdict(candidate_id, payload)` **after** the verdict is
   computed, and discards its result. The gate's `(ok, reason)` is fixed above
   the call; the recorder cannot alter it, never raises, and writes nothing for
   any payload not stamped `"measured"`. This is the only place in the tree where
   a real holdout suite has actually executed, so it is the only honest source of
   an outcome — which is what closes the calibration loop.
3. **`app/gateway/routers/predictive_rsi.py`** — the read-only operator surface:
   `GET /api/rsi/predictive/status` and `GET /api/rsi/predictive/calibration`.
   Authenticated, `GET` only, filesystem work off the event loop, and an
   unreadable ledger is `503` carrying its own reason rather than an empty `200`.

`alpha/rsi/__init__.py` re-exports the public names, exactly as it does for
`rrsi`. That re-export is what satisfies the orphan-module gate.

## Not implemented

Stated here so the module list is not read as a claim:

- **No execution influence, in any mode.** Nothing here changes a selection, a
  run, a tool call, a config value or a route. `rank()` is a pure ordering over
  the forecasts it just produced.
- **No promotion authority.** `GATE_ORDER` in `alpha/rsi/promotion.py` is
  untouched; this package is not a gate.
- **No learned model.** Tier 2+ requires measured evidence that Tier 0/1 is the
  binding constraint. A better Brier score alone is not that evidence.
- **No per-stratum calibration breakdown.** `CalibrationReport` reports whole-set
  aggregates only. A stratum breakdown was deliberately **removed** rather than
  shipped derived from the prose in `assumptions` — re-deriving a fact by parsing
  an English sentence is how a report ends up disagreeing with the ledger.
- **No cross-process coordination.** Two Gateways writing the same ledger are
  two independent predictors.
- **No claim of improved task outcomes.** Building a calibrated predictor is not
  evidence that ranking by it helps. That needs a frozen held-out comparison on
  real tasks, which this package does not provide and does not claim.
- **No caller yet.** Nothing in the RSI cycle consults `PredictiveRSIService`
  today. The package is a library with tests, wired into `alpha.rsi`'s public
  surface but **not** on any live execution path — the same disclosure
  `alpha.arena`'s engine primitives and `alpha.capability_mesh` make.
- **No outcome is recorded by a cycle.** `RSIEngine._predictive_forecast`
  forecasts and records the *forecast*; it never records an *outcome*, because a
  preview measures nothing. Outcomes come from `record_holdout_verdict`, which
  the promotion path's holdout gate calls **only** on a payload already stamped
  `evidence_kind="measured"` by a suite that actually ran.
- **No outcome is recorded by a cycle.** `RSIEngine._predictive_forecast`
  forecasts and records the *forecast*; it never records an *outcome*, because a
  preview measures nothing. Outcomes come from
  `record_holdout_verdict`, which the promotion path's holdout gate calls **only**
  on a payload already stamped `evidence_kind="measured"` by a suite that
  actually ran.

## Wiring

Three call sites, all additive and none of them a gate:

1. **`alpha/rsi/engine.py` `_predictive_forecast`** — observes every cycle and
   appends the forecast to the evidence before the two closing lines, so
   `evidence[-1]` keeps the meaning `_last_summary` has always quoted.
2. **`alpha/rsi/promotion.py` `_holdout_gate`** — calls
   `record_holdout_verdict(candidate_id, payload)` **after** the verdict is
   computed, and discards its result. The gate's `(ok, reason)` is fixed above
   the call; the recorder cannot alter it, never raises, and writes nothing for
   any payload not stamped `"measured"`. This is the only place in the tree where
   a real holdout suite has actually executed, so it is the only honest source of
   an outcome — which is what closes the calibration loop.
3. **`app/gateway/routers/predictive_rsi.py`** — the read-only operator surface:
   `GET /api/rsi/predictive/status` and `GET /api/rsi/predictive/calibration`.
   Authenticated, `GET` only, filesystem work off the event loop, and an
   unreadable ledger is `503` carrying its own reason rather than an empty `200`.

`alpha/rsi/__init__.py` re-exports the public names, exactly as it does for
`rrsi`. That re-export is what satisfies the orphan-module gate.

## Tests

`backend/tests/test_rsi_predictive.py` — the eleven invariants, including the
refusal paths, which are the safety properties here: a forecast refused by the
*real* `holdout_gate`, an unreadable ledger abstaining, a simulated outcome
refused as training data, and `influenced_selection is False` across every mode.