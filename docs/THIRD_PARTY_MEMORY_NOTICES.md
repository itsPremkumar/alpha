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

## Hy-Memory 2.0 (Apache-2.0) — history-retention contract adopted

- Upstream: <https://github.com/Tencent-Hunyuan/Hy-Memory> (Python SDK
  `hy-memory==2.0.2`, released October 2026); DCPM paper:
  <https://arxiv.org/abs/2606.09483>
- License: **Apache-2.0** (verified against the repository) — eligible with
  attribution; per `docs/ALPHA_HY_MEMORY_2_ADOPTION_PLAN.md`.
- Why: Hy-Memory's history-preserving fact revision — when a memory is revised
  or merged, the superseded value is retained as a bounded chain on the
  surviving memory instead of being destroyed — fixes a real defect in
  replace-on-write memories (the prior value's recency/audit value is lost,
  and recall can no longer answer "what did it say before?"). Alpha's L1
  dedup otherwise REPLACES targets in real time with no ancestry.
- Adaptation policy: **no code copied**. Alpha implements the contract in
  original English Python on its own `MemoryRecord` model, local JSON store,
  and recall-safety seam (see `docs/ALPHA_HY_MEMORY_2_ADOPTION_PLAN.md`,
  Phase 1 L1 slice). Divergences from the upstream design are deliberate:
  the history chain is a bounded record field (`MAX_HISTORY_ENTRIES`,
  char-capped per entry) rather than a separate memory store; it is written
  only by the dedup seam's update/merge path under the default-off
  `memory.l1.history_kept` gate; every entry passes the same
  redaction-before-persistence boundary as record content; and the recall
  tail is injected through `recall_safety.contain_recalled_text` (single
  line, capped, `</memory>` neutralized), so history text can never carry
  more authority through the prompt seam than current records.
- Where the contract lives:
  `backend/packages/harness/alpha/agents/memory/l1/models.py`
  (`MemoryHistoryEntry`, `build_history_entry`), `.../l1/dedup.py`
  (`_inherited_history`, `apply_decisions(..., history_enabled=)`),
  `.../l1/store.py` (extended redaction boundary), `.../l1/pipeline.py`
  (recall tail, `RunReport.history_entries`).
- Tests: `backend/tests/test_l1_history.py`,
  `backend/tests/test_memory_evaluation.py` (the `alpha-update-003`
  history-retention and `alpha-extraction-003` assistant-said families ride
  the closed evaluation vocabulary; the "assistant said" family mirrors the
  LongMemEval subset named `assistant said`, not a copied question set).

## Adoption slices #1–#4 (2026-10-17) — scholarly lineages, NO code copied

Four default-off, bounded upgrades on Alpha's existing L1/fusion read paths,
adapted from published systems and papers as **contracts only** (no code
copied from any of them; every mechanism below is original Python on Alpha's
own stores and seams):

| Slice | Gate | Upstream idea | Source | Alpha adaptation |
| --- | --- | --- | --- | --- |
| #1 staleness propagation | `memory.l1.staleness_propagation` | "state drift": derived records survive the supersession of the fact they were extracted from; the fix marks dependents for re-derivation instead of trusting stale artifacts | StateMem — *Can Agent Memory Systems Track Evolving State?* <https://arxiv.org/abs/2608.19652> | deterministic, LLM-free MARKING pass: the dedup seam reports store-confirmed supersession ids, the pipeline walks the entity mention lineage Alpha already writes, and dependent entity ids land as bounded id-only journal rows under `users/{u}/l1/staleness/YYYY-MM-DD.jsonl` (≤32 dependents per batch, `truncated` flag; content never enters the journal; nothing deletes anything) |
| #2 conflict-candidate expansion | `memory.l1.conflict_expansion` | the hard regime is the IMPLICIT conflict ("export moved to Wednesdays" never lexically negates "runs on Mondays"); conflict detection can only act on the candidates recall gives it, so widening the recalled pool past the top-k cut with same-subject/different-value shapes lets the adjudicator see the conflict | STALE — *Can LLM Agents Know When Their Memories Are No Longer Valid?* and the CUPMem prototype <https://arxiv.org/abs/2605.06527> | when the gate is on, dedup recall also surfaces up to 5 lower-ranked candidates passing a deterministic two-shared-tokens/different-value shape prefilter; the prefilter is over-inclusive by design — the LLM adjudicator, not the prefilter, decides what a conflict is |
| #3 as-of recall read | tool parameter `memory_search(as_of=...)` (no config key) | bi-temporal reads: answer "what did the system know at time T?" from `valid_at`/`invalid_at`-style knowledge times | Zep / Graphiti <https://arxiv.org/abs/2501.13956> | query-time projection only: the tool parses an ISO-8601/unix cutoff (refusing unparseable values) and the fused controller applies ONE central filter — a candidate dated after the cutoff (episode `event_time`, fact `createdAt`→`valid_from`) is dropped; undated candidates are kept ("unknown time" is not "future knowledge"); fully-filtered stages report `empty` with the cutoff in their reason; with `memory.fusion.enabled` off the parameter is refused, never ignored |
| #4 entity/recency signal boost | `memory.l1.signal_boost` | fusing structured identifier matches and recency with lexical recall so an identifier-shape match (`AuthService` vs `authservice`) or a fresh value outranks a stale tie | Mem0 — *Building Production-Ready AI Agents with Scalable Long-Term Memory* <https://arxiv.org/abs/2504.19413> | additive rank terms inside the relevance window only: case-sensitive identifier tokens (weight 0.15) and a bounded 14-day linear recency term (weight 0.10); the base hybrid score still decides the top-k window and the `> 0` relevance gate, so the boost reorders genuinely relevant records and can never admit an irrelevant one |

Common honesty boundaries for all four: default OFF via `memory.l1.*` gates
(slice #3 is a caller-side parameter with no config key); no new tool names;
`RunReport.stale_dependents` is the only new report field; the staleness
journal is an observation channel nothing reads back for decisions this
release; and the temporal projection never deletes or rewrites stored facts.

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
| `evaluation` | benchmark design only; **no LoCoMo / LongMemEval / MemBench dataset or question set was copied or bundled** — all 19 JSON cases are original, and LongMemEval is referenced solely as the name of the five-ability taxonomy (Hy-Memory's "assistant said" family is mirrored as an original case, see the section above; the implicit-invalidation family rides the STALE/CUPMem finding as a contract, with 100% original case content) | none |

Two shared conventions are reused by import from inside this repository (not
third-party code): `alpha.agents.memory.l1.paths` (`l1_root`, `safe_segment`,
`atomic_write_text`) and `alpha.memory._lazy_exports.install_lazy_exports`.
Copyleft projects (basic-memory AGPL-3.0, OpenViking AGPLv3) remain ideas-only
and contributed nothing to these packages.
