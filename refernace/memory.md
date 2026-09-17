Yes. I searched the current open-source agent-memory ecosystem, including projects active in 2026. The important point is that there is **no single project that is objectively “the memory system”**; different projects specialize in different memory problems. Current community maps include Mem0, Letta, Cognee, Memori, memU, Graphiti, LangMem, OpenMemory, MemMachine, Memary, A-MEM and newer local-first systems. ([GitHub][1])

For your **Hermes-based agent harness + multi-agent swarm + RSI**, this is the shortlist I would study and integrate.

## 1. Best projects by memory category

| Memory category                          | Projects to study/use                   | Main strength                                       | Direct fit for your harness |
| ---------------------------------------- | --------------------------------------- | --------------------------------------------------- | --------------------------- |
| **General long-term memory**             | **Mem0**, Cognee, Memori                | Persistent agent memory                             | ⭐⭐⭐⭐⭐                       |
| **Agent-native/stateful memory**         | **Letta**                               | Agent-controlled memory, identity, persistent state | ⭐⭐⭐⭐⭐                       |
| **Semantic/fact memory**                 | Mem0, Cognee, LangMem                   | Facts/preferences/knowledge                         | ⭐⭐⭐⭐⭐                       |
| **Episodic/experience memory**           | Letta, Memori, LangMem, A-MEM           | Past task experiences                               | ⭐⭐⭐⭐⭐                       |
| **Procedural memory**                    | LangMem, Letta, Memori                  | Learned procedures/rules/behavior                   | ⭐⭐⭐⭐⭐                       |
| **Self-improving memory**                | **LangMem**, Letta, A-MEM, Cognee       | Consolidation/adaptation                            | ⭐⭐⭐⭐⭐                       |
| **Temporal memory**                      | **Graphiti**, Cognee                    | What was true when                                  | ⭐⭐⭐⭐⭐                       |
| **Knowledge-graph memory**               | **Graphiti**, Cognee                    | Entities/relationships/multi-hop                    | ⭐⭐⭐⭐⭐                       |
| **User/profile memory**                  | **Memobase**, Mem0                      | Persistent user preferences/profile                 | ⭐⭐⭐⭐                        |
| **Conversation recall**                  | Letta, Mem0, Memori, LangMem            | Cross-session conversation recall                   | ⭐⭐⭐⭐⭐                       |
| **Agent execution/trace memory**         | **Memori**                              | Remembers what agents actually did                  | ⭐⭐⭐⭐⭐                       |
| **Cross-agent/shared memory**            | Cognee, Graphiti, SuperLocalMemory      | Shared organizational memory                        | ⭐⭐⭐⭐⭐                       |
| **Local/no-API-key memory**              | **Memory Fort**, mnem, SuperLocalMemory | Fully local memory                                  | ⭐⭐⭐⭐⭐                       |
| **Versioned memory**                     | **mnem**                                | Git-like memory history/branching                   | ⭐⭐⭐⭐⭐                       |
| **Hybrid lexical + vector memory**       | Memory Fort, SuperLocalMemory, mnem     | BM25 + semantic retrieval                           | ⭐⭐⭐⭐⭐                       |
| **Multimodal memory**                    | Cognee, memU                            | Files/images/code/etc.                              | ⭐⭐⭐⭐                        |
| **Memory governance/audit**              | SuperLocalMemory, Memory Fort           | Provenance, scopes, auditability                    | ⭐⭐⭐⭐                        |
| **Research/experimental agentic memory** | **A-MEM**                               | Dynamic memory organization                         | ⭐⭐⭐⭐                        |
| **Memory evaluation**                    | AgentMemBench, LongMemEval ecosystem    | Test memory quality                                 | ⭐⭐⭐⭐⭐                       |

---

# 2. Mem0 — general-purpose memory layer

**Repository:** `mem0ai/mem0`

Mem0 is one of the most directly usable projects for your harness. Its open-source version can run as a library or self-hosted server, and supports configurable LLM, embedding, vector store and reranking components. ([GitHub][2])

### Use it for

```text
User facts
Preferences
Semantic memories
Conversation memories
Agent-scoped memories
Project memories
Cross-session recall
```

### Why it matters

You can do something conceptually like:

```python
memory.add(...)
memory.search(...)
```

without replacing your whole agent runtime.

### Best role in your architecture

```text
Hermes
  ↓
Memory Controller
  ↓
Mem0 adapter
  ↓
semantic/user/project memory
```

**License:** Apache-2.0 according to the project. ([GitHub][2])

---

# 3. Letta — agent-native persistent memory

**Repository:** `letta-ai/letta`

This is probably the most important project for you to study conceptually because Letta was built around **stateful agents that retain memory, identity and behavior across interactions**. The current project is actively developed and Apache-2.0 licensed. ([GitHub][3])

Letta's architecture is especially useful for:

```text
Core memory
Recall memory
Archival memory
Agent identity
Self-managed memory
Persistent state
Agent learning
```

The project is explicitly positioned around agents that can learn and improve over time. ([GitHub][3])

### Best role

I would **borrow the architecture**, not necessarily make Letta your entire harness.

```text
Hermes Supervisor
       │
       ├── core memory
       ├── recall memory
       ├── archival memory
       └── agent state
```

This maps extremely well to your RSI design.

---

# 4. LangMem — memory + adaptation

**Repository:** `langchain-ai/langmem`

This one is especially interesting for your **RSI** work.

LangMem explicitly provides:

```text
memory extraction
memory search
memory management
memory consolidation
long-term memory
agent adaptation
prompt/behavior optimization
```

It supports both active “hot path” memory operations and background extraction/consolidation. ([GitHub][4])

The project itself describes the memory design questions around:

```text
facts / knowledge
past events
rules / style
when memories form
who forms them
where memories live
```

which fits your architecture extremely well. ([GitHub][5])

### For your RSI

This pattern is particularly useful:

```text
interaction
    ↓
memory extraction
    ↓
consolidation
    ↓
new knowledge
    ↓
behavior adaptation
```

**Very high relevance to your project.**

---

# 5. Cognee — knowledge + memory platform

**Repository:** `topoteretes/cognee`

Cognee is one of the most interesting choices for your **company-brain / project-brain / multi-agent memory**.

Its current architecture combines:

