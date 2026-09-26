# ALPHA — Advanced Completely Free & Open-Source Agentic Memory System

**Research/report date:** 2026-09-25  
**Target:** Alpha, a long-running, multi-agent, local-first AI agent harness  
**License goal for Alpha implementation:** permissive open-source dependencies whenever practical (MIT / Apache-2.0 / BSD / PostgreSQL-style permissive licenses), with a dependency/license inventory pinned to exact versions.

> **Important:** “Completely free and open source” here means the *software stack* can be self-hosted and run without paid memory APIs. LLM inference, embeddings, reranking, storage, and networking can all be local. If Alpha optionally connects to a paid model/API, that is an external cost, not a requirement of the memory architecture.
>
> License information below is based on the repositories/pages checked on 2026-09-25. Licenses can change in future versions; for a commercial product, pin exact versions/commits and perform a dependency/license audit before distribution. This document is engineering guidance, not legal advice.

---

## 1. Executive Summary

A modern agent memory system should **not** be a single vector database containing chat messages.

The strongest open-source/current approaches increasingly treat memory as a **multi-representation, lifecycle-managed system**:

- **Working/context memory** for the current turn or active task.
- **Session/short-term memory** for continuity inside a conversation or run.
- **Episodic memory** for what happened, when it happened, what actions were taken, and what the outcome was.
- **Semantic memory** for stable facts, concepts, entities, and relationships.
- **Procedural/skill memory** for reusable methods, workflows, tool-use patterns, and successful trajectories.
- **User/profile memory** for durable preferences and personalized facts.
- **Goal/prospective memory** for future intentions, commitments, deadlines, and pending tasks.
- **Temporal memory** for facts whose truth changes over time.
- **Entity/relationship memory** for people, projects, tools, organizations, resources, and how they relate.
- **Reflection/lesson memory** for distilled lessons from successes, failures, and reviews.
- **Project/codebase memory** for architecture, conventions, decisions, bugs, and repository-specific knowledge.
- **Environment/world-state memory** for the agent’s current machine, services, files, deployments, and other changing state.
- **Multimodal memory** for images, audio, video, and other non-text observations.
- **Provenance/evidence memory** so the agent can answer “where did this memory come from?”
- **Compressed/summarized memory** so very long histories can be preserved without repeatedly injecting raw transcripts.
- **Parametric/activation memory** for information represented in model weights or reusable KV/activation state when that is technically appropriate.

The recommended Alpha architecture is therefore a **Memory Operating System / Memory Fabric** rather than a single database.

### Alpha recommended architecture

```text
                         ┌───────────────────────────┐
                         │        ALPHA AGENT         │
                         │ planner / executor / tools │
                         └──────────────┬────────────┘
                                        │
                              memory read/write API
                                        │
                    ┌───────────────────▼───────────────────┐
                    │       ALPHA MEMORY ORCHESTRATOR       │
                    │ admission / retrieval / consolidation│
                    │ conflict / scoring / lifecycle       │
                    └───────┬──────────┬──────────┬─────────┘
                            │          │          │
                 ┌──────────▼───┐ ┌──▼────────┐ ┌▼──────────────┐
                 │ Context/STM  │ │ SQL Store │ │ Vector Index  │
                 │ hot working  │ │ source of  │ │ semantic      │
                 │ memory       │ │ truth      │ │ retrieval     │
                 └──────────────┘ └────┬───────┘ └─────┬────────┘
                                       │               │
                                   ┌───▼───────────────▼───┐
                                   │   Graph / Relations    │
                                   │ temporal + entities    │
                                   └─────────┬──────────────┘
                                             │
                            ┌────────────────▼────────────────┐
                            │   Archives / Raw Event Store    │
                            │ transcripts / traces / blobs    │
                            └─────────────────────────────────┘

      Background workers:
      extract → deduplicate → link → consolidate → reflect
      summarize → score → decay → archive → re-index → evaluate
```

### Why this design

A vector alone is excellent at approximate semantic similarity but is not a complete model of identity, time, causality, updates, exact identifiers, procedures, provenance, or operational state. The current open-source ecosystem reflects this shift: Mem0 supports structured extraction and graph relationships; Graphiti focuses on temporal context graphs; Letta/MemGPT uses tiered memory; MemOS treats memory as a first-class orchestrated resource; A-MEM creates linked, evolving notes; SimpleMem emphasizes semantic-lossless compression; OpenClaw combines SQLite FTS5, vector search, recency, importance and MMR; and OpenViking separates resources, memories, and skills. citeturn309978search11turn429276search0turn429276view0turn461845academia17turn199336view2turn199336view0turn199336view1turn397070view3turn397070view1

---

# 2. Agentic Memory: The Full Practical Taxonomy

There is no single universally accepted “all memory types” taxonomy for AI agents. The following is a **comprehensive engineering taxonomy** covering the major categories appearing across agent frameworks, research systems, and memory databases.

## 2.1 Context and time-horizon memory

| Type | What it stores | Best purpose | Typical lifetime | Recommended Alpha representation |
|---|---|---|---|---|
| Sensory/input buffer | Latest raw observations, tool outputs, user input | Immediate perception | milliseconds–seconds | in-process buffer |
| Working memory | Current reasoning context, intermediate facts | Active reasoning | current turn/task | bounded structured state + prompt context |
| Scratchpad | Temporary reasoning/execution notes | Planning and computation | one run | ephemeral files/DB |
| Short-term memory | Recent interaction history | Multi-turn continuity | minutes–days | session store |
| Session memory | Everything important within one conversation/run | Resume interrupted work | session | append-only events + checkpoint |
| Mid-term memory | Recent summarized episodes | Multi-session continuity | days–weeks | compressed event summaries |
| Long-term memory | Durable reusable information | Personalization and continuity | months–years | SQL + vector + graph |
| Archival memory | Large/rarely accessed historical material | Deep recall / audit | indefinite | compressed/raw archive + index |
| Cache memory | Frequently retrieved memory | Low latency | TTL-based | local cache |
| Checkpoint memory | Exact agent state at a point in execution | Recovery / resume / time travel | until cleanup | serialized state snapshots |

## 2.2 Cognitive/content memory

| Type | What it stores | Use it for |
|---|---|---|
| Episodic memory | Events and experiences with time/context | “What happened?” “What did we do last time?” |
| Semantic memory | Stable facts and conceptual knowledge | “What is true?” |
| Procedural memory | How to perform a task | “How do I do this?” |
| Autobiographical memory | Agent/user history and identity-relevant events | long-running identity and continuity |
| Prospective memory | Future intentions and commitments | reminders, pending actions, goals |
| Preference memory | Likes, dislikes, formatting/style choices | personalization |
| Profile memory | Stable user or agent attributes | user modeling |
| Identity memory | Agent identity/name/persona/role | persistent agent identity |
| Soul/constitution memory | Durable rules, values, boundaries, operating principles | consistent behavior |
| Experience memory | Distilled lessons from completed work | reuse successful patterns |
| Reflection memory | Explicit lessons learned from evaluation | self-improvement |
| Case memory | Prior problem → solution examples | case-based reasoning |
| Trajectory memory | Successful execution paths / sequences | reusable planning and tool-use patterns |
| Skill memory | Reusable skills and workflow knowledge | automatic skill reuse |
| Tool memory | Tool capabilities, constraints, failure modes, successful calls | reliable tool selection |
| Failure memory | Failed attempts, errors, root causes, mitigations | avoid repeating mistakes |
| Recovery memory | How the system recovered from failures | self-healing / resilience |
| Decision memory | Decisions, alternatives, rationale | consistency and auditability |
| Goal memory | Current goals and priorities | planning and execution |
| Plan memory | Active and historical plans | resumption, comparison, optimization |
| Task memory | Task objects, status, dependencies | long-running work management |
| Project memory | Shared project knowledge | coding/research/build work |
| Codebase memory | architecture, files, APIs, conventions, known bugs | software engineering agents |
| Environment/world-state memory | Machine, services, files, deployments, resources | operations / autonomous computer use |
| Social/team memory | Other agents, people, roles, communication history | multi-agent collaboration |
| Organizational memory | Shared policies, procedures, knowledge | agent companies / teams |

## 2.3 Structural memory

