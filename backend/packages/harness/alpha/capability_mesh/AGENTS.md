# Alpha Capability Mesh (`capability_mesh/`)

Evidence-backed capability records for every *runnable configuration* Alpha
can dispatch to — an agent, a model config, a tool, a skill bundle — plus the
durable store those records live in. This is the foundation layer of the
**Alpha Capability Mesh (ACM)**; the full feature plan (passport → memory →
microtask router → team formation → composition → drift → explainability) is
`docs/alpha-arena-new-capability-mesh-features-plan.md`, and this package is
the part that everything else reads from and writes to.

The package is deliberately split so a later phase extends the contract
instead of replacing it: an estimate that arrives later is *the same record*
with real observations behind it. Arena's own judge-side evidence ledger
(`alpha/arena/evidence.py`) is a **different concern** — that one verifies
*claims inside a candidate answer*; this one measures *how well a dispatchable
configuration performs*. Never conflate them: a verified claim is not a
capability measurement, and a capability passport never adjudicates a claim.

## Load-bearing honesty invariants

These are enforced in code *and* pinned by tests; do not relax any of them:

- **No measured estimate with zero observations.** `quality_mean`/`uncertainty`
  require `sample_count >= 1`; any evidence level above `UNMEASURED` requires
  it too. A fabricated statistic is refused at construction, so a hand-edited
  store document cannot smuggle one past the door (`from_dict` re-validates).
- **No score without its profile version.** `OUTCOME_VERIFIED` and
  `RECENTLY_REVALIDATED` require a non-empty `profile_version`: every score is
  traceable to the rubric/profile that produced it, and a rubric change makes
  the old score attributable rather than comparable-by-accident.
- **Evidence level is derived, never hand-set.** `derive_evidence_level()` in
  `models.py` is the single rule (unmeasured → observed → benchmarked →
  outcome_verified → recently_revalidated). The enum is *data*; the ordering
  lives in `EVIDENCE_RANK` because a `StrEnum` compares by string, so
  `min(levels)` would pick "benchmarked" over "outcome_verified"
  lexicographically. Never order levels by their string value.
- **A passport reports its *worst* evidence level**, so it can never over-claim:
  one unmeasured estimate beside three verified ones makes the answer
  unmeasured *for that capability*. `worst_evidence_level(())` is `UNMEASURED`.
- **An unreadable store is disclosed, never reset.** `PassportStore.read_state()`
  returns `StoreState(unreadable=True, passports=(), error=...)`; `get`/`list`
  raise `PassportStoreError` rather than answering with nothing, and every
  mutator refuses to overwrite unreadable state — one bad sector must not become
  amnesia. A *missing* file is genuinely empty and readable: absence is not
  corruption.
- **Derived passports are always UNMEASURED with `sample_count=0`.**
  `passports.py` mints the record shape from what registries already know; it
  never invents a measurement. Registry knowledge is *injected*
  (`known_tool_names`), never imported, so this package stays free of the
  heavyweight tool-graph import — and an unknown tool is refused by name when
  the set is provided.
- **A future-dated timestamp never proves recency.** `derive_evidence_level`
  treats a stamp ahead of the clock (and any unparseable stamp) as *stale*, so
  a corrupted or forged timestamp can only downgrade evidence, never upgrade it.

## What is NOT wired yet (say so when claiming about this package)

Phases A and B ship foundations only: value types, the store, UNMEASURED
derivation, and the measurement instruments (`memory.py`, `calibration.py`,
`drift.py`). There is still **no model-facing tool, no HTTP router, no
executor integration, and no measured data anywhere in the tree** — nothing in
Alpha *reads* a passport or a capability record to make a dispatch decision
today, and nothing *writes* records from a live run. Later phases (microtask
routing, team formation, composition) add those surfaces; until each lands,
describe this package as a tested foundation, never as a capability the agent
can use. Phase C adds the routing *plane* (`fingerprint.py`,
`decomposition.py`, `fit.py`, `assignment.py`, `policy.py`) — still with **no
tool, no router and no executor wiring**; `route()` is a tested pure function,
not a dispatch decision anything makes yet. Phase D adds the graph, pair
complementarity, greedy team selection and gap detection (`graph.py`,
`complementarity.py`, `team.py`, `gaps.py`); Phase E adds the composition
compiler (`synthesis.py`) — again all pure functions with no executor wiring.

