# Memory Systems Documentation

## Overview

Alpha implements a multi-layered memory architecture designed for different access patterns, retention requirements, and semantic richness.

## Memory Architecture

```
┌─────────────────────────────────────────────────────────────────┐
                        Memory Layers
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Short-term (In-Thread)                     │   │
│  │  • Conversation history                                 │   │
│  │  • Tool calls & results                                 │   │
│  │  • Checkpoints (every step)                             │   │
│  │  • TTL: Thread lifetime                                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Long-term (Persistent)                     │   │
│  │  • Vector embeddings (semantic search)                  │   │
│  │  • Key-value facts                                      │   │
│  │  • Episodic memories                                    │   │
│  │  • TTL: Configurable (default: forever)                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Cognitive (Structured)                     │   │
│  │  • Per-owner, per-directory process cache               │   │
│  │  • Atomic fsync-backed snapshots                        │   │
│  │  • Semantic concepts & relationships                    │   │
│  │  • TTL: Per-owner config                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Scenario-conditioned recall

The lead-agent prompt can optionally classify the request that triggers a
memory injection and select which enabled typed-memory surfaces to recall. Set
`memory.scenarios.enabled` to `true` to enable deterministic routing; it
remains off by default. The current dynamic-context lifecycle injects this
memory snapshot on the first applicable turn per thread/date, so later
same-day turns reuse that snapshot rather than reclassifying. The host routes
only concrete surfaces whose individual gates are enabled, records the
decision under the resolved owner and runtime thread ID, and does not persist
the raw request text. A weak or ambiguous classification falls back to the
general scenario. The prompt integration does not construct the optional model
classifier or preserve a frozen routing plan across prompt calls. See
[`MEMORY_TYPES.md`](MEMORY_TYPES.md) for coverage and
[`MEMORY_RUNTIME_AUDIT.md`](MEMORY_RUNTIME_AUDIT.md) for verification status.

## Coding-task repository structure memory

The existing opt-in `memory.codebase` indexer is connected to the real
lead-agent prompt path. When the master memory/injection gates and
`memory.codebase.enabled` are on, the dynamic context middleware accepts a
`repo_root` only from trusted internal runtime context; external clients cannot
smuggle a host path through request context or checkpoint configuration. If no
trusted root is present, Alpha does not search the filesystem or guess from the
process working directory. A bounded standard-library parser indexes the
explicit workspace without importing or executing project code. Refresh runs
in a worker thread, so it does not block the request event loop. The first
prompt schedules a refresh and can use only a previously published snapshot;
later prompts can recall the new snapshot. Retrieval selects query-matched
module summaries, symbol locations and dependency structure without adding
source text to the prompt. Snapshot IDs are opaque hashes of owner and resolved
root. The JSON store remains single-process state: it provides no
cross-process exactly-once guarantee or independent project ACL. The feature is
default-off and reads source files only within the trusted, explicitly supplied
workspace.

This design follows the distinction between thread state and cross-thread
long-term memory, and between semantic, episodic and procedural memories in the
[LangGraph memory guide](https://docs.langchain.com/oss/python/concepts/memory).
For coding agents, repository structure and dependency context are useful
retrieval units; prior execution traces can be linked to code entities as a
future extension, as explored in the [Remember Your Trace paper](https://arxiv.org/abs/2605.14563).
The current Alpha integration is a structural index, not a persistent task
trace store or an automatic learner.

## Short-term Memory (In-Thread)

### Structure
```python
ThreadState = {
    "thread_id": "uuid",
    "messages": [
        {
            "id": "msg-uuid",
            "role": "user|assistant|system|tool",
            "content": "Message content",
            "tool_calls": [{"id": "call-uuid", "name": "tool", "args": {}}],
            "tool_call_id": "call-uuid",  # For tool results
            "timestamp": "2026-09-17T10:00:00Z",
            "metadata": {}
        }
    ],
    "checkpoints": [
        {
            "id": "cp-uuid",
            "step": 5,
            "state": {...},  # Full agent state snapshot
            "timestamp": "2026-09-17T10:00:00Z"
        }
    ],
    "metadata": {
        "created_at": "2026-09-17T10:00:00Z",
        "updated_at": "2026-09-17T10:30:00Z",
        "token_count": 15000,
        "run_count": 3
    }
}
```

### Checkpointing
- **Automatic**: After every tool call and agent response
- **Manual**: Via `POST /api/threads/{id}/checkpoints`
- **Resume**: `POST /api/threads/{id}/runs/{run_id}/resume` with `checkpoint_id`
- **Storage**: Encrypted (AES-256-GCM) in database
- **Retention**: Configurable, default 30 days

### Context Window Management
```python
# Automatic summarization when approaching limit
if token_count > model_context_limit * 0.8:
    # Summarize oldest messages
    summary = await summarize(messages[:cutoff])
    # Replace with summary message
    messages = [summary] + messages[cutoff:]