| Type | Structure | Best use |
|---|---|---|
| Entity memory | canonical entities | person/project/tool/org identity |
| Relationship memory | edges between entities | “who works with what?” |
| Knowledge-graph memory | entity/relation graph with attributes | multi-hop reasoning |
| Temporal graph memory | graph with validity intervals/history | changing facts and “as-of” questions |
| Causal memory | cause→effect relationships | diagnosis and decision support |
| Provenance memory | source, author, timestamp, evidence chain | trustworthy recall |
| Version memory | supersedes/replaces history | evolving facts |
| Contradiction memory | conflicting statements | update and truth resolution |
| Hierarchical memory | coarse→fine representations | scalable retrieval |
| Cluster/topic memory | grouped memories by subject | thematic recall |
| Linked-note memory | notes linked by semantic relationships | self-organizing knowledge |
| Document/resource memory | external artifacts | RAG and reference retrieval |
| Skill-resource memory | skills connected to resources/tools | executable knowledge |

## 2.4 Representation-level memory

| Type | Representation | Purpose |
|---|---|---|
| Raw memory | original message/event/tool result | maximum fidelity |
| Structured memory | JSON/rows/typed objects | exact filtering and updates |
| Text memory | human-readable notes | inspectability and editing |
| Vector memory | embeddings | semantic similarity |
| Keyword memory | inverted index / BM25 | exact terms, IDs, errors |
| Graph memory | nodes/edges | relational and multi-hop retrieval |
| Sparse vector memory | learned or lexical sparse representations | exact/semantic hybrid |
| Multimodal memory | image/audio/video embeddings + metadata | cross-modal recall |
| Activation memory | KV cache / hidden states | fast reuse of context |
| Parametric memory | model weights | stable learned domain knowledge |
| Compressed memory | summaries/gists/distillations | lower storage/context cost |
| Hybrid memory | multiple representations for one fact | robust retrieval |

MemOS explicitly distinguishes parametric, activation, and plaintext/explicit memory, while OpenViking separates resources, memories, and skills and supports layered context. citeturn397070view0turn397070view1

---

# 3. Memory Operations Are as Important as Memory Types

A memory system is defined not only by what it stores but by the **lifecycle operations** it performs.

## 3.1 Write / encoding

The agent observes an interaction and decides what should become memory.

Recommended operations:

1. **Capture** — save the raw event/trace first.
2. **Normalize** — convert messages/tool calls/results into a canonical event schema.
3. **Extract** — derive facts, preferences, decisions, entities, goals, failures, procedures, etc.
4. **Classify** — choose memory type(s).
5. **Score** — estimate importance, salience, confidence, utility, novelty.
6. **Deduplicate** — detect equivalent memories.
7. **Resolve entities** — connect “the CRM project” with its canonical project identity.
8. **Link** — create graph relationships and related-memory edges.
9. **Version** — mark what the memory supersedes or contradicts.
10. **Index** — update lexical, vector, graph, temporal and metadata indexes.

Mem0’s current open-source documentation describes a similar extraction pipeline: lookup related memory, extract facts, deduplicate, embed, then retrieve them later; its graph-memory layer adds entity/relation extraction and graph context. citeturn309978search11turn429276search0

## 3.2 Read / recall

Alpha should use **multi-stage retrieval** rather than “embed query → top K vectors”.

```text
Query
  │
  ├── exact/keyword retrieval ──→ BM25/FTS
  ├── semantic retrieval ───────→ embeddings
  ├── entity retrieval ─────────→ graph/entities
  ├── temporal retrieval ───────→ event_time / validity
  ├── task retrieval ───────────→ goals/project/task scope
  └── procedural retrieval ─────→ skills/cases/trajectories
                │
                ▼
          candidate fusion
                │
          metadata filters
                │
          score + rerank
                │
          redundancy removal
                │
          evidence validation
                │
                ▼
        context composer
                │
                ▼
             LLM
```

OpenClaw’s current built-in engine is a strong practical example: SQLite + FTS5 keyword search, vector search, hybrid fusion, recency/importance weighting, and MMR diversity. citeturn397070view3turn654400search2

## 3.3 Consolidation

Consolidation turns many raw episodes into fewer durable memories.

Examples:

```text
100 tool traces
      ↓
20 episode summaries
      ↓
6 recurring lessons
      ↓
2 durable procedures
      ↓
1 updated skill
```

Do this asynchronously after task completion, not on every token.

OpenClaw uses a curated long-term file plus detailed daily notes and a background “dreaming” sweep to distill useful material into durable memory; MemOS uses an explicit memory operating system and scheduling model. citeturn397070view2turn199336view3

## 3.4 Reconsolidation / evolution

When a new fact arrives, do not blindly append it.

A better policy is:

```text
new memory
   │
   ├─ same fact? → merge
   ├─ refinement? → update
   ├─ replacement? → supersede old
   ├─ contradiction? → keep both + resolve status
   └─ unrelated? → create new node
```

A-MEM explicitly emphasizes linked notes and continuous memory evolution; Graphiti focuses on facts that can change over time while preserving historical context; Mem0 graph memory links entities/relations. citeturn199336view0turn429276view0turn429276search0

## 3.5 Forgetting / decay / archival

Memory should not grow forever at the same fidelity.

Use at least four states:

```text
ACTIVE → COMPRESSED → ARCHIVED → PURGED
```

A memory may be compressed because it is old but still useful, rather than deleted.

Useful signals:

- last access time
- access frequency
- importance
- confidence
- novelty
- task relevance
- recency
- source authority
- contradiction status
- user/agent scope
- storage cost
- retrieval utility

MemoryBank, LightMem, SimpleMem and other research lines demonstrate different forms of forgetting, compression, or tiered memory; current work continues to explore evidence-preserving reconstruction rather than simply throwing away old content. citeturn461845search6turn461845search4turn199336view1

---

# 4. What Major Open-Source Projects Contribute

## 4.1 Letta / MemGPT

**Repository:** https://github.com/letta-ai/letta  
**License:** Apache-2.0

Letta is the current continuation of MemGPT. The repository explicitly describes it as a platform for stateful agents with advanced memory, and its current repository is Apache-2.0. citeturn429276view2

### Important memory ideas to borrow

- Treat memory as a runtime resource, not a static database.
- Separate in-context memory from archival memory.
- Let the agent explicitly manage what remains in its active context.
- Support long-running stateful agents.
- Use memory blocks for structured persistent state.
- Support recovery across sessions/restarts.

The original MemGPT research introduced an OS-inspired virtual memory model that moves information between fast context and slower external storage. citeturn461845academia17

### Alpha adaptation

Borrow the **tiering concept**, but make Alpha’s memory orchestrator deterministic and inspectable rather than relying entirely on an LLM to decide everything.

---

## 4.2 Mem0

**Repository:** https://github.com/mem0ai/mem0  
**License:** Apache-2.0

Mem0 is a dedicated memory layer for agents. Its open-source implementation performs memory extraction, deduplication, embeddings and search, with graph memory extending the representation into entities/relationships. citeturn438199view2turn309978search11turn429276search0

### Important ideas

- Extract durable facts instead of storing every message as a permanent memory.
- Treat agent-generated facts as useful memory, not just user statements.
- Use metadata and scopes.
- Combine vector and graph retrieval.
- Support user/agent/run-scoped memory.
- Separate memory extraction from retrieval.

### Alpha adaptation

Use Mem0 as inspiration for an **Admission + Extraction Engine**:

```text
interaction → candidate facts → importance/confidence → dedupe → store → index
```

---

## 4.3 MemOS

**Repository:** https://github.com/MemTensor/MemOS  
**License:** Apache-2.0

MemOS explicitly frames memory as a **Memory Operating System** with orchestration, MemCubes, multiple memory types, lifecycle management, scheduling, feedback/correction, graph structure and multimodal/tool/persona memory. citeturn199336view2turn397070view0turn911690search1

### Important ideas

- Memory as a first-class resource.
- Multiple memory types in composable memory containers.
- Scheduling of memory work.
- Explainable/inspectable memory.
- Feedback/correction.
- Multimodal memory.
- Tool traces and personas as memory.
- Memory promotion and lifecycle management.

### Alpha adaptation

Make Alpha’s top-level object:

```text
MemoryFabric
  ├─ Scope
  ├─ MemoryCube
  ├─ MemoryType
  ├─ Indexes
  ├─ Scheduler
  ├─ Lifecycle
  └─ Policies
```