## Phase B — cross-run memory, calibration, drift (`memory.py`, `calibration.py`, `drift.py`)

The learning instruments that turn outcome history into honest estimates,
without changing the Phase A contract. Three modules, three jobs:

- **`memory.py` — what happened, compactly (plan §12).** One
  `CapabilityRecord` is one contribution (entity × capability × slot on one
  task) and what became of it: `OutcomeKind` (selected / repaired / rejected /
  materially_edited / unselected), measured `quality` (or honest `None`),
  tri-state `passed_hard_gate` (None = not checked, never fabricated), latency,
  cost, failure mode, and a stated `confidence` — a *calibration signal*, not
  evidence. `CapabilityMemory.estimate_for()` is THE learning function: records
  become one `CapabilityEstimate` routed through the same
  `derive_evidence_level()` rule. `credit_for()` computes conservative credit;
  `prune()` is bounded retention.
- **`calibration.py` — does stated confidence mean anything (plan §8).**
  `calibration()` pairs each record's self-stated `confidence` with its
  measured `quality`, buckets by confidence, and reports a Brier score plus a
  `well_calibrated` verdict.
- **`drift.py` — did performance move (plan §23).** `detect_drift()` splits
  each (entity, capability) stream into a baseline window (oldest N) and a
  recent window (newest M) and reports `DriftSignal`s with the observable
  counts behind every verdict, plus stale entities whose outcome evidence aged
  out of the revalidation window.

Load-bearing rules for this layer, in addition to every Phase A invariant:

- **The profile-version-comparability rule.** A rubric change invalidates
  comparability, so every aggregation is scoped to exactly ONE
  `profile_version`: `estimate_for` counts only records measured under the
  version you pass (v1 samples earn a v2 estimate nothing but honest
  UNMEASURED), `calibration()` excludes other versions before its exclusion
  counter runs, and `detect_drift()` scopes both its values and its staleness
  check. A measured `quality` may not exist without its profile version at
  record construction, so a score is always traceable to the rubric that
  produced it.
- **The credit formula is a *starting policy*, not truth** (plan §12.2):
  `credit = (selected + 0.5·repaired + 0.25·materially_edited) / attempts`.
  REJECTED and UNSELECTED earn nothing, and an attempt nobody measured is
  still an attempt — "appeared in a success" earns nothing without a selection
  outcome. The weights encode a deliberate conservatism chosen as a defensible
  starting point to revisit against real routing outcomes; never present them
  as a measurement of worth.
- **An under-sampled calibration panel says "unresolved", never "well
  calibrated."** Below `min_samples_for_verdict` comparable pairs the verdict
  is `None`, and a bucket needs ≥ 5 samples before it can flip the verdict.
  Zero comparable pairs is a `None` Brier score — never a placeholder 0.0.
- **Drift detection reports, it never acts.** `detect_drift()` is a pure
  reader: it marks entities degraded/stale and preserves audit evidence (every
  `DriftSignal.evidence` names the window sizes, means and threshold), but it
  mutates nothing — not a passport, not a record, not a config, not a routing
  decision. The plan's response list (limit assignments, benchmark, roll back)
  belongs to a later policy layer that *consumes* `DriftReport`. A window
  short of its required measurable samples is an `UNKNOWN` signal naming the
  shortfall, never a fabricated direction, and an entity that never earned a
  delivered outcome is *unverified*, not stale.
- **No task text is ever stored.** `task_fingerprint` is an opaque
  caller-supplied string; `fingerprint(text)` (sha256, first 16 hex, `fp_`
  prefix) exists so callers can build one *without* the raw prompt reaching
  this module in any form.