```

## Long-term Memory

### Vector Store Backend

#### Supported Providers
| Provider | Use Case | Pros | Cons |
|----------|----------|------|------|
| SQLite + sqlite-vec | Development | Zero config, embedded | Limited scale |
| PostgreSQL + pgvector | Production | ACID, SQL integration | Requires PG |
| Chroma | High-scale | Purpose-built, fast | Separate service |
| Qdrant | High-scale | Rust, distributed | Separate service |

#### Configuration
```yaml
memory:
  long_term:
    enabled: true
    provider: "pgvector"  # sqlite, pgvector, chroma, qdrant
    collection: "alpha_memory"
    embedding_model: "text-embedding-3-small"
    embedding_dim: 1536
    # Provider-specific
    sqlite:
      path: "./data/memory.db"
    pgvector:
      connection_string: "${DATABASE_URL}"
    chroma:
      host: "localhost"
      port: 8000
    qdrant:
      url: "http://localhost:6333"
```

### Memory Entities

#### Documents
```python
Document = {
    "id": "doc-uuid",
    "thread_id": "thread-uuid",  # Optional, for thread-scoped
    "project_id": "proj-uuid",   # Optional, for project-scoped
    "owner_id": "user-uuid",     # Owner for access control
    "content": "Text content to embed",
    "metadata": {
        "source": "thread|file|skill|manual",
        "tags": ["tag1", "tag2"],
        "importance": 0.8,  # 0-1, affects retrieval ranking
        "expires_at": "2027-09-17T10:00:00Z"  # Optional TTL
    },
    "embedding": [0.1, 0.2, ...],  # Vector (stored separately)
    "created_at": "2026-09-17T10:00:00Z"
}
```

#### Facts (Key-Value)
```python
Fact = {
    "id": "fact-uuid",
    "key": "user.preference.theme",
    "value": "dark",
    "confidence": 0.95,
    "source": "explicit|inferred|learned",
    "context": {"thread_id": "..."},
    "created_at": "2026-09-17T10:00:00Z",
    "updated_at": "2026-09-17T10:00:00Z"
}
```

### Memory Operations

#### Store Memory
```http
POST /api/memory/store
{
    "content": "User prefers dark mode",
    "type": "fact",  # or "document", "episode"
    "metadata": {
        "tags": ["preference", "ui"],
        "importance": 0.9
    },
    "thread_id": "optional-thread-id",
    "project_id: "optional-project-id"
}
```

#### Search Memory
```http
POST /api/memory/search
{
    "query": "user preferences",
    "limit": 10,
    "threshold": 0.7,  # Similarity threshold
    "filters": {
        "type": "fact",
        "tags": ["preference"],
        "owner_id": "user-uuid",
        "project_id": "proj-uuid",
        "date_range": {"start": "2026-01-01", "end": "2026-12-31"}
    },
    "include_metadata": true
}
```

#### Get Memory Stats
```http
GET /api/memory/stats

