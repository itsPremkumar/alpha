# Graphify → Alpha Integration Research & Implementation Blueprint

**Research target:** `Graphify-Labs/graphify`

**Repository:** https://github.com/Graphify-Labs/graphify

**Research date:** 2026-09-25

**Purpose:** Determine exactly what Graphify is useful for in the Alpha AI-agent project, what should be reused, what should remain separate, and how to integrate it as an open-source/local-first graph intelligence layer.

---

## 1. Executive conclusion

Graphify is highly relevant to Alpha, but it should be integrated as a **Graph Intelligence / Project Knowledge Graph subsystem**, not treated as Alpha's entire memory system.

The strongest fit is:

> **Alpha = agent runtime + planning + tools + skills + memory + swarm/orchestration + self-review + communications**
>
> **Graphify = structural knowledge graph and relationship-retrieval layer for the code, documents, schemas, configurations, research corpus, and selected agent knowledge.**

Graphify currently turns codebases and related artifacts into a queryable graph using local AST parsing and graph analysis. It exposes query/path/explain operations and can expose the graph through MCP over stdio or HTTP. Its documentation explicitly lists integrations for OpenClaw, Hermes, Codex, Cursor, Gemini CLI, Antigravity, and many other agent/coding environments. It also supports incremental updates, Git hooks, graph merging, a cross-project global graph, lessons/reflection, and optional Neo4j/FalkorDB export. [Sources: Graphify README; Architecture](https://github.com/Graphify-Labs/graphify/blob/v8/README.md) | https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md

### Recommended Alpha position

**Use Graphify.**

Use it first for:

1. **Codebase understanding**
2. **Project/document relationship memory**
3. **Task-context retrieval**
4. **Impact/dependency analysis**
5. **Architecture and call-flow understanding**
6. **Subagent context scoping**
7. **MCP-aware tool/skill/project topology**
8. **PR/change impact analysis**
9. **Self-review / recursive improvement context**
10. **Cross-project organizational knowledge graph**
11. **Research/document graphing**
12. **Provenance + confidence-aware reasoning**

Do **not** use it as the only storage mechanism for:

- raw chat history
- every event/log/heartbeat
- transient working memory
- live agent state
- task queue state
- high-frequency telemetry
- binary artifact storage
- secrets/credentials
- authoritative transactional application state

Alpha should use a **hybrid memory architecture**, where Graphify is the graph/relationship layer.

---

## 2. What Graphify actually is

Graphify describes itself as a Python library plus an agent skill that converts a project corpus into a queryable knowledge graph. The current repository documentation says it can map code, documentation, SQL schemas, configs, PDFs, images, video/audio, and related project artifacts. Code is parsed locally with tree-sitter; relationships are represented as graph edges and are tagged with confidence labels such as `EXTRACTED`, `INFERRED`, and `AMBIGUOUS`. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

The important conceptual difference is:

```text
Traditional file search / simple RAG
    question
       ↓
 text chunks / embeddings / keyword results
       ↓
    LLM answer

Graphify-style structural retrieval
    question
       ↓
 graph query / scoped retrieval
       ↓
 nodes + relationships + source locations + confidence
       ↓
    LLM reasoning
```

Graphify is therefore useful when the answer depends on **relationships**, not only on semantic similarity.

Examples:

- What calls this function?
- Which modules depend on this service?
- What connects authentication to the database?
- Which files are in the same subsystem?
- What changed that could affect this feature?
- Which documentation explains this implementation?
- What MCP servers/configs exist in this project?
- Which project owns or depends on a capability?
- What is the shortest relationship path between two concepts?

The repository exposes the core graph as `graph.json`, a human-facing `GRAPH_REPORT.md`, and a browser visualization `graph.html`. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

---

## 3. Why Graphify is unusually useful for Alpha

Alpha is intended to operate as a long-running agent system with:

- projects
- agents/bots
- subagents
- skills
- MCP servers
- tools/plugins
- codebases
- documents
- memory
- task plans
- collaboration
- self-review
- self-improvement
- long-running execution
- local/remote environments

Those objects naturally form a graph.

For example:

```text
Agent Alpha-Core
   │
   ├── uses → Skill: coding
   ├── uses → MCP: GitHub
   ├── uses → Tool: terminal
   ├── belongs_to → Project: Alpha
   ├── works_on → Task: Refactor memory
   └── consults → Project Knowledge Graph

Project: Alpha
   │
   ├── contains → packages
   ├── contains → agents
   ├── contains → skills
   ├── contains → MCP configs
   ├── contains → documents
   └── contains → source code

Code
   │
   ├── imports → package
   ├── calls → function
   ├── references → config
   ├── documented_by → ADR
   └── affected_by → task/change
```

Graphify can provide a strong starting implementation for the **structural project graph** rather than Alpha having to invent every graph-analysis feature from scratch.

---

## 4. Core Graphify capabilities relevant to Alpha

| Capability | What Graphify provides | Alpha use |
|---|---|---|
| AST parsing | Tree-sitter based code extraction | Deep project/code understanding |
| Cross-file relationships | Imports, calls, inheritance, uses, etc. | Dependency/impact analysis |
| Graph building | NetworkX graph pipeline | Graph reasoning layer |
| Confidence | `EXTRACTED`, `INFERRED`, `AMBIGUOUS` | Evidence-aware agent reasoning |
| Communities | Leiden-based subsystem clustering | Automatic project/module grouping |
| God nodes | Highly connected concepts | Architecture hotspots |
| Surprising connections | Cross-module relationship discovery | Discovery and research |
| Rationale extraction | `NOTE`, `WHY`, `HACK`, doc/design references | Preserve engineering intent |
| Query | Natural-language graph queries | Agent context retrieval |
| Path | Relationship/path tracing | Explainability and impact analysis |
| Explain | Node-centric relationship view | Focused investigation |
| Graph diff | Compare graph states | Change analysis / self-review |
| Watch/update | Incremental project updates | Always-current project knowledge |
| Git hooks | Automatic refresh after commit/branch events | Continuous project intelligence |
| Graph merge | Combine multiple graph files | Multi-project / multi-agent knowledge |
| Global graph | Cross-project graph registration | Alpha organization-wide knowledge |
| Reflection | Save result + reflect into lessons overlay | Alpha self-learning/self-review |
| MCP server | stdio or HTTP graph server | Native Alpha tool integration |
| Neo4j/FalkorDB export | External graph DB integration | Scale-out deployments |
| Wiki export | Graph → project knowledge wiki | Human-maintained/project docs |
| Call-flow HTML | Mermaid architecture/call-flow output | Debugging and architecture dashboards |
| GraphML/Cypher/Obsidian/SVG | Multiple export paths | Interoperability |
| URL/PDF/media ingestion | Broader knowledge corpus | Research/knowledge acquisition |
| SQL/Postgres | Schema extraction/introspection | Database-aware agents |
| MCP config extraction | MCP servers/packages/env requirements become graph concepts | MCP-aware Alpha |

The architecture document confirms a modular Python pipeline: `detect → extract → build → cluster → analyze → report → export`, with `serve.py` for MCP and `watch.py` for updates. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

---

## 5. Best Alpha use case #1 — deep codebase understanding

This is the clearest fit.

Alpha can build a graph of its own repository and every registered project. Instead of repeatedly reading dozens of source files, agents can first ask the graph for a scoped representation.

### Example tasks

```text
User: Fix the authentication bug.

Alpha Planner:
  1. Query graph for authentication architecture.
  2. Find auth service → middleware → database path.
  3. Identify relevant files and functions.
  4. Read only those files.
  5. Implement change.
  6. Query graph diff / impacted nodes.
  7. Run tests.
  8. Update graph.
```

### Why this matters

The graph can reduce irrelevant source reading and make dependency-aware planning easier.

Graphify's own example shows node explanation with source location, connections, degree, communities, and relationship confidence, as well as shortest-path tracing between concepts. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

### Alpha feature

Create an internal tool:

```text
alpha.project.graph.query(question)
alpha.project.graph.explain(node)
alpha.project.graph.path(source, target)
alpha.project.graph.neighbors(node)
alpha.project.graph.impact(node)
```

---

## 6. Best Alpha use case #2 — project memory

Graphify should become one layer of Alpha's **project memory**.

### Recommended project memory stack

```text
                         ALPHA MEMORY
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   Working Memory       Episodic Memory       Semantic Memory
   current task         past sessions          stable facts
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                     GRAPH / RELATIONSHIP
                              │
                         Graphify
                              │
                    structure + provenance
                              │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
        Code               Docs              Schemas
          │                  │                  │
          └──────────────────┼──────────────────┘
                              │
                      Vector / lexical RAG
                              │
                      Optional other stores
```

### Graph memory is especially good for

- entities
- relationships
- dependencies
- project topology
- causal links
- provenance
- architecture
- source references
- implementation/documentation relationships
- cross-project references
- change impact

It is less suited to storing every raw conversational turn or every high-frequency event.

---

## 7. Best Alpha use case #3 — long-term agent memory

Graphify's repository publishes memory benchmark results on LOCOMO and LongMemEval and presents the system as useful for conversational long-term memory as well as code intelligence. The documented benchmark snapshot reports LOCOMO recall@10 of 0.497, LOCOMO QA accuracy of 45.3%, and LongMemEval-S QA accuracy of 76% under its stated benchmark harness; the project also states that graph building itself requires zero LLM credits for the code graph. These are project-reported benchmark results, so Alpha should reproduce them independently before treating them as production guarantees. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/BENCHMARKS.md)

### Alpha adaptation

Do not dump every conversation directly into the graph.

Instead, use a memory compiler:

```text
Conversation / task execution
        ↓
 extraction
        ↓
 candidate memories
        ↓
 fact/entity/relation normalization
        ↓
 provenance attachment
        ↓
 confidence assessment
        ↓
 graph memory
```

Store something like:

```json
{
  "subject": "Alpha",
  "relation": "prefers",
  "object": "Ollama-local-inference",
  "source": "conversation/session/123",
  "confidence": 0.88,
  "status": "active",
  "observed_at": "2026-09-25T12:00:00Z",
  "last_verified": "2026-09-25T12:00:00Z"
}
```

Graphify currently already models relationships with source metadata and confidence labels. Alpha can extend the same conceptual model for agent memory.

---

## 8. Best Alpha use case #4 — evidence-aware reasoning

One of Graphify's strongest ideas for Alpha is its distinction between:

```text
EXTRACTED  = explicit relationship found in source
INFERRED   = relationship resolved/deduced
AMBIGUOUS  = uncertain relationship
```

[Source: Graphify architecture](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

### Alpha should reuse this philosophy globally

Every memory item should carry:

- source
- timestamp
- provenance
- confidence
- evidence type
- verification state
- freshness
- contradiction state

Example:

```json
{
  "relation": "service_A_calls_service_B",
  "evidence_type": "EXTRACTED",
  "source_file": "src/serviceA.ts",
  "source_location": "L87",
  "confidence": 1.0,
  "verified": true
}
```

For an inferred relationship:

```json
{
  "relation": "task_may_affect_service_B",
  "evidence_type": "INFERRED",
  "confidence": 0.72,
  "verification_required": true
}
```

This fits Alpha's goal of not silently treating guesses as facts.

---

## 9. Best Alpha use case #5 — dynamic task planning

Graphify can be used before a task is executed.

### Recommended planning flow

```text
User request
    ↓
Intent / objective extraction
    ↓
Graph query
    ↓
Relevant nodes
    ↓
Relevant communities
    ↓
Dependencies
    ↓
Potentially affected files/services
    ↓
Task decomposition
    ↓
Subagent assignment
```

### Example

```text
Request:
"Add a new memory backend."

Graph questions:
- Where are memory interfaces defined?
- Which modules implement current backends?
- Which agents consume the memory API?
- Which tests cover memory?
- Which configuration files select memory backends?
- Which docs describe memory architecture?

Result:
A smaller, dependency-aware task graph for Alpha's planner.
```

This makes Graphify useful as a **planning-context engine** rather than merely a search engine.

---

## 10. Best Alpha use case #6 — subagent context scoping

Alpha can use Graphify to decide what each subagent should know.

### Example

```text
Parent Agent
   │
   ├── Coding Agent
   │      └── gets graph community: backend/memory
   │
   ├── Test Agent
   │      └── gets graph neighborhood: test modules
   │
   ├── Docs Agent
   │      └── gets graph neighborhood: docs/ADRs
   │
   └── Review Agent
          └── gets graph diff + impacted nodes
```

Rather than injecting the whole repository into every agent, Alpha can ask Graphify for a **scoped subgraph** and give that subgraph to the subagent.

This can reduce context waste and improve role separation.

---

## 11. Best Alpha use case #7 — self-review and recursive self-improvement

This is one of the most interesting matches with Alpha's RSI / recursive self-improvement architecture.

### Alpha RSI loop

```text
Current Alpha code
      ↓
Graphify extraction
      ↓
architecture graph
      ↓
find hotspots / orphan concepts / surprising links
      ↓
compare desired architecture vs actual topology
      ↓
identify gaps
      ↓
create improvement task
      ↓
subagent implements change
      ↓
run tests
      ↓
rebuild graph
      ↓
graph diff
      ↓
review impact
      ↓
accept / reject / revise
```

### Useful signals

- unusually high-degree nodes
- import cycles
- unexpected cross-module dependencies
- missing documentation relationships
- changed communities
- graph topology changes after a patch
- nodes that became disconnected
- newly coupled subsystems
- increased complexity around critical hubs

Graphify's architecture explicitly lists graph diff, import-cycle detection, god nodes, surprising connections, and suggested questions as analysis functions. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

---

## 12. Best Alpha use case #8 — code change impact analysis

This can become a first-class Alpha feature.

### User asks

```text
"Can I change the memory interface without breaking the rest of Alpha?"
```

### Alpha process

```text
Find memory interface node
      ↓
neighbors
      ↓
call/import/reference graph
      ↓
shortest paths
      ↓
reverse dependencies
      ↓
tests/docs/configs
      ↓
impact set
```

Graphify has an explicit graph-diff utility and can generate graph-based architecture/call-flow outputs. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

---

## 13. Best Alpha use case #9 — PR and Git intelligence

The current command surface includes PR-related functionality such as:

- PR dashboard
- PR deep dive
- CI/review/worktree mapping
- graph impact
- conflict analysis
- triage

[Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

### Alpha integration

Create a review pipeline:

```text
GitHub PR
   ↓
changed files
   ↓
Graphify graph neighborhood
   ↓
affected modules
   ↓
affected agents/tools/docs/tests
   ↓
review plan
   ↓
parallel review subagents
   ↓
merged findings
```

### Important Alpha extension

The graph should make an explicit distinction between:

```text
code dependency impact
        vs
runtime behavior impact
        vs
business/task impact
```

Graphify covers the structural layer. Alpha's runtime telemetry/tests should provide the runtime layer.

---

## 14. Best Alpha use case #10 — skills, tools, MCP, and plugin topology

Graphify extracts MCP configuration files and package manifests. Its README states that MCP configs can produce server/package/environment-requirement nodes, and package manifests can create dependency relationships. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

This is especially useful for Alpha because Alpha is intended to dynamically manage:

- tools
- skills
- plugins
- MCP servers
- providers
- agents
- bot profiles
- project resources

### Alpha tool ecosystem graph

```text
Project
  ├── Agent
  │    ├── Skill
  │    ├── Tool
  │    └── MCP Server
  │          ├── package
  │          ├── environment requirement
  │          └── exposed capability
  │
  └── dependency
```

### Dynamic discovery example

```text
User: "Which agent can work with GitHub issues?"

Alpha queries:
Agent → Skills → MCP → tool capability → project ownership
```

That is much more powerful than scanning a flat plugin directory.

---

## 15. Best Alpha use case #11 — multi-agent organizational graph

Graphify itself is primarily a project/corpus knowledge graph, but Alpha can extend the same graph concept to agent organization.

### Suggested Alpha organizational graph

```text
Organization
   ↓
Project
   ↓
Team / Group
   ↓
Agent
   ↓
Role
   ↓
Skills / Tools / MCPs
   ↓
Tasks
   ↓
Artifacts
   ↓
Results / Lessons
```

### Example relations

```text
agent OWNS project
agent MEMBER_OF group
agent REPORTS_TO agent
agent CAN_USE skill
skill EXPOSES tool
agent ASSIGNED task
agent CREATED artifact
artifact REFERENCES document
artifact AFFECTS code node
task DEPENDS_ON task
lesson DERIVED_FROM execution
```

Graphify should not become the authoritative HR/attendance/task database; instead, Alpha can periodically materialize **structural snapshots** or selected relationships into the graph.

---

## 16. Best Alpha use case #12 — cross-project global knowledge

The current CLI supports a global graph where project graphs can be registered, listed, and queried, and it supports merging graphs. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

This maps directly to Alpha's multi-project architecture.

### Example

```text
GLOBAL ALPHA GRAPH
│
├── Project: Alpha
├── Project: Sproutern
├── Project: Video Generator
├── Project: Omni-Morph
├── Project: Client Project A
└── Research Corpus
```

Alpha can then ask:

- Which projects use the same package?
- Which skills are reusable across projects?
- Where does the same architecture pattern appear?
- Which agent already solved a similar problem?
- What implementation can be reused?
- Which projects are affected by a shared library change?

This is a high-value feature for a long-running multi-project agent.

---

## 17. Best Alpha use case #13 — research knowledge graph

Graphify can ingest URLs and papers and can process PDFs/media. Its README documents examples involving arXiv and video URLs, plus PDF/image/video/audio processing. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Alpha can use that to build a research graph:

```text
Research Question
      ↓
Paper
      ↓
Concept
      ↓
Method
      ↓
Implementation
      ↓
GitHub project
      ↓
Code module
      ↓
Alpha experiment
      ↓
Result
```

### This is useful for

- researching new agent architectures
- comparing open-source projects
- storing relationships between papers and implementations
- linking research findings to Alpha design decisions
- connecting an idea to an implementation and test result

For rigorous research workflows, Alpha should retain original source URLs, dates, quotes/evidence snippets where appropriate, and a separate source registry rather than relying solely on graph nodes.

---

## 18. Best Alpha use case #14 — architecture visualization

Graphify can produce:

- `graph.html`
- SVG
- GraphML
- Obsidian exports
- Canvas
- Cypher
- Mermaid call-flow HTML
- wiki-style Markdown

[Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

### Alpha dashboard ideas

```text
Alpha System Map
    ↓
Agent topology
    ↓
Project topology
    ↓
Tool/MCP topology
    ↓
Memory topology
    ↓
Code topology
    ↓
Dependency hotspots
```

This can become part of Alpha's system-monitoring and developer dashboard.

---

## 19. Best Alpha use case #15 — documentation that stays connected to code

Graphify treats documentation links, rationale comments, ADR/RFC-style references, and related documents as graph relationships. Its README explicitly describes `NOTE`, `WHY`, and related rationale information becoming first-class graph concepts. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

That enables Alpha to answer:

```text
Why does this code exist?
What document explains this decision?
Which ADR is connected to this implementation?
Which docs became stale after this change?
```

### Alpha improvement

Add a `STALE_AFTER_CHANGE` relationship when code changes but its documentation has not been reviewed.

---

## 20. Best Alpha use case #16 — SQL/database intelligence

Graphify supports SQL schema extraction and has a PostgreSQL optional integration for live introspection. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

### Alpha database graph

```text
Table
  ├── column
  ├── primary key
  ├── foreign key
  ├── index
  └── constraint

Service
  └── queries → Table

API
  └── calls → Service

Agent Task
  └── affects → API
```

This is useful for Alpha's coding/debugging agent and read-only database investigation.

### Recommended safety boundary

Keep live database access outside Graphify's normal graph as a controlled Alpha tool. Graphify can describe/schema-index the database, while Alpha's DB MCP or database tool remains the authoritative read-only execution interface.

---

## 21. Best Alpha use case #17 — filesystem and workspace knowledge

Graphify supports many code, documentation, office, image, and media types. This allows an Alpha workspace to have one structural index across multiple artifact categories.

Example:

```text
workspace/
├── source/
├── docs/
├── ADRs/
├── diagrams/
├── PDFs/
├── spreadsheets/
├── meeting recordings/
├── configs/
└── SQL schemas/
```

Graphify can connect selected pieces of this corpus into one graph.

However, Alpha should maintain an explicit **document/artifact registry** with content hashes and permissions so that a graph entry never becomes the sole source of truth for file permissions or file contents.

---

## 22. Best Alpha use case #18 — self-learning via reflections

The repository documents a `save-result` and `reflect` workflow and a graph learning overlay that can tag nodes as preferred/tentative/contested with provenance and recency, including a signal to re-verify when underlying code has changed. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

This idea maps directly to Alpha's self-learning architecture.

### Recommended Alpha learning pipeline

```text
Execution
   ↓
Outcome
   ↓
Useful / dead-end / corrected
   ↓
Evidence extraction
   ↓
Graph relationship update
   ↓
Lesson generation
   ↓
Confidence + recency
   ↓
Future retrieval hint
   ↓
Re-verification after code change
```

This is one of the most useful concepts to borrow even when Alpha does not reuse Graphify's exact implementation.

---

## 23. Recommended Alpha memory architecture with Graphify

A robust Alpha architecture should be hybrid:

```text
                         ┌───────────────────────┐
                         │       ALPHA            │
                         │   Agent Runtime       │
                         └──────────┬────────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
             Working Memory    Session/Events     Knowledge
                  │                 │                 │
                  │                 │                 │
                  │            Event Store      ┌──────┴──────┐
                  │                             │             │
                  │                        Graph Memory   Vector/RAG
                  │                             │             │
                  │                          Graphify      optional
                  │                             │             │
                  │                    ┌────────┼────────┐  │
                  │                    │        │        │  │
                  │                   Code     Docs    Schema │
                  │                    │        │        │  │
                  └────────────────────┴────────┴────────┴──┘
                                    │
                         Provenance / Evidence
                                    │
                          Verification / Review
```

### Suggested storage responsibilities

| Data | Suggested Alpha storage |
|---|---|
| Current task | Working-memory store |
| Agent state | Transactional DB/state store |
| Raw events | Event log |
| Conversation transcript | File/object store + indexed retrieval |
| Semantic text retrieval | Optional vector/RAG store |
| Entities/relationships | Graphify / graph backend |
| Code structure | Graphify |
| Project architecture | Graphify |
| Tool/MCP relationships | Graphify + Alpha registry |
| Lessons | Graph overlay + Alpha lesson store |
| Secrets | OS secret store / vault |
| Large binaries | File/object store |
| Telemetry | Metrics/time-series store |

---

## 24. Graphify integration options for Alpha

### Option A — MCP sidecar (recommended first)

Run Graphify as a separate local process and connect Alpha to its MCP server.

```text
Alpha
  │
  │ MCP
  ▼
Graphify server
  │
  ▼
graph.json
```

Benefits:

- lowest coupling
- Graphify remains upgradeable independently
- easy Python/TypeScript boundary
- clean plugin architecture
- Alpha only sees tool contracts
- can later replace Graphify with another graph engine

This is the recommended first implementation.

### Option B — Python library integration

Graphify's architecture says the Python library can be used standalone, and the pipeline is implemented as Python modules. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

```text
Alpha Python worker
      ↓
import graphify
      ↓
extract/build/query/export
```

Benefits:

- direct API access
- lower IPC overhead
- deeper customization

Cost:

- tighter coupling
- Python runtime management
- harder plugin lifecycle isolation

Use this when Alpha needs specialized graph processing beyond MCP.

### Option C — HTTP MCP server

Graphify supports Streamable HTTP MCP and documents a shared server model. The default bind is localhost; network exposure is opt-in. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

```text
Alpha Agent A ─┐
Alpha Agent B ─┼──→ Graphify HTTP MCP
Alpha Agent C ─┘
```

This is useful for the future Alpha multi-agent/local-network architecture.

### Option D — Graph database backend

Graphify has optional Neo4j and FalkorDB integrations/exports. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

```text
Graphify extraction
       ↓
Neo4j / FalkorDB
       ↓
shared graph infrastructure
```

Use this only when graph size, concurrent access, durability, query patterns, or remote collaboration justify it.

---

## 25. Recommended Alpha architecture: Graphify as a replaceable adapter

Do not hard-code Graphify throughout Alpha.

Create an abstraction:

```text
packages/
  knowledge/
    graph/
      interface.ts
      models.ts
      query.ts
      provenance.ts
      adapters/
        graphify-mcp.ts
        graphify-python.ts
        neo4j.ts
        in-memory.ts
```

### Suggested interface

```ts
export interface KnowledgeGraphProvider {
  query(input: GraphQuery): Promise<GraphQueryResult>;
  getNode(id: string): Promise<GraphNode | null>;
  getNeighbors(id: string, options?: NeighborOptions): Promise<GraphNode[]>;
  shortestPath(source: string, target: string): Promise<GraphPath>;
  explain(target: string): Promise<NodeExplanation>;
  diff(from: GraphSnapshot, to: GraphSnapshot): Promise<GraphDiff>;
  refresh(scope: GraphRefreshScope): Promise<GraphRefreshResult>;
}
```

This preserves Alpha's freedom to replace the implementation later.

---

## 26. Proposed Alpha graph data model

Graphify's extraction model is approximately:

```json
{
  "nodes": [
    {
      "id": "unique_string",
      "label": "human name",
      "source_file": "path",
      "source_location": "L42"
    }
  ],
  "edges": [
    {
      "source": "id_a",
      "target": "id_b",
      "relation": "calls|imports|uses|...",
      "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"
    }
  ]
}
```

[Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

Alpha can extend this to:

```ts
interface AlphaGraphNode {
  id: string;
  type:
    | "project"
    | "agent"
    | "task"
    | "skill"
    | "tool"
    | "mcp"
    | "file"
    | "function"
    | "class"
    | "document"
    | "schema"
    | "lesson"
    | "memory"
    | "research"
    | "artifact";

  label: string;
  projectId?: string;
  source?: Provenance;
  metadata?: Record<string, unknown>;
  status?: string;
  createdAt?: string;
  updatedAt?: string;
  lastVerifiedAt?: string;
  confidence?: number;
}

interface AlphaGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  evidenceType: "EXTRACTED" | "INFERRED" | "AMBIGUOUS";
  confidence: number;
  sourceRefs: string[];
  validFrom?: string;
  validUntil?: string;
  lastVerifiedAt?: string;
  status?: "active" | "stale" | "contested" | "rejected";
}
```

---

## 27. Provenance model Alpha should add

Graphify already gives source locations and confidence labels. Alpha should make provenance a first-class concept.

```text
Memory / Relation
    │
    ├── source file
    ├── source line
    ├── source document
    ├── conversation/session ID
    ├── tool execution ID
    ├── agent ID
    ├── timestamp
    ├── evidence type
    ├── confidence
    └── verification state
```

This allows Alpha to answer:

> "Why do you believe this?"

and produce:

```text
Evidence:
  src/memory/router.ts:L118
  session: 2026-09-25T...
  extracted relationship
  last verified: current
```

---

## 28. Freshness and temporal memory

For an always-on Alpha, graph freshness matters.

Graphify documents incremental update behavior and Git hooks that refresh the graph on commit and branch switching, while `graphify update .` is used after pulls/merges. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Alpha should extend this idea:

```text
Source changed
    ↓
source hash changed
    ↓
affected graph nodes identified
    ↓
affected edges marked stale
    ↓
re-extract
    ↓
re-verify lessons/memories
    ↓
update graph
```

### Important

A graph memory should never be assumed permanently true when its underlying source has changed.

Recommended states:

```text
ACTIVE
STALE
CONTESTED
REJECTED
VERIFIED
```

---

## 29. Agent query policy for Alpha

Alpha should establish a **graph-first when appropriate** policy.

### For codebase questions

```text
1. Graph query
2. Graph path/explain
3. Read exact source files
4. Execute tests/tools
5. Answer
```

### For broad textual questions

```text
1. Semantic/text retrieval
2. Graph relationship expansion
3. Source verification
4. Answer
```

### For high-risk decisions

```text
1. Graph retrieval
2. Source verification
3. Independent evidence
4. Tool execution/test
5. Review
6. Answer
```

Graphify itself installs platform-specific instructions/hooks that nudge agents to consult the graph before raw searching/reading in supported clients. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Alpha can generalize this to its own runtime.

---

## 30. Graph query strategy for Alpha

Do not send the whole graph to the LLM.

Use progressive expansion.

### Level 0 — node lookup

```text
find exact concept
```

### Level 1 — local neighborhood

```text
1-hop neighbors
```

### Level 2 — relevant paths

```text
source → target path
```

### Level 3 — community/subsystem

```text
relevant cluster
```

### Level 4 — source verification

```text
open only supporting files
```

### Level 5 — broad graph

Only when explicitly needed.

This is important for Alpha's limited-hardware/local-model mode because graph scoping can reduce unnecessary context processing.

---

## 31. Alpha + Graphify + Ollama

Graphify supports an Ollama backend and documents local Ollama configuration. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

For Alpha's local-first architecture this provides a useful path:

```text
Windows
  │
  ├── Alpha
  ├── Ollama
  └── Graphify
        │
        ├── code → local AST
        └── docs/media semantic extraction → Ollama
```

### Cost model

Code-only extraction can run without API keys according to Graphify's privacy documentation. Mixed corpora may use an LLM for semantic extraction of docs/PDFs/images unless an appropriate local backend such as Ollama is selected. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Therefore:

- **Code graph:** effectively local/offline
- **Docs graph:** can be local with Ollama, but consumes local compute
- **Cloud semantic extraction:** optional
- **Vector database:** not required by Graphify's documented graph design

---

## 32. Windows-first Alpha deployment

Graphify documents Windows support and recommends installing `uv`; it also notes PowerShell command differences. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Recommended Alpha deployment:

```text
Alpha Desktop
   │
   ├── Alpha API/runtime
   ├── Graphify sidecar
   ├── Ollama optional
   └── SQLite/other Alpha DB
```

### Suggested directories

```text
alpha/
├── data/
│   ├── projects/
│   │   ├── alpha/
│   │   │   └── graphify-out/
│   │   └── project-2/
│   ├── global-graph/
│   └── memory/
├── services/
│   ├── graphify/
│   └── ollama/
└── config/
```

Do not store all Alpha state inside `graphify-out/`.

---

## 33. Installation / proof-of-concept

### Install Graphify CLI

Graphify's current README identifies the official PyPI package as `graphifyy` while the command is `graphify`. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Windows:

```powershell
winget install astral-sh.uv
uv tool install graphifyy
```

### Build a first graph

```powershell
cd C:\path\to\alpha

graphify install --project

graphify .
```

This produces the documented `graphify-out/` artifacts such as `graph.json`, `GRAPH_REPORT.md`, and `graph.html`.

### For code-only local analysis

```powershell
graphify extract . --code-only
```

### For local semantic extraction with Ollama

```powershell
uv tool install "graphifyy[ollama]"

graphify extract . --backend ollama
```

### MCP

```powershell
uv tool install "graphifyy[mcp]"
python -m graphify.serve graphify-out\graph.json
```

The documented MCP tools include:

```text
query_graph
get_node
get_neighbors
shortest_path
list_prs
get_pr_impact
triage_prs
```

[Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

---

## 34. Alpha MCP adapter

Alpha should wrap Graphify behind a stable internal namespace.

### Suggested tools

```text
alpha_graph_query
alpha_graph_get_node
alpha_graph_neighbors
alpha_graph_path
alpha_graph_explain
alpha_graph_impact
alpha_graph_diff
alpha_graph_refresh
alpha_graph_search_projects
alpha_graph_verify_memory
alpha_graph_lessons
```

### Example `alpha_graph_query`

Input:

```json
{
  "projectId": "alpha",
  "question": "What connects memory routing to the agent execution loop?",
  "maxNodes": 80,
  "maxHops": 3,
  "includeEvidence": true
}
```

Output:

```json
{
  "nodes": [],
  "edges": [],
  "evidence": [],
  "communities": [],
  "confidence": {
    "extracted": 0,
    "inferred": 0,
    "ambiguous": 0
  }
}
```

The exact Graphify wire format should be wrapped rather than becoming the permanent Alpha public API.

---

## 35. Multi-agent Alpha architecture with a shared Graphify server

Graphify documents HTTP MCP serving for a shared graph server, with localhost as the default and API-key authorization when exposing it beyond localhost. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

Future Alpha topology:

```text
                    ┌───────────────┐
                    │ Graphify HTTP │
                    │   MCP Server  │
                    └───────┬───────┘
                            │
             ┌──────────────┼──────────────┐
             │              │              │
          Agent A        Agent B        Agent C
             │              │              │
          Project 1      Project 1      Research
```

For Alpha's same-LAN use case, this makes Graphify a shared read-oriented knowledge service.

### Important design rule

Use the graph server primarily for **knowledge queries**. Keep task mutation, agent control, credentials, and runtime operations behind Alpha's own authorization and tool layers.

---

## 36. Local-network and remote deployment

### Local machine

Best for the first version:

```text
stdio MCP
```

### Same LAN

```text
HTTP MCP
127.0.0.1 → local only
0.0.0.0 → LAN/shared host when explicitly configured
```

Graphify documents API-key authentication for HTTP serving. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

### Remote infrastructure

For remote/shared operation:

```text
Alpha Agent
  ↓
secure transport / gateway
  ↓
Graphify MCP service
  ↓
central graph backend
```

Alpha should add its own authentication/identity/authorization layer for multi-agent access rather than relying only on a single shared key when the environment grows.

---

## 37. Security findings relevant to Alpha

Graphify's security policy documents mitigations for:

- URL SSRF
- oversized downloads
- non-2xx fetches
- graph-path traversal
- XSS in graph HTML
- prompt injection through node labels/source text
- YAML frontmatter injection
- encoding failures
- symlink traversal
- corrupted graph files

It also says the normal MCP mode does not listen on a network interface unless HTTP transport is explicitly enabled, and that source files are parsed without executing them. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/SECURITY.md)

### Alpha should still add

- project-level authorization
- agent identity
- graph namespace isolation
- per-project graph permissions
- sensitive-file filters
- secret scanning before extraction
- source classification (`PUBLIC`, `PRIVATE`, `CONFIDENTIAL`, `RESTRICTED`)
- read-only graph credentials
- network ACLs for shared HTTP
- audit logs for sensitive graph queries

### Recommended sensitive-file policy

Do not index:

```text
.env*
credentials*
secrets/
private_keys/
cloud credentials
browser profiles
session tokens
cookies
password databases
```

Use `.graphifyignore` and Alpha's own secret scanner.

---

## 38. Privacy findings

Graphify's current README says code extraction is performed locally by tree-sitter and can run fully offline, while docs/PDFs/images may be passed through an AI assistant/model for semantic extraction. It also documents a local query log and an opt-out control. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

### Alpha privacy mode

Implement three levels:

```text
STRICT_LOCAL
  code + local semantic model only

LOCAL_PREFERRED
  local first, approved cloud providers only if requested

CLOUD_ENABLED
  explicit provider policy
```

Each project should have its own policy.

---

## 39. License / commercial-use notes

The current repository contains:

- `LICENSE` with Apache License 2.0 text
- `LICENSE-MIT` with MIT License text
- `NOTICE`

The current `pyproject.toml` metadata declares `Apache-2.0` and lists those license files. The GitHub repository page currently displays both Apache-2.0 and MIT license indicators. [Sources:](https://github.com/Graphify-Labs/graphify/blob/v8/LICENSE) | https://github.com/Graphify-Labs/graphify/blob/v8/LICENSE-MIT | https://github.com/Graphify-Labs/graphify/blob/v8/NOTICE | https://github.com/Graphify-Labs/graphify/blob/v8/pyproject.toml

### Practical Alpha rule

For internal/prototype use, Graphify can be evaluated as an open-source component.

For redistribution/commercial shipping of modified or embedded Graphify code:

1. preserve the applicable copyright/license notices;
2. retain required `NOTICE` attribution;
3. do not assume the presence of `LICENSE-MIT` means the entire repository can be relicensed under MIT;
4. identify which files/components are covered by which license;
5. review third-party dependency licenses;
6. review trademarks separately;
7. perform a final legal/compliance review before distribution.

The Alpha project should keep Graphify attribution and license files in a dedicated third-party notices directory if Graphify is redistributed.

---

## 40. Current technical stack observed

The current package metadata indicates:

- Python `>=3.10`
- NetworkX
- NumPy
- RapidFuzz
- tree-sitter and many language grammars
- optional MCP support
- optional Neo4j/FalkorDB support
- optional PDF/office/media integrations
- optional Ollama and cloud LLM backends

The current package version in the inspected `v8` tree is `0.9.67`. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/pyproject.toml)

Because this document is a research snapshot, pin the exact Graphify commit/tag when integrating into Alpha rather than tracking an unpinned moving branch.

Recommended:

```text
alpha dependencies
    └── graphify adapter
           └── pinned Graphify version
```

---

## 41. Graphify's own architecture is a good design reference for Alpha

The repository's architecture is cleanly staged:

```text
detect
  ↓
extract
  ↓
build
  ↓
cluster
  ↓
analyze
  ↓
report
  ↓
export
```

And then:

```text
watch → update → query
serve → MCP
cache → incremental semantic work
security → validate external input
```

[Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

### Alpha lesson

Do not build one giant "memory manager".

Separate:

```text
capture
→ normalize
→ extract
→ graph
→ index
→ retrieve
→ reason
→ verify
→ learn
```

That modularity matches Alpha's plugin/harness goals.

---

## 42. What Alpha should reuse directly

### Reuse as a dependency / service

- tree-sitter based structural extraction
- Graphify graph schema concepts
- NetworkX graph processing where appropriate
- graph query/path/explain concepts
- community detection
- graph diff ideas
- confidence labeling
- incremental cache/update logic
- Git-hook update pattern
- MCP serving pattern
- GraphML/Cypher interoperability
- lessons/reflection concept
- source/provenance model

### Reuse as architectural inspiration

- modular pipeline
- graph-first code investigation
- scoped retrieval
- graph + report + visualization outputs
- explicit extracted vs inferred relationships
- cross-project graph
- local-first operation
- source-change-triggered re-verification

---

## 43. What Alpha should NOT copy blindly

Do not blindly copy:

- Graphify's command-line UX into Alpha's core API
- its project directory conventions
- its exact graph schema as Alpha's universal data model
- its HTTP exposure model as Alpha's complete network security model
- its benchmark claims as guaranteed Alpha performance
- its graph as the only memory database
- its report format as the only reasoning context format
- its semantic extraction assumptions for all agent memories

Alpha has broader requirements than a code/project knowledge graph.

---

## 44. What Graphify does NOT replace in Alpha

| Alpha subsystem | Graphify replacement? | Why |
|---|---:|---|
| Agent runtime | No | Graphify is knowledge infrastructure |
| LLM router | No | Graphify consumes/configures models but is not Alpha's model router |
| Planning engine | No | Graphify can provide planning context |
| Tool executor | No | Graphify describes relationships, not arbitrary runtime actions |
| Browser automation | No | Separate tool |
| Terminal executor | No | Separate tool |
| Agent communication | No | Separate transport/messaging layer |
| Agent scheduler | No | Separate runtime system |
| Live monitoring | No | Separate telemetry system |
| Task/Kanban | No | Separate transactional state |
| Authentication | No | Separate Alpha security layer |
| Secret management | No | Separate vault/OS secret store |
| Raw chat storage | No | Better handled by transcript/event storage |
| Vector retrieval | No | Optional separate retrieval layer |
| Object/blob storage | No | Separate file/object storage |
| Long-running workflows | No | Alpha orchestration |
| Self-healing | No | Alpha runtime/recovery |
| Self-learning | Partial | Graphify gives useful reflection ideas |
| Code intelligence | Yes / strongly | Major Graphify strength |
| Project relationship memory | Yes / strongly | Major fit |

---

## 45. Recommended Alpha graph layers

Instead of a single graph, use namespaces/layers.

```text
GLOBAL GRAPH
│
├── PROJECT GRAPH
│   ├── code
│   ├── docs
│   ├── schemas
│   └── configs
│
├── AGENT GRAPH
│   ├── agents
│   ├── skills
│   ├── tools
│   ├── MCPs
│   └── roles
│
├── TASK GRAPH
│   ├── tasks
│   ├── dependencies
│   └── artifacts
│
├── MEMORY GRAPH
│   ├── facts
│   ├── entities
│   ├── lessons
│   └── preferences
│
├── RESEARCH GRAPH
│   ├── papers
│   ├── projects
│   ├── concepts
│   └── experiments
│
└── CHANGE GRAPH
    ├── commits
    ├── PRs
    ├── graph diffs
    └── impact records
```

This architecture is broader than Graphify itself but uses Graphify naturally for the project/code layer.

---

## 46. Recommended graph namespaces

Every Alpha node should be namespaced:

```text
alpha://project/<projectId>/...
alpha://agent/<agentId>/...
alpha://skill/<skillId>/...
alpha://tool/<toolId>/...
alpha://mcp/<mcpId>/...
alpha://task/<taskId>/...
alpha://memory/<memoryId>/...
alpha://research/<researchId>/...
```

This prevents collisions when multiple graphs are merged.

---

## 47. Graph freshness strategy

Implement a freshness score:

```text
freshness = f(
  source_change_time,
  verification_time,
  relation_type,
  evidence_strength,
  volatility
)
```

Examples:

```text
function-import relationship
  → low volatility

agent status
  → extremely high volatility

user preference
  → medium volatility

architecture decision
  → low/medium volatility

live task state
  → extremely high volatility
```

Graphify should primarily hold low-to-medium volatility structural knowledge, while Alpha's transactional runtime stores high-frequency state.

---

## 48. Recommended retrieval policy

### Query router

```text
User request
    ↓
Classify intent
    ↓
┌──────────────┬──────────────┬──────────────┐
│ Structural   │ Semantic     │ Operational  │
│ graph query  │ RAG/search   │ runtime tool │
└──────┬───────┴──────┬───────┴──────┬───────┘
       ↓              ↓              ↓
    Graphify        vector/text    Alpha tools
       │              │              │
       └──────────────┼──────────────┘
                      ↓
                  Evidence merge
                      ↓
                   LLM reason
```

### Structural examples

Use Graphify for:

- imports
- calls
- dependency chains
- architecture
- project topology
- cross-file relationships
- impact paths

### Semantic examples

Use text/vector retrieval for:

- fuzzy natural language memories
- long prose
- conversations
- large reports
- similar examples

### Operational examples

Use Alpha tools for:

- run tests
- modify files
- query live DB
- send messages
- manage agents
- install plugins

---

## 49. Alpha graph-aware planner

Add a planner stage named something like:

```text
GraphContextPlanner
```

Responsibilities:

1. determine whether graph information is relevant;
2. identify the project(s) involved;
3. select seed nodes;
4. expand relevant relationships;
5. collect source evidence;
6. produce a compact context pack;
7. record evidence/provenance;
8. request verification if confidence is low.

Output:

```json
{
  "task": "refactor memory router",
  "graphContext": {
    "seedNodes": [],
    "communities": [],
    "paths": [],
    "affectedNodes": [],
    "sourceRefs": [],
    "ambiguousRelations": []
  }
}
```

---

## 50. Alpha graph-aware reviewer

Create a dedicated review mode:

```text
REVIEW_GRAPH
```

### Inputs

- git diff
- previous graph snapshot
- current graph
- task objective
- tests

### Checks

```text
Did the patch:
- add an unexpected dependency?
- break a known path?
- create a cycle?
- disconnect a component?
- alter a high-centrality node?
- leave documentation stale?
- affect MCP/tool topology?
```

### Output

```text
Graph Impact Review
────────────────────
Changed nodes: N
New edges: N
Removed edges: N
Changed communities: N
Potentially affected modules: N
Ambiguous relationships: N
Documentation requiring review: N
```

---

## 51. Alpha graph-aware self-healing

Graphify is not itself a self-healing runtime, but graph topology can help Alpha select repair actions.

```text
Failure detected
    ↓
identify failing component
    ↓
Graphify neighbors/path
    ↓
find dependency chain
    ↓
find recent graph changes
    ↓
select likely repair scope
    ↓
spawn specialist subagent
    ↓
test
    ↓
graph refresh
    ↓
verify topology
```

This provides structural guidance for Alpha's existing recovery engine.

---

## 52. Alpha graph-aware skill generation

When Alpha dynamically creates a skill:

```text
new skill
    ↓
skill definition
    ↓
tools required
    ↓
MCPs required
    ↓
packages required
    ↓
projects where useful
    ↓
agents capable of using it
```

Represent those relationships in the graph.

Then Alpha can discover:

```text
"Who can use this skill?"
"Which project needs it?"
"What MCP does it require?"
"What packages are missing?"
```

---

## 53. Alpha dynamic agent creation

When Alpha creates an agent profile:

```text
Agent Profile
  ├── Soul
  ├── Instructions
  ├── Model
  ├── Skills
  ├── Tools
  ├── MCPs
  ├── Memory namespace
  ├── Project scope
  ├── Parent agent
  └── Assigned tasks
```

Add these as graph relations.

This does not make Graphify the source of truth for the agent registry. Alpha's registry remains authoritative; the graph becomes a queryable relationship index.

---

## 54. Alpha multi-agent collaboration graph

Example:

```text
Task T123
  │
  ├── assigned_to → Agent A
  ├── depends_on → Task T122
  ├── uses_skill → Coding
  ├── uses_tool → Git
  ├── touches → file X
  ├── affects → module Y
  ├── creates → artifact Z
  └── reviewed_by → Agent B
```

This lets Alpha answer:

- What is the current task context?
- What other agents touched related components?
- Which artifacts were created?
- Which previous task has a similar dependency neighborhood?

---

## 55. Alpha lesson graph

Use relations like:

```text
execution → produced → observation
observation → supports → lesson
lesson → applies_to → project/component
lesson → derived_from → task
lesson → supersedes → older_lesson
lesson → contradicted_by → observation
```

Graphify's documented reflection/learning overlay makes this a particularly relevant design reference. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

---

## 56. Graphify benchmark interpretation for Alpha

Graphify reports:

| Suite | Reported graphify result |
|---|---:|
| LOCOMO QA accuracy | 45.3% |
| LOCOMO recall@10 | 0.497 |
| LongMemEval-S QA accuracy | 76% |
| Graph build LLM credits | 0 for the graph build |

The benchmark document says the tests used the same model/budget conditions across compared systems, and reports judge validation. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/BENCHMARKS.md)

### How Alpha should use this information

Treat it as:

- evidence that the approach is worth testing;
- evidence that graph retrieval can be competitive for memory/code tasks;
- a starting point for your own benchmark.

Do not treat it as:

- proof that Graphify will outperform every RAG/memory system on Alpha's workload;
- a guarantee for local small models;
- a direct measure of Alpha's final memory quality.

### Alpha benchmark suite

Build a benchmark containing:

```text
1. code retrieval
2. dependency tracing
3. bug localization
4. architecture QA
5. cross-session memory
6. agent/tool discovery
7. PR impact
8. stale-memory detection
9. lesson retrieval
10. research relationship retrieval
```

---

## 57. Performance strategy for Alpha

Use layered graphs.

### Per-project graph

Fast local access.

### Global graph

For cross-project questions only.

### Cached summaries

Precompute:

- communities
- god nodes
- project summaries
- common paths
- frequently queried concepts

### Progressive retrieval

Never traverse more of the graph than required.

### Incremental refresh

Use source hashes and change manifests.

### Background refresh

Graphify already supports watch/update concepts; Alpha can schedule low-priority graph maintenance in its background worker. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

---

## 58. Recommended Alpha background jobs

```text
GRAPH_SYNC_FAST
  git changes / source modifications

GRAPH_SYNC_DOCS
  documentation changes

GRAPH_INDEX_REBUILD
  periodic full consistency rebuild

GRAPH_LESSON_REFLECT
  update learning overlay

GRAPH_GLOBAL_MERGE
  refresh cross-project graph

GRAPH_HEALTH_CHECK
  validate graph integrity

GRAPH_STALENESS_SCAN
  mark source-derived facts stale
```

These jobs should have resource limits and lower priority than active user tasks.

---

## 59. Graph health checks for Alpha

Implement:

```text
- graph file readable
- node count unexpectedly dropped
- edge count unexpectedly dropped
- duplicate IDs
- orphan nodes
- invalid source references
- broken project paths
- stale snapshots
- missing source files
- contradictory relationships
- excessive graph growth
- suspicious external source ingestion
```

Graphify's own architecture includes validation, security validation, cached extraction, and graph-path validation. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md)

---

## 60. Failure handling rules

Alpha must not silently fall back from graph retrieval.

### Good

```text
Graphify unavailable
    ↓
state explicitly:
"Graph context unavailable"
    ↓
choose allowed fallback policy
    ↓
record degraded mode
```

### Bad

```text
Graphify failed
    ↓
quietly read random files
    ↓
answer as if graph context was available
```

This aligns with Alpha's requirement for transparent tool/fallback behavior.

---

## 61. Suggested Alpha graph events

Create an internal event bus.

```text
GraphBuildStarted
GraphBuildCompleted
GraphBuildFailed
GraphUpdated
GraphDiffCreated
GraphQueryStarted
GraphQueryCompleted
GraphQueryFailed
GraphNodeChanged
GraphEdgeChanged
GraphLessonAdded
GraphLessonInvalidated
GraphSnapshotCreated
```

This integrates naturally with Alpha's 24/7 monitoring and self-recovery system.

---

## 62. Suggested Graphify plugin package for Alpha

```text
plugins/graphify/
├── manifest.json
├── README.md
├── config.schema.json
├── adapters/
│   ├── mcp.ts
│   └── process.ts
├── tools/
│   ├── query.ts
│   ├── explain.ts
│   ├── path.ts
│   ├── impact.ts
│   ├── diff.ts
│   ├── refresh.ts
│   └── lessons.ts
├── workers/
│   ├── sync.ts
│   ├── watch.ts
│   └── reflect.ts
└── policies/
    ├── privacy.ts
    └── permissions.ts
```

---

## 63. Suggested configuration

```yaml
knowledgeGraph:
  enabled: true
  provider: graphify

  projects:
    - id: alpha
      path: ./
      mode: code+docs
      autoUpdate: true

  retrieval:
    maxHops: 3
    maxNodes: 100
    includeEvidence: true
    includeAmbiguous: true
    requireSourceVerification: true

  privacy:
    mode: STRICT_LOCAL
    allowCloudSemanticExtraction: false

  mcp:
    transport: stdio

  ollama:
    enabled: true
    model: ${GRAPHIFY_OLLAMA_MODEL}

  learning:
    enabled: true
    reflectOnSuccessfulTasks: true
    markStaleOnSourceChange: true
```

---

## 64. Recommended first implementation sequence

### Phase 1 — proof of concept

```text
Install Graphify
↓
Run it on Alpha repository
↓
Inspect graph.json
↓
Try query/path/explain
↓
Serve through MCP
↓
Connect Alpha
```

### Phase 2 — project intelligence

```text
GraphContextPlanner
Graph-aware coding agent
Graph-aware reviewer
Graph-aware documentation agent
```

### Phase 3 — memory

```text
lesson extraction
provenance
memory graph
staleness
reflection
```

### Phase 4 — multi-agent

```text
agent/skill/tool/MCP relationships
shared graph
cross-agent discovery
```

### Phase 5 — multi-project

```text
global graph
project registry
cross-project retrieval
```

### Phase 6 — RSI

```text
graph diff
architecture drift
change impact
self-review
automatic improvement proposals
```

---

## 65. What I would build into Alpha first

### Tier 1 — implement now

```text
Graphify MCP adapter
Project graph manager
Graph query tool
Graph path tool
Graph explain tool
Graph refresh worker
Evidence/provenance wrapper
Graph-aware coding planner
```

### Tier 2 — next

```text
Graph diff
Impact analysis
PR review integration
Subagent context scoping
Global graph
```

### Tier 3

```text
Agent/skill/tool/MCP graph
Lesson graph
Self-learning overlay
Research graph
Cross-project relationships
```

### Tier 4

```text
Neo4j/FalkorDB option
Distributed graph server
Advanced graph analytics
Graph-based autonomous architecture review
```

---

## 66. What should remain outside Graphify

Keep these systems independent:

```text
Agent state DB
Task DB
Event store
Telemetry
Secrets
Credential manager
File permissions
Chat transcript archive
Object/blob store
Realtime messaging
Browser sessions
Workflow executor
```

The graph can reference them, but should not replace them.

---

## 67. Suggested relationship vocabulary for Alpha

### Code

```text
imports
calls
references
inherits
implements
uses
configures
```

### Documentation

```text
explains
documents
justifies
supersedes
references
```

### Agent system

```text
owns
reports_to
member_of
assigned_to
uses_skill
uses_tool
uses_mcp
can_handle
```

### Tasks

```text
depends_on
blocks
affects
creates
reviews
verified_by
```

### Memory

```text
supports
contradicts
derived_from
observed_in
applies_to
supersedes
verified_by
```

### Research

```text
cites
implements
extends
validates
contradicts
inspired_by
```

---

## 68. Graph-aware Alpha answer format

For technical questions, Alpha could internally construct:

```text
Answer Context
───────────────
Question
Relevant graph nodes
Relevant paths
Source files
Evidence types
Confidence
Recent changes
Potential contradictions
```

Then the LLM answers from this compact evidence pack.

This is preferable to injecting a giant unstructured memory dump.

---

## 69. Example end-to-end Alpha workflow

User:

```text
"Add a new local memory backend to Alpha."
```

### Step 1 — classify

```text
Type = coding + architecture + configuration
```

### Step 2 — graph lookup

```text
Find memory abstraction
Find current backends
Find agent memory consumers
Find tests
Find docs
Find configuration
```

### Step 3 — plan

```text
1. Add backend interface
2. Implement local provider
3. Register provider
4. Add config
5. Add tests
6. Add docs
```

### Step 4 — subagents

```text
Agent A → interface
Agent B → implementation
Agent C → tests
Agent D → docs
```

### Step 5 — merge

```text
graph-aware conflict detection
```

### Step 6 — review

```text
graph diff
new dependencies
changed communities
```

### Step 7 — validate

```text
tests
lint
type check
runtime test
```

### Step 8 — update

```text
Graphify update
lesson extraction
architecture snapshot
```

### Step 9 — final answer

```text
implemented
verified
changed graph topology
remaining risks
```

---

## 70. Recommended Alpha command layer

Expose user-facing commands such as:

```text
/graph
/graph query <question>
/graph path <A> <B>
/graph explain <node>
/graph impact <node>
/graph update
/graph diff
/graph stats
/graph health
/graph lessons
/graph projects
```

And Alpha slash commands:

```text
/analyze-project
/understand-codebase
/trace-dependency
/impact-change
/review-architecture
/find-related-work
/find-agent-capability
```

These can internally route to Graphify.

---

## 71. Recommended dashboard

Alpha system monitor could show:

```text
PROJECT GRAPH
──────────────
Nodes:             N
Edges:             N
Communities:       N
Last update:       timestamp
Stale nodes:       N
Ambiguous edges:   N
Cycles:            N
God nodes:         N
Graph size:        N MB
Health:            OK
```

### Agent graph

```text
Agents:            N
Skills:            N
Tools:             N
MCP servers:       N
Relationships:     N
```

### Learning graph

```text
Lessons:           N
Preferred:         N
Tentative:         N
Contested:         N
Stale:             N
```

---

## 72. Key limitation: Graphify is not the complete "memory brain"

This is the most important architecture warning.

Graphify is strongest when the information can be expressed as a **relationship graph**.

Alpha still needs other memory representations:

```text
Working memory
Episodic memory
Semantic memory
Procedural memory
Preference memory
Temporal memory
Spatial/environment memory
Resource/file memory
Raw transcript memory
Vector/semantic retrieval
Graph memory
```

Graphify should be the **graph layer** in this larger memory architecture.

---

## 73. Key limitation: semantic extraction may require an LLM

For code, Graphify uses local AST parsing.

For some non-code artifacts, the current privacy documentation says semantic extraction uses an AI assistant/model or configured backend. Ollama can be used for local inference. [Source](https://github.com/Graphify-Labs/graphify/blob/v8/README.md)

For Alpha's zero-cost target:

```text
code → local tree-sitter
pdf/docs → local Ollama when practical
video/audio → local transcription where hardware allows
cloud LLM → optional
```

The local semantic workload may still be significant on an 8 GB RAM Windows machine, so Alpha should schedule it as a background job and avoid building huge multimodal graphs continuously.

---

## 74. Key limitation: graph maintenance

A graph can become stale.

Alpha must therefore track:

```text
source hash
extraction version
graph version
generated timestamp
model/backend version
schema version
```

Recommended node metadata:

```json
{
  "sourceHash": "...",
  "extractorVersion": "...",
  "graphSchemaVersion": "...",
  "generatedAt": "..."
}
```

---

## 75. Key limitation: inferred relationships can be wrong

The `INFERRED` and `AMBIGUOUS` distinction is therefore important.

Alpha should never silently use an ambiguous relationship for a high-impact action.

Recommended policy:

```text
EXTRACTED
  can support normal reasoning

INFERRED
  can support hypothesis / planning

AMBIGUOUS
  must trigger verification when consequential
```

---

## 76. Key limitation: graph topology is not runtime truth

A source graph can tell Alpha:

```text
A calls B
```

It cannot by itself prove:

```text
A actually called B during this runtime
```

For runtime truth, Alpha needs:

- logs
- traces
- tool execution records
- metrics
- tests
- runtime instrumentation

Therefore combine:

```text
static graph + runtime evidence
```

---

## 77. Key limitation: Graphify is a Python component

Alpha can still integrate it cleanly through MCP or a subprocess/sidecar.

Recommended boundary:

```text
TypeScript/Node Alpha
         │
         │ MCP
         ▼
Python Graphify service
```

This avoids forcing Python into every Alpha core process.

---

## 78. Key limitation: large shared graphs need careful architecture

The repository supports a shared HTTP server and optional external graph databases, but Alpha should benchmark its own workloads before centralizing everything.

For large deployments:

```text
per-project graphs
       ↓
global registry
       ↓
selective cross-project graph
```

rather than one permanently giant graph containing every artifact from every agent.

---

## 79. Graphify vs simple RAG for Alpha

| Problem | Simple RAG | Graphify-style graph |
|---|---|---|
| Find similar text | Strong | Not the primary purpose |
| Trace dependencies | Weak | Strong |
| Call/import relationships | Weak | Strong |
| Explain architecture | Medium | Strong |
| Source provenance | Medium | Strong |
| Community/subsystem structure | Weak | Strong |
| Path between concepts | Weak | Strong |
| Cross-file source relationships | Medium | Strong |
| Raw chat retrieval | Strong | Medium/needs adaptation |
| High-frequency events | Strong in event stores | Poor fit |
| Entity relationships | Medium | Strong |
| Code understanding | Medium | Strong |
| Multimodal corpus | Strong depending on RAG stack | Supported, but semantic extraction may need model/backend |

Alpha should not choose one and discard the other.

Use:

```text
Graph + lexical + vector + source verification
```

---

## 80. Graphify vs traditional graph database

Graphify is not just a database.

It includes an extraction/analysis pipeline built around local code parsing and project ingestion.

A graph DB such as Neo4j/FalkorDB is primarily a persistent graph storage/query layer.

For Alpha:

```text
Graphify = extraction + normalization + graph analysis + MCP
Graph DB  = optional persistent/scalable graph backend
```

This is why the combination can be useful rather than mutually exclusive.

---

## 81. Suggested production architecture

```text
                         ALPHA DESKTOP / SERVER
                                  │
               ┌──────────────────┼──────────────────┐
               │                  │                  │
           Agent Runtime      Memory Router      Task Engine
               │                  │                  │
               │            ┌─────┴─────┐            │
               │            │           │            │
               │          Graph       Vector       Event
               │            │           │            │
               │        Graphify      optional     Store
               │            │
               │       ┌────┴────┐
               │       │         │
               │   Project     Global
               │   graphs      graph
               │       │
               │     source
               │       │
               │  code/docs/sql
               │
               └─────────────────────────────────────┘
```

---

## 82. Recommended plugin API

Alpha plugin manifest idea:

```json
{
  "id": "graphify",
  "name": "Graphify Knowledge Graph",
  "version": "1.0",
  "type": "knowledge-provider",
  "runtime": "python-sidecar",
  "transport": "mcp",
  "capabilities": [
    "code-graph",
    "document-graph",
    "dependency-analysis",
    "graph-query",
    "path-analysis",
    "impact-analysis",
    "graph-diff",
    "project-indexing",
    "lesson-reflection"
  ]
}
```

---

## 83. Alpha graph ingestion policy

Not every file should become a graph node.

Use tiers:

```text
TIER 1 — always index
  source code
  configs
  package manifests
  SQL schemas
  architecture docs

TIER 2 — optionally index
  PDFs
  office docs
  research notes
  diagrams

TIER 3 — selectively index
  meetings
  videos
  transcripts
  chat exports

TIER 4 — never index
  credentials
  secrets
  private tokens
  browser sessions
```

This keeps the graph useful and manageable.

---

## 84. Alpha graph snapshots

Maintain snapshots for structural diff:

```text
snapshot-2026-09-20
snapshot-2026-09-21
snapshot-2026-09-25
```

Each snapshot:

```text
node IDs
edge IDs
source hashes
community assignments
schema version
```

Then Alpha can ask:

```text
What changed in the architecture this week?
What changed after this PR?
What components became coupled?
What lessons should be re-verified?
```

---

## 85. Architecture drift detection

Create a desired architecture specification:

```yaml
rules:
  - from: frontend
    to: database
    allowed: false

  - from: agent-runtime
    to: secret-store
    allowed: true

  - from: tools
    to: ui
    allowed: false
```

Compare graph topology to rules.

If a new edge violates architecture:

```text
ArchitectureDriftDetected
```

Then trigger an Alpha review workflow.

This is a strong extension of Graphify's structural analysis approach.

---

## 86. Graph-driven autonomous research

Alpha can use a graph to avoid repeatedly researching the same territory.

```text
Question
 ↓
research graph lookup
 ↓
known concepts
known projects
known papers
known implementations
 ↓
identify missing edges
 ↓
search only missing evidence
 ↓
add verified relationships
```

This turns research from "search every time" into incremental knowledge acquisition.

---

## 87. Graph-driven agent capability discovery

Example:

```text
User: "Find an agent capable of database migration analysis."
```

Alpha:

```text
agent
 ↓
skill
 ↓
tool
 ↓
MCP
 ↓
DB technology
```

Then choose an available agent based on authoritative runtime capability data, not solely on graph inference.

The graph should be a discovery index; the runtime capability registry remains authoritative.

---

## 88. Graph-driven duplicate detection

Graph topology can help Alpha detect duplicated functionality.

Examples:

```text
Agent A and Agent B
  → same skills
  → same tools
  → same project scope
  → same code neighborhood
```

Or:

```text
Service A and Service B
  → similar dependency neighborhoods
  → overlapping responsibility docs
```

Alpha can then flag candidates for consolidation.

---

## 89. Graph-driven ownership mapping

Add:

```text
component → maintained_by → agent/team
component → documented_by → agent/team
component → reviewed_by → agent/team
```

Then Alpha can route tasks based on structural ownership.

Again, the graph should index ownership; the task/agent registry is the authority.

---

## 90. Graph-driven onboarding

When an Alpha agent joins a project:

```text
new agent
  ↓
project graph summary
  ↓
communities
  ↓
god nodes
  ↓
important paths
  ↓
architecture report
  ↓
docs/ADRs
  ↓
known lessons
```

This gives the agent a compact project orientation package before it starts modifying anything.

---

## 91. Alpha onboarding skill

Create an internal skill:

```text
/project-onboard
```

Workflow:

```text
1. detect project type
2. build/update graph
3. identify communities
4. identify major hubs
5. read architecture docs
6. inspect important paths
7. load known lessons
8. create project briefing
```

This can greatly improve first-run performance on unfamiliar repositories.

---

## 92. Alpha code-review briefing

Before a coding agent starts a review, automatically generate:

```text
Project architecture
Relevant community
Changed nodes
Incoming dependencies
Outgoing dependencies
Related docs
Related tests
Prior lessons
Potentially stale relationships
```

Then the review model receives a compact context rather than a huge repository dump.

---

## 93. Alpha incident-debugging briefing

For a runtime failure:

```text
error
 ↓
service/component
 ↓
Graphify neighborhood
 ↓
recent changes
 ↓
dependencies
 ↓
configuration references
 ↓
related tests/docs
 ↓
debug plan
```

This combines static architecture with runtime logs.

---

## 94. Alpha memory conflict detection

Graph representation makes contradictions explicit.

Example:

```text
Memory A:
local backend = "sqlite"

Memory B:
local backend = "postgres"
```

Represent:

```text
memory_A CONTRADICTS memory_B
```

Then Alpha can request fresh evidence rather than choosing one silently.

---

## 95. Alpha confidence policy

Suggested confidence policy:

```text
1.00  direct/extracted
0.85+ strongly supported
0.65–0.84 plausible inferred
0.40–0.64 weak inference
<0.40 hypothesis only
```

These thresholds are Alpha design suggestions, not Graphify defaults.

Use separate evidence labels from numeric confidence so that confidence does not erase provenance.

---

## 96. Alpha graph query caching

Cache stable query results with source fingerprints:

```text
query hash
+ graph snapshot ID
+ project ID
+ policy ID
```

Invalidate when:

```text
graph snapshot changes
source changes
permissions change
memory policy changes
```

This should reduce repeated graph traversal in long-running Alpha operation.

---

## 97. Alpha graph security boundaries

Use these boundaries:

```text
User
 ↓
Alpha authorization
 ↓
Graph query policy
 ↓
Project ACL
 ↓
Graphify
 ↓
source nodes
```

Do not expose raw graph files to arbitrary agents without Alpha-level authorization.

A graph can leak sensitive structure even if source contents are hidden.

---

## 98. Recommended observability

Track:

```text
graph_query_latency
nodes_returned
edges_returned
source_verification_rate
ambiguous_edge_rate
stale_node_rate
graph_refresh_duration
graph_build_failures
query_cache_hit_rate
graph_size_bytes
project_count
memory_relations_count
```

This fits Alpha's existing system-monitor concept.

---

## 99. Recommended tests for the Alpha integration

### Contract tests

```text
query returns valid schema
path returns ordered edges
node lookup works
MCP unavailable is explicit
refresh is idempotent
```

### Accuracy tests

```text
known import relationship
known call relationship
known documentation link
known schema relationship
known impact path
```

### Reliability tests

```text
corrupted graph
missing source file
partial rebuild
process crash
restart recovery
large project
```

### Security tests

```text
path traversal
malicious node labels
prompt injection corpus
secret file exclusion
HTTP authentication
cross-project isolation
```

---

## 100. Suggested Alpha acceptance criteria

Graphify integration is ready for Alpha when:

```text
[ ] Alpha can build/update a project graph
[ ] Alpha can query graph via MCP
[ ] Alpha can explain/path nodes
[ ] Alpha can scope graph context for agents
[ ] Alpha preserves source provenance
[ ] Alpha distinguishes extracted/inferred/ambiguous
[ ] Alpha can detect stale graph context
[ ] Alpha can perform graph diff after changes
[ ] Alpha logs degraded-mode failures
[ ] Alpha respects file exclusion policy
[ ] Alpha can run fully local for code analysis
[ ] Alpha supports optional Ollama semantic extraction
[ ] Alpha can maintain per-project graph namespaces
[ ] Alpha can add a global/cross-project graph later
[ ] Alpha has no hard dependency on Graphify's external SaaS
```

---

## 101. Strong architectural pattern for Alpha

The key pattern to adopt is:

> **Graphify should be a provider behind Alpha's Knowledge Graph interface.**

That gives Alpha:

```text
Graphify today
Neo4j tomorrow
FalkorDB later
In-memory test provider
Another graph engine in future
```

without changing the agent runtime.

---

## 102. Recommended implementation diagram

```text
                       ┌──────────────────────────┐
                       │          ALPHA           │
                       │                          │
                       │ Agent Runtime            │
                       │ Planner                  │
                       │ Memory Router            │
                       │ Tool Router              │
                       │ Swarm/Groups             │
                       │ RSI / Self-Review        │
                       └────────────┬─────────────┘
                                    │
                         KnowledgeGraphProvider
                                    │
                     ┌──────────────┼──────────────┐
                     │              │              │
                 Graphify MCP   Vector/RAG     Event Store
                     │              │              │
                     │              │              │
                 graph.json      embeddings     episodes
                     │
          ┌──────────┼───────────────┐
          │          │               │
        Code        Docs           Schemas
          │          │               │
          └──────────┼───────────────┘
                     │
                Alpha Projects
```

---

## 103. Final assessment for Alpha

### Overall relevance

Graphify is directly relevant to Alpha's architecture because Alpha needs a way to understand and connect:

- large codebases
- documents
- projects
- schemas
- agents
- skills
- tools
- MCPs
- lessons
- changes

Graphify is particularly strong for **structural knowledge**.

### Best role

```text
GRAPH INTELLIGENCE LAYER
```

### Best first integration

```text
Graphify + MCP + per-project graph + Alpha KnowledgeGraphProvider
```

### Best local setup

```text
Windows
 + Graphify
 + Ollama optional
 + Alpha
```

### Best long-term setup

```text
Alpha
  ├── hybrid memory router
  ├── Graphify/graph backend
  ├── vector retrieval
  ├── event memory
  ├── project registry
  ├── agent registry
  ├── task DB
  └── provenance/verification layer
```

---

## 104. Practical recommendation

**Use Graphify in Alpha rather than rewriting the same concept from scratch.**

The first useful milestone is not to fork Graphify deeply. Integrate it as a sidecar/MCP provider, build Alpha's `KnowledgeGraphProvider` abstraction, and prove three workflows:

```text
1. codebase question → graph query → source verification
2. coding task → graph context → subagent → graph diff
3. completed task → lesson → graph memory → later retrieval
```

Once those work reliably, extend the graph to:

```text
agents
skills
tools
MCPs
projects
tasks
research
lessons
```

That creates a much stronger architecture than using Graphify as only a `/graphify` developer command.

---

## 105. What to copy from Graphify into Alpha's design philosophy

The most valuable ideas are:

```text
LOCAL-FIRST

GRAPH-FIRST FOR RELATIONSHIP QUESTIONS

SOURCE PROVENANCE

EXTRACTED vs INFERRED vs AMBIGUOUS

SCOPED RETRIEVAL

INCREMENTAL UPDATES

GRAPH DIFF

COMMUNITY DETECTION

IMPACT ANALYSIS

REFLECTION / LESSONS

MCP EXPOSURE

CROSS-PROJECT GRAPH

RE-VERIFICATION AFTER SOURCE CHANGES
```

These ideas fit Alpha's long-running, tool-using, multi-agent and recursive-improvement goals extremely well.

---

## 106. Source verification list

The following official repository materials were inspected for this report:

1. Graphify main README — capabilities, supported clients, commands, file types, MCP, global graph, PR tools, privacy, Ollama, installation.
   - https://github.com/Graphify-Labs/graphify
   - https://github.com/Graphify-Labs/graphify/blob/v8/README.md

2. Graphify architecture documentation — pipeline, modules, extraction schema, confidence labels, MCP server, watch/update architecture.
   - https://github.com/Graphify-Labs/graphify/blob/v8/ARCHITECTURE.md

3. Graphify benchmarks — LOCOMO, LongMemEval-S, code-intelligence benchmark methodology and cost claims.
   - https://github.com/Graphify-Labs/graphify/blob/v8/BENCHMARKS.md

4. Graphify security policy — threat model, SSRF/path traversal/XSS/prompt-injection mitigations, network defaults.
   - https://github.com/Graphify-Labs/graphify/blob/v8/SECURITY.md

5. Package metadata — current inspected version, Python requirement, dependencies, optional backends.
   - https://github.com/Graphify-Labs/graphify/blob/v8/pyproject.toml

6. License files — Apache-2.0 and MIT license files currently present in the repository.
   - https://github.com/Graphify-Labs/graphify/blob/v8/LICENSE
   - https://github.com/Graphify-Labs/graphify/blob/v8/LICENSE-MIT
   - https://github.com/Graphify-Labs/graphify/blob/v8/NOTICE

---

## 107. Implementation starter checklist

```text
[ ] Pin Graphify version/commit
[ ] Add Graphify MCP sidecar plugin
[ ] Add KnowledgeGraphProvider interface
[ ] Add per-project graph registry
[ ] Add graph query tool
[ ] Add node/path/explain tools
[ ] Add graph-aware planner
[ ] Add graph-aware coding agent context
[ ] Add graph diff reviewer
[ ] Add source provenance wrapper
[ ] Add extracted/inferred/ambiguous handling
[ ] Add .graphifyignore management
[ ] Add secret-file exclusions
[ ] Add graph refresh worker
[ ] Add health/metrics
[ ] Add local Ollama mode
[ ] Add lessons/reflection integration
[ ] Add cross-project global graph
[ ] Add agent/skill/tool/MCP topology
[ ] Add architecture drift checks
[ ] Add benchmark suite
[ ] Add restart/self-healing for Graphify sidecar
```

---

## 108. Bottom line

**Graphify is useful for Alpha primarily as a structural knowledge and graph-memory engine.**

Its strongest contribution is not simply "making a graph". The valuable part is the combination of:

```text
AST extraction
+ source relationships
+ confidence labels
+ graph queries
+ path tracing
+ community detection
+ incremental updates
+ graph diffs
+ MCP
+ lessons/reflection
+ cross-project graph concepts
```

Use those capabilities to make Alpha's agents more context-aware, evidence-aware, architecture-aware, and change-aware.

Keep Alpha's transactional state, conversations, runtime telemetry, secrets, task scheduler, communication system, and broader memory stack separate.

The result should be:

```text
             ALPHA
               │
        ┌──────┴──────┐
        │             │
   Reasoning      Knowledge
        │             │
        │        ┌────┴────┐
        │        │ Graph   │
        │        │Graphify │
        │        └────┬────┘
        │             │
        │       code/docs/schema
        │             │
        └───── evidence/context ─────┐
                                     ↓
                                  ACTION
```

This is a strong architectural fit for Alpha's long-running, local-first, multi-agent, self-review, and recursive-improvement goals.
