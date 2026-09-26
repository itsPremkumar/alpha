# Third-party notices — memory subsystem

This file records every piece of third-party source adapted into Alpha's
memory subsystem, with its license and exactly what changed. No whole project
is vendored; the ported layer is original Python that follows the contracts
below. Keep this table in sync when memory code is added or re-licensed.

## TencentDB-Agent-Memory (MIT)

- Upstream: <https://github.com/TencentCloud/TencentDB-Agent-Memory>
- License: **MIT**, Copyright (C) 2026 Tencent (LICENSE at the repository root)
- Why: its L1 working-memory layer — scene segmentation + typed extraction +
  batch conflict detection (store/skip/update/merge) + quota + generation log
  + persona synthesis + retention — is the closest permissively-licensed
  reference for the typed-memory pipeline now in `alpha.agents.memory.l1`.
- Adaptation policy: upstream is TypeScript bound to TencentDB-hosted stores.
  **None** of its storage, SDK, cloud, or gateway code was copied. What was
  taken is the *contract* and the prompt semantics, re-expressed as original
  English Python for Alpha (prompts translated from the upstream Chinese;
  classes, storage layout, threading, and error handling written for this
  codebase). Alpha's L1 store is a local per-user JSON document store; the
  cloud quota reporter is replaced by local `quota.json` accounting.

| Alpha file | Upstream file (MIT) | What was adapted |
|---|---|---|
| `agents/memory/l1/prompts.py` | `MemoryCore/src/core/prompts/l1-extraction.ts`, `l1-dedup.ts`, `persona-generation.ts` | Extraction / conflict-detection / persona prompt contracts, translated to English and rebuilt as system+user prompt builders with `chat` and `work` dialects |
| `agents/memory/l1/extractor.py` | `MemoryCore/src/core/record/l1-extractor.ts` | One-call scene-segmentation + extraction run shape, strict-JSON response contract, background-context batching, tolerant text extraction |
| `agents/memory/l1/dedup.py` | `MemoryCore/src/core/prompts/l1-dedup.ts`, `record/l1-writer.ts` | `store` / `skip` / `update` / `merge` decision set, many-to-many `target_ids`, cross-type merge, timestamp-union bookkeeping, fail-open to `store` so content is never dropped |
| `agents/memory/l1/store.py` | `MemoryCore/src/core/record/l1-writer.ts`, `store/bm25-local.ts`, `store/search-utils.ts` | Record identity / versioning / timestamp-trail semantics and hybrid BM25 + similarity recall (re-implemented in dependency-free Python; no line-for-line copy) |
| `agents/memory/l1/quota.py` | `MemoryCore/src/core/quota/quota-manager.ts`, `quota/credit-calculator.ts` | Record + cumulative-credit quota checks (`memory_limit_exceeded`, `credit_limit_exceeded`) and the credit rate formula / model multiplier table |
| `agents/memory/l1/provenance.py` | `MemoryCore/src/core/memory-generation-log/` | Per-run append-only generation log: one entry per run, outcome always recorded (`succeeded` / `failed` / `skipped`) |
| `agents/memory/l1/cleaner.py` | `MemoryCore/src/utils/memory-cleaner.ts` | Age-based retention sweep (plus an added record-count cap, lowest-priority-oldest first) |
| `agents/memory/l1/persona.py` | `MemoryCore/src/core/persona/persona-generation.ts`, `persona-trigger.ts` | Incremental persona profile synthesis from persona-type memories (existing profile + new memories + change note, ≤ 2000 chars) |
| `agents/memory/l1/pipeline.py` | run shape only: `l1-extractor.ts`, `l1-dedup.ts`, `l1-writer.ts`, `quota/`, `memory-generation-log/`, `persona/` | Original Python orchestration: debounced capture, processed-message cursor (advances only after a usable extraction), quota before the LLM call and before writes, batch dedup, retention sweep, persona refresh, per-run provenance, recall seam |
| `agents/memory/l1/models.py`, `parser.py`, `paths.py`, `gates.py` | contracts from the files above | Original English data model (typed memories, priority band incl. the `-1` strict-order sentinel), tolerant response parsers with closed status sets, on-disk layout, two-level enable gate |

MIT requires the copyright notice to travel with the work: every adapted
module carries a "Design provenance" docstring naming its upstream files, and
`agents/memory/l1/__init__.py` carries the package-level notice. Upstream
attribution:

```text
TencentDB Agent Memory
Copyright (C) 2026 Tencent
Licensed under the MIT License
```

