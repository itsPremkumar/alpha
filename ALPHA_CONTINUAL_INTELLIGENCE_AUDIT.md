# Alpha — Continual Intelligence Audit

**Status:** audit only, no code changed. Baseline established against `HEAD` (`eab9b3c`).
**Rule applied throughout:** the repository is the source of truth. This document
answers "does Alpha already have this?" with evidence (file paths + real signatures),
never with assumption.

---

## 0. Headline finding

**The premise of the implementation prompt is largely inverted.** Alpha does not
lack an evolution/intelligence substrate — it has roughly **four overlapping,
independently-hardened ones**, totalling ~46k lines across `alpha.rsi`,
`alpha.evolution`, `alpha.learning`, and `alpha.config.self_tuning`.

The genuinely absent capabilities are **narrow and specific**, and they are
*composition* gaps, not *capability* gaps: there is no unified expert identity,
no prioritized replay reservoir, no plasticity controller, and no paging tier.
Everything else in the prompt — promotion gates, held-out evaluation, evidence
comparison, lineage, budgets, cooldowns, protected paths, capability-gap mining,
atomic persistence, Sentinel, Git safety — already exists and is stronger than
the prompt describes.

**Therefore this change set does not implement 78 sections. It fills the
measured holes and wires the existing halves together.** Building a second
evolution engine, a second expert registry, or a second memory system would have
violated the prompt's own rule 78 and the repo's orphan-module CI gate.

---

## 1. Existing architecture

### 1.1 Layer boundaries

Enforced by `backend/tests/test_harness_boundary.py`:

| Layer | Import prefix | Location |
| --- | --- | --- |
| Harness (publishable framework) | `alpha.*` | `backend/packages/harness/alpha/` |
| App (Gateway + IM channels) | `app.*` | `backend/app/gateway/`, `backend/app/channels/` |

`alpha` must never import `app`. Any new work goes in the harness and is
exposed to the Gateway by a router.

### 1.2 The four existing evolution/learning substrates

These are **not** duplicates of each other; they have different owners and
different trust levels. That distinction is load-bearing and is preserved.

| # | Subsystem | Path | Lines | Owns | Trust posture |
| --- | --- | --- | --- | --- | --- |
| 1 | **RSI** | `alpha/rsi/` | 6357 | Candidate generation, opportunity mining, promotion routing, human review, shadow/A-B, budgets, cooldown, releases, archive | The gated self-improvement pipeline |
| 2 | **Evolution** | `alpha/evolution/` | 7190 | Bounded surface evolution (skill/prompt/routing/memory_retrieval), identity, repo manifest, source auto-update | Guarded source mutation + repo identity |
| 3 | **Evidence gate** | `alpha/evolution/evidence/` | ~127kB | *Whether a self-change is actually an improvement* | Default-off bar; **not yet wired into any promotion path** |
| 4 | **Self-tuning** | `alpha/config/self_tuning/` | ~150kB | Typed config change-sets, canary, health verify, rollback, hash-linked provenance | Default-off; refuses protected paths |
| — | **Learning** | `alpha/learning/` | 2114 | Experience bank, knowledge graph, skill graph, reflexion, skill curator, curriculum | Experience + skill lifecycle |

`alpha.evolution.evidence` states this itself in its own module docstring:

> *"Alpha has plenty of self-evolution machinery — `alpha/rsi/**`,
> `alpha/evolution/**`, `alpha/evolution/promptbreeder.py`,
> `scripts/auto_update.py`, skill evolution. What it did not have was a
> component that decides whether a proposed self-change is actually an
> improvement."*

That is the correct characterisation, and it is why the audit below treats the
**evidence gate** as the most advanced existing component rather than building a
comparable one.

### 1.3 Existing persistence