This is a particularly useful architectural concept for Alpha because your agent system already has users, agents, projects, teams, tasks, skills and tools.

---

## 4.4 Graphiti / Zep ecosystem

**Graphiti:** https://github.com/getzep/graphiti  
**License:** Apache-2.0

Graphiti is a framework for temporal knowledge graphs for agents. Its current repository describes temporal context graphs that preserve changes over time, provenance, incremental updates and hybrid retrieval across semantics, keywords and graph traversal. citeturn429276view0

The `getzep/zep` repository is now primarily examples/integrations; its README points developers to Graphiti for the open-source temporal knowledge-graph framework and marks the old Zep Community Edition as deprecated/unsupported. citeturn410057view0

### Important ideas

- Facts have history.
- A graph can answer relational questions that vectors alone cannot.
- Incremental updates avoid rebuilding a whole graph.
- Temporal queries make “what was true before?” possible.
- Provenance is part of memory quality.

### Alpha adaptation

Use a temporal graph for:

- people ↔ projects
- agent ↔ skill
- skill ↔ tool
- task ↔ agent
- decision ↔ project
- fact ↔ evidence
- state ↔ validity interval

---

## 4.5 A-MEM

**Repository:** https://github.com/agiresearch/A-mem  
**License:** MIT

A-MEM presents agentic memory based on Zettelkasten-style linked notes. New memories get structured attributes, relevant memories are linked dynamically, and the network can evolve as new information arrives. The official repository is MIT-licensed. citeturn199336view0turn461845academia15

### Important ideas

- Memories are notes, not isolated vectors.
- Link new notes to relevant historical notes.
- Let the memory network evolve.
- Store contextual descriptions, keywords and tags.

### Alpha adaptation

Add a `related_memory_ids` / `edges` layer to every memory object.

---

## 4.6 SimpleMem

**Repository:** https://github.com/aiming-lab/SimpleMem  
**License:** MIT

SimpleMem focuses on efficient lifelong memory with semantic-lossless compression and current releases include multimodal support for text, image, audio and video. Its repository is MIT-licensed. citeturn199336view1turn911690search15

### Important ideas

- Preserve information while compressing it.
- Reduce memory construction/retrieval overhead.
- Treat multimodal memory as first-class.
- Provide MCP/Python integration.

### Alpha adaptation

Alpha should preserve a raw event while generating one or more compressed forms:

```text
raw → gist → semantic fact → durable summary
```

That gives you recoverability without injecting the raw history into every prompt.

---

## 4.7 MemMachine

**Repository:** https://github.com/MemMachine/MemMachine  
**License:** Apache-2.0

MemMachine describes itself as an open-source long-term memory layer and exposes episodic, profile and working memory, with memory persistence across restarts/sessions/model changes. citeturn429276view1

### Important ideas

- Separate episodic, profile and working memory.
- Keep identity and session scoping explicit.
- Persist memory independently of the model.

### Alpha adaptation

Use separate storage scopes:

```text
user/{id}
agent/{id}
project/{id}
team/{id}
session/{id}
task/{id}
```

---

## 4.8 Memori

**Repository:** https://github.com/MemoriLabs/Memori  
**License:** Apache-2.0

Memori positions itself as an agent-native, LLM/datastore/framework-agnostic memory layer that turns conversations and agent execution into structured persistent state. The repository is Apache-2.0. citeturn438199view4turn309978search1

### Important ideas

- Memory from actual agent execution, not only chat text.
- LLM/datastore/framework agnosticism.
- Integrate with existing infrastructure.

### Alpha adaptation

Capture:

- user message
- model response
- tool call
- tool output
- error
- retry
- plan step
- subagent handoff
- outcome

as one unified memory event stream.

---

## 4.9 Cognee

**Repository:** https://github.com/topoteretes/cognee  
**License:** Apache-2.0

Cognee is an open-source AI memory platform centered on transforming documents, code and conversations into self-hosted knowledge graphs that agents can search and reuse. It can run locally without a mandatory OpenAI/Anthropic API key, using local extraction/embedding models. citeturn199336view5turn429640search4

### Important ideas

- Turn raw context into a reusable knowledge structure.
- Combine documents, code and conversations.
- Use a graph to connect knowledge.
- Keep memory self-hosted.

### Alpha adaptation

Use this pattern for project/codebase memory:

```text
docs + repository + issues + conversations
                 ↓
      canonical entities/facts
                 ↓
         knowledge graph
                 ↓
       hybrid semantic recall
```

---

## 4.10 LangMem

**Repository:** https://github.com/langchain-ai/langmem  
**License:** MIT

LangMem provides memory primitives, agent-controlled memory tools, and background memory management that can extract/consolidate/update agent knowledge. It works with any storage system and integrates with LangGraph’s storage layer. citeturn438199view1

### Important ideas

- Memory can be written/queried explicitly by the agent.
- Background memory managers can operate asynchronously.
- Storage should be pluggable.

### Alpha adaptation

Expose memory through an internal interface instead of binding the agent to a single database.

---

## 4.11 LlamaIndex

**Repository:** https://github.com/run-llama/llama_index  
**License:** MIT for the main project repository.

LlamaIndex provides broad data/indexing infrastructure and historical memory patterns such as short chat buffers, summary memory, vector memory and compositional memory; the current documentation emphasizes newer agent/context abstractions, but those patterns remain useful design references. citeturn438199view7turn309978search7turn309978search2

### Important ideas

- Compose multiple memory mechanisms.
- Keep memory separate from the agent runtime.
- Use different retrieval/index types for different content.

---

## 4.12 Haystack

**Repository:** https://github.com/deepset-ai/haystack  
**License:** Apache-2.0

Haystack is an open-source orchestration framework with explicit control over retrieval, routing, memory and generation. Its current project license is Apache-2.0. citeturn438199view6turn309978search8

### Important ideas

- Make retrieval and routing explicit.
- Compose memory into pipelines.
- Treat memory as an orchestration concern, not a hidden feature.

---

## 4.13 OpenClaw

**Repository:** https://github.com/openclaw/openclaw

OpenClaw’s current memory design is particularly useful as a practical reference for a local agent. It uses Markdown files such as `USER.md`, `MEMORY.md` and dated `memory/*.md` notes, with background consolidation. Its built-in memory engine stores an index in per-agent SQLite and supports FTS5 keyword search, vector search, hybrid retrieval, recency, importance and MMR. Local/self-hosted embedding providers are supported, including Ollama and local llama.cpp. citeturn397070view2turn397070view3turn654400search2

### Ideas worth copying for Alpha

- Human-readable memory files.
- Per-agent memory namespace.
- Curated durable memory vs detailed daily memory.
- Hybrid lexical + semantic retrieval.
- Recency + importance weighting.
- Explicit memory promotion.
- Local embeddings.
- Memory CLI/maintenance commands.

---

## 4.14 DeerFlow

**Repository:** https://github.com/bytedance/deer-flow  
**License:** MIT

DeerFlow is an agent runtime/harness rather than a pure memory product, but its current documentation shows an explicit persistent-memory subsystem: automatic extraction of user context/facts/preferences, structured storage, debounced updates, prompt injection, and project/runtime memory integration. The project is MIT-licensed. citeturn654400search4turn654400search7turn380457search0

### Ideas worth copying

- Memory integrated with long-horizon execution.
- Automatic capture after useful work.
- Project/task-aware memory.
- Stable prompt injection of only high-value context.
- Middleware-based lifecycle hooks.

---

## 4.15 OpenViking

**Repository:** https://github.com/volcengine/OpenViking

OpenViking is an agent-native context database that organizes **resources, memories and skills** in a virtual filesystem-like structure, with layered context loading and explicit memory types such as profile, preferences, entities, events, identity, soul, cases, trajectories and experiences. citeturn397070view1turn380457search9

### License warning

OpenViking’s current repository says its **main project is AGPLv3**, while some components/examples are Apache-2.0 or MIT. Therefore it should **not** be treated as a straightforward permissive commercial-copy baseline for Alpha without separate license review. citeturn380457search1turn380457search4

### Ideas worth copying conceptually