```text
session memory
graph memory
vector retrieval
knowledge extraction
code relationships
memory improvement
memory deletion
```

The project explicitly positions itself as an open-source AI memory platform for agents, with self-hosted persistent memory and knowledge graphs. ([GitHub][6])

It has a very useful lifecycle:

```text
remember
   ↓
cognify
   ↓
recall
   ↓
improve
   ↓
forget
```

and its retrieval can use graph, vector or other search strategies. ([GitHub][7])

### Excellent for

```text
Project knowledge
Codebase knowledge
Research knowledge
Company brain
Shared multi-agent memory
Documents
Long-term knowledge
Relationships
```

Cognee also has integrations/MCP surfaces for agents, which makes it much more directly usable than a purely academic memory implementation. ([GitHub][8])

---

# 6. Graphiti — temporal memory

**Repository:** `getzep/graphiti`

This is your strongest candidate for:

```text
Temporal memory
Knowledge graph
Entity relationships
Historical state
Multi-hop reasoning
Dynamic environments
```

Graphiti is specifically designed for temporally aware knowledge graphs for AI agents. The current ecosystem comparison describes it as combining graph-based and temporal context retrieval. ([GitHub][9])

### Example

Instead of:

```text
database = MySQL
```

you can represent:

```text
database = MySQL
valid_from = Jan
valid_to = Jun

database = PostgreSQL
valid_from = Jul
```

Then your agent can answer:

> “What database did this project use in May?”

This is incredibly useful for long-running projects.

---

# 7. Memori — execution/experience memory

**Repository:** `MemoriLabs/Memori`

Memori is particularly interesting for **agent execution memory**, because its design isn't limited to what the user and agent said.

Its current documentation describes capturing:

```text
tool calls
workflow steps
execution paths
decisions
outcomes
failures
constraints
recurring patterns
```

and it has an explicit Hermes integration/provider. ([GitHub][10])

That is almost exactly the kind of memory you described for your harness.

### Extremely useful concept

```text
Conversation memory
        +
Execution memory
        ↓
Experience memory
```

For an autonomous coding agent, this can be much more valuable than ordinary chat memory.

**Important:** Memori has a cloud/API-oriented integration path as well as open-source code. For your “zero-cost/no external API” requirement, treat the OSS repository as a component to inspect and verify the exact local deployment path rather than assuming every integration is free/local. The repository is Apache-2.0. ([GitHub][11])

---

# 8. Memobase — user profile memory

**Repository:** `memodb-io/memobase`

Memobase specializes in **user profile-based long-term memory**.

It focuses on:

```text
persistent user profiles
user preferences
personalization
timeline-like user memory
cross-session user understanding
```

The project is Apache-2.0 licensed. ([GitHub][12])

### Use it for

```text
USER_PROFILE
├── preferences
├── habits
├── technical preferences
├── recurring goals
├── communication style
└── long-term interests
```

I'd keep this separate from general project/agent memory.

---

# 9. A-MEM — dynamic agentic memory

**Repository:** `WujiangXu/A-mem`

This is more of a **research architecture** than a turnkey production dependency, but it is highly relevant to your RSI design.

The project describes itself as an agentic memory system that dynamically organizes memories in an agentic manner. ([GitHub][13])

The important concept is:

```text
new memory
    ↓
organize
    ↓
connect
    ↓
update memory structure
    ↓
form useful memory network
```

That's very relevant to your goal of having memory **evolve automatically**.

I would study A-MEM for algorithmic ideas, not simply install it as the entire memory backend.

---

# 10. Memory Fort — extremely interesting for your zero-cost requirement

**Repository:** `GalaxyRuler/memory-fort`

This is one of the projects I'd test immediately for your local-first goal.

Its current architecture uses:

```text
plain text
YAML
Git
knowledge graph
BM25
semantic retrieval
MCP
cross-tool memory
```

and it explicitly advertises **no database server and no API key required to get started**. ([GitHub][14])

It can work across tools including coding agents and Hermes, according to its current README. ([GitHub][14])

### Why I like the concept

Memory becomes:

```text
~/.memory/
```

rather than an opaque proprietary database.

That means:

```text
git diff
git log
backup
restore
manual editing
human inspection
```

come naturally.

**License:** GPL-3.0. ([GitHub][14])

That license matters if you intend to distribute your harness commercially, so examine compatibility before embedding it directly.

---

# 11. mnem — version-controlled agent memory

**Repository:** `Uranid/mnem`

This is a particularly interesting project for **RSI/evolution**.

It describes itself as:

> Git for AI Agent Knowledge

and provides:

```text
persistent knowledge
versioned memory
hybrid GraphRAG
branches
diff
merge
content-addressed storage
offline operation
MCP
```

The repository says it can run entirely offline and has no-LLM ingestion options. ([GitHub][15])

### This gives you something very valuable:

```text
Memory v1
   ↓
Agent learns
   ↓
Memory v2
   ↓
Experiment
   ↓
Memory branch
   ↓
Evaluate
   ↓
Merge
```

That is extremely compatible with your **RSI memory-evolution architecture**.

**License:** Apache-2.0. ([GitHub][15])

---

# 12. SuperLocalMemory — local memory control plane

**Repository:** `qualixar/superlocalmemory`

This is another strong project to study for a **local/no-API memory controller**.

Its current architecture describes:

```text
SQLite
semantic retrieval
BM25
temporal retrieval
associative retrieval
graph enhancement
memory scopes
compression
audit
MCP
multi-agent sharing
```

and its default local mode does not require Docker, an external graph DB or an API key. ([GitHub][16])

It also supports:

```text
personal
shared
global
```

memory scopes. ([GitHub][16])

That's very relevant to your swarm architecture.

**License:** AGPL-3.0. ([GitHub][16])

Again, inspect licensing carefully before incorporating source code into a distributed/commercial harness.

---

# 13. memU — proactive/hierarchical memory

This is another project worth testing for your proactive-agent architecture.

It appears in current open-source agent-memory catalogs as a memory harness oriented toward proactive agents and multimodal memory. ([GitHub][17])

Conceptually, it is useful for:

```text
hierarchical memory
multimodal memories
proactive recall
memory organization
```

I would classify it as **experimental/secondary**, not as the first dependency I'd put into your core runtime.

