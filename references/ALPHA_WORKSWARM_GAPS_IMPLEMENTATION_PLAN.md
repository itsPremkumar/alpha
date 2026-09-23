# Alpha — WorkSwarm Gap Closure: Reality-Mapped Implementation Plan

| Field | Value |
|---|---|
| **Purpose** | Close the 7 concrete PARTIAL gaps that a prior inventory found in Alpha against the external WorkSwarm-derived plan, and wire the resulting enhancements into systems that already exist. This is **not** a rebuild of WorkSwarm. |
| **Source plan** | `references/ALPHA_WORKSWARM_ONLY_INTEGRATION_PLAN.md` (2873 lines, sections 1–56; read in full for this document) |
| **Date** | 2026-09-23 |
| **Status** | **DRAFT-for-review** — for the central agent; no code has been edited by this document. |
| **Reality snapshot** | 2026-09-23 17:31 local, repo `C:\Users\PREM KUMAR\Videos\alpha`, verified with reads + `git status --porcelain` + `git show HEAD:<path>` / `git cat-file -e HEAD:<path>` |
| **Scope of this file** | This single Markdown file only. |

**Reality-mapping statement.** Every claim below is mapped to actual files in this repository. The Alpha Python package is at **`backend/packages/harness/alpha/`** (referred to below as `ALPHA_PKG`); gateway routers are at **`backend/app/gateway/routers/`**, app wiring at **`backend/app/gateway/app.py`**, tests at **`backend/tests/`**, contracts at **`contracts/feature_manifest.json`**. Every path cited was checked to exist at the snapshot time (directory listings, targeted reads, `Select-String` symbol extraction, `git status`/`git show`). Where reality differs from the supplied 7-item PARTIAL list, this document reports what the code actually does, with symbols named.

> **Moving-target warning (honest disclosure):** at snapshot time, five wave-2 subagents were actively landing code for gaps 1–6 (see `docs/TASK_LIST.md` §2/§4). Gaps 1–5 are implemented **in the working tree but UNCOMMITTED**; `git show HEAD:<path>` confirms the committed baseline still lacks them. Gap 6's module landed during this verification and is **not yet wired**; gap 7 was still **unassigned**. Central review + staged commits are therefore prerequisites to treating any gap as closed.

---

## 1. Hard constraints (apply to all 7 gaps)

1. **No edits to `backend/app/gateway/app.py`.** New endpoints are **additive only on already-registered routers**. Registered routers verified via `include_router` grep in `app.py` — relevant ones: `skills.router`, `skills_workshop.router`, `plan_mode.router`, `policy.router`, `commands.router`, `models.router`, `memory.router`.
2. **No new router modules.** `backend/app/gateway/routers/` contains 56 `.py` files (55 routers + `__init__.py`), all 55 referenced by the **57 `include_router(...)` calls** in `app.py` (e.g. `commands.router` is mounted twice, at `/api/gateway` as well as default). Both counts must not grow.
3. **No new builtin tools.** `contracts/feature_manifest.json` currently declares **119 tools** and is clean in `git status contracts/`. It must stay byte-identical: no new entry, no wiring change. (Relevant pre-existing tools we reuse instead: `consult_experience`, `workflow_dag_manage`, `code_mode_tool`, `propose_skill_tool`, `memory_add`, `memory_update` — all `wired=True` today.)
4. **No auth changes**, no new endpoints that bypass `require_admin_user`/`Depends(get_config)` conventions already used on their router.
5. **No config schema semantics change → no `config_version` bump** (`backend/packages/harness/alpha/config/app_config.py::_check_config_version` compares user vs example version; gap work adds only optional fields with defaults, e.g. `TokenBudgetConfig.scopes`, or no config fields at all).
6. **Honesty rules** (Section 9) are non-negotiable and are pinned by assertions in the named tests.

---

## 2. Reality-mapping table

Verdict columns: **HEAD** = committed baseline (what the prior 15 EXISTS / 7 PARTIAL / 0 MISSING inventory was measured against), **WT** = working tree at 2026-09-23 17:31 (in-flight wave-2 landings).

| # | Gap (from the supplied 7-item list) | Exact existing files | HEAD verdict | WT verdict | One-line evidence (verified symbols / absence) |
|---|---|---|---|---|---|
| 1 | Skill evolution engine (config only) | `ALPHA_PKG/config/skill_evolution_config.py`, `ALPHA_PKG/skills/{workshop,proposals,curator}.py`, `ALPHA_PKG/evolution/engine.py`, **new** `ALPHA_PKG/skills/evolution_engine.py`, `backend/app/gateway/routers/skills_workshop.py`, `backend/tests/test_skill_evolution_engine.py` | **PARTIAL** | **EXISTS (uncommitted)** | HEAD lacks `skills/evolution_engine.py` (`git cat-file -e` fails); WT has `class SkillEvolutionEngine.propose/evaluate/promote/rollback`, `class SkillEvolutionStore` (atomic store under `runtime_home()/skill_evolution/`), module seam `moderation_invoke`, plus `/api/skills/workshop/evolution*` endpoints on the already-registered `skills_workshop` router. |
| 2 | Skill retrieval index + relationship graph | `ALPHA_PKG/skills/catalog.py`, **new** `ALPHA_PKG/skills/retrieval.py`, `ALPHA_PKG/learning/graph.py`, `backend/app/gateway/routers/skills.py`, `backend/tests/test_skill_retrieval_graph.py` | **PARTIAL** | **EXISTS (uncommitted)** | HEAD `catalog.py` has `search()`/`search_smart()` only and `graph.py` has no `SkillGraph`; WT adds `class SkillGraph` + `SkillEdge` + `suggest_chains()`/`_enumerate_paths()` (`SKILL_GRAPH_SCORE_METHOD = "graph-lexical-heuristic"`), `SCORE_METHOD = "lexical-heuristic"` in `retrieval.py`, and additive `GET .../retrieve` + `GET .../graph` handlers in `routers/skills.py` (`_retrieval_catalog`, `_skill_graph_store`). |
| 3 | Hierarchical scoped budgets (goal/workflow/node, parent-bounds-child) | `ALPHA_PKG/config/token_budget_config.py`, `ALPHA_PKG/agents/middlewares/token_budget_middleware.py`, `ALPHA_PKG/models/cost_governor.py`, `ALPHA_PKG/workflow/dag_engine.py`, `config.example.yaml`, `backend/tests/test_token_budget_scopes.py` | **PARTIAL** | **EXISTS (uncommitted)** | HEAD `token_budget_config.py` contains no `SCOPE_HIERARCHY`/`ScopeBudgetConfig` (grep empty); WT defines `SCOPE_HIERARCHY = ("global","project","goal","workflow","node","agent")`, `budget_scope()` ContextVar, `TokenBudgetConfig.scope_chain()`/`effective_scope_limits()` (child clamped by `min(child, remaining parent)`, `bounders` recorded), middleware `_charge_scope`/`consume_scope_exhaustion`, cost governor `set_scope_budget`/`_scope_chain_locked`, DAG binds `workflow:<key>` / `node:<key>:<node_id>`. |
| 4 | HITL human-gated workflow steps | `ALPHA_PKG/workflow/dag_engine.py`, `ALPHA_PKG/projects/approval_queue.py`, `ALPHA_PKG/tools/builtins/workflow_dag_tool.py`, `backend/tests/test_workflow_budget_hil.py`, `backend/tests/test_workflow_dag_engine.py` | **PARTIAL** | **EXISTS (uncommitted)** | HEAD `dag_engine.py` has no `requires_approval`/`WAITING_APPROVAL` (grep empty); WT `DAGNode.requires_approval`, `approval_request_id`, statuses `STATUS_WAITING_APPROVAL/STATUS_REJECTED/STATUS_APPROVAL_TIMED_OUT`, `_gate_decision()` + `_resolve_approval_queue()` → `alpha.projects.approval_queue.get_approval_queue()`, `DAGEngine.execute(..., approval_queue=, gate_timeout_seconds=)`. |
| 4b | *(scope correction)* approvals integration target | `ALPHA_PKG/orchestrator/approvals.py` vs `ALPHA_PKG/projects/approval_queue.py` | — | — | **Mismatch vs the brief:** `orchestrator/approvals.py` is *command custody* (`ApprovalDecision`, `command_hash()`, `classify_risk()`, allow/ask/deny/auto_allow) with **no** workflow-node queue API. The queue actually wired is `projects/approval_queue.py` (`ApprovalQueue.request_approval/resolve_request/list_pending`, file-backed). Plan keeps `approval_queue` as queue of record (Section 5). |
| 5 | FACT/TIP experience bank + hygiene/dedup | `ALPHA_PKG/learning/experience/{models,store,hygiene,retriever,__init__}.py`, `ALPHA_PKG/tools/builtins/experience_tool.py`, `backend/tests/test_experience_fact_tip.py` | **PARTIAL** | **EXISTS (uncommitted)** | HEAD `models.py` has only episodic fields (no `ExperienceKind`); WT adds `ExperienceKind.{EPISODE,FACT,TIP}`, `NEUTRAL_CONFIDENCE = 0.5`, `store.refusal_reason(record, require_evidence=True)` (refuses secret/PII-shaped text via `find_secret_shape()` **and** FACT/TIP without evidence), `run_hygiene(store) -> HygieneReport` (dedup/merge/down-rank + `append_audit`), `ExperienceRetriever.relevant_for()` returning dicts with `score_method="lexical_overlap_v1"` + `reason`. New file `hygiene.py` is untracked at HEAD. |
| 6 | Pre-memory-write secret filter | **new** `ALPHA_PKG/security/memory_redaction.py`, `ALPHA_PKG/agents/memory/manager.py`, `ALPHA_PKG/agents/memory/tools.py`, `ALPHA_PKG/runtime/secret_context.py`, `ALPHA_PKG/agents/middlewares/input_sanitization_middleware.py` | **PARTIAL** | **PARTIAL (module landed, UNWIRED)** | `git grep`/`Select-String` for `redact|secret|sanitiz|scrub|mask` in `agents/memory/manager.py` returns **nothing** → no write-boundary filter in HEAD or WT. WT added `redact_for_memory(text, extra_patterns) -> RedactionResult` and `redact_mapping(payload)` in `alpha/security/memory_redaction.py`, but **zero callers** at snapshot and its promised test `backend/tests/test_memory_secret_filter.py` is **ABSENT**. |
| 7 | Unified execution-mode switch (plan vs code as one global mode) | `ALPHA_PKG/planning/bridge.py`, `ALPHA_PKG/agents/factory.py`, `ALPHA_PKG/client.py`, `ALPHA_PKG/agents/lead_agent/agent.py`, `ALPHA_PKG/tools/builtins/code_mode_tool.py`, `ALPHA_PKG/policy/engine.py`, `backend/app/gateway/routers/plan_mode.py`, `backend/docs/plan_mode_usage.md`, `ALPHA_PKG/commands/catalog.py` | **PARTIAL** | **PARTIAL (unchanged — unassigned)** | Reality verified: mode state is **fragmented across four uncoordinated mechanisms**, and `grep "work.plan|code.normal|code.plan|work.plan"` returns **0 hits** anywhere in `ALPHA_PKG`/`backend/app`: (a) `plan_mode: bool` → `is_plan_mode` runtime configurable → `TodoMiddleware` (`agents/factory.py::create_agent_workspace_agent(plan_mode=)`, `client.py:187`, `lead_agent/agent.py:627`), (b) `/api/plan-mode` router = *evaluate/dispatch* (`CognitiveMetaPlanner`, `AutonomousDispatchBridge.dispatch`) not a side-effect gate, (c) `code_mode` builtin tool = programmatic tool execution, unrelated to a mode flag, (d) side-effect verdicts in `policy/engine.py` (`BASE_RULES` with `read:*→allow`, `git:push→approval`) know nothing about the current mode. No single global mode object exists. |