- Resource vs memory vs skill separation.
- URI/path based context namespaces.
- Layered loading: abstract → overview → full content.
- Memory types tied to agent behavior.
- Skill execution history as memory.

---

# 5. Research Systems Worth Studying

## MemGPT

OS-inspired virtual memory and explicit paging between context and external storage. citeturn461845academia17

## Generative Agents

The classic “memory stream → retrieval → reflection → planning” pattern is foundational for agent memory: raw observations are stored, relevant memories are retrieved, reflections form higher-level abstractions, and those abstractions inform future planning.

## Reflexion

Introduces verbal reinforcement where agents save feedback/lessons from previous attempts and reuse them in later attempts. This is the model for **reflection memory / lesson memory**.

## A-MEM

Dynamic linked-note memory inspired by Zettelkasten, with memory evolution and agent-driven organization. citeturn461845academia15

## MemoryBank

Long-term conversational memory with event summaries and forgetting/retention mechanisms. It is useful for studying memory decay.

## EverMemOS

A 2026 memory operating system research line that describes a lifecycle with episodic trace formation, semantic consolidation and reconstructive recollection. It introduces “MemCells”, “MemScenes” and foresight signals as structured memory primitives. citeturn461845academia16

## MemOS research line

Memory is explicitly treated as a system resource with scheduling, memory types and lifecycle management rather than only a vector store. citeturn397070view0turn199336view3

## SimpleMem / EvolveMem line

Focuses on lifelong memory efficiency, semantic-lossless compression and research into self-evolving memory architectures. The current SimpleMem repository identifies SimpleMem, EvolveMem and Omni-SimpleMem research directions. citeturn911690search15

## LongMemEval

A major benchmark for long-term interactive memory. It explicitly measures five abilities: **information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention**. Use these as Alpha’s baseline memory evaluation categories. citeturn461845academia14

---

# 6. Open-Source Memory Projects and License Matrix

The table below focuses on projects useful as code/architecture references where the current repository license was directly checked.

| Project | Main role | Current repo license checked | Commercial modification/distribution generally allowed?* | Best Alpha use |
|---|---|---:|---|---|
| Letta / MemGPT | Stateful agent + tiered memory | Apache-2.0 | Yes, subject to Apache terms | memory orchestration + stateful agents |
| Mem0 | dedicated memory layer | Apache-2.0 | Yes, subject to Apache terms | fact extraction + memory CRUD |
| MemOS | memory operating system | Apache-2.0 | Yes, subject to Apache terms | scheduler + multi-type memory |
| Graphiti | temporal context graph | Apache-2.0 | Yes, subject to Apache terms | temporal/entity graph |
| MemMachine | long-term memory layer | Apache-2.0 | Yes, subject to Apache terms | episodic/profile/working memory |
| Memori | execution-aware memory | Apache-2.0 | Yes, subject to Apache terms | structured execution memory |
| Cognee | knowledge/graph memory | Apache-2.0 | Yes, subject to Apache terms | project/code/document memory |
| LangMem | memory primitives | MIT | Yes, subject to MIT terms | pluggable memory API |
| LlamaIndex | indexing/data/agent framework | MIT | Yes, subject to MIT terms | composite retrieval/indexing |
| Haystack | orchestration/retrieval framework | Apache-2.0 | Yes, subject to Apache terms | pipeline-based retrieval |
| A-MEM | agentic linked-note memory | MIT | Yes, subject to MIT terms | self-organizing links/evolution |
| SimpleMem | lifelong compressed memory | MIT | Yes, subject to MIT terms | compression + multimodal memory |
| OpenClaw memory engine | practical local memory | open-source repo; check exact repo/version license before redistribution | Version-specific | SQLite + hybrid search reference |
| DeerFlow | agent harness with memory | MIT | Yes, subject to MIT terms | long-horizon integration |
| OpenViking | context DB / memory + skills | AGPLv3 main project | Not equivalent to permissive licensing | conceptual resource/memory/skill design |

\* “Generally allowed” is a summary of the cited license permissions, not legal advice. The safest commercial workflow is to pin an exact commit/version, preserve notices, and run an SBOM/license scanner over the whole dependency tree.

### License sources checked

- Letta: repository identifies Apache-2.0. citeturn429276view2
- Mem0: repository identifies Apache-2.0. citeturn438199view2turn309978search0
- MemOS: repository identifies Apache-2.0. citeturn199336view2turn911690search3
- Graphiti: repository identifies Apache-2.0. citeturn429276view0
- MemMachine: repository identifies Apache-2.0. citeturn429276view1
- Memori: repository license is Apache-2.0. citeturn309978search1
- Cognee: repository license is Apache-2.0. citeturn429640search4
- LangMem: repository identifies MIT. citeturn438199view1
- LlamaIndex: current repository identifies MIT. citeturn438199view7turn309978search2
- Haystack: current project metadata identifies Apache-2.0. citeturn309978search8turn309978search10
- A-MEM: repository identifies MIT. citeturn199336view0
- SimpleMem: repository identifies MIT. citeturn911690search15turn911690search7
- DeerFlow: repository identifies MIT. citeturn380457search0
- OpenViking: main project currently identifies AGPLv3 with component-specific exceptions. citeturn380457search1turn380457search4

---

# 7. Free & Open-Source Storage / Retrieval Building Blocks

Memory frameworks and memory databases are different layers. Alpha should keep them separate.

## 7.1 Relational / structured source of truth

### SQLite

Ideal for Alpha Desktop and single-node deployments:

- embedded
- durable
- transactional
- portable
- easy backup
- excellent metadata/filtering
- FTS5 available for lexical search

OpenClaw demonstrates a practical SQLite-based agent memory index. citeturn397070view3

### DuckDB

Useful for analytics, offline memory evaluation, and batch consolidation. DuckDB is MIT-licensed. citeturn420543search1turn420543search2

### PostgreSQL

Best for multi-agent / multi-user server deployment.

### pgvector

Allows vector storage/search inside PostgreSQL and uses a permissive PostgreSQL-style license for the extension. citeturn963291search1

### Recommended Alpha progression

```text
Desktop / single agent:
SQLite

Desktop + larger corpus:
SQLite + FAISS or Qdrant

Multi-agent server:
PostgreSQL + pgvector

Graph-heavy server:
PostgreSQL + Apache AGE
```

Apache AGE is Apache-2.0 and extends PostgreSQL with graph support. citeturn950249search1

---

## 7.2 Vector stores / indexes

### Qdrant

**License:** Apache-2.0  
**Use:** scalable vector + metadata search, local/self-hosted or server deployment. citeturn429640search5turn429640search13

### Milvus

**License:** Apache-2.0  
**Use:** large-scale distributed vector search; Milvus Lite provides a local-file mode. citeturn420543search6

### Weaviate

**License:** BSD-3-Clause  
**Use:** vector database + structured filtering and hybrid retrieval. citeturn420543search3turn420543search7

### FAISS

**License:** MIT  
**Use:** in-process high-performance similarity search; excellent for a lightweight local index. citeturn963291search0turn963291search8

### LanceDB

**Current GitHub organization listing:** Apache-2.0 for the main repository.  
**Use:** embedded/local vector and multimodal data workflows. citeturn950249search10

### Rule for Alpha

Do not make a vector DB the canonical source of truth. Keep canonical memory records in a structured durable store and treat vector/graph indexes as rebuildable indexes.

---

# 8. The Alpha Memory Model

## 8.1 Canonical memory record

Every durable memory should have a common envelope.

```json
{
  "id": "mem_01J...",
  "scope": {
    "tenant_id": "default",
    "user_id": "user_123",
    "agent_id": "alpha_main",
    "project_id": "project_abc",
    "team_id": null,
    "session_id": "sess_456",
    "task_id": "task_789"
  },
  "type": ["episodic", "semantic"],
  "subtype": "decision",
  "content": "The project will use PostgreSQL for production persistence.",
  "summary": "Project DB decision: PostgreSQL.",
  "entities": ["project_abc", "postgresql"],
  "relations": [],
  "source": {
    "kind": "conversation",
    "event_id": "evt_123",
    "uri": "session://sess_456/event/123"
  },
  "timestamps": {
    "created_at": "...",
    "observed_at": "...",
    "valid_from": "...",
    "valid_to": null,
    "last_accessed_at": null
  },
  "quality": {
    "importance": 0.92,
    "confidence": 0.95,
    "salience": 0.87,
    "novelty": 0.72
  },
  "lifecycle": {
    "status": "active",
    "access_count": 0,
    "decay_rate": 0.05,
    "ttl": null,
    "supersedes": [],
    "superseded_by": [],
    "contradicts": []
  },
  "representations": {
    "raw": true,
    "text": true,
    "embedding": true,
    "graph": true,
    "summary": true
  },
  "tags": ["database", "architecture"],
  "security": {
    "classification": "normal",
    "acl": ["project:project_abc"]
  }
}
```

