# Automatic Thread Title Generation

## Overview

Automatically generates descriptive titles for conversation threads after the user asks an initial question and receives a response.

## Implementation

Using `TitleMiddleware` within the `after_model` hook:
1. Detects whether this is the first turn in the conversation (1 user message + 1 assistant reply).
2. Checks whether state already contains a title.
3. By default, generates a fast local fallback title from the initial user message, avoiding an extra LLM call before streaming completes; calls an LLM to generate titles (maximum 6 words by default) only when `model_name` is explicitly configured.
4. Stores the generated title in `ThreadState` (persisted by checkpointer).

TitleMiddleware normalizes structured block/list contents from LangChain messages into plain text before interpolating into the title prompt, avoiding leaking raw Python/JSON representations into the title generation model.

## ⚠️ Important: Storage Mechanism

### Title Storage Location

Title is stored in **`ThreadState.title`**, rather than thread metadata:

```python
class ThreadState(AgentState):
    sandbox: SandboxState | None = None
    title: str | None = None  # ✅ Title stored here
```

### Persistence Notes

| Deployment Mode | Persistent | Notes |
|---|---|---|
| **LangGraph Studio (Local)** | ❌ No | In-memory only, lost on restart |
| **LangGraph Platform** | ✅ Yes | Persisted automatically to database |
| **Custom + Checkpointer** | ✅ Yes | Requires PostgreSQL / SQLite checkpointer configuration |

### How to Enable Persistence

To persist titles during local development, configure a checkpointer:

```python
# Create checkpointer.py alongside langgraph.json
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/dbname"
)
```

Then reference it in `langgraph.json`:

```json
{
  "graphs": {
    "lead_agent": "alpha.agents:lead_agent"
  },
  "checkpointer": "checkpointer:checkpointer"
}
```

## Configuration

In `config.yaml` (optional):

```yaml
title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null  # null = fast local fallback; specify model name to enable LLM generation
```

Or programmatically in Python:

```python
from alpha.config.title_config import TitleConfig, set_title_config

set_title_config(TitleConfig(
    enabled=True,
    max_words=8,
    max_chars=80,
))
```

## Client Usage

### Fetching Thread Title

```typescript
// Approach 1: Read from thread state
const state = await client.threads.getState(threadId);
const title = state.values.title || "New Conversation";

// Approach 2: Listen to stream events
for await (const chunk of client.runs.stream(threadId, assistantId, {
  input: { messages: [{ role: "user", content: "Hello" }] }
})) {
  if (chunk.event === "values" && chunk.data.title) {
    console.log("Title:", chunk.data.title);
  }
}
```

### Displaying Title

```typescript
// Render in conversation list
function ConversationList() {
  const [threads, setThreads] = useState([]);

  useEffect(() => {
    async function loadThreads() {
      const allThreads = await client.threads.list();
      
      // Fetch each thread state to read title
      const threadsWithTitles = await Promise.all(
        allThreads.map(async (t) => {
          const state = await client.threads.getState(t.thread_id);
          return {
            id: t.thread_id,
            title: state.values.title || "New Conversation",
            updatedAt: t.updated_at,
          };
        })
      );
      
      setThreads(threadsWithTitles);
    }
    loadThreads();
  }, []);

  return (
    <ul>
      {threads.map(thread => (
        <li key={thread.id}>
          <a href={`/chat/${thread.id}`}>{thread.title}</a>
        </li>
      ))}
    </ul>
  );
}
```

## Workflow

```mermaid
sequenceDiagram
    participant User
    participant Client
    participant LangGraph
    participant TitleMiddleware
    participant TitleModel as Title model (optional)
    participant Checkpointer

    User->>Client: Send first message
    Client->>LangGraph: POST /threads/{id}/runs
    LangGraph->>Agent: Process message
    Agent-->>LangGraph: Return response
    LangGraph->>TitleMiddleware: after_model() / aafter_model()
    TitleMiddleware->>TitleMiddleware: Check if title generation needed
    alt title.model_name is null (default)
        TitleMiddleware->>TitleMiddleware: Generate local fallback title from first user message
    else explicitly configured title.model_name
        TitleMiddleware->>TitleModel: Generate LLM title
        TitleModel-->>TitleMiddleware: Return title
    end
    TitleMiddleware->>LangGraph: return {"title": "..."}
    LangGraph->>Checkpointer: Save state (including title)
    LangGraph-->>Client: Return response
    Client->>Client: Read from state.values.title
```