---

# 14. Memori / Mem0 / Cognee / Letta / LangMem comparison

| Project              |  Semantic |       Episodic |       Procedural |             Graph |          Temporal | Execution traces | Self-improvement |             Local/self-host |
| -------------------- | --------: | -------------: | ---------------: | ----------------: | ----------------: | ---------------: | ---------------: | --------------------------: |
| **Mem0**             |         ✅ | ✅/configurable |          Partial |                 ✅ |           Partial |          Partial |                ✅ |                           ✅ |
| **Letta**            |         ✅ |              ✅ |                ✅ | Archival-oriented |           Partial |                ✅ |                ✅ |                           ✅ |
| **LangMem**          |         ✅ |              ✅ |                ✅ | Backend-dependent | Backend-dependent |          Partial |               ✅✅ |                           ✅ |
| **Cognee**           |         ✅ |              ✅ |      ✅/knowledge |                ✅✅ |                 ✅ |                ✅ |                ✅ |                           ✅ |
| **Graphiti**         |         ✅ |              ✅ |                — |                ✅✅ |                ✅✅ |                — |                — |                           ✅ |
| **Memori**           |         ✅ |              ✅ |                ✅ |        Structured |      ✅/contextual |               ✅✅ |                ✅ | OSS/self-host possibilities |
| **Memobase**         | ✅ profile |       Timeline |                — |                 — |          Timeline |                — |                — |                           ✅ |
| **A-MEM**            |         ✅ |             ✅✅ |                — |                 ✅ |          Research |                — |               ✅✅ |                           ✅ |
| **Memory Fort**      |         ✅ |              ✅ | Notes/procedures |                 ✅ |                 ✅ |                ✅ |          Partial |                          ✅✅ |
| **mnem**             |         ✅ |              ✅ |        Knowledge |                ✅✅ |   Version history |                ✅ |                ✅ |                          ✅✅ |
| **SuperLocalMemory** |         ✅ |              ✅ |                ✅ |                 ✅ |                 ✅ |                ✅ |                ✅ |                          ✅✅ |

These are architectural comparisons from the projects' current public descriptions; they should not be interpreted as a standardized benchmark ranking. The ecosystem itself is actively evolving, and AgentMemBench is now explicitly working on dimensions such as retrieval quality, temporal consistency, isolation, scalability and LLM portability. ([GitHub][18])

---

# 15. For your harness, don't install all of them

This is important.

I **would not** build:

```text
Mem0
+
Letta
+
Cognee
+
Graphiti
+
Memori
+
Memobase
+
SuperLocalMemory
```

all inside your production runtime.

You'll create:

```text
duplicate memories
different schemas
conflicting retrieval
higher RAM use
multiple embeddings
multiple databases
difficult debugging
```

Instead create **one memory abstraction**:

```python
class MemoryProvider:
    async def remember(...)
    async def recall(...)
    async def update(...)
    async def forget(...)
    async def consolidate(...)
    async def reflect(...)
    async def search(...)
```

Then implement adapters:

```text
providers/
├── mem0.py
├── cognee.py
├── graphiti.py
├── letta.py
├── langmem.py
├── memori.py
├── mnem.py
├── superlocalmemory.py
└── local_native.py
```

That gives your harness freedom to swap providers.

---

# 16. My recommended architecture for your exact project

I'd divide it like this:

```text
                    HERMES SUPERVISOR
                           │
                    MEMORY ORCHESTRATOR
                           │
       ┌───────────────────┼────────────────────┐
       │                   │                    │
       ▼                   ▼                    ▼
   HOT STATE          EXPERIENCE MEMORY    KNOWLEDGE MEMORY
       │                   │                    │
   SQLite              Memori/LangMem        Cognee
   Session             Episodes              Documents
   Current task        Outcomes              Code
   Scratch             Failures              Research
       │                   │                    │
       └───────────────────┼────────────────────┘
                           │
                           ▼
                    SEMANTIC MEMORY
                           │
                         Mem0
                           │
                           ▼
                    TEMPORAL MEMORY
                           │
                        Graphiti
                           │
                           ▼
                    USER MEMORY
                           │
                       Memobase
                           │
                           ▼
                VERSIONED MEMORY / RSI
                           │
                        mnem
                           │
                           ▼
                    MEMORY EVALUATOR
                           │
                    AgentMemBench
```

But I would make **three modes**.

### Mode 1 — lightweight

```text
SQLite
+
FTS5
+
local embeddings
```

Perfect for your laptop.

### Mode 2 — advanced

```text
PostgreSQL
+
pgvector
+
Mem0
+
Cognee/Graphiti
```

### Mode 3 — RSI laboratory

```text
mnem
+
A-MEM concepts
+
LangMem
+
Graphiti
+
evaluation
```

---

# 17. What I would personally prototype first

For your project, my first experimental stack would be:

```text
                   MEMORY API
                       │
             ┌─────────┴─────────┐
             │                   │
         SQLite             PostgreSQL
       hot/recall          long-term facts
             │              + pgvector
             │                   │
             └────────┬──────────┘
                      │
                 Cognee/Graphiti
                      │
                  knowledge graph
                      │
               LangMem controller
                      │
             consolidation/reflection
                      │
                  mnem layer
                      │
              versioned memory/RSI
```

And test **Mem0 separately as an alternative provider** rather than making it inseparable from the core.

---

# 18. The 10 projects I would put on your research/install list

In this order for your particular architecture:

**1. Letta** — study stateful/agent-managed memory. ([GitHub][3])

**2. Mem0** — practical general-purpose memory API. ([GitHub][2])

**3. Cognee** — shared knowledge + graph + vector memory. ([GitHub][6])

**4. Graphiti** — temporal graph memory. ([GitHub][9])

**5. LangMem** — memory formation/consolidation/adaptation. ([GitHub][4])

**6. Memori** — execution/trace/outcome memory and Hermes integration. ([GitHub][10])

**7. mnem** — versioned, offline, Git-like memory. ([GitHub][15])

**8. Memory Fort** — zero-key, local, cross-tool memory. ([GitHub][14])

**9. Memobase** — user-profile memory. ([GitHub][12])

**10. A-MEM** — research ideas for dynamically reorganizing memory. ([GitHub][13])