## Reviewed agent-memory projects — licenses verified, NO code copied

`references/ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM.md` names fifteen
agent-memory projects plus storage engines. Before anything could be reused,
each license was verified against the repository itself
(`gh api repos/<owner>/<repo>/license`, checked 2026-09-25) rather than trusted
from prose. **No code from any project below is present in this tree**; the
table records why each was or was not eligible, so a future change does not
have to re-derive the decision.

| Project | License (verified) | Copying into Alpha |
|---|---|---|
| Letta / MemGPT | Apache-2.0 | Eligible with attribution — concepts adopted (tiering) |
| Mem0 | Apache-2.0 | Eligible with attribution — concepts only so far (durable-fact extraction) |
| MemOS | Apache-2.0 | Eligible with attribution — concepts only (memory-OS orchestration) |
| Graphiti / Zep | Apache-2.0 | Eligible with attribution — concepts only (temporal graph) |
| MemMachine | Apache-2.0 | Eligible with attribution — concepts only (episodic/profile/working split) |
| Memori | Apache-2.0 | Eligible with attribution — concepts only (execution-aware capture) |
| Cognee | Apache-2.0 | Eligible with attribution — concepts only (document/code graph) |
| LangMem | MIT | Eligible with attribution — concepts only (pluggable memory primitives) |
| LlamaIndex | MIT | Eligible with attribution — not used |
| Haystack | Apache-2.0 | Eligible with attribution — not used |
| A-MEM | MIT | Eligible with attribution — concepts only (linked notes) |
| SimpleMem | MIT | Eligible with attribution — concepts only (semantic-lossless compression) |
| DeerFlow | MIT | Eligible with attribution — concepts only (long-horizon memory integration) |
| memodb-io / Memobase | Apache-2.0 | Eligible with attribution — not used |
| **basic-memory** | **AGPL-3.0** | **NOT eligible — copyleft: ideas only, never copy** |
| **OpenViking** (main project) | **AGPLv3** | **NOT eligible — copyleft: resource/memory/skill separation adopted conceptually only** |

Storage and retrieval engines named by the plan, all permissive and therefore
eligible if ever adopted as optional backends: FAISS (MIT), Qdrant (Apache-2.0),
Milvus (Apache-2.0), Weaviate (BSD-3-Clause), LanceDB (Apache-2.0),
pgvector (PostgreSQL-style permissive), Apache AGE (Apache-2.0), DuckDB (MIT).
None is a dependency today: Alpha's canonical memory remains the local
per-scope store plus the pluggable `MemoryManager` backend protocol, so a
vector/graph engine can be added later without rewriting the agent.

## Wave-2 memory packages — ORIGINAL Alpha code, zero third-party code

The ten additive memory packages built in the second wave
(`alpha/memory/{affective,prospective,entities,social,narrative,policy,fusion,scenarios,fabric,evaluation}/`)
contain **no copied third-party source**. Every line is original Alpha code
written for this repository. What they do carry is *attribution for the design
lineage* — the paper or project whose idea informed a contract — recorded here
so a future reader can tell inspiration from copying.

| Package | Design lineage cited in its own docs | Code copied |
|---|---|---|
| `affective` | valence/arousal affect models; mood-aware retrieval | none |
| `prospective` | prospective-memory reminder/obligation lifecycles | none |
| `entities` | alias resolution and entity-linked recall | none |
| `social` | shared/collaborative memory with audience scoping | none |
| `narrative` | *Generative Agents: Interactive Simulacra of Human Behavior* (Park et al., UIST 2023) — reflection + importance-weighted retrieval | none |
| `policy` | admission control, fail-closed secret rejection, hot-reloadable policy documents | none |
| `fusion` | multi-stage retrieval, reciprocal-rank fusion, MMR diversification | none |
| `scenarios` | context/scenario-conditioned retrieval routing | none |
| `fabric` | canonical-envelope + lifecycle/temporal-integrity storage discipline | none |
| `evaluation` | benchmark design only; **no LoCoMo / LongMemEval / MemBench dataset or question set was copied or bundled** — all 16 JSON cases are original, and LongMemEval is referenced solely as the name of the five-ability taxonomy | none |

Two shared conventions are reused by import from inside this repository (not
third-party code): `alpha.agents.memory.l1.paths` (`l1_root`, `safe_segment`,
`atomic_write_text`) and `alpha.memory._lazy_exports.install_lazy_exports`.
Copyleft projects (basic-memory AGPL-3.0, OpenViking AGPLv3) remain ideas-only
and contributed nothing to these packages.