| Concern | Owner | Notes |
| --- | --- | --- |
| Atomic write (canonical) | `alpha/persistence/storekit/atomic.py` | `atomic_write_bytes/text/json`, `FsyncPolicy`, `InjectedCrash`, fault-injection hooks per step. **The one to reuse.** |
| Atomic write (evolution-local) | `alpha/evolution/identity.py::atomic_write_json` | uuid-suffixed tmp + `os.replace`. Used by `alpha.rsi.state`. |
| Experts store | `alpha/experts/registry.py` | json + tmp + `fsync` + `os.replace`, loud on corruption (`ExpertStoreUnreadable`) |
| RSI cycle state | `alpha/rsi/state.py` | atomic, corrupt ⇒ `(None, real_error)`, resume quarantines |
| RSI lineage | `alpha/rsi/lineage.py` | append-only JSONL + snapshot, hash-chained payload |
| Provenance chain | `alpha/evolution/evidence/provenance.py` | hash-linked append-only, `replay_chain()` |
| Self-tuning provenance | `alpha/config/self_tuning/provenance.py` | `ProvenanceLedger` with `verify_entries`, `replay` |
| Sentinel reports | `alpha/runtime/sentinel/report_store.py` | append-only, corrupt-aware |
| Reflexion / trajectories | `alpha/learning/reflexion.py`, `alpha/trajectory/store.py` | **SQLite** |
| DB + migrations | `backend/app/gateway` + alembic | `make migrate-rev MSG="..."` |

> **Note:** there are ~13 private `atomic_write*` helpers scattered across the
> harness (`memory/*/paths.py`, `company_os/store.py`, `swarm/coordinator.py`,
> …). Consolidating them onto `storekit.atomic` is a real cleanup but is
> **out of scope** for this change set — it touches ~13 modules' persistence
> and belongs in its own PR.

### 1.4 Gateway API surface (63 routers)

Existing learning/evolution-adjacent endpoints:

| Router | Prefix | Relevance |
| --- | --- | --- |
| `evolution.py` | `/api/evolution` | candidates, benchmark, gate, rollback, ledger, identity, update-check/apply/skip/recover |
| `benchmarks.py` | `/api/benchmarks` | suites + recent results |
| `evidence.py` | `/api/evidence` | evidence-gate surfaces |
| `memory.py` | `/api/memory` | memory data/config/status |
| `skills.py` | `/api/skills` | incl. `/curator`, `/proposals`, `/graph` |
| `capability_honesty.py` | `/api/capability-honesty` | wiring-state audit |
| `ops.py` | `/api/ops` | version/status/resources/advice/runtime/network |
| `ops_integration.py` | `/api/ops/integration-health` | live coverage + supervisor status |
| `system_monitor.py` | `/api/system-monitor` | host CPU/RAM/disk/GPU |
| `autonomy.py` | `/api/autonomy` | background loop control |

**There is no `/api/intelligence/*`.**

### 1.5 Background loops (`app/gateway/autonomy/supervisor.py`)

Single owner of all 9 loops; absent id = disabled; flags off = zero tasks:
`sentinel`, `perpetual`, `review_queue`, `skill_curator`, `enterprise_heartbeat`,
`swarm_status`, `free_models_sync`, `company_operations`, `self_update`.

### 1.6 Configuration

Single source of truth `config.yaml` (`config_version: 57`). Relevant existing
sections: `evolution_evidence`, `self_tuning`, `skill_evolution`,
`specialists`, `capabilities`, `autonomy`, `reasoning` (via `reasoning/`), `models`,
`model_routing`. **No `learning:`, `experts:`, `paging:`, or `replay:` section.**

### 1.7 Frontend

37 section components in `frontend/src/components/sections/`; no
`IntelligenceSection`. `frontend/src/lib/evolution.ts` is a typed client for
`/api/evolution` with 13 pinned test files including `evolution.test.mjs` and
`evolution-update.test.mjs`.

---

## 2. Existing feature matrix

Status is `YES` (reuse) / `PARTIAL` (extend) / `NO` (implement).

### 2.1 Experience, sanitization, replay