Response:
{
    "total_documents": 15000,
    "total_facts": 3200,
    "total_episodes": 450,
    "storage_size_mb": 520,
    "by_provider": {"pgvector": 15000},
    "by_owner": {"user-uuid": 5000}
}
```

### Retrieval Strategies

#### Semantic Search
```python
# Vector similarity search
results = await vector_store.search(
    query_embedding=embed(query),
    k=10,
    filter=metadata_filter
)
```

#### Hybrid Search (Vector + Keyword)
```python
# Combine vector and BM25
vector_results = await vector_search(query, k=20)
keyword_results = await bm25_search(query, k=20)
# Reciprocal rank fusion
results = rrf_fusion(vector_results, keyword_results)
```

#### Contextual Retrieval
```python
# Include thread/project context in query
enhanced_query = f"""
Thread context: {thread_summary}
Project: {project_name}
Query: {user_query}
"""
results = await search(enhanced_query)
```

## Cognitive Memory

### Overview
Cognitive memory provides structured, per-owner knowledge representation with atomic persistence guarantees.

### Architecture
```
Cognitive Memory (per owner, per directory)
├── Process Cache (in-memory, locked)
│   ├── Concept nodes
│   ├── Relationships
│   └── Inference rules
├── Snapshot Store (disk)
│   ├── snapshot_001.json (fsync'd)
│   ├── snapshot_002.json (fsync'd)
│   └── ...
└── WAL (write-ahead log)
    └── wal_001.log
```

### Configuration
```yaml
memory:
  cognitive:
    enabled: true
    storage_dir: "./data/cognitive_memory"  # Per-owner subdirs created automatically
    snapshot_interval: 300  # seconds
    max_snapshots: 100
    wal_enabled: true
```

### Data Model

#### Concepts
```python
Concept = {
    "id": "concept-uuid",
    "name": "Python",
    "type": "language|framework|tool|concept|entity",
    "properties": {
        "paradigm": "multi-paradigm",
        "typing": "dynamic",
        "created": "1991"
    },
    "relationships": [
        {"type": "uses", "target": "concept-uuid-2", "strength": 0.8},
        {"type": "inspired_by", "target": "concept-uuid-3", "strength": 0.6}
    ],
    "embeddings": [0.1, 0.2, ...],  # For semantic similarity
    "confidence": 0.9,
    "source": "learned|explicit|inferred",
    "access_count": 42,
    "last_accessed": "2026-09-17T10:00:00Z"
}
```

#### Inference Rules
```python
Rule = {
    "id": "rule-uuid",
    "pattern": "If X uses Y and Y is a framework, then X is a developer",
    "formal": "uses(X,Y) ∧ framework(Y) → developer(X)",
    "confidence": 0.75,
    "applications": 10,
    "success_rate": 0.8
}
```

### Operations

#### Get/Set Cognitive State
```http
GET /api/memory/cognitive/{owner}
POST /api/memory/cognitive/{owner}
{
    "concepts": [...],
    "relationships": [...]
}
```

#### Query Concepts
```http
POST /api/memory/cognitive/{owner}/query
{
    "query": "programming languages",
    "limit": 10,
    "include_relationships": true
}
```

### Persistence Guarantees
- **Atomic snapshots**: fsync after write, crash-safe
- **Process cache**: Per-directory, lock-protected
- **WAL**: Durability between snapshots
- **Failure modes**: 
  - Missing owner → fail closed (no auto-create)
  - Corrupt snapshot → fail closed (manual recovery)
  - Lock contention → timeout with retry

## Episodic Memory

### Overview
Episodic memory stores experiences for skill learning and pattern recognition.

### Structure
```python
Episode = {
    "id": "episode-uuid",
    "skill_id": "skill-uuid",  # Associated skill
    "task": "Task description",
    "context": {...},  # Input context
    "actions": [       # Step-by-step actions
        {"tool": "search", "input": {...}, "output": {...}},
        {"tool": "analyze", "input": {...}, "output": {...}}
    ],
    "outcome": "success|failure|partial",
    "reward": 0.8,  # -1 to 1, for RL
    "metadata": {
        "duration_ms": 5000,
        "token_cost": 1500,
        "tools_used": ["search", "analyze"]
    },
    "created_at": "2026-09-17T10:00:00Z"
}
```

### Lifecycle
1. **Capture**: Automatic during skill execution
2. **Curate**: Skill curator reviews, tags quality
3. **Consolidate**: Pattern extraction during idle time
4. **Replay**: Used for few-shot prompting, training
5. **Archive**: Old episodes compressed

### Configuration
```yaml
memory:
  episodic:
    enabled: true
    max_episodes: 10000
    max_per_skill: 1000
    curation_interval: 3600  # seconds
    auto_archive_days: 90
```

## Knowledge Graph

### Overview
Enterprise knowledge graph for structured entity-relationship queries.

### Backend
- **Primary**: PostgreSQL with Apache AGE (Graph extension)
- **Alternative**: Neo4j, KuzuDB, FalkorDB

### Schema
```cypher
// Node types
(:Entity {id, name, type, properties, embedding})
(:Document {id, title, content, metadata})
(:Skill {id, name, version, tools})
(:Project {id, name, constitution})
(:User {id, name, preferences})

// Relationships
(:Entity)-[:MENTIONED_IN]->(:Document)
(:Entity)-[:RELATED_TO {strength}]->(:Entity)
(:Skill)-[:HAS_TOOL]->(:Tool)
(:Project)-[:HAS_MEMBER]->(:User)
(:User)-[:OWNS]->(:Project)
```

### Queries
```http
POST /api/knowledge/query
{
    "cypher": "MATCH (e:Entity)-[:RELATED_TO]->(e2) WHERE e.name = 'Python' RETURN e2 LIMIT 10",
    "parameters": {}
}

POST /api/knowledge/traverse
{
    "start_node": "entity-uuid",
    "max_depth": 2,
    "relationship_types": ["RELATED_TO", "MENTIONED_IN"],
    "limit": 50
}
```

### Graph Statistics
```http
GET /api/knowledge/stats

Response:
{
    "nodes": 50000,
    "relationships": 120000,
    "by_type": {
        "Entity": 30000,
        "Document": 15000,
        "Skill": 50
    },
    "by_relationship": {
        "MENTIONED_IN": 80000,
        "RELATED_TO": 40000
    }
}
```

## Memory API Reference

### L1 credential protection

New L1 records pass through `MemoryRecord.create()`, which redacts recognized
credential patterns in content and metadata before deriving the record ID. The
durable `L1RecordStore.put_records()` boundary checks again and refuses
unredacted records that bypass the constructor. A refusal does not include the
matched secret in its exception text. Redaction reports store only the count and
pattern families replaced.

This is heuristic protection, not a guarantee that every secret format is
recognized. Existing snapshots are not retroactively scrubbed by this write
guard. The supported patterns and coverage limits are documented in
`backend/packages/harness/alpha/security/memory_redaction.py`; focused coverage
is in `backend/tests/test_memory_secret_filter.py`.

`GET /api/memory/status` includes an `l1_capture` projection for the authenticated
owner. It reads at most seven days of per-owner L1 generation logs and limits the
tail read to 128 KiB and 500 lines per day. The projection contains the latest
readable run timestamp/status and per-surface counters only; it never returns
memory text or record IDs. `disabled`, `no_recent_runs`, `no_readable_entries`,
`not_reported`, `unreadable`, and `degraded` are distinct states. This is owner/agent scope:
`CaptureJob` has no project ID, so this status does not claim project isolation.

The Memory overview renders this projection in the **Automatic L1 Capture** card.
It is diagnostic evidence from the local generation log, not a proof of successful
retrieval or cross-process exactly-once behavior.

The overview also renders the backend's **Automatic Memory Extraction** health
record: current extraction posture, model/source label, recent attempt/failure
times, and bounded success/failure counters. It omits raw provider error text and
owner/thread scope values. This health record is process-local; after a Gateway
restart it returns to `unknown` until an extraction result is observed. A
`degraded` result means background extraction failed even if the chat run itself
completed; it does not imply that existing stored facts are unavailable.

### Prospective reminders

Lead-agent middleware can capture explicit user requests phrased as “remind me to …”
or “don't let me forget to …” after a successful turn. Capture and prompt recall both
require `memory.enabled: true` and `memory.prospective.enabled: true`; prospective
memory is off by default. The middleware requires a resolved non-default owner and
a stable source message/run ID, which makes retries with that same source
idempotent. Capture also stamps a bounded due time: `alpha.memory.prospective.dates`
(stdlib-only, injectable clock) turns an unambiguous explicit phrase — relative
offsets (“in 30 minutes”), self-disambiguating clocks (meridiem-qualified or 24h
hours such as “at 5pm”, “at 21:30” — a bare “at 9:30” is refused), day cues
(“today”, “tomorrow”, “tonight”, part-of-day defaults), weekdays (“friday”, “next
friday”) and “next week”/“next month” — into the record's `due_at`, persisting
`due_phrase` and the stable `due_rule` name in the record metadata for audit.
Anything ambiguous (two conflicting day cues, two different clocks, a false match
inside an e-mail address) keeps the reminder undated rather than guessing a wrong
date. Capture still does not schedule notifications or deliver due-time reminders,
does not capture arbitrary commitments, and the parsed dates are unit-tested in
isolated scopes, not proven end to end on a live Gateway. The store is
owner/agent scoped and has no project scope. The
authenticated Gateway exposes `GET /api/memory/prospective` plus
`POST /api/memory/prospective/{item_id}/complete|cancel`; owner identity always
comes from the request context, never the body. Unsupported future store formats
return an explicit unavailable error and are preserved. `GET /api/memory/status`
includes content-free lifecycle counts, and the Memory UI's **Reminders** tab
lists records and supports completion/cancellation. This UI does not schedule or
deliver time-based notifications.

### Canonical memory-fabric deletion

Memory-fabric scope deletion and hard deletion return a disclosed
`format_unsupported` result if the scope file was written by a newer schema.
Candidate enumeration, just like the delete operation itself, refuses to
describe that scope as empty. The store file is left byte-for-byte intact so a
compatible build can read it later. Corruption-preservation failures are
reported separately; neither condition is a successful forget. These local
JSON writes remain process-local and do not provide cross-worker exactly-once
coordination.

### Core Endpoints
```
POST   /api/memory/store          # Store memory
POST   /api/memory/search         # Semantic search
GET    /api/memory/stats          # Statistics
DELETE /api/memory/{memory_id}    # Delete memory
PATCH  /api/memory/{memory_id}    # Update metadata
```

### Cognitive Memory
```
GET    /api/memory/cognitive/{owner}           # Get state
POST   /api/memory/cognitive/{owner}           # Set state
POST   /api/memory/cognitive/{owner}/query     # Query concepts
POST   /api/memory/cognitive/{owner}/infer     # Run inference
```

### Knowledge Graph
```
POST   /api/knowledge/query       # Cypher query
POST   /api/knowledge/traverse    # Graph traversal
GET    /api/knowledge/stats       # Statistics
POST   /api/knowledge/entities    # Create entity
POST   /api/knowledge/relationships  # Create relationship
```

### Episodic Memory
```
GET    /api/memory/episodes                    # List episodes
GET    /api/memory/episodes/{episode_id}       # Get episode
POST   /api/memory/episodes/{episode_id}/replay  # Replay episode
```

## Frontend Integration

### Memory workspace view (`MemorySection`)

The workspace **Memory** view (`frontend/src/components/sections/MemorySection.tsx`)
is the operator surface for memory state. It has six tabs:
**Overview & Tiers** (tiered status plus the health card below),
**Recall**, **Beliefs** (semantic),
**Skills** (procedural), **Working** (scratchpad/episodic) and **Facts**.
Every data block carries its own loading / empty / error state; one failed read
never blanks the other tabs.

The header reads `GET /api/memory/status` through `lib/memory.ts`:

- **on/off badge** — shows `gateway memory on`, `off`, or `state not reported`
  for `config.enabled` verbatim (`true` / `false` / absent are different claims).
- **extraction-health badge** — while the status read is in flight it says
  `extraction health: checking…`; after a successful read it becomes
  `extraction ok` (green) when the backend reports
  `health.status === "ok"`, `extraction degraded` (red) for `"degraded"`,
  `extraction disabled` (gray) for `"disabled"`, `extraction health not reported`
  when the payload has no `health` object (the backend hook is absent there), and
  any other status word — including `"unknown"`, which a fresh Gateway or an
  extraction backend with no determined model legitimately reports — rendered
  verbatim in amber as `extraction: unknown`.
- **status failure** — a separate line renders the Gateway's own reason
  (`Memory status is unavailable: …`) with `role="status"`; extraction health,
  counters and the on/off state are treated as unknown until a status read
  succeeds. The failure never degrades into a "healthy" or "empty" rendering.

### Extraction health card (`MemoryHealthCard`)

When the status read succeeds, the Overview tab renders an extraction-health card
from `status.health`:

- `consecutive_failures` / `total_failures` (failures) and `total_successes`
  (successes) — a count the server did not send renders as `not reported`, never `0`.
- `last_attempt_at` / `last_success_at` / `last_failure_at` timestamps via
  `relTime` with `not reported` when absent.
- `model` / `model_source` when reported, `Model: not reported` otherwise, and
  `Extraction: on / off / unknown` tri-state for `extraction_enabled`.
- `last_error` — shown only through the text the **server** already
  truncated and credential-masked in
  `backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/extraction_health.py`.
  The client adds no redaction and no mirror of health state other than this copy.
- the basis line at the bottom states that counters cover **this Gateway
  process since its last restart** (the record is process-local by design) and
  that the displayed error is server-masked.

### Client rules (`lib/memory.ts`)

`lib/memory.ts` exports plain async clients (`fetchMemoryStatus`,
`fetchCognitiveOverview`, `recallCognitiveMemory`, …) built on the shared
`get`/`send` transport. There are no React hooks in this client and no automatic
polling. `fetchMemoryStatus` maps the
`MemoryStatusEnvelope { config, health }` contract literally: absent optional
fields become `null`, a non-object `health` map degrades to `null`, unknown
status words pass through verbatim, and non-2xx responses reject with the
Gateway's reason rather than resolving to a placeholder.

## Configuration

### Complete Memory Config
```yaml
memory:
  # Short-term (in-thread)
  short_term:
    max_messages: 100
    auto_summarize: true
    summarize_threshold: 0.8
    checkpoint_interval: 1  # Every N steps
  
  # Long-term (vector)
  long_term:
    enabled: true
    provider: "pgvector"
    collection: "alpha_memory"
    embedding_model: "text-embedding-3-small"
    embedding_batch_size: 100
    auto_embed: true  # Embed on store
    similarity_threshold: 0.7
    max_results: 20
  
  # Cognitive (structured)
  cognitive:
    enabled: true
    storage_dir: "./data/cognitive_memory"
    snapshot_interval: 300
    max_snapshots: 100
    wal_enabled: true
    lock_timeout: 30
  
  # Episodic (experiences)
  episodic:
    enabled: true
    max_episodes: 10000
    max_per_skill: 1000
    curation_interval: 3600
    auto_archive_days: 90
    min_reward_for_keep: 0.3
  
  # Knowledge Graph
  knowledge_graph:
    enabled: true
    provider: "age"  # age, neo4j, kuzu
    auto_extract: true  # Extract entities from documents
    embedding_enabled: true
```

## Best Practices

### Memory Hygiene
1. **Tag consistently** - Use standardized tag taxonomy
2. **Set importance** - Critical info: 0.9+, Reference: 0.5+
3. **Use TTL** - Temporary info should expire
4. **Scope properly** - Thread vs project vs global
5. **Regular cleanup** - Archive old, low-importance memories

### Performance
1. **Batch embeddings** - Use `embedding_batch_size`
2. **Filter early** - Apply metadata filters before vector search
3. **Cache embeddings** - Reuse for repeated queries
4. **Limit results** - Default 10, max 50

### Privacy & Compliance
1. **Owner isolation** - Cognitive memory strictly per-owner
2. **Right to deletion** - `DELETE /api/memory/{id}` cascades
3. **No PII in embeddings** - Strip before embedding
4. **Audit access** - Log all memory operations

## Troubleshooting

### Search Not Returning Results
```bash
# Check embeddings generated
curl /api/memory/stats | jq '.by_provider'

# Verify embedding model
curl /api/models | jq '.[] | select(.name=="embedding")'

# Test direct search
curl -X POST /api/memory/search -d '{"query": "test", "limit": 5}'
```

### Cognitive Memory Errors
```bash
# Check storage directory permissions
ls -la data/cognitive_memory/

# Verify owner exists
curl /api/memory/cognitive/owner-id

# Check lock files
ls -la data/cognitive_memory/owner-id/*.lock
```

### Knowledge Graph Slow
```bash
# Check index usage
EXPLAIN ANALYZE MATCH (e:Entity) WHERE e.name = 'X' RETURN e

# Add indexes
CREATE INDEX ON :Entity(name);
CREATE INDEX ON :Entity(type);
```

### High Memory Usage
```bash
# Check what's consuming memory
curl /api/memory/stats

# Cleanup old memories
# - Archive episodes older than 90 days
# - Delete low-importance facts
# - Compress old snapshots
```

## Migration

### Provider Migration

There is no `scripts/memory_export.py` or `scripts/memory_import.py` in this
repository — no automated provider-to-provider memory transfer ships. Switch the
backend in `config.yaml` and let the subsystem read the configured store; see
[MEMORY.md](MEMORY.md) backend sections and `MEMORY_FABRIC_PLAN.md` for which
providers are wired.

```bash
# 1. Configure the new provider in config.yaml (memory.backend)
# 2. Restart the Gateway
# 3. Verify
curl /api/memory/stats
```

### Schema Updates
```bash
# There is no `make migrate-upgrade`. The Gateway runs `alembic upgrade head`
# automatically at startup via `bootstrap_schema`, so upgrades need no operator
# command. To AUTHOR a new revision after changing the ORM models:
cd backend && make migrate-rev MSG="add foo column to runs"

# Cognitive memory: manual (atomic snapshots)
# Knowledge graph: Cypher migrations
```
