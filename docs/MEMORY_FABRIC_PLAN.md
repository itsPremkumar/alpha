# Memory fabric plan — what Alpha has, what the new plan adds, what we refuse

Source: `references/ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM.md`
(user-supplied research, 2,411 lines, 38 sections + 2 appendices, dated
2026-09-25). That file stays uncommitted; this document is the committed,
auditable version of the decisions taken from it. Type-level coverage lives in
`docs/MEMORY_TYPES.md`; licensing lives in
`docs/THIRD_PARTY_MEMORY_NOTICES.md`.

The plan's own principle is adopted verbatim as the program's rule of thumb:
**store once, represent many ways, retrieve intelligently, consolidate
continuously, and preserve provenance.**

## 1. Section-by-section coverage (measured against this repository)

| Plan section | What it asks for | Alpha status | Evidence / decision |
|---|---|---|---|
| 2.1–2.4 Taxonomy | ~60 memory types across time-horizon, cognitive, structural, representation | **HAVE (8 types) + WAVE (6) + PARTIAL** | `docs/MEMORY_TYPES.md` rows 1–19; the taxonomy is decomposed into cognitive tiers, the L1 typed layer, and six new packages rather than 60 silos |
| 3.1 Write pipeline | capture → normalize → extract → classify → score → dedupe → resolve entities → link → version → index | **PARTIAL → WAVE** | L1 does capture/extract/classify/score/dedupe/index; the admission engine (scoring + hard rules) and fabric envelope (versioning/supersession) are in this wave |
| 3.2 Read pipeline | multi-stage retrieval (keyword/semantic/entity/temporal/procedural) → fusion → rerank → compose | **PARTIAL → WAVE** | cognitive retrieval already fuses BM25+vector+graph+temporal with RRF; the new `fusion` package adds the plan's exact five modes, weighted formula, MMR diversity, contradiction filtering and typed token budgets |
| 3.3 Consolidation | async episode → lesson → procedure → skill distillation | **HAVE** | `alpha/memory/dreaming/`, `cognitive/consolidation.py` (3-phase sleep, decay, belief reconciliation) |
| 3.4 Reconsolidation | merge / update / supersede / contradiction sets | **PARTIAL → WAVE** | L1 dedup does store/skip/update/merge; the fabric envelope adds explicit `supersedes` / `superseded_by` / `contradicts` links and bi-temporal `valid_from/valid_to` |
| 3.5 Forgetting | ACTIVE → COMPRESSED → ARCHIVED → PURGED with decay signals | **PARTIAL → WAVE** | L1 retention + DeerMem staleness review exist; the fabric lifecycle adds the four-state ladder, promotion/demotion, restore and an explicit `forget()` API |
| 4.x OSS contributions | borrow ideas, stay dependency-free | **HAVE (policy)** | every license verified (`THIRD_PARTY_MEMORY_NOTICES.md`); AGPL projects (basic-memory, OpenViking) excluded from copying; zero third-party code beyond the MIT TencentDB port |
| 5 Research systems | Generative Agents, Reflexion, A-MEM, LongMemEval | **PARTIAL → WAVE** | reflection/consolidation exist; LongMemEval's five abilities become the `evaluation` package's benchmark dimensions |
| 6 License matrix | permissive baseline | **HAVE** | verified table committed; AGPL flagged |
| 7 Storage stacks | SQLite canonical + FTS5, optional vector/graph tiers | **HAVE (local) / DEFERRED (scale tiers)** | local per-scope store + FTS5 + pluggable `MemoryManager` backends; Qdrant/pgvector/AGE only when scale demands, never a new hard dependency |
| 8 Canonical record | envelope with scope/quality/lifecycle/representations/security/source | **GAP → WAVE** | `alpha/memory/fabric/` implements it as Pydantic, additive to L1/cognitive records |
| 9 Namespaces | user/agent/project/team/session/task scopes | **GAP → WAVE** | `MemoryScope` with descendant-visibility rules; cross-user reads are never implicit |
| 10 Admission | weighted score + always/usually/never-promote rules + secret rejection | **GAP → WAVE** | `alpha/memory/policy/` implements the documented weights, rule table, secret detector, and hot-reloadable policies that fail CLOSED |
| 11 Retrieval scoring | weighted formula + exact/semantic/graph/temporal/procedural modes | **PARTIAL → WAVE** | `alpha/memory/fusion/` |
| 12 Composition | scope filter → dedupe → diversity → evidence groups → per-type token budget | **GAP → WAVE** | `alpha/memory/fusion/composer.py` with coding/debugging/planning budget profiles |
| 13–14 Dreaming + self-improvement | consolidation worker, memory credit assignment | **HAVE (consolidation) / DEFERRED (auto-tuning)** | automatic policy tuning from utility signals is deferred: it needs the feedback data first, and silent self-tuning of an honesty-critical system is the wrong default |
| 15 Shared memory | private / project / org levels, propose-then-accept events | **GAP → WAVE** | `alpha/memory/social/` with default-deny audience grants |
| 16 Skill/tool memory | tool outcomes → procedures → lessons → skills | **PARTIAL** | skills forge/proposals + cognitive procedural stats exist; promoting memory-derived procedures into skills stays gated behind the existing approval flow (no auto-promotion) |
| 17 Codebase memory | repo architecture/conventions/decisions as a memory family | **PARTIAL / DEFERRED** | repo guides and decisions exist as documents, not as queryable memory; building it properly is a separate wave (would need an ingestion contract for docs + code) |
| 18 Temporal memory | validity intervals, "as of" queries, supersession on change | **HAVE + WAVE** | cognitive spatio-temporal has intervals; fabric adds envelope-level bi-temporality |
| 19 Provenance | every durable fact traceable to its source event | **HAVE** | L1 generation log, evidence store, record `source` blocks |
| 20 Contradiction | state machine + supports/contradicts/supersedes edges | **PARTIAL → WAVE** | cognitive belief reconciliation exists; fabric + fusion add explicit edges and retrieval-side handling |
| 21 Security | scope separation, no secrets in semantic memory, classification, forget, no authz-from-memory | **PARTIAL → WAVE** | DeerMem secret filter + redaction boundary exist; fabric adds classification/ACL and policy adds secret rejection at admission |
| 22 Free local stack | Tier A/B/C deployment profiles | **HAVE (Tier A)** | the local-first stack is the only supported default; no hosted memory service is required |
| 23–24 Memory Fabric + API | unified facade, `remember/recall/forget/...` API | **DEFERRED (facade) / WAVE (pieces)** | the pieces land first; a single facade over them is a later, deliberately thin integration (avoid building an orchestrator that duplicates the existing MemoryManager) |
| 25 Policy engine | hot-reloadable policy table | **GAP → WAVE** | `alpha/memory/policy/` |
| 26 Health dashboard | volume/quality/retrieval/utility/lifecycle/storage metrics | **DEFERRED** | metrics vocabulary is being defined by the evaluation package first; a dashboard needs an owner for the UI surface |
| 27–28 Benchmarks + dataset | five LongMemEval abilities + dataset schema | **GAP → WAVE** | `alpha/memory/evaluation/` with ORIGINAL cases (no third-party dataset copied) |
| 29 World-class flow | on-start recall, async capture, after-exec lessons, failure↔recovery linking | **PARTIAL** | L1/cognitive cover capture and consolidation; the prospective/social packages add commitment and counterpart state; the flow is documented, not yet one orchestrator |
| 30 Routing table | situation → primary/secondary memory + mode | **GAP → WAVE** | `alpha/memory/scenarios/` (registry-based, decoupled from concrete subsystems) |
| 31 What NOT to build | no giant MEMORY.md, no one-vector-bucket, no blind last-write-wins, no missing timestamps/provenance/forgetting/evaluation | **ADOPTED as a rule** | every wave package is separately scoped, timestamped, provenanced, forgettable and benchmarked |
| 32–34 Phases + adapters | 6-phase order, pluggable backends/adapters | **IN PROGRESS** | this program executes the plan's Phase 1–2 items now; Phase 3–6 (skills intelligence, memory OS, self-evolution, distributed) stay queued with reasons |
| 35 Licensing | permissive baseline, review copyleft | **HAVE** | notices file |