| # | Feature | Status | Current implementation | Problems | Required modification |
| --- | --- | --- | --- | --- | --- |
| 1 | Experience capture | **PARTIAL** | `alpha/learning/experience/models.py::ExperienceRecord` — `task_goal`, `outcome`, `lessons_learned`, `pitfalls_to_avoid`, `error_types`, `tags`, `evidence`, `confidence`, `success/failure/reuse_count`, `audit[]` | No structured `actions/agents/skills/tools/models/observations`, no `cost`, no `duration_ms`, no `verification`, no `provenance` block, no `trust`. Records are task-trajectory shaped, not execution-trace shaped. | Add an **optional** telemetry block so existing records round-trip unchanged. Do not reshape `ExperienceRecord`. |
| 2 | Secret/PII screening | **YES** | `experience/store.py::SECRET_PATTERNS` (AWS, GitHub, Slack, OpenAI, PEM, JWT, credential assignments, email) + `find_secret_shape` | Pattern-only denylist (acknowledged, deliberately, in docstring) | Reuse as-is. |
| 3 | Evidence gating on intake | **YES** | `ExperienceStore.add()` refuses without non-empty `evidence`; `record()` keeps legacy EPISODE behaviour | — | Reuse as-is. |
| 4 | Record hygiene (dedup/merge/decay) | **YES** | `alpha/learning/experience/hygiene.py::run_hygiene` → `HygieneReport`; merge, dedup, confidence from real reuse counts, audited | — | Reuse. |
| 5 | Multi-step sanitizer pipeline | **NO** | Only a flat pattern screen inside the store | No staged pipeline; **no prompt-injection detection**; no quality validation; no content-addressed dedup key; no provenance validator | New staged sanitizer that **calls** the existing screen, not a second screen. |
| 6 | Replay reservoir | **NO** | — | Nothing samples across recency/importance/diversity/failure-value. `alpha.trajectory.store.replay_from_step` re-executes a *trajectory step* — a different concept entirely, not prioritized experience replay. | **Implement.** Highest-value genuine gap. |

### 2.2 Evaluation, regression, promotion

| # | Feature | Status | Current implementation | Problems | Required modification |
| --- | --- | --- | --- | --- | --- |
| 7 | Benchmark harness | **YES** | `alpha/benchmarks/runner.py::BenchmarkRunner/BenchmarkSuite/BenchmarkCase/BenchmarkResult`; suites in `benchmarks/suites.py` | Only 3 registered suites | Reuse runner; add suites. |
| 8 | Held-out evaluation | **YES** | `alpha/rsi/holdout.py` — real executed suite, `evidence_kind="measured"` only, `score=0.5/unverified` fail-closed, hidden case ids never in candidate payloads | Only 5 trivial config-range cases | Reuse contract verbatim. |
| 9 | Evidence gate | **YES** | `alpha/evolution/evidence/` — `integrity.py` (detects tampering with its own decision logic), `gates.py`, `compare.py` (noise floors, `within_noise`), `decision.py` (`needs_human`), `measure.py`, `provenance.py` | **Default-off and not wired into any promotion path** (stated in its own docstring) | Wire it in as the pre-promotion bar, default-off. Do not reimplement. |
| 10 | Promotion routing | **YES** | `alpha/rsi/promotion.py::route_promotion_decision` — lineage, evidence-standard, holdout, human-review gates; `decide()` → `PromotionDecision` | — | Reuse. |
| 11 | Evolution gate | **YES** | `alpha/evolution/engine.py::EvolutionEngine.gate` — strictly-better + zero-regression + human approval unless `autonomous_mode`; `FORBIDDEN_SURFACES` | — | Reuse. |
| 12 | Release gate | **YES** | `alpha/benchmarks/release_gate.py::evaluate_release_gate` with `MetricGate`/`GateFailure` | — | Reuse. |
| 13 | Regression safety | **PARTIAL** | `alpha/reproduction/gates.py` — `PreFixFailureGate`, `PostFixVerificationGate`, `RegressionSafetyGuard` (fix-scoped, not learning-scoped) | Not a standing suite every learning change is measured against | Extend: suite that a learning candidate must pass. |
| 14 | Shadow / A-B | **YES** | `alpha/rsi/shadow.py::run_shadow` → `ShadowComparison` | — | Reuse. |
| 15 | Overfitting detection (train↑/held-out↓) | **NO** | — | No train/held-out gap tracked anywhere | Implement on top of existing holdout + scorecard. |
| 16 | Category taxonomy for regression suite | **NO** | Cases are flat, id-keyed | No coding/reasoning/planning/git/mcp/memory/browser/… taxonomy the prompt asks for | Implement as suite metadata. |
| 17 | Bounded reasoning | **YES** | `alpha/reasoning/budget.py::ReasoningBudgetLedger` (`BudgetDimension` ledger, `marginal_value`, `continue_if_value`, `BudgetExhaustedError`), `reasoning/loopguard.py`, `reasoning/governor.py`, `rsi/budgets.py::BudgetTracker`, `max_recursion_limit: 1000` | — | Reuse. This already answers prompt §27/§28. |
| 18 | Adaptive compute | **PARTIAL** | `reasoning/governor.py::evaluate(prompt) -> ReasoningConfig` maps prompt ⇒ tier; `deliberation/router.py`; `learning/curriculum/` has `CapabilityGap.gap_size/priority` | Prompt-text heuristic, no multi-signal estimator; no unified difficulty score | Add a real estimator feeding the existing governor. |