---

## One particularly important discovery for your Hermes project

There is now a **direct Hermes memory integration from Memori** documented publicly: it provides a Hermes memory provider and tools such as `memori_recall` and `memori_recall_summary`, with memory formed from conversation, agent traces and execution context. ([GitHub][10])

So your harness should not only study generic RAG memory. **Agent-execution memory is a major architectural dimension for the RSI system you're building.**

The other especially interesting combination is **mnem + LangMem + Graphiti**:

```text
LangMem
   ↓
learn / consolidate
   ↓
Graphiti
   ↓
relationships + temporal truth
   ↓
mnem
   ↓
versioned memory state
   ↓
RSI evaluation
   ↓
promote / rollback
```

That gives you a very strong foundation for the **self-evolving memory layer** you have been designing.

[1]: https://github.com/fomos-openai/awesome-ai-memory?utm_source=chatgpt.com "GitHub - fomos-openai/awesome-ai-memory: A curated collection of Agent Memory research papers and projects released in 2026. · GitHub"
[2]: https://github.com/mem0ai/mem0/blob/main/docs/open-source/overview.mdx?utm_source=chatgpt.com "mem0/docs/open-source/overview.mdx at main · mem0ai/mem0 · GitHub"
[3]: https://github.com/letta-ai/letta?utm_source=chatgpt.com "GitHub - letta-ai/letta: Platform for stateful agents: AI with advanced memory that can learn and self-improve over time. · GitHub"
[4]: https://github.com/langchain-ai/langmem?utm_source=chatgpt.com "GitHub - langchain-ai/langmem · GitHub"
[5]: https://github.com/langchain-ai/langmem/blob/main/docs/docs/concepts/conceptual_guide.md?utm_source=chatgpt.com "langmem/docs/docs/concepts/conceptual_guide.md at main · langchain-ai/langmem · GitHub"
[6]: https://github.com/topoteretes/cognee?utm_source=chatgpt.com "GitHub - topoteretes/cognee: Cognee is the open-source AI memory platform for agents. Give your AI agents persistent long-term memory across sessions with a self-hosted knowledge graph engine. · GitHub"
[7]: https://github.com/topoteretes/cognee/blob/main/README.md?utm_source=chatgpt.com "cognee/README.md at main · topoteretes/cognee · GitHub"
[8]: https://github.com/topoteretes/cognee-integrations?utm_source=chatgpt.com "GitHub - topoteretes/cognee-integrations · GitHub"
[9]: https://github.com/DEEP-PolyU/Awesome-GraphMemory?utm_source=chatgpt.com "GitHub - DEEP-PolyU/Awesome-GraphMemory: A survey of Graph-based Agent Memory | A curated list of resources (surveys, papers, benchmarks, and opensource projects) on graph-based agent memory. · GitHub"
[10]: https://github.com/MemoriLabs/Memori/blob/main/docs/memori-cloud/hermes/overview.mdx?utm_source=chatgpt.com "Memori/docs/memori-cloud/hermes/overview.mdx at main · MemoriLabs/Memori · GitHub"
[11]: https://github.com/MemoriLabs/Memori?utm_source=chatgpt.com "GitHub - MemoriLabs/Memori: Memori is agent-native memory infrastructure. A LLM-agnostic layer that turns agent execution and conversation into structured, persistent state for production systems. Built for enterprise, Memori works with the data infrastructure you already run, no rip-and-replace, and deploys across managed cloud, single-tenant cloud, VPC, and on-premises. · GitHub"
[12]: https://github.com/memodb-io/memobase?utm_source=chatgpt.com "GitHub - memodb-io/memobase: User Profile-Based Long-Term Memory for AI Chatbot Applications. · GitHub"
[13]: https://github.com/WujiangXu/A-mem?utm_source=chatgpt.com "GitHub - WujiangXu/A-mem: The code for NeurIPS 2025 paper \"A-Mem: Agentic Memory for LLM Agents\" · GitHub"
[14]: https://github.com/GalaxyRuler/memory-fort?utm_source=chatgpt.com "GitHub - GalaxyRuler/memory-fort: Cross-tool persistent memory for AI agents — local, private, no API key required · GitHub"
[15]: https://github.com/Uranid/mnem?utm_source=chatgpt.com "GitHub - Uranid/mnem: Git for AI Agent Knowledge. A persistent, versioned memory layer for AI systems. Hybrid GraphRAG retrieval. Runs entirely offline. · GitHub"
[16]: https://github.com/qualixar/superlocalmemory?utm_source=chatgpt.com "GitHub - qualixar/superlocalmemory: Open-source governed, local-first memory control plane for AI agents and teams. arXiv:2608.08253 · GitHub"
[17]: https://github.com/Anandesh-Sharma/awesome-agentic-memory?utm_source=chatgpt.com "GitHub - Anandesh-Sharma/awesome-agentic-memory: 🧠 The definitive curated map of memory for LLM agents — frameworks, research papers, benchmarks, taxonomy & deep dives. · GitHub"
[18]: https://github.com/mazaiying/AgentMemBench?utm_source=chatgpt.com "GitHub - mazaiying/AgentMemBench: AgentMemBench: A taxonomy-driven benchmark for agent memory systems · GitHub"









Yes. For your harness, I would treat **persistent long-term memory as a full subsystem**, not as “add a vector DB.” The current open-source agent ecosystem strongly points toward a layered design: always-visible core state, searchable conversation history, semantic facts, episodic experiences, and procedural knowledge. ([github.com][1])

Also, “completely free” should mean **no mandatory SaaS/API subscription**. You can keep the entire memory stack local/self-hosted, although the machine, disk and electricity are still costs.

## 1. Complete memory taxonomy for your AI harness

I recommend these **18 memory categories**:

| #  | Memory type                      | What it remembers                     | Example for your harness                                   | Recommended OSS/free implementation      |
| -- | -------------------------------- | ------------------------------------- | ---------------------------------------------------------- | ---------------------------------------- |
| 1  | **Working memory**               | What is needed right now              | Current user request, active subgoals, current tool result | In-process Python/JSON + context manager |
| 2  | **Conversation / recall memory** | Full historical interactions          | “What did we discuss 3 weeks ago?”                         | SQLite + FTS5                            |
| 3  | **Semantic memory**              | Stable facts/knowledge                | “Project uses FastAPI + MySQL”                             | Qdrant / pgvector                        |
| 4  | **Episodic memory**              | Experiences/events                    | “Last deployment failed because migration X broke”         | Qdrant/pgvector + structured event DB    |
| 5  | **Procedural memory**            | How to perform tasks                  | “Deploy this project using these steps”                    | Git-backed Markdown/YAML skills          |
| 6  | **User/profile memory**          | Persistent user facts/preferences     | Preferred style, coding preferences                        | Memobase / custom PostgreSQL             |
| 7  | **Preference memory**            | Likes/dislikes/behavioral preferences | “Prefer open-source/local solutions”                       | PostgreSQL/SQLite profile tables         |
| 8  | **Project memory**               | Long-lived project state              | Architecture, stack, roadmap, constraints                  | Git + SQLite/Postgres + vector index     |
| 9  | **Task memory**                  | Active and completed work             | Goals, subtasks, blockers, attempts                        | SQLite/Postgres                          |
| 10 | **Temporal memory**              | What was true when                    | Old architecture vs current architecture                   | PostgreSQL timestamps + Graphiti         |
| 11 | **Entity/relationship memory**   | Connections between things            | User → project → repo → service → issue                    | Graphiti + Neo4j/FalkorDB                |
| 12 | **Knowledge-base memory**        | Documents/web/research                | Papers, docs, manuals, repositories                        | LanceDB/Qdrant + filesystem              |
| 13 | **Experience/reflection memory** | Lessons learned                       | “This approach caused repeated failures”                   | Structured SQLite + vector retrieval     |
| 14 | **Decision memory**              | Why decisions were made               | “Chose PostgreSQL because…”                                | SQLite/Postgres + Markdown               |
| 15 | **Error/failure memory**         | Errors and recovery patterns          | Stack traces + successful fixes                            | SQLite + vector DB                       |
| 16 | **Tool memory**                  | Tool capabilities/history             | API/tool parameters, successful tool patterns              | SQLite/JSON + procedural memory          |
| 17 | **Agent/self memory**            | Agent identity and operating state    | Role, capabilities, limitations, learned policies          | Core memory blocks + Git                 |
| 18 | **Multi-agent/shared memory**    | Shared organizational knowledge       | Agent A's discovery available to Agent B                   | Shared Postgres/Qdrant/Graph DB          |

LangMem explicitly describes semantic, episodic and procedural memory as major long-term categories, while Letta distinguishes always-in-context core memory from archival memory and searchable conversation history. ([github.com][1])

---

# 2. The architecture I recommend for YOUR harness

Don't do this:

```text
Agent
  ↓
Vector DB
  ↓
"Memory"
```

Build this:

```text
                    ┌──────────────────────────┐
                    │       HERMES CORE        │
                    │ Supervisor / Orchestrator│
                    └────────────┬─────────────┘
                                 │
                     ┌───────────▼───────────┐
                     │   MEMORY CONTROLLER   │
                     │ remember / recall /   │
                     │ update / forget /     │
                     │ consolidate / reflect │
                     └───────────┬───────────┘
                                 │
       ┌─────────────────────────┼─────────────────────────┐
       │                         │                         │
       ▼                         ▼                         ▼
┌───────────────┐       ┌────────────────┐       ┌─────────────────┐
│ FAST MEMORY   │       │ LONG-TERM      │       │ KNOWLEDGE       │
│               │       │ MEMORY         │       │ MEMORY          │
│ Working       │       │ Semantic       │       │ Documents       │
│ Core state    │       │ Episodic       │       │ Web research    │
│ Current task  │       │ Procedural     │       │ Code/repositories│
│ Scratchpad    │       │ User profile   │       │ PDFs            │
└───────┬───────┘       └────────┬───────┘       └────────┬────────┘
        │                         │                         │
        └─────────────────────────┼─────────────────────────┘
                                  ▼
                      ┌────────────────────────┐
                      │   HYBRID RETRIEVAL     │
                      │ Vector + BM25 + Graph  │
                      │ metadata + time +      │
                      │ importance + recency   │
                      └───────────┬────────────┘
                                  ▼
                        Relevant memory set
                                  │
                                  ▼
                         Context assembler
                                  │
                                  ▼
                             LLM / Agent
```

This is much closer to the direction taken by modern agent-memory systems than simply embedding every conversation. Letta's architecture separates core memory, archival memory and conversation recall, while Graphiti adds temporal knowledge graphs and hybrid semantic/keyword/graph retrieval. ([GitHub][1])

---

# 3. Your persistent memory should have 5 major layers

### Layer A — Instant memory

These should be immediately available to the agent:

```text
agent_identity
current_user
current_goal
current_plan
active_subgoals
current_project
current_constraints
current_decisions
current_environment
current_session
```

Use:

**SQLite / JSON / in-process state**

This is similar to Letta's core-memory concept, where important state is kept persistently available to the model. ([GitHub][1])

---

### Layer B — Recall memory

Store the complete interaction stream:

```text
session
  ├── user_message
  ├── assistant_message
  ├── tool_call
  ├── tool_result
  ├── observation
  ├── decision
  └── event
```

Use:

**SQLite + FTS5**

SQLite FTS5 provides local full-text search and ranking, making it an excellent zero-service dependency for historical recall. ([SQLite][2])

This gives you:

```text
search("what happened with deployment?")
search("Alembic migration")
search(last_30_days)
search(project="harness")
```

---

# 4. Semantic memory

This contains stable knowledge rather than entire conversations.

Example:

```json
{
  "subject": "Harness",
  "predicate": "uses",
  "object": "Hermes",
  "confidence": 0.98,
  "source": "conversation",
  "created_at": "...",
  "updated_at": "..."
}
```

Examples:

```text
User prefers local/open-source software
Harness uses MySQL
Project uses MCP
Repository contains X
Agent supports Y
```

Recommended stores:

### Qdrant

Very good dedicated vector-search layer.

### pgvector

Especially attractive if you're already using PostgreSQL. It gives vector search directly inside PostgreSQL and supports exact and approximate nearest-neighbor search, filtering, HNSW and other capabilities. ([GitHub][3])

### LanceDB