## 2. What this wave builds (ten disjoint packages, one owner each)

| Package | Plan origin | State |
|---|---|---|
| `alpha/agents/memory/l1/` | §3.1–3.5, §8 (L1 contracts) | **LANDED** (`cc5ac5f`) |
| `alpha/memory/prospective/` | §2.2 goal/prospective, §29 | building |
| `alpha/memory/affective/` | emotional tone (extension of §2.2) | delivered, review in progress |
| `alpha/memory/narrative/` | §2.2 autobiographical | building |
| `alpha/memory/entities/` | §2.3 entity/relationship | building |
| `alpha/memory/social/` | §15 shared memory | building |
| `alpha/memory/scenarios/` | §30 routing | building |
| `alpha/memory/fabric/` | §8 envelope, §9 namespaces, §18 temporal, §3.4–3.5 lifecycle | building |
| `alpha/memory/policy/` | §10 admission, §25 policy engine | building |
| `alpha/memory/fusion/` | §11 retrieval, §12 composition | building |
| `alpha/memory/evaluation/` | §27–28 benchmarks | building |

Every package is default-OFF, config-gated, hermetically tested, and wired by
the central agent into real lifecycle seams — no package ships dormant, and no
package replaces an existing store.

## 3. What we deliberately do NOT build (and why)

- **A second canonical store.** The plan proposes SQLite as the source of
  truth; Alpha already has a local per-scope store with FTS5 and a pluggable
  backend protocol. A parallel SQLite "memory OS" would duplicate state and
  create two truths. Revisit only if scale forces it.
- **Any mandatory vector/graph dependency.** Qdrant/pgvector/AGE stay
  optional; zero-dependency lexical+hybrid retrieval is the default.
- **OpenViking / basic-memory code or shape-copying.** AGPL.
- **Auto-tuning admission/retrieval policy from live traffic.** Deferred until
  utility feedback exists and is evaluated; silent self-tuning of an
  honesty-critical subsystem is the wrong default for this project.
- **A monolithic `MEMORY.md`.** Explicitly rejected by the plan itself and by
  this repository's memory-fence conventions.

## 4. Queued after this wave (with the reason each is not now)

1. Memory utility feedback events (§14) — needs the surfaces above to exist.
2. Memory health/metrics surface (§26) — metrics vocabulary lands with the
   evaluation package.
3. Codebase memory family (§17) — needs an ingestion contract for docs/code.
4. Memory import/export + a user-facing forget control (§24, §21) — a trust
   feature that deserves its own UX and audit pass.
5. A single `MemoryFabric` facade (§23) — deliberately last, so it wraps real
   packages instead of inventing an orchestrator nobody uses.