**EXISTS areas (acknowledged so nothing is rebuilt):** autonomous loops (`runtime/sentinel/{loop,runner,scheduler,checkpoint}.py`, `perpetual/{daemon,memory_consolidator,stagnation}.py`, `learning/review_queue.py`, `skills/curator.py`, `enterprise/heartbeat.py`, `swarm/{coordinator,watchdog}.py`), workflow DAG engine (`workflow/dag_engine.py` + `tools/builtins/workflow_dag_tool.py`), approvals queue (`projects/approval_queue.py`, `orchestrator/approvals.py`), cost governor (`models/cost_governor.py::CostGovernor`), token budget middleware (`agents/middlewares/token_budget_middleware.py::TokenBudgetMiddleware`), skills catalog/workshop/proposals/curator/forge/hub (`skills/catalog.py`, `skills/workshop.py`, `skills/proposals.py`, `skills/curator.py`, `skills/forge/`, `skills/hub/`), learning graph + experience store (`learning/graph.py::KnowledgeGraph`, `learning/experience/store.py`), evolution engine propose→benchmark→gate→promote (`evolution/engine.py::EvolutionEngine.propose/record_benchmark/gate/rollback`), RSI engine (`rsi/engine.py`), memory manager (`agents/memory/manager.py::MemoryManager`), and **55 registered gateway router modules** under `backend/app/gateway/routers/` (57 `include_router(...)` references in `app.py` — the "57 modules" figure from earlier inventories counts references, not files).

---

## 3. Gap 1 — Skill evolution engine

### 3.1 Goal + honest-failure semantics
Turn validated evidence into a **versioned candidate** skill body, evaluate it offline, gate it by policy, promote/rollback — **never mutating the active `SKILL.md` before promotion**.

A failed/unconfigured path must report, never fake success:
- `skill_evolution.enabled=False` → raise `SkillEvolutionDisabledError` (no silent no-op).
- No `test-command` in candidate frontmatter → `validation_kind="structural-only"` + note `"runtime behavior NOT verified"`; score = `checks_passed / checks_total` of checks that actually ran.
- Moderation model not configured → `moderation_status="not_configured"` (NEVER a pass); with `security_fail_closed=True` (default) a moderation/evaluation error **rejects with the real error string**; with `False` the record stays `proposed` (never `validated`) and the error is recorded.
- `auto_promote=False` (default) → `promote()` without `approve=True` → `InvalidTransitionError` with reason.
- TOCTOU: `promote()` refuses if active body hash ≠ proposal baseline; `rollback()` refuses to clobber a body modified after promotion — both with explicit reasons.

### 3.2 Design (landed in working tree — keep as specified, then review)
New module `backend/packages/harness/alpha/skills/evolution_engine.py`:
- `class SkillEvolutionProposal` (dataclass; `to_dict`/`from_dict`): `proposal_id, skill_name, status ∈ {proposed, validated, rejected, promoted, rolled_back}, evidence: list[dict], candidate_md, baseline_sha256, candidate_version, validation_kind, checks: list[dict], score: float, score_method, moderation_status, notes: list[str], events: list[dict]`.
- `class SkillEvolutionStore(root: str | Path)` — `root = runtime_home() / "skill_evolution"`; `save/load/list_all`, one JSON file per proposal, **atomic write: tmp file + `os.replace`**.
- `class SkillEvolutionEngine(config=None)` — `propose(skill_name, *, evidence, candidate_md, reason="") -> SkillEvolutionProposal`, `evaluate(proposal_id) -> SkillEvolutionProposal`, `promote(proposal_id, *, approve=False, reason="", actor="") -> SkillEvolutionProposal`, `rollback(proposal_id, *, reason="", actor="") -> SkillEvolutionProposal`, `get`, `list_proposals(status=None)`, `store_root()`.
- **Integration seam tests stub:** module-level `moderation_invoke(content, *, executable, model_name) -> dict` — tests monkeypatch `alpha.skills.evolution_engine.moderation_invoke`; offline suite runs via `_run_suite` (subprocess, only when declared).

Config: `backend/packages/harness/alpha/config/skill_evolution_config.py` (`enabled`, `moderation_model_name`, `security_fail_closed`, `auto_promote`) — working tree only adds documentation/default alignment (**no schema semantics change → no `config_version` bump**).