### 2.3 Experts / capability fabric

| # | Feature | Status | Current implementation | Problems | Required modification |
| --- | --- | --- | --- | --- | --- |
| 19 | Expert catalog | **YES** | `alpha/experts/catalog.py` — `Expert` (frozen, `id`/`name`/`category`/`role`/`skills`/`model_hint`/`version`), `ExpertGroup`, `ExpertStep`, `ExpertCatalog` | **Static.** `version` is a hand-written string. No lifecycle, no lineage, no metrics. | Keep as the *declaration* layer; layer dynamics above it. |
| 20 | Expert install/enable state | **YES** | `alpha/experts/registry.py::ExpertRegistry` — atomic json, `install/uninstall/set_enabled`, catalog-validated | Purely declarative enablement | Keep; do not extend with dynamics — separate concern. |
| 21 | Specialists | **YES** | `config.yaml -> specialists` — declared units of expertise with `role` ring, `tool_groups`, `routing` **slot**, `capabilities`, `not_for`, duplicate-territory refusal | Declared, not learned | Reference from the router. |
| 22 | Stable dynamic expert identity | **NO** | No expert entity with stable id across restart | `Expert.id` is a catalog key, not a learned identity. No `generation`, `parents`, `status`, `reason`. | **Implement.** |
| 23 | Expert lineage | **PARTIAL** | `alpha/rsi/lineage.py::RsiLineageStore` — `RsiLineageRecord`, `record()`, `transition()`, `ancestry()`, `promotions()` | **Candidate-scoped** (evolution surfaces), not expert-scoped. Not exposed by any route. | Reuse the hash-chained store pattern for experts. |
| 24 | Expert lifecycle | **NO** | — | `alpha/learning/autonomous_curator.py::SkillLifecycleState` is **skill**-scoped; nothing expert-scoped | Implement expert-scoped states. |
| 25 | Expert routing | **PARTIAL** | `alpha/capabilities/eligibility.py` (tag match, `eligible_candidates`), `model_routing` slots | Tag **matching**, not ranking; no exploration; no utility score | Add ranking + admission above the existing matcher. |
| 26 | Exploration bonus | **NO** | — | Nothing prefers underused specialists; nothing prefers nothing | Implement, gated so it never overrides production safety. |
| 27 | Expert utility scoring | **PARTIAL** | `SkillMetadata.error_rate()` in the curator | Skill-scoped, usage+error only | Implement replaceable expert scorer. |
| 28 | Expert pruning | **PARTIAL** | `AutonomousSkillCurator.apply_automatic_transitions` (skill-scoped), `self_tuning` cooldown | Skill-scoped; no archive-before-delete, no protection list for experts | Implement with archive + lineage + reason. |
| 29 | Paging (disk→RAM→VRAM) | **NO** | — | Zero hits for `ResidentSet`/`EvictionPolicy`/`paging`. There is a bounded model cache in the memory backends, but no expert paging tier. | **Implement.** Interfaces + one real policy. |
| 30 | Stable-core vs adaptive tiers | **PARTIAL** | `rsi/protected_paths.py`, `self_tuning.protected_paths` | Path-protection, not plasticity tiers | Reuse the protection idea; add a learning-rate controller. |

