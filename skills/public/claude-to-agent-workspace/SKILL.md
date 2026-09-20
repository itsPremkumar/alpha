---
name: claude-to-agent-workspace
description: "Interact with Alpha AI agent platform via its HTTP API. Use this skill when the user wants to send messages or questions to Alpha for research/analysis, start a Alpha conversation thread, check Alpha status or health, list available models/skills/agents in Alpha, manage Alpha memory, upload files to Alpha threads, or delegate complex research tasks to Alpha. Also use when the user mentions alpha or wants to run a deep research task that Alpha can handle."
---

# Alpha Skill

Communicate with a running Alpha instance via its HTTP API. Alpha is an AI agent platform
built on LangGraph that orchestrates sub-agents for research, code execution, web browsing, and more.

## Architecture

Alpha exposes two API surfaces behind an Nginx reverse proxy:

| Service        | Direct Port | Via Proxy                        | Purpose                          |
|----------------|-------------|----------------------------------|----------------------------------|
| Gateway API    | 8001        | `$AGENT_WORKSPACE_GATEWAY_URL`          | REST endpoints and embedded agent runtime |
| LangGraph-compatible API | 8001 | `$AGENT_WORKSPACE_LANGGRAPH_URL`       | Agent threads, runs, streaming   |

## Environment Variables

All URLs are configurable via environment variables. **Read these env vars before making any request.**

| Variable                | Default                                  | Description                        |
|-------------------------|------------------------------------------|------------------------------------|
| `AGENT_WORKSPACE_URL`          | `http://localhost:2026`                  | Unified proxy base URL             |
| `AGENT_WORKSPACE_GATEWAY_URL`  | `${AGENT_WORKSPACE_URL}`                        | Gateway API base (models, skills, memory, uploads) |
| `AGENT_WORKSPACE_LANGGRAPH_URL`| `${AGENT_WORKSPACE_URL}/api/langgraph`          | LangGraph API base (threads, runs) |

When making curl calls, always resolve the URL like this:

```bash
# Resolve base URLs from env (do this FIRST before any API call)
AGENT_WORKSPACE_URL="${AGENT_WORKSPACE_URL:-http://localhost:2026}"
AGENT_WORKSPACE_GATEWAY_URL="${AGENT_WORKSPACE_GATEWAY_URL:-$AGENT_WORKSPACE_URL}"
AGENT_WORKSPACE_LANGGRAPH_URL="${AGENT_WORKSPACE_LANGGRAPH_URL:-$AGENT_WORKSPACE_URL/api/langgraph}"
```

## Available Operations

### 1. Health Check

Verify Alpha is running:

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/health"
```

### 2. Send a Message (Streaming)

This is the primary operation. It creates a thread and streams the agent's response.

**Step 1: Create a thread**

```bash
curl -s -X POST "$AGENT_WORKSPACE_LANGGRAPH_URL/threads" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Response: `{"thread_id": "<uuid>", ...}`

**Step 2: Stream a run**

```bash
curl -s -N -X POST "$AGENT_WORKSPACE_LANGGRAPH_URL/threads/<thread_id>/runs/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "lead_agent",
    "input": {
      "messages": [
        {
          "type": "human",
          "content": [{"type": "text", "text": "YOUR MESSAGE HERE"}]
        }
      ]
    },
    "stream_mode": ["values", "messages-tuple"],
    "stream_subgraphs": true,
    "config": {
      "recursion_limit": 1000
    },
    "context": {
      "thinking_enabled": true,
      "is_plan_mode": true,
      "subagent_enabled": true,
      "thread_id": "<thread_id>"
    }
  }'
```

The response is an SSE stream. Each event has the format:
```
event: <event_type>
data: <json_data>
```

Key event types:
- `metadata` — run metadata including `run_id`
- `values` — full state snapshot with `messages` array
- `messages-tuple` — incremental message updates (AI text chunks, tool calls, tool results)
- `end` — stream is complete

**Context modes** (set via `context`):
- Flash mode: `thinking_enabled: false, is_plan_mode: false, subagent_enabled: false`
- Standard mode: `thinking_enabled: true, is_plan_mode: false, subagent_enabled: false`
- Pro mode: `thinking_enabled: true, is_plan_mode: true, subagent_enabled: false`
- Ultra mode: `thinking_enabled: true, is_plan_mode: true, subagent_enabled: true`

### 3. Continue a Conversation

To send follow-up messages, reuse the same `thread_id` from step 2 and POST another run
with the new message.

### 4. List Models

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/api/models"
```

Returns: `{"models": [{"name": "...", "provider": "...", ...}, ...]}`

### 5. List Skills

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/api/skills"
```

Returns: `{"skills": [{"name": "...", "enabled": true, ...}, ...]}`

### 6. Enable/Disable a Skill

```bash
curl -s -X PUT "$AGENT_WORKSPACE_GATEWAY_URL/api/skills/<skill_name>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

### 7. List Agents

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/api/agents"
```

Returns: `{"agents": [{"name": "...", ...}, ...]}`

### 8. Get Memory

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/api/memory"
```

Returns user context, facts, and conversation history summaries.

### 9. Upload Files to a Thread

```bash
curl -s -X POST "$AGENT_WORKSPACE_GATEWAY_URL/api/threads/<thread_id>/uploads" \
  -F "files=@/path/to/file.pdf"
```

Supports PDF, PPTX, XLSX, DOCX — automatically converts to Markdown.

### 10. List Uploaded Files

```bash
curl -s "$AGENT_WORKSPACE_GATEWAY_URL/api/threads/<thread_id>/uploads/list"
```

### 11. Get Thread History

```bash
curl -s "$AGENT_WORKSPACE_LANGGRAPH_URL/threads/<thread_id>/history"
```

### 12. List Threads

```bash
curl -s -X POST "$AGENT_WORKSPACE_LANGGRAPH_URL/threads/search" \
  -H "Content-Type: application/json" \
  -d '{"limit": 20, "sort_by": "updated_at", "sort_order": "desc"}'
```

## Usage Script

For sending messages and collecting the full response, use the helper script:

```bash
bash /path/to/skills/claude-to-agent-workspace/scripts/chat.sh "Your question here"
```

See `scripts/chat.sh` for the implementation. The script:
1. Checks health
2. Creates a thread
3. Streams the run and collects the final AI response
4. Prints the result

## Parsing SSE Output

The stream returns SSE events. To extract the final AI response from a `values` event:
- Look for the last `event: values` block
- Parse its `data` JSON
- The `messages` array contains all messages; the last one with `type: "ai"` is the response
- The `content` field of that message is the AI's text reply

## Error Handling

- If health check fails, Alpha is not running. Inform the user they need to start it.
- If the stream returns an error event, extract and display the error message.
- Common issues: port not open, services still starting up, config errors.

## Tips

- For quick questions, use flash mode (fastest, no planning).
- For research tasks, use pro or ultra mode (enables planning and sub-agents).
- You can upload files first, then reference them in your message.
- Thread IDs persist — you can return to a conversation later.