Interesting for a local-first architecture because it is embedded, open-source, supports vector/full-text search and multimodal data, and does not require a separate vector server. ([GitHub][4])

---

# 5. Episodic memory

This is **extremely important for your RSI/self-improving harness**.

Don't only remember:

> “Python code failed.”

Remember:

```text
EPISODE
────────────────────────
Goal:
Deploy FastAPI service

Attempt:
Used migration command X

Observation:
Migration failed

Error:
...

Action:
Changed configuration

Result:
Deployment succeeded

Lesson:
Run migration validation before deployment

Environment:
Windows + MySQL

Confidence:
0.91
```

Your agent can then retrieve similar experiences before attempting a task.

This is where your harness can become substantially more capable than a basic “memory plugin.”

---

# 6. Procedural memory

This stores **how the agent does things**.

Example:

```text
procedure:
  task: release_python_package

  steps:
    1. inspect repository
    2. run tests
    3. build package
    4. inspect metadata
    5. run installation test
    6. publish
    7. verify package
```

Store these as:

```text
skills/
procedures/
workflows/
playbooks/
```

Prefer **Git-backed Markdown/YAML** rather than putting all procedural knowledge into vectors.

Letta's current architecture explicitly treats skills/external files as persistent procedural knowledge, and its MemFS model makes memory changes Git-backed and persistent. ([GitHub][5])

---

# 7. Temporal memory

Your system needs to know:

```text
FACT A
valid: Jan → Mar

FACT B
valid: Apr → Jun

FACT C
valid: Jul → present
```

Instead of overwriting:

```text
Python 3.11
```

store:

```text
Python version = 3.11
valid_from = ...
valid_to = ...
source = ...
```

Then queries can become:

```text
"What was the architecture in May?"

"What is the architecture now?"
```

Graphiti is particularly relevant here because its architecture supports temporal knowledge graphs, incremental updates and explicit historical timing, as well as hybrid semantic/keyword/graph retrieval. ([GitHub][6])

---

# 8. Knowledge graph memory

Vector retrieval answers approximately:

> “What memories are similar to this question?”

A graph can answer:

> “How are these things related?”

Example:

```text
USER
 │
 ├── owns → PROJECT
 │             │
 │             ├── uses → HERMES
 │             ├── contains → REPOSITORY
 │             └── depends_on → MCP
 │
 └── prefers → LOCAL MODELS
```

For your harness this is valuable for:

```text
project relationships
agent relationships
tool dependencies
task dependencies
people/organizations
code architecture
research knowledge
causal relationships
```

### Recommended

**Graphiti + Neo4j Community**

Graphiti supports Neo4j and FalkorDB and can use local models via Ollama; Neo4j Community is free for local development/testing and smaller workloads. ([GitHub][6])

You can also consider FalkorDB, although its server is SSPL-licensed rather than a permissive license, so I would distinguish “free/self-hostable” from “permissively open-source” when choosing it. ([GitHub][7])

---

# 9. User memory

Make a dedicated profile rather than relying exclusively on semantic search.

```text
USER_PROFILE
├── identity
├── preferences
├── communication
├── technical_preferences
├── recurring_tasks
├── projects
├── goals
└── constraints
```

**Memobase** is particularly interesting for this specific layer. It is an open-source long-term user-memory system centered on profiles plus event timelines and uses PostgreSQL/Redis in its server architecture. ([GitHub][8])

---

# 10. Reflection / learning memory

For your RSI architecture, add:

```text
OBSERVATION
     ↓
EPISODE
     ↓
REFLECTION
     ↓
LESSON
     ↓
POLICY / PROCEDURE
     ↓
FUTURE BEHAVIOR
```

Example:

```text
Observation:
Deployment failed.

Reflection:
Agent skipped environment verification.

Lesson:
Always verify environment before deployment.

Procedure update:
Add environment verification stage.

Verification:
Next 5 deployments succeeded.
```

This is much more useful than simply storing the error message.

There are already experimental open-source systems specifically exploring agentic memory that dynamically reorganizes memories rather than only accumulating them. A-MEM is one such research implementation. ([GitHub][9])

---

# 11. Decision memory

I strongly recommend this for your supervisor agent.

Store:

```text
decision_id
question
options_considered
selected_action
reason
evidence
confidence
agent
timestamp
reversible
outcome
```

Example:

```text
Decision:
Use Qdrant instead of custom ANN.

Reason:
Lower implementation complexity.

Evidence:
Benchmark + project requirements.

Outcome:
Retrieval latency acceptable.

Status:
Validated
```

This prevents the agent from repeatedly reconsidering decisions it already made.

---

# 12. Failure memory

Make failures first-class objects.

```text
FAILURE
├── task
├── environment
├── command
├── error
├── root_cause
├── attempted_fixes
├── successful_fix
├── verification
└── recurrence_count
```

Then before execution:

```text
retrieve_similar_failures()
```

This gives your harness:

**failure avoidance memory**

rather than passive historical storage.

---

# 13. Tool memory

The agent should remember:

```text
tool
 ├── capability
 ├── input schema
 ├── examples
 ├── limitations
 ├── common errors
 ├── successful patterns
 └── failed patterns
```

Example:

```text
github.create_pr

Known issue:
Fails when branch doesn't exist.

Learned procedure:
Check branch before PR creation.
```

This becomes particularly powerful as the number of tools and MCP servers grows.

---

# 14. Self/agent memory

Your top-level Hermes agent should maintain its own state:

```text
AGENT_SELF
├── identity
├── mission
├── capabilities
├── unavailable_capabilities
├── tools
├── skills
├── learned_rules
├── known_limitations
├── current_state
├── confidence
└── evolution_history
```

This is the foundation for your RSI architecture.

---

# 15. Multi-agent shared memory

For your planned swarm architecture:

```text
              SHARED MEMORY
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
   Research      Coding       Testing
    Agent         Agent        Agent
       │            │            │
       └────────────┼────────────┘
                    ▼
               Supervisor
```

Shared memory should contain:

```text
shared_facts
shared_episodes
shared_decisions
shared_lessons
shared_artifacts
shared_project_state
```

But **private agent scratch memory should remain private** unless explicitly promoted.

---

# 16. The most important design principle: memory ≠ database

Your memory subsystem should contain:

```text
Memory Object
│
├── content
├── memory_type
├── source
├── owner
├── scope
├── timestamp
├── valid_from
├── valid_to
├── importance
├── confidence
├── reliability
├── access_count
├── last_accessed
├── decay_score
├── embedding
├── entities
├── relationships
├── parent_memory
├── child_memories
├── supporting_evidence
├── contradictory_evidence
└── provenance
```

That metadata is what lets you build **actual memory management**, rather than merely RAG.

---

# 17. Hybrid retrieval is essential

Do not use only:

```text
vector similarity
```

Use:

```text
FinalScore =
    semantic_similarity
  + keyword_score
  + recency
  + importance
  + confidence
  + entity_match
  + relationship_score
  + task_relevance
  + access_frequency
  - contradiction_penalty
  - stale_penalty
```

Then:

```text
Query
 ↓
Query understanding
 ↓
 ┌──────────────┬──────────────┬──────────────┬───────────────┐
 │ Vector       │ BM25/FTS     │ Graph        │ Metadata      │
 │ search       │ search       │ traversal    │ filtering     │
 └──────────────┴──────────────┴──────────────┴───────────────┘
                         ↓
                    candidate pool
                         ↓
                       rerank
                         ↓
                memory consolidation
                         ↓
                 context selection
```

Mem0's current retrieval architecture, for example, uses embeddings, filtering and optional reranking; Graphiti adds semantic, keyword and graph retrieval. ([GitHub][10])

---

# 18. Memory lifecycle

Every memory should go through:

```text
CAPTURE
   ↓
CLASSIFY
   ↓
NORMALIZE
   ↓
DEDUPLICATE
   ↓
SCORE
   ↓
STORE
   ↓
INDEX
   ↓
RETRIEVE
   ↓
USE
   ↓
UPDATE
   ↓
CONSOLIDATE
   ↓
DECAY
   ↓
ARCHIVE
   ↓
FORGET
```

This is particularly important for your RSI goal.

---

# 19. Memory consolidation

At the end of a task:

```text
conversation
     ↓
important events
     ↓
facts
     ↓
episodes
     ↓
lessons
     ↓
decisions
     ↓
procedure improvements
```

For example:

```text
100 messages
      ↓
15 events
      ↓
6 useful facts
      ↓
3 episodes
      ↓
2 lessons
      ↓
1 procedural improvement
```

That prevents your memory database from becoming a giant dump.

---

# 20. Memory decay

Not every memory should remain equally important forever.

Use:

```text
importance
recency
confidence
frequency
utility
```

Example:

```text
recent + frequently used + verified
       ↓
high retrieval priority

old + never used + low confidence
       ↓
low retrieval priority

expired
       ↓
archive/delete
```

But **never physically delete important memories just because they're old** unless policy permits it. Archive them and retain provenance.

---

# 21. Contradiction handling

This is critical.

Suppose memory says:

```text
Python = 3.11
```

Later:

```text
Python = 3.12
```

Don't blindly overwrite.

Create:

```text
Memory A
Python 3.11
valid_to = ...

Memory B
Python 3.12
valid_from = ...
```

Also keep:

```text
source
confidence
verification
```

A later verified source can supersede an earlier memory.

---

# 22. Free/open-source stack I would use

### Core stack

| Function                   | Recommended                                                |
| -------------------------- | ---------------------------------------------------------- |
| Primary structured DB      | **PostgreSQL**                                             |
| Vector search              | **pgvector**                                               |
| Full-text                  | **PostgreSQL FTS / SQLite FTS5**                           |
| Local lightweight DB       | **SQLite**                                                 |
| Graph                      | **Neo4j Community + Graphiti**                             |
| Local embeddings           | **Ollama embeddings**                                      |
| Local inference            | **Ollama / llama.cpp**                                     |
| Long-term memory framework | **custom memory controller + selected Mem0/LangMem ideas** |
| User profile memory        | **Memobase ideas / implementation**                        |
| Procedural memory          | **Git + Markdown/YAML skills**                             |
| Documents/artifacts        | **local filesystem / Git / object storage**                |
| Analytics                  | **DuckDB**                                                 |
| Retrieval testing          | **LoCoMo-style evaluation + your own task traces**         |

Postgres + pgvector is particularly attractive because it lets you keep structured memory, metadata, relationships and embeddings together, while pgvector supports exact/approximate search and HNSW. ([GitHub][3])

DuckDB is also free/open-source and MIT-licensed, making it useful for offline memory analytics and evaluation. ([DuckDB][11])

---

# 23. Best lightweight version for your current machine

Since you're targeting a resource-constrained/free architecture, I would **not start with 8 different databases**.

Start with:

```text
                    YOUR HARNESS
                         │
                 Memory Controller
                         │
          ┌──────────────┼───────────────┐
          ▼              ▼               ▼
       SQLite         PostgreSQL      Git Files
          │              │               │
       history       pgvector        procedures
       FTS5          semantic        skills
       tasks         episodic        policies
       decisions     profiles        lessons
                         │
                         ▼
                    Graphiti
                         │
                  Neo4j Community
```

And:

```text
Ollama
 ├── embedding model
 └── local LLM
```

Ollama currently exposes local embedding generation and lists models such as `embeddinggemma`, `qwen3-embedding` and `all-minilm`. ([GitHub][12])

This keeps the complete system self-hosted and avoids mandatory API calls.

---

# 24. Where Mem0 fits

Mem0 is worth studying and potentially integrating as **one memory component**, but don't design your architecture around the assumption that its named semantic/episodic/procedural categories are all implemented. Its current OSS documentation explicitly says `procedural_memory` is implemented while semantic and episodic `memory_type` pathways are not currently implemented. ([GitHub][13])

So for your harness:

```text
Your Memory Controller
       │
       ├── custom semantic memory
       ├── custom episodic memory
       ├── custom procedural memory
       ├── Mem0-compatible layer
       └── Graph memory
```

rather than:

```text
Everything → Mem0
```

---

# 25. Projects worth studying

These are particularly relevant to your design:

**Letta / MemGPT** — core memory + archival memory + conversation recall + agent-editable memory. ([GitHub][1])

**LangMem** — explicit semantic/episodic/procedural long-term memory model and memory-management patterns. ([GitHub][14])