- **Single-process JSONL, honestly.** `CapabilityMemory` owns one
  `capability_memory.jsonl` per directory: `record()` appends one fsynced line
  (O(1), compacted to a bounded tail), `prune()`/`save()` rewrite it atomically
  (temp + fsync + `os.replace`). A missing file is an empty, healthy memory;
  a corrupt, truncated or dishonest line raises `MemoryStoreError` naming the
  line instead of silently starting empty and later writing amnesia over the
  file. It is atomic against a crash in one process, **never cross-process
  exactly-once** — no lock is taken, and a second Gateway worker sharing the
  directory must serialize writes itself.

## Phase C — fingerprint, decomposition, fit, routing, policy

The routing plane on top of Phases A/B, still with **no tool, no router and
no executor wiring** — `route()` is a tested pure function, not a dispatch
decision anything makes yet. Five modules, five jobs:

- **`fingerprint.py` — what situation is this task in (plan §25).**
  `SituationFingerprint` (task/subtask type, complexity band, recency,
  risk, context pressure, required capabilities/tools, user policy) plus
  `fingerprint_task()`, a *deliberately crude, deterministic* keyword
  baseline over the pinned `TERMS` table (word-boundary matches — "prod"
  never fires on "produce"). It is keyword matching, not understanding;
  a model-driven decomposer may replace it, and its output must never be
  presented as intelligent situation assessment.
- **`decomposition.py` — the microtask contract (plan §3.2).**
  `Microtask` (one deliverable, explicit acceptance criteria, dependencies,
  attempt bound) and `Decomposition` (bounded at `MAX_MICROTASKS=16`,
  unique ids, resolvable acyclic dependencies, `decomposer` + `notes`
  provenance). `decompose_heuristic()` splits one microtask per declared
  capability (or stays single-step); `decompose_model()` is the model seam
  whose `generate` callable is **injected, never imported, never called at
  import**; `try_decompose()` is the one caller that falls back — with the
  disclosure stamped into the result.
- **`fit.py` — context-aware fit scoring (plan §3.3).**
  `eligible_entities()` applies the hard gates FIRST (disabled, missing
  tool), `fit_score()` composites observable parts
  (`COVERAGE_WEIGHT 0.5`, `QUALITY_WEIGHT 0.35`, `TOOL_WEIGHT 0.15`), and
  `rank_entities()` sorts deterministically. Every factor appends its
  number to `FitScore.score_explanation`; `profile_version` is threaded
  into every memory read (the Phase B comparability rule).
- **`assignment.py` — roles and routing (plan §3.4).**
  `Role` (PRIMARY / CHALLENGER / VERIFIER / INTEGRATOR),
  `MicrotaskAssignment`, `RoutingPlan`, and `route()` — the whole
  step-to-specialist map with refusals and notes.
- **`policy.py` — user-selectable decision policy (Feature N).**
  `DecisionPolicy` (8 policies), `PolicyProfile`, `resolve_policy()` — a
  resolution TABLE, not intelligence — and `merge_fingerprint_policy()`.

Load-bearing rules for this layer, in addition to every Phase A/B invariant:

- **Acceptance criteria are required on EVERY microtask AND every
  assignment.** `Microtask` and `MicrotaskAssignment` both refuse
  construction without a non-empty `acceptance_checks`, and the assignment
  copies the microtask's checks — "no assignment without explicit
  acceptance criteria" (plan §25) holds on both records, so a routed
  assignment can always be objectively accepted or rejected.
- **Hard refusals cannot be overridden by any score.** Eligibility
  (`eligible_entities`) is a gate evaluated BEFORE any fit score exists; a
  disabled passport or one missing a required tool is refused no matter
  what it would have scored. Penalties are not gates and gates are not
  penalties — never "solve" a missing tool with a high capability score.
