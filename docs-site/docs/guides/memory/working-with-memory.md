---
sidebar_position: 1
title: Working with Memory
description: Persistent memory, semantic search, and cognitive memory
slug: /guides/memory/working-with-memory
---

# Working with Memory

Alpha provides a multi-layered memory system for persistent, searchable knowledge.

## Memory Architecture

| Layer | Technology | Purpose | Retention |
|-------|------------|---------|-----------|
| Working | In-memory | Active context | Session |
| Episodic | DeerMem + SQLite | Conversation history | Permanent |
| Semantic | DeerMem + embeddings | Semantic search | Permanent |
| Cognitive | Custom | Structured knowledge | Permanent |
| L1 | In-memory | Typed working memory | Configurable |

## Memory Layers

### Working Memory
- Active conversation context
- Cleared on session end
- In-memory only

### Episodic Memory (DeerMem)
- Full conversation history
- Persisted to SQLite
- Automatic summarization

### Semantic Memory
- Vector embeddings for semantic search
- Powered by DeerMem + embeddings
- Enables semantic search across conversations

### Cognitive Memory
- Structured knowledge extraction
- Entity/relationship extraction
- Cross-thread knowledge

### L1 Memory (Typed Working Memory)
- Typed working memory slots
- Schema-validated slots
- Configurable TTL

## Memory Tools

### Search Memory
```bash
/memory search "FastAPI best practices"
```

### Add to Memory
```bash
/memory add "FastAPI uses Pydantic for validation"
```

### Forget
```bash
/memory forget "outdated information"
```

### Inspect
```bash
/memory inspect
```

## Configuration

```yaml
memory:
  provider: "deermem"  # deermem, simple
  deermem:
    path: ".alpha/memory"
    embedding_model: "text-embedding-3-small"
    chunk_size: 1000
    chunk_overlap: 200
  l1:
    enabled: true
    max_entries: 1000
    ttl_hours: 24
```

## Memory Tools

### Search
```bash
/memory search "FastAPI best practices"
```

### Add
```bash
/memory add "FastAPI uses Pydantic for validation"
```

### Forget
```bash
/memory forget "outdated information"
```

### Inspect
```bash
/memory inspect
```

## Summarization

Auto-summarization triggers when context window reaches threshold:

```yaml
summarization:
  enabled: true
  model: "gpt-4"
  max_tokens: 4000
  trigger_threshold: 0.8
  preserve_recent: 10
```

## L1 Memory (Typed Working Memory)

Typed working memory with schema validation:

```yaml
memory:
  l1:
    enabled: true
    max_entries: 1000
    ttl_hours: 24
```

## Memory Tools

### Search
```bash
/memory search "FastAPI best practices"
```

### Add
```bash
/memory add "FastAPI uses Pydantic for validation"
```

### Forget
```bash
/memory forget "outdated information"
```

### Inspect
```bash
/memory inspect
```

## Configuration

```yaml
memory:
  provider: "deermem"  # deermem, simple
  deermem:
    path: ".alpha/memory"
    embedding_model: "text-embedding-3-small"
    chunk_size: 1000
    chunk_overlap: 200
  l1:
    enabled: true
    max_entries: 1000
    ttl_hours: 24
```

## Summarization

Auto-summarization triggers when context window reaches threshold:

```yaml
summarization:
  enabled: true
  model: "gpt-4"
  max_tokens: 4000
  trigger_threshold: 0.8
  preserve_recent: 10
```

## Cognitive Memory

Structured knowledge extraction:
- Entity extraction
- Relationship extraction
- Cross-thread knowledge synthesis

## Next Steps

- [Context Window Management](/guides/memory/context-window)
- [Summarization](/guides/memory/summarization)
- [Cognitive Memory](/guides/memory/cognitive-memory)