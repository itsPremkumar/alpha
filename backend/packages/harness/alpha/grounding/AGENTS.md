# Grounding layer (`alpha/grounding/`)

The layer that stops the agent claiming things it did not check. This file is
the normative contract; the module docstrings carry the evidence.

## What this is for

Alpha already had a lot of correctness machinery — `alpha.epistemics`,
`alpha.evidence`, `alpha.verification`, `alpha.critique`, `alpha.metacognition`,
`alpha.capabilities.honesty`. The defect was not missing pieces. It was that
**none of them sat between a model request and a tool call**, so a run could pass
through every one of them while every claim it made went unchecked.

This package adds the wiring (`agents/middlewares/grounding_middleware.py`, wired
in `agents/lead_agent/agent.py::build_middlewares`) plus the three things that
genuinely did not exist: a live capability manifest the agent can read, a claim
ledger with support status and blast radius, and an effort brake.

## The four rules that are structural, not conventional

Each has a `# BITE`-marked test. If you change one, that test fails.

1. **A model gate may raise suspicion; it may never clear a block.**
   `GatePipeline.block` filters `GateLayer.MODEL` results out of the blocking
   path before resolving. Self-preference, position, and verbosity bias are all
   measured, and self-validation has been shown not to improve adversarial
   robustness at all — so a judge is exactly the party that must not have the
   last word.
2. **Model-supplied evidence does not support a claim.**
   `ClaimLedger._derive_support` requires a non-`MODEL_DERIVED` tier for
   `DIRECT`, and downgrades evidence whose locator merely contains the claim's
   own words. Without this a hallucination cites itself.
3. **An unmeasured verdict cannot stop work.**
   `SolvabilityVerdict.authoritative` is false whenever a model-stated
   confidence entered the input. The number is still recorded — it is a real
   signal — but it is quarantined from the gate.
4. **Unmeasured is not zero.**
   `Rate.value` is `None` at zero samples, `duplication_measured` travels beside
   `duplication`, and `reuse_probed` gates the whole section. An unprobed run
   reports `"diagnosis": "unmeasured"`, never a clean bill of health.

## The reuse rule, and why it is enforced this way

Measured over 3,000 turns (`RepoReuse`, 2026): self-recall of the agent's own
earlier code sits above 98% in every turn while self-reuse still decays
83.9% → 69.1%, and 50.8% of task chains hold a cross-turn re-implementation by
turn 5 — with pass rates barely moving.

The ablation is what sets the design:

| What the agent was told | Self-reuse | Structural duplication |
| --- | --- | --- |
| nothing | 30.0% | 65.1% |
| **interface map (names + signatures)** | **67.8%** | **46.7%** |
| full source of its own earlier work | 29.2% | 70.7% |

Therefore:

* `CapabilityEntry` has **no field for source text**. Only `address`.
  `render_for_prompt` is regression-tested against leaking `def`/`class`/`{`.
* `check_reuse_probe` gates **productive** steps only, and is satisfied by a
  read/search call, by `reuse_probe_run=True` (the run already consulted the
  manifest), or by a purely read-only step. An earlier version blocked every
  tool call in a default installation; the wiring test caught it.

## Layer map

| Layer | Module | Prevents |
| --- | --- | --- |
| L0/L5 deterministic gates | `gates.py` | a span with no possible check delivered as fact |
| L1 capability manifest | `manifest.py` | re-implementing what exists; calling a tool that is not installed |
| L2 solvability gate | `solvability.py` | planning work whose preconditions do not hold |
| L3 pre-flight research | `service.py` | acting before knowing what is already known |
| L4 claim ledger | `claims.py` | standing on an unsupported premise for twenty steps |
| L6 memory provenance | `provenance.py` | a poisoned fact outliving the session that planted it |
| L7 effort brake | `effort.py` | talking itself out of a correct answer |
| L8 measurement | `metrics.py` | believing it worked because pass rate held |

`service.py` owns the **order** between them, and the order is the contract:
manifest → gates → solvability (with gate results in hand) → claims → reuse →
effort. An ordering held in each caller's head does not hold.

## Configuration

`config.yaml -> grounding`, read opportunistically by
`alpha.config.grounding_config.resolve_grounding_config` (same pattern as
`verification_loop_config`). Operator control is **budget and breadth only**.

There is deliberately no `disable_claim_gate`, `trust_model_output`,
`skip_side_effect_check`, or `allow_weak_claims`. A loop that can be satisfied by
deleting its assertion is worse than no loop, because it manufactures a green
result; `verification_loop_config` refuses to make `refuse_weakened_tests` a
field for the same reason, and
`tests/test_grounding_wiring.py::test_no_setting_can_switch_off_a_correctness_check`
pins the absence of all four.

`grounding.enabled=false` suppresses **prompt injection only**. The per-step
gates stay live, because a gate is a correctness check and not a feature.

## Boundaries this package does not cross

* **`alpha.epistemics` is not replaced.** It updates a Bayesian posterior over a
  claim; this records what the trajectory cited and who leaned on it. Folding
  them together would mean either losing the audit trail or presenting a
  posterior as evidence.
* **`alpha.evidence` is not replaced.** That is a durable
  candidate/evaluation/promotion ledger; this is per-trajectory and
  support-scored.
* **`alpha.metacognition.calibration` is not re-derived.** It already computes
  Brier scores. This reports claim support and duplication.
* **`alpha.verification` is untouched.** The bounded run→repair→decide
  controller lives there and is separately owned; the wiring gap in
  `docs/VERIFICATION_LOOP.md` §6 is that owner's change, not this one's.
* **`harness must not import `app`.** Pinned by
  `test_grounding_wiring.py::TestHarnessBoundary`.

## Known limitations — stated, not hidden

* **`Availability.DECLARED` is not `AVAILABLE`.** Only an `EntrySource.PROBE`
  entry was actually checked this request. A registry entry proves declaration.
  The manifest reports which is which, but an unprobed deployment is running
  with declared-only facts and no gate will notice that for you.
* **`alpha.capabilities.honesty` covers 4 claims.** The static wiring audit is a
  hand-maintained ratchet, so most subsystem entries render `DECLARED` rather
  than `WIRED`. That is the audit's stated boundary, not a regression here.
* **The lead prompt's static `<rlm_harness_system>` block is not addressed.** It
  still lists ~50 tool names unconditionally, unchecked against the assembled
  toolset. This package supplies the live, checked alternative; it does not
  remove the static list, which is prompt-layer debt in a file this package does
  not own.
* **`task` delegation has no feasibility gate on the task tool itself.** The
  manifest and solvability gate answer "can this be done", but nothing here
  refuses a `task` call for naming an unsuitable subagent;
  `alpha.capabilities.eligibility` is still not on that path.
* **No prose fact-checking.** Every check here is tool-execution- or
  registry-anchored. Nothing verifies that a sentence in an answer is true;
  `claims.py` tracks premises and their citations, not the correctness of the
  final text.
* **`EffortController.classify_tier` is a heuristic on counts**, not a model
  estimate — deliberately, because effort gating is the decision a
  miscalibrated model makes worst.
* **Process-local state.** `ClaimLedger`, `MemoryStore`, and `EffortController`
  are in-memory per run. Nothing here is a durable store, and none of it is
  cross-process exactly-once.

## Tests

* `tests/test_grounding_layer.py` — behaviour, with the measurement that justifies
  each rule in the class docstring.
* `tests/test_grounding_wiring.py` — wiring, and the chain-membership test that
  fails if `GroundingMiddleware` is removed from `build_middlewares`. Every other
  test in the package passes with the middleware deleted; that one does not.