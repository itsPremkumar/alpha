# Alpha — Sprawl & Duplication Audit

Author: red-team critic (alpha-critic profile). Read-only pass; no production code touched.
All findings below are **MEASURED** via ripgrep (`search_files`) and `wc -l`, run 2026-10-05.
Write scope honoured: only this file was created.

Repo paths resolved to `backend/packages/harness/alpha/` — there is no top-level `harness/`
directory. `harness/alpha/...` in the audit brief means `backend/packages/harness/alpha/...`.

---

## 1. `alpha/avo/` — verdict: LOAD-BEARING (thin surface, real prod wiring)

MEASURED. 15 files, 3,518 LOC.

| file | LOC |
|---|---|
| commit_gate.py | 451 |
| workspace_runner.py | 431 |
| lineage.py | 327 |
| scorer_authority.py | 300 |
| evidence.py | 278 |
| supervisor.py | 272 |
| honesty.py | 262 |
| persistence.py | 212 |
| knowledge.py | 174 |
| variation_agent.py | 167 |
| trajectory.py | 167 |
| engine.py | 145 |
| lineage_chain.py | 126 |
| scoring.py | 125 |
| __init__.py | 81 |

**Non-test importers: 3 files.**

- `backend/app/gateway/routers/projects.py:1037,1253,1273` — `get_avo_runner`, `VersionRecord`
- `alpha/tools/builtins/variation_operator_tool.py:11,16,17`
- `alpha/tools/builtins/avo_lineage_tool.py:9`

Test-only importers: 10 files (`test_avo_decision_layer.py`, `test_avo_agent.py`,
`test_avo_architecture.py`, `test_avo_evolution_lineage.py`, `test_avo_supervisor.py`,
`test_avo_scoring.py`, `test_avo_knowledge.py`, `test_avo_workspace_safety.py`,
`test_avo_transfer_and_ab.py`, `test_asi_core.py`).

Verdict: **LOAD-BEARING**, but with the narrowest possible production surface — 3 caller
files and 6 import sites total, versus 28 test import sites. The package is registered in
`contracts/feature_manifest.json:2044` (`"id": "alpha.avo"`) and surfaces two tools
(`run_avo_variation` at manifest:871). It is reachable, not dead. But ~90% of its
maintenance surface is exercised only by its own tests; a reviewer should treat the
test-to-prod ratio (28:6) as a signal that the subsystem is under-integrated, not proven.

Not DORMANT. Not broad. Six lines of glue.

---

## 2. `alpha/memory/` vs `alpha/agents/memory/` — both exist; NOT duplicates

MEASURED.

| package | files | LOC |
|---|---|---|
| `alpha/memory/` | 148 | 42,952 |
| `alpha/agents/memory/` | 58 | 17,073 |

**They are layered, not parallel, and the dependency direction is unambiguous.**

`alpha/memory/*` imports `alpha/agents/memory/*` in **15+ distinct files** — the rich
knowledge layer is built ON TOP of the agent layer:

- `memory/affective/paths.py:12`, `memory/entities/paths.py:7`, `memory/narrative/paths.py:14`,
  `memory/fabric/paths`-equivalents, `memory/prospective/store.py:16`, `memory/policy/loader.py:21`,
  `memory/social/config.py:18`, `memory/utility/paths.py:16`, `memory/fusion/provenance.py:13`,
  `memory/scenarios/config.py:21` — all `from alpha.agents.memory.l1.paths import ...`
- `memory/affective/recall.py:10` and `memory/fusion/diversity.py:9` → `...l1.store.tokenize`
- `memory/affective/extraction.py:19`, `memory/entities/extraction.py:19` → `...l1.extractor.message_text`
- `memory/recall_composition.py:57` → `alpha.agents.memory.recall_safety`

A duplicate pair cannot have a one-way 15-file dependency. **These are genuinely different:**

- `alpha/agents/memory/` = *agent-scoped conversational memory*: a `MemoryManager` façade,
  the `l1/` capture pipeline (extract → dedup → gates → quota → store), and 7 pluggable
  external backends (`mem0`, `mem0oss`, `honcho`, `deermem`, `openviking`, `fullmemory`, `noop`).
- `alpha/memory/` = *cognitive/knowledge memory*: cognitive tiers (episodic/semantic/
  procedural/working/spatio-temporal/associative) plus affective, social, entities,
  narrative, fabric, prospective, policy, scenarios, fusion, dreaming, utility,
  evaluation, health, codebase, wiki_vault subsystems.

**Non-test importer counts** (outside the packages themselves):

- `alpha.agents.memory.*` — 11 prod files: `app/gateway/app.py`, `routers/agents.py`,
  `routers/memory.py`, `alpha/client.py`, `agents/factory.py`, `agents/lead_agent/{agent,prompt}.py`,
  `agents/middlewares/{memory,l1_memory,dynamic_context,learning_fork,user_model,summarization}_middleware.py`
- `alpha.memory.<subpkg>.*` — 7 prod files: `config/memory_config.py`,
  `agents/memory/backends/fullmemory/fullmemory_manager.py` (wiki_vault),
  `orchestrator/memory_recall.py` (dreaming), `tools/discovery/search.py`,
  `tools/builtins/{cognitive_memory_tool,cognitive_memory_tiering_tool,dreaming_tool}.py`,
  `agents/lead_agent/prompt.py:899`

**Verdict: KEEP BOTH. The naming is the real defect** — `alpha/memory` vs
`alpha/agents/memory` reads as duplication and will keep producing false positives in
every audit, including this one. Rename candidate: `alpha/knowledge_memory/` or
`alpha/memory/cognitive/…` collapsed up. This is a naming/packaging debt item, not a
duplication item.