- **Unmeasured priors never outrank measurements.** An UNMEASURED
  capability contributes a `UNMEASURED_PRIOR` (0.25) coverage fraction and
  a `NEUTRAL_QUALITY` (0.5) quality — a prior, never evidence. The ranking
  tiebreak puts `estimated=True` scores after measured ones immediately
  after the score itself (before uncertainty), because the prior's constant
  0.1 uncertainty would otherwise beat a measured 0.3 and smuggle itself
  ahead on an equal score.
- **A model decomposition failure is disclosed, never silently swapped.**
  `decompose_model()` raises `DecompositionError` (naming the cause) on a
  `None`/non-string/garbage/over-bound/invalid reply — it never quietly
  falls back to the heuristic, because deciding to fall back is the
  CALLER's. `try_decompose()` is the caller that may fall back, and it
  stamps `decomposer="heuristic_baseline"` plus the note "model
  decomposition unavailable; heuristic baseline used" (and the cause) onto
  the result, so the disclosure travels with the decomposition.
- **Exactly one PRIMARY per microtask.** `RoutingPlan` refuses two by
  name; a CHALLENGER/VERIFIER without a PRIMARY is refused; and a
  microtask both assigned and refused is refused (one microtask, one
  honest fate). `route()` additionally proves every microtask is assigned
  or named in a refusal before returning — work is never silently dropped.
- **A challenger is earned.** Only when the policy wants one, the runner-up
  is within `CHALLENGER_MARGIN` (0.15) of the primary, and the primary's
  evidence is NOT `RECENTLY_REVALIDATED` — a well-measured leader needs no
  second opinion. A verifier must know a capability DIFFERENT from the
  primary's weakest required capability, and is never the primary or the
  challenger. **INTEGRATOR is never auto-assigned**: composition phase
  owns integration, and the plan says so in `notes`.
- **Policies are a resolution table, not intelligence.** `resolve_policy()`
  reads one explicit inspectable row per policy; an unknown name is
  refused listing the valid ones; bounds (`agent_count` 1..64,
  `default_agents`) are refused, never clamped; the caller's budget is the
  stated rule `min(policy preference, default_agents)`. **`LOCAL` cannot
  verify locality** — the mesh has no runtime locality constraint to check
  today, so the LOCAL rationale discloses that it declares intent it
  cannot enforce; never present it as a guarantee.
- **Recency may only be RAISED by a policy, never lowered.**
  `merge_fingerprint_policy()` ORs `recency_required`: it is a
  user-visible task requirement, and a policy that does not itself demand
  recency cannot know the task stops needing it.
- **The fit heuristic weights are a documented starting policy, not
  truth** (the plan forbids presenting hard-coded weights as objectively
  correct): module constants, hand-checkable, to be tuned against measured
  routing outcomes later — which requires wiring that does not exist yet.

## Phase D — capability graph, complementarity, teams, gaps

The structural plane on top of the routing plane, still **pure functions with
no tool, no router and no executor wiring**. Four modules, four jobs:

- **`graph.py` — the empirical capability graph (plan §4.1).** Node kinds
  (ENTITY / CAPABILITY / TASK_CATEGORY / TOOL / SKILL / FAILURE_MODE /
  OUTCOME / TEAM) and edge kinds (`DEMONSTRATED_CAPABILITY`,
  `REQUIRES_TOOL`, `OFTEN_MISSES`, `VERIFIED_ON`, `COMPLEMENTS`,
  `DUPLICATES`, `CONFLICTS_WITH`, `IMPROVES_WITH_SKILL`).
  `build_from_passports()` weights a demonstrated capability by its measured
  quality (the `UNMEASURED_PRIOR` when unmeasured) and stamps
  `evidence_count = sample_count`; `build_from_memory()` derives
  `OFTEN_MISSES` from recorded failure modes. An edge with zero observations
  is **speculative** — a hypothesis, never evidence — and callers must honour
  that.
- **`complementarity.py` — measured pair value (plan §4.2).** `PairEstimate`
  (solo A, solo B, together, disagreement usefulness, redundant-call rate,
  added cost/latency) with `PairMemory` behind it. `complementarity_value()`
  returns `together - max(solo_a, solo_b)` **only when all three are
  measurable** — otherwise `None`, never a fabricated synergy number.
  `correlated_failure_risk()` reports `known=False` when neither entity has
  recorded failure modes: unknown is not safe.