**Mem0** — practical persistent memory layer with self-hosting, scoping, metadata, expiration and retrieval/reranking. ([GitHub][15])

**Graphiti** — temporal knowledge graph + hybrid graph/semantic/keyword retrieval for changing agent knowledge. ([GitHub][6])

**Memobase** — profile + timeline-oriented long-term user memory. ([GitHub][8])

**A-MEM** — research direction for agentic memory that dynamically organizes memory rather than merely accumulating records. ([GitHub][9])

**OpenMemory projects** — useful to inspect for alternative hierarchical/cognitive-memory designs, although I would treat these as experimental references rather than foundational dependencies. ([GitHub][16])

---

# 26. The final memory architecture I recommend

For your **Hermes + supervisor + RSI + multi-agent harness**, I would make this the target:

```text
                    ┌──────────────────────┐
                    │   HERMES SUPERVISOR  │
                    └──────────┬───────────┘
                               │
                     ┌─────────▼─────────┐
                     │  MEMORY CONTROLLER│
                     └─────────┬─────────┘
                               │
       ┌───────────────────────┼────────────────────────┐
       │                       │                        │
       ▼                       ▼                        ▼
  HOT MEMORY              LONG MEMORY              KNOWLEDGE
       │                       │                        │
 working state             semantic                  documents
 active task               episodic                 code
 current plan              procedural                research
 session                   user/profile              web
 scratchpad                decision                  manuals
       │                    failure
       │                    reflection
       │                    temporal
       │
       └───────────────────────┐
                               ▼
                       RETRIEVAL ENGINE
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
           Vector             BM25             Graph
              │                │                │
              └────────────────┼────────────────┘
                               ▼
                            Reranker
                               │
                               ▼
                        Context Composer
                               │
                               ▼
                              AGENT
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
           outcome          lessons           failures
              │                │                │
              └────────────────┼────────────────┘
                               ▼
                         CONSOLIDATION
                               │
                               ▼
                          MEMORY UPDATE
                               │
                         ┌─────┴─────┐
                         ▼           ▼
                     new facts   new procedures
                         │           │
                         └─────┬─────┘
                               ▼
                              RSI
```

### The most important part for your project

Your memory should eventually become **agentic**:

```text
Agent does work
      ↓
records experience
      ↓
evaluates outcome
      ↓
extracts lesson
      ↓
updates memory
      ↓
detects repeated patterns
      ↓
proposes procedure improvement
      ↓
tests improvement
      ↓
verifies improvement
      ↓
promotes verified change
```

That makes memory a foundation for your **RSI system**, instead of a passive storage layer.

A particularly strong implementation path is **SQLite/Postgres + pgvector + Git-backed procedural memory + Graphiti/Neo4j + local Ollama embeddings**, with your own `MemoryController` sitting above all of them. That gives you persistent semantic, episodic, temporal, procedural, relational, reflective and operational memory without requiring a paid memory API. ([GitHub][3])

[1]: https://github.com/letta-ai/skills/blob/main/letta/agent-development/references/memory-architecture.md?utm_source=chatgpt.com "skills/letta/agent-development/references/memory-architecture.md at main · letta-ai/skills · GitHub"
[2]: https://www.sqlite.org/fts5.html?utm_source=chatgpt.com "SQLite FTS5 Extension"
[3]: https://github.com/pgvector/pgvector?utm_source=chatgpt.com "GitHub - pgvector/pgvector: Open-source vector similarity search for Postgres · GitHub"
[4]: https://github.com/lancedb/lancedb?utm_source=chatgpt.com "GitHub - lancedb/lancedb: Developer-friendly OSS embedded retrieval library for multimodal AI. Search More; Manage Less. · GitHub"
[5]: https://github.com/letta-ai/letta-code/blob/main/src/agent/prompts/letta_local_memfs.md?utm_source=chatgpt.com "letta-code/src/agent/prompts/letta_local_memfs.md at main · letta-ai/letta-code · GitHub"
[6]: https://github.com/getzep/graphiti/blob/main/README.md?utm_source=chatgpt.com "graphiti/README.md at main · getzep/graphiti · GitHub"
[7]: https://github.com/falkordb/falkordb?utm_source=chatgpt.com "GitHub - FalkorDB/FalkorDB: A super fast Graph Database uses GraphBLAS under the hood for its sparse adjacency matrix graph representation. Our goal is to provide the best Knowledge Graph for LLM (GraphRAG). · GitHub"
[8]: https://github.com/memodb-io/memobase?utm_source=chatgpt.com "GitHub - memodb-io/memobase: User Profile-Based Long-Term Memory for AI Chatbot Applications. · GitHub"
[9]: https://github.com/WujiangXu/A-mem?utm_source=chatgpt.com "GitHub - WujiangXu/A-mem: The code for NeurIPS 2025 paper \"A-Mem: Agentic Memory for LLM Agents\" · GitHub"
[10]: https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/search.mdx?utm_source=chatgpt.com "mem0/docs/core-concepts/memory-operations/search.mdx at main · mem0ai/mem0 · GitHub"
[11]: https://duckdb.org/?utm_source=chatgpt.com "An analytical SQL database management system – DuckDB"
[12]: https://github.com/ollama/ollama/blob/main/docs/capabilities/embeddings.mdx?utm_source=chatgpt.com "ollama/docs/capabilities/embeddings.mdx at main · ollama/ollama · GitHub"
[13]: https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-types.mdx?utm_source=chatgpt.com "mem0/docs/core-concepts/memory-types.mdx at main · mem0ai/mem0 · GitHub"
[14]: https://github.com/langchain-ai/langmem/blob/main/docs/docs/concepts/conceptual_guide.md?utm_source=chatgpt.com "langmem/docs/docs/concepts/conceptual_guide.md at main · langchain-ai/langmem · GitHub"
[15]: https://github.com/mem0ai/mem0/blob/main/docs/open-source/overview.mdx?utm_source=chatgpt.com "mem0/docs/open-source/overview.mdx at main · mem0ai/mem0 · GitHub"
[16]: https://github.com/Tancy/openmemory?utm_source=chatgpt.com "GitHub - Tancy/openmemory: Add long-term memory to any AI in minutes. Self-hosted, open, and framework-free. · GitHub"