**Real duplication found here instead:** `alpha/agents/memory/l1/paths.py` has been
copy-pasted into at least 11 sibling packages that each re-export `atomic_write_text`,
`safe_segment`, `l1_root` rather than importing the original — `memory/health/paths.py`,
`memory/codebase/paths.py`, `memory/narrative/paths.py`, `memory/entities/paths.py`,
`memory/social/paths.py`, `memory/fabric/config.py`, `memory/prospective/{config,paths,store,provenance}.py`,
`memory/policy/{loader,provenance}.py`, `memory/scenarios/{config,provenance}.py`,
`memory/utility/paths.py`, `memory/fusion/provenance.py`, and `avo/persistence.py:51`
(which reaches into `agents.memory.backends.deermem.deermem.core.storage._atomic_write` —
a **private** symbol from a sibling package). That last one is the sharpest finding here:
`_atomic_write` imported across a package boundary is a refactor landmine.

---

## 3. Ten largest `harness/alpha` subpackages by file count

MEASURED (`find -name '*.py' | wc -l`, then `wc -l` for LOC).
LOAD-BEARING = real production callers found under `backend/app/` or another package.
SPECULATIVE = thin or indirect callers only.

| # | package | files | LOC | verdict | evidence |
|---|---|---|---|---|---|
| 1 | `tools/` | 164 | 28,567 | **LOAD-BEARING** | `alpha.tools.tools.BUILTIN_TOOLS` imported by `routers/models.py:591`, `app.py` tool setup, `checkpoints.py:32`, `agents/factory.py` |
| 2 | `memory/` | 148 | 42,952 | **LOAD-BEARING** | `config/memory_config.py:27-39` imports 13 sub-configs; `orchestrator/memory_recall.py:31`; 3 builtin tools; `routers/memory.py:645` |
| 3 | `agents/` | 135 | — | **LOAD-BEARING** | 11 prod files incl. `agents/factory.py`, `agents/lead_agent/agent.py`, `client.py:1551-1825`, 5 middlewares |
| 4 | `runtime/` | 107 | 31,227 | **LOAD-BEARING** | heaviest: `deps.py:36-47,522-526`, `services.py:33-74`, `health.py:115,176`, `run_recovery.py:25-26`, `scheduler/service.py:12-14` |
| 5 | `persistence/` | 103 | 18,180 | **LOAD-BEARING** | `deps.py:34-35,405,522,603-688` (users, feedback, tokens, runs, scheduled tasks, subagent batches), `health.py:38,202` |
| 6 | `community/` | 85 | 25,866 | **LOAD-BEARING** | 7 prod files: `routers/browser.py:91,245`, `deps.py:30`, `browser_capability.py:9`, `threads.py:790`, `routers/models.py:9` (url_safety) |
| 7 | `config/` | 78 | 13,004 | **LOAD-BEARING** | 60 import sites in `backend/app` alone — `app_config`, `paths`, `agents_config`, `auth_config` pervasive |
| 8 | `skills/` | 57 | 11,050 | **LOAD-BEARING** | `routers/skills.py:31-55,946-1223` (~20 sites), `channels/manager.py:29-31`, `middlewares/skill_activation_middleware.py:33-36` |
| 9 | `workflow/` | 42 | 14,515 | **LOAD-BEARING** | `routers/workflows.py:15-26` (12 module-level imports) + `:364-455,969,1058` |
| 10 | `models/` | 36 | 11,420 | **LOAD-BEARING** | `routers/models.py:12,395,428,570,714,773,968,1010`, `autonomy/loops.py:163`, `middlewares/citation_support.py:25` |

Just outside the top 10: `subagents/` (34, 10,014) LOAD-BEARING (`routers/subagent_control.py:17-23`,
`routers/subagents.py:21`, `agents/factory.py:37`), `sandbox/` (33, 10,719) LOAD-BEARING
(`routers/uploads.py:19`, `channels/sandbox_files.py:5`, `authz.py:443-537`),
`projects/` (32, 6,007) LOAD-BEARING but mostly via one router (`routers/projects.py`, ~60 sites)
plus `routers/bots.py:839,1170` and `routers/benchmarks.py:23`.

**No package in the top 10 is SPECULATIVE.** That is the honest and somewhat unwelcome
finding: the sprawl is not built from dead top-level packages. `avo/` (15 files) is the
only subsystem in this audit whose prod surface is genuinely narrow.

---

## What I could NOT verify

- No caller counts for intra-package imports were separated from cross-package ones;
  the SPECULATIVE/LOAD-BEARING split here is based on `backend/app/` + cross-package
  evidence only. A package used solely by another harness package (e.g. `harness/`)
  would be misclassified as SPECULATIVE.
- I did not run pytest, so "load-bearing" means *imported*, not *exercised at runtime*.
- `search_files` `output_mode="files_only"` returned 0 matches repo-wide (appears
  broken on this host); all counts are from `output_mode="content"` totals instead.
- `git` was never invoked, per BUG_FIX_BOARD rule 1.

## Verdict

**Alpha is structurally better than its sprawl suggests.** Zero speculative packages in
the top 10, ~90 subpackages all with real gateway callers, and a self-critical doc culture
that has already found some of its own duplication. Where it is weak, it is weak in
*naming and cross-package symbol discipline*, not in dead weight: 148 files of
`alpha/memory/` versus 58 of `alpha/agents/memory/` reads as duplication to every human
and every tool, and 11 hand-rolled copies of `atomic_write_text`/`safe_segment` plus an
import of a private `_atomic_write` across a package boundary are the concrete cost.
Fix the packaging and the illusion of bloat largely disappears.