# Alpha × Hy-Memory 2.0 adoption plan

This document maps Tencent Hunyuan's open-source **Hy-Memory 2.0** agent-memory
system (Apache-2.0, `hy-memory==2.0.2`, released October 2026) feature-by-feature
onto Alpha's **actually implemented** memory stacks, gives each Hy-Memory
capability an EXISTS / PARTIAL / ABSENT verdict against Alpha's code, and defines
the phased, default-off adoption plan plus the adjacent work Alpha needs
regardless of Hy-Memory. It is a research and planning document: nothing in it
claims a shipped Alpha feature, and every Alpha-side status below is evidenced by
file paths and the runtime audit.

Research snapshot: **2026-10-17**.

## What is Hy-Memory 2.0?

Hy-Memory 2.0 is Tencent Hunyuan's production-oriented **dual-process cognitive
memory engine** for LLM agents: a Python SDK (`hy-memory`), an open-weight
memory-writing model, and MemHub (a local-first cross-app memory hub). Sources:
[GitHub monorepo](https://github.com/Tencent-Hunyuan/Hy-Memory),
[SDK on PyPI](https://pypi.org/project/hy-memory/2.0.2/),
[Hy-Memory-30B-A3B on Hugging Face](https://huggingface.co/tencent/Hy-Memory-30B-A3B),
[DCPM paper (EMNLP 2026)](https://arxiv.org/abs/2606.09483),
[30B model report](https://arxiv.org/abs/2610.22196).

| Scene (`scene=`) | Task kind | Memory shape |
|---|---|---|
| `chat` | Conversation, preferences, personal info | Hierarchical L0–L7 (basic info, raw, atomic *versioned* facts, session summaries, identity/knowledge reserved, induced schemas, prospective intentions) |
| `working` | Documents, research, spreadsheets, computer use | Two layers: **session Facts** (exactly 1/session) + **reusable Experiences** (0–N behind a value threshold), linked bidirectionally, superseded facts kept traceable via `fact_chain_summary` |
| `coding` | Software engineering | Working's two layers **plus a zero-LLM version-evolution diff chain** per file/function/branch/workspace |

| Mode | Behavior |
|---|---|
| `lite` | Raw-only vector writes/search; zero LLM calls |
| `pro` | + LLM extraction + reconcile; optional graph (`MEMORY_ENABLE_GRAPH`) |
| `ultra` | + System 2 schema induction and cross-domain sweeping via explicit `evolve()`; graph always on |

Additional components (verified from the repo's own docs pages):

- **Readers** — `legacy` (vector-only) and `hybrid` (vector + BM25 keyword
  channel), channel quotas (`limit`, `min_score`, `schema_limit`,
  `intention_limit`), optional **local cross-encoder rerank**
  (`MEMORY_LOCAL_RERANK_*`, construction-time fail-fast, inference fail-open).
- **Scene router / agentic search** — writes with tool-call traces classify once
  into `chat|working|coding`; `search_mode="agentic"` spends one LLM call on
  scene judgment, query rewriting and time-range parsing; `default` stays
  zero-LLM. Multi-query `queries=[...]` resolves the last query using the
  preceding ones as context.
- **Graph layer (fail-open)** — L2 facts projected as `Memory` nodes with edges
  (`co_occurs`, `semantic_neighbor`, `causes`, `elaborates`, `contrasts`,
  `same_series`, `TAGGED_WITH`, `MENTIONS`, `TEMPORAL_NEXT`); retrieval adds
  **support-propagation rerank**, bounded relation expansion, relation paths and
  relation-type priors. Vector retrieval remains the source of truth.
- **Source isolation** — `source=` on writes (per calling app), include/exclude
  filters on search; aggregates derived from multiple sources are marked
  `mixed` and **excluded fail-closed** when any filter is active.
- **CRUD / profile** — `get/update/delete/delete_all`, `list_memories`,
  `get_user_profile()` (L0), `clone_user()`.
- **Open model** — Hy-Memory-30B-A3B (30B MoE, 3B active) trained by
  attributed RL specifically for memory writing and conflict reconciliation.
- **MemHub** — a local-first hub that unifies conversation history and coding
  traces from user-authorized apps (Claude Code, Cursor, Codex, OpenCode, Qwen
  Code) into one managed runtime with separate consent boundaries
  (source read / model processing / host read).

**Claim honesty.** Tencent's headlines ("beats Claude Opus 5") come from its own
model-card comparisons on LongMemEval. An independent reproduction
([dev.to, 2026-10-16](https://dev.to/teolex2020/tencents-hy-memory-is-only-as-good-as-the-llm-that-writes-it-i-measured-it-on-longmemeval-4bj4),
60 LongMemEval questions) measured a **writer-model dependency**: with a cheap
writer LLM, Hy-Memory scored 43% overall (0/8 on "assistant said"); with a strong
writer (Claude Opus 5.5, ≈$5 per user history at list price) it matched a
zero-LLM baseline (90% on knowledge-update questions). Its fact extraction also
loses what the writer did not consider a "fact about the user", but its
**keep-old-value-as-history** design handled changed versions well. Two
consequences for the plan below: (1) extraction quality is a function of the
configured model, which Alpha must document rather than claim away; (2)
history-preserving revision is the single most defensible design lesson.

## What Alpha already has (implemented inventory)

Alpha has **two memory stacks** plus cross-cutting planes
(`docs/MEMORY_RUNTIME_AUDIT.md` is the authority; statuses below follow it):

| Stack | Status | Contents |
|---|---|---|
| `backend/packages/harness/alpha/agents/memory/` | **Wired; live by default** | `MemoryManager` contract + pluggable backends (`deermem` default: markdown facts, SQLite **FTS5 BM25** retrieval, eviction incl. `hybrid-v1`, lossy consolidation; `mem0`, `mem0oss` (local qdrant/Ollama), `openviking`, `honcho`, `fullmemory`, `noop`); model tools (`memory_search/add/update/delete`); `MemoryMiddleware` passive capture; `DynamicContextMiddleware` injection; **L1 typed pipeline** — scene segmentation, typed extraction, dedup (store/skip/update/merge), quota, provenance, retention, persona synthesis, heuristic secret redaction with refuse-on-bypass |
| `backend/packages/harness/alpha/memory/` | Ten-plus **default-off** additive packages | `cognitive` (6 tiers, **semantic belief graph** with conflict detect/reconcile, 3-phase consolidation, skill lifecycle, autonomous upkeep loop — gated on runtime **owner**, not config), `prospective`, `affective`, `narrative`, `entities`, `social`, `scenarios`, `policy`, `fabric` (lifecycle ACTIVE→COMPRESSED→ARCHIVED→PURGED, forget/restore, supersedes/contradicts), `fusion` (weighted + RRF k=60, MMR, per-type token budgets), `evaluation` (16 bundled cases + thresholds), `health`, `utility`, `codebase` (trusted-root repo structure index), `dreaming` |
| Cross-cutting | Wired | Gateway `backend/app/gateway/routers/memory.py` (29 routes), recall composition (`recall_composition.py`, untrusted marking), session recall (`session_search.py` FTS5), System One rerank seam (`rerank.py`) |

Precedent that matters for licensing: Alpha's L1 pipeline is a **contract-level
adaptation of Tencent's TencentDB-Agent-Memory (MIT)** — documented per-file in
`docs/THIRD_PARTY_MEMORY_NOTICES.md` with the "design provenance" docstring
policy. Hy-Memory is a different, later Tencent system (Apache-2.0), so the same
eligible-with-attribution path applies.

The audit's standing honesty gap: **no live Gateway write→recall scenario has
yet been run and recorded.**

## Feature comparison: Hy-Memory 2.0 vs Alpha

| Hy-Memory 2.0 capability | Alpha status | Evidence | Adoption decision |
|---|---|---|---|
| L0 basic-info profile | **EXISTS** (persona synthesis) | `agents/memory/l1/persona.py` | Keep; no adoption needed |
| L1 raw conversation | **EXISTS** | deermem fact files + `session_search.py` FTS5 raw recall | Keep |
| L2 atomic facts, **versioned, with history retention** | **PARTIAL** — dedup decides `update`/`merge`, but the prior value is dropped; fabric has `supersedes` links, default-off | `agents/memory/l1/dedup.py`; `memory/fabric/lifecycle.py` | **Adopt (Phase 1)** — highest-value lesson; the independent study shows history retention is what makes knowledge-update recall work |
| L3 session summaries as a typed layer | **PARTIAL** — compaction + summary channel exist; narrative timeline exists; not a session-scoped typed layer | `summarization_middleware.py`; `memory/narrative/` | Optional; narrative already covers the user-facing story |
| L6 schemas (induced abstractions) | **PARTIAL** — semantic belief graph exists; no *sweep that induces* schema narratives; consolidation engine is local/capacity-oriented | `memory/cognitive/semantic_graph.py`, `consolidation.py` | **Adopt (Phase 2)** — schema induction as an explicit System-2 pass over the existing graph |
| L7 prospective intentions with expiry | **EXISTS (capture)** — no scheduling/delivery | `memory/prospective/*` | Delivery is on Alpha's own roadmap (below), not a Hy adoption |
| `working` two-layer facts + experiences | **PARTIAL/ABSENT** — procedural skills memory exists with strict evidence discipline, but no session-fact + reusable-experience pair with `search_keys`/`solution`/`boundary_envs` and value threshold | `memory/cognitive/procedural_memory.py`, `skill_lifecycle.py` | **Adopt (Phase 3)** — as a new default-off typed layer, not a rewrite of skills memory |
| `coding` version-evolution diff chain (zero-LLM) | **ABSENT** — `memory.codebase` is explicitly a structure index, and trace→code-entity linking is a declared *future extension* | `memory/codebase/*`; `docs/MEMORY.md` future-extension note | **Adopt (Phase 4)** — direct fit for the declared gap; zero-LLM decorator design |
| Scene routing (tool-trace detection + one-LLM judge) | **PARTIAL** — L1 already segments scenes (`mode: chat\|work`); `memory.scenarios` has a deterministic classifier with an unwired model seam | `agents/memory/l1/extractor.py`; `memory/scenarios/*` | Complete the scenario classifier seam (below), not a new router |
| Agentic search: query rewrite + time-range parse | **PARTIAL** — bounded time parsing exists for prospective capture only | `memory/prospective/dates.py` | **Adopt (Phase 5)** — reuse the same bounded parser semantics at recall time |
| Reader quotas + budgeted typed channels | **EXISTS** | `recall_composition.py`; `memory/fusion` budgets | Keep |
| Hybrid reader (vector + BM25) | **EXISTS** | deermem FTS5 BM25; cognitive retrieval + RRF; fusion | Keep; **caveat**: no true embeddings locally (TF-IDF/char-gram approximation) |
| Local cross-encoder rerank (fail-open) | **PARTIAL** — `rerank.py` seam exists; no local cross-encoder stage | `memory/rerank.py` | **Adopt (Phase 5)** — optional extra, strict fail-open/fail-fast split |
| Graph projection + support-propagation rerank + bounded expansion + relation priors | **ABSENT** — belief graph exists but is not used at retrieval time | `memory/cognitive/semantic_graph.py` | **Adopt (Phase 5)** — strictly fail-open, VDB/BM25 stays source of truth |
| Graph lifecycle sync (supersede/shadow retire nodes) | **PARTIAL** | fabric lifecycle exists | Falls out of Phase 1 + Phase 5 |
| Source isolation per calling app + `mixed` fail-closed | **ABSENT** — owner/agent/session scoping exists; no per-entry-point source tags | stack 1 scoping | **Adopt late (Phase 6)** — valuable *because* Alpha spans IM bridges + peer network |
| Vector/graph scale backends (Qdrant, FAISS, Neo4j, Kuzu) | **PLANNED-ONLY, deliberately deferred** | `docs/MEMORY_FABRIC_PLAN.md` defers Qdrant/pgvector/AGE until scale demands | Keep deferred; Hy's "vector stays the truth, graph is fail-open" matches the same posture |
| CRUD/list/export/import | **EXISTS** | Gateway memory routes (`/api/memory`, facts CRUD, export/import) | Keep |
| `clone_user` | n/a | — | **Not adopting** — cross-owner copying contradicts owner isolation |
| Open 30B writer model | **n/a** | Alpha's model routing accepts any OpenAI-compatible endpoint | Operator option only (config), never default; see honesty section |
| MemHub cross-app hub | **ABSENT** | — | **Not adopting in core** (Phase 6 keeps a consent boundary); optional future backend consuming an external hub, mirroring the openviking backend pattern |
| Benchmarks (LongMemEval, PersonaMem, CMB) | **PARTIAL** — 16-case deterministic evaluation suite, no live baseline | `memory/evaluation/*` | **Adopt (Phase 0)** — run against the real backend, record the baseline, add knowledge-update + assistant-said case families |

## What to implement in Alpha (the adoption plan)

Every phase is additive-only, default-off, owner-scoped, TDD-first in
`backend/tests/`, and updated in `docs/MEMORY_RUNTIME_AUDIT.md` +
`docs/MEMORY_TYPES.md` in the same change set. `AppConfig` is
`extra="forbid"`, so each new key lands in
`backend/packages/harness/alpha/config/memory_config.py` and
`config.example.yaml` together.

### Phase 0 — Measure before building (the honesty anchor)

1. Wire and run the existing `memory.evaluation` suite against the real DeerMem
   backend through the config-driven path (not scripted doubles), record the
   committed baseline, and close the audit's "no live write→recall" gap for at
   least one scenario end-to-end.
2. Add two case families directly informed by the third-party study:
   **knowledge-update** (older value must not resurface as current) and
   **assistant-said** (recommendations made by the assistant are recallable).
   Alpha's only defensible performance claim after this phase is one measured on
   these Alpha-run cases.

### Phase 1 — History-preserving fact revision (supersedes chains)

- **What:** when L1 dedup issues `update`/`merge`, and when DeerMem's
  consolidation/staleness review removes or replaces a fact, persist the prior
  value as a versioned ancestor (a "History" line with timestamps) rather than
  discarding it; recall returns the current value with a **bounded** history
  tail. Retire vectors/graph nodes gradually (supersede, not hard delete).
- **Where:** `agents/memory/l1/dedup.py` writer decisions, deermem
  `core/updater.py` + `core/storage.py`, optionally promoted through
  `memory/fabric/lifecycle.py` (`supersedes`) when fabric becomes canonical.
- **Gate:** `memory.l1.history_kept` (default `false`; then flip in `config.example.yaml` after a phase of soak).
- **Tests:** update produces one ancestor and one current; merge unions
  timestamps across the chain (upstream semantics Alpha already documents);
  bounded tail at recall; credential redaction applies to history lines;
  cap compliance with `retention_max_records`.

### Phase 2 — System-2 evolve sweep (schema induction over the belief graph)

- **What:** an explicit, operator-or-upkeep-triggered pass that (a) clusters
  semantically related facts in the existing semantic belief graph, (b) induces
  bounded **schema narratives** (L6 analog — "how this user works/decides"
  templates) with confidence + provenance, (c) sweeps for cross-domain
  collisions, and (d) does **nothing** when the verdict is degraded.
  Hy's insight that System 2 earns +5.20 on implicit cross-session benchmarks
  while barely affecting span recall maps onto Alpha's upkeep loop.
- **Where:** extend `memory/cognitive/consolidation.py` + `autonomous_upkeep.py`
  (`memory_upkeep_tick`), new surface on `POST /api/memory/cognitive/consolidate`
  (`mode: schema`).
- **Gate:** rides the existing owner-gated cognitive package; induction itself
  behind `cognitive.schema_induction` (default `false`).
- **Tests:** induced schemas carry provenance + confidence; degraded verdict
  blocks action; 2000-node semantic cap respected; recall exposes schemas only
  through `schema` channels with their own budget (Hy's `schema_limit` idea).

### Phase 3 — Working-scene experience layer

- **What:** new typed record, `experience`, emitted by L1 extraction in `work`
  dialect behind a value threshold: `task`, `search_keys` (deterministic
  retrieval keys), `solution`, `boundary_envs`, `confidence`; linked
  bidirectionally to the session record that produced it; display-only
  confidence until `memory.utility` calibration says otherwise (existing
  honesty rule). It composes with — does not replace — procedural skills memory:
  skills remain evidence-gated aggregates; experiences are per-instance reuse
  units.
- **Where:** `agents/memory/l1/models.py` (typed set), `extractor.py` prompt
  contract, `recall_composition.py` channel.
- **Gate:** `memory.experience.enabled` (default `false`).
- **Tests:** threshold gating, bidirectional link integrity, search-key recall
  ranking, isolation between owners and agents.

### Phase 4 — Coding version-evolution diff chain (zero LLM)

- **What:** per `owner + workspace + branch + file/function block` chain store
  attaching, under each matching fact, the real diff and the user's words for
  every revision. Sources: git history of a **trusted** repo root (reusing
  `memory.codebase`'s trusted-internal-`repo_root` rule — never from request
  context), and deterministic `path/old/new` replace actions observed in tool
  traces. It is a **decorator, not a retriever**: the chain attaches evidence
  under facts already selected by recall; it never adds candidates by itself
  (Hy's own design rule, and it keeps the token budget honest).
- **Where:** new `backend/packages/harness/alpha/memory/coding_chain/`
  package; recall lands through `recall_composition.py`.
- **Gate:** `memory.coding_chain.enabled` (default `false`).
- **Tests:** chain granularity per file/function/branch; cross-workspace
  isolation; request-supplied `repo_root` refused (security negative test);
  zero-LLM verification; budget bounded per fact; fail-closed on unsupported
  store formats (repo's `_store_format.py` discipline).

### Phase 5 — Reader upgrades (rerank, graph post-processing, query rewrite)

1. **Local cross-encoder rerank** as an optional fusion stage:
   construction-time model load + dummy forward (fail-fast, so the service
   never looks "ready" before the model works), inference fail-open (keep
   pre-rerank order on any failure), bounded candidate pool (`pool factor`),
   explicit device/batch knobs. Optional dependency extra only.
2. **Graph post-processing** over the existing belief graph — support
   propagation with degree normalization and a support cap, bounded relation
   expansion (hop limit, seed score floor, semantic hard gate, relation-type
   quota, merged-into-seed admission), typed-relation priors. **Strictly
   fail-open**: any exception returns the pre-graph candidate order.
3. **Agentic query rewrite + time-range parsing**: one bounded LLM call
   (multi-query resolution: last query is the target), reusing
   `prospective/dates.py` language rules at recall time; fail-open to the
   original query.
- **Where:** `memory/fusion/` (new stages behind `memory.fusion.enabled`),
  `memory/cognitive/semantic_graph.py` for post-processing inputs,
  `memory/rerank.py` for the local model stage.
- **Gates:** `memory.fusion.local_rerank`, `memory.fusion.graph_rerank`,
  `memory.fusion.query_rewrite` (all default `false`).
- **Tests:** fail-open ordering preserved on model/graph failure; expansion
  admission bounds; rewrite never escalates privileges or runs outside the
  injection token budget; blocking-IO offload for any HF/endpoint calls
  (the `MemoryMiddleware.aadd` async boundary rule).

### Phase 6 — Source isolation (late; Alpha-specific payoff)

- **What:** every memory record gains a stripped/lowercased `source` tag of the
  entry point that wrote it (chat UI, Feishu/Slack/Telegram/Discord/DingTalk
  bridges, peer network); search supports include/exclude lists; aggregates
  derived from multiple sources are marked `mixed` and **excluded fail-closed**
  when any filter is active. This is the Hy capability that becomes valuable in
  Alpha because one owner's memory spans several IM channels.
- **Gate:** `memory.sources.enabled` (default `false`).
- **Tests:** filter semantics (empty allowlist = unrestricted; denylist wins;
  `mixed` hidden unless explicitly allowed); no source-tag leaks into prompt
  untrusted blocks.

### Config hygiene item

`config.example.yaml` currently repeats the `l1:` block four times (last-wins,
harmless, but misleading); de-duplicate it in the same change set that adds the
first new key.

## What Alpha should NOT adopt

| Hy-Memory element | Why not |
|---|---|
| `clone_user` | Cross-owner memory copying contradicts Alpha's owner-isolation contract and its cross-owner negative tests |
| Lite raw-only mode as a pipeline stage | Alpha already retains raw conversations (deermem files + `session_search.py`); duplicates storage |
| Vendoring the `hy-memory` SDK wholesale | Alpha's `MemoryManager` + owner-scoped JSON layout is the contract; a parallel SDK would create a second canonical store (explicitly barred by `docs/MEMORY_FABRIC_PLAN.md`). Follow contracts + prompts with attribution instead — the TencentDB-Agent-Memory port is the working precedent |
| The 30B writer model as default extractor | Write-time cost is real (measured: 416K–695K input tokens + 9K–61K output tokens per ~50-conversation history). Offer it as an operator-configured `extraction_model` via the existing model-routing config; document the dependency, never benchmark-claim it |
| Graph as source of truth | Alpha keeps BM25/vector candidates authoritative; graph post-processing is fail-open (a design Hy itself states) |
| MemHub-style cross-app hub in Alpha core | Requires product-level consent boundaries Alpha does not manage; keep the option of consuming an **external** hub as a backend, mirroring the openviking remote-backend pattern |
| Vendor benchmark numbers as Alpha's own | `docs/PRODUCTION_READINESS_INVENTORY.md` culture: a run Alpha has not measured is never "verified" |

## The other list: what Alpha needs beyond Hy-Memory 2.0

These are pre-existing Alpha gaps (mostly from `docs/MEMORY_RUNTIME_AUDIT.md`,
`docs/MEMORY_TYPES.md` rows 15–19, and `docs/MEMORY_FABRIC_PLAN.md`) that the
adoption plan does not resolve by itself:

1. **A recorded live-Gateway write→recall verification** — the standing audit
   gap; Phase 0 makes it a committed baseline artifact.
2. **Reminder scheduling and delivery** — prospective capture/recall/UI exist;
   nothing schedules or delivers a due reminder (Hy's L7 only expires, so this
   is Alpha-roadmap work, not Hy adoption).
3. **A canonical fabric carrier decision** — `memory.fabric` lifecycle +
   forget/restore exist but are not the carrier for the existing stores; the
   unified user-facing forget control deserves its own UX.
4. **True-embedding decision** — local "semantic" stages are TF-IDF/char-gram
   approximations; Qdrant/pgvector stay deferred until scale demands
   (`MEMORY_FABRIC_PLAN`). The Phase 0 baseline should quantify whether the gap
   is measurable on Alpha's workloads.
5. **Scenario model-classifier seam + session plan freeze** — the deterministic
   classifier ships, but the optional model seam and the frozen plan are still
   unwired.
6. **Social capture bridge** — a safe normal-turn capture path into
   `memory.social` is not evidenced (relationship decay + default-deny grants
   exist).
7. **Utility calibration data** — `memory.utility` stays heuristic until ≥20
   scored samples exist; calibrate during Phase 0–1 soak.
8. **Stale-doc repair** — `docs/MEMORY_TYPES.md` rows 15–18 ("wiring pending")
   and the Gateway `AGENTS.md` endpoint table (4 of 29 routes) predate the audit
   and should be reconciled in the first phase's change set.
9. **Cross-process honesty language** — single-process JSON/JSONL state stays
   documented as process-local; a memory backend adapter never claims
   cross-worker exactly-once.

## Acceptance criteria and honesty boundaries

- Each phase lands **TDD-first** (`backend/tests/`, plus package-level suites
  where the module guide demands), with the new gate default-off and
  configurable solely through `memory.*` keys validated by `memory_config.py`.
- Every adapted Hy-Memory contract or prompt is recorded in
  `docs/THIRD_PARTY_MEMORY_NOTICES.md` (Apache-2.0, eligible with attribution)
  carrying the design-provenance docstring before the code ships.
- `docs/MEMORY_RUNTIME_AUDIT.md` and `docs/MEMORY_TYPES.md` gain the wired rows
  in the same change set; no "BUILT — wiring pending" rows are added without the
  wiring.
- Performance language follows the audit's standard: only the Phase 0 baseline
  and its successors are citable; vendor claims and third-party numbers stay
  attributed, never absorbed into Alpha's counts.
- Blocking-IO gating: network/model operations offload via `asyncio.to_thread`
  or native async, per the existing backend async-boundary contract.

## Related reading

- [Memory runtime audit](MEMORY_RUNTIME_AUDIT.md) — the authority on what is implemented and what is verified
- [Memory types taxonomy](MEMORY_TYPES.md) — the canonical 19-type coverage map
- [Memory fabric plan](MEMORY_FABRIC_PLAN.md) — deferred scale-tier decisions this plan must not contradict
- [Advanced memory system implementation plan](ALPHA_ADVANCED_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md) — the earlier plan whose phases 0–10 this document extends
- [Third-party memory notices](THIRD_PARTY_MEMORY_NOTICES.md) — attribution contract every adaptation must join
- [Memory documentation](MEMORY.md) — user-facing memory architecture reference
- External: [Hy-Memory GitHub](https://github.com/Tencent-Hunyuan/Hy-Memory) ·
  [DCPM paper](https://arxiv.org/abs/2606.09483) ·
  [30B model report](https://arxiv.org/abs/2610.22196) ·
  [independent LongMemEval measurement](https://dev.to/teolex2020/tencents-hy-memory-is-only-as-good-as-the-llm-that-writes-it-i-measured-it-on-longmemeval-4bj4)