### 3.3 Integration points (additive, registered router only)
- `backend/app/gateway/routers/skills_workshop.py` (already `include_router`'d; `APIRouter(prefix="/api/skills/workshop")`): existing `POST /api/skills/workshop/distill`, `POST /api/skills/workshop/publish` + **new additive** `POST /api/skills/workshop/evolution` (status 201), `GET /api/skills/workshop/evolution`, `GET /api/skills/workshop/evolution/{proposal_id}`, `POST /api/skills/workshop/evolution/{proposal_id}/evaluate`, `POST .../promote`, `POST .../rollback` via the `_build_evolution_engine(config)` / `_evolution_http_error(exc)` helpers. All guarded by `Depends(get_config)` + request actor; failures map to a real HTTP status carrying the engine's reason.
- **Do not** add tools (`propose_skill_tool` already in manifest), **do not** touch `app.py`, **do not** create a router file.

### 3.4 Test plan
- `backend/tests/test_skill_evolution_engine.py` (new, 22 kB at snapshot) — must pin: disabled-config raises; structural-only honesty (`validation_kind` + "not verified" note); score arithmetic equals passed/total (no invented score); moderation `not_configured` never passes; `security_fail_closed` reject-with-reason; promote requires recorded evaluation + `approve=True`; baseline-hash TOCTOU refusal; rollback refusal after external modification; store writes are atomic (tmp+`os.replace`) and land under `runtime_home()/skill_evolution`.
- Regressions: `tests/test_skill_workshop.py`, `tests/test_skill_proposals.py`, `tests/test_gateway_skill_proposals.py`, `tests/test_skills_validation.py`.

### 3.5 Gates (per gap)
```powershell
# from backend/ (PowerShell 5.1 form of the Makefile test target)
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_skill_evolution_engine.py tests/test_skill_workshop.py tests/test_skill_proposals.py -q
uv run ruff check packages/harness/alpha/skills/evolution_engine.py app/gateway/routers/skills_workshop.py tests/test_skill_evolution_engine.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/skills/evolution_engine.py app/gateway/routers/skills_workshop.py tests/test_skill_evolution_engine.py
# gate = pytest exit 0, ruff clean, ast_dup_gate_dups=0 (exit 0)
```

### 3.6 Ordering / ownership
Independent → **W1-A**. Owner files (do not share): `ALPHA_PKG/skills/evolution_engine.py`, `ALPHA_PKG/config/skill_evolution_config.py`, `backend/app/gateway/routers/skills_workshop.py`, `tests/test_skill_evolution_engine.py`. Must NOT touch `skills/catalog.py`/`skills/retrieval.py` (gap 2) or `learning/experience/*` (gap 5).

---

## 4. Gap 2 — Skill retrieval/index + relationship graph

### 4.1 Goal + honest-failure semantics
Scored, **reason-disclosing** candidate retrieval over installed skills + a directed skill-relationship graph that proposes composition chains. Failure/unconfigured semantics:
- Scores come only from the disclosed lexical heuristic: every candidate carries `score_method="lexical-heuristic"` **and** `reasons[]` citing matched evidence (name/tool/tag/description hits); every response carries a `note` stating scores are heuristic, not measured relevance.
- No embeddings, no network: documented in the module docstring (repo has no offline embedder; `alpha.tools.selection` System One is a network client). If an embedder lands later it must be config-gated and named in `score_method`.
- Empty catalog / everything below `min_score` → **empty list + honest note**, never a fabricated top hit; results always capped by `top_k`.
- Graph chains: `SKILL_GRAPH_SCORE_METHOD="graph-lexical-heuristic"`, `BASELINE_CONFIDENCE=0.5` + `BASELINE_CONFIDENCE_NOTE`, `SKILL_GRAPH_CHAIN_NOTE` discloses that edge confidences are stored evidence (not guarantees), `_enumerate_paths` returns a truncation flag when the `MAX_CHAIN_CANDIDATES=250` cap bites (no silent "no chains exist").

### 4.2 Design (landed in working tree — keep, then review)
- `backend/packages/harness/alpha/skills/retrieval.py` — `SCORE_METHOD`, dataclass result payload `{skill, match_score, score_method, reasons[], note}`, deterministic ranking `(-match_score, name)`, `min_score`/`top_k` caps.
- `backend/packages/harness/alpha/skills/catalog.py` (additive helpers) — `_retrieval_scores(query, skills) -> dict[str,float]`, `build_skill_candidates`, `merge_skill_results`, `search_skills_smart(query, skills, *, limit)`, kept alongside the pre-existing `search()`/`search_smart()` so current callers are unchanged (**module-level seam: `_retrieval_scores` is what tests stub**).
- `backend/packages/harness/alpha/learning/graph.py` (additive to the existing `KnowledgeGraph`) — `class SkillEdge` (`from,to,edge_type ∈ {can_feed,requires,enhances,conflicts_with,alternative_to,produces},confidence,evidence_count,success_rate,last_validated,note`; `to_dict/from_dict`), `class SkillGraph` with `add_edge`, `edges`, `suggest_chains(*, start, goal, max_depth)`, `_enumerate_paths`, `to_dict/from_dict`, `save/load`, `skill_graph_path()` → **`runtime_home()/skill_graph.json`, atomic tmp + `os.replace`** (`load_skill_graph(path=None)` convenience).
- Persistence: proposal-free; graph only. `MAX_STORED_EVIDENCE = 50` per edge.

### 4.3 Integration points (additive, registered router only)
- `backend/app/gateway/routers/skills.py` (already registered; `APIRouter(prefix="/api")`): additive `GET /api/skills/retrieve` (`async def retrieve_skills(...)`, fed by `_retrieval_catalog(config)`, calling `alpha.skills.retrieval::retrieve_skills` imported as `score_skill_retrieval`) and `GET /api/skills/graph` (`async def skill_graph(...)`, fed by `_skill_graph_store()` → `alpha.learning.graph::load_skill_graph`, payload includes `path = str(skill_graph_path())`), both `Depends(get_config)`; existing list/install/custom/curator endpoints untouched.
- No new tool, no `app.py` edit, no manifest change (skill discovery already reachable through existing skill endpoints).

### 4.4 Test plan
- `backend/tests/test_skill_retrieval_graph.py` (new) — pins: every candidate has non-empty `reasons[]` + `score_method` + response `note`; ordering deterministic under ties; `top_k`/`min_score` enforced; empty catalog ⇒ `[]` with note (no fabricated hit); graph round-trip `save`→`load` from `runtime_home()`; chain suggestion respects `max_depth`, discloses truncation at `MAX_CHAIN_CANDIDATES`, and returns `[]` honestly when no path exists; edge confidence never rises without `evidence_count` increasing (arithmetic only).
- Regressions: `tests/test_skill_catalog.py`, `tests/test_public_skill_cartographer.py`, `tests/test_learning_graph_engine.py`, `tests/test_autonomous_curator_and_learning_graph.py`, `tests/test_skills_router_authz.py`.

### 4.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_skill_retrieval_graph.py tests/test_skill_catalog.py tests/test_learning_graph_engine.py -q
uv run ruff check packages/harness/alpha/skills/retrieval.py packages/harness/alpha/skills/catalog.py packages/harness/alpha/learning/graph.py app/gateway/routers/skills.py tests/test_skill_retrieval_graph.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/skills/retrieval.py packages/harness/alpha/skills/catalog.py packages/harness/alpha/learning/graph.py app/gateway/routers/skills.py tests/test_skill_retrieval_graph.py
```

### 4.6 Ordering / ownership
Independent of gaps 1/5/6 → **W1-A** (parallel). Owner files: `skills/retrieval.py`, `skills/catalog.py`, `learning/graph.py`, `routers/skills.py`, `tests/test_skill_retrieval_graph.py`. Shared-file alert: `learning/graph.py` is also read by gap 5's neighborhood — experience code must keep using `KnowledgeGraph` API unchanged (additive-only rule).

---

## 5. Gap 3 — Hierarchical scoped budgets (+ Gap 4 dependency note)

### 5.1 Goal + honest-failure semantics
One budget hierarchy `global → project → goal → workflow → node → agent` where **a parent's remaining limit bounds every child** (`min(child limit, remaining parent)`, recursively), charged by both the token middleware and the cost governor, and surfaced honestly:
- Exhaustion ends in the explicit status **`BUDGET_EXHAUSTED`** carrying `scope_id`, `budget_limit`, `consumed_tokens` (and `EffectiveScopeLimits.bounders` — the ancestor that clamped), never a "completed" result and never a retried-forever loop.
- Disabled + no `scopes` configured ⇒ behavior is **exactly** the pre-existing per-run enforcement (existing `test_token_budget_middleware.py` must pass unmodified in spirit — no behavior drift for old configs).
- Config validation fails closed at load: duplicate `scope_id`, dangling `parent`, hierarchy cycle, unknown `level` → `ValueError` (never silently ignored).

### 5.2 Design (landed in working tree — keep, then review)
- `ALPHA_PKG/config/token_budget_config.py`: `SCOPE_HIERARCHY`, `ScopeBudgetConfig(scope_id, parent, level, max_tokens, max_input_tokens, max_output_tokens)` with `infer_level()` validator, `TokenBudgetConfig.scopes` + `validate_scopes()` + `scope_index()` + `scope_chain(scope_id)` + `effective_scope_limits(scope_id, usage) -> EffectiveScopeLimits | None`, ContextVar helpers `get_current_budget_scope()/set_budget_scope()/reset_budget_scope()/budget_scope(scope_id)` (nesting-safe contextmanager).
- `ALPHA_PKG/agents/middlewares/token_budget_middleware.py`: `scope_usage`, `_charge_scope(scope_id, delta_input, delta_output)`, `_scope_limits`, `consume_scope_exhaustion(run_id) -> dict | None` (emits the `BUDGET_EXHAUSTED` payload + warning/hard-stop message constants `_BUDGET_WARNING_MSG`/`_BUDGET_EXCEEDED_MSG`), plus unchanged `wrap_model_call` path.
- `ALPHA_PKG/models/cost_governor.py` (USD side): `set_scope_budget(scope_id, limit_usd, parent=None)`, `ScopeBudgetState`, `_scope_chain_locked(scope_id)`, `_charge_scope(scope_id, cost_usd)` with `CircuitBreakerTrippedError(scope_id, blocked_by, detail)`; storage `_costs_storage_path()` (existing atomic save pattern).
- `ALPHA_PKG/workflow/dag_engine.py` binds scopes: `budget_scope(f"workflow:{key}")` around the run and `budget_scope(f"node:{key}:{node_id}")` around each `node_runner` call; `create_workflow(key, name, budget=None)` / `DAGNode.budget`.
- `config.example.yaml`: documented commented `scopes:` example (additive, optional ⇒ **no `config_version` bump**).

### 5.3 Integration points
- Middleware is wired through the **existing** `TokenBudgetMiddleware.from_config(config)` construction path (no new middleware, no `app.py` change).
- DAG scope binding is internal to `ALPHA_PKG/workflow/dag_engine.py::DAGEngine.execute`; the already-existing builtin tool `workflow_dag_manage` (`wired=True` in manifest) exposes it — **no new tool**.
- Cost governor reached through its existing singleton `get_cost_governor()`.

### 5.4 Test plan
- `backend/tests/test_token_budget_scopes.py` (new) — pins: parent-bounds-child arithmetic (child effective = min(child, parent remaining) at each hop); cycle/duplicate/dangling-parent validation errors; ContextVar nesting restores on exception; `bounders` names the clamping ancestor; no-scope behavior identical to per-run behavior; exhaustion reports real numbers, `score`-free, status `BUDGET_EXHAUSTED`.
- `backend/tests/test_workflow_budget_hil.py` (new, covers gaps 3+4 jointly — see §6) — pins workflow/node budget stop + dependent nodes `SKIPPED`.
- Regressions: `tests/test_token_budget_middleware.py`, `tests/test_token_usage.py`, `tests/test_token_meter.py`, `tests/test_reasoning_budget_governor.py`, `tests/test_tool_output_budget_middleware.py`, `tests/test_workflow_dag_engine.py`.

### 5.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_token_budget_scopes.py tests/test_workflow_budget_hil.py tests/test_token_budget_middleware.py tests/test_workflow_dag_engine.py tests/test_reasoning_budget_governor.py -q
uv run ruff check packages/harness/alpha/config/token_budget_config.py packages/harness/alpha/agents/middlewares/token_budget_middleware.py packages/harness/alpha/models/cost_governor.py config.example.yaml
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/config/token_budget_config.py packages/harness/alpha/agents/middlewares/token_budget_middleware.py packages/harness/alpha/models/cost_governor.py
```
(`config.example.yaml` is YAML — AST gate applies to the `.py` files only; YAML validity is covered by the repo's YAML-parse gate.)

### 5.6 Ordering / ownership
**Gap 3 must land before (or in the same commit as) Gap 4** — they share `ALPHA_PKG/workflow/dag_engine.py` and `token_budget_middleware.py`. Both gaps = **one owner (W1-B)**, sequenced 3→4. If split across agents anyway: gap 3 owns `token_budget_config.py`, `token_budget_middleware.py`, `cost_governor.py`, `config.example.yaml`, `tests/test_token_budget_scopes.py`; gap 4 owns `dag_engine.py`, `projects/approval_queue.py` (read/extend only if needed), `tests/test_workflow_budget_hil.py` — **`dag_engine.py` is single-writer: whichever of the two is active edits it, the other reviews**.

---

## 6. Gap 4 — HITL human-gated workflow steps

### 6.1 Goal + honest-failure semantics
A DAG node declared `requires_approval=True` pauses the run until a human decision, resumes on approval, and **fails honestly** otherwise:
- `WAITING_APPROVAL` — request pending; run returns this status (dependents marked `SKIPPED`, never executed, never reported complete); caller re-invokes `execute()` after a decision.
- `REJECTED` — human refused; reason from the queue decision propagates into `WorkflowRunResult.detail`.
- `APPROVAL_TIMED_OUT` — `gate_timeout_seconds` elapsed with no decision; explicit status, no silent auto-approve.
- Approval requests are created through the **existing file-backed queue** (`alpha.projects.approval_queue.ApprovalQueue.request_approval(action_type="workflow_node_approval", ...)`) and carry real `request_id`s; nothing about a human gate may be simulated in tests except via an injected fake queue.

### 6.2 Design (landed in working tree — keep, then review)
- `ALPHA_PKG/workflow/dag_engine.py`: `DAGNode.requires_approval: bool`, `approval_request_id: str | None`, `DAGEngine.execute(key, node_runner, *, approval_queue=None, project_id=None, gate_timeout_seconds=None)`, `_gate_decision(node, queue, default_timeout) -> str`, `_resolve_approval_queue(project_id)` (defaults to project `"dag-workflows"`), status constants `STATUS_WAITING_APPROVAL/STATUS_REJECTED/STATUS_APPROVAL_TIMED_OUT/STATUS_BUDGET_EXHAUSTED/STATUS_SKIPPED`, `WorkflowRunResult.waiting_node/waiting_node_id/approval_request_id/budget_*` fields with `to_dict()` for checkpoint/resume snapshots.
- Queue of record: `ALPHA_PKG/projects/approval_queue.py` (`ApprovalRequest`, `ApprovalQueue.request_approval/resolve_request/get_request/list_requests/list_pending`, `get_approval_queue(project_id)`) — file-backed, atomic save.
- **Scope correction (brief vs reality):** `ALPHA_PKG/orchestrator/approvals.py` (command-custody `ApprovalDecision`/`classify_risk`) is **not** the workflow queue. Optional *advisory* bridge only: when a node gate resolves, mirror the decision into `orchestrator/approvals.ApprovalDecision`-style custody records **if** a trace/session id is available; this is a follow-up nicety, not a prerequisite. Do not re-point the DAG at `orchestrator/approvals.py` — it has no queue semantics.

### 6.3 Integration points
- Existing builtin tool `workflow_dag_manage` (`ALPHA_PKG/tools/builtins/workflow_dag_tool.py`, `wired=True`) — extend its **existing** action surface if new verbs are needed (tool name/symbol unchanged ⇒ manifest untouched).
- Resume path: re-invoke `DAGEngine.execute` after `ApprovalQueue.resolve_request(...)`; `snapshot()`/`restore()` already persist waiting state (checkpoint requirement from source plan §10.5).
- No new router, no `app.py` edit. (Human decisions surface through existing queue-reading code paths; if an HTTP surface is needed, it must be an additive route on an **already-registered** router that already owns approval-style endpoints — `routers/policy.py` has `POST /approvals`, `POST /approvals/{id}/decide`; prefer reusing that vocabulary over inventing a new one.)

### 6.4 Test plan
- `backend/tests/test_workflow_budget_hil.py` (new) — pins jointly: gated node ⇒ `WAITING_APPROVAL` + `waiting_node` set + dependents `SKIPPED`; approval ⇒ re-execute completes; rejection ⇒ `REJECTED` with reason; timeout ⇒ `APPROVAL_TIMED_OUT`; budget stop ⇒ `BUDGET_EXHAUSTED` with `scope_id/budget_limit/consumed_tokens`; **no path returns `completed` without `record_evidence`** (`UnverifiedNodeCompletionError`).
- `backend/tests/test_workflow_dag_engine.py` (existing) — regression: DAG waves, write-scope collision (`WriteScopeCollisionError`), `from_dict`/`to_dict` round-trip incl. approval fields.
- Honesty assertions: assert statuses are exactly the exported constants; assert no `detail` claims success on a rejected/timed-out gate.

### 6.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_workflow_budget_hil.py tests/test_workflow_dag_engine.py tests/test_workflow_tools_integration.py -q
uv run ruff check packages/harness/alpha/workflow/dag_engine.py packages/harness/alpha/projects/approval_queue.py tests/test_workflow_budget_hil.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/workflow/dag_engine.py packages/harness/alpha/projects/approval_queue.py tests/test_workflow_budget_hil.py
```
Gate = targeted pytest exit 0, `ruff check` clean on the listed files, `ast_dup_gate_dups=0` (script exits non-zero on any duplicate).

### 6.6 Ordering / ownership
Depends on Gap 3 (`budget_scope` semantics + `BUDGET_EXHAUSTED` status constant live in the same file) ⇒ **sequenced 3 → 4 inside W1-B, single owner, single commit preferred (`feat(workflow): scoped budgets + HITL gates`)**.

---

## 7. Gap 5 — FACT/TIP experience bank + hygiene/dedup

### 7.1 Goal + honest-failure semantics
Typed `FACT` / `TIP` items alongside legacy `EPISODE` records, with **evidence required** for FACT/TIP, secret-shape refusal, and a hygiene job that dedups/merges/down-ranks — all auditable:
- `refusal_reason(record, require_evidence=True)` returns a real reason (secret/PII pattern label, or missing-evidence reason) and the store **refuses** the write — a refusal is surfaced to the caller, never swallowed.
- `confidence` starts at disclosed `NEUTRAL_CONFIDENCE = 0.5` and only moves via arithmetic over `success_count`/`failure_count` (`hygiene.py`); no invented confidence, no fabricated `success_rate`.
- Expired items (`is_expired()`) are excluded from injection (`relevant_for` skips them) and reported as expired rather than silently dropped from accounting.
- Hygiene mutations are never silent: `append_audit(action, detail)` on every rewrite, summarized in `HygieneReport.to_dict()` (`merged/removed/downranked/refreshed counts` + run_id entries persisted via `_persist`).

### 7.2 Design (landed in working tree — keep, then review)
- `ALPHA_PKG/learning/experience/models.py`: `ExperienceKind`, `OutcomeType`, `NEUTRAL_CONFIDENCE`, `ExperienceRecord` extended with `kind, statement, confidence, evidence, success_count, failure_count, reuse_count, expires_at, last_reviewed, hygiene_note, audit` — all optional/backward-compatible, `__post_init__` coerces enums and range-checks counters/confidence (raises `ValueError` on out-of-range ⇒ never clamps silently).
- `ALPHA_PKG/learning/experience/store.py`: `find_secret_shape(text) -> str | None`, `record_text(record)`, `refusal_reason(record, *, require_evidence)`, `ExperienceStore.record/add/_put/get/list_all/remove/clear/_save_to_disk/_load_from_disk/_load_default_experiences` (JSON persistence, existing path convention).
- `ALPHA_PKG/learning/experience/hygiene.py` (**new, untracked at HEAD**): `run_hygiene(store) -> HygieneReport` — normalized dedup (`_normalized`, `_tokens`), merge (`_merge_into` with count arithmetic), stale removal, failure down-rank, confidence refresh, persistence via `_persist(store, record, run_id, action, report)`.
- `ALPHA_PKG/learning/experience/retriever.py`: `ExperienceRetriever.retrieve/render_lessons_prompt` unchanged + additive `relevant_for(query, top_k=5, kinds=[FACT,TIP]) -> list[dict]` with `score_method="lexical_overlap_v1"` and per-item `reason`; `_compute_relevance` is the disclosed `|query ∩ item| / (|query| + 1)` heuristic.
- `ALPHA_PKG/tools/builtins/experience_tool.py`: existing builtin `consult_experience` (`wired=True`) extended in place — **no new tool, manifest unchanged**.
- Persistence: existing experience store file (atomic save already used by `ExperienceStore._save_to_disk`); hygiene writes go through the same store — no second source of truth.

### 7.3 Integration points
- `ALPHA_PKG/learning/experience/__init__.py` re-exports the new names (additive imports).
- Injection site: `ExperienceRetriever.relevant_for` is the prompt-injection API; any consumer must render `score_method` + `reason` alongside injected text (existing consumers of `render_lessons_prompt` unchanged).
- No router/tool/manifest/app.py changes.

### 7.4 Test plan
- `backend/tests/test_experience_fact_tip.py` (new) — pins: FACT/TIP **refused without evidence**; secret/PII-shaped content refused with the pattern label; confidence stays 0.5 until real reuse arithmetic; hygiene dedup merges only normalized-equal records and records audit entries; expiry honored by `relevant_for`; every injected dict carries `score_method` + `reason`; legacy EPISODE records still load (backward compat).
- Regressions: `tests/test_experience_memory.py`, `tests/test_dreaming_memory.py`, `tests/test_memory_prompt_injection.py`, `tests/test_memory_manager_interface.py`.

### 7.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_experience_fact_tip.py tests/test_experience_memory.py tests/test_dreaming_memory.py -q
uv run ruff check packages/harness/alpha/learning/experience/ packages/harness/alpha/tools/builtins/experience_tool.py tests/test_experience_fact_tip.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/learning/experience/models.py packages/harness/alpha/learning/experience/store.py packages/harness/alpha/learning/experience/hygiene.py packages/harness/alpha/learning/experience/retriever.py packages/harness/alpha/tools/builtins/experience_tool.py tests/test_experience_fact_tip.py
```

### 7.6 Ordering / ownership
Independent → **W1-A** (parallel with 1, 2, 6). Owner files: `ALPHA_PKG/learning/experience/*`, `ALPHA_PKG/tools/builtins/experience_tool.py`, `tests/test_experience_fact_tip.py`. Overlap watch: gap 6's secret-pattern work must **not** duplicate `find_secret_shape` — gap 6's registry lives in `security/memory_redaction.py`; experience store keeps its own denylist (different boundary: experience writes vs memory writes). If unification is desired, do it later as a refactor, not in this wave.

---

## 8. Gap 6 — Pre-memory-write secret filter

### 8.1 Goal + honest-failure semantics
Pattern-based redaction at the **memory write boundary** so long-term memory never receives credential-shaped content, with an honest report of what was actually replaced:
- `redact_for_memory(text, extra_patterns=()) -> RedactionResult(redacted_text, redactions=[{kind, pattern_name}], count)` — `count` = merged spans after overlap collapse, never "approximate cleanliness".
- **Disclosed coverage limits** (already in the module docstring and must stay): unknown vendor formats, high-entropy blobs without a known prefix, and base64/obfuscated values pass through **UNREDACTED**; over-redaction of session-shaped prose is possible; `extra_patterns` defaults empty and nothing is wired to config ⇒ the report must never claim "all secrets removed".
- If redaction itself errors, the write path must **fail closed for the sensitive span** (drop/truncate with `note`) or refuse the write — never "filter unavailable ⇒ store raw".
- Reports are additive metadata (`redaction_count`, `pattern_names`) on the stored item/return payload — not a fake success flag.

### 8.2 Design
**Landed (untracked, UNWIRED at snapshot):** `backend/packages/harness/alpha/security/memory_redaction.py`
- `redact_for_memory(text: str, extra_patterns: Sequence[ExtraPatternSpec] = ()) -> RedactionResult`
- `redact_mapping(payload: Any, extra_patterns=()) -> MappingRedactionResult` (also replaces values of secret-named keys; list elements inherit key context)
- Pattern families: `openai_key, github_token, aws_access_key_id, private_key, authorization_header, session_cookie, secret_assignment, env_assignment, mapping_secret_key, extra_pattern`; `REDACTION_PLACEHOLDER = "[REDACTED:{pattern_name}]"`; helpers `_find_candidates`, `_merge_candidates`, `_normalize_extra_pattern`.

**Remaining to close the gap (this is the actual work left):**
1. **Wire the boundary** in `ALPHA_PKG/agents/memory/manager.py`: import `redact_for_memory`/`redact_mapping` at module level (this is the **module-level seam tests stub** — tests monkeypatch `alpha.agents.memory.manager.redact_for_memory`) and apply in `MemoryManager.add`, `add_nowait`, `aadd` (message text parts) and in `import_memory` / `create_fact` / `update_fact` (content strings). Return/record `redaction_count` + pattern names; on filter exception, refuse the write with reason (`MemoryManagerError("memory write refused: redaction unavailable: ...")`).
2. **Wire the tool boundary** in `ALPHA_PKG/agents/memory/tools.py`: `memory_add_tool`, `memory_update_tool` run the same redaction before persisting (they are pre-existing manifest tools — **no manifest change**).
3. **Persistence:** none new (redaction is inline). If audit is wanted, write counters only — `runtime_home()/memory_redaction/audit.jsonl` via append of `{ts, kind, pattern_name, length}` (**never the matched text**), atomic per-record write; otherwise skip persistence entirely (preferred default: no new files).
4. Reference-only (do not modify): `ALPHA_PKG/runtime/secret_context.py` (`redact_metadata_secrets`, `validate_run_metadata_secrets`, `redact_config_secrets`) for consistent placeholder vocabulary, and `ALPHA_PKG/agents/middlewares/input_sanitization_middleware.py` (inbound prompt/tool-result sanitization — a *different* boundary).

### 8.3 Integration points
- `ALPHA_PKG/agents/memory/manager.py::MemoryManager.add/add_nowait/aadd/import_memory/create_fact/update_fact` (all verified to exist) + `ALPHA_PKG/agents/memory/tools.py::memory_add_tool/memory_update_tool`.
- Callers already in place (`memory_middleware.after_agent`, `client.py`, `summarization_hook.py`) require no change — they call `add()`.
- No new tool, no router change, no `app.py` edit, **`contracts/feature_manifest.json` unchanged**.

### 8.4 Test plan
- `backend/tests/test_memory_secret_filter.py` (**ABSENT at snapshot — must be written**; it is already named in the module docstring, so the docstring and test must agree): one test per pattern family (`openai_key`, `github_token`, `aws_access_key_id`, `private_key`, `authorization_header`, `session_cookie`, `secret_assignment`, `env_assignment`, `mapping_secret_key`, `extra_pattern`), plus: `max_tokens=1024` NOT redacted (false-positive guard); mapping secret-key replacement; **honesty assertions** — `count` equals actual replaced spans, report lists only real `pattern_name`s, a no-match input returns `count == 0` and unchanged text, filter-exception path refuses the write with the real error (no raw passthrough).
- Boundary tests: `MemoryManager.add` with a secret-bearing message ⇒ persisted/queued content contains `[REDACTED:` and a `redaction_count ≥ 1`; `memory_add_tool` same.
- Regressions: `tests/test_memory_manager_interface.py`, `tests/test_memory_middleware.py`, `tests/test_memory_storage.py`, `tests/test_memory_upload_filtering.py`, `tests/test_run_metadata_secret_safety.py`, `tests/test_moa_and_redaction.py`, `tests/test_skill_request_scoped_secrets.py`.

### 8.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_memory_secret_filter.py tests/test_memory_manager_interface.py tests/test_memory_middleware.py tests/test_memory_upload_filtering.py -q
uv run ruff check packages/harness/alpha/security/memory_redaction.py packages/harness/alpha/agents/memory/manager.py packages/harness/alpha/agents/memory/tools.py tests/test_memory_secret_filter.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/security/memory_redaction.py packages/harness/alpha/agents/memory/manager.py packages/harness/alpha/agents/memory/tools.py tests/test_memory_secret_filter.py
```

### 8.6 Ordering / ownership
Independent of gaps 1–5 file-wise → **W1-A** (owner: `security/memory_redaction.py`, `agents/memory/manager.py`, `agents/memory/tools.py`, `tests/test_memory_secret_filter.py`). Note: gap 5 shares the *concept* but not files — sequence review so both land before the full-suite run.

---

## 9. Gap 7 — Unified execution-mode switch (plan vs code as one global mode)

### 9.1 Goal + honest-failure semantics
One authoritative, persisted execution mode (`work.normal`, `work.plan`, `code.normal`, `code.plan`) that every existing mechanism derives from, instead of today's four uncoordinated flags:
(a) `plan_mode: bool`/`is_plan_mode` → `TodoMiddleware`, (b) `/api/plan-mode` evaluate+dispatch router, (c) `code_mode` builtin tool, (d) `policy/engine.py` side-effect verdicts.

Honest-failure semantics:
- Missing/corrupt persisted mode file ⇒ fall back to `work.normal` **with `note="default: no persisted mode found"`** — never invent a mode, never raise out of `get_mode()`.
- In `*.plan` modes, side-effecting actions return an explicit decision `("approval"|"deny", reason="plan-mode:<mode>: side effects require exiting plan mode")` — never a silent allow, never a fake "already done".
- `set_mode` returns `{mode, previous, persisted, note}`; `persisted=False` must be honestly reported if the atomic write failed (write error surfaced, not swallowed).
- Mode never fabricates capability: it does **not** grant permissions it does not have — it only constrains (plan) or leaves existing policy untouched (normal).

### 9.2 Design (nothing exists yet — full design)
New module `backend/packages/harness/alpha/runtime/execution_mode.py`:
```python
class ExecutionMode(str, Enum):
    WORK_NORMAL = "work.normal"
    WORK_PLAN   = "work.plan"
    CODE_NORMAL = "code.normal"
    CODE_PLAN   = "code.plan"

DEFAULT_MODE: ExecutionMode = ExecutionMode.WORK_NORMAL
_mode_ctx: ContextVar[ExecutionMode | None]          # request/run-scoped override

def mode_path() -> Path: ...                          # runtime_home() / "execution_mode.json"
def load_mode() -> ExecutionMode: ...                 # missing/corrupt -> DEFAULT_MODE + note (never raises)
def save_mode(mode: ExecutionMode, *, actor: str = "") -> Path: ...   # tmp file + os.replace (atomic)
def get_mode() -> ExecutionMode: ...                  # contextvar -> persisted -> DEFAULT
def set_mode(mode: ExecutionMode | str, *, actor: str = "") -> dict: ...
def mode_context(mode: ExecutionMode | str): ...      # contextmanager, restores prior value (incl. on exception)
def is_plan_mode(mode: ExecutionMode | None = None) -> bool: ...
def side_effects_allowed(mode: ExecutionMode | None = None) -> bool: ...
def plan_side_effect_decision(action: str, mode: ExecutionMode | None = None) -> tuple[Literal["allow","deny","approval"], str]: ...
```
- Data shape on disk: `{"mode": "work.plan", "updated_at": "<iso-8601>", "actor": "<who>", "note": "<why/default text>"}` — written atomically at `runtime_home()/execution_mode.json` (`runtime_home()` from `ALPHA_PKG/config/runtime_paths.py`; honors `AGENT_WORKSPACE_HOME`).
- `plan_side_effect_decision` reuses `ALPHA_PKG/policy/engine.py`'s vocabulary (`Verdict = allow|deny|approval`, `BASE_RULES` read-only prefixes `read:*/search:*/test:*/lint:*`): in plan modes, actions matching those read-only prefixes stay `allow`, everything else becomes `approval` (destructive/irreversible: `deny`) with the reason string above. **Module-level seam:** it calls `alpha.policy.engine.get_policy_engine().evaluate(action, ...)` through a module-level indirection (`_evaluate = ...`) so tests stub `alpha.runtime.execution_mode._evaluate`.
- No config schema additions (mode is persisted runtime state, not YAML schema) ⇒ **no `config_version` bump**.

### 9.3 Integration points (additive only)
1. `backend/app/gateway/routers/plan_mode.py` (**already registered** as `plan_mode.router`, prefix `/api/plan-mode`): additive `GET /api/plan-mode/mode` (read `get_mode()` + note) and `POST /api/plan-mode/mode` (body `{mode: str, actor?: str}` → `set_mode`, admin-gated with the router's existing `require_admin_user` + `_ADMIN_REQUIRED_DETAIL` pattern). Existing `/evaluate` and `/dispatch` handlers untouched.
2. `ALPHA_PKG/agents/factory.py::create_agent_workspace_agent(plan_mode=...)` and `ALPHA_PKG/client.py` (`plan_mode` at :187, `is_plan_mode` override at :284): change the parameter default to a `None` sentinel — `None` ⇒ derive from `execution_mode.is_plan_mode()`; an explicit `True/False` still wins (backward compatible, `backend/docs/plan_mode_usage.md` remains accurate). `ALPHA_PKG/agents/lead_agent/agent.py::cfg.get("is_plan_mode")` keeps reading the same key.
3. `ALPHA_PKG/policy/engine.py`: additive wrapper only (`alpha.runtime.execution_mode.plan_side_effect_decision` calls into `PolicyEngine.evaluate`); **`BASE_RULES` and `PolicyEngine.evaluate` semantics unchanged**.
4. `ALPHA_PKG/commands/catalog.py::get_default_catalog_entries()` (feeds `_register_default_catalog`): add rows `("/mode", CommandCategory.PLANNING, "Shows the current unified execution mode", "/mode [work.normal|work.plan|code.normal|code.plan]", True)` (+ optional `/mode set <value>`) and register a handler via `ALPHA_PKG/commands/backend_handlers.py::register_all_backend_handlers()` (`handle_mode(args, context) -> CommandExecutionResult`). Additive entries only; no parser rewrite (`SlashCommandRegistry.find_command/execute` unchanged).
5. Reuse, don't rebuild: `/plan approve|reject` catalog rows already exist; `code_mode` tool, `workflow_dag_manage`, `consult_experience` already in the manifest.

### 9.4 Test plan
- `backend/tests/test_execution_mode.py` (**new**): default on missing file + honest `note`; corrupt JSON ⇒ default (no raise); `set_mode` → `load_mode` round-trip; atomic write (tmp + `os.replace`; assert no `.tmp` residue); `mode_context` restores on exception; `is_plan_mode`/`side_effects_allowed` truth table for all four modes; `plan_side_effect_decision` lets `read:*` and denies/approves writes in plan modes with the `plan-mode:` reason (stub `alpha.runtime.execution_mode._evaluate`); factory derives `is_plan_mode` when kwarg is `None` and honors explicit `False/True`; endpoint payload shape via router function call (no `app.py` changes); **honesty assertions**: `GET` on a missing file returns the default with its note (never a fabricated last-mode), `POST` failure returns `persisted: false` + real error, no endpoint reports success without the persisted file containing the mode.
- Regressions: `tests/test_cognitive_plan_mode.py`, `tests/test_code_mode.py`, `tests/test_autonomous_planner.py`, `tests/test_smart_approvals_guardian.py`, `tests/test_feature_manifest_wiring.py` (manifest still 119 tools), `tests/test_slash_skill_contract.py` (command catalog regressions).

### 9.5 Gates
```powershell
$env:PYTHONPATH="."; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
uv run pytest tests/test_execution_mode.py tests/test_cognitive_plan_mode.py tests/test_code_mode.py tests/test_feature_manifest_wiring.py -q
uv run ruff check packages/harness/alpha/runtime/execution_mode.py app/gateway/routers/plan_mode.py packages/harness/alpha/agents/factory.py packages/harness/alpha/client.py packages/harness/alpha/policy/engine.py packages/harness/alpha/commands/catalog.py packages/harness/alpha/commands/backend_handlers.py tests/test_execution_mode.py
python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py packages/harness/alpha/runtime/execution_mode.py app/gateway/routers/plan_mode.py packages/harness/alpha/agents/factory.py packages/harness/alpha/client.py packages/harness/alpha/policy/engine.py packages/harness/alpha/commands/catalog.py packages/harness/alpha/commands/backend_handlers.py tests/test_execution_mode.py
```
Plus a contract check after the change: `git status contracts/` must stay empty (manifest untouched).

### 9.6 Ordering / ownership
File-disjoint from gaps 1–6 (owns `runtime/execution_mode.py`, `routers/plan_mode.py`, `agents/factory.py`, `client.py`, `policy/engine.py`, `commands/catalog.py`, `commands/backend_handlers.py`, `tests/test_execution_mode.py`) → can start in **parallel**, but it is the natural **W2** item (`docs/TASK_LIST.md` §4 already records gap 7 as *unassigned → after wave-2 review*), because it touches shared agent-construction code (`factory.py`/`client.py`) that reviewers should look at after the W1 commits settle.

---

## 10. Wave / phase plan

| Wave | Gaps | Files owned (disjoint ⇒ parallel-safe) | Notes |
|---|---|---|---|
| **W1-A (parallel, 4 owners)** | 1 | `ALPHA_PKG/skills/evolution_engine.py`, `ALPHA_PKG/config/skill_evolution_config.py`, `backend/app/gateway/routers/skills_workshop.py`, `tests/test_skill_evolution_engine.py` | already landed (uncommitted) |
| | 2 | `ALPHA_PKG/skills/retrieval.py`, `ALPHA_PKG/skills/catalog.py`, `ALPHA_PKG/learning/graph.py`, `backend/app/gateway/routers/skills.py`, `tests/test_skill_retrieval_graph.py` | already landed (uncommitted) |
| | 5 | `ALPHA_PKG/learning/experience/*`, `ALPHA_PKG/tools/builtins/experience_tool.py`, `tests/test_experience_fact_tip.py` | already landed (uncommitted) |
| | 6 | `ALPHA_PKG/security/memory_redaction.py`, `ALPHA_PKG/agents/memory/manager.py`, `ALPHA_PKG/agents/memory/tools.py`, `tests/test_memory_secret_filter.py` | **module landed; wiring + test still missing** |
| **W1-B (single owner, sequenced 3→4)** | 3 then 4 | `ALPHA_PKG/config/token_budget_config.py`, `ALPHA_PKG/agents/middlewares/token_budget_middleware.py`, `ALPHA_PKG/models/cost_governor.py`, `config.example.yaml` → then `ALPHA_PKG/workflow/dag_engine.py` (+ `ALPHA_PKG/projects/approval_queue.py` read/extend), `tests/test_token_budget_scopes.py`, `tests/test_workflow_budget_hil.py` | **shared files `dag_engine.py` + middleware ⇒ one writer, never two** |
| **W2** | 7 | `ALPHA_PKG/runtime/execution_mode.py` (new), `backend/app/gateway/routers/plan_mode.py`, `ALPHA_PKG/agents/factory.py`, `ALPHA_PKG/client.py`, `ALPHA_PKG/policy/engine.py`, `ALPHA_PKG/commands/catalog.py`, `ALPHA_PKG/commands/backend_handlers.py`, `tests/test_execution_mode.py` | unassigned at snapshot; start after W1 review |

**What the central agent must do (per wave):**
1. **Review each diff** for honesty: no fabricated scores/confidences/success; every score has `score_method`/`note`; every terminal state is an explicit status (`BUDGET_EXHAUSTED`, `WAITING_APPROVAL`, `REJECTED`, `APPROVAL_TIMED_OUT`, `refused: <reason>`, `not_configured`).
2. **Manifest verification:** `git status contracts/` empty and `contracts/feature_manifest.json` still reports **119 tools**; `git diff backend/app/gateway/app.py` empty; `git status backend/app/gateway/routers/ | measure` must show only *modified* existing routers (no added files).
3. **Run the gates per gap** (targeted `uv run pytest ... -q`, `uv run ruff check <files>`, `python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py <files>` → `ast_dup_gate_dups=0`), then the regression quartet (`tests/test_model_factory.py`, `tests/test_model_fallbacks.py`, `tests/test_models_authorization.py`, `tests/test_feature_manifest_wiring.py`) and only then a **fresh** full-suite run (the previous full run was cancelled by the server restart — see `docs/TASK_LIST.md` §2).
4. **Staged commits, one per gap** (suggested subjects): `feat(skills): skill evolution engine (propose→evaluate→gate→promote)` · `feat(skills): scored retrieval + skill relationship graph` · `feat(budget): hierarchical scoped token/cost budgets` · `feat(workflow): HITL approval gates + budget stops in DAG engine` · `feat(learning): FACT/TIP experience bank + hygiene` · `feat(security): pre-memory-write secret redaction` · `feat(runtime): unified execution mode switch`.
5. **Matrix entries:** add row **#19 "WorkSwarm gaps"** to `docs/IMPLEMENTATION_MATRIX.md` (statuses: ✅ verified / 🟡 partial / 🔴 blocked) with evidence = test log tail + commit SHA per gap; append results to `docs/TASK_LIST.md` §4 (inventory table) — per that file's rule, *"nothing is marked done without evidence (commit SHA, test log tail, or subagent session report)"*.

---

## 11. Out of scope (explicitly not planned)

- **Auth / authorization changes** — no new authz rules, no `require_admin_user` semantics changed (gap 7's `POST /mode` only *reuses* the existing admin gate).
- **New router modules** — additive endpoints only on already-registered routers (`skills.py`, `skills_workshop.py`, `plan_mode.py`); `backend/app/gateway/app.py` untouched.
- **New builtin tools** — `contracts/feature_manifest.json` must not change (119 tools before and after); reuse `consult_experience`, `workflow_dag_manage`, `code_mode`, `memory_add`, `memory_update`.
- **Frontend/Electron UI** — deferred; only revisit if a change is trivially additive (source plan §43 UI screens are explicitly deferred).
- **`config_version` bump** — not needed: no existing config field changes meaning/type; gap 3 adds only an optional `scopes` list with an empty default; gap 7 adds no YAML fields (persisted runtime state instead). If central review decides even an additive optional field warrants a bump, raise it there — default position is **no bump**.
- Source-plan items already EXISTING and therefore not re-planned here: perpetual session, goal management, swarm/swarm mode, trajectory/event store, context compaction, scheduler/heartbeat, dynamic MCP, marketplace/updates, external-agent adapters (source plan §5, §6, §9, §19–23, §40–42).

---

## 12. Honesty section (non-negotiable rules for all 7 gaps)

1. **No fabricated scores, confidences, or success.** Every numeric score is computed by a disclosed heuristic and travels with a `score_method` field plus a `note` explaining what it does *not* mean: `lexical-heuristic` (skill retrieval), `graph-lexical-heuristic` (skill chains), `lexical_overlap_v1` (experience relevance), `checks_passed/checks_total` (skill evolution validation), `NEUTRAL_CONFIDENCE = 0.5` baseline (experience confidence until real reuse arithmetic moves it).
2. **Budgets and gates end in explicit statuses, never "done":** `BUDGET_EXHAUSTED` (with `scope_id`/`budget_limit`/`consumed_tokens`/`bounders`), `WAITING_APPROVAL` (+ `waiting_node`/`approval_request_id`), `REJECTED` / `APPROVAL_TIMED_OUT` with the human's or clock's real reason, memory/experience writes `refused: <pattern label or missing evidence>` (fail-closed with reason).
3. **Unconfigured is reported, not simulated:** moderation absent ⇒ `not_configured`; no skill test suite ⇒ `validation_kind="structural-only"` + "runtime behavior NOT verified"; missing mode file ⇒ default + note; redaction limits disclosed ("does not and cannot promise all secrets are removed").
4. **Evidence-required storage:** FACT/TIP refuse to persist without `evidence[]`; skill-evolution promotion refuses without recorded evaluation evidence + explicit approval (TOCTOU hash guard).
5. **No silent mutation:** skill promotion/rollback, experience hygiene rewrites, and mode changes all leave auditable records (`events[]`, `audit[]`, `updated_at`/`actor`).
6. **Disclosures survive to the consumer:** anything injected into a prompt (experience items, retrieval candidates) carries its `score_method`/`reason`/`note` alongside the text.

---

## 13. Final checklist (for the central agent to tick)

| Gap | Planned / touched files (repo-relative) | Tests | Status |
|---|---|---|---|
| 1 — Skill evolution engine | `backend/packages/harness/alpha/skills/evolution_engine.py`, `backend/packages/harness/alpha/config/skill_evolution_config.py`, `backend/app/gateway/routers/skills_workshop.py` (additive `/evolution*`) | `backend/tests/test_skill_evolution_engine.py` (+ `test_skill_workshop.py`, `test_skill_proposals.py`) | TODO (not yet implemented) |
| 2 — Skill retrieval + graph | `backend/packages/harness/alpha/skills/retrieval.py`, `backend/packages/harness/alpha/skills/catalog.py`, `backend/packages/harness/alpha/learning/graph.py`, `backend/app/gateway/routers/skills.py` (additive `/retrieve`, `/graph`) | `backend/tests/test_skill_retrieval_graph.py` (+ `test_skill_catalog.py`, `test_learning_graph_engine.py`) | TODO (not yet implemented) |
| 3 — Hierarchical scoped budgets | `backend/packages/harness/alpha/config/token_budget_config.py`, `backend/packages/harness/alpha/agents/middlewares/token_budget_middleware.py`, `backend/packages/harness/alpha/models/cost_governor.py`, `config.example.yaml` | `backend/tests/test_token_budget_scopes.py` (+ `test_token_budget_middleware.py`, `test_reasoning_budget_governor.py`) | TODO (not yet implemented) |
| 4 — HITL workflow gates | `backend/packages/harness/alpha/workflow/dag_engine.py`, `backend/packages/harness/alpha/projects/approval_queue.py` (queue of record; `orchestrator/approvals.py` = custody only) | `backend/tests/test_workflow_budget_hil.py` (+ `test_workflow_dag_engine.py`) | TODO (not yet implemented) |
| 5 — FACT/TIP experience bank + hygiene | `backend/packages/harness/alpha/learning/experience/{models,store,hygiene,retriever,__init__}.py`, `backend/packages/harness/alpha/tools/builtins/experience_tool.py` | `backend/tests/test_experience_fact_tip.py` (+ `test_experience_memory.py`, `test_dreaming_memory.py`) | TODO (not yet implemented) |
| 6 — Pre-memory-write secret filter | `backend/packages/harness/alpha/security/memory_redaction.py` (landed, **unwired**), `backend/packages/harness/alpha/agents/memory/manager.py`, `backend/packages/harness/alpha/agents/memory/tools.py` | `backend/tests/test_memory_secret_filter.py` (**not yet created**) + `test_memory_manager_interface.py`, `test_memory_middleware.py` | TODO (not yet implemented) |
| 7 — Unified execution mode switch | `backend/packages/harness/alpha/runtime/execution_mode.py` (**new**), `backend/app/gateway/routers/plan_mode.py` (additive `GET/POST /api/plan-mode/mode`), `backend/packages/harness/alpha/agents/factory.py`, `backend/packages/harness/alpha/client.py`, `backend/packages/harness/alpha/policy/engine.py` (wrapper only), `backend/packages/harness/alpha/commands/{catalog,backend_handlers}.py` | `backend/tests/test_execution_mode.py` (**not yet created**) + `test_cognitive_plan_mode.py`, `test_code_mode.py`, `test_feature_manifest_wiring.py` | TODO (not yet implemented) |
| Central gates | — | targeted `uv run pytest` per gap → regression quartet → fresh full suite; `uv run ruff check <files>`; `python C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py <files>` → `ast_dup_gate_dups=0`; `git status contracts/` empty (manifest = 119 tools); `git diff backend/app/gateway/app.py` empty | TODO (not yet implemented) |
| Matrix / task list | `docs/IMPLEMENTATION_MATRIX.md` row **#19 WorkSwarm gaps**; `docs/TASK_LIST.md` §4 evidence (SHA + test-log tail) | — | TODO (not yet implemented) |

---

*Prepared 2026-09-23 (reality snapshot 17:31 local). Every file path above was verified to exist at snapshot time except the ones explicitly marked "not yet created" (`backend/tests/test_memory_secret_filter.py`, `backend/tests/test_execution_mode.py`) and the two files marked "new" for gap 7 — those are the only paths this document asserts do **not** yet exist.*
