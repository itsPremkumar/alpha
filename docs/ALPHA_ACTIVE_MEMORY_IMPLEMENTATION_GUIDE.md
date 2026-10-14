# Alpha Active Memory Engineering Guide

**Research and repository audit date:** 13 October 2026
**Target repository:** [`itsPremkumar/alpha`](https://github.com/itsPremkumar/alpha)
**Implementation style:** additive, Python-first, local-first, cost-aware, evidence-driven
**Purpose:** audit what Alpha already has, define the missing composition layer, and provide a detailed implementation, testing, and rollout plan.

> **Important scope note:** This guide audits the public GitHub `main` branch and its source/docs as accessible on the date above. It does not inspect the contents of your local `config.yaml`, execute your installed Alpha, or run its test suite on your machine. Treat every "confirmed" item below as repository evidence, not a guarantee that your local deployment has the feature enabled or is healthy. The first implementation task must verify the actual checked-out commit and effective runtime configuration.

---

## 1. Executive decision

Alpha already has substantial memory-related infrastructure. Do **not** build a second standalone memory database or replace the current memory system wholesale. The main task is to connect existing components into one explicit **Active Memory Controller** that manages retrieval, context budgeting, safe writes, task checkpoints, consolidation, provenance, and evaluation.

### Current-state verdict

| Capability | Finding from public repository | Implementation decision |
|---|---|---|
| Thread/run context and recovery | Alpha documents durable runs/checkpoints and SQL-backed run events; these serve operational continuity | Reuse the existing checkpointer/run-state as the task-state authority; do not duplicate run state in semantic memory |
| Agent-facing persistent memory | A backend-neutral `MemoryManager` contract and backend discovery exist in `backend/packages/harness/alpha/agents/memory/manager.py` | Extend through the existing contract/backend mechanism |
| Default fact memory | A `deermem` backend is present and described as the default backend | Preserve and test its storage, extraction, injection, and lifecycle behavior |
| Composite long-term memory | A `fullmemory` backend composes DeerMem, optional local Mem0 (`mem0oss`), and the wiki vault | It is the most relevant existing integration point; verify its effective configuration and all write/read paths before enabling it |
| Cognitive memory | `alpha.memory` exports `CognitiveMemorySystem`, belief/recall/consolidation types, and related components | Reuse as the structured-cognition plane; do not bypass its invariants |
| Active retrieval | `backend/packages/harness/alpha/memory/active_memory.py` defines `ActiveMemoryRouter` with local keyword lookup and optional Tier-2 escalation | This is a real active-retrieval component, but its shown implementation is narrow; harden and wire it rather than treating it as finished universal memory orchestration |
| Past-session search | `alpha.memory.session_search` implements SQLite FTS5/BM25 search with bounded results, hidden-source filtering, and optional smart reranking | Reuse as the episodic-history search channel; ensure callers supply a durable DB path (the class defaults to `:memory:`) |
| Dreaming / consolidation | `alpha.memory.dreaming.store.DreamStore` persists `MEMORY.md` and `DREAMS.md`; composite memory can append wiki-vault episodes for out-of-band compilation | Verify the real scheduler/worker/caller wiring. The store itself is persistence, not evidence that a safe consolidation cycle always runs |
| Procedural memory | README describes evidence-gated ReasoningBank strategies and a `ralph_loop` consumer | Reuse its verdict/win/attempt records; verify task-time retrieval and update events in the real run path |
| Unified lifecycle across all memory planes | `alpha.memory` and `alpha.agents.memory` are explicitly documented as separate trees, with no implicit cross-dependency | This is the main architectural composition gap. Add a host-level facade/adaptor, with ownership boundaries intact |
| Automatic experience capture from every run | A separate continual-intelligence report says run-worker capture of its `ExperienceTelemetry` is not wired automatically; it is currently caller-attached | Verify this separately from ordinary conversational memory. Add a real run-event bridge where needed |
| Retrieval quality measurement | Existing tests and components exist, but source existence is not proof of end-to-end active-memory behavior | Add behavior-level evaluations across sessions, restarts, changed facts, code tasks, and subagents |

### Most important conclusion

Alpha does not need “memory from zero.” It needs the existing memory capabilities to behave as one **observable, lifecycle-managed, evidence-bearing system** during real execution. The first milestone should prove that a new task retrieves the right relevant memory and that completing a task creates a reusable, verified record that can be retrieved after a process restart.

---

## 2. Repository audit: what already exists

### 2.1 Dedicated memory documentation

File: [`docs/MEMORY.md`](https://github.com/itsPremkumar/alpha/blob/main/docs/MEMORY.md)

The document describes short-term thread context/checkpoints, long-term vector memory, key-value facts, episodic experiences, cognitive memory, a knowledge graph, memory endpoints, and frontend integration concepts. Treat this document as an architectural guide, not conclusive runtime proof for every provider or endpoint. The actual implementation and the effective `config.yaml` must be inspected before assuming a provider or UI path is active.

### 2.2 Two deliberately separate memory trees

File: [`backend/packages/harness/alpha/memory/__init__.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/__init__.py)

This package explicitly documents the split:

1. **Cognitive side — `alpha.memory`:** cognitive beliefs/traces/consolidation/hybrid recall, resident memory nudges (`kibitzer`), past-session search, active memory routing, dreaming, and wiki vault components.
2. **Agent-facing side — `alpha.agents.memory`:** a backend-neutral `MemoryManager` contract and pluggable backends such as DeerMem and OpenViking. This side answers where user/agent memories are stored and how memory context is injected.

The package documentation explicitly says the cognitive package must not depend on the agent-facing backend package and the manager must not reach directly into the cognitive engines. This is an intentional architectural boundary. A new active-memory feature must respect it.

**Correct integration pattern:** add a small host/orchestration facade or neutral event/query protocol that can call adapters for both trees. Do not introduce direct imports that violate the documented boundary, and do not copy data between stores without provenance, idempotency, and reconciliation.

### 2.3 Agent-facing memory manager and safety boundary

File: [`backend/packages/harness/alpha/agents/memory/manager.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/agents/memory/manager.py)

Repository evidence indicates that the module provides:

- A backend-neutral `MemoryManager` contract, including `add`, `get_context`, search/management operations, and lifecycle hooks.
- A factory that resolves the configured backend and discovers backend packages exposing `MANAGER_CLASS`.
- Owner/user and agent scoping for memory buckets, with conversation `thread_id` alignment.
- A centralized `redact_for_memory_write` boundary wrapping supported write methods.
- Fail-loud behavior when an explicitly configured backend cannot be resolved, rather than silently switching to a different memory store.
- Optional callback hooks around memory LLM calls and trace integration.

**Implementation rule:** use this existing factory and contract. Do not instantiate a second global manager in the new code or route around redaction, owner scope, or trace context.

### 2.4 DeerMem backend

File: [`backend/packages/harness/alpha/agents/memory/backends/deermem/`](https://github.com/itsPremkumar/alpha/tree/main/backend/packages/harness/alpha/agents/memory/backends/deermem)

The package describes DeerMem as the default memory manager. Its memory is primarily durable fact-oriented memory, with message filtering, signal detection, a debounced update queue, and a formatted context-injection path. The implementation also documents relevance ranking against the current query when the relevant retrieval option is enabled; otherwise older confidence-ordering behavior applies.

**What to verify in a running Alpha installation:**

- Which `manager_class` is actually configured.
- Whether `memory.enabled` and `memory.injection_enabled` are both effective.
- Whether writes are triggered after real user/assistant turns and after tool-mode calls.
- Whether `get_context` is called on the real run path, with the current user/agent/thread scope.
- Whether the configured write queue is drained on shutdown and survives failure/restart as intended.
- Whether a stored fact appears in the next relevant prompt and not in an unrelated user's prompt.

### 2.5 Composite `fullmemory` backend

Files:

- [`backend/packages/harness/alpha/agents/memory/backends/fullmemory/fullmemory_manager.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/agents/memory/backends/fullmemory/fullmemory_manager.py)
- [`memory-full.example.yaml`](https://github.com/itsPremkumar/alpha/blob/main/memory-full.example.yaml)

The composite implementation describes three inputs:

- **DeerMem:** durable facts and its searchable memory/injection path; treated as the durable core.
- **mem0oss:** optional local open-source Mem0 plus Ollama for semantic fact memory; best-effort and capable of falling back to a stub when dependencies are missing.
- **Wiki vault:** per-user Markdown-backed knowledge/digest with zero-dependency keyword searching and episode append operations. The documentation says a background Dreaming job compiles raw episodes into wiki pages out of band; verify that job is scheduled and running rather than assuming it is.

Its read path merges DeerMem context, optional Mem0 facts, and a wiki digest within a configured injection budget. Its write path fans out to DeerMem and optional Mem0 and appends recent episode text to the wiki vault. The implementation shown catches some optional backend failures and continues without that source.

The example config is commented out. Its existence **does not prove `fullmemory` is enabled in your deployment**. Its example also describes optional local model/dependency setup and tolerant/stub behavior. That can be useful for a zero-API-key environment, but verify that memory failures are visible and do not silently drop important task state. The operational task checkpoint must remain in Alpha's durable run store, not depend on an optional semantic-memory backend.

For local use, the example mentions Ollama and models such as `llama3.1` plus `nomic-embed-text`. Pick models that actually fit your hardware and evaluate memory latency/quality; those are examples, not mandatory model choices.

### 2.6 ActiveMemoryRouter: the existing feature with the same name

File: [`backend/packages/harness/alpha/memory/active_memory.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/active_memory.py)

This file defines `ActiveMemoryRouter` and `MemoryLookupResult`. Its docstring calls it a two-tier escalation engine:

- **Tier 1:** reads lines from explicit memory files, or reads `MEMORY.md` via `DreamStore` when no files are supplied; uses deterministic keyword/substring token scoring and returns a small number of matching lines.
- **Tier 1.5:** may use the System One gate to decide if the expensive deeper pass is needed, when an escalation handler is provided and System One is enabled/available.
- **Tier 2:** delegates to a caller-provided `escalation_handler` for a deeper pass when local confidence is inadequate.

This is a useful existing seed. It is **not on its own** a complete modern active-memory controller: its shown Tier-1 implementation scans text lines and scores substring matches, rather than orchestrating semantic retrieval, episodic search, temporal validity, graph relations, provenance checks, context budgeting, post-task writes, or memory lifecycle.

Two code-level concerns to verify and address:

1. `DreamStore` defaults its root to `Path.cwd()` if no explicit root is passed. A changing process working directory can accidentally change which `MEMORY.md`/`DREAMS.md` files the router sees. Bind the store to the configured Alpha runtime-home/state directory.
2. In the visible `ActiveMemoryRouter.query` implementation, a successful Tier-2 callback is returned with a fixed confidence value (0.90). Do not treat this as calibrated confidence. Carry actual evidence/verification state from the callback and distinguish provider completion from factual correctness.

Also verify all real call sites. Having `get_active_memory_router()` exported and unit-tested does not prove the router runs before the planner, on tool failures, on subagent delegation, or at checkpoints in production.

### 2.7 Past-session search engine

File: [`backend/packages/harness/alpha/memory/session_search.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/session_search.py)

The source defines `SessionSearchEngine` using SQLite FTS5 and BM25-style ranking. It has discovery, scroll-around-message, and read paths; excludes internal sources such as tool/subagent chatter from discovery; applies bounded result/content sizes; and optionally reranks discovery results with Alpha's System One/retrieval-reranking logic.

**Important persistence detail:** the class signature defaults `db_path` to `":memory:"`. That is an in-memory SQLite database. Any long-lived deployment must inject a stable file-backed path or verified persistent connection/store. An ephemeral default is acceptable for tests; it is not a cross-restart session-memory guarantee.

Wire this as one retrieval channel—not as the only memory store. Its job is to find the relevant historical interaction, then the controller should fetch a small surrounding window or authoritative artifact rather than injecting a whole past conversation.

### 2.8 DreamStore and dreaming

File: [`backend/packages/harness/alpha/memory/dreaming/store.py`](https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/dreaming/store.py)

`DreamStore` keeps `MEMORY.md` (durable curated rules) and `DREAMS.md` (consolidation journal). It supports reading and appending and suppresses exact duplicate `MEMORY.md` lines. This is useful, inspectable file storage.

The store by itself does not prove that nightly/idle consolidation is scheduled, that facts are evidence-checked, that conflicting rules are versioned, or that deletion/retention is enforced. Inspect the dreaming runner/scheduler and its invocation paths. A production consolidation system must be idempotent and able to resume after interruption.

### 2.9 ReasoningBank and continual learning

The current README describes ReasoningBank as durable, evidence-gated procedure memory: strategies are saved along with verdicts and measured wins/attempts, then recalled deterministically for similar tasks. It identifies `ralph_loop` as a production consumer.

Related files that deserve an audit before extending memory:

- [`ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md`](https://github.com/itsPremkumar/alpha/blob/main/ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md)
- [`ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md`](https://github.com/itsPremkumar/alpha/blob/main/ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md)
- [`docs/CONTINUAL_INTELLIGENCE.md`](https://github.com/itsPremkumar/alpha/blob/main/docs/CONTINUAL_INTELLIGENCE.md)

The continual-intelligence report notes one concrete integration gap: `ExperienceTelemetry` is optional and there is no automatic capture hook from `runtime/runs/worker.py` for that learning layer; telemetry must be attached by a caller. It also identifies capabilities that are implemented but partial or default-off. Do not equate continual-learning state with the user-facing conversational memory backend. Connect the systems through events/adapters and keep their separate policies intact.

### 2.10 What this audit does not establish

This public source inspection did **not** establish:

- Which memory backend your local `config.yaml` selects.
- Whether all optional packages/models are installed.
- Whether the active-memory router is called during actual production executions.
- Whether the dreaming/wikivault compilation job is running 24/7.
- Whether `SessionSearchEngine` is instantiated with durable storage in the running app.
- Whether all documented API/UI endpoints are registered and enabled in your current checkout.
- Whether active-memory changes improve task success against an evaluation set.

These are explicit tasks in Phase 0 and Phase 7 below, not assumptions.

---

## 3. Research findings and design choices

This implementation plan draws on the following research and project designs. Use them as patterns, not as a mandate to copy every framework into Alpha.

| Reference | Useful concept for Alpha | Primary source |
|---|---|---|
| MemGPT / Letta | Manage bounded context as a working set over external persistent memory; agent-accessible memory operations | https://arxiv.org/abs/2310.08560 · https://github.com/letta-ai |
| Generative Agents | Experience stream, reflection, and planning from retrieved memories | https://doi.org/10.1145/3586183.3606763 |
| A-Mem | Linked memories that can be refined as new related records arrive | https://arxiv.org/abs/2502.12110 · https://github.com/agiresearch/A-mem |
| Graphiti / Zep | Temporal fact graph, relationship-aware retrieval, and provenance | https://arxiv.org/abs/2501.13956 · https://github.com/getzep/graphiti |
| Hindsight | Separate retain, recall, and reflect operations; combine semantic, keyword, graph, and temporal search; preserve evidence around beliefs | https://aclanthology.org/2026.acl-demo.27/ · https://github.com/vectorize-io/hindsight |
| ReMe | Local-first, readable/portable memory files, consolidation and retrieval integration | https://github.com/agentscope-ai/ReMe |
| Mem0 | Memory extraction/update/retrieval as an application layer | https://github.com/mem0ai/mem0 |
| LangMem | Semantic, episodic, and procedural memory primitives for graph-based agents | https://github.com/langchain-ai/langmem · https://langchain-ai.github.io/langmem/ |
| AgeMem | Research direction where the agent selects memory actions spanning long-term memory and short-term context management; paper discusses RL/GRPO | https://arxiv.org/abs/2601.01885 |
| LongMemEval | Measure long-term conversational memory, temporal reasoning, updates, abstention, and multi-session reasoning | https://arxiv.org/abs/2410.10813 |
| MemoryAgentBench | Evaluate accurate retrieval, interactive learning, long-range understanding, and selective forgetting | https://arxiv.org/abs/2507.05257 |

### Design conclusions

1. **Long context is not active memory.** Persist facts and task state outside the context window, then retrieve selectively.
2. **Vector-only retrieval is not enough.** Exact code identifiers and errors are lexical; related events may require graph links; changes need temporal filters; past conversations need session search.
3. **Memory has at least two write timescales.** Persist critical task events/checkpoints synchronously; consolidate experience into durable facts/procedures asynchronously.
4. **Facts and beliefs must be distinguishable.** A fact backed by a config file or test log is not the same thing as an inferred explanation.
5. **The agent must know when to abstain.** Empty or weak memory results should trigger further retrieval or an explicit “not found,” not a high fabricated confidence score.
6. **Memory policies need benchmarks.** More records, more embeddings, or a more complicated graph are not success criteria.
7. **Learned policies come later.** First build deterministic, observable memory actions and a task-level evaluation baseline; only then consider AgeMem-style learned action selection.

---

## 4. Target architecture: Alpha Active Memory Controller

Keep the controller as a thin composition layer that coordinates existing stores. It should not own every piece of memory content.

```text
                    USER / SCHEDULED GOAL / RECOVERY TRIGGER
                                  |
                                  v
                         ALPHA ORCHESTRATOR
                                  |
                                  v
                     ACTIVE MEMORY CONTROLLER
                    /     |      |       \
                   /      |      |        \
                  v       v      v         v
          Intent/need   Safety  Budget   Observability
            analysis   filter   policy     + trace
                  \       |      |        /
                   \      |      |       /
                    v     v      v       v
                      RETRIEVAL ADAPTERS
       +----------------+----------------+-----------------+
       |                |                |                 |
       v                v                v                 v
   DeerMem /       SessionSearch     Cognitive memory   ReasoningBank
   Mem0 / wiki      (FTS5/BM25)       / relations        procedures
       |                |                |                 |
       +----------------+----------------+-----------------+
                                  |
                                  v
                HYBRID MERGE / DEDUPE / RERANK / TEMPORAL CHECK
                                  |
                                  v
                   BOUNDED, CITED WORKING MEMORY SET
                                  |
                                  v
                    PLANNER / WORKER / SUBAGENT(S)
                                  |
                     tool calls, artifacts, outcomes
                                  |
                                  v
                   DURABLE RUN STATE / EVENT JOURNAL
                                  |
                     +------------+--------------+
                     |                           |
                     v                           v
          Synchronous checkpoint             Async consolidate
          + failure/recovery state            + fact/procedure
                                                promotion review
                                                     |
                                                     v
                              EXISTING STORES + PROVENANCE / AUDIT
```

### 4.1 Ownership rules

- **Run state and next action:** existing run/checkpoint store. Never rely on vector memory to recover whether an operation already ran.
- **Raw past messages and their search index:** session-search storage or the existing durable conversation store, as configured.
- **User/project facts:** configured agent-facing memory backend (DeerMem or fullmemory), subject to its owner scope and redaction boundary.
- **Structured entities/beliefs and cognitive consolidation:** cognitive memory subsystem.
- **Reusable strategy/procedure with measured outcome:** ReasoningBank/skill system.
- **Raw logs, screenshots, test reports, source files, commits:** artifact store/repository/test evidence. Memory holds references/hashes/snippets, not a competing copy of the entire source of truth.
- **Unified retrieval policy, budget and audit:** new Active Memory Controller.

### 4.2 Module placement

Suggested file boundaries, subject to checking nearby established package naming and orphan-module rules before creating anything:

```text
backend/packages/harness/alpha/
  memory/
    active_memory.py             # existing; evolve carefully, do not replace blindly
    active_controller.py         # proposed: orchestration across adapters
    memory_envelope.py           # proposed: shared record/evidence contract
    retrieval.py                 # proposed: hybrid merge/ranking/budget helpers
    write_policy.py              # proposed: promotion/retention eligibility
    evaluation.py                # proposed or existing evaluation package integration
  agents/memory/
    manager.py                   # existing backend-neutral contract/factory
    backends/deermem/            # existing durable facts backend
    backends/fullmemory/         # existing composite backend
```

These are proposed names, not assertions that those exact files currently exist. Before creating them, search for a matching implementation; reuse or extend the nearest existing module. Alpha's architecture has an orphan-module check, so every new module must have a real production caller and tests.

**Boundary requirement:** `alpha.memory` documentation says not to import agent-facing backends directly and vice versa. Place cross-plane coordination in an existing host/runtime/orchestration boundary or define a small neutral protocol at a reviewed dependency layer. The exact location should be chosen after inspecting existing DI/service factories.

---

## 5. Data model: a memory record must carry evidence and lifecycle

Do not store only a text string and an embedding. Add an envelope around each candidate memory. Adapt to the backend's existing schema instead of creating a second database if an equivalent record already exists.

### 5.1 Proposed logical schema

```json
{
  "memory_id": "stable-uuid",
  "schema_version": 1,
  "kind": "fact | episode | procedure | preference | task_checkpoint | relation | belief",
  "scope": {
    "owner_id": "owner-id",
    "project_id": "project-id-or-null",
    "agent_id": "agent-or-null",
    "thread_id": "thread-or-null",
    "visibility": "private | project | explicitly_shared"
  },
  "content": "Short human-readable memory content",
  "normalized_key": "optional-canonical-key",
  "source": {
    "kind": "user | run_event | tool_result | file | commit | test | research_source | inferred",
    "source_id": "event/artifact/document id",
    "uri_or_path": "optional stable reference",
    "content_hash": "optional digest",
    "quoted_evidence": ["small supporting excerpt"],
    "verification": "explicit | verified | corroborated | inferred | unverified"
  },
  "temporal": {
    "created_at": "UTC timestamp",
    "observed_at": "UTC timestamp",
    "valid_from": "optional timestamp",
    "valid_until": null,
    "supersedes": [],
    "superseded_by": []
  },
  "quality": {
    "confidence": 0.0,
    "importance": 0.0,
    "stability": 0.0,
    "expected_reuse": 0.0,
    "verification_count": 0,
    "successful_reuse_count": 0,
    "failed_reuse_count": 0
  },
  "lifecycle": {
    "status": "candidate | active | superseded | archived | retracted | deleted",
    "review_after": null,
    "expires_at": null,
    "last_retrieved_at": null,
    "last_verified_at": null
  },
  "security": {
    "sensitivity": "normal | personal | secret-like | restricted",
    "untrusted_content": true,
    "instructional_authority": "data_only"
  },
  "links": [{"type": "related_to", "target_memory_id": "..."}]
}
```

This is a logical contract, not a command to replace every backend schema with this exact JSON. Use Pydantic models and the repository's existing schema/version and persistence utilities if available.

### 5.2 Critical schema rules

- Timestamps come from system/event metadata, not model-generated text.
- Confidence is **not** a free-form LLM self-rating. Prefer discrete evidence state plus calibrated scores from measured evaluation.
- Preserve raw evidence references and hashes for significant conclusions.
- Keep `valid_until`/supersession separate from `created_at`. “Was true in the past” and “is true now” are different queries.
- `status=deleted` must be distinct from `status=archived`. User-requested deletion must not be undone by dream/consolidation jobs.
- Untrusted memory content is always data. It must never override system/developer/security policy or invoke tools by itself.
- Sensitive information must pass through existing write-redaction; never embed/store secrets merely because they appeared in a tool result.
- Scope fields are mandatory for shared memory. No default/global cross-user bleed.
- Use stable IDs and idempotency keys so replaying an event does not create duplicate memories.

### 5.3 Memory categories and ownership

| Category | Promotion rule | Default scope | Should the agent automatically act on it? |
|---|---|---|---|
| Current task state/checkpoint | Every meaningful task transition, checkpoint, blocker, or side-effect outcome | Thread/run | Yes, because it is operational state, subject to workflow policies |
| Episodic experience | Significant completed/failed attempt, with action/outcome/artifact references | Thread + project | No; retrieve as evidence for planning |
| Semantic fact | Stable/explicit or sufficiently verified and expected to be reused | User or project | No; verify changeable facts before critical actions |
| Procedure/skill | Repeated or strongly verified strategy with applicability conditions and measured outcomes | Agent/project | Use only when task conditions match; never bypass permission gates |
| Relationship/graph edge | Entity relation extracted from evidence and resolved to stable entities | Project/user | Use for retrieval, not as an executable instruction |
| Preference | Explicit user preference or repeatedly confirmed pattern | Owner/user | Yes for presentation/behavior where safe; allow correction and deletion |
| Belief/hypothesis | Inference with evidence, confidence and review condition | Project/task | Only as a hypothesis until verified |
| Prospective obligation | Explicit follow-up or unfinished dependency tied to a durable run/task | Task/run | Yes, but only through the authorized scheduler/orchestrator |

---

## 6. Core algorithms to implement

### 6.1 Query interpretation and memory needs

For every new request or major task transition, derive a compact retrieval context:

```python
MemoryRequest(
    user_query=...,              # normalized request
    goal_id=...,                 # active goal/run
    project_id=...,              # from verified repo/project context
    agent_role=...,              # planner/coder/researcher/QA/etc.
    current_step=...,            # current task stage
    required_memory_kinds=[...], # inferred from task type
    excluded_scopes=[...],       # explicit privacy/isolation boundaries
    freshness_requirement=...,   # e.g. current config/current dependency
    token_budget=...,            # bounded injection budget
    must_verify=[...],           # facts that require current artifact/source
)
```

Do not rely only on an LLM to invent `project_id`, current port, version, file path, or current task status. Pass verified values from the current runtime and repository scan.

### 6.2 Parallel retrieval channels

Query only sources that can plausibly answer the question. Suggested initial channels:

1. **Lexical search:** exact identifier, exception text, file path, command, endpoint, package/version; use SessionSearch/FTS5 or an existing backend's text-search support.
2. **Semantic search:** paraphrased concepts and similar old tasks; use existing Mem0 or configured embedding backend only if enabled and healthy.
3. **Fact memory:** explicit or verified project/user facts from the configured `MemoryManager`.
4. **Episodic/session history:** past attempts and outcomes, retrieved by query then expanded to a bounded surrounding message window.
5. **Cognitive/relationship search:** linked entities, decisions, and previous changes when the question asks “why,” “what depends on,” or spans several events.
6. **Procedure memory:** retrieve strategies based on task/action type only if existing ReasoningBank reports a matching and eligible strategy.
7. **Artifact verification:** for current repository state, use the actual file/config/test/commit artifact. A stored memory is a pointer and clue, not a replacement for the current artifact.

Start with current built-in capabilities. Do not add a dedicated vector database, graph service, and embedding service all at once just because an architecture paper uses them.

### 6.3 Merge and ranking

Use a cheap, testable multi-signal ranker before any optional LLM rerank. Initial candidates should carry source and evidence metadata.

A useful starting score (all signals normalized to `[0, 1]`) is:

```text
score =
    0.30 * task_relevance
  + 0.20 * evidence_quality
  + 0.15 * temporal_validity
  + 0.10 * scope_match
  + 0.10 * observed_utility
  + 0.05 * importance
  + 0.05 * stability
  + 0.05 * lexical_or_exact_match
  - stale_penalty
  - duplication_penalty
  - sensitivity_or_permission_penalty
```

This is a **starting hypothesis**, not an evidence-based universal optimum. Tune weights against Alpha's benchmark data. Hard scope/security exclusions must be filters, not small score penalties; inaccessible memory must never be recoverable just because its relevance score is high.

Recommended merge approach:

1. Apply owner/project/agent ACL filters first.
2. Apply lifecycle and temporal-validity filters.
3. Fetch bounded top-K per channel.
4. Deduplicate by stable ID, canonical key, content hash, and normalized near-duplicates.
5. Use reciprocal-rank fusion or another deterministic rank-fusion method to merge independent search rankings.
6. Apply a local deterministic score using evidence, recency/validity, scope, and observed usefulness.
7. Optional LLM/cross-encoder rerank only a small candidate set when it adds measured value and fits the budget.
8. Return the top items plus source IDs, reason, score breakdown, and known uncertainty.
9. If a required fact remains unavailable, say that it is unavailable and take the approved re-retrieval/verification path.

Never call the combined score “probability of truth.” It is ranking relevance unless specifically calibrated.

### 6.4 Context budget allocation

The active controller should choose a compact, task-specific working set. Suggested initial maximum injection budget: **1,500–3,000 tokens** for an ordinary request, with a separate configurable ceiling for long coding/research runs. The actual budget must follow the configured model context, run budget, prompt length, and model performance.

Allocate context in order of task value:

1. Active goal, verified task state, next step and blockers.
2. Safety/permission constraints and project-specific current facts.
3. The strongest relevant previous episodes and verified procedures.
4. Supporting memories and source/evidence pointers.
5. Brief preference/user context when relevant.

Do not inject all conversation history, every fact, the entire knowledge graph, or full logs. Keep an explicit `memory_injection_report` with selected records, rejected records, budget, and reason codes. Sensitive content should never be added merely to fill the budget.

### 6.5 Active retrieval triggers

The controller should have a policy for when to retrieve; it should not wait for the LLM to remember that a memory tool exists.

**Mandatory, low-cost retrieval points:**

- New task/run start.
- Resume after pause, crash, process restart, or expired worker lease.
- Before plan decomposition and task assignment.
- Before a subagent starts work in a known project.
- After a repeated failure or a tool error.
- When the plan changes, a blocker occurs, or the agent retries a failed strategy.
- Before important actions that depend on project state or prior user preferences.
- At milestones and task completion to write the operational checkpoint and useful episode.

**Optional/deeper retrieval:**

- When lexical/context lookup returns too few relevant records.
- When sources conflict or a question asks for causal/temporal synthesis.
- When the current task needs cross-project experience.
- When the agent explicitly requests a deeper recall/reflect action.

Retrieval must be bounded by per-run query/cost/time budgets to avoid loops where the agent repeatedly searches the same memory with slightly different wording.

### 6.6 When should Alpha remember something?

Do not store every message as durable semantic memory. Use a candidate gate with reasons:

**High-priority candidate examples:** explicit long-term user preference, stable project convention, verified root cause and successful fix, repeatable procedure with outcome evidence, significant architectural decision, recurring failure mode, task that must resume later, or an explicit user instruction to remember something.

**Usually do not promote:** greetings, filler, duplicated logs, temporary state that belongs in run checkpoints, unverified internet claims, model speculation, secrets/tokens/passwords, tool output with malicious embedded instructions, failed hypotheses without the failure evidence, or facts that will become stale quickly and have no expiry.

Store task events/episodes first when useful; promote a concise semantic fact or procedure only after review/verification. That preserves evidence and avoids turning a single failure into a permanent false rule.

Suggested initial promotion score:

```text
promotion_eligible =
    policy_allows_write
    AND scope_is_valid
    AND not_secret_or_blocked_sensitive_data
    AND candidate_has_source_or_explicit_user_intent
    AND (explicit_memory_request OR (reuse_likelihood_high AND quality_gate_passed))
```

`quality_gate_passed` should require different checks by memory type. A user-stated preference can be stored as explicitly stated; a code-fix strategy should usually require test/evidence and applicability conditions.

### 6.7 Update, supersede, archive and delete

Implement lifecycle operations through the authoritative owning store. The controller should not create duplicate records in each backend independently without an idempotent transaction/outbox pattern.

- `CREATE`: new memory or candidate.
- `MERGE`: merge duplicates with a link to original evidence.
- `UPDATE`: correct metadata or content while preserving necessary prior state.
- `SUPERSEDE`: new time-scoped value replaces an old current value; retain history when policy permits.
- `VERIFY`: check source artifacts and update verification metadata.
- `ARCHIVE`: no longer in hot retrieval but retained per retention policy.
- `RETRACT`: mark a known-false statement as not usable; link to correction.
- `DELETE`: permanently delete under user/policy requirements, with propagation to indexes/caches/digests and a tombstone/reconciliation mechanism as appropriate.

Do not use a generic “recency decay” to discard durable project conventions or old but important historical incidents. Age, stability, usage, importance, and correctness are separate signals.

---

## 7. Implementation roadmap for Alpha

Implement this in phases. Each phase needs a production caller, tests, diagnostics, and a rollback path. Do not try to ship the entire design in one giant refactor.

## Phase 0 — Baseline and runtime wiring audit

**Goal:** Establish what runs today, not what its name/documentation suggests.

### Tasks

1. Record `git rev-parse HEAD`, branch, dirty state, Python version, package lock state, and test commands.
2. Inspect the current effective `config.yaml`, environment overrides, and startup logs. Never print secrets into reports.
3. Resolve the configured `memory.manager_class`; prove it is discoverable and identify the actual backend class.
4. Inspect whether memory read/injection and memory writes run on real conversation/execution paths.
5. Inspect all `ActiveMemoryRouter` usages, including import paths, constructors, `query()` calls, and any `escalation_handler` registration.
6. Inspect `SessionSearchEngine` construction and prove whether it uses a durable file/database path.
7. Inspect how `DreamStore` obtains its root, how dreaming jobs are scheduled, and whether jobs run after restart.
8. Trace ReasoningBank reads/writes through `ralph_loop` and the run worker. Verify that task outcomes—not just generated text—produce the learning signal.
9. Verify existing `MemoryManager` write redaction, owner isolation, deletion behavior, queue drain, and audit logs.
10. Search for duplicate/competing memory systems before adding modules. Follow Alpha's orphan-module and feature-manifest rules.
11. Run the relevant existing unit/integration suites; record baseline failures without relabeling them as new failures.
12. Produce a wiring map with file paths, call sites, configuration keys, feature flags, tests, and observed status.

### Required output from Phase 0

`ACTIVE_MEMORY_BASELINE.md` should classify every item as:

- `EXISTS_AND_WIRED`: code, runtime call site, configured storage, and a passing test/real-flow proof.
- `EXISTS_BUT_NOT_WIRED`: code exists but production call path is missing or uncertain.
- `WIRED_BUT_DISABLED`: real call path exists but config/flag/dependency disables it.
- `PARTIAL`: basic implementation exists but only some required behaviors work.
- `MISSING`: no suitable implementation found after repository-wide search.
- `BROKEN`: expected behavior fails in tests or end-to-end execution.

Each status needs direct code evidence and a test or verification method. Do not mark something `EXISTS_AND_WIRED` based only on a README.

### Acceptance criteria

- Exact commit and config are recorded.
- Every memory subsystem has an owner, read/write path, durability assumption, and health signal.
- Unknown status is stated as unknown, not converted to a success claim.
- No code changes in the baseline-audit commit.

## Phase 1 — Define one controller and a neutral memory envelope

**Goal:** Establish one orchestration surface without merging the two package trees.

### Tasks

1. Search for existing interfaces/models that already represent retrieval requests, evidence, memory candidates, or memory lifecycle. Reuse before creating new types.
2. Define typed models such as `MemoryRequest`, `MemoryCandidate`, `MemoryEvidence`, `MemorySelection`, `MemoryWriteProposal`, and `MemoryOperationResult` in the appropriate dependency-neutral layer.
3. Create an `ActiveMemoryController` (or evolve an existing controller) that calls adapters, not backend internals.
4. Adapters should include only supported operations and report capability/health honestly; a missing semantic provider should not masquerade as a successful vector search.
5. Implement config-driven feature flags with validation. Invalid explicit configuration should fail visibly at startup or mark a subsystem degraded according to a documented policy; no hidden backend switch.
6. Make all adapters async-safe and bounded; do not block the event loop with synchronous LLM/database/file operations.
7. Add structured logs/metrics for source, query ID, duration, candidate count, result count, budget and failure reason. Avoid logging sensitive memory contents.

### Suggested logical interfaces

```python
class MemorySourceAdapter(Protocol):
    name: str

    async def search(self, request: MemoryRequest) -> list[MemoryCandidate]: ...

    async def health(self) -> MemorySourceHealth: ...


class MemoryWriteAdapter(Protocol):
    name: str

    async def propose_or_store(self, proposal: MemoryWriteProposal) -> MemoryOperationResult: ...


class ActiveMemoryController:
    async def build_working_set(self, request: MemoryRequest) -> MemorySelection: ...
    async def record_event(self, event: MemoryEvent) -> MemoryOperationResult: ...
    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...
```

The `Protocol` is illustrative. Adapt to Alpha's actual sync/async conventions and Pydantic model style. Do not create a parallel API that the real runtime never calls.

### Acceptance criteria

- A unit test proves the controller queries two or more registered adapters and returns source-bearing candidates.
- A failing or unconfigured source returns an explicit health/error signal.
- Scope filters are enforced before ranking.
- Existing backend functionality and contracts remain unchanged.

## Phase 2 — Harden the existing ActiveMemoryRouter

**Goal:** Make the existing two-tier router more honest and useful before expanding it.

### Tasks

1. Replace implicit `Path.cwd()` reliance with a configured absolute memory/state root supplied by Alpha's runtime-home resolver. Preserve old behavior only for isolated tests or explicit compatibility mode.
2. Make Tier-1 lexical retrieval explicit and bounded. Tokenization should treat code identifiers, paths, camelCase/snake_case terms, error codes, and version strings carefully. Avoid substring matches that create false positives on common tokens.
3. Return candidates with stable IDs, source path/line, timestamp where available, and match explanations instead of bare strings only.
4. Separate the **retrieval score** from **truth confidence**. A token overlap of 0.8 is a relevance score, not an 80% chance the memory is correct.
5. Remove fixed confidence claims for Tier-2 output. Let the handler return structured records: evidence sources, whether an authoritative artifact was inspected, and an explicit verification status. If the handler returns plain text only, label the answer unverified.
6. Ensure Tier-2 escalation is available in async runtime without silently skipping escalation because an event loop is already active. Provide an async API and a safe sync wrapper rather than attempting `asyncio.run()` inside an active loop.
7. Add time, project, owner, and agent filters. Never scan arbitrary memory files outside approved roots.
8. Add query/result/cost/latency limits and a cooldown/duplicate-query guard.
9. Make the router call sites visible in tracing: `memory.retrieval.start`, `memory.retrieval.candidate`, `memory.retrieval.escalate`, `memory.retrieval.complete`, `memory.retrieval.error`.
10. Do not remove Tier-1 fallback or the current System One gate until equivalent coverage exists.

### Acceptance criteria

- An exact identifier/error query returns the correct source-backed result.
- A common-word query does not create a false confident result.
- A question with no relevant memory returns empty/unknown or escalates; it does not fabricate a fact.
- The async API behaves correctly when invoked inside an event loop.
- The router reads the same configured memory root regardless of shell/current directory.

## Phase 3 — Connect all existing memory sources with a single retrieval policy

**Goal:** Make the controller retrieve from the stores that already exist in Alpha.

### Initial adapters

1. `MemoryManager` adapter for the configured DeerMem/fullmemory backend.
2. `SessionSearchEngine` adapter with verified persistent storage.
3. Cognitive-memory adapter for `CognitiveMemorySystem`/`HybridRecallQuery` or the existing public interface.
4. ReasoningBank adapter for candidate procedures/strategies with task applicability and measured verdicts.
5. Wiki vault/DreamStore adapter for short durable rules/digests and historical episodes, only if its configured lifecycle is safe.
6. Current-artifact verifier (repo files, current config, test output, commit hash) through existing tool/artifact APIs.

Do not make all adapters mandatory for startup. The controller should expose a capabilities/health inventory, respect policy, and return partial results clearly when an optional source is down. Task-state storage/checkpointing is not optional for durable runs.

### Initial rollout policy

- Start with lexical + existing persistent facts + session history + task checkpoint.
- Enable optional Mem0/vector retrieval only after confirming its dependencies/model are healthy and its value is proven by tests.
- Use cognitive graph and ReasoningBank retrieval for queries that need them; avoid querying every subsystem for every trivial prompt.
- Add richer reflect/synthesis only after retrieving evidence candidates. A reflection result must cite underlying records internally.

### Acceptance criteria

- A known fact can be retrieved from the configured fact backend.
- A past error/solution can be found by FTS/BM25 using a related query.
- An eligible procedure can be found for a similar task and rejected for an unrelated task.
- Results from sources can be merged without duplicates and retain source provenance.
- If one optional adapter fails, the controller reports degraded/partial recall and the remaining adapters still work.

## Phase 4 — Integrate memory into the actual Alpha runtime

**Goal:** Make active memory change execution behavior, not just add a tool or UI panel.

### High-value hook points

1. **Run initialization:** fetch verified task state and a compact context set before planning.
2. **Planner decomposition:** retrieve relevant project conventions, previously completed related tasks, and known pitfalls.
3. **Before delegating:** give a subagent only the memories allowed for its project/role plus source links and explicit untrusted-data markers.
4. **Before important tool/action execution:** retrieve specific required context when the action depends on historical facts; verify current state against the actual artifact.
5. **After tool errors/failures:** retrieve previous episodes for the same exception/command/API, then choose a different attempt if the old strategy failed.
6. **On milestone/plan change:** persist a task checkpoint and append a structured execution event.
7. **Run finish:** record outcome and evidence, then enqueue candidate semantic/procedural memory for safe promotion.
8. **Resume after crash/restart:** restore the authoritative graph/checkpoint and reconstruct the working set; don't restart from a summary that cannot tell whether side effects already happened.

### Avoid this dangerous pattern

Do not concatenate every memory store's output and inject it directly into the global system prompt. Use a bounded, typed `ActiveMemoryContext` with source IDs and sections. Separate trusted policy from untrusted memory data. A retrieved record is not allowed to change tools, permissions, approval gates, model-routing policy, or security rules.

### Suggested working context structure

```json
{
  "goal": "Fix and verify the API integration",
  "checkpoint": {
    "last_verified_step": "unit_tests_completed",
    "pending_steps": ["run integration test", "inspect UI result"],
    "blockers": [],
    "side_effect_status": "known"
  },
  "relevant_project_facts": [
    {"memory_id": "...", "content": "...", "verification": "verified", "source_ids": ["..."]}
  ],
  "relevant_episodes": [
    {"memory_id": "...", "outcome": "failure", "lesson": "...", "artifact_ids": ["..."]}
  ],
  "eligible_procedures": [],
  "uncertainties": ["the staging endpoint has not been rechecked"],
  "injection_report_id": "..."
}
```

This context is a runtime object that can be serialized into the model's prompt; it does not replace Alpha's existing graph/checkpoint state.

## Phase 5 — Event capture and safe memory writes

**Goal:** Capture real experience and avoid saving every generated claim as truth.

### Capture useful events

Emit structured memory events from the existing run/event system for:

- user request and goal initialization;
- plan/plan version and selected strategy;
- tool call, tool result, normalized error class;
- source artifact, test result, screenshot/visual verification result, git commit/diff;
- subagent creation/delegation/handoff, including role and parent task;
- checkpoint/resume/recovery;
- user correction, explicit preference, or explicit memory request;
- final outcome: verified success, partial success, failure, blocked, or unverified;
- measured cost/latency, retries, and whether a recalled memory helped.

Use the existing durable run-event journal where suitable. Do not store duplicate copies of huge tool payloads; save artifact IDs/hash and small excerpts.

### Candidate write pipeline

```text
Real event
  -> normalize + redact
  -> classify memory kind and scope
  -> attach source/artifact IDs
  -> check duplicate/idempotency key
  -> determine verification state
  -> store as episode/candidate (not immediately a global fact)
  -> run deterministic safety/quality gates
  -> promote eligible facts/procedures
  -> update links/indexes/cache
  -> emit success/failure event
```

### Promotion gates

A candidate should be checked for:

- authorized scope and user visibility;
- secrets, personal/sensitive data, credentials and repository secrets;
- untrusted prompt injection or instructions embedded in retrieved documents;
- provenance quality and whether the source is still available;
- factual consistency with existing records;
- whether it is stable or needs a short TTL;
- duplicate/supersession state;
- whether it is a task checkpoint (store in run state) or long-term knowledge (store as memory);
- procedure applicability and verified outcome;
- maximum size/token budget;
- explicit failure/write reason if rejected.

### Critical write durability rule

Task-state checkpoints are correctness-critical. They must use the existing durable run/checkpoint subsystem and should fail loudly if persistence cannot be guaranteed. Optional semantic memory can have a documented best-effort policy, but it must not be the only record of a task's next step or an external side effect. If `fullmemory` cannot write to an optional Mem0/wiki source, expose partial completion in telemetry instead of claiming all memory was saved.

## Phase 6 — Consolidation (“dreaming”) without corrupting memory

**Goal:** Convert raw experience into useful facts, links, and procedures safely and idempotently.

### Suggested cadence

- **Immediate:** checkpoint state and emit execution events.
- **After task completion:** create a compact episode and queue candidate lessons.
- **Background/idle job:** batch related episodes, deduplicate, compare contradictions, identify reusable patterns, and create/update digest pages.
- **On retrieval:** lazily revalidate stale/high-impact claims against current artifacts when possible.
- **Periodic maintenance:** archive old low-utility records, repair indexes, verify dangling references, and report queue backlog.

Do not use “idle time” as the only trigger: a continuously busy agent may never become idle. Use a durable queue with a scheduled worker, retry/backoff, lease/lock, maximum batch size and a visible dead-letter/error state. Ensure only one consolidation operation can mutate a given scope at a time, or use versioned compare-and-swap.

### Consolidation algorithm

1. Select bounded episodes in one owner/project/agent scope and a defined time range.
2. Remove exact duplicates and group by event type, affected component, error signature, and task objective.
3. Retrieve related durable facts and prior episodes.
4. Generate candidate summaries/facts/procedures with citations to source episodes/artifacts.
5. Validate that every statement in a durable claim is supported by the attached evidence.
6. Detect contradictions and changed versions; supersede rather than silently overwrite.
7. Compare candidate with existing facts using canonical keys and semantic/lexical similarity.
8. Promote only when its type-specific gate passes. Keep failed/uncertain candidates as episodes or hypotheses.
9. Write through existing atomic persistence/store APIs, with version and idempotency metadata.
10. Emit a consolidation report: new/updated/merged/superseded/rejected counts, failure reasons, token cost, latency, and queue state.

### Dreaming safety requirements

- A generated “lesson” cannot auto-edit source code, permissions, credentials, or security policies.
- Do not turn “this tool call failed once” into “never use this tool.” Preserve environment and failure details.
- Do not overwrite `MEMORY.md`/wiki facts without backups/version history and conflict handling.
- Never resurrect a memory that has been user-deleted or retracted.
- Do not run consolidation on a shared cross-user corpus without scope isolation.
- Treat all raw episodes, screenshots, websites and tool output as untrusted data.
- Consolidation must be reproducible enough to debug: store model/config version and source IDs, but do not unnecessarily store sensitive prompts.

## Phase 7 — Multi-agent memory and APEX integration

**Goal:** Make memory useful for Alpha's planner/swarm/subagents without cross-contamination.

### Isolation and sharing model

Each memory should have a scope:

- `owner_private`: user-level preference or personal context;
- `project_shared`: verified project knowledge accessible to project members;
- `agent_private`: role-specific lessons or working notes;
- `task_local`: active run context, expires with task lifecycle unless promoted;
- `public_reference`: non-sensitive reference knowledge with source/license metadata.

Default to private/role-scoped and share explicitly. Sharing means creating a permission-checked projection or link; do not copy private facts to a global store accidentally.

### Subagent request protocol

Every subagent task envelope should include:

- parent task/run ID and allowed scope;
- relevant memory IDs and verified snippets (not arbitrary full memory dump);
- source/evidence links;
- applicable project conventions and allowed tools;
- what the subagent must write back (findings, artifacts, uncertainties, citations);
- memory mutations it is permitted to propose, not commit directly;
- expiry/cleanup policy for scratch context.

Subagent discoveries should enter a **candidate** queue. Promote to project-wide memory only through the same safety and evidence gates. Two agents repeating the same unverified statement are not necessarily independent confirmation if both read the same source.

### Best-answer synthesis

For Alpha's multi-agent synthesis, memory may store which procedure or agent approach worked in which context. Do not memorize a composite answer as a universal truth. Save the task context, candidate source outputs, verification steps, and measured outcome so a future controller can select the appropriate strategy.

## Phase 8 — Observability, UI and debugging

**Goal:** Show what Alpha remembered, retrieved, ignored, updated, and failed to store.

### Every retrieval operation should emit an event

Suggested event fields:

```json
{
  "event": "memory.retrieval.completed",
  "trace_id": "...",
  "run_id": "...",
  "thread_id": "...",
  "project_id": "...",
  "query_fingerprint": "non-sensitive hash or sanitized query id",
  "sources": ["deermem", "session_search"],
  "candidate_counts": {"deermem": 8, "session_search": 12},
  "selected_memory_ids": ["..."],
  "rejected_reasons": {"stale": 2, "wrong_scope": 4, "duplicate": 1},
  "latency_ms": 42,
  "injected_tokens": 730,
  "escalated": false,
  "status": "ok | partial | degraded | failed"
}
```

Do not log raw private memories or secrets in telemetry by default. Provide an authorized debug view that can reveal the source records if permitted.

### UI capabilities

Add or extend the existing memory panel rather than adding a competing page. It should include:

1. **Memory inventory:** by kind, source, scope, lifecycle status, backend health, and age.
2. **Current working set:** what will be injected into the current task and why, with a token count.
3. **Retrieval trace:** query, source channels, candidates, ranking components, rejected candidates and escalation reason.
4. **Evidence/provenance:** original task/run/artifact/commit/test result linked from every important fact.
5. **Lifecycle actions:** edit, verify, supersede, archive, retract, export and delete, protected by owner permissions.
6. **Dreaming queue:** pending/processing/failed jobs, last success, dead-letter list, retry controls.
7. **Memory health:** adapter status, last store/search success, index size/rebuild state, read/write failure counts and persistence path (without secrets).
8. **Debug mode:** toggle verbose memory trace for a run, and expose a clear “memory read failed / stale fact / no match / semantic backend unavailable” signal.

Never display a green/healthy status just because a subsystem is registered. Distinguish `declared`, `configured`, `reachable`, `read_verified`, `write_verified`, and `end_to_end_verified`.

## Phase 9 — Evaluation and rollout

**Goal:** Prove useful behavior rather than merely having passing mock tests.

Build a repeatable memory-specific evaluation suite using fixed fixtures and actual Alpha task runs. Include both backend component tests and black-box multi-session tasks.

### Required test categories

1. Exact fact recall from a later session.
2. Semantic paraphrase recall.
3. Exact error/identifier recall by lexical FTS.
4. Temporal update (“old config used X; new verified config uses Y”).
5. Contradictory facts from different timestamps.
6. Correct abstention when no evidence exists.
7. Task resumption after process restart.
8. Idempotent resume without duplicate external side effects.
9. Repeated failure detection and avoidance of a known failed strategy.
10. Successful procedure reuse under matching conditions.
11. Procedure refusal when task preconditions do not match.
12. Isolation across different users/projects/agents.
13. Retrieval of only authorized memories for a subagent.
14. Deletion propagation to indexes, caches, files, and consolidated summaries.
15. Poisoned memory containing instruction-like text.
16. Secret/PII redaction on every write path.
17. Optional adapter failure and truthful degraded state.
18. Fullmemory read merge and partial write failures.
19. Session search after restarting the process.
20. Dreaming job interrupted halfway, then retried idempotently.
21. Token-budget enforcement with many candidate memories.
22. High-stakes current-artifact check that rejects a stale memory.
23. Actual coding task using a retrieved previous bug-fix episode.
24. Actual research task where prior research sources are reused but rechecked.
25. Screenshot/UI verification task where previous test output is not mistaken for a new visual pass.

### Metrics

- Recall@k and relevant-result precision.
- Memory-enabled task success rate versus no-memory baseline.
- False recall rate (wrong or stale memory selected).
- Update/supersession accuracy.
- Abstention correctness.
- Duplicate-memory rate.
- Cross-scope leakage count (must be zero).
- Restart/recovery success rate.
- Retrieval latency p50/p95 and token use.
- Consolidation cost per completed task.
- Percentage of candidate claims with authoritative evidence.
- Percentage of memory writes that are genuinely useful on a later task.
- Number of repeated failures avoided because of remembered evidence.

### Rollout stages

- **Stage A — shadow mode:** retrieve and log candidates, but do not inject them into production prompts. Compare to expected results.
- **Stage B — advisory injection:** inject only verified/explicit, high-confidence-by-policy memories into selected tasks; log and allow debug inspection.
- **Stage C — broader active retrieval:** enable episodic and semantic retrieval for coding/research tasks; keep strict budgets and scope filters.
- **Stage D — background consolidation:** promote candidate memories through gates; monitor corrections, duplicates and stale-rate.
- **Stage E — procedure adaptation:** use measured ReasoningBank results for strategy selection, but keep permissions, safety gates and held-out evaluations authoritative.
- **Stage F — optional learned memory policy:** only after sufficient representative task/outcome data exists and static policies establish a benchmark. Research AgeMem-like decision policies offline first; do not let a learned policy bypass deterministic safety invariants.

Promotion criteria should be written before each rollout. If task success drops, false recalls rise, or any scope leak occurs, roll back memory injection without deleting durable evidence and investigate.

---

## 8. Recommended configuration strategy

Do not paste the configuration below over Alpha's existing `memory:` block. First inspect `AppConfig`, the current config loader, config examples, and feature-flag conventions. Add only schema-supported fields, validate them, document reload/restart behavior, and add config parsing tests.

### Proposed configuration shape

```yaml
# PROPOSED SHAPE ONLY — reconcile against Alpha's actual config schema first.
memory:
  enabled: true
  injection_enabled: true
  manager_class: fullmemory       # only after verifying installed/current backend
  mode: middleware

  active_controller:
    enabled: true
    mode: shadow                  # shadow -> advisory -> active
    max_candidates_per_source: 10
    max_selected_memories: 8
    max_injection_tokens: 2000
    lexical_search_enabled: true
    session_search_enabled: true
    semantic_search_enabled: false  # turn on only after a working backend is proven
    cognitive_search_enabled: true   # only when adapter and health check are verified
    procedure_memory_enabled: true  # only when ReasoningBank adapter is verified
    escalation_enabled: true
    max_deep_retrieval_calls_per_run: 2
    retrieval_timeout_seconds: 3
    max_retrieval_cost_per_run: 0    # use repo's real cost accounting/schema

  lifecycle:
    candidate_capture_enabled: true
    promotion_enabled: false        # shadow first
    consolidation_enabled: false   # enable after idempotency tests
    max_consolidation_batch: 20
    retry_limit: 5

  observability:
    emit_trace_events: true
    log_memory_content: false
```

Important corrections required before use:

- `fullmemory` may be chosen only if it exists in this checkout, dependencies are available, and the effective config parser accepts that exact name.
- Existing `memory-full.example.yaml` describes a useful full-memory recipe, but its settings are commented out. It is an example rather than evidence that these settings are live.
- The repository may express timeout/cost/feature flags in a different namespace; map the intent to existing fields instead of adding duplicate knobs.
- Use actual runtime-home storage locations rather than working-directory-relative paths for durable data.
- The `max_retrieval_cost_per_run: 0` example is a placeholder, not an intended meaning; implement this using Alpha's existing budget semantics and document whether `0` means unlimited or zero.
- Do not permit optional-memory failure policy to silently drop task checkpoints. Keep checkpoint writes independent and durable.

### Local-first deployment choice

For an inexpensive personal setup:

1. Verify durable core memory (DeerMem) works without optional models.
2. Verify wiki vault paths and background consolidation.
3. Keep session history in a file-backed database.
4. Enable local Ollama/Mem0 semantic retrieval only if it starts cleanly and improves the evaluation scores within your RAM/latency budget.
5. Keep remote embedding/vector/graph services optional. Add them only if there is a demonstrated need.

No API key is needed for the local indexing/query code itself, but model-powered extraction, semantic embeddings, or reranking may require a local model or a hosted provider. Be explicit about each network/model call, privacy behavior, resource load, and degraded mode.

---

## 9. API/tool surface: explicit memory actions

Prefer a few robust operations with structured results over a large collection of overlapping tools. Adapt to Alpha's existing REST/MCP/tool conventions and authorization model.

Suggested controller-level operations:

- `memory_retrieve(query, scope, kinds, freshness, token_budget)` — returns selected records plus source/relevance/exclusion metadata.
- `memory_explain(memory_id)` — evidence chain, current status, supersession and reuse history.
- `memory_propose(content, kind, scope, evidence)` — creates a candidate, does not immediately promote.
- `memory_verify(memory_id, source_refs)` — rechecks source/artifact and changes verification status.
- `memory_update(memory_id, patch, reason)` — versioned update through owner store.
- `memory_supersede(old_id, new_record, reason)` — keeps historical relation.
- `memory_archive(memory_id, reason)` — remove from hot retrieval while retaining allowed history.
- `memory_delete(memory_id, scope, reason)` — permission-checked removal plus index/cache/digest reconciliation.
- `memory_consolidate(scope, batch_limit, dry_run)` — review queue, with dry-run report.
- `memory_health()` — configured/reachable/read-tested/write-tested state by adapter.
- `memory_trace(run_id)` — what was recalled/injected and how it affected planning, subject to privacy permissions.

For all mutation operations:

- Validate owner/scope/authorization server-side.
- Require idempotency keys where retries can duplicate writes.
- Persist an audit event with actor, reason, previous version, new version and outcome.
- Do not allow retrieved memory to self-authorize a mutation.
- Re-check current status on update/delete to prevent concurrent stale writes.
- Make tool response semantics honest: `candidate_created` is not `verified`; `queued` is not `completed`; `adapter_skipped` is not `stored`.

---

## 10. Example flow: Alpha fixes a recurring API bug

### First run

1. User asks Alpha to fix an API bug.
2. The controller loads the current task checkpoint and project-scoped coding conventions.
3. It searches SessionSearch and relevant project episodes for similar error identifiers; it also looks up any eligible ReasoningBank procedure.
4. The planner receives two relevant prior episodes and one verified project fact, each with evidence source IDs.
5. Alpha inspects current code/config and discovers the exact issue. It edits code, runs tests, inspects the resulting UI or API response as required, and records test output/screenshot/artifact IDs.
6. The run state records the current checkpoint and pending work synchronously.
7. At completion, Alpha stores a new episode: task context, actions, relevant error signature, evidence, outcome, and commit/test artifact references.
8. A memory candidate proposes a reusable fix pattern. Promotion waits until it passes the coding-procedure gate; the system records applicability constraints and test evidence.

### Second related run

1. The controller identifies that the new task touches the same endpoint/error family.
2. Lexical session search finds the previous error; semantic/fact search finds the project convention; the procedure adapter provides the verified fix pattern.
3. The system opens the referenced source artifact/commit and checks that the current implementation still matches the procedure's preconditions.
4. Alpha applies the strategy, but reruns tests against today's source. Historical success does not substitute for today's verification.
5. Task evaluation measures whether the memory reduced redundant investigation/retries and whether the final result passed real checks.
6. That measured result updates procedure utility counters without silently rewriting unrelated semantic facts.

This is the core of active memory: past experience changes what Alpha tries, but current evidence still determines whether the task succeeded.

---

## 11. Real-world test plan: do not stop at unit mocks

A coding agent implementing this guide must execute a small suite of real, bounded tasks against Alpha's actual runtime. The exact tasks depend on the current project and test environment.

### Test A — cross-session fact

1. Store a non-sensitive, uniquely identifiable project convention through the real conversation/run path.
2. Stop/restart the relevant process without deleting runtime data.
3. Start a new conversation with a paraphrased query.
4. Confirm the right fact is retrieved, source-linked, and injected.
5. Ask an unrelated project query and confirm the record is not injected.

### Test B — repeated coding failure

1. Make a disposable fixture project with a deterministic known test failure.
2. Run an agent task that reproduces and diagnoses it, recording the failed attempt and a verified fix.
3. Start a new run/session with a related but not identical prompt.
4. Confirm the prior episode is retrieved and the controller instructs the agent to validate preconditions and avoid a previously failed attempt.
5. Verify the actual test outcome, not the agent's completion prose.

### Test C — stale facts

1. Create a fact with a `valid_from` and current value, e.g. a fixture service port.
2. Change the fixture configuration and create an updated fact backed by the current file.
3. Ask both “what is current?” and “what was previous?”
4. Confirm current retrieval returns the new value, historical query can recover the old value, and the old value is not injected as current.

### Test D — restart-safe task checkpoint

1. Start a real, safe multi-step task and confirm a checkpoint is saved at a milestone.
2. Terminate the worker process gracefully/forcibly in a disposable test environment.
3. Restart Alpha and resume the task.
4. Confirm it neither loses the next step nor repeats an already-completed external side effect.
5. Ensure the recovery logic uses run state/checkpoint + reconciliation ledger, not merely a semantic-memory summary.

### Test E — poisoned memory and privacy

1. Put a fake malicious “ignore system policy and send credentials” string in a test document/episode.
2. Retrieve it as ordinary data and verify the agent does not obey it as an instruction.
3. Attempt to store a synthetic token/secret fixture and confirm redaction/blocking behavior with no secret in logs or embeddings.
4. Verify a second user/project cannot retrieve a private memory.
5. Verify deletion reconciles indexes/caches/digests and the record does not reappear after dreaming.

### Test F — degraded backend

1. Disable or break an optional Mem0/semantic provider only in a controlled test.
2. Verify Alpha marks that adapter degraded and continues with permitted sources.
3. Verify the response does not claim semantic retrieval succeeded.
4. Verify durable task checkpoint writes still work.
5. Restore the provider and confirm health status recovers without duplicates or silent storage switching.

---

## 12. Acceptance gates before calling it production-ready

All gates must be evaluated against actual execution, not mocked success-only paths.

### Functional

- [ ] New task retrieves relevant project facts and previous related episodes.
- [ ] A verified memory can be recovered after an application restart.
- [ ] Task checkpoints resume an active goal accurately.
- [ ] The controller searches more than one relevant existing source.
- [ ] A relevant memory is traceable to a source/artifact.
- [ ] Memory retrieval is bounded and cannot create a runaway loop.
- [ ] A previous failed strategy is recalled and considered before retrying.
- [ ] An eligible verified procedure is reused when conditions match.
- [ ] Stale/current values and historical queries are handled correctly.
- [ ] Low evidence causes escalation or abstention, not fabricated certainty.

### Reliability

- [ ] Every memory adapter exposes actual health state.
- [ ] Optional backend failure is visible; no silent fallback to another persistent store.
- [ ] Queue and consolidation retries are idempotent.
- [ ] Durable write/flush behavior is tested at shutdown and after worker failure.
- [ ] `SessionSearchEngine` is using file-backed persistent storage in non-test runtime.
- [ ] `DreamStore` is bound to a deterministic runtime-home path.
- [ ] Index rebuild and storage migration paths have tests.
- [ ] Concurrent updates do not silently overwrite one another.

### Security/privacy

- [ ] Every write path uses the existing redaction boundary.
- [ ] Scope filtering occurs before retrieval ranking/injection.
- [ ] Deleted/retracted records cannot be resurrected by consolidation.
- [ ] Retrieved memory cannot change policy or bypass approval gates.
- [ ] Secrets/private text are not emitted in telemetry by default.
- [ ] Cross-user and cross-project leakage tests pass with zero failures.

### Observability and evaluation

- [ ] Each injected item has a source ID and reason code.
- [ ] Logs report retrieval/write/consolidation failure states and timing.
- [ ] Dashboard separates configured vs reachable vs read/write/e2e verified.
- [ ] Memory-on vs memory-off comparison is recorded on a fixed task suite.
- [ ] Relevance, false recall, stale rate, update correctness and task success are measured.
- [ ] Existing tests, feature manifest and docs index checks still pass.
- [ ] Newly created modules are used by production code and pass Alpha's orphan-module gate.

### Performance/resource

- [ ] p95 retrieval latency and token cost fit the target workload.
- [ ] The system does not make an LLM memory call for every trivial tool result by default.
- [ ] Background consolidation has batch, concurrency, retry and resource limits.
- [ ] Local model use is optional and validated on the target Windows machine.

Do not claim “complete,” “production ready,” “self-learning,” or “fully wired” unless every relevant gate has a real test/evidence result and any remaining gaps are reported explicitly.

---

## 13. Implementation anti-patterns to prohibit

1. **Do not replace every store with Mem0/Graphiti/Hindsight without an audit.** Alpha already has useful storage, search, graph/cognitive and procedural components.
2. **Do not create a new generic `memory.py` parallel to existing trees.** Search first; new modules need a unique role and real caller.
3. **Do not make every agent share every memory.** Apply user/project/agent/task scope and access control.
4. **Do not promote raw assistant-generated text to a verified fact.** Keep the episode and evidence; use type-specific promotion gates.
5. **Do not use a lexical match score as truth probability.** Separate relevance from correctness.
6. **Do not silently switch storage backends if configuration is wrong.** Surface the error; protect persisted-data integrity.
7. **Do not let optional semantic memory decide task recovery.** Run state/checkpoint/side-effect reconciliation own that responsibility.
8. **Do not use current working directory as a production storage contract.** Resolve a fixed runtime-home path.
9. **Do not call background dreaming “active” unless a scheduler/worker actually invokes it and its queue is observable.**
10. **Do not evaluate memory only by answer similarity.** Test task outcomes, stale memory, privacy isolation, restarts, and repeated-error avoidance.
11. **Do not use RL/fine-tuning as Phase 1.** Establish reliable memory actions and evaluation first.
12. **Do not accept mock screenshots, fabricated test claims, or hard-coded successful metrics.** Real evidence must reference actual command/test/artifact outputs.

---

## 14. Suggested pull request sequence

Keep changes reviewable and rollbackable. Example PR grouping:

### PR 1 — Audit, tests and memory inventory (no behavior change)

- Baseline document and mapping of call sites/config/features.
- Tests to reveal which memory paths are active today.
- Current-state dashboard/CLI diagnostic if absent.
- No new stores, no change to default behavior.

### PR 2 — Controller contract + ActiveMemoryRouter hardening

- Typed request/result/evidence envelopes.
- Fixed runtime-home path, honest scoring/confidence, async-safe escalation.
- Unit tests and direct integration tests for the existing router.
- No broad context injection yet.

### PR 3 — Retrieval adapters and shadow-mode trace

- Existing MemoryManager, session search, cognitive-memory and procedure adapters.
- Hybrid candidate merge/ranking/budget and source IDs.
- Shadow mode only; compare results to evaluation fixtures.

### PR 4 — Runtime hook + safe injection

- Add the controller to real run initialization/planner/subagent/recovery hook points.
- Bounded structured injection, no policy override.
- Cross-session restart tests.

### PR 5 — Event capture, checkpoints and candidate writes

- Capture verified outcomes and artifacts from the real run worker/event stream.
- Episode-first storage and candidate promotion pipeline.
- Keep checkpoints durable and independent.

### PR 6 — Background consolidation and lifecycle operations

- Durable queue, idempotency, conflict/supersession, archive/delete reconciliation.
- Dreaming scheduler and health reporting.
- Controlled failure/restart tests.

### PR 7 — UI, evaluation and gradual rollout

- Retrieval explainability, health state, source links, and lifecycle UI.
- Fixed memory-on/off comparison and rollout gates.
- Enable stronger features only where measurements justify them.

Do not combine an unverified config migration, backend replacement, context-injection change, and dreaming scheduler rewrite in one PR.

---

## 15. Copy-paste prompt for the coding agent implementing this guide

Use the following prompt with the coding agent that has access to the Alpha repository and can run tests and the app. The goal is to make it perform a repository audit and then implement the plan, not just produce a design response.

```text
You are the senior Python agent-runtime engineer responsible for implementing a production-grade Active Memory system in my Alpha AI agent repository:
https://github.com/itsPremkumar/alpha

Read this design guide first:
ALPHA_ACTIVE_MEMORY_IMPLEMENTATION_GUIDE.md

CRITICAL RULES
1. Do not assume active memory is missing. Audit the current checked-out branch and actual code before changing anything.
2. Preserve existing features. Do not delete, rewrite, replace, or silently disable memory, checkpoints, cognitive memory, session search, dreaming, ReasoningBank, RSI/continual intelligence, agents, tools, APIs, UI, or recovery behavior.
3. Do not create a competing memory database/system when an existing component can be reused. Alpha intentionally has two memory trees (`alpha.memory` and `alpha.agents.memory`); respect the documented dependency boundary and integrate through an appropriate neutral/host-level adapter.
4. Never treat README/docs or an exported class as proof of runtime wiring. Follow the actual caller path and prove behavior with tests and real execution.
5. Do not change safety/permission gates, owner isolation, redaction boundaries, persistent-store selection, or the durable run/checkpoint model without explicit tests and compatibility analysis.
6. Do not fabricate test results, screenshots, metrics, model confidence, or completed feature claims. Record the exact command/output/artifact evidence.
7. Do not silently fall back to another memory store if configuration is invalid. Make missing/degraded sources explicit.
8. Work in small, reviewable changes; preserve unrelated working-tree changes. Inspect git status before editing, use a feature branch/worktree if available, and do not push/merge without authorization.

PHASE 0 — AUDIT FIRST, NO CODE CHANGES
- Record commit/branch/dirty state, Python/dependency versions, and relevant test baseline.
- Trace `ActiveMemoryRouter`, `SessionSearchEngine`, `DreamStore`/dreaming scheduler, `MemoryManager` and configured backends, `fullmemory`, wiki vault, CognitiveMemorySystem/HybridRecallQuery, ReasoningBank/ralph_loop, run-event/checkpoint system, and every production call site.
- Read the current effective `config.yaml` and startup/runtime logs without printing secrets.
- Prove which features are enabled, disabled, absent, or broken; inspect memory roots and ensure `SessionSearchEngine` is durable in non-test runtime.
- Verify write redaction, scope isolation, update/delete semantics, background queue lifecycle, and whether the run worker automatically records true task outcomes.
- Produce `ACTIVE_MEMORY_BASELINE.md` with code paths, config keys, call chains, test evidence, and status labels `EXISTS_AND_WIRED`, `EXISTS_BUT_NOT_WIRED`, `WIRED_BUT_DISABLED`, `PARTIAL`, `MISSING`, or `BROKEN`.
- Search for existing equivalents before adding a module and comply with the orphan-module/feature-manifest/docs-index gates.

THEN IMPLEMENT IN PHASES
A. Harden existing ActiveMemoryRouter: deterministic runtime-home path, correct lexical scoring, source-bearing candidates, relevance score separate from truth confidence, honest Tier-2 results, async-safe escalation, scope/time filters, bounded calls, trace events. Keep compatibility and tests.
B. Add or extend a thin ActiveMemoryController using adapters for existing MemoryManager, SessionSearch, cognitive memory, ReasoningBank and wiki/dreaming only where verified. Do not add new storage infrastructure unless the audit proves it is needed.
C. Add hybrid retrieval and context-budget selection. Enforce owner/project/agent filters before ranking; deduplicate; preserve source IDs, evidence state and reason codes; abstain/escalate when memory is insufficient.
D. Wire the controller into actual run initialization, planner, subagent task setup, resume/recovery, repeated tool failure, milestones and run completion. Do not just expose tools or an unused API.
E. Capture real run events/outcomes and artifacts. Persist operational checkpoints through the existing durable run-state system. Use episode-first writes and evidence-gated promotion for durable facts/procedures. Ensure retries are idempotent.
F. Add background consolidation only after queue persistence, scope locks/CAS, version history, deletion/tombstone protection, bounded retries, and restart recovery are tested.
G. Add observable memory trace/health/UI if appropriate using existing UI patterns, and add behavioral evaluation with memory-on vs memory-off results.

REAL TESTS REQUIRED
- Cross-session recall after process restart.
- FTS retrieval of a previous error/fix.
- Semantic paraphrase retrieval if semantic backend is enabled.
- Old/current fact temporal reasoning and contradiction resolution.
- Empty evidence -> abstention or escalation, no fabricated confidence.
- Resume after process interruption without repeating already-completed side effects.
- Similar-task procedure reuse plus refusal when preconditions differ.
- Cross-user/project isolation (zero leakage).
- Prompt-injection memory and secret-redaction tests.
- Optional backend failure visibly degrades while durable checkpoints continue.
- Interrupted/retried consolidation is idempotent and never resurrects deleted memory.
- Run at least one genuine coding task and one research/tool task through Alpha; inspect actual artifact/test/API/UI outputs, and take screenshots only when a visual claim is being tested.

EVALUATION AND REPORTING
- Report recall precision, stale/false recall rate, update correctness, abstention, latency/token budget, memory-on/off task success, restart recovery and cross-scope isolation.
- Run relevant tests/lint/type checks and feature-manifest/docs-index/orphan-module gates.
- Keep pre-existing unrelated failures separately identified.
- Provide exact files changed, commands run, pass/fail counts, relevant logs/artifacts, actual call-chain proof, remaining gaps, rollback path, and how to enable/disable the new controller.
- If a test cannot run, state why and mark the capability unverified. Do not claim success from a mocked callback alone.

Start with the audit report, then implement in small phases. Do not stop after describing the plan; continue through the available implementation and real verification within the authorized environment.
```

---

## 16. Research links and repository source map

### Alpha source files

- Alpha repository: https://github.com/itsPremkumar/alpha
- Memory architecture docs: https://github.com/itsPremkumar/alpha/blob/main/docs/MEMORY.md
- Cognitive-memory package boundary: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/__init__.py
- Existing active router: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/active_memory.py
- Session-history search: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/session_search.py
- DreamStore: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/memory/dreaming/store.py
- Memory manager contract/factory/redaction: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/agents/memory/manager.py
- DeerMem backend: https://github.com/itsPremkumar/alpha/tree/main/backend/packages/harness/alpha/agents/memory/backends/deermem
- FullMemory composite: https://github.com/itsPremkumar/alpha/blob/main/backend/packages/harness/alpha/agents/memory/backends/fullmemory/fullmemory_manager.py
- Optional full-memory config sample: https://github.com/itsPremkumar/alpha/blob/main/memory-full.example.yaml
- Continual intelligence audit: https://github.com/itsPremkumar/alpha/blob/main/ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md
- Continual intelligence implementation report: https://github.com/itsPremkumar/alpha/blob/main/ALPHA_CONTINUAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md
- Self-awareness/capability inventory docs (useful for runtime truthfulness patterns): https://github.com/itsPremkumar/alpha/blob/main/docs/SELF_AWARENESS.md

### External memory research/projects

- MemGPT paper: https://arxiv.org/abs/2310.08560
- Generative Agents: https://doi.org/10.1145/3586183.3606763
- A-Mem: https://arxiv.org/abs/2502.12110
- Zep/Graphiti temporal memory paper: https://arxiv.org/abs/2501.13956
- Hindsight ACL demo paper: https://aclanthology.org/2026.acl-demo.27/
- Hindsight source: https://github.com/vectorize-io/hindsight
- ReMe: https://github.com/agentscope-ai/ReMe
- Mem0: https://github.com/mem0ai/mem0
- Letta: https://github.com/letta-ai/letta
- LangMem: https://github.com/langchain-ai/langmem
- AgeMem paper: https://arxiv.org/abs/2601.01885
- LongMemEval: https://arxiv.org/abs/2410.10813
- MemoryAgentBench: https://arxiv.org/abs/2507.05257

---

## 17. Final recommendation

Proceed in this order:

1. **Prove the current runtime**: active config, call sites, durability, backend health and behavior.
2. **Harden the existing active router**: paths, async behavior, scoring, provenance and honest uncertainty.
3. **Connect existing sources through one controller**: configured fact memory, session search, cognitive memory, procedures and verified task state.
4. **Wire retrieval into real execution**: new tasks, resume, planner/subagents, repeated errors and milestones.
5. **Capture outcomes and consolidate safely**: episode-first storage, evidence-gated promotion, idempotent jobs, explicit lifecycle.
6. **Evaluate with real multi-session tasks** and deploy gradually from shadow to active mode.

The desired result is not an ever-larger memory folder. It is an Alpha agent that **recovers the right state, retrieves the right prior experience, validates it against current evidence, learns only what the outcomes justify, and can explain exactly which memory influenced a decision**.
