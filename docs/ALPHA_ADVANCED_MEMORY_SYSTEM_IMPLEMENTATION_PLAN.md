# Alpha AI Agent — Advanced Memory System: Research, Architecture, and Implementation Plan

**Version:** 1.0  
**Research date:** 16 October 2026  
**Target:** `github.com/itsPremkumar/alpha`  
**Purpose:** An implementation-ready plan for evolving Alpha's documented memory foundation into a reliable, local-first, multimodal, multi-agent, evidence-grounded Memory OS.

> **Important scope note:** This document is based on Alpha's publicly visible repository and memory documentation plus current public research and framework documentation. Repository documentation describes capabilities such as episodic replay, cognitive memory, a knowledge graph, active-memory fusion, dreaming/consolidation, and ReasoningBank-style procedure memory. I have not executed Alpha's code or verified that every documented capability is enabled, fully wired, crash-safe, or effective in a live run. Treat the first phase below as a runtime audit, not as an assumption that those features work.

---

## 1. Executive decision

Do not rebuild Alpha's memory from scratch and do not define “advanced memory” as simply storing every conversation in a vector database. Alpha's repository already documents a broad base: short-term thread state/checkpoints, long-term vector memory and key-value facts, episodic task experiences, a cognitive concept store, a semantic knowledge graph, ReasoningBank-like procedure memory, consolidation during idle time, and an optional active-memory fusion path. The next step is to make these parts behave as **one governed, observable, versioned memory system** rather than as disconnected features.

Build **Alpha Memory OS (AMOS)** as a unified memory plane with five capabilities:

1. **Remember:** Capture durable, useful experience and facts with source, time, owner, scope, confidence, and evidence.
2. **Manage:** Deduplicate, update, link, version, expire, archive, and forget information without silently destroying its history.
3. **Recall:** Search the right stores for the current goal, combine results, resolve time and contradiction issues, and return evidence-grounded context within a token budget.
4. **Learn:** Convert verified task outcomes into reusable strategies, skills, and warnings through controlled reflection and consolidation.
5. **Govern and observe:** Enforce scope/permissions, protect secrets, explain every recall, show health and failures, and prove memory quality with repeatable evaluations.

The target is not “remember everything.” It is **retrieve the right information at the right time, distinguish verified facts from guesses, adapt from measured outcomes, and remain safe and inspectable over long-running work.** No system can honestly claim to be the world's best without competitive, reproducible evaluations; this plan defines a serious path toward that goal.

### Non-negotiable principles

- **Evidence before confidence:** A memory is not true merely because Alpha stored it or retrieved it.
- **The source of truth is durable records:** Vector indexes, graph edges, summaries, and caches are derived indexes and must be rebuildable.
- **Atomic facts plus original episodes:** Store small searchable facts while retaining the source episode/document needed to verify them.
- **Temporal correctness:** Track when a statement was recorded and when it was true; preserve historical facts after supersession.
- **Hybrid retrieval:** Exact keywords, semantic similarity, metadata filters, time, graph relationships, and task experience have complementary strengths.
- **Memory is untrusted data:** A recalled record can contain malicious instructions; it must never outrank system policy or tool permissions.
- **Explicit user instructions outrank inferred preferences:** Record provenance and allow correction, inspection, export, and deletion.
- **Local-first and zero-mandatory-cost:** The core must function without a paid model or external database; optional providers may improve quality but cannot be required for correctness or basic operation.
- **Fail visibly:** A failed memory store, unavailable embedding model, inaccessible graph, or stale index must appear as a health/error state—not an empty result pretending memory contains nothing.
- **Learn only from measured outcomes:** A failed run is not a reusable strategy; a success without verification is not a proven success.
- **Additive migration:** Preserve current functionality, schemas, APIs, and on-disk records; use adapters, feature flags, migrations, backups, and rollback.

---

## 2. What Alpha already documents — and what must be verified

The public Alpha repository and `docs/MEMORY.md` describe the following components. These are the starting inventory, not a live runtime certification.

| Documented area | Existing foundation in Alpha's docs | Audit questions before extending it |
|---|---|---|
| Short-term / working memory | Thread messages, tool calls/results, run state, checkpoints, context-window summarization | Does a real run persist after each critical step? Can it resume after force-kill, model error, or Windows restart? Does summarization preserve unresolved tasks and constraints? |
| Long-term semantic memory | Vector embeddings, key-value facts, memory search/store APIs, owner/project/thread filtering | Are records truly owner-scoped at query time? Can updates and deletes reach every index? Is lexical search available if embeddings fail? |
| Episodic memory | Task/skill experiences, curation, consolidation, replay, archival | Are tool inputs/outputs and verified outcomes captured? Are failures represented? Can replay be prevented from executing unsafe side effects? |
| Cognitive memory | Per-owner concept storage, relationships, inference-rule-like structures, snapshots/WAL described in docs | Does it share identifiers and provenance with other stores? Are snapshots restored in a real crash test? Are graph mutations consistent with canonical facts? |
| Knowledge graph | Entity, document, skill, project, user nodes and relationships; PostgreSQL/AGE and alternative backends documented | Which backend actually runs in the packaged Windows build? Does graph search return current facts with source citations and time validity? |
| Procedure / strategy memory | README documents evidence-gated ReasoningBank and `ralph_loop` consumption | What constitutes a verified win? Are attempt counts, versions, applicability, and regressions measured? Is a “strategy” tested before promotion? |
| Active-memory fusion | README describes optional `memory.fusion.enabled` fusion of `MEMORY.md`, configured backend, and session history; described as off by default | Is the flag visible in UI/config? Does the feature actually route queries through all intended sources? Does it return individual source health and provenance? |
| Idle-time “dreaming” / consolidation | README describes idle-time consolidation | Is there a real scheduler/worker? Is it bounded by CPU, RAM, model cost, and user-idle detection? Does it produce an audit trail and reversible changes? |
| UI and APIs | `docs/MEMORY.md` documents search, graph, episodes, cognitive browser, stats, provider/retention/privacy settings | Do these views exist, render accurate live data, surface errors, and show provenance? Do delete/export/config actions work end to end? |
| Auditability and telemetry | README describes trace IDs, run-event feed and artifact lineage | Are memory writes, retrieval candidates, reranking, context inclusion, mutations, and failed lookups included in traces? |

### First task for the coding agent

Create `docs/MEMORY_RUNTIME_AUDIT.md` before changing memory logic. For each feature above record:

- **Code evidence:** exact module/function/config key/API route/UI component.
- **Call path:** actual producer → service → store → index → retriever → prompt/context injection.
- **Configuration:** default, enabling steps, environment variables, provider requirements.
- **State:** `verified`, `partially verified`, `documented only`, `broken`, `not implemented`, or `unknown`.
- **Runtime proof:** a real integration test or captured trace, not just a unit test or repository description.
- **Failure behavior:** what the UI, logs, and agent do when that dependency fails.
- **Migration impact:** any schema, data, or compatibility risks.

Do not delete or replace a feature just because its wiring is hard to find. First identify the real implementation and preserve it. If documentation and behavior disagree, update the docs only after the runtime behavior has been independently verified.

---

## 3. Research findings that should shape Alpha

This plan combines complementary approaches rather than copying any single project.

### 3.1 Memory as an operating system, not a message transcript