### 2.4 Safety, provenance, recovery, self-knowledge

| # | Feature | Status | Current implementation | Problems | Required modification |
| --- | --- | --- | --- | --- | --- |
| 31 | Protected paths | **YES** | `alpha/rsi/protected_paths.py`; `self_tuning.protected_paths` (auth, sandbox, approvals, kill switches, token budgets) | — | Reuse the list. |
| 32 | Provenance chains | **YES** | Three hash-linked implementations (`evidence/provenance.py`, `self_tuning/provenance.py`, `rsi/lineage.py`) | **Three separate mechanisms**, no unified learning event | Add one learning journal that **references** these. |
| 33 | Snapshots / rollback | **YES** | `sentinel/checkpoint.py::CheckpointManager`, `self_tuning/rollback.py::RollbackManager/verify_or_rollback`, `rsi/releases.py`, `evolution.update_engine` guarded rollback behind a backup ref | No snapshot primitive for *learning state* | Reuse `CheckpointManager` pattern. |
| 34 | Bounded retries | **YES** | `rsi/cooldown.py` (`record_promotion`, `evaluate_cooldown`), `rsi/budgets.py`, `max_recursion_limit` | — | Reuse. |
| 35 | Safe learning modes | **PARTIAL** | Ad-hoc booleans: `evolution_evidence.enabled`, `self_tuning.enabled`, `autonomous_mode`, `skill_curator_tick(dry_run=True)`, `local_digest` executor | No mode enum; a mode cannot be *read back* | Add one enum that maps onto these existing switches. |
| 36 | Dry-run learning | **PARTIAL** | `workflow.time_travel.simulate_run` (throwaway engine), `skill_curator_tick(dry_run)`, `rsi/shadow.py` | No "what would change if I learned from these?" | Implement in terms of shadow + curator dry-run. |
| 37 | Capability-gap detection | **YES** | `alpha/rsi/opportunity.py::mine(error_events, feedback, benchmark_regressions)` → `Opportunity` with `prioritization()`; `learning/curriculum::CapabilityGap` | — | **Reuse — this is prompt §19 exactly.** |
| 38 | Sentinel | **YES** | `alpha/runtime/sentinel/` — `Signals`, `SignalTracker`, `SentinelLoop`, `Verifier`, `CheckpointManager`, `Committer`, `ReportStore` | Not fed by learning events | Feed it. |
| 39 | Observability | **YES** | `alpha/trace_context.py` (`X-Trace-Id`, `resolve_trace_id`), `alpha/events/bus.py`, `alpha/tracing/` | — | Reuse; never add a second logger. |
| 40 | Resource awareness | **YES** | `app/gateway/system_monitor_service.py`, `/api/ops/resources`, `/api/ops/advice`, `self_tuning/dynamics.py::ResourceGovernor` with `ResourceSignals` + `ResourceRule` | Learning is not yet gated on it | Wire learning to `ResourceGovernor`. |
| 41 | Self-knowledge | **NO** | Scattered across `/api/features`, `/api/ops/*`, `/api/ops/integration-health`, `/api/evolution/identity` | No single structured, machine-readable intelligence snapshot | Implement a **read-only projection** over existing state. |
| 42 | Git safety | **YES** | `alpha/evolution/git_source.py`, `evolution/update_engine.py` (fast-forward only, backup ref, clean worktree, admin-gated, rollback), `sentinel/commit.py::validate_paths/is_forbidden_path` | — | Reuse; do not touch `main`. |
| 43 | Run persistence / recovery | **YES** | `RunManager` + leases + `SafeRunRecoveryService` + `run_events` + checkpoint channels | — | Reuse. |
| 44 | Local models / Ollama | **YES** | `alpha/models/` + `model_routing` + `free_gateways` + `models/free_router` | — | Reuse. |
| 45 | ContinualLearner interface | **NO** | — | `alpha.rsi.strategy_memory.StrategyMemory` is a recall store, not a learner seam | Implement a narrow seam **only if** a learner lands. Low priority. |