## 8.2 Why all these fields exist

- `scope` prevents cross-user/project/agent contamination.
- `type` enables specialized retrieval.
- `source` makes memory auditable.
- timestamps enable temporal reasoning.
- quality scores support admission and ranking.
- lifecycle fields make evolution explicit.
- representations let indexes be rebuilt.
- relations enable graph reasoning.

---

# 9. Alpha Memory Namespaces

Use a namespace model instead of one giant memory bucket.

```text
alpha://memory/
│
├── global/
│   ├── system-facts/
│   ├── shared-skills/
│   └── organization/
│
├── users/{user_id}/
│   ├── profile/
│   ├── preferences/
│   ├── history/
│   ├── goals/
│   └── relationships/
│
├── agents/{agent_id}/
│   ├── identity/
│   ├── soul/
│   ├── lessons/
│   ├── tool-memory/
│   ├── experiences/
│   └── failures/
│
├── projects/{project_id}/
│   ├── architecture/
│   ├── decisions/
│   ├── codebase/
│   ├── issues/
│   ├── resources/
│   └── skills/
│
├── teams/{team_id}/
│   ├── members/
│   ├── decisions/
│   ├── protocols/
│   └── shared-knowledge/
│
├── tasks/{task_id}/
│   ├── plan/
│   ├── execution/
│   ├── checkpoints/
│   ├── evidence/
│   └── lessons/
│
└── archives/
    ├── sessions/
    ├── traces/
    └── snapshots/
```

---

# 10. Memory Admission: What Should Be Remembered?

A world-class memory system should not persist everything as “important”.

Use an admission score:

```text
memory_score =
    0.25 * importance
  + 0.20 * future_utility
  + 0.15 * novelty
  + 0.15 * confidence
  + 0.10 * recurrence
  + 0.10 * task_relevance
  + 0.05 * explicit_user_request
```

Then apply hard rules.

### Always remember

- explicit “remember this” requests
- durable project decisions
- stable user preferences
- important constraints
- successful recovery procedures
- reusable skills
- canonical entity relationships
- major task outcomes

### Usually remember

- repeated facts
- recurring tool errors
- useful workflow patterns
- important environment discoveries
- non-obvious project conventions

### Usually keep only in episodic/raw memory

- routine assistant text
- redundant status messages
- low-value intermediate reasoning
- repeated tool output that adds no new information

### Do not promote blindly

- uncertain guesses
- temporary values
- raw secrets/tokens
- highly volatile values
- unverified third-party claims

---

# 11. Retrieval Scoring

A robust retrieval function should combine multiple signals.

```text
final_score =
    0.30 * semantic_score
  + 0.20 * lexical_score
  + 0.15 * graph_score
  + 0.10 * temporal_score
  + 0.10 * scope_match
  + 0.05 * importance
  + 0.05 * confidence
  + 0.03 * task_relevance
  + 0.02 * access_history
  - redundancy_penalty
  - contradiction_penalty
```

### Retrieval modes

#### Exact mode

Use for:

- IDs
- filenames
- error strings
- API names
- version numbers
- environment variables
- class/function names

#### Semantic mode

Use for:

- concepts
- paraphrases
- user preferences
- broad project questions

#### Graph mode

Use for:

- relationships
- multi-hop queries
- “what depends on X?”
- “which agent learned this skill?”

#### Temporal mode

Use for:

- “latest”
- “as of last week”
- “what changed?”
- “what did we believe before?”

#### Procedural mode

Use for:

- “how did we solve this?”
- “which workflow worked?”
- “what tool sequence succeeded?”

---

# 12. Memory Composition

The agent should not receive 50 raw search results.

Create a **Context Composer**.

```text
Retrieval candidates
      ↓
filter by scope
      ↓
remove contradictions / stale versions
      ↓
merge duplicate facts
      ↓
select diversity
      ↓
build evidence groups
      ↓
allocate token budget by memory type
      ↓
compose compact context
```

Example budget for a coding task:

```text
10% identity / soul
15% project facts
20% recent task state
20% relevant codebase facts
15% successful procedures / skills
10% failures / lessons
10% evidence / provenance
```

The budget should be dynamic. A debugging task should emphasize failures and codebase context; a planning task should emphasize goals, decisions, and trajectories.

---

# 13. Memory Consolidation / Dreaming Engine

Run a background worker after a meaningful task completes.

## Pipeline

```text
session/trace
   ↓
segment into episodes
   ↓
extract facts/entities/decisions
   ↓
score candidates
   ↓
link to existing memory
   ↓
detect conflicts
   ↓
compress repeated information
   ↓
create lessons
   ↓
update skills/trajectories
   ↓
update project/user/agent models
   ↓
archive low-value raw material
   ↓
re-index
```

## Example

```text
Raw:
"We tried FastAPI with migration X and it failed because ..."

Episode:
"Database migration attempt failed due to ..."

Lesson:
"When schema version is Y, apply migration Z before ..."

Procedure:
"Before starting the migration agent should inspect ..."

Skill update:
"db-migration-check"
```

This creates a bridge between **memory** and your existing Alpha **RSI/self-improvement** engine.

---

# 14. Self-Improving Memory

Alpha should evaluate its own memory system.

## Memory feedback loop

```text
Task
 ↓
Recall
 ↓
Action
 ↓
Outcome
 ↓
Was recall useful?
 ↓
Memory credit assignment
 ↓
Adjust:
  - admission policy
  - retrieval weights
  - decay
  - summarization strategy
  - memory type classification
  - skill promotion
```

Store these feedback events:

```text
memory_used
memory_helped
memory_hurt
memory_irrelevant
memory_missing
memory_outdated
memory_contradictory
memory_verified
```

This creates **memory-level reinforcement signals** without requiring model fine-tuning.

---

# 15. Agent-to-Agent Shared Memory

Alpha’s multi-agent architecture makes scoped shared memory especially important.

## Three levels

### Private memory

Only one agent can read/write it.

```text
agent://alpha-main/private/*
```

### Shared project memory

A group of agents working on one project can access it.

```text
project://alpha/shared/*
```

### Global organization memory

Reusable knowledge that multiple projects/agents can read.

```text
org://global/*
```

## Memory write events

Agents should emit structured events, not direct arbitrary DB mutations.

```json
{
  "event": "memory.propose",
  "agent_id": "agent_coder_01",
  "scope": "project:alpha",
  "memory_type": "procedural",
  "payload": {
    "summary": "Run tests before database migration",
    "evidence": ["task_123", "run_456"]
  }
}
```

The memory orchestrator accepts, rejects, merges or quarantines the proposal.

This prevents one agent from silently rewriting shared truth.

---

# 16. Memory of Skills and Tool Use

For Alpha, **procedural/tool memory is unusually important**.

Every tool execution can produce:

```text
Tool:
  name
  version
  inputs shape
  context

Outcome:
  success/failure
  latency
  output quality
  error class

Procedure:
  preconditions
  steps
  postconditions

Lesson:
  what to do next time
```

Example:

```yaml
skill: git-recover-conflict
success_rate: 0.82
preconditions:
  - working tree not clean
  - merge conflict detected
procedure:
  - save changes
  - inspect status
  - inspect conflicted files
  - resolve
  - run targeted tests
  - commit
known_failures:
  - generated files conflict
last_verified: 2026-09-25
```

This should feed your existing dynamic skill system.

---

# 17. Codebase Memory

For a coding agent, create a dedicated memory family.

## Store

- repository identity
- architecture map
- package map
- important files
- entry points
- APIs
- schemas
- coding conventions
- build/test commands
- dependency relationships
- recurring bugs
- deployment assumptions
- decisions
- failed approaches
- successful patches
- CI failures
- performance discoveries
- developer preferences

## Retrieval examples

