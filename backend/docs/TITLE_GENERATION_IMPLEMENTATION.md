# Automatic Title Generation Implementation Summary

## ✅ Completed Work

### 1. Core Implementation Files

#### [`packages/harness/agent_workspace/agents/thread_state.py`](../packages/harness/agent_workspace/agents/thread_state.py)
- ✅ Added `title: str | None = None` field to `ThreadState`

#### [`packages/harness/agent_workspace/config/title_config.py`](../packages/harness/agent_workspace/config/title_config.py) (New)
- ✅ Created `TitleConfig` configuration class
- ✅ Supports configuration options: `enabled`, `max_words`, `max_chars`, `model_name`, `prompt_template`
- ✅ Provided `get_title_config()` and `set_title_config()` functions
- ✅ Provided `load_title_config_from_dict()` to load configuration from file/dict

#### [`packages/harness/agent_workspace/agents/middlewares/title_middleware.py`](../packages/harness/agent_workspace/agents/middlewares/title_middleware.py) (New)
- ✅ Created `TitleMiddleware` class
- ✅ Implemented `_should_generate_title()` check
- ✅ Defaults to generating a local fallback title, avoiding extra LLM latency before streaming completes; supports LLM title generation when `model_name` is explicitly configured
- ✅ Implemented `after_model()` / `aafter_model()` hooks, triggered automatically after the initial turn
- ✅ Included fallback policy (uses leading characters of user message if LLM is unconfigured or fails)

#### [`packages/harness/agent_workspace/config/app_config.py`](../packages/harness/agent_workspace/config/app_config.py)
- ✅ Imported `load_title_config_from_dict`
- ✅ Loaded title configuration in `from_file()`

#### [`packages/harness/agent_workspace/agents/lead_agent/agent.py`](../packages/harness/agent_workspace/agents/lead_agent/agent.py)
- ✅ Imported `TitleMiddleware`
- ✅ Registered into `middleware` list: `[SandboxMiddleware(), TitleMiddleware()]`

### 2. Configuration Files

#### [`config.yaml`](../../config.example.yaml)
- ✅ Added title configuration section:
```yaml
title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null  # null = fast local fallback; specify model name to enable LLM generation
```

### 3. Documentation

#### [`docs/AUTO_TITLE_GENERATION.md`](../docs/AUTO_TITLE_GENERATION.md) (New)
- ✅ Complete feature documentation
- ✅ Architecture and implementation details
- ✅ Configuration guide
- ✅ Client usage examples (TypeScript)
- ✅ Sequence workflow diagrams (Mermaid)
- ✅ Troubleshooting guide
- ✅ State vs Metadata comparison

#### [`TODO.md`](TODO.md)
- ✅ Added feature completion record

### 4. Tests

#### [`tests/test_title_generation.py`](../tests/test_title_generation.py) (New)
- ✅ Configuration class unit tests
- ✅ Middleware initialization unit tests
- ✅ Integration tests with mock runtime

---

## 🎯 Core Design Decisions

### Why State Instead of Metadata?

| Aspect | State (✅ Adopted) | Metadata (❌ Not Adopted) |
|---|---|---|
| **Persistence** | Automatic (via checkpointer) | Implementation dependent, unreliable |
| **Version Control** | Supports time-travel inspection | Not supported |
| **Type Safety** | TypedDict definition | Arbitrary dictionary |
| **Standardization** | LangGraph core mechanism | Platform extension |

### Workflow

```
User sends initial message
  ↓
Agent processes and returns response
  ↓
TitleMiddleware.after_model() / aafter_model() triggers
  ↓
Check: First turn? Title already exists?
  ↓
Default: Generate local fallback title from first user message
  ↓
If title.model_name explicitly configured, call LLM for refined title
  ↓
Return {"title": "..."} to update state
  ↓
Checkpointer automatically persists state (if configured)
  ↓
Client reads from state.values.title
```

---

## 📋 Usage Guide

### Backend Configuration

1. **Enable / Disable Feature**
```yaml
# config.yaml
title:
  enabled: true  # set to false to disable
```

2. **Custom Configuration**
```yaml
title:
  enabled: true
  max_words: 8      # Maximum 8 words in title
  max_chars: 80     # Maximum 80 characters in title
  model_name: null  # null = fast local fallback; specify model to enable LLM titles
```

3. **Configure Persistence (Optional)**

To persist titles during local development:

```python
# checkpointer.py
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("agent_workspace.db")
```

```json
// langgraph.json
{
  "graphs": {
    "lead_agent": "agent_workspace.agents:lead_agent"
  },
  "checkpointer": "checkpointer:checkpointer"
}
```

### Client Usage

```typescript
// Retrieve thread title
const state = await client.threads.getState(threadId);
const title = state.values.title || "New Conversation";

// Display in conversation list
<li>{title}</li>
```

**⚠️ Note**: Title is stored in `state.values.title`, not `thread.metadata.title`.

---

## 🧪 Testing

```bash
# Run title generation tests
pytest tests/test_title_generation.py -v

# Run all test suites
pytest
```

---

## 🔍 Troubleshooting

### Title Not Generated?

1. Check configuration: `title.enabled = true`
2. Confirm first turn (1 user message + 1 assistant reply)
3. If `title.model_name` is explicitly configured, verify title model availability; defaults to local fallback when unset

### Title Generated but Not Visible?

1. Verify read path: `state.values.title` (not `thread.metadata.title`)
2. Check if API response contains the `title` field
3. Re-fetch state

### Title Lost After Server Restart?

1. Local development requires a configured checkpointer
2. LangGraph Platform automatically persists state
3. Check database to ensure checkpointer works normally

### Default Title Displayed After First Turn Cancellation?

1. `runtime/runs/worker.py` keeps the run in finalizing state during interrupted-run cleanup to prevent newer runs on the same thread from overwriting checkpoints while fallback titles are being written.
2. If cancellation occurs before an available checkpoint is written, the worker generates a local fallback title using the first user message in `graph_input`.
3. Before writing the fallback title, the system re-reads the latest checkpoint; if thread state has advanced, it performs a title-only update to the latest snapshot to prevent stale messages from becoming latest.

---

## 📊 Performance Impact

- **Default Latency**: Default `title.model_name: null` incurs no extra LLM latency, generating local fallback titles directly from the first user message.
- **Explicit LLM Latency**: Only when `title.model_name` is configured does the system wait for a title model call after the first response.
- **Concurrency Safety**: State is updated within `after_model()` / `aafter_model()`, requiring no client polling.
- **Resource Consumption**: Generated only once per conversation thread.

### Optimization Tips

1. Keep `model_name: null` by default to avoid extra LLM latency before streaming ends.
2. If concise generated titles are required, configure a fast, lightweight title model.
3. Keep `max_words` and `max_chars` bounded, and ensure the prompt template is concise.

---

## 🚀 Next Steps

- [ ] Add integration tests for custom prompt templates
- [ ] Support multilingual title generation prompts
- [ ] Add manual title regeneration capability
- [ ] Monitor title generation success rates and latencies

---

## 📚 Related Resources

- [Full Documentation](../docs/AUTO_TITLE_GENERATION.md)
- [LangGraph Middleware](https://langchain-ai.github.io/langgraph/concepts/middleware/)
- [LangGraph State Management](https://langchain-ai.github.io/langgraph/concepts/low_level/#state)
- [LangGraph Checkpointer](https://langchain-ai.github.io/langgraph/concepts/persistence/)