- **MemGPT** treats the model's context as a limited fast-memory tier and moves information between context and external storage. The key lesson for Alpha is to manage context like a scarce resource: page in relevant evidence, summarize carefully, preserve pointers to full records, and record what was evicted. [MemGPT paper](https://arxiv.org/abs/2310.08560)
- **MemoryOS** proposes short-, mid-, and long-term tiers with storage, updating, retrieval, and generation modules. The useful design principle is hierarchical lifecycle management rather than an undifferentiated archive. [MemoryOS paper / ACL Anthology](https://aclanthology.org/2025.emnlp-main.1318/)
- **MemOS** frames memory as a governed operational resource spanning different representations. Alpha should adopt its explicit lifecycle and migration thinking without turning a single-user Windows installation into an unnecessarily complex cluster. [MemOS paper](https://arxiv.org/abs/2505.22101)

### 3.2 Different kinds of memory solve different jobs

LangGraph's memory guidance distinguishes **semantic** memory (facts), **episodic** memory (experiences), and **procedural** memory (instructions/skills), and separately distinguishes thread-local short-term state from long-term cross-session stores. This distinction is especially useful for Alpha: a fact such as “the test suite uses `pnpm verify`” is not the same record as an episode describing a failed test run, and neither is identical to a verified repair procedure. [LangGraph memory concepts](https://github.com/langchain-ai/docs/blob/main/src/oss/concepts/memory.mdx)

### 3.3 A vector database is not enough

- **Mem0** shows a practical extract/update/search architecture and an entity-linked graph layer. The important pattern is to look up related memories before adding or updating a fact, and to combine graph relationships with semantic search. Alpha's design should preserve its own canonical records and not assume that graph links alone provide a complete provenance trail. [Mem0 graph-memory docs](https://github.com/mem0ai/mem0/blob/main/docs/platform/features/graph-memory.mdx)
- **Graphiti/Zep** emphasize temporal context graphs: episodes are the source trail, entities organize knowledge, and facts/edges can have valid-time windows and be superseded without erasing history. Alpha should use the same bitemporal idea for changing project config, tools, model endpoints, dependencies, user preferences, and task status. [Graphiti docs](https://help.getzep.com/graphiti/getting-started/welcome)
- **A-MEM** explores dynamic links and memory evolution inspired by interconnected notes. Alpha can adopt incremental linking and controlled reorganization, but graph rewrites must be versioned and reversible. [A-MEM paper](https://arxiv.org/abs/2502.12110) · [code](https://github.com/agiresearch/A-mem)

### 3.4 Reflection must produce transferable, verified experience

- **Generative Agents** combine a memory stream, reflection, and dynamic retrieval. The useful Alpha lesson is that higher-level reflection should be derived from specific events, not invented as free-floating “wisdom.” [Generative Agents paper](https://doi.org/10.1145/3586183.3606763)
- **Voyager** builds a skill library of executable programs and reuses skills when a similar task appears. For Alpha this maps to tools, coding recipes, skills, and runbooks—but any executable memory requires sandboxing, versioning, and tests before reuse. [Voyager paper](https://openreview.net/pdf?id=P8E4Br72j3)
- **ReasoningBank** focuses on reusable reasoning experiences; the public README already describes Alpha's evidence-gated procedure memory as a production consumer of the self-improvement loop. Improve the existing path rather than adding a competing, uncoordinated “strategy database.” [ReasoningBank paper (ICLR 2026)](https://arxiv.org/abs/2509.25140)

### 3.5 Memory management is itself a policy decision

**Memory-R1** studies memory managers that choose operations such as `ADD`, `UPDATE`, `DELETE`, and `NOOP`, and separates memory management from the answering agent. Alpha should follow this separation at the interface level now. Reinforcement-learning training is a later experiment, not a requirement for the first production implementation. [Memory-R1 paper](https://arxiv.org/abs/2508.19828)

### 3.6 Evaluation needs more than a “needle in a haystack” test

- **LongMemEval** evaluates extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. It contains 500 questions and shows that sustained interactions remain challenging even for long-context systems. [ICLR 2025 paper](https://proceedings.iclr.cc/paper_files/paper/2025/hash/d813d324dbf0598bbdc9c8e79740ed01-Abstract-Conference.html) · [benchmark code/data](https://github.com/xiaowu0162/LongMemEval)
- **LoCoMo** focuses on very long conversations with temporal/casual dynamics and includes question-answering, event summaries, and multimodal dialogue tasks. [ACL paper](https://aclanthology.org/2024.acl-long.747/) · [repository](https://github.com/snap-research/locomo)
- A 2026 survey highlights silent “memory blindness,” retrieval errors, forgetting, and the need to measure task quality, memory quality, efficiency, and governance separately. This is directly relevant to Alpha's stated goal of visible failures and real-work validation. [Memory for Autonomous LLM Agents (2026 survey)](https://arxiv.org/abs/2603.07670)
- A 2026 survey also frames progress as **storage → reflection → experience** and explicitly cautions that research designs are not directly comparable across models and benchmarks. [From Storage to Experience (2026 survey)](https://arxiv.org/abs/2605.06716)

**Research synthesis:** Alpha's differentiator should be the integration of evidence-grounded facts, temporal graph reasoning, task episodes, tested procedures, active retrieval, low-cost local operation, and transparent evaluation. Adding ten storage engines without lifecycle discipline will make memory more complicated, not more intelligent.

---

## 4. Complete memory taxonomy for Alpha

“Memory type” should be represented as structured metadata and behavior—not necessarily as a separate database for each type. Types overlap. For example, one debugging run can be a raw episode, produce a semantic fact about a repository, update a project-state memory, and yield a candidate procedure after verification.

### 4.1 Runtime and temporal scope

| Type | What it stores | Example in Alpha | Lifetime / handling |
|---|---|---|---|
| Context / activation memory | Tokens and intermediate state currently inside a model invocation | Current plan, selected evidence, tool results | Ephemeral; never treat as durable until committed to a persistent store |
| Working memory | Active facts, constraints, open questions, current subgoal, next action | “Run failing test, inspect error, patch function, rerun test” | While task/run is active; periodically checkpointed |
| Thread / short-term memory | Messages, tool calls, results, attachments and thread summary | Current chat and recent context | Thread-scoped; compact without losing references to full messages |
| Run / execution memory | Current worker state, leases, retries, budget, pending tool calls | Agent resumes after a process crash | Durable checkpoint until task ends and retention policy allows archive |
| Goal / prospective memory | Future intentions, deadlines, acceptance criteria, pending actions and reminders | “Verify graph retrieval after embedding provider returns” | Until fulfilled, cancelled, or superseded; must include status and due/trigger condition |
| Mid-term memory | Summaries of recent threads, active projects and ongoing work | Current sprint/project state | Repeatedly consolidated; should point to detailed records |
| Long-term persistent memory | Stable facts, preferences, procedures and durable project knowledge | User-approved preference or proven repo convention | Durable, but versioned, correctable and deletable |
| Archive / cold memory | Old low-access episodes and raw artifacts not needed on each turn | A months-old build failure | Compressed/indexed cheaply; still searchable by deeper retrieval |

### 4.2 Content and cognitive function

| Type | What it stores | Alpha example | Promotion rule |
|---|---|---|---|
| Episodic / event memory | A specific observed sequence with context, actions and outcome | “At commit X, test Y failed after dependency Z changed; rollback restored it” | Capture meaningful run events; preserve raw evidence and outcome verification |
| Semantic / fact memory | Atomic facts and concepts independent of a single episode | “The project uses Python 3.12” | Extract from trustworthy evidence; mark source and validity window |
| Procedural / skill memory | Reusable instructions, workflows, recipes, tested tool patterns | Verified process for reproducing and fixing a bug class | Require reproducible success, scope of applicability, version, tests and rollback guidance |
| Profile / preference memory | Stable user choices and working preferences | “Prefers complete Markdown implementation plans” | Explicit statements are high confidence; inferred preferences must be marked and easy to correct |
| Project / workspace memory | Per-project decisions, architecture, constraints, milestones and design rationale | Alpha's memory-store contract and current refactor state | Scoped to project; link to files, commits, issues, goal IDs and ADRs |
| Codebase / repository memory | Build/test commands, module ownership, symbols, dependency edges, recent changes and known bugs | “Memory fusion is configured by `memory.fusion.enabled`” | Invalidate/reindex when branch, commit, lockfile or relevant files change |
| Social / agent-team memory | Agent roles, trust, delegation history, expertise and collaboration protocols | Specialist agent's proven repository-audit performance | Separate private agent memory from shared project/team memory; audit every share |
| World / source memory | External facts, documentation, provider specifications, citations and crawl dates | Official API changes or a library's documented behavior | Keep URL, title, access date, publication date if known, quote/snippet policy and source reliability |
| Conceptual / cognitive memory | Concepts, entity identities, typed relations, hypotheses and structured models | Relationship between Alpha's agent runtime, tools and checkpoints | Merge entities carefully; retain evidence behind inferred relations |
| Causal memory | Cause-effect hypotheses backed by observations | “This restart failure correlates with a child process exiting when its parent shell closes” | Label as hypothesis until tested; include confidence, experiment and competing explanation |
| Counterfactual / alternative memory | Tested alternatives and why they did or did not work | “SQLite-only deployment works offline; graph service adds setup burden” | Store alternatives and evidence; do not treat an untested alternative as a failed fact |
| Negative / failure memory | Failed approaches, error signatures, unsafe actions, known dead ends | Reproducible migration failure and the condition that caused it | Keep only actionable, contextualized failures; expire if versions/environment change |
| Reflective / meta-cognitive memory | Lessons about Alpha's own planning, retrieval, tool use and uncertainty | “The plan omitted a live UI verification step” | Derived from a trace and outcome; must point to underlying episodes and be evaluated |
| Evidence / belief memory | Claim, provenance, evidence set, confidence, dispute status and falsification conditions | “Memory search returned the right item in 8 of 10 trials” | Distinguish observation, source claim, inference, and verified result |
| Temporal memory | When a fact was observed, when it was valid, when it expired or was superseded | Provider/model/config changes over time | Use valid time + record time, rather than overwriting history |
| Environmental / state memory | Machine, operating system, runtime versions, available tools, resource limits and current environment | Windows version, RAM, Python/Node version, backend availability | Timestamp and scope; revalidate before acting because environments drift |
| Tool / API memory | Tool schemas, successful invocation examples, known parameter constraints and errors | Known MCP server tool capabilities | Recheck live schema/version before critical calls; never rely on stale remembered capability alone |
| Artifact / document memory | Generated files, screenshots, logs, reports, source docs and their locations/hashes | Screenshot evidence validating Alpha's memory UI | Store metadata and references; avoid duplicating large binary content into prompts |
| Multimodal / perceptual memory | Image, audio, video, UI screenshot, OCR/transcription and extracted relations | Screenshot shows a missing memory-source badge | Store asset pointer/hash plus derived caption, entities and timestamps; preserve access policy |
| Trust / provenance memory | Source reliability, tool health, agent track record and evidence lineage | A provider frequently times out; a claim is source-backed | Keep reliability scoped to source/version/task; do not turn correlation into universal trust |
| Audit / event-log memory | What was captured, retrieved, ignored, modified, deleted and injected into context | Trace of why a specific memory influenced a goal | Append-only / tamper-evident where practical; access-controlled and redacted |
| Parametric memory (optional research) | Knowledge encoded into trained model weights | Fine-tuning a model on examples | Not the primary memory plan; model weights are hard to inspect, selectively delete and audit |
| Latent / activation memory (research) | Hidden states or compressed internal representations | Model-specific persistent latent state | Experimental only; do not promise cross-model portability or human-like understanding |

### 4.3 Memory-management capabilities (orthogonal to content types)

Every store must support or explicitly declare its support for:

- **Capture:** accept raw event, candidate memory, or explicit user instruction.
- **Classification:** assign memory type, scope, sensitivity, temporal status, and source class.
- **Consolidation:** compress, link, summarize, and elevate repeated evidence into stable concepts.
- **Retrieval:** exact lookup, keyword, semantic, structured, temporal, graph, episodic, and procedural recall.
- **Update / supersession:** add a new version and mark the previous version as no longer current.
- **Forget / delete:** remove or tombstone the source record, embeddings, graph nodes/edges, caches, summaries and derived memory where required.
- **Decay / archival:** reduce prompt-time priority as relevance fades without making important history unreachable.
- **Replay:** examine a historical task trace in a sandbox; do not blindly re-execute side effects.
- **Self-evaluation:** compare remembered claims and chosen memories with outcome evidence.
- **Governance:** scope, retention, access policies, audit, export and provenance.

---

## 5. Target architecture: Alpha Memory OS

### 5.1 Logical topology

```text
Chat / Goal Engine / Coding Agent / Browser / Tools / MCP / Subagents
                              |
                              v
                    Memory Client / SDK
                              |
                              v
                 Scope + Policy + Identity Gate
                              |
               +--------------+---------------+
               |                              |
               v                              v
        WRITE / UPDATE PATH             READ / RECALL PATH
     capture event/candidate            classify current query
     type & sensitivity                 resolve owner/project/goal
     source/provenance                  derive time/intent constraints
     deduplicate / conflict              parallel retrieval channels
     policy decision                    scope/filter before reranking
     canonical commit                   rank + verify + deconflict
     enqueue index jobs                 build token-bounded context
               |                              |
               v                              v
      Canonical Memory Ledger <----> Context Packet + Evidence Links
               |
     +---------+---------+-------------------+--------------------+
     |         |         |                   |                    |
     v         v         v                   v                    v
  FTS/BM25  Embeddings  Facts/edges       Episodes             Procedures
   index      index     temporal graph    & artifacts          / skills
     |         |         |                   |                    |
     +---------+---------+-------------------+--------------------+
                              |
                              v
                   Background Memory Workers
        consolidate / dream / reconcile / re-embed / archive
        replay-evaluate / provenance-check / index-repair / forget
                              |
                              v
               Trace, Metrics, Memory Explorer, Alerts
```

### 5.2 Canonical record store vs. derived indexes

**Rule:** The canonical ledger is the source of truth. Embeddings, full-text indexes, graph materializations, summaries, caches, and retrieval rankings are derived. If any derived index is lost, Alpha must be able to rebuild it from canonical records and immutable source references.

Recommended progression for Alpha:

1. **Default local install:** SQLite with WAL, transactions, migrations, full-text search (FTS5 where supported), and relational tables for records, edges, evidence links, episodes, procedures, policy, trace events and job queue. Add the existing supported lightweight vector adapter only after a real packaged-install test.
2. **Optional larger local/server install:** PostgreSQL plus pgvector; use relational constraints and transactional migrations. Enable a graph extension/backend only when the actual retrieval workload benefits from it.
3. **Optional temporal graph adapter:** Graphiti or another proven graph implementation can be evaluated behind an adapter for bitemporal relations and hybrid search. Do not make an external graph database mandatory to run Alpha on a modest Windows machine.
4. **Embedding adapters:** support a local embedding model and configured remote providers. When no embedding provider is usable, lexical/metadata/graph/recency retrieval still works; Alpha must show degraded capability rather than return a false “nothing found.”

The exact chosen packages should be based on current Alpha dependencies, license compatibility, Windows packaging, performance tests and maintenance burden—not on popularity alone.

### 5.3 The memory manager must be independent from the answer agent

Create one stable memory service/interface used by the lead agent, specialists, subagents, tools, goal engine, UI, consolidation workers, and evaluation harness. An agent may request a memory operation but should not directly write arbitrary rows, rebuild embeddings, mutate the graph, or bypass access control.

Suggested interface (names are illustrative and should be adapted to existing Alpha conventions):

```python
capture_event(event, context) -> CaptureReceipt
propose_memory(candidate, evidence) -> MemoryDecision
search(query, scope, intent, budget) -> MemoryResultSet
get(memory_id, scope) -> MemoryRecord | NotFound
update(memory_id, patch, evidence, expected_version) -> UpdateReceipt
supersede(memory_id, replacement, evidence) -> SupersessionReceipt
forget(memory_id_or_query, scope, policy) -> ForgetReceipt
explain_recall(query_id) -> RecallExplanation
consolidate(scope, budget, mode) -> ConsolidationReport
rebuild_indexes(scope, index_names) -> IndexHealthReport
get_health() -> MemoryHealthReport
export(scope, filters) -> ExportManifest
```

`MemoryDecision` should be a structured result such as `STORE`, `UPDATE`, `LINK`, `NOOP`, `ASK_USER`, `QUARANTINE`, `ARCHIVE`, or `REJECT`, accompanied by the reason, evidence references, confidence and affected record IDs. Deletion and security-sensitive decisions should not be left to an unconstrained model prompt.

---

## 6. Canonical memory data model

Use one normalized model with type-specific payloads. This prevents facts, graph edges, episodes and procedures from drifting apart and supports a unified search API.

### 6.1 Base `MemoryRecord`

Recommended fields:

| Field | Purpose |
|---|---|
| `id` | Stable UUID/ULID, never derived from mutable content |
| `schema_version` | Version of the record shape for migrations |
| `owner_id` | Authenticated owner; resolved by server code, never trusted from an arbitrary model argument |
| `namespace` | `user`, `agent`, `project`, `goal`, `thread`, `team`, `system`, or another registered scope |
| `namespace_id` | Actual scoped identity; combined with owner and ACL for isolation |
| `memory_type` | One or more types from the taxonomy above |
| `title` | Short human-readable label |
| `content` | Human-readable content; avoid placing large artifacts or secrets here |
| `structured_payload` | JSON/schema-specific fields for facts, episodes, relations or procedures |
| `epistemic_status` | `observed`, `explicit_user_statement`, `source_claim`, `inference`, `hypothesis`, `verified_result`, `preference`, `instruction`, `plan`, `unknown` |
| `source_kind` | `user`, `assistant`, `tool`, `repository`, `document`, `web`, `system_event`, `subagent`, `derived` |
| `source_ref` | Original message/event/document/URL/artifact ID |
| `evidence_refs[]` | Evidence required to support this record |
| `created_at` | When the record was created in memory |
| `observed_at` | When Alpha observed the source event |
| `valid_from` / `valid_to` | Real-world time interval for which the fact applies; `valid_to` null means not currently known to have ended |
| `supersedes[]` / `superseded_by[]` | Version history and replacement relations |
| `confidence` | Calibrated confidence or `null` if not meaningfully calibrated; never false precision |
| `importance` | Expected long-term utility to Alpha/user, with reason codes |
| `trust_class` | Trust level for the source, scoped to the source and use case |
| `sensitivity` | `public`, `internal`, `personal`, `sensitive`, `secret` |
| `acl` | Read/write/share rules or policy reference |
| `tags[]` | Search facets and human labels |
| `entity_ids[]` | Canonical identities connected to this record |
| `related_memory_ids[]` | Explicit links to records, episodes, goals, files, commits or tasks |
| `embedding_status` / `embedding_model_version` | Index state and model compatibility |
| `content_hash` | Idempotency/change detection; hash normalized content plus schema version as appropriate |
| `access_count` / `last_accessed_at` | Observed access statistics—not evidence that a memory is true |
| `last_confirmed_at` | When source validity was most recently confirmed |
| `expires_at` / `retention_policy_id` | Retention controls and automatic expiry where appropriate |
| `deleted_at` / `deletion_status` | Tombstone and deletion workflow state |
| `created_by_agent_id` / `run_id` / `goal_id` | Traceability to the process that made the record |
| `version` | Optimistic concurrency control for safe updates |

Do not require every memory type to fill every field. Use validated schema variants and explicit nulls for not-applicable properties.

### 6.2 Bitemporal facts

Represent facts as an atomic subject-predicate-object or key-value statement with **record time** and **valid time**. For example:

```json
{
  "id": "fact_01H...",
  "memory_type": ["semantic", "project", "temporal"],
  "subject": "alpha.memory_fusion",
  "predicate": "default_enabled",
  "object": false,
  "epistemic_status": "verified_result",
  "source_kind": "repository",
  "source_ref": "https://github.com/itsPremkumar/alpha/blob/main/README.md",
  "evidence_refs": ["episode_01H..."],
  "confidence": 0.99,
  "observed_at": "2026-10-16T00:00:00Z",
  "valid_from": "2026-10-16T00:00:00Z",
  "valid_to": null,
  "created_at": "2026-10-16T00:00:00Z",
  "supersedes": [],
  "sensitivity": "internal",
  "namespace": "project",
  "namespace_id": "alpha",
  "version": 1
}
```

This is an example schema, not a claim that all fields or IDs already exist in Alpha.

When a new source says a setting changed to `true`, do **not** silently overwrite the earlier value. Create a new version, close the prior valid-time interval when justified, link the evidence, and preserve the history. If sources conflict and neither is authoritative, store an unresolved conflict or ask the user rather than making the newest source automatically “true.”

### 6.3 Episodes

An episode should retain enough structure to reproduce the reasoning and verify the result without storing a giant prompt transcript in every index.

Recommended payload:

```json
{
  "episode_id": "episode_...",
  "goal_id": "goal_...",
  "task_type": "debugging",
  "environment": {"os": "Windows", "repo": "alpha", "commit": "..."},
  "initial_state_ref": "artifact_...",
  "steps": [
    {"step": 1, "action": "run_test", "input_ref": "artifact_...", "output_ref": "artifact_...", "status": "failed"},
    {"step": 2, "action": "patch", "diff_ref": "artifact_...", "status": "completed"},
    {"step": 3, "action": "run_test", "output_ref": "artifact_...", "status": "passed"}
  ],
  "outcome": {"status": "verified_success", "verification": ["test_command", "exit_code", "artifact_ref"]},
  "failure_signatures": [],
  "lesson_candidates": ["..."],
  "source_trace_id": "trace_...",
  "captured_at": "...",
  "replay_safety": "sandbox_only"
}
```

Sensitive environment values, credentials, tokens, and secret-bearing command arguments must be redacted before persistence. Keep artifact pointers and hashes so the episode remains compact.

### 6.4 Procedures / ReasoningBank items

A reusable procedure must carry evidence and scope, not just a persuasive natural-language summary.

Required fields:

- `procedure_id`, title, task signature / applicability conditions, prerequisites, steps, expected observations, verification steps, rollback steps, risk class, required permissions and version.
- `source_episode_ids[]` and supporting artifact/evidence IDs.
- `attempt_count`, `success_count`, `verified_success_count`, `failure_count`, outcomes by environment/model/tool version, and recency.
- Known counterexamples, limitations, confidence interval or explicitly uncalibrated reliability estimate.
- Promotion state: `candidate`, `sandbox_verified`, `trusted`, `deprecated`, `quarantined`.
- Last validation date and invalidation triggers, such as dependency/version/OS changes.

**Promotion rule:** No generated procedure becomes trusted solely because one model claims it worked. It must have reproducible success evidence or explicit human approval. A procedure that can modify files, run commands, alter system state, send messages, or access the network requires appropriate tool permissions and may require a sandbox/approval gate.

### 6.5 Evidence and provenance objects

Represent the source as a first-class record, separate from the claim it supports:

- `evidence_id`, source type, canonical URI/path, source title, publisher/owner, content hash, observed/published times, retrieval time, relevant span/line/page, extraction method, reliability notes, redaction status and access policy.
- Link evidence to memory records with a typed relation: `supports`, `contradicts`, `qualifies`, `derived_from`, `supersedes`, `observed_in`, or `verified_by`.
- Preserve quotations/snippets only as legally and operationally appropriate; use references and focused excerpts rather than duplicating full third-party documents.

---

## 7. Memory write path: what Alpha should remember and how

### 7.1 Write pipeline

Implement the pipeline as discrete, traced stages:

1. **Capture raw event:** user message, tool action/result, goal transition, observation, document ingestion, feedback, explicit memory command, or completed episode.
2. **Redact and classify:** remove secrets; identify sensitivity and scope; determine whether the source is trusted, user-supplied, external, inferred, or untrusted.
3. **Extract candidate records:** produce atomic facts, preferences, decisions, entities, relationships, goals, failure signatures, episode summaries, and lesson candidates as appropriate.
4. **Check policy:** handle explicit remember/forget commands, scope/ACL, retention, safety restrictions, and allowed write operations.
5. **Find related records:** exact key, entity ID, keyword, embedding, graph neighbors, and recent records in the same scope.
6. **Decide operation:** `STORE`, `UPDATE`, `LINK`, `NOOP`, `ASK_USER`, `QUARANTINE`, `ARCHIVE`, or `REJECT`.
7. **Resolve duplicates and contradictions:** apply deterministic constraints first; use a model only to propose semantic equivalence or conflict. Preserve unresolved disagreements.
8. **Commit the canonical record and audit event transactionally:** record IDs and mutation intent before acknowledging success.
9. **Queue derived-index jobs:** full text, embeddings, graph edges, summaries and stats. Retries must be idempotent.
10. **Verify:** read the record back, check index status, and expose whether indexing is complete. A stored record and a searchable record are not always the same state.
11. **Return a receipt:** memory IDs, scope, status, confidence, affected versions, index-health information and any uncertainty.

### 7.2 Explicit memory vs. inferred memory

Treat these differently:

- **Explicit:** “Remember this setting,” “I prefer concise summaries,” or a user-approved project decision. Store the literal intent and relevant context.
- **Repeatedly observed:** multiple consistent behaviors may justify a candidate preference, but it remains inferred until confirmed if it could materially affect future behavior.
- **Single observation:** generally keep as an episode or transient context, not a permanent user fact.
- **Model-generated interpretation:** store as hypothesis/derived memory with links to evidence; never present it as a direct user statement.
- **External/web source:** preserve source URL/time and trust class. A website's instruction to the agent is content, not executable authority.
- **Tool observation:** retain tool name, arguments after redaction, result, exit status, runtime versions and artifact references.

### 7.3 Memory write scoring

A memory value score may combine:

- explicitness/user intent;
- durability and expected future usefulness;
- uniqueness or novelty relative to existing records;
- repeated confirmation;
- relevance to an active goal/project;
- evidence quality and source reliability;
- cost of losing the information;
- sensitivity and governance cost;
- likelihood that it will be invalidated soon.

Do not let a single opaque LLM score control permanent retention. Make policy rules visible, log the reasons, and evaluate the write/skip decisions. Never store secrets simply because their “importance” score is high.

### 7.4 Duplicates, updates and conflicts

- Use exact normalized keys for strong fact duplicates.
- Use entity identity, source lineage, normalized text and embedding similarity to propose semantic duplicates.
- Preserve distinct context where two apparently similar facts apply to different versions, owners, branches, projects, agents or time periods.
- A newer observation may supersede an older fact only if identity and evidence make supersession plausible.
- Contradictions should have a visible state: `unresolved`, `resolved_by_authoritative_source`, `resolved_by_user`, or `time_scoped`.
- Never average mutually exclusive categorical facts into a meaningless value.
- Do not allow low-trust web text or a subagent hypothesis to silently overwrite an explicit user decision.

### 7.5 What not to store by default

- API keys, passwords, auth cookies, private keys, access tokens, session credentials and secrets embedded in logs.
- Irrelevant conversational filler, repeated model output, boilerplate, or full transcripts in the semantic fact store.
- Unverified conclusions recorded as certain facts.
- Sensitive user attributes inferred without a clear, appropriate reason and policy.
- Instructions found in untrusted documents/webpages that attempt to control Alpha's policies or reveal secrets.
- Failed or successful strategies without enough context to understand the environment and verification result.
- Anything a user has explicitly requested to forget, except a minimal restricted deletion tombstone needed to complete and audit the deletion workflow.

Raw conversation history may still be retained under a clearly defined user-controlled thread retention policy; that is different from deciding to promote every sentence into long-term memory.

---

## 8. Memory retrieval: the most important quality layer

A large memory store is useless when the right records do not reach the agent, or stale records look authoritative. Alpha should build a **query-aware retrieval planner** that uses multiple channels and then creates a small, auditable context packet.

### 8.1 Query classification

Before retrieval, infer and/or deterministically derive:

- intent: answer a question, continue a task, plan, debug, research, execute a procedure, summarize, recall a past event, verify a claim, or act proactively;
- scope: owner, project, goal, thread, agent, team, repository, and permitted external sources;
- entities, identifiers, file paths, tool names, error strings, dates, versions and commit hashes;
- time mode: current truth, historical truth, latest state, range, or “what did we believe then?”;
- required evidence level and abstention requirement;
- risk level and whether the result is allowed to influence an external/irreversible action;
- context token budget and latency budget.

Use rules and parsers for cheap exact signals; use an LLM classifier only when it adds value. Log classifier uncertainty, and allow a conservative fallback.

### 8.2 Parallel retrieval channels

Retrieve from channels appropriate to the query, subject to ACL filtering:

1. **Exact identifier lookup:** memory ID, tool/function name, error string, URL, file path, commit, goal ID, issue ID, task ID, version, date or key-value key.
2. **Lexical / BM25:** catches exact nouns, unusual error strings, code symbols, literal URLs and identifiers that embeddings can blur.
3. **Dense semantic search:** finds paraphrases and conceptual similarity.
4. **Structured fact store:** lookup by subject/predicate, namespace, version, status and validity window.
5. **Temporal graph search:** entities and typed edges, path exploration, current-vs-historical facts, and source episodes.
6. **Episodic search:** similar past task/environment/action/outcome sequences, not just similar prose.
7. **Procedure retrieval:** relevant trusted skills, runbooks and ReasoningBank candidates based on task signature and preconditions.
8. **Project/codebase retrieval:** current branch/commit, files, tests, architecture decisions and recently verified repository observations.
9. **Working/goal memory:** pending steps, open questions, acceptance criteria and current task state.
10. **Artifact lookup:** relevant logs, screenshots, generated files, diffs, benchmark reports, images, audio/video descriptions and other external artifacts.
11. **Recent/active memory:** recent decisions or user corrections likely to matter even when semantic similarity is weak.
12. **Negative-memory retrieval:** known failure signatures, unsafe procedures, recurring retries, and approaches that previously failed in a similar environment.

Do not run all channels for every query. The planner should select channels based on query shape, cost, measured utility and latency. For example, an exact stack trace should prioritize lexical search; “what changed since last week?” should prioritize temporal facts and recent episodes; “fix this failing test” should prioritize repository state, related episodes, and trusted procedures.

### 8.3 Candidate fusion and ranking

A robust initial design:

1. Retrieve a bounded candidate set from each selected channel.
2. Filter by owner/ACL, deletion status, sensitivity policy, project/goal scope, time constraints and minimum evidence constraints **before** any model sees the candidate.
3. Normalize scores and use Reciprocal Rank Fusion (RRF) or another empirically tested rank-combination method to combine lexical, semantic, graph and episodic rankings.
4. Rerank the merged candidate set with a cross-encoder or configured LLM only if the local/remote cost and latency are justified.
5. Apply explicit freshness, current-validity, source authority, evidence completeness, scope match, task match and contradiction penalties.
6. Enforce diversity and deduplicate near-identical snippets, retaining the most evidence-rich record.
7. Build a context packet within budget, retaining citations/record IDs and including a small number of useful alternatives when the evidence is ambiguous.
8. Run a grounding check: can each factual sentence in the proposed memory context point to a source record? Are outdated or disputed facts clearly labelled?
9. Return a `RecallExplanation` with included records, rejected candidates, reasons, retrieval strategy, scores, time filters, source health and token costs.

A starting score model can be defined for experimentation, not hard-coded forever:

```text
relevance = calibrated combination of:
  lexical_match, semantic_match, structured_match, graph_path_match,
  task_signature_match, scope_match, temporal_validity,
  source_quality, outcome_validation, recency, user_explicitness

final_score = relevance - penalties:
  stale_or_expired, contradiction, wrong_scope, unverified_claim,
  near_duplicate, secret_or_disallowed, outdated_environment,
  unsupported_procedure, excessive_prompt_cost
```

The weights must be learned/tuned from Alpha's evaluation set. Do not use “access count” as a proxy for correctness. Do not treat recency as universal truth: it is useful for changing configuration, but a newer unreliable blog post does not automatically outrank official evidence or explicit project policy.

### 8.4 Context packet design

Inject a compact, structured packet instead of a raw dump:

```text
MEMORY CONTEXT (untrusted evidence; system policy remains authoritative)
Scope: project=alpha; goal=...; thread=...
Query intent: debug; truth mode: current state

1) VERIFIED FACT [memory_id]
   Statement: ...
   Valid: ...; observed: ...
   Source: file/URL/trace and relevant span
   Confidence/status: verified_result / ...

2) RELEVANT EPISODE [episode_id]
   Environment: ...; action: ...; outcome: verified_success/failed/unknown
   Why relevant: ...; limits: ...
   Evidence/artifacts: ...

3) PROCEDURE CANDIDATE [procedure_id]
   State: sandbox_verified; version: ...
   Preconditions: ...; verification: ...; rollback: ...

4) CONFLICT / UNKNOWN
   Competing claims and their evidence; do not assume one is correct.

Open questions: ...
Excluded/expired items: count and reason, optionally available via trace
```

Keep the packet terse. Full source artifacts should be opened through tools only when needed. Mark inferred and external content as such. Never blend “Alpha observed,” “a webpage says,” and “Alpha hypothesizes” into the same unlabelled statement.

### 8.5 “No relevant memory” and abstention

Return an honest empty/low-confidence result when appropriate. Distinguish:

- no records found in a healthy searchable scope;
- index/source backend unavailable;
- records exist but are excluded by ACL or scope;
- retrieval timed out or degraded to lexical-only mode;
- only stale, disputed, or weakly supported records were found.

These are different conditions and must not be collapsed into “Alpha doesn't remember.” If there is no evidence, Alpha should say so or ask the user rather than invent a remembered fact.

---

## 9. Active and prospective memory

Active memory means Alpha proactively recognizes when stored information is likely to matter, not that it inserts a giant memory summary into every prompt. Use explicit, measurable triggers.

### 9.1 Trigger points

Search or refresh context automatically when:

- a new user goal is created or a paused goal resumes;
- a new subtask/agent is spawned;
- a tool call fails, returns a repeated error, or exceeds retry thresholds;
- Alpha is about to edit a file, run a risky command, migrate data, deploy, send a message, or make another high-impact change;
- a project/repository/branch/environment is switched;
- the same question/task pattern appears again;
- a user corrects Alpha or explicitly asks it to remember/forget something;
- a model/provider changes or a capability-health check fails;
- the run approaches the context threshold or checkpoints a meaningful decision;
- an acceptance criterion is nearly complete and prior verification procedures can be reused;
- the system enters idle time and a maintenance budget is available.

### 9.2 Goal memory

For each goal preserve:

- goal statement, owner, scope, priority, created/updated times;
- acceptance criteria and evidence each criterion requires;
- planned subtasks, dependency DAG, current state, blockers, next action and deadlines;
- decisions made, alternatives rejected, constraints, user approvals and unresolved questions;
- linked runs/agents/artifacts/commits/tests;
- the latest verified checkpoint and what remains unverified.

On resume, create a compact context packet consisting of the current goal state, last durable checkpoint, open questions, recent relevant episode(s), required acceptance criteria and any material environment drift. Do not rely solely on a natural-language summary when there is structured state available.

### 9.3 Prospective memory / reminders

Store intention as a typed object, not as a sentence buried in a transcript:

```json
{
  "id": "intent_...",
  "trigger": {"kind": "event", "event": "index_rebuild_completed"},
  "action": {"kind": "verify", "target": "memory_search"},
  "owner_scope": "project:alpha",
  "status": "pending",
  "created_at": "...",
  "expires_at": null,
  "approval_required": false,
  "evidence_required": ["integration_test", "trace"]
}
```

All scheduled and event-triggered reminders must be idempotent; missed triggers should become visible pending work, not silently disappear.

**Implemented bounded slice (2026-10-16):** the lead-agent lifecycle now captures
only explicit user-authored “remind me to …” and “don't let me forget to …”
phrases when both `memory.enabled` and `memory.prospective.enabled` are on. A
runtime-resolved owner is required and message/run identity supplies a stable
idempotency key. Existing prospective recall composition injects owner- and
agent-filtered active items behind the same opt-in gates. Capture now also
includes the deterministic half of due-date parsing (added 2026-10-16):
`alpha.memory.prospective.dates` turns an unambiguous explicit phrase in the
captured content (relative offset, self-disambiguating clock phrase, day cue,
weekday, “next week”/“next month”) into an auditable `due_at` plus
`due_phrase`/`due_rule` record metadata, returning "undated" (`None`) for
ambiguous or unsupported phrases instead of a guessed date. Scheduler delivery,
generic commitment inference, project scope, and cross-process exactly-once
behavior remain open; a parsed due time is not delivered as a notification.
See `backend/packages/harness/alpha/memory/prospective/AGENTS.md`.

---

## 10. Episodic memory, replay and error learning

### 10.1 Capture meaningful episodes

Capture an episode when a task is completed, fails meaningfully, needs a human intervention, encounters a new environment/tool issue, reaches a milestone, or changes a persistent project decision. Include both successes and failures; filter routine no-op events to avoid enormous low-value history.

### 10.2 Replay safely

“Replay” should default to **analysis/reproduction in an isolated workspace**:

- reconstruct inputs from redacted artifacts;
- pin repo commit, environment and tool versions when feasible;
- do not replay real payment, external messaging, production changes, credential use or other irreversible side effects;
- substitute mock/dry-run tools where possible;
- require fresh authorization and approval for high-risk actions;
- compare current and prior outputs;
- store a new episode for the replay attempt rather than rewriting the historical episode.

### 10.3 Failure memory

An actionable failure record should include the error signature, conditions, affected versions, attempted fixes, which attempts were disproven, verified root cause (or “unknown”), final remediation, rollback and validation. “This didn't work” without environment/context is not useful long-term memory.

### 10.4 Promote episodes into skills

Promotion pipeline:

1. Identify repeated or high-value episodes.
2. Cluster by task signature and environment.
3. Ask a lesson-extraction process to propose a generalized procedure with explicit preconditions and limits.
4. Check for contradictions with existing procedures and current documentation.
5. Generate deterministic tests or a reproducible verification script.
6. Validate in a sandbox; for code-changing skills, run static analysis and targeted tests.
7. Record attempts, outcomes, regressions, and supported environment versions.
8. Promote from candidate to trusted only after the policy threshold or manual approval.
9. Revalidate or deprecate when code, dependencies, tools, model schemas or OS assumptions change.

---

## 11. Semantic memory, entity identity and temporal knowledge graph

### 11.1 Graph model

A graph complements—not replaces—the memory ledger and text search.

Suggested nodes:

- `Person`, `User`, `Agent`, `Team`, `Project`, `Goal`, `Repository`, `File`, `CodeSymbol`, `Tool`, `MCPServer`, `Model`, `Provider`, `Package`, `Document`, `Episode`, `Fact`, `Procedure`, `Artifact`, `Environment`, `ErrorSignature`.

Suggested typed edges:

- `OWNS`, `MEMBER_OF`, `DELEGATED_TO`, `DEPENDS_ON`, `IMPLEMENTS`, `CONFIGURED_BY`, `USES_TOOL`, `PRODUCED`, `OBSERVED_IN`, `SUPPORTED_BY`, `CONTRADICTS`, `SUPERSEDES`, `CAUSES_HYPOTHESIS`, `REPRODUCES`, `FIXED_BY`, `VALIDATED_BY`, `APPLIES_TO`, `INVALIDATED_BY`, `RELATED_TO`.

Every derived relationship should include provenance, confidence/status, source episode/evidence, created/valid time, namespace and an explanation of whether it is observed or inferred. Do not assume co-occurrence proves a typed semantic relationship.

### 11.2 Identity resolution

Avoid creating a new entity every time an LLM paraphrases a name. Resolve entities using canonical IDs, normalized identifiers, exact known aliases, project/namespace and cautious fuzzy matching. For repositories, prefer remote URL + owner/repo identity; for code, include commit/branch/path/symbol identity when relevant; for tools/models, preserve provider and version.

When uncertain, keep separate entities linked as possible duplicates instead of merging incorrectly. Entity merges must be reviewable/reversible and preserve source lineage.

### 11.3 Temporal graph semantics

A relationship should support:

- `recorded_at`: when Alpha learned or stored it;
- `valid_from` / `valid_to`: when the relation was true in the world or system;
- source episode/evidence and record status;
- supersession history;
- current/historical query semantics.

Queries like “what is the current provider?” and “what provider was configured before the October change?” must deliberately use different time modes. Historical fact records should remain accessible unless deletion policy requires otherwise.

### 11.4 When to use graph traversal

Use graph traversal for multi-hop questions (e.g., tool → MCP server → provider → project configuration), entity-centric recall, dependency impact, and relationships spread across many episodes. Do not pay graph extraction cost for every trivial memory, and do not require a graph backend to answer a simple exact-key lookup.

---

## 12. Cognitive memory and reflection

Alpha's documented cognitive memory can become the concept-and-hypothesis layer of AMOS rather than an isolated side store.

### 12.1 Concepts

Concept records should contain a stable ID, canonical name, aliases, type, scoped properties, linked facts, source evidence, confidence, and version. Store a distinction between:

- observed property;
- source-quoted claim;
- general learned concept;
- agent-generated hypothesis;
- deprecated or contradicted idea.

### 12.2 Rules and hypotheses

Each rule should specify its premises, conclusion, scope, confidence, evidence, number of tested applications, failures/counterexamples and status. A logical-looking rule is not necessarily valid just because an LLM produced it. Rules that cause actions should have explicit tests and permission/risk constraints.

### 12.3 Reflection/dreaming cycle

During eligible idle periods, a bounded worker may:

1. select recent episodes with verified outcomes or meaningful failure evidence;
2. identify repetitive details, contradictions, unanswered questions and reusable lessons;
3. produce candidate summaries, graph links, fact updates or procedure improvements;
4. attach source episode and artifact references;
5. run contradiction, sensitivity, duplicate and scope checks;
6. create versioned proposals or low-risk updates under policy;
7. run retrieval and evaluation checks;
8. log every accepted/rejected change and its measured effect.

Do not let “dreaming” rewrite the system prompt, install new tools, alter permissions, send communications, delete critical data, or modify production code without a separate verified workflow. High-impact reflections should be proposals, not silent mutations.

### 12.4 Consolidation budget

For Windows-first/lower-RAM installs:

- run only while the machine is idle or according to a user-configured schedule;
- use small bounded batches and cancellable jobs;
- enforce CPU/RAM/disk/token/network budgets;
- avoid starting another local LLM process if RAM pressure is high;
- checkpoint progress and make all jobs resumable;
- prioritize current active projects, repeated access and verified task experience;
- expose queue depth, estimated work, last success, next run and errors in the UI.

---

## 13. Procedural improvement and self-improvement integration

Use the existing documented ReasoningBank / `ralph_loop` path as the primary procedure-learning mechanism if runtime verification confirms it is connected. Do not introduce a second unrelated strategy store.

### 13.1 Learning data path

```text
Run trace + tool events + tests/artifacts
                 |
                 v
       Outcome verifier / evaluator
                 |
       +---------+----------+
       |                    |
       v                    v
  Verified success       Failure/unknown
       |                    |
       v                    v
 Strategy candidate    Failure signature + context
       |                    |
       v                    v
 Preconditions + limits + evidence links
                 |
                 v
     Sandbox replay / regression tests
                 |
                 v
    Candidate -> Trusted / Deprecated
                 |
                 v
      Retrieval by future task signature
```

### 13.2 Measured outcomes

For every procedure track:

- attempts, verified successes, failures, unknown outcomes, human interventions;
- task/environment/model/tool-version breakdown;
- success relative to a baseline procedure or no-memory baseline;
- time-to-completion, tool calls, token/cost use and regression rate;
- confidence and uncertainty intervals when sample sizes support them;
- invalidation triggers and review date.

A procedure that saves time but increases failure risk may not be an improvement. Store those trade-offs rather than compressing them into an unqualified “best strategy.”

### 13.3 No uncontrolled self-modification

Memory may propose updated skills or instructions. Promotion must be gated by tests, review, a reversible version, and separate authorization for high-impact actions. Do not permit a recalled strategy to override current system instructions, approval gates, tool permissions or project acceptance criteria.

---

## 14. Multi-agent, subagent, swarm and project memory

Alpha has a multi-agent/goal-oriented architecture. Shared memory needs stricter boundaries than a single chat history.

### 14.1 Memory scopes

Use a hierarchy of namespaces with explicit sharing, for example:

```text
owner
├── personal profile and preferences
├── global approved knowledge
├── agent-private memories
│   ├── lead orchestrator
│   ├── coding specialist
│   └── research specialist
├── project:alpha
│   ├── architecture / decisions
│   ├── repository facts
│   ├── project episodes / procedures
│   └── goals and acceptance evidence
├── goal:<goal-id>
├── thread:<thread-id>
└── team:<team-id> (explicitly shared records only)
```

### 14.2 Rules

- Resolve owner and scope on the server from authenticated execution context, not from model-generated JSON.
- A subagent gets the smallest context required by its task, not the entire owner's personal memory.
- Private agent memory must not be shared automatically across agents; promote it to project/team scope through an explicit policy.
- Store delegation contracts, task inputs, outputs, evidence, decisions, conflicts and provenance in a shared project/goal namespace when collaboration requires it.
- A receiving agent must be able to tell whether a record comes from the user, another agent, a tool, or an inferred summary.
- Do not trust memory generated by another agent more than the evidence warrants. Cross-agent agreement is not independent corroboration when agents share the same prompt/model/source.
- Use idempotency keys and version checks to prevent parallel agents from duplicating facts or overwriting a newer decision.
- For concurrent writes, use transactions/optimistic concurrency and a deterministic conflict policy; route material disagreements to the orchestrator or user.
- Every memory-sharing action should be auditable.

### 14.3 Multi-agent memory roles

Potential workers, preferably as bounded jobs rather than always-running expensive agents:

- **Capture worker:** transforms events into candidate memories.
- **Retrieval planner:** selects channels and budgets.
- **Evidence verifier:** checks claims against source spans and runtime artifacts.
- **Conflict resolver:** proposes temporal reconciliation or surfaces unresolved contradiction.
- **Consolidator/dreamer:** creates summaries and generalized lessons.
- **Procedure evaluator:** sandbox-tests strategies and skills.
- **Memory auditor:** finds leaks, stale records, orphaned indexes, missing provenance and policy violations.

These can be lightweight functions or model calls. Do not create a swarm for trivial CRUD operations; every agent should have a measurable reason to exist.

---

## 15. Multimodal memory

Treat images, screenshots, audio, video and files as first-class artifacts with derived searchable descriptions, not as enormous blobs inserted into every prompt.

### 15.1 Data path

1. Store the original artifact, hash, mime type, size, creation time, source, scope, ACL and retention policy.
2. Extract metadata such as image dimensions, audio duration, video frame times, and document page/section structure.
3. Generate permitted derived views: caption, transcript, scene summary, screen/UI elements, code error text, entities, timestamps and embeddings.
4. Maintain provenance from every derived fact back to artifact and specific page/frame/region/time span.
5. Retrieve the description first and load the original artifact only when visual/audio verification is required.
6. Apply the same redaction, deletion and access controls to source assets and derived records.

### 15.2 Alpha-specific examples

- UI screenshots that prove whether a memory view or tool execution actually worked.
- Terminal screenshots paired with structured logs and exit codes.
- Audio transcripts linked to time ranges and speaker labels when appropriate.
- Video frame summaries for browser/screen automation episodes.
- PDF/document chunks with exact page and section provenance.
- Generated code/report/test artifacts linked to the goal and commit that produced them.

### 15.3 Reliability rule

A caption is an interpretation of an artifact, not a replacement for the artifact. When the task depends on exact UI state, code, numbers, or text, retrieve and inspect the source image/document/log instead of trusting a generic summary. OCR or transcription output should carry extraction confidence and an explicit “recheck source” route.

---

## 16. Memory decay, archiving, forgetting and deletion

Forgetting is a governed lifecycle operation—not merely reducing a score.

### 16.1 Priority decay

Priority may decline with age for transient facts, but use type-specific rules:

- short-lived environment state: decay quickly and revalidate before actions;
- project configuration: preserve until a newer verified state supersedes it;
- explicit user preferences: persist until corrected or deleted, subject to user control;
- procedures: revalidate after relevant version/environment changes, rather than solely by age;
- failures: remain available while applicable; lower priority after the cause/version becomes obsolete;
- legal/safety/audit records: follow explicit retention policy, not popularity-based decay;
- raw conversation/attachments: follow user-selected thread retention and deletion policies.

### 16.2 Archive lifecycle

Use states such as `active`, `low_priority`, `archived`, `quarantined`, `tombstoned`, and `purged`. Archive should reduce index/prompt cost while preserving searchable metadata and an authorized path back to the source. Never silently archive a critical unfinished goal or only copy of a verified procedure.

### 16.3 Forget/delete workflow

Deletion should produce a tracked job that covers:

1. canonical memory record(s);
2. vectors and keyword index entries;
3. graph nodes/edges and entity summaries derived from the record;
4. cached context packets and in-process caches;
5. summaries, reflections and procedures materially derived from the content, according to policy;
6. artifacts/transcripts/source files when within Alpha's control;
7. export/sync targets, if configured;
8. backups according to the documented backup retention window;
9. evaluation fixtures derived from user data;
10. the UI/search surfaces and audit records, retaining only a minimal non-sensitive tombstone if required to prevent resurrection.

Do not promise instant physical erasure from immutable backups if the retention system does not support it. Expose the real status and completion scope honestly. A record should not reappear after re-embedding, restart, backup restore, nightly consolidation or re-import.

### 16.4 Forget commands and ambiguous requests

Explicit “forget this memory” requests should be handled by a dedicated policy path, not interpreted as a low-priority conversational utterance. For ambiguous requests like “don't bring this up again,” distinguish a conversational preference from deleting stored data when the intended scope matters. When safe, show the action/target and a reversible preview; for sensitive or destructive operations, seek the configured approval.

---

## 17. Security, privacy and prompt-injection resistance

Memory makes an agent more useful and also gives attackers a longer-lived place to hide instructions. Security must be enforced below the model.

### 17.1 Required controls

- **Identity and tenancy:** resolve `owner_id`, scope and permissions server-side for every read/write/delete.
- **ACL-filter-first retrieval:** unauthorized records must not reach rerankers or LLM context, even temporarily.
- **Secret detection/redaction:** scan user/tool/web/repository content before indexing; never store secrets as normal memory.
- **Untrusted-content labelling:** external docs, webpages, uploaded files, issue comments, code comments and other-agent text remain untrusted data.
- **No instruction elevation:** memory can provide evidence but cannot override system/developer policy or add permissions to tools.
- **Tool-call gating:** a memory recall may recommend a procedure; the runtime still enforces tool schemas, sandbox boundaries, budgets, approval gates and risk checks.
- **Prompt-injection tests:** include memory records that tell the agent to reveal credentials, ignore previous rules, modify ACLs, or delete evidence. The agent must treat these as content and refuse unauthorized instructions.
- **Encryption:** protect sensitive records at rest and in transit where applicable; keep keys outside the memory store.
- **Access audit:** record who/which agent accessed or mutated a record and why, without logging the secret itself.
- **Retention minimization:** retain only what improves the task or is required by explicit policy.
- **Human control:** inspection, correction, export, forget, pause, and disable controls.
- **Backup/recovery tests:** restore must not bypass access policies or resurrect deleted records.

### 17.2 Model/provider privacy

For every provider, document what content is sent externally, whether embeddings/rerankers require remote calls, and which content classes are allowed. The local-first path must work with external LLM/embedding calls disabled. Do not send personal or sensitive memories to a remote provider merely because it is the default embedding endpoint.

### 17.3 Memory poisoning

- Track provenance and trust class for each candidate.
- Do not update trusted project facts solely from a low-authority source.
- Detect repeated near-identical messages meant to inflate salience/access counts.
- Quarantine suspicious instructions or mass updates and expose them in the audit view.
- Keep memory write permissions narrower than memory read permissions where appropriate.
- Require additional evidence for records that will influence tool execution, code changes or irreversible actions.

---

## 18. Observability and Memory Explorer UI

Alpha's stated emphasis on tracing and debugging should be carried into memory as a first-class feature. Retrieval failures are often silent, so a spinner and a “memory enabled” badge are not enough.

### 18.1 Memory health panel

Display live, truthful status for:

- canonical store connectivity/read/write health;
- last successful commit and last failed write;
- full-text index health and backlog;
- embedding provider/model/version and last embedding success;
- vector index health and rebuild progress;
- graph backend health, relationship count, and last update;
- episode capture and procedure-learning queue health;
- consolidation/dreaming last run, duration, budget and errors;
- pending deletion/tombstone jobs;
- cache age and stale-index alerts;
- storage size, growth rate, oldest record, record counts by type/scope/status;
- retrieval latency p50/p95, fallback frequency, token use and top failure reasons.

Unknown/unreadable statistics should be shown as **unknown/unavailable**, not `0`.

### 18.2 Memory record inspector

Each record should show:

- content and type;
- who/what created it and why it was stored;
- source URL/path/message/trace/artifact and relevant line/page/time span;
- record time vs. valid time;
- explicit/inferred/verified status and calibrated confidence, if available;
- scope/ACL/sensitivity/retention;
- relations, duplicates, contradictions and supersession history;
- last access and last confirmation;
- index status;
- edit/correct, approve/reject, pin, archive, export and forget actions according to permission.

### 18.3 Retrieval trace (“Why did Alpha remember this?”)

For a selected agent turn show:

1. normalized query and intent classification;
2. scopes and temporal filters;
3. channels invoked and channel health;
4. candidate IDs and pre/post-filter counts;
5. ranking/fusion/reranker result with human-readable reasons;
6. rejected candidates and why;
7. the exact records injected into the context packet;
8. token contribution, source evidence and confidence state;
9. whether the agent actually used or ignored the retrieved memory in its reasoning/action, to the extent measurable;
10. outcome label and any subsequent user correction.

Avoid displaying private records to a user or subagent that lacks access. Redact secrets from logs, traces and screenshots as well as from the database.

### 18.4 Memory dashboard sections

Recommended UI tabs:

- **Overview:** health, counts, retrieval quality, recent issues.
- **Search:** universal search, filters, result source and scope.
- **Timeline:** facts/episodes/decisions over time, including supersession.
- **Graph:** entity and relationship explorer with evidence side panel.
- **Episodes:** task traces, replay-in-sandbox, success/failure evidence.
- **Procedures / ReasoningBank:** candidate/trusted/deprecated procedures, attempts and test status.
- **Goals & intentions:** open prospective actions and memory state for active goals.
- **Learning / Dreaming:** job queue, changes proposed, accepted/rejected, outcomes.
- **Privacy & retention:** per-type retention, export, deletion jobs, provider/data-sharing controls.
- **Diagnostics:** failed reads/writes, stale indexes, tracing and benchmark regressions.

---

## 19. API and internal contract plan

Alpha's `docs/MEMORY.md` documents baseline endpoints such as memory store/search/stats/delete/patch, episodes/replay, cognitive memory and knowledge-graph queries. Preserve those paths where compatible and add versioned endpoints or response fields instead of gratuitously breaking the API.

Potential API surface (illustrative—reconcile with current routers before implementation):

| Capability | Endpoint idea | Required semantics |
|---|---|---|
| Capture candidate/event | `POST /api/memory/capture` | Idempotency key, server-resolved owner, scope and policy check, receipt with record/index status |
| Search | `POST /api/memory/search` | Query intent, filters/time mode, bounded result size, source health, provenance and recall explanation ID |
| Read one record | `GET /api/memory/{id}` | ACL check, source/evidence details, version and validity state |
| Correct/update | `PATCH /api/memory/{id}` | Expected version, evidence/actor, append-only history, conflict response |
| Supersede fact | `POST /api/memory/{id}/supersede` | Link replacement and validity interval, preserve history |
| Forget | `POST /api/memory/forget` | Dry-run preview, selected scope, policy, deletion-job ID and status |
| Explain retrieval | `GET /api/memory/recalls/{query_id}` | Candidate trace, channels, filters, final context packet references and health |
| Stats | `GET /api/memory/stats` | Per-store health, counts with unknown distinct from zero, growth and backlog |
| Health | `GET /api/memory/health` | Dependency status and degradation reasons |
| Consolidation jobs | `POST /api/memory/consolidations` and `GET /api/memory/jobs/{id}` | Bounded, resumable, idempotent, cancellable, progress-visible jobs |
| Export | `POST /api/memory/export` | Authorized export manifest with format, scope, record count, hashes and warnings |
| Episode operations | Keep compatible episodes/list/get/replay routes | Replay is sandboxed by default and must be auditable |
| Procedure operations | Add/list/test/promote/deprecate procedures | Promotion requires evidence and evaluation policy |
| Benchmark | `POST /api/memory/evaluations` / run registry | Reproducible suite/version and privacy-safe datasets |

### API response requirements

- Include `request_id` / trace ID for correlating frontend and backend logs.
- Return structured error categories: `store_unavailable`, `index_degraded`, `scope_denied`, `invalid_schema`, `conflict`, `stale_version`, `provider_unavailable`, `deletion_pending`, etc.
- A success response should distinguish canonical commit success from index readiness.
- Never return `200 OK` with a fake empty result when an underlying store failed.
- Paginate and bound result size; protect against unrestricted graph traversal or arbitrary database queries.
- Use typed request/response schemas and input validation; do not expose arbitrary graph query execution to untrusted agents.

---

## 20. Configuration plan

Make settings explicit, validated, and visible in Alpha's settings UI. The following YAML is a **target configuration sketch**, not a claim that all keys currently exist. Merge it into the current `memory-full.example.yaml` or configuration schema after auditing existing naming; do not introduce duplicate configuration trees.

```yaml
memory:
  enabled: true
  mode: local_first  # local_first | hybrid | managed

  storage:
    provider: sqlite
    path: "${ALPHA_USER_DATA}/memory/alpha-memory.db"
    wal: true
    auto_migrate: true
    backups_enabled: true

  scopes:
    strict_owner_resolution: true
    default_agent_scope: private
    allow_cross_agent_sharing: false
    require_explicit_project_sharing: true

  write_policy:
    store_explicit_memory_requests: true
    capture_meaningful_episodes: true
    store_unverified_inferences: as_hypothesis
    deduplicate_before_insert: true
    require_evidence_for_verified_status: true
    redact_secrets: true
    max_candidates_per_event: 20
    model_decision_timeout_seconds: 8

  retrieval:
    enabled: true
    hybrid: true
    channels: [exact, lexical, semantic, structured, temporal, episodic, procedural]
    lexical_top_k: 30
    vector_top_k: 30
    graph_hop_limit: 2
    final_top_k: 12
    reranker: auto
    context_token_budget: 3000
    max_latency_ms: 1500
    fallback_to_lexical: true
    fail_on_scope_error: true

  embeddings:
    provider: local_optional
    model: null
    remote_allowed: false
    batch_size: 32
    retry_policy: bounded_exponential

  temporal:
    enabled: true
    track_recorded_and_valid_time: true
    preserve_superseded_facts: true
    require_explicit_current_vs_historical_mode: true

  episodes:
    enabled: true
    capture_successes: true
    capture_meaningful_failures: true
    store_redacted_tool_traces: true
    artifact_max_inline_bytes: 16384
    replay_mode: sandbox_only

  procedures:
    enabled: true
    promotion_requires_verification: true
    auto_promote: false
    require_rollback_for_risky_actions: true
    revalidate_on_environment_drift: true

  consolidation:
    enabled: true
    schedule: idle_or_manual
    max_job_seconds: 60
    max_records_per_batch: 100
    max_cpu_percent: 35
    cancel_on_user_activity: true
    model_calls_allowed: false
    apply_high_impact_mutations: false

  retention:
    default_policy: user_configured
    preserve_project_decisions_until_superseded: true
    soft_delete_first: true
    deletion_cascades_to_derived_indexes: true

  observability:
    trace_all_writes: true
    trace_retrieval_decisions: true
    trace_context_injection: true
    redact_logs: true
    record_metrics: true
```

### Configuration rules

- Validate unknown keys and invalid combinations at startup with actionable messages.
- Show effective configuration, source of each value and override precedence (default → config file → environment → UI, as appropriate).
- Secrets belong in the credential vault or OS-protected secret store, not YAML or memory records.
- Feature-flag new paths and provide a safe rollback switch.
- If a setting claims to enable a provider that is missing or broken, show a degraded/error state; do not quietly switch to another paid/remote provider.
- Keep the base single-user install workable with SQLite and no mandatory graph/vector service.

---

## 21. Performance and resilience for Alpha's Windows-first target

The design should scale in memory quality before infrastructure complexity.

### 21.1 Latency targets to test

Initial engineering goals (not existing measured performance guarantees):

- exact-key and indexed metadata lookup: p95 under 50 ms on a normal local store;
- lexical retrieval: p95 under 150 ms on a representative single-user corpus;
- ordinary hybrid retrieval without remote reranking: p95 under 500 ms on the target machine;
- memory context assembly: p95 under 100 ms after candidates are fetched;
- remote/local model reranking may exceed these goals; measure it separately and make it optional;
- a user should see immediate status and be able to continue when a background index/consolidation job is running.

Profile on the real supported machine and corpus before enforcing thresholds in CI. Keep cold-start and packaged Windows measurements separate from development-server measurements.

### 21.2 Resilience requirements

- All background jobs persist status/checkpoint and can resume after process termination.
- Use bounded retries, exponential backoff with jitter and a dead-letter/quarantine queue after the retry budget is exhausted.
- Make writes and indexing idempotent; duplicate job delivery must not create duplicate records.
- Use database transactions, optimistic concurrency and locking consistently.
- Keep a durable canonical write even if a derived vector/graph index is down; mark the index pending and repair it later.
- Do not claim a memory is searchable until required indexes are ready, unless the UI explicitly indicates a degraded retrieval path.
- Health failures must include error codes, timestamps, trace IDs and the impacted channels.
- Detect disk-full, corrupted DB, invalid schema, embedding dimension mismatch, provider rate limits, stale graph edges and index drift.
- Provide backup, restore, migration dry-run and rollback workflows.
- Run data integrity checks after app upgrades; preserve memory across Electron/Windows upgrades and reinstall paths according to documented user-data policy.

### 21.3 Storage control

- Separate metadata from binary artifacts.
- Compress older raw episodes and avoid storing repetitive prompt wrappers.
- Reuse embeddings only when the exact normalized content and embedding model/version match.
- Batch embedding/index work and cap concurrency.
- Track database bytes, index bytes, artifact bytes, and growth per week.
- Provide safe vacuum/compaction and archive operations.
- Never delete the only original evidence simply to meet a storage target; require a policy allowing that data loss.

---

## 22. Evaluation system: prove memory helps Alpha

A memory system needs its own regression suite and real-task evaluations. Unit tests passing do not prove that Alpha remembers a past decision, retrieves the right error fix, or resumes an interrupted goal.

### 22.1 Evaluation layers

1. **Memory write quality:** did Alpha keep the right items and reject noise/secrets/duplicates?
2. **Memory storage correctness:** are records durable, scoped, versioned, and consistently represented across derived indexes?
3. **Retrieval quality:** did it return relevant, current, authorized, evidence-rich candidates?
4. **Answer/decision grounding:** did the agent use evidence accurately and abstain when evidence was insufficient?
5. **Task outcome:** did memory increase completion rate, reduce repeated mistakes, or reduce time/cost without adding regressions?
6. **Temporal and change handling:** can it distinguish current facts from historical facts and reconcile updates?
7. **Privacy/security:** can one owner/agent/project see another's private memory? Does deletion truly propagate?
8. **Efficiency and reliability:** retrieval latency, index queue, storage growth, recovery and provider fallback.

### 22.2 Benchmark set

Use both published and Alpha-specific tests:

- **LongMemEval:** extraction, multi-session reasoning, temporal reasoning, updates and abstention.
- **LoCoMo:** long-conversation factual/temporal questions and event summarization; consider multimodal tasks where supported.
- **MemoryAgentBench:** add selective-forgetting and memory-management scenarios after checking current benchmark instructions/license and adapting only the allowed data.
- **Alpha Memory Workload:** real coding/research/repo-maintenance tasks with exact ground truth, test outcomes and expected evidence.
- **Security corpus:** cross-owner leakage, prompt injection in retrieved files, secret persistence, malicious shared-memory content and deletion resurrection.

Do not compare leaderboard numbers unless the underlying models, prompts, data splits, retrieval budget and evaluator protocol are aligned. Report local model vs remote model results separately.

### 22.3 Alpha-specific golden test scenarios

At minimum create golden cases covering:

1. **Explicit save:** user says to remember a stable project constraint; verify it survives a full restart and a new thread.
2. **Correction:** user corrects a fact; ensure the new version becomes current and the old one stays historical, not silently active.
3. **Temporal query:** “What was true before the change?” returns the previous version with a time boundary.
4. **Fact extraction:** a long conversation contains multiple facts; atomic facts remain individually recallable.
5. **Long context:** task resumes after many turns and context compaction without losing acceptance criteria.
6. **Goal resume:** terminate Alpha during a multi-step goal, restart it and continue from the last verified checkpoint.
7. **Code task episode:** a real test fails, Alpha records the error, fix, and verified result; future similar tasks retrieve the episode.
8. **Failed-fix suppression:** a known bad fix is not recommended as a trusted procedure; its scope/version is shown if retrieved as a warning.
9. **Procedure promotion:** one claimed success does not automatically turn a generated procedure into trusted memory.
10. **Exact identifier retrieval:** find a literal tool name, URL, error string, line number, commit hash or model ID despite semantically dissimilar language.
11. **Hybrid retrieval:** exact lexical hits and semantic paraphrases are both retrievable.
12. **Scope separation:** personal, project, goal, thread and agent-private memories do not leak across boundaries.
13. **Concurrent agents:** parallel updates do not lose changes or duplicate facts.
14. **Provider down:** embedding service unavailable; lexical search works, health is degraded, and Alpha doesn't pretend there are no memories.
15. **Index drift:** deliberately remove an embedding/edge and verify the integrity job detects/rebuilds it.
16. **Deletion:** deleted record is absent from API, search, graph, cache, summaries, exports and after restart/reindex.
17. **Prompt injection:** a retrieved document tells Alpha to reveal secrets or ignore policies; no instruction elevation occurs.
18. **Secret redaction:** a tool log includes a fake token; it must not appear in memory content, embedding input, trace or UI.
19. **User forget request:** confirm that all declared replicas and derived records are processed and status is honest.
20. **Dreaming/consolidation:** run in a bounded idle cycle, inspect proposed changes and verify it does not rewrite high-impact configuration without approval.
21. **UI reality test:** search, open record, inspect provenance, change a filter, pin/forget, watch live health and confirm the API reflects each action.
22. **Multimodal evidence:** screenshot-derived note links to the original artifact; exact UI claim can be rechecked against the source image.
23. **Environment drift:** procedure captured on one branch/dependency version is revalidated before use on a materially changed environment.
24. **No-memory baseline:** run task with retrieval disabled and compare task success, retries, cost and time against memory-on.

### 22.4 Metrics and proposed release gates

Targets below are engineering goals to measure and adjust, not claims about current Alpha performance.

| Metric | Definition | Initial release target |
|---|---|---|
| Explicit memory capture | Rate of valid, authorized explicit save requests correctly persisted | 100% of valid test cases |
| Secret leakage | Secrets appearing in memory/index/prompt/trace when policy forbids them | 0 in adversarial tests |
| Scope violation | Unauthorized record returned or injected into context | 0 in adversarial tests |
| Deletion correctness | Deleted records remain absent after cache clear, restart, reindex and restore workflow within policy | 100% of controlled test cases |
| Retrieval Recall@k | Ground-truth relevant records in top-k for test queries | Establish baseline, then improve without violating precision/latency constraints |
| Retrieval precision | Returned candidates that are genuinely useful and correctly scoped | Target at least 0.80 on curated initial set; raise after stable evaluation |
| Temporal accuracy | Correct selection of current vs historical facts | Target at least 0.90 on a curated date/version suite |
| Unsupported-answer rate | Answers stated as remembered facts without adequate source evidence | Target near zero for memory-grounded factual questions; measure separately from model hallucination baseline |
| Explicit abstention | System abstains/asks when memory is insufficient or contradictory | At least 0.90 on curated abstention cases before trusting high-impact memory |
| Procedure promotion integrity | Procedures promoted without required proof | 0 |
| Recovery | Critical memory data remains available after force-kill/restart test | 100% of defined crash-injection scenarios |
| Retrieval latency | p50/p95 by channel and full retrieval pipeline | Meet target profile for declared hardware/corpus; no single average hiding slow tails |
| Useful-memory delta | Task success/time/retries with memory vs no-memory | Demonstrable improvement on multiple task classes; no major regression in another class |

A single aggregate score can hide serious failures. Publish per-category scores, failure examples, model/provider settings, test commit and confidence intervals where useful.

---

## 23. Phased implementation roadmap

**Execution rule:** the coding agent must inspect and reuse current implementations, make one bounded change at a time, add tests, execute real runtime workflows, inspect screenshots where the UI is changed, and preserve existing features. Do not implement every advanced idea in one giant refactor.

### Phase 0 — Runtime audit and baseline (P0)

**Goal:** learn what is actually live and measure current memory performance.

Tasks:

- Read `AGENTS.md`, `docs/MEMORY.md`, current config examples, schema/models, memory APIs, background jobs, frontend memory screens, and existing tests.
- Trace every memory feature through code, config, API, storage, indexes and UI.
- Verify the public README claims for active fusion, cognitive memory, graph, episodic replay, dreaming and ReasoningBank from a real running instance.
- Identify which stores are canonical and which are derived; test actual owner/project/agent filters.
- Add trace spans for capture → commit → indexing → search → context injection → outcome.
- Build a small local baseline corpus and run exact, semantic, temporal, cross-session, deletion and restart tests.
- Produce `docs/MEMORY_RUNTIME_AUDIT.md` with evidence, blockers, duplicates, risk and recommended next steps.

**Exit criteria:** no core feature is assumed complete without call-path and runtime evidence; every known failure is represented in the audit; baseline metrics are reproducible.

### Phase 1 — Unified contract, provenance and observability (P0)

**Goal:** make all current stores report memory records and failures consistently.

Tasks:

- Define base record, evidence, scope, temporal and version contracts compatible with existing schemas.
- Add a shared memory service/SDK boundary and route existing callers through it incrementally.
- Add structured trace IDs, index states, write receipts and health APIs.
- Establish the canonical ledger and identify rebuildable derived indexes. If there is already a valid canonical source, extend it rather than duplicating it.
- Add idempotency keys, schema versioning and migration backups.
- Add memory health panel and a retrieval trace view (minimum viable version).

**Exit criteria:** every read/write can be traced; provider/index failures are visible; migration can be rolled back; existing APIs still work.

### Phase 2 — Correct temporal semantics, scope and memory governance (P0)

**Goal:** prevent stale facts, scope leaks and uncontrolled writes.

Tasks:

- Add or reconcile bitemporal fields and supersession/version rules.
- Make owner/project/goal/thread/agent scope filtering server-enforced and tested.
- Add candidate write actions (`STORE`, `UPDATE`, `LINK`, `NOOP`, `ASK_USER`, `QUARANTINE`, `ARCHIVE`, `REJECT`).
- Implement explicit-save, correction, contradiction and forget workflows.
- Add secret detection/redaction before persistence and embedding/indexing. **Partial:** new L1 writes redact recognized patterns in content/metadata before ID creation; the durable L1 writer rejects direct unsanitized writes. Detection is heuristic, legacy rows are not scrubbed, and other stores/embedding paths still need auditing.
- Make deletion cascade through all registered replicas/derived materializations. **Partial:** memory-fabric candidate enumeration and forget/delete routes now fail visibly on future-schema or unreadable scopes and preserve the source file; replicas, summaries, caches, and other memory packages are not yet covered.

**Exit criteria:** temporal tests and scope isolation pass; a deletion cannot be resurrected by rebuild/restart; critical explicit instructions cannot be silently overwritten.

### Phase 3 — Hybrid retrieval and context engineering (P0)

**Goal:** get the right records into the right turn under a strict budget.

Tasks:

- Implement exact-key + lexical + semantic + structured retrieval behind adapters.
- Add RRF or another tested rank fusion method and optional reranking.
- Add intent/query planning, scope/time filters, deduplication, evidence completeness and uncertainty labels.
- Return explanations of included and excluded candidates.
- Add configurable token budget and retrieval-channel fallback.
- Test memory context injection within chat, goal execution, coding, research and subagent runs.

**Exit criteria:** golden retrieval suite passes; degraded provider behavior is explicit; context packets preserve evidence and do not balloon token cost.

### Phase 4 — Goal, prospective and active memory (P1)

**Goal:** preserve long-running task continuity and retrieve memories at decision points.

Tasks:

- Integrate with the Goal Engine and run checkpoint state.
- Store acceptance criteria, unresolved questions, next actions and verified artifacts structurally.
- Add event-triggered retrieval at goal resume, subagent spawn, tool failure, project switch, context compaction and risky actions.
- Add explicit active-memory fusion tests and a visible effective configuration state.
- Ensure proactive memory searches are budgeted, deduplicated and cancellable.

**Exit criteria:** force-killed goal resumes correctly; active retrieval is proven to run at documented trigger points; no private scope leakage.

### Phase 5 — Episodic quality and safe replay (P1)

**Goal:** turn real work into searchable, evidence-linked experience.

Tasks:

- Standardize episode capture for verified success, meaningful failure, user correction and milestone.
- Link episodes to code commits, tool outputs, test reports, screenshots and artifacts.
- Build task-signature retrieval and a sandbox-only replay path.
- Add failure-signature indexing and duplicate episode compression.
- Test after restart and across different but related projects without sharing restricted data.

**Exit criteria:** real coding/research episodes are retrievable; replay cannot trigger external side effects by default; success status requires evidence.

### Phase 6 — ReasoningBank/procedure learning hardening (P1)

**Goal:** only promote repeatable, verified strategies.

Tasks:

- Audit and connect existing ReasoningBank and `ralph_loop` consumers to the unified memory contract.
- Define procedure schema, preconditions, environment/version metadata and validation artifacts.
- Record attempts/successes/failures and compare to a no-memory baseline.
- Add candidate → sandbox-verified → trusted/deprecated lifecycle.
- Generate targeted regression tests and invalidation rules.

**Exit criteria:** no strategy is trusted without the required proof; retrieval avoids known-inapplicable procedures; performance evidence is visible.

### Phase 7 — Temporal graph and cognitive-memory reconciliation (P1/P2)

**Goal:** useful multi-hop and temporal reasoning without duplicated graph truth.

Tasks:

- Connect cognitive concepts, knowledge-graph entities, evidence records and canonical facts with stable IDs.
- Add typed relations, source provenance, confidence status and validity windows.
- Implement cautious entity resolution and reversible merge suggestions.
- Add graph retrieval to hybrid fusion for relevant multi-hop queries only.
- Benchmark graph utility against hybrid text/fact retrieval; keep backend optional if performance/complexity doesn't justify it.

**Exit criteria:** graph results can be traced to source facts; current/historical queries work; graph outages degrade visibly without blocking exact/lexical lookup.

### Phase 8 — Bounded dreaming and consolidation (P2)

**Goal:** compress experience into high-value memories and expose what changed.

Tasks:

- Implement a resumable idle/manual consolidation worker with resource limits.
- Propose summaries, duplicate links, fact updates, relationship links and procedure candidates.
- Add dry-run preview, change ledger, rollback and model-call budget.
- Protect critical decisions and never silently mutate prompts, permissions, external integrations or production state.
- Measure whether consolidated memory improves retrieval rather than assuming fewer tokens means better memory.

**Exit criteria:** repeated jobs are idempotent, can resume after termination, respect budgets and produce measurable retrieval benefit.

### Phase 9 — Multimodal/artifact memory (P2)

**Goal:** connect visual and document evidence to tasks and memory.

Tasks:

- Standardize artifact records and secure storage references.
- Add image/screenshot descriptions, document/page chunks, audio transcripts and video frame timestamps only for enabled modalities.
- Link outputs to exact source assets and regions/time spans.
- Add multimodal retrieval/evaluation and deletion propagation.

**Exit criteria:** source artifacts can be re-opened and checked; derived captions are labelled as interpretations; permission and deletion tests include all modalities.

### Phase 10 — Learned memory policies and advanced research (P3)

**Goal:** optimize memory management from measured outcomes.

Possible experiments:

- Contextual bandit or supervised classifier for selecting retrieve/write operations from Alpha traces.
- A Memory-R1-inspired manager policy with `ADD`, `UPDATE`, `DELETE`, `NOOP`, `RETRIEVE`, `SUMMARIZE`; start with offline simulation and logged policy decisions.
- Learned salience/freshness/retention policy constrained by hard safety rules.
- Cross-trajectory abstraction that finds reusable procedures across distinct episodes.
- MemoryAgentBench/LongMemEval-style agentic tests including forgetting, task completion and adversarial updates.

**Do not start here.** Learned policies are high-risk before the data, trace, privacy and evaluator systems are reliable. Keep deterministic guardrails and a fallback to the existing policy. Never allow a learned policy to delete safety-critical records or bypass user intent.

---

## 24. Proposed repository layout (adapt, do not duplicate)

The repository already has a memory implementation and documentation. Before adding files, search for equivalent modules and extend them where possible. The following is an organizational target, not a mandatory directory renaming plan:

```text
backend/...
  memory/
    contracts.py          # Base schemas/interfaces
    service.py            # Unified memory facade
    policy.py             # Scope, privacy and retention rules
    write_pipeline.py     # Capture/extract/dedupe/version lifecycle
    retrieval/
      planner.py           # Intent/scope/channel planning
      lexical.py           # Full-text adapter
      semantic.py          # Embedding adapter
      structured.py        # Exact/fact queries
      temporal.py          # Bitemporal queries
      graph.py             # Graph adapter
      episodic.py          # Experience retrieval
      procedural.py        # Verified procedure retrieval
      fusion.py            # Candidate fusion/ranking
      context.py           # Token-bounded context packets
    stores/
      canonical.py         # Canonical transactional records
      vectors.py           # Vector index adapter
      graph_store.py       # Graph adapter
      artifacts.py         # Artifact metadata/reference adapter
    jobs/
      indexing.py
      deletion.py
      consolidation.py
      integrity.py
    schemas/
      memory.py
      evidence.py
      episode.py
      procedure.py
      recall.py
    telemetry.py
    migrations/

backend/tests/...
  memory/
    test_contracts.py
    test_write_policy.py
    test_temporal_memory.py
    test_scope_isolation.py
    test_hybrid_retrieval.py
    test_delete_cascade.py
    test_restart_recovery.py
    test_index_rebuild.py
    test_episode_replay.py
    test_procedure_promotion.py
    test_memory_injection_security.py
    test_end_to_end_real_tasks.py

frontend/...
  memory/
    MemoryOverview...
    MemoryRecordInspector...
    RecallTrace...
    MemoryTimeline...
    MemoryHealth...
    MemoryPrivacy...

docs/
  MEMORY.md                    # Updated implementation docs
  MEMORY_RUNTIME_AUDIT.md      # Evidence-backed audit
  MEMORY_EVALUATION.md         # Benchmark definitions/results
  MEMORY_SECURITY.md           # Threat model and controls
  MEMORY_MIGRATION.md          # Data upgrade/rollback guide
```

Avoid creating a parallel memory system or a parallel UI if equivalents already exist. The implementation path must follow Alpha's real backend/frontend layout and naming conventions.

---

## 25. Technical implementation checklist

### Data and storage

- [ ] There is one documented canonical ledger/source of truth.
- [ ] Every derived index has a rebuild job and health status.
- [ ] Record schemas have versions and migrations.
- [ ] Writes use transactions/idempotency keys and concurrency control.
- [ ] Original evidence/artifacts are referenced, not copied into every record.
- [ ] Current and historical facts are distinguishable.
- [ ] Duplicate and contradiction operations preserve lineage.
- [ ] Memory indexes are owner- and scope-aware.

### Capture and consolidation

- [ ] Explicit memory requests are captured reliably.
- [ ] Inferred facts are labelled as inferred.
- [ ] High-noise chat does not pollute durable semantic memory.
- [ ] Source trust/sensitivity is evaluated before indexing.
- [ ] Tool logs and screenshots link to episodes with redaction.
- [ ] Consolidation is cancellable, budgeted, resumable and traceable.
- [ ] Procedure promotion requires verified outcome evidence.

### Retrieval and use

- [ ] Exact, lexical, semantic, structured, time, graph and task memory channels are available through adapters as configured.
- [ ] Query planner chooses channels using measured relevance/cost.
- [ ] ACL and scope filters run before model reranking/context injection.
- [ ] Retrieval returns source pointers, temporal validity and epistemic labels.
- [ ] Contradictory/stale/untrusted records are marked.
- [ ] Context packet is token-bounded and includes record IDs.
- [ ] Failure/fallback is explicit and visible.
- [ ] Real agent tools/goals/subagents actually consume the context packet.

### Security and governance

- [ ] No secrets are stored, embedded, or exposed in traces by default.
- [ ] Retrieved content cannot alter permissions or override system policy.
- [ ] User/agent/project isolation tests pass.
- [ ] Delete/export/correction workflows cover all derived data.
- [ ] Backup restore and index rebuild do not resurrect deleted records.
- [ ] External data-sharing behavior is visible and configurable.

### Runtime and interface

- [ ] App restart/force-kill scenarios preserve critical memory.
- [ ] The packaged Windows build behaves like the development build.
- [ ] The memory UI uses live API data and truthful health states.
- [ ] Frontend shows trace IDs/error reasons instead of generic success/failure.
- [ ] Background jobs stop or yield when required and do not destabilize a low-RAM machine.

### Evaluation

- [ ] Current implementation baseline is captured before changes.
- [ ] LongMemEval and LoCoMo integration/adaptation is documented.
- [ ] Alpha-specific golden tests cover real coding and research tasks.
- [ ] Security, temporal, forgetting and no-memory ablation tests run in CI/nightly as appropriate.
- [ ] Every release reports quality, speed, storage and governance metrics separately.
- [ ] Any claimed improvement is reproducible from a committed evaluation manifest.

---

## 26. Definition of done

Alpha's advanced memory system is not “done” because every type has a database table or the dashboard says “memory enabled.” It is ready for production only when all of the following hold:

1. **Runtime wiring:** the actual chat, goal, coding, research, tool and subagent execution paths capture and retrieve memories through the unified interface.
2. **Continuity:** long-running goals resume correctly after restart and preserve structured acceptance criteria, checkpoints and evidence.
3. **Recall quality:** exact strings, semantic paraphrases, facts, episodes and procedures can be found using the right retrieval channels, with an honest “unknown” response when evidence is inadequate.
4. **Truthfulness:** each durable claim has provenance and a clear state; current and historical facts are not conflated.
5. **Safe learning:** only evidence-supported strategies become trusted and high-impact mutations remain gated.
6. **Multi-agent boundaries:** owner/private/team/project memory isolation is demonstrated with adversarial integration tests.
7. **Recoverability:** the system survives process termination, provider outage, index drift and restart without silent data loss.
8. **Deletion:** forget requests propagate through records, indexes, caches, summaries and artifacts according to a documented retention policy.
9. **Security:** secret leakage, prompt-injection and unauthorized tool-action memory tests pass.
10. **Transparency:** Alpha can explain what it remembered, why it retrieved it, what evidence supports it and where a failure occurred.
11. **Measured benefit:** an agreed suite demonstrates an improvement over the no-memory baseline without unacceptable latency, cost, privacy or task-success regressions.
12. **Maintainability:** the core works locally at zero mandatory service cost, optional components are adapters, and migrations and backups are documented.

---

## 27. Priority order: what to implement first

If the coding agent needs to choose the next work items, use this order:

1. **Audit and end-to-end tracing.** Verify existing features and find broken/unwired paths before changing architecture.
2. **Canonical contract + provenance + true scope isolation.** Without these, other layers cannot be trusted.
3. **Temporal updates, contradiction handling and deletion correctness.** These prevent long-term memory from becoming a stale-fact generator.
4. **Hybrid retrieval + context injection + explanations.** This is where most practical memory value is won or lost.
5. **Goal continuity + active retrieval triggers.** Connect memory to Alpha's core autonomous execution, not only chat responses.
6. **Episodic quality + safe procedure promotion.** Turn real verified work into reusable competence.
7. **Consolidation/dreaming + temporal graph expansion.** Add only when trace/evaluation shows the existing structure needs it.
8. **Multimodal and learned policies.** Add in bounded, testable slices once the core is reliable.

**Do not prioritize:** switching databases only because a project uses another database; always running multiple LLM subagents to save a small memory; fine-tuning before measuring retrieval; unbounded autonomous reflection; injecting a large `MEMORY.md` into every prompt; or calling the system “AGI/ASI-level” without evidence.

---

## 28. Copy-paste implementation assignment for Alpha's coding agent

Use the following task prompt to implement this plan inside the actual repository. It intentionally starts with audit and integration rather than immediately generating many files.

```text
You are the senior memory-systems engineer responsible for the Alpha AI agent repository: https://github.com/itsPremkumar/alpha

MISSION
Implement the Advanced Memory System described in ALPHA_ADVANCED_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md as a production-grade, local-first, evidence-grounded Memory OS. Alpha already documents working/thread memory, persistent semantic/vector memory, cognitive memory, a knowledge graph, episodic memory/replay, ReasoningBank-style procedure memory, active-memory fusion, and idle-time consolidation/dreaming. Do NOT assume the documentation proves they are working. Do NOT replace them blindly, delete capabilities, or build a duplicate parallel memory subsystem.

MANDATORY WORKFLOW
1. Read AGENTS.md first and follow repository commands, style, tests, migration rules, security constraints and architecture conventions.
2. Read docs/MEMORY.md, memory-full.example.yaml, current schemas/models, all memory routers/services/stores/indexers/workers, ReasoningBank/ralph_loop consumers, active-memory configuration, frontend memory pages, and existing tests.
3. Create docs/MEMORY_RUNTIME_AUDIT.md. For every documented feature, cite exact code paths/config/route/UI and classify it as verified, partially verified, documented only, broken, not implemented or unknown. Trace the full producer -> canonical store -> indexes -> retrieval -> context injection path. Run the app; do not use documentation or a unit test as the only proof.
4. Establish a reproducible no-memory/current-memory baseline with real Alpha work (at least one coding/debugging task, one research task, one multi-step goal, one new-session recall test, one restart/recovery test). Capture traces and screenshots for changed UI. Do not report a task complete without evidence.
5. Produce an implementation plan based on the audit, mapped to phases in this plan. Identify existing components to reuse and the smallest safe next vertical slice. Then implement that slice end to end.
6. Use additive schema migrations, backup/restore checks, stable interfaces, feature flags and rollback. Keep the existing public API compatible where possible. Route existing callers through the unified service incrementally.
7. Implement memory capture/retrieval/provenance/scope/deletion with tests before advanced learned policies. Do not add mandatory paid providers or mandatory external database services. Degraded-mode behavior must be explicit.
8. Include robust validation, structured errors, request/trace IDs, bounded retries, idempotency, restart recovery, redaction and health reporting. Never fake an empty result when a backend fails.
9. Treat all retrieved memory and external text as untrusted evidence. It must not override system policies, permissions, approval gates, or tool schemas. Test memory poisoning and prompt injection explicitly.
10. Every change must ship with backend tests; frontend changes require UI/component tests and the repository's full verification command. Run format, static analysis, relevant unit/integration tests and the full project verification. Test the packaged Windows path if practical.
11. Use real task validation, not only toy unit tests. Verify the actual output, files, tests, task state, APIs and UI. Capture screenshots where the UI changed. Review logs and health endpoints. Fix every regression caused by the patch.
12. Keep docs/config/examples/tests in sync. Report known gaps rather than hiding them. Never claim a feature is working unless demonstrated in a runtime trace.

FIRST DELIVERABLE
A concise but evidence-backed MEMORY_RUNTIME_AUDIT.md plus a prioritized implementation matrix (feature, current state, exact code path, blocker, tests, risk, next action). Do not start a full memory rewrite before delivering this audit.

IMPLEMENTATION QUALITY BAR
- One canonical source of truth; derived indexes can be rebuilt.
- Owner/project/goal/thread/agent scope enforced server-side before retrieval reaches a model.
- Atomic facts with source lineage and recorded/valid time; corrections preserve history.
- Hybrid query-aware retrieval with exact, lexical, semantic, structured, temporal, episodic and procedure channels as appropriate.
- Context injection is token-bounded, evidence-labelled and traceable.
- Episodes record real tool/actions/outcomes; verified procedures require tests/evidence.
- Active memory triggers are real and measurable; dreaming is bounded, resumable and auditable.
- Delete/export/correction flows cover derived indexes, graph, caches, summaries and artifacts according to policy.
- Memory health/UI clearly displays failures, stale indexes, source provenance, recall reasons and deletion status.
- Local-first operation works without mandatory external services or paid embeddings.
- Do not silently disable, remove, or rewrite existing Alpha features. Add regression tests for all affected behavior.

At the end, provide: (a) changed files, (b) architecture changes, (c) exact commands run and results, (d) runtime task scenarios and evidence/screenshots, (e) benchmark changes against baseline, (f) unresolved issues, (g) migration/rollback instructions, and (h) honest feature status. A passing test suite alone is not proof that memory improves real task execution.
```

---

## 29. Research references and implementation resources

These sources informed the architectural recommendations. Research papers may be preprints and benchmark results are specific to their reported model, data and protocol; do not transfer reported percentage gains directly to Alpha.

### Alpha — current implementation baseline

- [Alpha repository](https://github.com/itsPremkumar/alpha)
- [Alpha memory documentation](https://github.com/itsPremkumar/alpha/blob/main/docs/MEMORY.md)
- [Alpha public README / feature catalog](https://github.com/itsPremkumar/alpha/blob/main/README.md)
- [Alpha self-awareness / capability inventory docs](https://github.com/itsPremkumar/alpha/blob/main/docs/SELF_AWARENESS.md)

### Core memory architecture and agent frameworks

- [LangGraph — Memory overview](https://github.com/langchain-ai/docs/blob/main/src/oss/concepts/memory.mdx)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [MemoryOS of AI Agent (EMNLP 2025)](https://aclanthology.org/2025.emnlp-main.1318/)
- [MemOS: An Operating System for Memory-Augmented Generation](https://arxiv.org/abs/2505.22101)
- [Mem0 — graph-memory documentation](https://github.com/mem0ai/mem0/blob/main/docs/platform/features/graph-memory.mdx)
- [Graphiti — temporal context graphs for agents](https://help.getzep.com/graphiti/getting-started/welcome)
- [Zep — agent-memory concepts and temporal knowledge](https://help.getzep.com/)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [A-MEM source code](https://github.com/agiresearch/A-mem)

### Reflection, skills and adaptive memory management

- [Generative Agents](https://doi.org/10.1145/3586183.3606763)
- [Voyager: An Open-Ended Embodied Agent](https://openreview.net/pdf?id=P8E4Br72j3)
- [ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory (ICLR 2026)](https://arxiv.org/abs/2509.25140) · [official code](https://github.com/google-research/reasoning-bank)
- [Memory-R1: Memory management via reinforcement learning](https://arxiv.org/abs/2508.19828)

### Surveys and evaluation

- [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (2026)](https://arxiv.org/abs/2603.07670)
- [From Storage to Experience: A Survey on the Evolution of LLM Agent Memory Mechanisms (2026)](https://arxiv.org/abs/2605.06716)
- [A Survey on the Memory Mechanism of LLM-based Agents](https://arxiv.org/abs/2404.13501)
- [LongMemEval — ICLR 2025 paper](https://proceedings.iclr.cc/paper_files/paper/2025/hash/d813d324dbf0598bbdc9c8e79740ed01-Abstract-Conference.html)
- [LongMemEval repository, including 2026 LongMemEval-V2 announcement](https://github.com/xiaowu0162/LongMemEval)
- [LoCoMo — ACL 2024 paper](https://aclanthology.org/2024.acl-long.747/)
- [LoCoMo dataset and code](https://github.com/snap-research/locomo)

---

## Final recommendation

The most valuable next step is **not another memory type or another vector database**. It is a verified end-to-end memory loop:

**real event → evidence-backed candidate → governed durable record → searchable indexes → correct scoped retrieval → compact context injection → actual task decision → measured outcome → safe consolidation.**

Make every link observable, test it with real Alpha work, and evolve the system from a reliable foundation. That is how Alpha can build a genuinely advanced memory system instead of a large archive that merely appears intelligent.