- **`team.py` — the greedy portfolio selector (plan §4.3–§4.5).**
  `select_team()` adds the highest-fit eligible entity as PRIMARY, then the
  candidate with the best *marginal* coverage gain adjusted by measured
  complementarity (a SPECULATIVE pair contributes **half** value and flags
  the member speculative), correlated-failure risk and cost pressure, then
  a VERIFIER when the policy wants one. It stops on
  marginal value, budget, `MAX_TEAM_SIZE` (64, refused not clamped) or no
  candidates, and records the reason in `TeamPlan.stops`. Every member's
  `reason` carries its marginal number; `total_predicted_cost` is `None`
  when any member is unpriced (never a reassuring 0.0).
- **`gaps.py` — capability gap detection (plan §6, DATA ONLY).**
  `detect_gaps()` classifies each required capability as
  `MODEL_GAP` / `TOOL_GAP` / `EVIDENCE_GAP` (declared but UNMEASURED) /
  `QUALITY_GAP` (measured below the floor, naming both numbers).
  `plan_specialist()` returns a `TemporarySpecialistPlan` — capability,
  tools, smoke check, promotion gate, `retire_after_run=True` — that is a
  **plan, never an action**: nothing here creates, installs, registers or
  promotes anything, and a temporary role built on the same model is
  specialization, **not** an independent model.

Load-bearing rules for this layer, in addition to every earlier invariant:

- **A SPECULATIVE pair never drives an asserted synergy.** Below
  `PAIR_MIN_SAMPLES` the evidence is `SPECULATIVE`, it contributes half
  weight to team selection, and the member it justifies is flagged
  speculative — so the plan is flagged, and a reviewer can see the synergy
  was a hypothesis.
- **Independence is disclosed, never faked.** When every member of a team
  shares a `model_family` or `provider`, `TeamPlan.independence_note`
  says so *and names the shared value*; a single member says
  "single specialist; no independent verification"; and role variety is
  NOT diversity (plan §4.4) — strategy cards do not prove independent
  judgement.
- **Diversity is measured on units, not roles**: model family, provider,
  tool path, verification method, entity kind. Two members that differ only
  by role produce an empty `diversity_axes` delta and an honest
  independence note.