```text
"Where is authentication handled?"
→ codebase semantic + lexical search

"Why did we choose PostgreSQL?"
→ decision memory + provenance

"How did we fix this migration issue last time?"
→ failure + procedural + episodic memory

"Which agent owns the deployment workflow?"
→ entity/relationship memory
```

---

# 18. Temporal Memory

Every fact that can change should have validity information.

Bad:

```text
user_location = X
```

Better:

```text
fact = user_location
value = X
observed_at = 2026-09-01
valid_from = 2026-09-01
valid_to = null
confidence = 0.87
```

When the value changes:

```text
old.valid_to = new.valid_from
new.valid_from = now
new.supersedes = old.id
```

Questions Alpha should then answer:

- what is true now?
- what was true last month?
- when did the change happen?
- what source caused the update?
- how confident are we?

Graphiti is a major reference for this style of temporal context graph. citeturn429276view0

---

# 19. Evidence / Provenance Memory

Every important durable fact should be traceable.

```text
memory → source event → session → agent action → external artifact
```

Store:

- source type
- source ID
- timestamp
- originating agent
- originating task
- file/document URI
- tool call ID
- evidence excerpt hash
- confidence

This enables:

- explanation
- debugging
- memory correction
- selective deletion
- stale-memory detection
- user trust

---

# 20. Contradiction Resolution

Do not use “last write wins” blindly.

Use a state machine:

```text
new fact
  │
  ├─ same as current → reinforce
  ├─ more specific → replace/merge
  ├─ higher authority → supersede
  ├─ lower confidence → retain as historical alternative
  └─ unresolved → contradiction set
```

Store:

```text
supports
contradicts
supersedes
superseded_by
verified_by
```

The agent can then retrieve both sides when needed instead of hallucinating certainty.

---

# 21. Memory Security Without Making the System Complicated

A completely free memory engine does not need a giant enterprise security layer, but it should have a few hard boundaries.

## Minimum rules

- Separate `user_id`, `agent_id`, `project_id`, `team_id` scopes.
- Never persist secrets in normal semantic memory.
- Keep secrets in the existing secret manager/environment system.
- Mark memory sensitivity/classification.
- Enforce scope before retrieval.
- Record provenance for shared memory.
- Allow explicit `forget(memory_id)`.
- Allow project/user/agent bulk deletion.
- Keep raw event archives optional.
- Never allow a retrieved memory to become a tool authorization by itself.

---

# 22. Completely Free Local-First Alpha Stack

## Tier A — smallest Windows laptop deployment

```text
Alpha runtime
  ↓
SQLite
  ├─ canonical memory records
  ├─ event log
  ├─ metadata
  ├─ FTS5 keyword index
  └─ lifecycle state

Local embedding model
  ↓
FAISS or an embedded vector index

JSON/Markdown
  ↓
Human-readable memory exports
```

This is the easiest zero-cost configuration.

FAISS is MIT-licensed. citeturn963291search0

## Tier B — stronger local deployment

```text
Alpha
 ↓
SQLite canonical store
 ├─ FTS5
 └─ metadata

Qdrant local/self-hosted
 └─ semantic vectors

Apache AGE or separate graph engine (optional)
 └─ temporal/entity graph
```

Qdrant is Apache-2.0; Apache AGE is Apache-2.0. citeturn429640search5turn950249search1

## Tier C — multi-agent/server deployment

```text
PostgreSQL
 ├─ canonical memory
 ├─ pgvector
 └─ relational metadata

Apache AGE
 └─ knowledge graph

Object/file storage
 └─ raw archives
```

pgvector uses a PostgreSQL-style permissive license; Apache AGE is Apache-2.0. citeturn963291search1turn950249search1

---

# 23. Recommended Alpha Architecture: “Memory Fabric”

## Core modules

```text
alpha_memory/
├── api/
│   ├── memory_api.py
│   ├── schemas.py
│   └── events.py
│
├── core/
│   ├── orchestrator.py
│   ├── scopes.py
│   ├── policies.py
│   ├── scoring.py
│   └── lifecycle.py
│
├── capture/
│   ├── conversation.py
│   ├── tool_trace.py
│   ├── task_trace.py
│   └── agent_events.py
│
├── extraction/
│   ├── facts.py
│   ├── entities.py
│   ├── preferences.py
│   ├── decisions.py
│   ├── lessons.py
│   ├── procedures.py
│   └── goals.py
│
├── storage/
│   ├── sql.py
│   ├── sqlite.py
│   ├── postgres.py
│   └── archive.py
│
├── indexes/
│   ├── lexical.py
│   ├── vector.py
│   ├── graph.py
│   └── temporal.py
│
├── retrieval/
│   ├── lexical.py
│   ├── semantic.py
│   ├── graph.py
│   ├── temporal.py
│   ├── procedural.py
│   ├── fusion.py
│   ├── rerank.py
│   └── composer.py
│
├── consolidation/
│   ├── summarize.py
│   ├── cluster.py
│   ├── dedupe.py
│   ├── conflict.py
│   ├── reflection.py
│   └── promotion.py
│
├── evolution/
│   ├── feedback.py
│   ├── utility.py
│   ├── decay.py
│   └── memory_learning.py
│
├── namespaces/
│   ├── user.py
│   ├── agent.py
│   ├── project.py
│   ├── team.py
│   └── task.py
│
├── multimodal/
│   ├── image.py
│   ├── audio.py
│   └── video.py
│
└── evaluation/
    ├── locomo.py
    ├── longmemeval.py
    ├── retrieval.py
    ├── temporal.py
    └── regression.py
```

---

# 24. Memory API

Expose a stable internal API so Alpha is not tied to one memory implementation.

```text
remember(input, scope, policy)
recall(query, scope, filters, budget)
get(memory_id)
update(memory_id, patch)
forget(memory_id)
link(memory_a, memory_b, relation)
supersede(old, new)
search(query, mode)
search_hybrid(query)
search_temporal(query, time)
search_procedural(query)
get_context(task)
record_event(event)
checkpoint(session)
consolidate(scope)
reflect(task_id)
promote(memory_id)
demote(memory_id)
archive(memory_id)
restore(memory_id)
explain(memory_id)
health()
metrics()
```

---

# 25. Memory Policy Engine

The agent should not decide everything manually.

```yaml
policy:
  explicit_remember:
    action: always_store

  user_preference:
    action: durable_profile
    min_confidence: 0.70

  project_decision:
    action: durable_project
    require_provenance: true

  successful_procedure:
    action: promote_to_skill_candidate
    min_successes: 2

  transient_status:
    action: session_only

  raw_tool_output:
    action: episodic_archive

  secret_like:
    action: reject
```

Make policies hot-reloadable so Alpha can evolve without code changes.

---

# 26. Memory Health Dashboard

Alpha should expose:

### Volume

- total memories
- active memories
- archived memories
- memories per type
- memories per agent/project/user

### Quality

- average confidence
- unresolved contradictions
- stale memories
- duplicate rate
- low-value memory ratio

### Retrieval

- hit rate
- recall@K
- MRR / nDCG
- average result count
- retrieval latency
- reranking latency

### Utility

- memories used
- memories useful
- memories ignored
- memories harmful
- missing-memory incidents

### Lifecycle

- promotions
- consolidations
- archives
- deletions
- restores

### Storage

- SQLite size
- vector index size
- graph size
- raw archive size
- embedding count

---

# 27. Memory Benchmarks for Alpha

At minimum, benchmark five dimensions inspired by LongMemEval:

1. **Information extraction** — did Alpha store the important fact?
2. **Multi-session reasoning** — can it connect information across sessions?
3. **Temporal reasoning** — can it distinguish old vs current truth?
4. **Knowledge updates** — does it properly replace/supersede changed facts?
5. **Abstention** — does it avoid inventing a memory when evidence is missing?

LongMemEval explicitly evaluates these five capabilities. citeturn461845academia14

### Add Alpha-specific benchmarks

- procedural reuse
- failure avoidance
- tool-selection memory
- project memory retrieval
- agent-to-agent knowledge sharing
- memory contamination rate
- memory write precision
- contradiction recovery
- evidence traceability
- context token efficiency
- memory maintenance cost
- cold-start performance
- local-only/offline reliability

---

# 28. Memory Test Dataset Structure