## Benefits

✅ **Reliable Persistence** - Uses LangGraph state mechanisms, automatically persisted  
✅ **Fully Backend-Managed** - Zero client-side logic required  
✅ **Automatic Triggering** - Generates automatically after first turn  
✅ **Configurable** - Supports customizable length, models, and prompts  
✅ **Robust Fault Tolerance** - Falls back to local truncation when LLM unavailable  
✅ **Architectural Consistency** - Aligns with SandboxMiddleware and harness patterns  

## Important Considerations

1. **Access Pattern**: Title is stored in `state.values.title`, not in `thread.metadata.title`.
2. **Performance**: Default configuration does not call an LLM; only when `title.model_name` is explicitly set does the system wait for an LLM title generation after the first turn.
3. **Concurrency Safety**: The middleware updates state upon completing the first turn, requiring no auxiliary client requests.
4. **Fallback Policy**: Defaults to using the leading characters of the user message; when an explicit LLM call fails, the system automatically falls back to this local strategy.

## Testing

```bash
cd backend
uv run pytest tests/test_title_middleware_core_logic.py tests/test_title_generation.py
```

## Troubleshooting

### Title is Not Generated

1. Check configuration: `get_title_config().enabled == True`.
2. Confirm first turn: Title only triggers when there is exactly 1 user message and 1 assistant reply.
3. If `title.model_name` is configured, verify title model accessibility; when unset, it falls back to local title generation.

### Title Generated but Not Visible on Client

1. Verify read path: Read from `state.values.title`, not `thread.metadata.title`.
2. Check API response: Confirm the state payload includes the `title` field.
3. Refetch state: Call `client.threads.getState(threadId)`.

### Title Lost After Server Restart

1. Verify checkpointer configuration (required for local persistence).
2. Check deployment mode: LangGraph Platform automatically persists state.
3. Inspect database: Confirm checkpointer tables are functioning properly.

## Architecture Design

### Why State Instead of Metadata?

| Feature | State | Metadata |
|---|---|---|
| **Persistence** | ✅ Automatic (via checkpointer) | ⚠️ Depends on implementation |
| **Version Control** | ✅ Supports time-travel inspection | ❌ Not supported |
| **Type Safety** | ✅ Defined via TypedDict | ❌ Arbitrary dictionary |
| **Traceability** | ✅ Recorded with each snapshot update | ⚠️ Only latest value available |
| **Standardization** | ✅ LangGraph core mechanism | ⚠️ Platform extension |

### Implementation Details

```python
# TitleMiddleware Core Logic
@override
async def aafter_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
    return await self._agenerate_title_result(state)

async def _agenerate_title_result(self, state: TitleMiddlewareState) -> dict | None:
    if not self._should_generate_title(state):
        return None

    config = self._get_title_config()
    if not config.model_name:
        return self._generate_title_result(state)

    # Call title model only when title.model_name is explicitly configured; fall back on error.
    ...
```

## Related Files

- [`packages/harness/alpha/agents/thread_state.py`](../packages/harness/alpha/agents/thread_state.py) - ThreadState definition
- [`packages/harness/alpha/agents/middlewares/title_middleware.py`](../packages/harness/alpha/agents/middlewares/title_middleware.py) - TitleMiddleware implementation
- [`packages/harness/alpha/config/title_config.py`](../packages/harness/alpha/config/title_config.py) - Configuration management
- [`config.yaml`](../../config.example.yaml) - Configuration file
- [`packages/harness/alpha/agents/lead_agent/agent.py`](../packages/harness/alpha/agents/lead_agent/agent.py) - Middleware registration

## References

- [LangGraph Checkpointer Documentation](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- [LangGraph State Management](https://langchain-ai.github.io/langgraph/concepts/low_level/#state)
- [LangGraph Middleware](https://langchain-ai.github.io/langgraph/concepts/middleware/)