- **Gaps are findings and plans, never actions.** `detect_gaps()` names
  the observable reason ("no passport declares this capability" / "declared
  but UNMEASURED" / "measured quality 0.31 below floor 0.5"). An unresolvable
  temporary specialist — no base entity, no data source — is reported as
  such rather than invented.

## Phase E — best-per-step composition (`synthesis.py`)

The composition compiler (plan §7 + §22): contribution slots, deterministic
slot building, conservative conflict detection and greedy resolution — still a
**tested pure function**, with no tool, router or executor wiring. The central
rule is the one Feature U makes load-bearing:

- **Incompatible assumptions are never merged into a synthetic "best of
  both".** `resolve_slots()` leaves `composed_artifact` as `None` while ANY
  slot holds a `CONFLICT`, and `CompositionResult.__post_init__` refuses a
  non-None artifact alongside a conflict by name. Every conflict also produces
  at least one entry in `alternatives` describing the divergent assumptions
  and the conditions under which each would be chosen.
- **Everything the compiler cannot know is honest.** A slot's `selected_reason`
  carries its selection number; `unresolved_capabilities` names what could
  not be covered; a `Evaluation` with no assumption found on an UNMEASURED
  capability yields the explicit placeholder
  "`[assumptions unknown — unmeasured capability]`" rather than a silent
  empty tuple that would read as "no assumptions".
- **The heuristics are crude, and say so.** `extract_assumptions()` is
  keyword-window matching, `detect_conflicts()` is string-level contradiction
  matching ("not X" vs "X", "requires Y" vs "excludes Y", mutually exclusive
  `use:` capabilities), and `resolve_slots()` picks by
  quality × evidence rank. A missed conflict is a false `COMPATIBLE` — which
  is exactly why the compiler is a baseline, not a final answer.
- **`QUALITY_FLOOR` (0.5) is a documented constant, not a learned
  threshold**: an artifact below it is `FAILED`, at or above with the evidence
  floor met is `PASSED`, and an unmeasured stream can only reach `UNCERTAIN`.

## Storage

`PassportStore` owns exactly one JSON document (`passports.json`) in a
caller-chosen directory, written atomically (temp file + fsync + `os.replace`,
UTF-8, `ensure_ascii=False`) and version-stamped (`STORE_DOCUMENT_VERSION`).
It is single-process JSON state: atomic against a crash, **never**
cross-process exactly-once; no lock is taken here, and a second Gateway worker
racing the same directory must serialize writes itself. A duplicate
`entity_id` keeps the first occurrence and names the duplicates in
`StoreState.error` — rows are never silently merged, and `replace_all` refuses
duplicates by name before writing.

## Tests

`backend/tests/test_capability_mesh_models.py` (evidence-level derivation
matrix, constructor refusals, serialization round-trips, worst-level
aggregation), `test_capability_mesh_store.py` (lifecycle, atomicity, corrupt /
duplicate / missing documents), `test_capability_mesh_passports.py` (derivation
shape, registry injection, unknown-tool refusal),
`test_capability_mesh_memory.py` (record honesty rules, fingerprint
non-reversibility, estimates through every evidence level, profile-version
isolation, the credit formula incl. the unmeasured-attempt case, prune with
and without a keep predicate, JSONL durability and corrupt-line refusal),
`test_capability_mesh_calibration.py` (bucketing, Brier math on a known input,
exclusion accounting, the unresolved-under-min and bucket-floor verdict
rules), `test_capability_mesh_drift.py` (UNKNOWN on thin streams,
degraded/improved/unchanged detection, materiality boundary, staleness with an
injected `now`, report-don't-act), `test_capability_mesh_fingerprint.py`
(pinned `TERMS` table, word-boundary matching, UNKNOWN-is-the-honest-default,
round-trips), `test_capability_mesh_decomposition.py` (acceptance-criteria
required, `MAX_MICROTASKS` bound, cycle-by-name, the model seam's JSON
defense — non-JSON, fences, unknown keys, oversized output, empty list — and
`try_decompose` fallback disclosure), `test_capability_mesh_fit.py`
(hard-gate-before-score, unmeasured-prior-never-outranks-measured, weight math
on hand-computed inputs, recency multiplier, profile-version-scoped memory),
`test_capability_mesh_assignment.py` (one-PRIMARY invariant, challenger margin
and evidence gate, verifier's different-competence rule, integrator deferred,
uncovered-microtask refusal path), `test_capability_mesh_policy.py` (the
resolution table's every row, agent-budget ceiling, bounds refused not clamped,
LOCAL's locality disclosure, recency raised-never-lowered),
`test_capability_mesh_graph.py` (endpoint/duplicate-kind refusals,
speculative-edge marker, derivation weights and evidence counts, merge
collision), `test_capability_mesh_complementarity.py` (pair refusals,
SPECULATIVE forced at zero samples, `complementarity_value` None cases,
correlated-risk known/unknown), `test_capability_mesh_team.py` (greedy
ordering, marginal/budget/max-size stops, speculative pair at half value,
independence notes, diversity axes, unpriced cost), `test_capability_mesh_gaps.py`
(gap classification paths, quality floor, UNMEASURED reason, specialist-plan
refusals, retire default), `test_capability_mesh_synthesis.py` (honest slot
fields, assumption extraction conservative, conflict-detection boundary cases,
resolution ordering, `composed_artifact=None` under conflict, alternatives
per conflict, policy switching, `ArtifactStore` round-trip and corrupt-file
refusal). All offline, no model calls.