```json
{
  "id": "case_001",
  "sessions": [
    {
      "session_id": "s1",
      "events": [
        "User prefers X",
        "Agent learns project decision Y"
      ]
    },
    {
      "session_id": "s2",
      "events": [
        "User changes preference X → Z"
      ]
    }
  ],
  "questions": [
    {
      "question": "What is the current preference?",
      "expected": "Z",
      "type": "knowledge_update"
    }
  ]
}
```

---

# 29. Alpha “World-Class” Memory Flow

## On user/task start

```text
INPUT
 ↓
Identify scope
 ↓
Detect task type
 ↓
Query memory policy
 ↓
Recall relevant:
  user/profile
  project
  task
  procedures
  failures
  latest facts
  entities
 ↓
Compose context
 ↓
Agent starts
```

## During execution

```text
event
 ↓
append raw event
 ↓
update working/session memory
 ↓
extract critical candidate memory asynchronously
 ↓
do not block normal execution unless memory is required
```

## After execution

```text
outcome
 ↓
judge success/failure
 ↓
create episode
 ↓
extract lessons
 ↓
update procedures
 ↓
update graph
 ↓
update project/user/agent memory
 ↓
consolidate
 ↓
archive low-value details
```

## On failure/recovery

```text
failure
 ↓
store exact error + context
 ↓
attempt recovery
 ↓
store successful recovery
 ↓
link failure ↔ recovery
 ↓
promote repeated successful recovery to skill
```

This is especially important for your long-running/self-healing Alpha architecture.

---

# 30. Suggested Memory Type Routing Table for Alpha

| Situation | Primary memory | Secondary memory | Retrieval mode |
|---|---|---|---|
| normal chat | session | profile/preferences | recency + semantic |
| “remember this” | semantic | profile/project | exact + semantic |
| project decision | decision | semantic + provenance | project + temporal |
| task execution | episodic | task/plan | recent + task |
| completed task | episode | lesson/procedure | task + semantic |
| coding bug | failure | codebase + procedure | lexical + semantic + graph |
| successful fix | procedure | case/skill | procedural + semantic |
| repeated workflow | skill | trajectory | procedural |
| user preference | preference | profile | exact + semantic |
| changing fact | temporal semantic | graph | temporal + graph |
| people/agents | entity | relationship | graph |
| multi-agent handoff | task/session | shared project | exact + recent |
| large documents | resource | semantic | hierarchical semantic |
| image/audio/video | multimodal | episode | multimodal + semantic |
| current machine state | environment | episodic | exact + recency |
| “why did we do this?” | decision | provenance | graph + exact |
| “how did we solve it?” | trajectory/procedure | failure/lesson | procedural |
| “what happened last week?” | episodic | temporal | temporal + recency |
| “what changed?” | version/temporal | provenance | diff/temporal |

---

# 31. What NOT to Build

Avoid these anti-patterns:

## One giant MEMORY.md

Good for a tiny personal agent, but eventually becomes a bottleneck.

## One vector collection for everything

It loses strong typing and exact update semantics.

## Store every message forever as “memory”

It creates noise and retrieval pollution.

## Only summarize and delete the raw event

You lose forensic/evidence value.

## Only keep raw history

Retrieval cost becomes too high.

## Let the LLM arbitrarily rewrite shared memory

Shared memory becomes unstable and hard to debug.

## No timestamps

You cannot answer “what was true before?” reliably.

## No source/provenance

You cannot explain or correct memory.

## No explicit forgetting

Memory grows indefinitely.

## No evaluation

You will not know whether memory actually helps.

---

# 32. Recommended Alpha Reference Implementation Order

## Phase 1 — Free local foundation

Implement:

- SQLite source of truth
- FTS5 lexical search
- session/event memory
- user/profile/preference memory
- project/task memory
- vector embeddings with local model
- hybrid search
- explicit `remember()` / `recall()` / `forget()`
- provenance

## Phase 2 — Agentic memory

Add:

- entity extraction
- relationship graph
- contradiction detection
- temporal validity
- memory scoring
- deduplication
- consolidation
- reflection
- failure/lesson memory

## Phase 3 — Procedural intelligence

Add:

- trajectory memory
- skill candidates
- tool memory
- successful pattern mining
- recovery memory
- project/codebase memory

## Phase 4 — Memory OS

Add:

- MemoryCube-like containers
- scheduler
- asynchronous consolidation
- memory budgets
- tier promotion/demotion
- archive/restore
- memory dashboard
- memory health checks

## Phase 5 — Self-evolving memory

Add:

- retrieval feedback
- memory utility scoring
- automatic policy tuning
- adaptive compression
- automatic type reclassification
- skill promotion based on repeated success
- benchmark regression gate

## Phase 6 — Distributed Alpha memory

Add:

- Postgres backend
- pgvector
- graph backend
- shared team memory
- agent-to-agent shared namespaces
- remote replication
- conflict-aware synchronization

---

# 33. Recommended Alpha Default Stack

For a completely free local deployment, prefer this progression:

```text
                    ALPHA MEMORY FABRIC
                           │
             ┌─────────────┴─────────────┐
             │                           │
        Canonical store             Search indexes
             │                           │
          SQLite                  ┌──────┴───────┐
             │                    │              │
      FTS5 + JSON             lexical          semantic
             │                    │              │
             └──────────────┬─────┴──────────────┘
                            │
                         fusion
                            │
                      graph/temporal
                            │
                    context composer
```

### Default lightweight components

- **Canonical:** SQLite
- **Lexical:** SQLite FTS5
- **Vector:** FAISS or Qdrant depending on scale
- **Graph:** in-SQL relationship tables first; Apache AGE when graph queries become important
- **Embeddings:** local Ollama or local embedding runtime
- **LLM extraction:** local Ollama or any OpenAI-compatible local endpoint
- **Raw archive:** regular filesystem / project storage
- **Analytics:** DuckDB optional
- **API:** Alpha’s existing Python/TypeScript service layer

This avoids mandatory SaaS dependencies.

---

# 34. Optional External Projects to Integrate Rather Than Rebuild

Alpha should remain modular.

```text
IMemoryBackend
├── SQLiteMemoryBackend
├── PostgresMemoryBackend
├── QdrantVectorBackend
├── FAISSVectorBackend
├── GraphBackend
│   ├── SqlGraphBackend
│   └── ApacheAGEBackend
└── FilesystemArchiveBackend
```

### Framework adapters

```text
IMemoryAdapter
├── LettaAdapter
├── Mem0Adapter
├── MemOSAdapter
├── GraphitiAdapter
├── MemMachineAdapter
├── MemoriAdapter
├── CogneeAdapter
├── LangMemAdapter
├── SimpleMemAdapter
└── OpenVikingAdapter (license-reviewed deployment only)
```

This allows Alpha to learn from other projects without making any third-party system a hard dependency.

---

# 35. Commercial/Open-Source Licensing Strategy for Alpha

For an Alpha release intended for commercial use:

### Prefer

- MIT
- Apache-2.0
- BSD-2-Clause
- BSD-3-Clause
- PostgreSQL-style permissive licenses

### Review carefully

- AGPL/GPL when you want closed-source commercial deployment.
- SSPL/BSL/source-available licenses.
- Repo-specific dual-licensing.
- Components with different licenses inside one repository.
- Model/embedding licenses separately from software licenses.

### Important distinction

“Open source” does **not** automatically mean “all commercial rights without conditions.”

For example, OpenViking currently identifies its main project as AGPLv3, despite some components being Apache-2.0 or MIT, so it should not be treated the same as an Apache-2.0 memory library. citeturn380457search1turn380457search4

Likewise, a permissive software license does not automatically grant rights to every model, dataset, logo, hosted service or third-party dependency used alongside it.

---

# 36. Source / Research Index

## Memory frameworks / platforms

- Letta / MemGPT — https://github.com/letta-ai/letta
- Mem0 — https://github.com/mem0ai/mem0
- MemOS — https://github.com/MemTensor/MemOS
- Graphiti — https://github.com/getzep/graphiti
- MemMachine — https://github.com/MemMachine/MemMachine
- Memori — https://github.com/MemoriLabs/Memori
- Cognee — https://github.com/topoteretes/cognee
- LangMem — https://github.com/langchain-ai/langmem
- LlamaIndex — https://github.com/run-llama/llama_index
- Haystack — https://github.com/deepset-ai/haystack
- A-MEM — https://github.com/agiresearch/A-mem
- SimpleMem — https://github.com/aiming-lab/SimpleMem
- OpenClaw — https://github.com/openclaw/openclaw
- DeerFlow — https://github.com/bytedance/deer-flow
- OpenViking — https://github.com/volcengine/OpenViking

