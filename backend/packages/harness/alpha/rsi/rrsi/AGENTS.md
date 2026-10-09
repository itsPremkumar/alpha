# RRSI — Regularized Recursive Self-Improvement (`alpha/rsi/rrsi/`)

Scope: this file covers `packages/harness/alpha/rsi/rrsi/`. It is stricter than
the two guides above it and wins where they are looser.

This subpackage is a **subsystem of the existing RSI engine**, not a fork of it.
`alpha.rsi` owns runs, stages and promotion; `alpha.rsi.rrsi` owns the
*regularizers* the RRSI paper layers over a self-improvement search. It adds no
new notion of a run and no new promotion authority — see
[Wiring points](#wiring-points) for the three places it is called from.

## Provenance and claim honesty

The design comes from *"RRSI: Regularized Recursive Self-Improvement of Agent
Harnesses"* (arXiv:2609.24972), Algorithms 1–2, and the hyperparameters of its
Table 5. Three consequences are binding on every claim made in code, docs or a
reply:

- **Preset provenance is per-field, not per-preset.** `PRESET_SOURCES` names
  which values the paper reports and which are local starting points. Quoting
  "the paper's setting" for a value it never reported is the failure mode this
  exists to prevent.
- **Nothing here is a result.** Every function computes a *constraint*
  (P1–P3) or a *verdict* (S1–S5, Algorithm 2). None of them scores, ranks or
  approves anything on its own, and none of them runs an agent.
- **The paper's own caveats travel with it.** It reports no matched-budget
  test-time-scaling baseline; its regularizer hyperparameters are tuned on the
  same evolve set they police; its ablation is group-level only. Do not summarise
  those results as if any of the three were settled.

Table 5, as pinned verbatim by `backend/tests/test_rrsi_config.py`:

| preset | T | k | δ | b_min | b_max | w | m_draft | n_prune | β₀ | β₁ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `coding` | 20 | 2 | 0.017 | 1 | 4 | 3 | 1 | 4 | 0.10 | 44.5 |
| `workspace` | 20 | 2 | 0.004 | 1 | 3 | 3 | 1 | 4 | 0.10 | 35.4 |
| `engineering` | 40 | 4 | 0.020 | 1 | 4 | 3 | 1 | 5 | 0.15 | 24.4 |

`RrsiParams()` defaults to the `workspace` column. The coding column is the one
with `w_s = 0`, so an inside-the-noise-band score gain earns no admissibility
credit there — only cheaper inference and structural novelty do.

## One source per fact

| Module | Owns |
| --- | --- |
| `config.py` | `RrsiParams` (every hyperparameter, with bounds), `DOMAIN_PRESETS`, `PRESET_SOURCES`, `preset`, `preset_names` |
| `components.py` | `K` (the nine editable components), `K_struct`, `SURFACE_COMPONENTS`, `component_for`, `components_for`, `novelty`, `novelty_breakdown` |
| `budget.py` | P1 — the cosine edit budget `b_t` and its prefix cut (`edit_budget`, `apply_edit_budget`, `BudgetOutcome`) |
| `history.py` | P2 — the append-only credit-assignment ledger `L_t`, `GainWindow`, `history_path` |
| `exploration.py` | P3 — the stall indicator `σ_t` and the reserved-slot plan (`detect_stall`, `plan_exploration`, `StallSignal`, `ExplorationPlan`) |
| `pruning.py` | S4 — the L1 pruning targets `B_t` under `max ∅ = −∞` (`pruning_targets`, `PruningReport`) |
| `critic.py` | S1 — the benchmark-leakage screen and its bounded repairs (`screen_candidate`, `ScreenVerdict`, `BENCHMARK_MARKERS`) |
| `acceptance.py` | `Measurement`, `Check`, `Admissibility`, and the non-compensatory rules S2 / S3a / S3b / S5 / attribution (`admit`) |
| `selection.py` | Algorithm 2 over one round (`select_round`, `RoundDecision`, `ScoredCandidate`) and the block-only promotion conjunct (`election_gate_for`, `RrsiGateOutcome`) |
| `store.py` | the durable round store: `RoundState`, `DurableWrite`, `RrsiRoundStore`, `round_store_path` |
| `proposal.py` | Algorithm 1's composition and its single rendered instruction (`build_proposal_plan`, `ProposalPlan`) |
| `__init__.py` | the package surface (`__all__`, 54 names) and nothing else |

Two facts are declared once and read from that one place everywhere:
`K`/`K_struct` (`components.py`) and the credit ledger's membership functions
(`history.py`). A second definition would make two call sites disagree about
which components have ever won.

## Durable state

Both files live under `runtime_home()/rsi/` and resolve `ALPHA_HOME` per call,
so a test-set home is honoured without a cache:

- `rrsi_rounds.json` — `RrsiRoundStore`. `RoundState.round` is the **1-based index
  of the last completed round** (`0` = none); `next_round` is `round + 1`; an
  unreadable store reports `next_round == 1`, never `0`. `round_scores` holds
  **only rounds that produced a measured score**.
- `rrsi_history.jsonl` — `RrsiHistory`. One append-only `HistoryEntry` per
  attempt; `REQUIRED_FIELDS` is checked on read and a malformed line is skipped
  with a count rather than back-filled.

Both are local to one Gateway process. They are atomic and restart-recoverable
for that process, **never cross-process exactly-once**; do not describe them as
a shared lease or consensus record.

## Invariants

**Unknown is never empty.** An unreadable store, ledger or manifest reports
`count: null`, `entries: None`, `best_score: None` — never a count of `0` and
never `0.0`. `S*` of `0.0` would make the floor `−δ` and admit every candidate
thereafter. `RrsiRoundStore.status()`, `RrsiHistory.status()` and
`RrsiHistory.to_disclosure()` are the three honest health blocks.

**Bounds are refused, never clamped.** `RrsiParams.validate()`, `edit_budget`,
`component_for`, `validate_component`, `build_proposal_plan` and
`pruning_targets` all raise with the offending field named and the bound stated.
A typo must be visible as a refusal; absorbing it into a different regularizer
would keep the typo's field name in the report as if it had been honoured.
A boolean is never accepted as a number or an integer (`True` is not `rounds=1`).

**`ok is None` is a third state.** `Check.applicable`, `Check.ran` and `Check.ok`
distinguish "this rule does not govern this candidate" from "it governs it and we
could not evaluate it". `admit()` is non-compensatory: a rule that could not be
evaluated blocks with the real reason, and a cheaper candidate cannot buy its way
past a floor it failed. Exactly one of S3a/S3b governs any candidate; the other
records the measured `ΔS` that excluded it.

**An unmeasured round cannot erase a measurement.** `RoundState.with_round`
carries the incumbent fields forward when `score is None`: a preview that
measured nothing must not discard the last real `(Ŝ_t, Ĉ_t)` by recording its own
absence. `ΔC` is relative and requires both absolute costs plus a non-zero
incumbent cost — anything else leaves it unknown rather than `0`.

**`max ∅ = −∞`.** A component with **no** measured `ΔS` inside the window is a
pruning target; a never-exercised component is not. Both live in
`PruningReport.targets`, and `PruningReport.unmeasured` keeps the subset whose
membership comes from absence distinguishable from those that measured and
failed — they are not the same evidence and a count that merges them is a
fabricated count.

**The stall boundary is inclusive.** `stalled = delta <= noise_delta`, because
`σ_t = 1[Ŝ_t − Ŝ_(t−w) ≤ δ]`. A stall is only declared when the series supports
one (`observed >= window + 1`); otherwise `delta is None` and the reason states
how many observations were required.

**The proposal side never decides.** `build_proposal_plan` renders constraints
and a rendered `instruction` into one bounded artifact (`MAX_REASONS = 5`,
`MAX_INSTRUCTION_CHARS = 4000`, both announced when truncated). `ProposalPlan.usable`
is `False` the moment any section could not be computed — a plan that silently
dropped its pruning section would be a constraint that looked complete. Its
`to_dict()` carries no score, no verdict and no approval.

**The screen runs before the evaluation it protects.** `screen_candidate` is
deterministic and cheap; a caller-supplied `reviewer` may only *fail* a screen,
never clear a finding, and a reviewer that raises fails the screen with the real
error. A static pass means "the declared rules found nothing" and says how many
ran — not "this candidate is clean".

## Wiring points

1. **`alpha/rsi/engine.py` `_rrsi_proposal_plan`** — a per-cycle disclosure of
   the Algorithm 1 plan. Its lines are inserted **before** the two closing
   evidence lines, so `evidence[-1]` and `_last_summary` keep their established
   meaning, and the round is recorded as `UNMEASURED` when no evolve-set
   measurement was taken.
2. **`alpha/rsi/generator.py` `CandidateFactory.generate(..., plan=)`** — attaches
   one `payload["rrsi"]` block per variant *before* the payload hash is computed
   (so the constraints are part of the hashed identity) and enforces P1 as a hard
   cardinality constraint: an over-budget variant is discarded **whole** with a
   `{"skipped": "over_edit_budget", ...}` entry, never trimmed. `plan=None`
   keeps the payload byte-identical for callers that supply no plan.
   `_rrsi_edit_count` excludes the provenance keys `operator` / `operator_choice`
   from `‖z_t‖₀`.
3. **`alpha/evolution/promotion_route.py` `rrsi_selection_gate`** — a second,
   *block-only* conjunct beside the evidence gate. It is tri-state:

   - `blocked` — the gate ran and refused; `promoted` becomes `False` and the
     gate's own reason is appended with `" + "`.
   - `admitted` — the gate ran and every applicable rule passed; nothing is
     appended.
   - `not_run` — no complete measurement set. It **neither blocks nor passes**,
     and it is never reported as a pass: the non-event is logged with every
     quantity that could not be resolved and where it was looked for.

   `election_gate_for` requires a *complete* measurement set — both scores, both
   costs, `S*`, and an attributed component set — resolved in order
   `signals` → `baseline` → durable state, with the source of each recorded in
   `RrsiGateOutcome.basis`. Partial measurements are never interpolated.

`GATE_ORDER` in `alpha/rsi/promotion.py` is deliberately **unchanged**: RRSI is
not a promotion gate there. It reads as a block-only conjunct above the
evolution engine, which is what the paper's Algorithm 2 is.

## Not implemented

Stated here so nobody reads the module list as a claim:

- No evaluate-set runner. `election_gate_for` consumes measurements supplied by
  the caller or read from the round store / evidence bundle — nothing here runs an
  agent or collects a score.
- No domain guard (S5) beyond `domain_guard_default`, which states plainly that
  no guard is configured rather than implying one ran.
- No mutation of `GroupRoom.members`, bot profiles, `config.yaml` or any harness
  state. P1/P2/P3/S4 constrain and disclose; they never edit.
- No cross-process coordination. Two Gateways writing the same `rrsi_rounds.json`
  are two independent searches, not a shared one.

## Tests

`backend/tests/test_rrsi_config.py` (Table 5 pins, refusal-not-clamp, `K`),
`test_rrsi_proposal_side.py` (P1 schedule and cap, P2 ledger durability, P3,
S4, Algorithm 1 composition), `test_rrsi_selection_side.py` (S2/S3/S5/attribution,
S1, Algorithm 2, the promotion conjunct and its wiring),
`test_rrsi_store.py` (durable state) and `test_rrsi_generator_plan.py` (the
proposal-plan seam and the engine's disclosure). The pre-existing RSI gates —
`tests/test_rsi_cycle.py`, `test_rsi_variant_generation.py`,
`test_rsi_promotion.py`, `test_rsi_wave4.py`, `test_no_orphan_modules.py`,
`test_feature_manifest_wiring.py` and `test_wired_durable_runtime_surfaces.py` —
pin the boundaries this subpackage must not move.
