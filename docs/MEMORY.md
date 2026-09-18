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

### MemorySection Component
- **Search tab**: Semantic search across all memory
- **Graph tab**: Knowledge graph visualization
- **Episodes tab**: Skill experience replay
- **Cognitive tab**: Concept browser (per-owner)
- **Settings tab**: Retention, providers, privacy

### Memory Hooks
```typescript
// lib/memory.ts
export function useMemorySearch(query: string) {
  // Reactive search with debouncing
}

export function useMemoryStats() {
  // Periodic stats refresh
}

export function useCognitiveMemory(owner: string) {
  // Owner-scoped cognitive state
}
```

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
```bash
# 1. Export from old provider
python scripts/memory_export.py --provider sqlite --output backup.json

# 2. Configure new provider in config.yaml
# 3. Import to new provider
python scripts/memory_import.py --provider pgvector --input backup.json

# 4. Verify
curl /api/memory/stats
```

### Schema Updates
```bash
# Run migrations
cd backend && make migrate-upgrade

# Cognitive memory: manual (atomic snapshots)
# Knowledge graph: Cypher migrations
```