## Storage / retrieval

- Qdrant — https://github.com/qdrant/qdrant
- Milvus — https://github.com/milvus-io/milvus
- Weaviate — https://github.com/weaviate/weaviate
- FAISS — https://github.com/facebookresearch/faiss
- LanceDB — https://github.com/lancedb/lancedb
- pgvector — https://github.com/pgvector/pgvector
- Apache AGE — https://github.com/apache/age
- DuckDB — https://github.com/duckdb/duckdb

## Research / benchmarks

- MemGPT — https://arxiv.org/abs/2310.08560
- A-MEM — https://arxiv.org/abs/2502.12110
- EverMemOS — https://arxiv.org/abs/2601.02163
- LongMemEval — https://arxiv.org/abs/2410.10813

---

# 37. Final Architecture Recommendation for Alpha

If Alpha needs a single consolidated target, build this:

```text
                         ┌─────────────────────┐
                         │     ALPHA AGENT      │
                         └──────────┬──────────┘
                                    │
                             Memory Gateway
                                    │
                    ┌───────────────▼────────────────┐
                    │       MEMORY ORCHESTRATOR      │
                    │                                │
                    │ admission                     │
                    │ routing                        │
                    │ scoring                        │
                    │ scope                          │
                    │ lifecycle                      │
                    │ conflict resolution            │
                    │ consolidation scheduling       │
                    └─────┬───────────┬─────────┬────┘
                          │           │         │
              ┌───────────▼──┐ ┌────▼────┐ ┌──▼───────────┐
              │  EVENT STORE │ │ MEMORY  │ │ KNOWLEDGE    │
              │   raw logs   │ │ RECORDS │ │ GRAPH        │
              │   traces     │ │ SQL     │ │ entities     │
              │   checkpoints│ │ facts   │ │ relations    │
              └──────┬───────┘ └────┬────┘ │ temporal     │
                     │              │      └──────┬───────┘
                     │              │             │
                     │          ┌───▼─────────────▼───┐
                     │          │     INDEX LAYER      │
                     │          │ FTS + vector + graph │
                     │          └──────────┬───────────┘
                     │                     │
                     │               ┌─────▼──────┐
                     └──────────────►│  RETRIEVAL │
                                     │  + RERANK   │
                                     └─────┬───────┘
                                           │
                                    ┌──────▼──────┐
                                    │   CONTEXT   │
                                    │  COMPOSER   │
                                    └──────┬──────┘
                                           │
                                           ▼
                                         LLM

        BACKGROUND MEMORY WORKERS
        ─────────────────────────
        extract → dedupe → link → consolidate → reflect
                 → promote skills → decay → archive
                 → benchmark → tune retrieval policies
```

### Core principle

**Store once, represent many ways, retrieve intelligently, consolidate continuously, and preserve provenance.**

That principle allows Alpha to combine:

- Letta/MemGPT’s memory hierarchy,
- Mem0’s durable fact extraction,
- MemOS’s memory-OS orchestration,
- Graphiti’s temporal graph,
- A-MEM’s linked/evolving notes,
- SimpleMem’s compression,
- OpenClaw’s local SQLite + hybrid search,
- OpenViking’s resource/memory/skill separation,
- and DeerFlow’s long-horizon execution integration,

without making Alpha dependent on any one project. citeturn461845academia17turn309978search11turn199336view2turn429276view0turn199336view0turn199336view1turn397070view3turn397070view1turn654400search4

---

# 38. Immediate Implementation Checklist

```text
[ ] Define canonical MemoryRecord schema
[ ] Implement scope model
[ ] Implement SQLite source of truth
[ ] Add FTS5 lexical search
[ ] Add local embedding provider
[ ] Add vector index adapter
[ ] Implement hybrid retrieval
[ ] Implement memory admission scoring
[ ] Implement provenance
[ ] Implement timestamps + validity intervals
[ ] Implement entity extraction
[ ] Implement relation graph
[ ] Implement deduplication
[ ] Implement contradiction/supersession
[ ] Implement episodic memory
[ ] Implement semantic memory
[ ] Implement procedural memory
[ ] Implement profile/preference memory
[ ] Implement task/project memory
[ ] Implement failure/lesson memory
[ ] Implement skill/trajectory memory
[ ] Implement context composer
[ ] Implement background consolidation
[ ] Implement reflection
[ ] Implement forgetting/archiving
[ ] Implement explicit forget/delete
[ ] Implement memory health metrics
[ ] Implement memory evaluation suite
[ ] Add LoCoMo evaluation
[ ] Add LongMemEval-style evaluation
[ ] Add Alpha-specific benchmarks
[ ] Add optional Qdrant backend
[ ] Add optional Postgres/pgvector backend
[ ] Add optional graph backend
[ ] Add memory import/export
[ ] Add memory viewer/editor
[ ] Add agent-to-agent shared memory scopes
[ ] Connect memory feedback to Alpha RSI
```

---

# 39. Bottom Line

The next generation of agent memory is moving away from “chat history + vector database” toward a **memory operating system** that can:

```text
remember
understand
link
version
retrieve
verify
reflect
compress
forget
learn from outcomes
and reuse skills
```

For Alpha, the strongest fully self-hosted direction is therefore a **hybrid Memory Fabric** with:

**SQLite/PostgreSQL as source of truth + lexical search + local vectors + temporal/entity graph + episodic/semantic/procedural memory + provenance + consolidation + reflection + skill learning.**

The important architectural decision is to make every layer **replaceable**. Alpha should be able to run entirely with free local software today, while optionally adding Qdrant, PostgreSQL, graph backends, external memory frameworks, or hosted LLMs later without rewriting the agent itself.

---

## Appendix A — Current License-Safe Component Shortlist

For a permissive commercial-oriented Alpha baseline, the most straightforward code/library shortlist from the sources reviewed is:

### Core memory/reference implementations

- **Letta — Apache-2.0**
- **Mem0 — Apache-2.0**
- **MemOS — Apache-2.0**
- **Graphiti — Apache-2.0**
- **MemMachine — Apache-2.0**
- **Memori — Apache-2.0**
- **Cognee — Apache-2.0**
- **LangMem — MIT**
- **LlamaIndex — MIT**
- **A-MEM — MIT**
- **SimpleMem — MIT**

### Storage/search

- **FAISS — MIT**
- **Qdrant — Apache-2.0**
- **Milvus — Apache-2.0**
- **Weaviate — BSD-3-Clause**
- **pgvector — PostgreSQL-style permissive license**
- **Apache AGE — Apache-2.0**
- **DuckDB — MIT**

These are particularly attractive because their current repositories/pages explicitly identify permissive licenses. citeturn429276view2turn438199view2turn199336view2turn429276view0turn429276view1turn309978search1turn429640search4turn438199view1turn309978search2turn199336view0turn911690search15turn963291search0turn429640search5turn420543search6turn420543search7turn963291search1turn950249search1turn420543search1

## Appendix B — Practical Selection Guide

| Need | Start with | Why |
|---|---|---|
| simplest local memory | SQLite + FTS5 + local embeddings | zero external memory service |
| stateful autonomous agent | Letta concepts | strong tiered-memory model |
| fact extraction | Mem0 concepts | clean durable-fact pipeline |
| memory OS | MemOS concepts | orchestration + lifecycle |
| temporal relations | Graphiti | graph + time + provenance |
| evolving linked notes | A-MEM | self-organizing memory |
| efficient lifelong memory | SimpleMem | compression + multimodal direction |
| project/code knowledge | Cognee concepts | graph-oriented knowledge layer |
| agent framework integration | LangMem | pluggable memory primitives |
| local simple hybrid search inspiration | OpenClaw | SQLite + FTS/vector/hybrid |
| long-horizon task integration | DeerFlow | memory integrated with agent harness |
| resources + memory + skills model | OpenViking concepts | strong conceptual separation; review AGPL before reuse |

---

**End of report.**