---

## 3. What mini-AGI contributes that Alpha does not have

Taken deliberately from the reference implementation, at the correct altitude:

1. **Prioritized experience replay across strata** — sample from
   recent / high-value / rare-failure / regression / strategy / recovery strata
   so the newest domain cannot erase older knowledge. → `#6`, genuinely absent.
2. **A plasticity dial driven by measured trends** — more learning when
   improvement is clear, less when forgetting appears, freeze on regression. →
   `#6`/`#30`, genuinely absent.
3. **Explicit train / held-out separation with a tracked gap** — Alpha has a
   real held-out suite but never *compares* train vs held-out. → `#15`, absent.
4. **A disk-backed expert pool with an eviction policy** so the pool need not
   be resident. → `#29`, absent.
5. **A stable identity + generation counter for learned entities** that survives
   restart, paging, pruning, and migration. → `#22`, absent.
6. **Adaptive recurrent computation** — Alpha has the budget ledger and the
   governor, so this is already covered by `#18`'s predecessor; only the
   multi-signal estimator is missing.

**Explicitly rejected from mini-AGI** (per prompt §3 and repo rule): byte-level
tokenizer, toy language model, its hyperparameters, its expert counts, its
thresholds. None of it is imported.

---

## 4. Implementation plan derived from the audit

Only the `#NO` rows above, plus the three `#PARTIAL` rows that need a new seam
(`#1`, `#15`, `#35`). Everything else is wired to, never rewritten.

| Phase | Work | Location | Depends on |
| --- | --- | --- | --- |
| B | Experience telemetry block (optional field) + staged sanitizer | `alpha/intelligence/experience/` | reuses `learning.experience` screen |
| C | Regression suite w/ category taxonomy + overfitting gap | `alpha/intelligence/regression/` | `benchmarks.runner`, `rsi.holdout` |
| D | Replay reservoir (bounded, stratified, prioritized) | `alpha/intelligence/replay/` | `ExperienceRecord` |
| E | Snapshot/rollback for learning state | `alpha/intelligence/snapshots/` | `persistence.storekit.atomic`, `sentinel.checkpoint` |
| F | Plasticity controller (tiered, trend-based) | `alpha/intelligence/plasticity.py` | C, D |
| G/H | Expert identity, lineage, lifecycle, registry, router | `alpha/intelligence/experts/` | `experts.catalog`, `rsi.lineage`, `capabilities.eligibility` |
| I | Paging: `ExpertStore`/`ResidentSet`/`EvictionPolicy` | `alpha/intelligence/paging.py` | G |
| J | Difficulty estimator → existing governor | `alpha/intelligence/difficulty.py` | `reasoning.governor` |
| K | Learning journal (hash-linked, references existing ledgers) | `alpha/intelligence/journal.py` | `storekit.atomic` |
| L | Safe-mode enum mapping onto existing switches | `alpha/intelligence/modes.py` | existing flags |
| M | `/api/intelligence/*` **read-only** projection + UI section | `app/gateway/routers/intelligence.py`, `frontend/.../IntelligenceSection.tsx` | all above |
| N | Wire evidence gate + Sentinel + ResourceGovernor | promotion path, supervisor loop | C, F |

**Deliberately not built:** a second evolution engine, a second experience
store, a second atomic-write helper, a second model router, a second lineage
store, an expert system that retrains the base model.

---

## 5. Baseline test state (before any change)

```
uv run pytest -m "not live" --ignore=tests/blocking_io \
  tests/test_experience_memory.py tests/test_evolution_identity.py tests/test_release_gate.py -q
→ 52 passed, 1 warning in 259.33s
```

Lint/format gate is `ruff check .` + `ruff format --check .` (line length 240).
Full offline suite is `uv run pytest -m "not live" --ignore=tests/blocking_io tests/`.