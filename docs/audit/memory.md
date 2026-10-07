# Audit: is `alpha.memory` wired into the running agent?

Method: ripgrep (`search_files`) only, no execution, no git. All findings below are
MEASURED-as-static (the symbol and its call chain exist and are reachable in source);
whether the branch executes at runtime depends on config, which I did not run.

## 1. Non-test importers (production code)

| Importer | file:line | What it pulls |
|---|---|---|
| `alpha/agents/lead_agent/prompt.py` | 899, 958 | `alpha.memory.cognitive.get_cognitive_memory_system`, `alpha.memory.recall_composition.compose_typed_memory_blocks` |
| `app/gateway/routers/memory.py` | 645, 684, 763 | `alpha.memory.cognitive` (system, `HybridRecallQuery`, `BeliefStatus`) |
| `alpha/config/memory_config.py` | 27-39 | 12 `alpha.memory.<type>.config` model classes |
| `alpha/capabilities/catalog.py` | 118 | `alpha.memory.active_memory` (capability descriptor) |
| `alpha/workflow/registry/memory.py` | 31-36 | importability probes only |
| `alpha/agents/memory/backends/fullmemory/fullmemory_manager.py` | 70,82,164,192 | `alpha.memory.wiki_vault.ensure_user_vault` |
| `alpha/orchestrator/__init__.py` | 40 | re-exports `CrossThreadRecallConfig`, `trigger_dream_cycle` from `alpha.orchestrator.memory_recall` |

`memory_recall` as a *name*: only 4 non-test sites, and **none is a memory read**:
- `observability/writer.py:1080` def `memory_recall(...)` — emitter
- `observability/trace/instrumentation.py:805` `emit_memory_recall` — emitter
- `observability/trace/coverage.py:106` layer->emitter map
- `safety/authority/taint.py:77` `MEMORY_RECALL = "memory_recall"` — enum

## 2. Real read vs instrumentation

**Real read, in the hot path (MEASURED, static).**
`alpha/agents/middlewares/dynamic_context_middleware.py:409-420` calls
`lead_agent.prompt._get_memory_context(agent_name, app_config, user_id)`.
That function (`prompt.py:858-974`) does, in order:
1. `alpha.agents.memory.get_memory_manager().get_context(...)` — the real provider read.
2. cognitive enrichment (working memory + procedural skills), **only if step 1
   returned non-empty** — a deliberate anti-fabrication guard at :919.
3. L1 typed working memory (`agents/memory/l1/pipeline.py`) when `l1_enabled(config)`.
4. wave-2 typed blocks via `alpha.memory.recall_composition.compose_typed_memory_blocks`.

So `alpha/memory/` **is** on the per-turn injection path. It is not a parallel
universe. But note the honest shape of the win: cognitive/L1/typed layers are
*enrichment appended to* the `agents/memory` provider read, and every layer is
wrapped in `except Exception: logger.debug(...)` (:922, :950, :969) — a silent
degradation. Wave-2 types are default-OFF, so with defaults they append nothing.

## 3. Per-symbol verdict

- `alpha.memory.cognitive` — **LOAD-BEARING** (prompt.py:899, routers/memory.py:645)
- `alpha.memory.recall_composition` — **LOAD-BEARING, gated** (prompt.py:958)
- `alpha.memory.wiki_vault` — **LOAD-BEARING, conditional** (fullmemory manager only)
- `alpha.memory.active_memory` — **DORMANT** in the agent: catalog.py:118 declares
  the capability; only importer is `tests/test_active_memory.py`
- `alpha.memory.capture_composition` — **DORMANT** (orphan allowlist,
  `contracts/inert_declarations_allowlist.v1.json:131`; tests
  `test_memory_capture_composition.py`, `test_memory_capture_integration.py` only)
- `alpha.memory.fusion / health / codebase / policy / scenarios / social / narrative /
  entities / affective / prospective / utility` via `config/memory_config.py` —
  **DORMANT**: config-only imports, declared inert in the allowlist at lines 81-107.
  Config is live; behaviour is not.
- `memory_recall` (symbol) — **DORMANT as a read**; instrumentation + enum only.
- `CrossThreadRecallConfig` / `trigger_dream_cycle` — **DORMANT**: exported from
  `orchestrator/__init__.py:40,65,94`, sole consumer is
  `tests/test_orchestrator_additions.py:14,117`. Re-export is not a call path.

## 4. Both directories exist — different roles, not duplicates

- `alpha/memory/` — typed/domain memory: 166 files, 17 subpackages
  (affective, cognitive, entities, fusion, health, narrative, policy, prospective,
  scenarios, social, utility, codebase, dreaming, wiki_vault, active_memory, ...)
  plus `recall_composition.py` / `capture_composition.py` seams.
- `alpha/agents/memory/` — the operational runtime: `MemoryManager` ABC (9
  methods), pluggable backends (`deermem/`, `mem0/`, `openviking/`, `honcho/`,
  `fullmemory/`), `tools.py` (`memory_search/add/update/delete`),
  `l1/` pipeline, `recall_safety.py`, `summarization_hook.py`.

**Different roles, real coupling.** `agents/memory/__init__.py:18` says so explicitly
("live in the sibling `alpha.memory` package"), and it consumes
`alpha.memory._lazy_exports` + `wiki_vault`. `agents/memory/tools.py:24` and
`factory.py:314-315` put `get_memory_tools` into live agent construction; gateway
routes in `routers/memory.py:11` and `app.py:341,366,879` plus `client.py:1551-1825`
drive capture/recall. This is layering, not duplication — though the two planes
share no storage contract, which is the sharp edge.

## Not verified
Runtime execution, config defaults actually loaded, storage backends (SQLite/
Postgres/Redis), migration runner, and non-atomic write/fsync behaviour — all out of
scope for a ripgrep-only pass and all unexamined here.