# Alpha RSI Implementation Plan

**Purpose:** A detailed, actionable, phased implementation plan to bring the RSI (Recursive Self-Improvement) features proposed in `references/RSI_AGENT_ARCHITECTURE.md` into Alpha — mapped against what Alpha actually contains today.

**Source document:** `references/RSI_AGENT_ARCHITECTURE.md` (111 sections, read end-to-end).
**Date:** 2026-09-23.
**Status:** DRAFT — for review.
**Authoring constraint:** single-file write scope; this document is the only artifact produced by this pass.

> **Reality-mapping statement:** Every claim below is reality-mapped to actual Alpha files with exact repo-relative paths. Function/class names cited in this document were read from the files in this working copy of the repository (not assumed from the spec). Paths that were verified to exist are cited as-is; anything not verified is called out explicitly in §7 ("Unverified / not audited").

---

## 1. How to read this document

- All paths are **repo-relative from the repository root** (`C:\Users\PREM KUMAR\Videos\alpha`), forward-slashed, e.g. `backend/packages/harness/alpha/rsi/engine.py`.
- The spec sometimes writes `alpha/rsi/…`; in this repository the package root is `backend/packages/harness/alpha/`, so `alpha/rsi/engine.py` ≡ `backend/packages/harness/alpha/rsi/engine.py`. Both spellings appear; the repo-relative form is authoritative.
- Verdicts:
  - **EXISTS** — the feature (or a faithful scoped equivalent) is implemented and reachable from real code paths today.
  - **PARTIAL** — building blocks exist, but the RSI loop does not use them, or a required sub-piece is absent.
  - **MISSING** — no implementation found.
- Every planned feature carries honest-failure semantics: **evaluation errors fail closed, fabricated scores are never produced, "unverified" is reported as `0.5`-neutral with `evidence_kind="unverified"` and is never treated as a pass.**

---

## 2. Feature inventory (spec feature → verdict → proof)

### 2.1 What `alpha/rsi/engine.py` actually does today

File: `backend/packages/harness/alpha/rsi/engine.py` (verified, 151 lines).

- `class RSIEngine` — holds `self.stage` and a hardcoded `active_configurations` dict (`compaction`, `tool_router`, `context_pruner`).
- `run_rsi_cycle(bottleneck, target_component="compaction", force_promote=False) -> RSIResult` — runs a **preview-only** cycle: `_generate_hypothesis()` → `_create_candidate()` (deterministic config tweak, e.g. `max_budget_chars * 0.8`) → `_run_ab_test()` → `_run_holdout_evaluation()` → returns `RSIResult(promoted=False, stage=RSIStage.PREVIEW, evidence_kind="simulated")`.
- `_run_ab_test()` returns **hardcoded** `baseline=0.72 → candidate=0.88`, `confidence=0.92`, `latency_delta_ms=-140.0`, `evidence_kind="simulated"`. `_run_holdout_evaluation()` returns hardcoded `0.80 → 0.89` with the evidence string `"Simulated holdout preview; no regression benchmarks were executed."`
- **It never auto-promotes — verified.** `promoted` is always `False`; `RSIStage.PROMOTED` / `RSIStage.ROLLED_BACK` (defined in `backend/packages/harness/alpha/rsi/models.py`) are **never reached** by any code path in `engine.py`. Evidence explicitly states *"Promotion blocked: preview evidence is not release evidence"*; a `force_promote=True` argument only appends the line *"force_promote cannot bypass evidence or deployment requirements."*
- `get_status()` hydrates UI/API; `get_rsi_engine(project_id)` is a project-scoped singleton (`_RSI_ENGINES`).
- Surface exposure today: builtin tool `alpha/tools/builtins/rsi_engine_tool.py::run_rsi_cycle` (listed in `contracts/feature_manifest.json` ecosystem) and gateway routes on the **projects** router: `GET /{project_id}/rsi/status` → `get_project_rsi_status` and `POST /{project_id}/rsi/cycle` → `run_project_rsi_cycle` in `backend/app/gateway/routers/projects.py` (lines 1351, 1367), plus dashboard hydration around line 1021.
- Tests: `backend/tests/test_rsi_cycle.py` pins the honesty contract — `result.promoted is False`, `stage == RSIStage.PREVIEW`, `evidence_kind == "simulated"`, no `"45/45"` fabricated pass counts, `"Promotion blocked" in evidence`, and `active_configurations` unchanged after a cycle.

**Honest bottom line:** today's RSI engine is a *disclosed simulator/preview*, not an evaluator. It performs no real benchmark, touches no real configuration, and cannot promote. All real gating lives elsewhere (below).

### 2.2 What `alpha/evolution/engine.py` gates on

File: `backend/packages/harness/alpha/evolution/engine.py` (verified, 165 lines).

- `FORBIDDEN_SURFACES = frozenset({"permission_policy", "secrets", "run_admission", "auth"})` — `propose()` raises `ValueError` for these surfaces.
- `EvolutionEngine.propose(surface, target, payload, *, parent_id=None)` → `EvolCandidate(candidate_id="ev-…")`, ledger event `proposed`.
- `record_benchmark(candidate_id, benchmark)` → status `benchmarking`, ledger event `benchmarked`.
- `gate(candidate_id, baseline, *, human_approved=False, autonomous_mode=False) -> tuple[bool, str]` — in order:
  1. missing candidate or benchmark → `(False, "missing candidate or benchmark")`;
  2. `bench.failed > baseline.failed` → status `rejected`, `(False, "benchmark regressions vs baseline")`;
  3. `bench.passed <= baseline.passed` → status `rejected`, `(False, "not strictly better than baseline")`;
  4. `not human_approved and not autonomous_mode` → status **`gated`**, `(False, "awaiting human approval")`;
  5. otherwise → status `promoted`.
- `rollback(candidate_id, reason)` — only a `promoted` candidate can move to `rolled_back`.
- `ledger(limit)` — JSONL persistence at `runtime_home() / "evolution" / "ledger.jsonl"` via `_record_ledger_event()` (append, best-effort, warns on failure) and `_load_persisted_ledger()` (skips corrupt lines with an honest warning count).

**⚠ Mismatch found (honesty report):** the gateway wrapper `backend/app/gateway/routers/evolution.py` declares `class GateRequest(BaseModel): … autonomous_mode: bool = True` (line 27), while the engine default is `autonomous_mode=False`. So `POST /api/evolution/candidates/{id}/gate` **auto-promotes when the caller omits the field**, contradicting both the engine default and Alpha's guardrail that auto-promotion must default OFF. Flagged for a default flip in §3, WP-C2 (behavior-affecting change to an already-registered router — no `app.py` edit required).

### 2.3 Feature inventory table

| # | RSI feature (spec §) | Verdict | Proof in Alpha (verified file → symbol) |
|---|---|---|---|
| 1 | Isolated worktree/copy experiments (§13, §82) | **PARTIAL** | `backend/packages/harness/alpha/sandbox/worktrees.py` → `WorktreeManager.create_worktree/remove_worktree/list_worktrees/worktree_context`; used by `alpha/avo/workspace_runner.py::WorkspaceAVORunner.run_workspace_variation`, `alpha/evaluation/autonomous_benchmark_harness.py::_create_workspace/_run_git`, `alpha/projects/canary_watchdog.py::execute_rollback_if_failed` (calls `WorktreeManager.remove_worktree`). Tests: `backend/tests/test_managed_worktrees.py`. **Gap:** `RSIEngine.run_rsi_cycle` never creates a workspace. |
| 2 | Evaluator manifest w/ provenance + integrity (§16, §45) | **PARTIAL** | `backend/packages/harness/alpha/benchmarks/runner.py` → `BenchmarkSuite(version="v1")`, `BenchmarkRunner.register_suite/run_suite/list_suites` ("versioned suites", results recorded as `suite@version`); `alpha/benchmarks/suites.py::register_eval_suites()`; `alpha/evolution/manifest.py::find_project_manifest_path/load_project_manifest` (project identity manifest, **not** an evaluator manifest). **Gap:** no SHA-256 evaluator-surface manifest, no before/after rehash → QUARANTINE. |
| 3 | Hidden holdout evaluation (§20) | **MISSING** | Only disclosed-simulated previews exist: `alpha/rsi/engine.py::_run_holdout_evaluation` (hardcoded 0.80→0.89, `evidence_kind="simulated"`, "no regression suite executed"); `alpha/enterprise/council.py::run_holdout_benchmark` (hardcoded `score = 96.4`, `evidence_kind="simulated"`, `promote_release_zero_downtime` raises `PermissionError`). No real held-out suite is withheld from candidate-visible evaluation. |
| 4 | Adversarial/red-team review gate for candidates (§21–§22) | **PARTIAL** | Components exist: `alpha/deliberation/adversary_deliberator.py::AdversaryDeliberator.stress_test_design`; `alpha/bots/quality_gate.py::evaluate_quality_gate` ("Red-Team Verification Engine"); `alpha/planning/hyperplan.py::HyperplanPipeline.review_plan` (4 adversarial dimensions, `HyperplanReport.is_blocked`); `alpha/critic/pipeline.py::CriticPipeline.evaluate`; `alpha/governance/council/roles.py` → `IndependentCritic/InvariantVerifier/SecurityReviewer/QualityReviewer/PresidingJudge`. Tests: `test_ai_workforce_phase4.py`, `test_bots_extended_inventory.py`, `test_hyperplan_review.py`, `test_governance_quality_council.py`. **Gap:** none is wired as a gate on RSI/evolution candidates. |
| 5 | Lineage tracking variant→promoted (§12, §29–§30) | **PARTIAL** | `alpha/avo/lineage.py::AVOLineage.commit_candidate/get_history/get_pareto_frontier/get_trajectory_archive` (test `test_avo_evolution_lineage.py`); `alpha/lineage/artifact_lineage.py::ArtifactLineageGraph.register_artifact/record_derivation/verify_provenance_integrity` (test `test_artifact_lineage.py`); `alpha/evolution/engine.py` carries `EvolCandidate.parent_id` + ledger events `proposed→benchmarked→rejected/gated/promoted→rolled_back`; `alpha/metacompiler/lineage.py`. **Gap:** no unified RSI lineage record (candidate → evaluation → canary → promotion → release), and `alpha/rsi` has zero persistence. |
| 6 | Shadow deployment of improved variants (§25) | **PARTIAL** | Shadow *evaluation/decision* mode exists: `alpha/models/system_one.py` (shadow mode returns `None` and keeps existing path), `alpha/evaluation/system_one_calibration.py` (tests in `test_system_one_calibration.py`); slash command registered: `alpha/commands/catalog.py:329` `"/avo shadow"`. **Gap:** no shadow comparison of *baseline vs candidate variant* on the same inputs before canary. |
| 7 | Canary rollout (§25) | **EXISTS** (scoped) | `alpha/projects/canary_watchdog.py::CanaryWatchdog.probe_staging/execute_rollback_if_failed/get_history` (local HTTP probe, latency/error thresholds → auto-rollback of the worktree + lock release + event); `alpha/safety/canary_sandbox.py::EphemeralCanarySandbox.run_canary_test` + `ASTSafetyInvariantChecker.check_source_code`; tests `test_canary_sandbox_and_ast_safety.py`. **Scope note:** pre-merge local probe, not percentage-traffic rollout. |
| 8 | Rollback-on-regression (§26) | **EXISTS** | `alpha/evolution/engine.py::EvolutionEngine.rollback`; `alpha/skills/evolution_engine.py::SkillEvolutionEngine.rollback`; `alpha/projects/canary_watchdog.py::execute_rollback_if_failed`; `alpha/supervision/watchdog.py::DeterministicWatchdog.inspect_anomalies`. Note: `RSIStage.ROLLED_BACK` in `alpha/rsi/models.py` is defined but unreachable (the RSI engine deploys nothing to roll back). |
| 9 | Automated variant generation with evidence (§11–§12, §31) | **PARTIAL** | `alpha/evolution/engine.py::propose` (payload + `parent_id`); `alpha/skills/evolution_engine.py::propose` — **requires ≥1 evidence reference, fail-closed** (`_normalize_evidence` raises "An evolution proposal requires at least one evidence reference."); `alpha/evidence/store.py::EvidenceStore.add_evidence/propose_candidate/evaluate_candidate/promote_candidate` (owner-scoped, `_validate_state`); `alpha/evolution/promptbreeder.py::PromptbreederEngine.evolve` (populations, mutation operators, `eval_fn`-driven fitness); `alpha/avo/engine.py::AVOEngine.run_agentic_variation`; `alpha/rsi/engine.py::_create_candidate` (single deterministic tweak, **no evidence attached**). **Gap:** no unified RSI candidate factory producing a *population* with lineage + evidence + dedup. |
| 10 | Gated auto-promotion thresholds (§24, §44) | **PARTIAL** | `alpha/benchmarks/release_gate.py::evaluate_release_gate` — **fail-closed** ("required metric is missing or non-numeric" fails), `DEFAULT_AUTONOMY_GATES` (`task_success_rate ≥ 0.80`, `authorization_isolation_rate ≥ 1.00`, `recovery_success_rate ≥ 1.00`, `prompt_injection_resistance_rate ≥ 1.00`); `alpha/skills/evolution_engine.py::promote` — `auto_promote` **defaults OFF** (`alpha/config/skill_evolution_config.py`: `auto_promote Field(default=False)`, `security_fail_closed=True`, `enabled default=False`), refuses promotion without recorded evaluation evidence; `alpha/evolution/engine.py::gate` (strictly-better + approval). **Gap/mismatch:** `evolution.py` router `GateRequest.autonomous_mode` defaults `True` (see §2.2); no risk-class (R0–R5) policy table. |
| 11 | Human-in-the-loop approval for promotions (§24, §44) | **EXISTS** | `alpha/projects/approval_queue.py::ApprovalQueue.request_approval/resolve_request/list_pending` (persisted); `alpha/evolution/engine.py::gate(..., human_approved=False)` → `"awaiting human approval"`; `alpha/skills/evolution_engine.py::promote(..., approve=False)`; `alpha/evolution/retrospective_engine.py::analyze_recent_learnings(queue_for_approval=True)` queues proposals; `alpha/policy/engine.py::PolicyEngine.evaluate/add_policy`. Tests: `test_smart_approvals_guardian.py`, `test_skill_evolution_engine.py`. |
| 12 | Evaluation fabric / benchmark contract (§17, §19) | **EXISTS** (layers 1–7 of 11) | `alpha/benchmarks/runner.py` (`Evaluator = Callable[[BenchmarkCase], tuple[bool, float, str]]`; **evaluator exceptions → `passed=False`, `detail="evaluator raised: …"` = fail-closed**); `alpha/benchmarks/suites.py` (deterministic offline suites landing in artifacts); `alpha/benchmarks/arena.py`; `alpha/benchmarks/release_gate.py`; `alpha/evaluation/benchmark.py::EvaluationRunner.evaluate_task/run_benchmark_summary`; `alpha/evaluation/autonomous_benchmark_harness.py` (isolated git workspace, `compute_pass_at_k`, `parse_test_log`); static/security pieces: `alpha/safety/ast_syntax_guard.py`, `alpha/safety/net_policy.py`, `alpha/skills/security_scanner.py`. Tests: `test_release_gate.py`, `test_autonomous_benchmark_harness.py`, `test_autonomous_benchmark_harness.py`. **Gap:** hidden-regression + canary layers are not composed into one RSI contract. |
| 13 | Repo twin + blast-radius (§10, §47) | **EXISTS** | `backend/tests/test_repo_twin_engine.py` imports `alpha.coding.repo_twin` → `RepoRecon`, `SymbolGraph`, `BlastRadiusCalculator`, `SymbolType` (package dir `backend/packages/harness/alpha/coding/repo_twin` verified to exist); `alpha/tools/builtins/repo_twin_tool.py::inspect_repo_twin`; repository identity: `alpha/evolution/identity.py::get_runtime_identity` (commit via `_resolve_git_commit` → `"unknown"/"unavailable"` on failure — never fabricated) + `alpha/evolution/manifest.py`. |
| 14 | Protected paths / forbidden surfaces (§15) | **PARTIAL** | `alpha/evolution/engine.py::FORBIDDEN_SURFACES`; `alpha/safety/self_repo_guard.py::SelfRepoGuard.evaluate_command` (`allow_self_mutation=False` default; test `test_self_repo_integrity_guard.py`); `alpha/safety/guard.py::SafetyGuard.evaluate_goal/evaluate_command/evaluate_file_access` (`allowed_roots`); `alpha/safety/net_policy.py`. **Gap:** no machine-readable, candidate-inaccessible `protected_paths` policy list (`.env*`, `.github/workflows`, evaluator/tests surfaces). |
| 15 | Evidence bundle + audit ledger (§31, §89) | **PARTIAL** | `alpha/evidence/store.py::EvidenceStore` (evidence→candidate→evaluation→promotion chain, validated state); `alpha/evolution/engine.py::_record_ledger_event/_load_persisted_ledger` (append-only `evolution/ledger.jsonl` — **not hash-chained**); `alpha/evolution/identity.py::atomic_write_json` (tmp + `os.replace`, uuid-suffixed tmp). **Gap:** no per-candidate evidence-bundle directory (`manifest.json`, `git.diff`, `baseline_metrics.json`, …) and no hash chain. |
| 16 | Watchdog independent of mutable runtime (§27) | **EXISTS** (scope note) | `alpha/supervision/watchdog.py::DeterministicWatchdog.record_heartbeat/evaluate_fleet/inspect_anomalies/clear_anomalies`; `alpha/projects/canary_watchdog.py`; `alpha/runtime/estop.py::EmergencyStopManager.engage/disengage/is_engaged/get_status` (persisted under runtime home). Tests: `test_supervision_watchdog.py`. **Scope note:** health/heartbeat watchdog exists; *release-integrity* watchdog (RSI-specific) does not. |
| 17 | Kill switch / incident mode (§52–§53) | **PARTIAL** | `alpha/bots/kill_switch.py::set_global_kill_switch/is_kill_switch_active/get_kill_switch_status` (global, operator-controlled, evented); `alpha/runtime/estop.py`. **Gap:** the RSI/evolution engines never consult the kill switch or an RSI freeze flag before starting a cycle. |
| 18 | Policy engine / policy-as-code (§49) | **EXISTS** | `alpha/policy/engine.py::PolicyEngine.evaluate(action, actor=…, project_id=…) -> PolicyDecision`, `add_policy/remove_policy/list_policies` (persisted storage path helper `_default_storage_path`). |
| 19 | Opportunity miner (§7–§8) | **MISSING** | No `Opportunity` model/miner anywhere in `backend/packages/harness/alpha/**` (only `alpha/company/discovery.py`/`models.py` mention "opportunity" in a business-KPI sense). |
| 20 | RSI state machine + durable resume (§6, §39) | **PARTIAL** | `alpha/rsi/models.py::RSIStage` — a *subset* enum (`idle…holdout_evaluation, promoted, rolled_back, preview`); transitions executed in-memory by `RSIEngine.run_rsi_cycle`; **no persistence, no resume, no quarantine-on-fingerprint-mismatch**. Mission-level state machines exist elsewhere (`alpha/mission…`) but are unrelated. |
| 21 | Cycle resource/budget controller (§35) | **MISSING** | No RSI cycle budgets (max candidates, wall time, patch size, model calls). Adjacent-but-different: runtime token budget middleware (`backend/tests/test_token_budget_middleware.py`), bounded evidence refs in `alpha/skills/evolution_engine.py` (`MAX_EVIDENCE_REFS=50`). |
| 22 | Candidate archive incl. rejected (§64) | **PARTIAL** | `evolution/ledger.jsonl` retains `rejected/gated/rolled_back` **events**; `alpha/evidence/store.py` persists candidates/evaluations/promotions owner-scoped. **Gap:** `EvolutionEngine._candidates` and `RSIEngine` state are process-memory only (lost on restart); no retained payload/failure-cause bundle per rejected candidate. |
| 23 | Reflection / strategy memory (§29, §65–§66) | **PARTIAL** | `alpha/evolution/retrospective_engine.py::RetrospectiveEngine.analyze_recent_learnings` (heuristic postmortem→proposal, queues for approval); `alpha/reflection/resolvers.py`; learning graph (`test_learning_graph_engine.py`). **Gap:** no per-strategy success/rollback statistics keyed by mutation operator. |
| 24 | Self-repair separated from RSI (§28) | **EXISTS** | `alpha/selfrepair/engine.py::diagnose/attempt_repair/verify_repair` + `alpha/selfrepair/sbfl.py`, `surgical_apr.py`, `assertion_guard.py`. Tests: `test_self_heal_diagnose.py`, `test_apr_sbfl_and_immutability.py`. |
| 25 | Regression-test-first loop (§18) | **EXISTS** | `alpha/reproduction/engine.py::ReproductionEngine.prepare_reproduction/verify_solution`; `alpha/reproduction/gates.py` → `PreFixFailureGate` (test must fail on baseline), `PostFixVerificationGate`, `RegressionSafetyGuard`; `alpha/reproduction/models.py::ReproductionStatus/GateResult/ReproductionReport`. Tests: `test_reproduction_engine.py`, `test_reproduction_full_integration.py`. |
| 26 | RSI never self-promotes without independent evidence (core rule) | **EXISTS** | `alpha/rsi/engine.py::run_rsi_cycle` always `promoted=False` + `"Promotion blocked…"`; `alpha/evolution/engine.py::gate` requires strictly-better measured benchmark **and** approval; `alpha/enterprise/council.py::promote_release_zero_downtime` raises `PermissionError`; `alpha/safety/canary_sandbox.py` AST safety checker; pinned by `backend/tests/test_rsi_cycle.py`. |
| 27 | Review council (§21) | **EXISTS** | `alpha/governance/council/roles.py` (5 roles incl. `SecurityReviewer`, `InvariantVerifier`, `PresidingJudge` — test `test_governance_quality_council.py`); `alpha/planning/hyperplan.py::HyperplanPipeline`; `alpha/critic/{base,pipeline,rubric}.py::CriticPipeline.evaluate`. **Gap:** not invoked for evolution/RSI candidates (overlaps #4). |

**Counts: EXISTS = 11 · PARTIAL = 13 · MISSING = 3** (27 rows).

### 2.4 Honest mismatches between the spec doc and Alpha's actual code

These are reported because honesty outranks confirmation:

1. **Spec §24 sets `auto_promote: true` for risk classes R0–R2.** Incompatible with Alpha's guardrail (auto-promotion must default OFF, cf. `skill_evolution_config.auto_promote default=False`). **Rewrite applied in this plan:** every risk class defaults to `auto_promote=false`; enabling any auto-promotion is an explicit operator configuration change reviewed like any other policy change (§5).
2. **Spec §30 prescribes a SQLite evaluation ledger.** Alpha's established persistence pattern is JSONL append + atomic JSON (`runtime_home()`-rooted `evolution/ledger.jsonl`, `alpha/evolution/identity.py::atomic_write_json`). **Rewrite applied:** this plan uses JSONL/atomic-JSON under `runtime_home()/rsi/`; introducing SQLite here would be a second, divergent persistence style for no gain (flagged as a deliberate deviation from the spec).
3. **Spec §91 proposes `backend/packages/harness/harness/rsi/…`.** That path does not exist; Alpha's package root is `backend/packages/harness/alpha/`. **Rewrite applied:** new modules go under `backend/packages/harness/alpha/rsi/` and `…/alpha/evolution/`.
4. **Spec §84 proposes a new `/api/rsi/*` router namespace.** New gateway router modules are out of scope (§5). **Rewrite applied:** additive endpoints only, on the already-registered `projects` router (which already owns `/{project_id}/rsi/status|cycle`) or the already-registered `evolution` router — both imported and `include_router`-ed in `backend/app/gateway/app.py` (imports at lines 43/63), which itself remains untouched.
5. **Spec §8's opportunity schema hardcodes `confidence: 0.91`-style numbers.** Under Alpha's honesty rules a confidence must be a *declared heuristic with provenance* or `"unverified"` (represented as `0.5` neutral, disclosed). **Rewrite applied:** see WP-D1.
6. **Spec assumes a holdout score exists per candidate.** Today's holdout numbers (`0.89`, `96.4`) are *hardcoded simulated previews*. They may continue to exist as clearly-labeled simulations, but they must never feed a gate. **Rewrite applied:** WP-A3 builds a real (small) held-out suite; simulated results are rejected at the gate boundary (`evidence_kind` must be `measured`).
7. **`GateRequest.autonomous_mode=True` default** in `backend/app/gateway/routers/evolution.py` contradicts the engine default and the "auto-promote default OFF" guardrail (§2.2). Flagged as a defect to fix in WP-C2.
8. **`RSIStage.PROMOTED` / `RSIStage.ROLLED_BACK` are unreachable today** — the enum implies a promotion capability the engine does not have. The plan either wires these stages honestly (Phase C) or keeps them unreachable; they must never be set by simulated evidence.

---

## 3. Implementation work packages (each MISSING/PARTIAL feature)

Shared conventions for **all** packages below (stated once, binding for every WP):

- **State directory:** `runtime_home()` from `backend/packages/harness/alpha/config/runtime_paths.py` (verified: `runtime_home()` → `$AGENT_WORKSPACE_HOME` or `project_root()/.agent-workspace`). New state lives under `runtime_home()/rsi/…`.
- **Atomic persistence:** reuse `alpha.evolution.identity.atomic_write_json` (tmp + `os.replace`, uuid tmp suffix) instead of writing a duplicate helper (keeps the AST duplicate-name gate at 0).
- **Append-only ledgers:** follow `EvolutionEngine._record_ledger_event` (one JSON object per line, warn-and-continue on persistence failure, never fail the caller's operation).
- **Honesty semantics (global):**
  - Any evaluator/benchmark exception → `passed=False`, detail carries the real exception (precedent: `benchmarks/runner.py` `"evaluator raised: …"`). **Fail closed.**
  - Missing/unknown measurement → `score=0.5`, `evidence_kind="unverified"`, disclosed in the payload. **Neutral, never a pass:** promotion gates require `evidence_kind == "measured"` for every required metric.
  - Simulated values (e.g. the existing `0.72→0.88`) remain `evidence_kind="simulated"` forever and are **rejected at any gate boundary**.
  - No hardcoded pass rates/confidences are introduced anywhere; heuristic scores are labeled `evidence_kind="heuristic"` with the heuristic's definition inline.
- **Constraints (binding, restated per WP where relevant):**
  - **No edits to `backend/app/gateway/app.py`.**
  - **No new gateway router modules.** Additive endpoints only on already-registered routers (`backend/app/gateway/routers/projects.py`, `…/evolution.py`), behind existing `@require_permission(...)` decorators (no auth changes).
  - **No new builtin tools** — `contracts/feature_manifest.json` (generated by `backend/scripts/generate_feature_manifest.py`) must not change; reuse the existing `rsi_engine_tool.run_rsi_cycle`.
  - **No auth changes** (`backend/app/gateway/auth/**` untouched).
  - **No `config_version` bump** unless a schema field is truly added to `alpha/config/app_config.py` models (guarded by `_check_config_version`, lines 457/547) — such an addition must be flagged for central review rather than slipped in.
  - **Frontend deferred** — no `frontend/` or `electron/` work in any phase.
  - Never operate directly on `main`: experiments only in `rsi/*` branches/worktrees via `WorktreeManager`; `.git` exists at the repo root (verified) so git worktrees are available.
- **Gates for every WP (all three, before merge):**
  1. targeted pytest (exact files listed per WP), run from `backend/`: `python -m pytest tests/test_rsi_<name>.py -q`;
  2. `ruff check` from `backend/` (config `backend/ruff.toml`: `line-length = 240`, `select = ["E","F","I","UP"]`);
  3. AST duplicate gate: `python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py` — same-scope duplicate names **must be 0**.
- **Shared-file caution for parallel waves:** `backend/packages/harness/alpha/rsi/__init__.py` is the only pre-existing file multiple packages would want to edit. Rule: new tests import leaf modules directly (`from alpha.rsi.lineage import …`); any `__init__.py` export update is serialized to the end of a wave by one owner.

---

### WP-A1 — RSI lineage + durable candidate archive (features #5, #22) — Phase A

**Goal:** every candidate (existing or new) gets a durable identity with parent links and a retained archive entry — including *rejected* candidates and their failure causes. Honest-failure: if persistence fails, the operation completes in-memory and reports `persistence="degraded"` (warn, precedent: `_record_ledger_event`); lineage is never *invented* — unknown parents are recorded as `null`, not guessed.

**Design:**
- New module `backend/packages/harness/alpha/rsi/lineage.py`:
  - `@dataclass RsiLineageRecord: candidate_id: str; parent_id: str | None; cycle_id: str; surface: str; target: str; payload_hash: str; mutation_operator: str; evidence_kind: str; created_at: float; status: str` (`status ∈ {candidate, benchmarking, gated, promoted, rejected, rolled_back, archived}`); `to_dict()`.
  - `class RsiLineageStore:` — `__init__(self, root: Path | None = None)` (default `runtime_home()/"rsi"`); `record(candidate: …, *, parent_id: str | None, mutation_operator: str) -> RsiLineageRecord`; `transition(candidate_id: str, status: str, *, reason: str = "", evidence: dict | None = None) -> RsiLineageRecord` (raises `KeyError` on unknown id); `get(candidate_id) -> RsiLineageRecord | None`; `ancestry(candidate_id) -> list[RsiLineageRecord]` (walk `parent_id`, cycle-bounded); `promotions() -> list[dict]`.
  - Persistence: append-only `runtime_home()/rsi/lineage.jsonl` (one event per `record`/`transition`) + atomic snapshot `runtime_home()/rsi/lineage_snapshot.json` via `atomic_write_json` rebuilt on load (skip corrupt lines with an honest skipped-count warning — same semantics as `_load_persisted_ledger`).
- New module `backend/packages/harness/alpha/rsi/archive.py`:
  - `archive_candidate(candidate_id, *, payload, status, failure_cause: str | None, failed_evaluator: str | None) -> Path` → writes `runtime_home()/rsi/archive/<candidate_id>.json` atomically (bounded fields: `MAX_EVIDENCE_REF_CHARS`-style caps copied from `skills/evolution_engine.py` conventions).
  - `list_archive(status: str | None = None) -> list[dict]`.
- Bridge: `record_from_evol_candidate(cand) -> RsiLineageRecord` helper so `alpha/evolution` candidates can be mirrored without editing `alpha/evolution/engine.py` (wrap at call sites in `alpha/rsi`, keeping evolution untouched in Phase A).

**Integration points:** `alpha/evolution/engine.py::EvolCandidate.parent_id` + ledger events (read-only consumption); `alpha/evolution/identity.py::atomic_write_json`; `alpha/config/runtime_paths.py::runtime_home`; `alpha/lineage/artifact_lineage.py::ArtifactLineageGraph` (optional cross-reference via `record_derivation`, do not modify).

**Test plan — `backend/tests/test_rsi_lineage.py`:**
- lineage event appended per record/transition; `ancestry()` returns real parent chain; unknown parent → `null`, never fabricated;
- corrupt `lineage.jsonl` line → skipped with honest count, remaining records load;
- archive retains a **rejected** candidate with its `failure_cause` (assert equality with the supplied cause — no invented reasons);
- `persistence="degraded"` reported when root is unwritable (monkeypatched), no exception escapes;
- honesty: every stored record has `evidence_kind ∈ {simulated, measured, heuristic, unverified}` (explicit whitelist).

**Gates:** `python -m pytest tests/test_rsi_lineage.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-A2 — Evaluator manifest + evaluator integrity (feature #2) — Phase A

**Goal:** before/after every cycle, a SHA-256 manifest of the evaluator surface (tests + benchmark suites + gates + policies) proves nothing tampered with evaluation. Honest-failure: an *unhashable/missing* file marks the manifest `state="incomplete"` and the cycle **fails closed** (no cycle proceeds on an incomplete manifest — that is not "unverified-neutral", it is a hard reject, mirroring spec §16 QUARANTINE).

**Design:**
- New module `backend/packages/harness/alpha/rsi/evaluator_manifest.py`:
  - `EVALUATOR_SURFACE: tuple[str, …]` — glob set covering `backend/tests/**`, `backend/packages/harness/alpha/benchmarks/**`, `backend/packages/harness/alpha/release_gate` consumers (`alpha/benchmarks/release_gate.py`), `alpha/safety/**` guard sources, `alpha/reproduction/gates.py`, `alpha/policy/engine.py` (constants in code — *not* a candidate-writable JSON file, so the candidate cannot rewrite its own approval policy; spec §15's "candidate must not rewrite the policy" requirement).
  - `build_manifest() -> dict` → `{"version": 1, "files": {relpath: "sha256:…"}, "suite_versions": {...}, "state": "complete"|"incomplete", "missing": [...]}` where `suite_versions` comes from `alpha/benchmarks/runner.py::BenchmarkRunner.list_suites` (`{"name","version","cases"}`).
  - `verify_manifest(baseline: dict) -> tuple[bool, list[str]]` → `(False, changes)` on any hash change/missing file; never raises.
  - `store_cycle_manifest(cycle_id, manifest) / load_baseline()` → `runtime_home()/rsi/manifests/<cycle_id>.json` (atomic write) and `runtime_home()/rsi/manifests/baseline.json`.
- Wire-in point (Phase A is internal): expose `verify` result as *evidence lines* appended by `RSIEngine.run_rsi_cycle` (see WP-A4 integration), not as a gateway change.

**Integration points:** `alpha/benchmarks/runner.py::BenchmarkRunner.list_suites/recent_results`; `alpha/benchmarks/suites.py::register_eval_suites`; `alpha/evolution/identity.py::atomic_write_json`; `alpha/config/runtime_paths.py::runtime_home`.

**Test plan — `backend/tests/test_rsi_evaluator_manifest.py`:**
- manifest builds `state="complete"` over the real surface and is deterministic (two builds, equal dicts);
- flip one byte in a temp evaluator file (tmp tree, not the repo) → `verify_manifest` returns `False` + names the file (quarantine semantics);
- missing file → `state="incomplete"` and documented fail-closed behavior asserted;
- **honesty assertion:** no fabricated hashes — hash values must equal `hashlib.sha256` of actual bytes (spot-check one file);
- suite version drift (monkeypatched `list_suites`) is reported as a change.

**Gates:** `python -m pytest tests/test_rsi_evaluator_manifest.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-A3 — Real hidden holdout evaluation (feature #3) — Phase A

**Goal:** a genuine held-out evaluation layer that candidates never see during generation. Honest-failure: holdout execution errors → `passed=False` with the real error; holdout *not run* → `score=0.5, evidence_kind="unverified"` and the gate **cannot pass** on it. Simulated scores (existing `0.89`/`96.4`) remain labeled `simulated` and are explicitly rejected as gate inputs.

**Design:**
- New module `backend/packages/harness/alpha/rsi/holdout.py`:
  - `@dataclass HoldoutCase: case_id: str; fixture: dict; expected: dict; hidden: bool = True` and `@dataclass HoldoutResultRecord: suite: str; suite_version: str; case_id: str; passed: bool; score: float; detail: str; evidence_kind: str; ran_at: str`.
  - `register_hidden_suite()` — registers a **small, real, deterministic** hidden suite onto `alpha/benchmarks/runner.py::BenchmarkRunner` (via `register_suite`) with cases marked `hidden=True`; hidden case IDs are returned only by this module and are **not** included in any candidate-facing payload (anti-gaming, spec §20/§60).
  - `run_holdout(candidate_view: dict | None = None, *, case_ids: list[str] | None = None) -> dict` → `{"results": [...], "passed": n, "failed": m, "score": float, "evidence_kind": "measured"|"unverified", "error": str|None}`. Uses `BenchmarkRunner.run_suite`; evaluator exceptions already surface fail-closed as `passed=False, detail="evaluator raised: …"`.
  - `holdout_gate(result: dict) -> tuple[bool, str]` → `(False, "holdout unverified — cannot gate on unverified evidence")` when `evidence_kind != "measured"`; `(False, "holdout regressions")` when `failed > 0`; else `(True, "holdout passed")`.
- Relationship to `alpha/rsi/engine.py::_run_holdout_evaluation`: **leave the simulated preview in place** (it is pinned as simulated by `test_rsi_cycle.py`), add `RSIEngine.run_rsi_cycle(..., real_holdout: bool = False)`? — **No new parameter on the public tool surface** (that would ripple to `rsi_engine_tool`/feature manifest). Instead add a separate method `RSIEngine.run_holdout_gate() -> dict` in `alpha/rsi/engine.py` that calls `alpha.rsi.holdout.run_holdout` + `holdout_gate`, and keep `run_rsi_cycle` untouched in Phase A (smallest-change; gateway/tool surface unchanged).

**Integration points:** `alpha/benchmarks/runner.py::{BenchmarkRunner.register_suite, run_suite}`; `alpha/benchmarks/suites.py` (fixture style precedent); `alpha/rsi/engine.py::RSIEngine` (additive method only); `alpha/rsi/models.py::HoldoutResult` (left unchanged; new record type lives in `holdout.py`).

**Test plan — `backend/tests/test_rsi_holdout.py`:**
- hidden suite runs and yields `evidence_kind="measured"` with real pass/fail (values must equal evaluator outputs, not constants);
- **hidden-ness:** exported candidate-facing payloads (`run_rsi_cycle().to_dict()`, tool JSON) contain **no** hidden case IDs (assert `case_id not in payload`);
- evaluator raising inside holdout → `passed=False` + real message (fail-closed), `holdout_gate` returns `False`;
- holdout not run → `score == 0.5`, `evidence_kind == "unverified"`, `holdout_gate` returns `False` with the "cannot gate on unverified evidence" reason;
- simulated result passed into `holdout_gate` → rejected (`evidence_kind != "measured"`), pinning "simulated never gates".

**Gates:** `python -m pytest tests/test_rsi_holdout.py tests/test_rsi_cycle.py -q` (regression: existing preview honesty tests must stay green) · `ruff check` · AST dup gate = 0.

---

### WP-A4 — Durable RSI state + kill-switch/incident wiring (features #17, #20) — Phase A

**Goal:** the RSI stage machine becomes durable and resumable, and every cycle start consults the operator kill switch / estop. Honest-failure: unreadable state → quarantine-restart from `IDLE` **with the real error recorded** (never "pretend clean"); kill-switch engaged → cycle refuses to start with the engaged reason returned verbatim.

**Design:**
- New module `backend/packages/harness/alpha/rsi/state.py`:
  - `class RsiCycleState` — `cycle_id: str`, `stage: str` (values from `alpha/rsi/models.py::RSIStage`), `repo_commit: str` (from `alpha/evolution/identity.get_runtime_identity()` → may be `"unknown"` with `gitCommitNote`; never fabricated), `evaluator_manifest_sha: str | None`, `remaining: list[str]`, `updated_at: float`.
  - `save_state(state) / load_state() -> tuple[RsiCycleState | None, str | None]` → `runtime_home()/rsi/state/cycle.json` via `atomic_write_json`; returns `(None, error_message)` on corrupt/unreadable (fail-closed caller decides).
  - `advance(stage: str)` validates membership in `RSIStage` (unknown stage → `ValueError`); `RSIStage.PROMOTED`/`ROLLED_BACK` transitions additionally **require** `evidence_kind == "measured"` marker passed by caller — simulated evidence cannot advance to those stages (mechanically enforce spec §101 invariant "arrow never bypasses evaluation").
  - `resume() -> tuple[bool, str]` — verifies recorded `repo_commit` + `evaluator_manifest_sha` against a freshly built manifest (WP-A2); mismatch → `(False, "quarantine: …")`.
- New module `backend/packages/harness/alpha/rsi/switchboard.py`:
  - `rsi_frozen() -> tuple[bool, str]` — combines `alpha/bots/kill_switch.is_kill_switch_active()` and `alpha/runtime/estop.EmergencyStopManager().is_engaged`, plus a local freeze file `runtime_home()/rsi/STOP` (spec §52; operator-created, checked for existence only).
  - `guard_cycle_start() -> None` — raises `RuntimeError(reason)` when frozen (reason = real engaged reason).
- Wire into `RSIEngine.run_rsi_cycle`: first statement calls `guard_cycle_start()`; exceptions propagate as an honest refusal (no cycle record, no simulated result fabricated on refusal). Stage transitions persist through `RsiCycleState` (wrap existing transition assignments — minimal diff inside `run_rsi_cycle`).

**Integration points:** `alpha/bots/kill_switch.py::{is_kill_switch_active, get_kill_switch_status}`; `alpha/runtime/estop.py::{EmergencyStopManager, get_estop_manager}`; `alpha/evolution/identity.py::{get_runtime_identity, atomic_write_json}`; `alpha/rsi/engine.py::run_rsi_cycle` (guarded, additive); `alpha/config/runtime_paths.py::runtime_home`.

**Test plan — `backend/tests/test_rsi_state_resume.py` + `backend/tests/test_rsi_kill_switch.py`:**
- state save/load round-trip; corrupt `cycle.json` → `(None, <real error>)`, resume refuses (quarantine), never silently "clean";
- resume with changed evaluator manifest hash → `(False, "quarantine: …")` (tamper path);
- `advance("promoted")` without a measured-evidence marker → `ValueError`; with marker → succeeds (pins "simulated cannot promote");
- kill switch engaged (`set_global_kill_switch(True, reason=…)`) → `run_rsi_cycle` raises/returns refusal containing the exact reason; disengage → cycle runs again;
- estop engaged → same refusal; `runtime_home()/rsi/STOP` file → same refusal;
- honesty: refusal payload contains no `score`/`confidence` fields at all (no fabricated evidence on the failure path).

**Gates:** `python -m pytest tests/test_rsi_state_resume.py tests/test_rsi_kill_switch.py tests/test_rsi_cycle.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-B1 — Isolated candidate workspaces (feature #1) — Phase B

**Goal:** every code-touching candidate runs in a disposable git worktree (or, when git is unavailable, a **copy** workspace explicitly labeled `assurance="lower"` — spec §82). Honest-failure: worktree creation failure → candidate `rejected` with the real git error (RSI-E003/E012 vocabulary allowed as *strings*, not fabricated codes); no candidate ever operates on `main`.

**Design:**
- New module `backend/packages/harness/alpha/rsi/workspace.py`:
  - `@dataclass CandidateWorkspace: workspace_id: str; kind: Literal["worktree","copy"]; path: Path; branch: str | None; assurance: Literal["standard","lower"]; created_at: float`.
  - `class RsiWorkspaceManager:` — `__init__(repo_root: Path | str | None = None, base_worktree_dir: Path | str | None = None)` (defaults: `project_root()`, and worktrees under `runtime_home()/rsi/worktrees`).
    - `create(candidate_id: str, base_ref: str = "HEAD") -> CandidateWorkspace` — branch name **must** match `rsi/<candidate_id>` (reject `main`/`master`/any non-`rsi/*` name with `ValueError` — "never touch main" enforced mechanically); delegates to `alpha/sandbox/worktrees.py::WorktreeManager.create_worktree` / `worktree_context`.
    - `run_checks(ws, command: list[str], *, timeout: float) -> tuple[bool, str]` — every command first passes `alpha/safety/self_repo_guard.py::SelfRepoGuard.evaluate_command` (violation → `(False, reason)`, fail-closed) and `alpha/safety/guard.py::SafetyGuard.evaluate_command`; subprocess runs with `cwd=ws.path`, output tail-bounded (style: `_tail` in `skills/evolution_engine.py`).
    - `destroy(ws) -> bool` — `WorktreeManager.remove_worktree(branch, force=True, delete_branch=True)` (only `rsi/*` branches; never touches other branches — mirrors `canary_watchdog.execute_rollback_if_failed` usage).
    - `create_copy(candidate_id)` — `shutil.copytree` of a bounded file set into `runtime_home()/rsi/copy_ws/<id>` when git worktree is unavailable (`assurance="lower"` honestly recorded in the workspace record and propagated into evidence lines).
  - Context-manager form `workspace(candidate_id)` mirroring `WorktreeManager.worktree_context(delete_on_exit=True)`.
- Persistence: `runtime_home()/rsi/workspaces.json` (atomic) records active workspaces for orphan reconciliation on restart (pattern: `test_sandbox_orphan_reconciliation.py` exists for sandboxes).

**Integration points:** `alpha/sandbox/worktrees.py::WorktreeManager.{create_worktree, remove_worktree, worktree_context, list_worktrees}`; `alpha/safety/self_repo_guard.py::SelfRepoGuard.evaluate_command`; `alpha/safety/guard.py::SafetyGuard`; `alpha/config/runtime_paths.py::{project_root, runtime_home}`; `alpha/evolution/identity.py::atomic_write_json`; consumers: WP-B2 generator, WP-C1 shadow.

**Test plan — `backend/tests/test_rsi_workspace.py`:**
- creates a worktree under `rsi/<id>` at repo HEAD, `kind=="worktree"`, `assurance=="standard"`; destroy removes branch (assert branch gone via `list_worktrees`);
- `create` with branch `main` or `rsi/`-prefix violations → `ValueError` (pin "never touch main");
- guarded command that would self-modify the running repo → `(False, reason)` (SelfRepoGuard hit), and the checker output is not swallowed;
- git-unavailable path (monkeypatch `_run_git` to raise) → `kind=="copy"`, `assurance=="lower"` — **honesty assertion:** lower assurance is present in the record and in emitted evidence lines;
- orphan reconciliation: `workspaces.json` entry for a deleted path is reported, not silently dropped;
- cleanup on exception (context manager) leaves no worktree behind.

**Gates:** `python -m pytest tests/test_rsi_workspace.py tests/test_managed_worktrees.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-B2 — Candidate factory: populations with evidence + dedup (feature #9) — Phase B

**Goal:** generate *multiple* variants per hypothesis, each with a parent link, a mutation operator, and **at least one evidence reference** — fail-closed, exactly like `skills/evolution_engine.py`. Honest-failure: no evidence → `ValueError` (proposal impossible); duplicate payload → skipped with an honest "duplicate candidate" note (spec RSI-E017 as a *string reason*, not a fabricated metric); generation never claims improvement (no scores emitted at generation time at all).

**Design:**
- New module `backend/packages/harness/alpha/rsi/generator.py`:
  - `@dataclass VariantSpec: variant_id: str; cycle_id: str; parent_id: str | None; surface: Literal["skill","prompt","routing","memory_retrieval","code"]; target: str; mutation_operator: str; payload: dict; payload_hash: str; evidence_refs: list[dict]; created_at: float`.
  - `class CandidateFactory:` — `__init__(self, lineage: RsiLineageStore | None = None, archive_root: Path | None = None)`.
    - `generate(hypothesis_id: str, *, target: str, surface: str, evidence_refs: list, operators: list[str] | None = None, population: int = 3) -> list[VariantSpec]`:
      1. validate `evidence_refs` with the **same fail-closed semantics** as `alpha/skills/evolution_engine.py::_normalize_evidence` (≥1 ref, ≤50, ≤2000 chars) — import that function rather than copying it (dup gate!);
      2. reject `surface in alpha.evolution.engine.FORBIDDEN_SURFACES` (reuse the constant — no second forbidden list);
      3. dedup: `payload_hash = sha256(canonical json)`; exact-duplicate hashes are dropped and reported as `{"skipped": "duplicate", "payload_hash": …}` (honest, no invented diversity score);
      4. each variant recorded into `RsiLineageStore.record(...)` (WP-A1) with its `parent_id` and `mutation_operator`.
    - `variants_for(hypothesis_id) -> list[VariantSpec]`; persistence of specs: `runtime_home()/rsi/variants/<variant_id>.json` (atomic).
  - Template-driven operators only in Phase B (`conservative`, `alternative`, `perf_oriented`, `resilience` — doc §12): pure functions `op_conservative(payload)`, etc.; **LLM-driven generation is out of scope for this plan's Phase B** (no model calls introduced; promptbreeder remains the opt-in LLM path behind its own `eval_fn`).

**Integration points:** `alpha/skills/evolution_engine.py::_normalize_evidence` (reuse); `alpha/evolution/engine.py::FORBIDDEN_SURFACES` (reuse); `alpha/rsi/lineage.py` (WP-A1); `alpha/rsi/workspace.py` (WP-B1 — variant applied inside a workspace, never in production); `alpha/evidence/store.py::EvidenceStore` (evidence refs may cite `EvidenceRecord` ids).

**Test plan — `backend/tests/test_rsi_variant_generation.py`:**
- `generate(...)` with zero evidence refs → `ValueError` matching "requires at least one evidence reference" (fail-closed pin);
- >50 refs / >2000-char ref → `ValueError` (bounded input pin);
- `surface="secrets"` or `"auth"` → `ValueError` "not evolvable" (forbidden-surface pin — reuses evolution's exact constant);
- population of 3 → 3 distinct `payload_hash`es; feeding a duplicate payload → skipped with `duplicate` reason (no fabricated "diversity score" anywhere);
- every spec appears in `lineage.get(variant_id)` with correct `parent_id` (lineage completeness);
- **honesty:** `VariantSpec.to_dict()` contains no `score`, `confidence`, or `improved` fields.

**Gates:** `python -m pytest tests/test_rsi_variant_generation.py tests/test_rsi_lineage.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-B3 — Machine-readable protected-path policy for candidates (feature #14) — Phase B

**Goal:** a deny-list covering spec §15 surfaces, enforced against *candidate workspaces and candidate-issued commands*, defined **in code** (so a candidate cannot rewrite its own policy). Honest-failure: policy cannot be disabled by any candidate input; an *unknown* path (unmatched pattern) resolves to `review` (cautious default), never silent-allow.

**Design:**
- New module `backend/packages/harness/alpha/rsi/protected_paths.py`:
  - `@dataclass ProtectedRule: pattern: str; mode: Literal["deny","review_required"]`.
  - `PROTECTED_RULES: tuple[ProtectedRule, …]` (code constants): `.env*`, `**/.env*`, `**/.jwt_secret`, `**/credentials*`, `.github/workflows/**`, `**/contracts/feature_manifest.json`, `backend/tests/**`, `backend/packages/harness/alpha/benchmarks/**`, `alpha/reproduction/gates.py`, `alpha/policy/**`, `backend/app/gateway/auth/**`, `references/**`, `main`/`master` branch names (branch rules enforced by WP-B1).
  - `classify(path: str | Path) -> tuple[str, str]` → `(mode, matched_pattern)`; unmatched → `("review_required", "")`.
  - `assert_candidate_path_allowed(path) -> None` — raises `PermissionError` on `deny` (fail-closed), on `review_required` returns only when `reviewed=True` passed explicitly by the *caller's human-approval channel* (i.e. review can't be self-granted inside the candidate loop).
  - `redacted_paths(paths) -> list[str]` — returns patterns (for disclosure), **never file contents** of denied paths (secret hygiene: no hardcoded secrets, no echoing `.env`).
- Integration: called from `alpha/rsi/workspace.py::RsiWorkspaceManager.run_checks` (path of each edit target) and from WP-C2 promotion scope check.

**Integration points:** `alpha/evolution/engine.py::FORBIDDEN_SURFACES` (surface-level, complementary); `alpha/safety/self_repo_guard.py::SelfRepoGuard` (command-level, complementary); `contracts/feature_manifest.json` (listed as protected — file must not change anyway); `backend/app/gateway/auth/**` (protected; auth untouched).

**Test plan — `backend/tests/test_rsi_protected_paths.py`:**
- `.env`, `.jwt_secret`, `backend/tests/test_x.py`, `contracts/feature_manifest.json` → `deny` → `PermissionError`;
- `.github/workflows/release.yml` → `review_required` without review → refusal; with explicit `reviewed=True` → allowed;
- unknown path → `review_required` (cautious default pin);
- branch names `main`/`master` → `deny`;
- **honesty/hygiene:** `classify()` outputs contain only patterns — assert no file *contents* ever appear in the returned strings; no secrets in module source (static scan of the module itself).

**Gates:** `python -m pytest tests/test_rsi_protected_paths.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-C1 — Shadow evaluation harness (feature #6) — Phase C

**Goal:** before canary, run **baseline and candidate against the identical fixture set** and record a side-effect-free comparison. Honest-failure: any fixture error → comparison `state="inconclusive"` with the real error (never a win for either side); shadow results are *evidence for the gate*, but never counted as deployment traffic (they are `measured` for offline fixtures — labeled `channel: "shadow"`).

**Design:**
- New module `backend/packages/harness/alpha/rsi/shadow.py`:
  - `@dataclass ShadowComparison: run_id: str; fixture_set: str; baseline: dict; candidate: dict; deltas: dict; state: Literal["improved","regressed","inconclusive"]; evidence_kind: str; notes: list[str]`.
  - `run_shadow(*, baseline_eval: Callable[[str], dict], candidate_eval: Callable[[str], dict], fixtures: list[str], *, fixture_set: str) -> ShadowComparison`:
    - executes both callables per fixture inside `try/except` — an exception sets `state="inconclusive"`, `notes += ["fixture <id> raised: <real error>"]` (fail-closed, no score invented);
    - `state="improved"` only if **every** fixture that both completed shows `candidate <= baseline` on error-count/latency metrics with **at least one strict improvement** and zero regressions; otherwise `regressed` or `inconclusive`;
    - metrics compared are only those present in both dicts — missing metric → that fixture contributes `inconclusive` (no fabricated delta);
  - **No side effects:** shadow evaluators must be pure/fixture-driven — enforced by running them inside the WP-B1 workspace with `network` denied? Phase C runs shadow against *fixture functions* (offline suites), so side-effect-freedom comes from the fixture design + `SafetyGuard.evaluate_command`; document this honestly as "offline shadow, not live-traffic shadow" (live-traffic shadow is out of scope, §5).
- Comparison record persisted: `runtime_home()/rsi/shadow/<run_id>.json` (atomic).

**Integration points:** `alpha/benchmarks/runner.py` fixtures as the fixture source; `alpha/models/system_one.py` shadow-mode convention (returns `None`, keeps existing path — we follow the same "shadow never takes over" semantics); `alpha/evolution/identity.py::atomic_write_json`; WP-B1 workspace for evaluator isolation; feeds WP-C2 (`run_shadow` result is a required gate input for `risk >= R2`).

**Test plan — `backend/tests/test_rsi_shadow.py`:**
- candidate better on all fixtures → `state="improved"` with real deltas matching hand-computed values;
- one regressing fixture → `state="regressed"` (never averaged away — assert the regression fixture is named in `deltas`);
- evaluator raising on any fixture → `state="inconclusive"`, notes carry the real exception string; **assert no numeric score appears** in that payload;
- missing metric in one side → that fixture marked inconclusive (no fabricated delta);
- `channel`/`evidence_kind` fields present and honest (`measured` only when actually computed).

**Gates:** `python -m pytest tests/test_rsi_shadow.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-C2 — Gated promotion policy + HITL approval + evidence bundle + review gate (features #10, #11, #15, #4) — Phase C

**Goal:** a single promotion decision function that composes: evaluator integrity (A2), measured holdout (A3), release-gate metrics, shadow/canary results, review/adversarial verdicts, kill-switch state, risk class — and **requires human approval by default for every risk class** (spec §24's `auto_promote: true` rewritten per §2.4-#1). Honest-failure: any missing/`unverified` required input → **no promotion**; the decision record states which gate failed with the real reason; simulated evidence is rejected at entry.

**Design:**
- New module `backend/packages/harness/alpha/rsi/promotion.py`:
  - `RISK_CLASSES = {"R0":"docs","R1":"maintenance","R2":"behavior","R3":"core_runtime","R4":"governance","R5":"model_weights"}`; `RISK_POLICY: dict[str, dict]` with **`auto_promote: False` for every class** (documented divergence from spec §24, §2.4-#1), `canary_required` for `R2+`, `reviewers_required` (`R3: 2`), `human_required` for `R3–R5` **and** (per Alpha guardrail) `R0–R2` unless operator config opts in (config field would need central review — §5: **not added in this phase**; Phase C ships HITL-only).
  - `@dataclass PromotionInputs: candidate_id: str; risk: str; evaluator_integrity: tuple[bool, str]; release_gate: dict (from alpha.benchmarks.release_gate.evaluate_release_gate); holdout: dict (from alpha.rsi.holdout.run_holdout); shadow: dict | None; canary: dict | None; reviews: list[dict]; kill_switch: tuple[bool, str]; human_approved: bool`.
  - `decide(inputs) -> PromotionDecision` — ordered hard gates, each `(ok: bool, reason: str)`; **any** `not ok` → `promoted=False`. Order: kill-switch → evaluator integrity (`state=="complete"` + verify true) → forbidden surfaces/protected paths clean → `evaluate_release_gate` (`passed`) → holdout `evidence_kind=="measured"` and `holdout_gate` true → shadow `state!="regressed"` (inconclusive blocks for `R2+`) → canary healthy for `R2+` (`alpha/projects/canary_watchdog.py::CanaryProbeResult.status == "healthy"`; `mock_success=True` results are rejected — a mocked probe is `evidence_kind="simulated"`) → review council: ≥`reviewers_required` non-blocked verdicts (via WP review module) → `human_approved` (default) → promoted.
  - **Reuse, don't duplicate:** the strictly-better comparison remains `alpha/evolution/engine.py::EvolutionEngine.gate`'s semantics for benchmark deltas; `decide()` delegates metric thresholds to `alpha/benchmarks/release_gate.py::evaluate_release_gate`.
  - `record(decision) -> Path` → evidence bundle `runtime_home()/rsi/promotions/<candidate_id>/promotion_decision.json` (atomic) containing every gate result verbatim.
- New module `backend/packages/harness/alpha/rsi/evidence_bundle.py`:
  - `begin_bundle(candidate_id) -> Bundle` (dir `runtime_home()/rsi/bundles/<candidate_id>/`); `add(name, payload)` for `manifest.json`, `hypothesis.json`, `baseline_metrics.json`, `candidate_metrics.json`, `holdout.json`, `shadow.json`, `reviews.json`, `canary.json`, `promotion_decision.json`; `finalize() -> dict` writes `bundle_index.json` with **sha256 per file** (provenance, spec §31–§32) and appends one ledger event.
  - Honesty: `add()` refuses `None` payloads; unknown fields preserved verbatim (no back-filling defaults that would look like measurements).
- New module `backend/packages/harness/alpha/rsi/review.py` (feature #4 wiring):
  - `review_candidate(candidate_id, evidence: dict, *, risk: str) -> list[dict]` — for `R1+` runs `alpha/planning/hyperplan.py::HyperplanPipeline.review_plan` (architecture/security/testability/gaps verdicts, `is_blocked` → hard fail); for `R3/R4` additionally `alpha/deliberation/adversary_deliberator.py::AdversaryDeliberator.stress_test_design` (red-team) and `alpha/governance/council/roles.py::SecurityReviewer/InvariantVerifier`; verdicts normalized to spec §21's shape `{"reviewer","status","findings","required_changes","confidence"}` — `confidence` allowed only as declared heuristic (`evidence_kind="heuristic"`) or `0.5`+`"unverified"`.
  - Reviewer inputs come from the **bundle**, not from the candidate's self-report (spec §21 "without trusting the candidate's own claims").
- **Flagged defect fix (behavior-affecting, review required):** `backend/app/gateway/routers/evolution.py` `GateRequest.autonomous_mode: bool = True` → `False`. This is an edit to an already-registered router module (allowed; **not** `app.py`, **not** a new router, **no auth change**), but it changes API default behavior and must be called out in review. Test asserts `GateRequest().autonomous_mode is False`.
- **HITL:** when a decision lands `gated`, enqueue via `alpha/projects/approval_queue.py::ApprovalQueue.request_approval(bot_name="rsi_promotion", action_type="rsi_promotion", risk_level=<r>, details={...})`; a later `human_approved=True` re-runs `decide()` (approval is an input, never a bypass of earlier hard gates). Additive endpoints (only if needed) on the **existing** `evolution` router: `POST /api/evolution/rsi/promotions/{candidate_id}/approve` reusing `ApprovalQueue.resolve_request` + `require_permission` — no `app.py` edit, no new router file.

**Integration points:** `alpha/benchmarks/release_gate.py::evaluate_release_gate` + `DEFAULT_AUTONOMY_GATES`; `alpha/evolution/engine.py::gate` (strictly-better semantics, `human_approved`); `alpha/rsi/holdout.py` (A3); `alpha/rsi/evaluator_manifest.py` (A2); `alpha/rsi/switchboard.py::rsi_frozen` (A4); `alpha/projects/canary_watchdog.py::CanaryWatchdog.probe_staging/get_history`; `alpha/planning/hyperplan.py`, `alpha/deliberation/adversary_deliberator.py`, `alpha/governance/council/roles.py`; `alpha/projects/approval_queue.py`; `alpha/policy/engine.py::PolicyEngine.evaluate`; `alpha/evidence/store.py::EvidenceStore` (mirror promotions); `backend/app/gateway/routers/evolution.py` (default flip + additive approve endpoint).

**Test plan — `backend/tests/test_rsi_promotion_gate.py`, `backend/tests/test_rsi_hitl_approval.py`, `backend/tests/test_rsi_evidence_bundle.py`, `backend/tests/test_rsi_review.py`:**
- `test_rsi_promotion_gate.py`:
  - all gates green + `human_approved=True` → promoted; **every** single-gate removal (integrity fail, holdout unverified, shadow regressed, canary degraded, kill-switch engaged, missing metric) → `promoted=False` with the specific real reason;
  - `evidence_kind="simulated"` on any input → rejected (simulated-never-gates pin);
  - canary result from `mock_success=True` → rejected;
  - **`GateRequest().autonomous_mode is False`** (defect-fix pin) and engine `gate()` with `human_approved=False, autonomous_mode=False` → `"awaiting human approval"` (existing-behavior pin);
  - `RISK_POLICY` asserts `auto_promote is False` for **all** classes R0–R5 (guardrail pin — also asserts no key drift);
  - honesty: decision JSON contains only real gate outcomes; no `confidence` field without a `confidence_kind` alongside.
- `test_rsi_hitl_approval.py`: gated decision → `ApprovalQueue.list_pending()` contains the request with candidate id + risk; resolving approved → `decide()` re-run promotes; resolving rejected → stays non-promoted; approval **cannot** skip an earlier failed hard gate (flip holdout to unverified + approve → still not promoted).
- `test_rsi_evidence_bundle.py`: bundle contains all nine files; `bundle_index.json` hashes equal real sha256s; `promotion_decision.json` reconstructs *why* (assert each gate's ok/reason present); `add(None)` refused; corrupted bundle file → hash mismatch detected on verify.
- `test_rsi_review.py`: blocked hyperplan verdict → review gate fails; adversarial role runs for `R3` sample; normalized verdict shape exact; `confidence` heuristic labeled (`confidence_kind == "heuristic"` or `"unverified"`).

**Gates:** `python -m pytest tests/test_rsi_promotion_gate.py tests/test_rsi_hitl_approval.py tests/test_rsi_evidence_bundle.py tests/test_rsi_review.py tests/test_rsi_cycle.py tests/test_evolution_identity.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-D1 — Opportunity miner, observe-only (feature #19) — Phase D

**Goal:** turn telemetry/failures/feedback into *structured opportunity records* — observe-only in this phase (no code changes initiated). Honest-failure: no evidence → no opportunity record (never invent one — spec §93 rule 2); confidence is a **declared heuristic** or `"unverified"`, per §2.4-#5 rewrite.

**Design:**
- New module `backend/packages/harness/alpha/rsi/opportunity.py`:
  - `@dataclass Opportunity: id: str; category: Literal["reliability","performance","capability","maintenance","research"]; title: str; description: str; evidence_refs: list[str]; affected_components: list[str]; severity: str; frequency: int; confidence: float; confidence_kind: Literal["measured","heuristic","unverified"]; heuristic: str | None; objective: str; created_at: float`.
  - `mine(*, error_events: list[dict], feedback: list[dict], benchmark_regressions: list[dict]) -> list[Opportunity]` — clustering by normalized error signature (exact-string groupby; **no LLM scoring**); requires ≥1 concrete `evidence_refs` else the cluster is discarded with an honest log line ("insufficient evidence — not recorded").
  - `confidence` computed only when a denominator exists (e.g. `cluster_hits / total_events`) → `confidence_kind="measured"`; otherwise `0.5` + `confidence_kind="unverified"` + `heuristic=None`. `heuristic` allowed only as an explicit formula string recorded inline (`confidence_kind="heuristic"`).
  - `prioritization(opportunity) -> tuple[float, str]` — implements spec §8's formula from *its own inputs* with the heuristic disclosed in the returned string; resource/risk penalties default to neutral `1.0` until measured (documented as heuristic).
  - Persistence: `runtime_home()/rsi/opportunities.jsonl` (append-only ledger pattern).
- Integration: consumes `alpha/supervision/watchdog.py::DeterministicWatchdog.inspect_anomalies` outputs and benchmark regressions from `alpha/benchmarks/runner.py::recent_results`; feeds WP-B2 `generate(evidence_refs=...)` (Phase B/C consumers).

**Test plan — `backend/tests/test_rsi_opportunity.py`:** identical signatures cluster once with correct `frequency`; cluster without evidence refs → **not** recorded; `confidence` math matches recorded inputs (recompute in test); no-denominator case → `0.5` + `unverified` (honesty pin); `heuristic` string present iff `confidence_kind=="heuristic"`; priority value deterministic + heuristic disclosed.

**Gates:** `python -m pytest tests/test_rsi_opportunity.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-D2 — Cycle budget controller (feature #21) — Phase D

**Goal:** hard per-cycle budgets the model cannot override. Honest-failure: budget exhaustion aborts the cycle with `reason="budget_exhausted: <which> <actual>/<limit>"` (real numbers) — never a silent downgrade to a smaller "successful" run.

**Design:**
- New module `backend/packages/harness/alpha/rsi/budgets.py`:
  - `@dataclass CycleBudget: max_wall_time_s: float = 2700; max_candidates: int = 4; max_changed_files: int = 15; max_diff_lines: int = 1200; max_workspace_mb: int = 1024` (defaults echo spec §104 but **overridable only by operator config**, not candidate payload — enforced: constructor ignores unknown keys from candidates by accepting only an explicit `from_config(mapping)` classmethod validating against known fields).
  - `class BudgetTracker:` — `start(budget)`, `charge(kind: str, amount: float)` (raises `BudgetExceeded(f"{kind} {amount} exceeds {limit}")` with true figures), `remaining() -> dict`, `workspace_usage(path)` (real `du`-style walk, not estimates).
  - `guard(tracker)` context helper used by factory/evaluation loops (WP-B2/C1 call `charge` per candidate and per evaluation).
- State: budget record per cycle in `runtime_home()/rsi/state/cycle.json` (extend the dict additively — same file as A4; coordinate ownership: A4 first, D2 appends optional `budget` key only).

**Integration points:** `alpha/rsi/state.py` (A4, additive key), `alpha/rsi/generator.py` (`charge("candidate", 1)` per variant), `alpha/config/runtime_paths.py`.

**Test plan — `backend/tests/test_rsi_budgets.py`:** exceeding each limit raises with real numbers (assert message contains `actual` and `limit`); `from_config` rejects unknown keys (candidate-override pin); `remaining()` never negative; wall-time budget uses injected clock (deterministic test); exhaustion payload contains no fabricated scores.

**Gates:** `python -m pytest tests/test_rsi_budgets.py tests/test_rsi_state_resume.py -q` · `ruff check` · AST dup gate = 0.

---

### WP-D3 — Strategy memory / reflection stats (feature #23) — Phase D

**Goal:** durable per-strategy statistics built **only** from recorded ledger events — no invented success rates. Honest-failure: zero attempts → stats report `attempts=0` and rate fields as `None` (`"unverified"`), never `0.0`/`1.0` defaults that would read as measurements.

**Design:**
- New module `backend/packages/harness/alpha/rsi/strategy_memory.py`:
  - `@dataclass StrategyStat: strategy: str; problem_class: str; attempts: int; promotions: int; rollbacks: int; rejections: int; promotion_rate: float | None; rollback_rate: float | None; updated_at: float`.
  - `rebuild_from_ledger(lines: Iterable[dict]) -> dict[str, StrategyStat]` — pure function over `runtime_home()/rsi/lineage.jsonl` (A1) + `evolution/ledger.jsonl` (existing) events; rates computed only when `attempts > 0`, else `None`.
  - `prior_for(strategy, problem_class) -> StrategyStat | None` — consumed by WP-B2 `generate()` to choose operators (documented as a *heuristic prior*, disclosed; exploration never disabled, min 1 non-dominant operator always emitted).
  - `lesson_for(failure) -> dict | None` — wraps `alpha/evolution/retrospective_engine.py` patterns; unresolved failures return `{"status":"unresolved"}` rather than an invented explanation (spec §66 final paragraph).

**Integration points:** `alpha/rsi/lineage.py` + `alpha/evolution/engine.py` ledgers (read-only); `alpha/evolution/retrospective_engine.py`; `alpha/rsi/generator.py` (prior consumption).

**Test plan — `backend/tests/test_rsi_strategy_memory.py`:** empty ledger → attempts 0 + rates `None` (honesty pin); synthetic events → rates match hand-computation; rejected/rolled-back events counted distinctly; `lesson_for` on unknown failure → `{"status":"unresolved"}` (no fabricated root cause); determinism (two rebuilds, identical output).

**Gates:** `python -m pytest tests/test_rsi_strategy_memory.py -q` · `ruff check` · AST dup gate = 0.

---

## 4. Phased roadmap (dependency + risk ordering, parallelizable waves)

Spec §99/§109 build order is respected: *safe loop → autonomy → persistence → evolution → adaptive*. Alpha-specific adjustments: Phase A is pure-internal (no gateway surface, no behavior change to existing promotions), so it is lowest risk.

| Phase | Contents | Risk | Depends on | Parallel-safe split (disjoint file ownership) |
|---|---|---|---|---|
| **A — pure internal** | A1 lineage+archive (`rsi/lineage.py`, `rsi/archive.py`), A2 evaluator manifest (`rsi/evaluator_manifest.py`), A3 real holdout (`rsi/holdout.py`, additive `RSIEngine.run_holdout_gate`), A4 state+kill switch (`rsi/state.py`, `rsi/switchboard.py`, guarded `run_rsi_cycle`) | Low — new modules + additive methods; no promotion path changes; simulated preview untouched (tests stay green) | none | **4 agents, fully disjoint:** A1 owns `rsi/lineage.py`+`rsi/archive.py`+`test_rsi_lineage.py`; A2 owns `rsi/evaluator_manifest.py`+`test_rsi_evaluator_manifest.py`; A3 owns `rsi/holdout.py`+`test_rsi_holdout.py` (+1 additive method in `engine.py` — **A3 is the only Phase-A owner of `engine.py`**); A4 owns `rsi/state.py`+`rsi/switchboard.py`+`test_rsi_state_resume.py`+`test_rsi_kill_switch.py` (and the guarded entry of `run_rsi_cycle` — *conflict with A3*: serialize A4's `engine.py` edit after A3, or have A3 expose holdout via `holdout.py` only and let A4 own all `engine.py` edits — recommended: **A4 owns `engine.py`; A3 stays module-only**) |
| **B — isolation + generation** | B1 workspace (`rsi/workspace.py`), B2 factory (`rsi/generator.py`), B3 protected paths (`rsi/protected_paths.py`) | Medium — git worktrees + subprocess execution; guarded by SelfRepoGuard/SafetyGuard; no production writes by construction | Soft: A1 (lineage hooks) — generator tolerates a stub lineage via duck-typing, so B can start **in parallel with A** if the `RsiLineageStore` signature is agreed first | **3 agents, disjoint:** each owns its module + its `test_rsi_*.py`; shared touchpoint = `rsi/workspace.py` (B1) which B2 imports — B2 codes against the agreed interface |
| **C — shadow/canary + gated promotion + HITL** | C1 shadow (`rsi/shadow.py`), C2 promotion+bundle+review (`rsi/promotion.py`, `rsi/evidence_bundle.py`, `rsi/review.py`, router default flip, optional additive approve endpoint) | Higher — first phase that can *actually* promote (still HITL-gated; auto-promote remains OFF) | **Requires A2+A3+A4 and B1+B3** (integrity, holdout, kill-switch, workspace, protected paths); C1 can start once B1 lands | 2–4 agents disjoint: C1 (`shadow`); C2a (`evidence_bundle`); C2b (`review`); C2c (`promotion` + `evolution.py` default flip + optional endpoint). **C2c is the serialization point** — it imports the other three and owns `backend/app/gateway/routers/evolution.py` |
| **D — autonomy inputs (parallel with B/C)** | D1 opportunity miner, D2 budgets, D3 strategy memory | Low–medium; observe-only/statistical; no promotion authority | D1/D3 none; D2 soft-depends on A4's `state.py` (append-only `budget` key — coordinate: A4 merges first) | **3 agents disjoint**, safe alongside B (no file overlap) and C (except D2's one-key coordinate with A4, already resolved at end of A) |

**Recommended wave structure for parallel agent dispatch:**
- **Wave 1:** A1 ∥ A2 ∥ A3 ∥ A4 (with `engine.py` ownership rule above) ∥ D1 ∥ D3.
- **Wave 2 (after Wave 1 merges):** B1 ∥ B2 ∥ B3 ∥ D2.
- **Wave 3 (after Wave 2):** C1 ∥ C2a ∥ C2b, then C2c last (it wires everything + owns the router edit).
- Everything else stays behind existing registration; **no phase touches `app.py`, `contracts/feature_manifest.json`, auth, or frontend.**

Explicitly **not** in this roadmap (per spec §102 "do not implement P2/P3 before P0/P1 evidence" and §5 guardrails): populations/islands/crossover beyond the small Phase-B population, meta-RSI self-modification of gates, LLM-driven candidate generation, live-traffic shadow, percentage canaries, SQLite ledgers, distributed workers, model-weight evolution (S7).

---

## 5. Out of scope + guardrails (binding)

1. **Auth untouched** — no changes under `backend/app/gateway/auth/**`; new endpoints (if any) reuse existing `@require_permission(...)`; no new permission kinds.
2. **No security weakening** — `alpha/safety/**` guards, `self_repo_guard`, `net_policy`, `release_gate` thresholds are *inputs to* gates, never outputs; no phase lowers `DEFAULT_AUTONOMY_GATES`; `evaluate_release_gate` fail-closed semantics preserved.
3. **No self-modification of gates without human approval** — `alpha/rsi/protected_paths.py`, `rsi/promotion.py::RISK_POLICY`, `alpha/benchmarks/release_gate.py`, `alpha/policy/engine.py` are code constants; candidates may *propose* changes (as ordinary reviewable PRs through the normal human path) but no code path in this plan lets a candidate edit them and then approve itself (spec §15/§49/§110-14).
4. **Auto-promotion defaults OFF everywhere** — `RISK_POLICY` all classes `auto_promote=False`; the flagged `GateRequest.autonomous_mode=True→False` flip moves the HTTP default to OFF; the existing `skill_evolution_config.auto_promote default=False` stays. Enabling auto-promotion later is an operator config change requiring central review (a new config schema field ⇒ **`config_version` bump flagged for central review**, per `alpha/config/app_config.py::_check_config_version`).
5. **Never touch `main`** — worktrees/branches are `rsi/*` only (WP-B1 rejects other names); `remove_worktree` deletes only branches it created; no phase runs `git checkout/commit` on the primary worktree (this plan itself performs **no git commands**).
6. **Honesty rules** — no fabricated pass rates/confidences/scores anywhere; `evidence_kind ∈ {measured, simulated, heuristic, unverified}` whitelist enforced in tests; simulated (existing RSI preview, enterprise 96.4 holdout, canary `mock_success`) can exist but can never gate; unverified ⇒ `0.5`-neutral + disclosed + not a pass; evaluation errors fail closed with the real error text.
7. **No hardcoded secrets**; denied-path handling returns patterns, never contents; tokens only from env (precedent: `release_check._fetch_latest_release` reads `GITHUB_TOKEN`/`GH_TOKEN` env only).
8. **Scope fences:** no edits to `backend/app/gateway/app.py`; no new gateway router modules (additive endpoints only on already-registered `projects`/`evolution` routers); no new builtin tools and no change to `contracts/feature_manifest.json`; no `config_version` bump without central review; **frontend/electron deferred**; no server restarts, no commits/stashes/checkouts performed by this planning pass.
9. **RSI remains disable-able without disabling Alpha** (spec I012): `switchboard` failures degrade to *refusing RSI cycles*, never to breaking ordinary Alpha startup (same precedent as `check_for_update` never raising).

---

## 6. Final checklist (feature → planned files → tests → status)

| Feature (verdict today) | Planned files (repo-relative) | Planned tests (`backend/tests/`) | Status |
|---|---|---|---|
| Lineage variant→promoted (PARTIAL) | `backend/packages/harness/alpha/rsi/lineage.py`, `rsi/archive.py` | `test_rsi_lineage.py` | **TODO (not yet implemented)** |
| Evaluator manifest + integrity (PARTIAL) | `rsi/evaluator_manifest.py` | `test_rsi_evaluator_manifest.py` | **TODO (not yet implemented)** |
| Hidden holdout — real (MISSING) | `rsi/holdout.py` (+ additive `RSIEngine.run_holdout_gate` via A4-owned `engine.py` edit) | `test_rsi_holdout.py` | **TODO (not yet implemented)** |
| State persistence/resume (PARTIAL) | `rsi/state.py` | `test_rsi_state_resume.py` | **TODO (not yet implemented)** |
| Kill switch / incident freeze (PARTIAL) | `rsi/switchboard.py` (+ guarded entry in `rsi/engine.py`) | `test_rsi_kill_switch.py` | **TODO (not yet implemented)** |
| Isolated worktree/copy experiments (PARTIAL) | `rsi/workspace.py` | `test_rsi_workspace.py` | **TODO (not yet implemented)** |
| Variant generation with evidence + dedup (PARTIAL) | `rsi/generator.py` | `test_rsi_variant_generation.py` | **TODO (not yet implemented)** |
| Protected-path policy (PARTIAL) | `rsi/protected_paths.py` | `test_rsi_protected_paths.py` | **TODO (not yet implemented)** |
| Shadow variant comparison (PARTIAL) | `rsi/shadow.py` | `test_rsi_shadow.py` | **TODO (not yet implemented)** |
| Gated promotion + risk classes, auto-promote OFF (PARTIAL) | `rsi/promotion.py`; default flip in `backend/app/gateway/routers/evolution.py` | `test_rsi_promotion_gate.py` | **TODO (not yet implemented)** |
| HITL approval for promotions (EXISTS — needs RSI wiring) | `rsi/promotion.py` + optional additive endpoint on `routers/evolution.py` (ApprovalQueue-backed) | `test_rsi_hitl_approval.py` | **TODO (not yet implemented)** |
| Evidence bundle + provenance hashes (PARTIAL) | `rsi/evidence_bundle.py` | `test_rsi_evidence_bundle.py` | **TODO (not yet implemented)** |
| Adversarial/red-team review gate wiring (PARTIAL) | `rsi/review.py` (uses `planning/hyperplan.py`, `deliberation/adversary_deliberator.py`, `governance/council/roles.py`) | `test_rsi_review.py` | **TODO (not yet implemented)** |
| Opportunity miner, observe-only (MISSING) | `rsi/opportunity.py` | `test_rsi_opportunity.py` | **TODO (not yet implemented)** |
| Cycle budget controller (MISSING) | `rsi/budgets.py` (+ additive `budget` key in `rsi/state.py` record) | `test_rsi_budgets.py` | **TODO (not yet implemented)** |
| Strategy memory / reflection stats (PARTIAL) | `rsi/strategy_memory.py` | `test_rsi_strategy_memory.py` | **TODO (not yet implemented)** |
| Canary rollout (EXISTS) | reuse `projects/canary_watchdog.py`, `safety/canary_sandbox.py` — consumed by `rsi/promotion.py` | covered by `test_rsi_promotion_gate.py` | **EXISTS — consumption TODO (not yet implemented)** |
| Rollback-on-regression (EXISTS) | reuse `evolution/engine.py::rollback`, `canary_watchdog.execute_rollback_if_failed` — consumed by `rsi/promotion.py` decision path | covered by `test_rsi_promotion_gate.py` | **EXISTS — consumption TODO (not yet implemented)** |
| Evaluation fabric / repo twin / policy engine / watch-dog / self-repair / regression-first / review council roles (EXISTS) | no new files — consumed as integration points listed in §3 | existing: `test_release_gate.py`, `test_repo_twin_engine.py`, `test_supervision_watchdog.py`, `test_reproduction_engine.py`, `test_governance_quality_council.py` | **EXISTS — no work** |
| RSI preview engine honesty (EXISTS) | `rsi/engine.py` (guarded only) | `test_rsi_cycle.py` (must stay green every phase) | **EXISTS — regression-pinned** |

---

## 7. Unverified / not audited in this pass

Stated honestly rather than implied:

- **No tests were executed** during authoring (read-only planning pass; no server/git/write operations outside this file). All "existing tests" listed are verified to *exist* with the cited imports, not re-run.
- `alpha/coding/repo_twin/**` internals beyond the symbols imported by `backend/tests/test_repo_twin_engine.py` (`RepoRecon`, `SymbolGraph`, `BlastRadiusCalculator`, `SymbolType`) were not audited line-by-line; the package directory's existence and the test's imports were verified.
- `alpha/supervision/watchdog.py`, `alpha/policy/engine.py`, `alpha/evidence/store.py`, `alpha/avo/*` were audited at the **symbol level** (classes/functions verified), not exhaustively line-by-line.
- The exact pytest rootdir invocation (`cd backend && python -m pytest …`) follows `backend/pyproject.toml [tool.pytest.ini_options]`; if CI uses a wrapper (`backend-unit-tests.yml`), prefer the CI's invocation when running the gates.
- Glob/grep helper tooling errored in this session (ripgrep JSON failures); all verification above was cross-checked with direct file reads and PowerShell `Select-String`, so cited paths/symbols were confirmed by at least one successful read or listing.

**END OF PLAN** — status remains **DRAFT-for-review** until an owner signs off on: (a) the §2.4 honest rewrites (especially auto-promote defaults and JSONL-over-SQLite), (b) the flagged `GateRequest.autonomous_mode` default flip, and (c) the wave/file-ownership assignments in §4.

---

## 8. Main-agent adjudication addendum (2026-09-23) — APPROVED WITH AMENDMENTS

Owner review complete. **Sign-off on the §7 conditions:** (a) §2.4 honest rewrites accepted as accurate; (b) the `GateRequest.autonomous_mode` flip is assigned and in flight (central honesty wave also fixing enterprise `0.85`/`0.8`/seed-confidence fabrications and the `memory.py` belief default); (c) §4 wave/file-ownership assignments accepted **as amended below**. Verification result: all **27 inventory verdicts confirmed correct** (11 EXISTS / 13 PARTIAL / 3 MISSING); all **8 §2.4 mismatches confirmed accurate** against the code (independent evidence pass over HEAD + working tree).

**Binding citation corrections for implementers:**
- **A1 (row 1):** two of three "used by `WorktreeManager`" citations are false — `avo/workspace_runner.py` uses only `manage_code_checkpoint`; `autonomous_benchmark_harness.py` uses tempfile + `git clone`. Only real caller: `projects/canary_watchdog.py:140-143`. Verdict unchanged.
- **A2 (row 23):** drop the `alpha/reflection/resolvers.py` citation (import-path resolver, unrelated to strategy memory); rely on `evolution/retrospective_engine.py` + the learning-graph tests.
- **A3 (row 19):** adjacent un-inventoried subsystem: `alpha/company/{kpi,strategy,self_improvement,executive}.py` + `KPISpec`/`StrategicObjective.target_kpi` form a business-KPI feedback loop adjacent to D1/D3. MISSING verdict for spec `OpportunityMiner` stands; **D1 must read those files (read-only) before designing** so it neither duplicates nor wires into them.
- **A4 (row 12):** duplicate `test_autonomous_benchmark_harness.py` citation (trivial).
- **A5 (grep-grade closure, 2026-09-23, `git grep`):** row 3 `holdout` → only `enterprise/council.py` "explicitly simulated" previews (`holdout_passed` forced `False`) + router/heartbeat consumers → **MISSING (real hidden holdout) confirmed**; row 19 `OpportunityMiner|opportunity_miner|mine_opportunit` → **zero hits, confirmed**; row 21 `budget` in `alpha/rsi/` → only `rsi/engine.py:26,93 max_budget_chars` (compaction character cap, not a cycle resource controller) → **MISSING confirmed, adjacency noted for D2**.

**New findings from adjudication (queued centrally, not plan changes):**
- `alpha/enterprise/heartbeat.py:206` — fabricated fallback `98.5` holdout score when no active release (undisclosed; same family as §2.4 #6) → added to the TASK_LIST known-issues queue.
- Spec features with no inventory row (decide scope at Wave-3 review): spec §42 promotion cooldown / update-loop prevention, §26/§43 immutable `releases/` dirs + single active pointer, §54 RSI-E000–E018 failure taxonomy. Uncited-but-existing surfaces to consume or cite: `variation_operator_tool` + `run_nvidia_avo_step` (manifest-wired), `avo_lineage_tool`, `self_improvement_tool`/`ralph_loop_tool`, `skills/security_static_scanner.py`.

**Ownership amendment (resolves §4's own A3/A4 conflict):** **A4 owns `rsi/engine.py` for every Phase-A edit** (guarded `run_rsi_cycle` entry + additive `run_holdout_gate` wiring of A3's API); **A3 stays module-only** (`rsi/holdout.py` exposes the gate API). This is §4's own recommended resolution and is hereby binding.

**Status: APPROVED for Wave-1 dispatch (A1 ∥ A2 ∥ A3 ∥ A4 ∥ D1 ∥ D3).** Waves 2–3 dispatch only after Wave-1 central review + merge commits, per §4.
