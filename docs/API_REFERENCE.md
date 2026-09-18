# API Reference

## Base URLs

| Environment | Base URL |
|-------------|----------|
| **Development (nginx)** | `http://localhost:2026` |
| **Development (Gateway direct)** | `http://localhost:8001` |
| **Desktop App** | `http://localhost:8201` |
| **Docker Production** | `http://localhost:2026` |

## Authentication

Most endpoints require authentication via Better Auth session cookie or Bearer token.

```
Authorization: Bearer <token>
Cookie: session=<session_id>
```

Operator endpoints (`/api/ops/*`) require explicit operator authentication.

---

## Models API

### List Models
```http
GET /api/models
```

**Response:**
```json
[
  {
    "name": "gpt-4o",
    "display_name": "GPT-4o",
    "supports_thinking": false,
    "supports_vision": true,
    "provider": "openai",
    "model": "gpt-4o"
  }
]
```

### Get Model Config
```http
GET /api/models/{model_name}
```

### Update Model Config
```http
PUT /api/models/{model_name}
Content-Type: application/json

{
  "api_key": "sk-...",
  "supports_thinking": true,
  "supports_vision": true
}
```

---

## Threads API

### Create Thread
```http
POST /api/threads
Content-Type: application/json

{
  "metadata": {
    "title": "My Conversation"
  }
}
```

**Response:**
```json
{
  "thread_id": "abc123...",
  "created_at": "2026-01-15T10:30:00Z",
  "metadata": { "title": "My Conversation" }
}
```

### List Threads
```http
GET /api/threads?limit=50&offset=0
```

### Get Thread
```http
GET /api/threads/{thread_id}
```

### Delete Thread
```http
DELETE /api/threads/{thread_id}
```

### Get Thread Artifacts
```http
GET /api/threads/{thread_id}/artifacts/{path}
```

### Compact Thread (Summarization)
```http
POST /api/threads/{thread_id}/compact
Content-Type: application/json

{
  "keep_recent": 10
}
```

---

## Runs API (LangGraph Compatible)

### Stream Run (Primary)
```http
POST /api/langgraph/threads/{thread_id}/runs/stream
Content-Type: application/json

{
  "input": {
    "messages": [
      { "role": "user", "content": "Hello!" }
    ]
  },
  "config": {
    "configurable": {
      "thread_id": "thread_id",
      "recursion_limit": 1000
    }
  },
  "stream_mode": ["messages-tuple", "values", "custom"]
}
```

**SSE Events:**
- `messages-tuple`: Individual message chunks
- `values`: Complete state snapshots
- `custom`: Custom events (task_*, progress, etc.)

### Create Run (Non-streaming)
```http
POST /api/langgraph/threads/{thread_id}/runs
Content-Type: application/json

{
  "input": { "messages": [...] },
  "config": { "configurable": { "thread_id": "..." } }
}
```

### Get Run Status
```http
GET /api/langgraph/threads/{thread_id}/runs/{run_id}
```

### Get Run Events (Debug/Audit)
```http
GET /api/threads/{thread_id}/runs/{run_id}/events?event_types=context:memory
```

**Event Types:**
- `context:memory` — Returns SHA-256 identity of effective hidden memory block

### Cancel Run
```http
POST /api/langgraph/threads/{thread_id}/runs/{run_id}/cancel
```

---

## Uploads API

### Upload Files
```http
POST /api/threads/{thread_id}/uploads
Content-Type: multipart/form-data

files: <file1>, <file2>, ...
```

**Supported Formats:** PDF, PPT/PPTX, Excel (XLS/XLSX), Word (DOC/DOCX), Markdown, Text, Images

**Response:**
```json
{
  "uploads": [
    {
      "filename": "document.pdf",
      "path": "users/123/threads/abc/uploads/document.pdf",
      "size": 102400,
      "mime_type": "application/pdf",
      "converted": true,
      "markdown_path": "users/123/threads/abc/uploads/document.md"
    }
  ]
}
```

### List Uploads
```http
GET /api/threads/{thread_id}/uploads/list
```

### Delete Upload
```http
DELETE /api/threads/{thread_id}/uploads/{filename}
```

---

## Memory API

### Get Memory Data
```http
GET /api/memory
```

**Response:**
```json
{
  "user_context": {
    "work": "Software engineer at...",
    "personal": "Lives in...",
    "top_of_mind": ["Project deadline", "Learning Rust"]
  },
  "facts": [
    {
      "fact": "User prefers TypeScript over JavaScript",
      "confidence": 0.9,
      "source": "conversation",
      "created_at": "2026-01-10T14:22:00Z"
    }
  ],
  "history": [...]
}
```

### Get Memory Config
```http
GET /api/memory/config
```

### Update Memory Config
```http
PUT /api/memory/config
Content-Type: application/json

{
  "enabled": true,
  "storage_path": "memory.json",
  "debounce_seconds": 300,
  "max_facts": 100
}
```

### Force Memory Reload
```http
POST /api/memory/reload
```

### Get Memory Status
```http
GET /api/memory/status
```

---

## MCP (Model Context Protocol) API

### List MCP Servers
```http
GET /api/mcp/config
```

### Update MCP Config
```http
PUT /api/mcp/config
Content-Type: application/json

{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" }
    }
  }
}
```

### Reset MCP Cache
```http
POST /api/mcp/cache/reset
```

---

## Skills API

### List Skills
```http
GET /api/skills
```

### Install Skill
```http
POST /api/skills/install
Content-Type: multipart/form-data

file: <skill.archive>
```

### Enable/Disable Skill
```http
PATCH /api/skills/{skill_name}
Content-Type: application/json

{ "enabled": true }
```

### Get Skill Review
```http
GET /api/skills/{skill_name}/review
```

---

## Agent/Subagent API

### List Agents
```http
GET /api/agents
```

### Get Agent Info
```http
GET /api/agents/{agent_name}
```

### Bot DM (Fire-and-Forget)
```http
POST /api/bots/{bot_name}/dm
Content-Type: application/json

{
  "message": "Task for bot",
  "context": {}
}
```

### Bot Inbox
```http
GET /api/bots/{bot_name}/inbox
```

---

## Scheduled Tasks API

### List Scheduled Tasks
```http
GET /api/scheduled-tasks
```

### Create Scheduled Task
```http
POST /api/scheduled-tasks
Content-Type: application/json

{
  "name": "Daily Report",
  "cron": "0 9 * * *",
  "input": { "messages": [{ "role": "user", "content": "Generate report" }] },
  "config": {}
}
```

### Get Scheduled Task
```http
GET /api/scheduled-tasks/{task_id}
```

### Update Scheduled Task
```http
PATCH /api/scheduled-tasks/{task_id}
Content-Type: application/json

{ "enabled": false }
```

### Delete Scheduled Task
```http
DELETE /api/scheduled-tasks/{task_id}
```

### Preview Cron
```http
POST /api/scheduled-tasks/preview-cron
Content-Type: application/json

{
  "cron": "0 9 * * *",
  "timezone": "UTC",
  "count": 10
}
```

### List Blueprints
```http
GET /api/scheduled-tasks/blueprints
```

### List Incidents
```http
GET /api/scheduled-tasks/incidents
```

---

## Council/Quality API

### List Council Deliberations
```http
GET /api/council/deliberations
```

### Create Deliberation
```http
POST /api/council/deliberate
Content-Type: application/json

{
  "topic": "Code review",
  "context": "...",
  "deliberators": ["security", "performance", "maintainability"]
}
```

---

## Policy API

### Get Policy
```http
GET /api/policy
```

### Update Policy
```http
PUT /api/policy
Content-Type: application/json

{ "rules": [...] }
```

---

## Missions API

### List Missions
```http
GET /api/missions
```

### Create Mission
```http
POST /api/missions
Content-Type: application/json

{
  "objective": "Build a REST API",
  "agents": ["planner", "coder", "tester"]
}
```

---

## Benchmarks API

### List Benchmarks
```http
GET /api/benchmarks
```

### Run Benchmark
```http
POST /api/benchmarks/run
Content-Type: application/json

{ "benchmark_id": "code_generation" }
```

---

## Evolution API

### Get Evolution Status
```http
GET /api/evolution/status
```

### Trigger Evolution
```http
POST /api/evolution/trigger
```

---

## OpenAI Compatible API

### Chat Completions (Non-streaming)
```http
POST /api/compat/openai/chat/completions
Content-Type: application/json

{
  "model": "gpt-4o",
  "messages": [
    { "role": "user", "content": "Hello!" }
  ],
  "thread_id": "optional-existing-thread-id"
}
```

**Response:** OpenAI-compatible format reusing thread + run lifecycle.

---

## Operator Endpoints (Authenticated)

### Get Version
```http
GET /api/ops/version
```

### Get Status
```http
GET /api/ops/status
```

**Response:**
```json
{
  "uptime_seconds": 3600,
  "server_time": "2026-01-15T10:30:00Z",
  "docs_available": true
}
```

### Get Resources
```http
GET /api/ops/resources
```

### Get Autonomy Advice
```http
GET /api/ops/advice
```

---

## Health Checks

### Liveness
```http
GET /health
```

### Readiness
```http
GET /health/ready
```

---

## Error Responses

All endpoints return standard error format:

```json
{
  "detail": "Error description",
  "error_code": "ERROR_CODE",
  "status_code": 400
}
```

**Common Status Codes:**
- `200` — Success
- `400` — Bad Request (validation error)
- `401` — Unauthorized
- `403` — Forbidden
- `404` — Not Found
- `409` — Conflict (e.g., duplicate thread)
- `422` — Unprocessable Entity
- `500` — Internal Server Error
- `503` — Service Unavailable

---

## Rate Limits

| Endpoint Category | Limit |
|-------------------|-------|
| General API | 100 req/min |
| Runs/Streaming | 20 concurrent |
| Uploads | 10 req/min, 50MB max |
| Operator | 10 req/min |

---

## WebSocket/SSE Events

### Run Stream Events

| Event Type | Description |
|------------|-------------|
| `messages-tuple` | `(message, metadata)` tuples for each message chunk |
| `values` | Complete ThreadState snapshots |
| `custom` | Custom events: `task_start`, `task_complete`, `task_error`, `progress` |

### Custom Event Payloads

```json
// task_start
{ "type": "task_start", "task_id": "subagent-123", "agent": "general-purpose", "input": "..." }

// task_complete
{ "type": "task_complete", "task_id": "subagent-123", "result": "..." }

// task_error
{ "type": "task_error", "task_id": "subagent-123", "error": "..." }

// progress
{ "type": "progress", "stage": "planning", "message": "Analyzing request..." }
```

---

## Client Libraries

### Python (AgentWorkspaceClient)

```python
from agent_workspace.client import AgentWorkspaceClient

client = AgentWorkspaceClient(base_url="http://localhost:2026")
client.authenticate(token="your-token")

# Create thread
thread = client.create_thread(title="My Task")

# Stream run
async for event in client.stream_run(thread.thread_id, "Hello agent!"):
    print(event)

# Upload files
client.upload_files(thread.thread_id, ["document.pdf"])

# Memory
memory = client.get_memory()
```

### JavaScript/TypeScript

```typescript
import { AgentWorkspaceClient } from '@agent-workspace/client';

const client = new AgentWorkspaceClient({ baseUrl: 'http://localhost:2026' });
await client.authenticate({ token: 'your-token' });

const thread = await client.threads.create({ title: 'My Task' });

for await (const event of client.runs.stream(thread.threadId, 'Hello!')) {
  console.log(event);
}
```

---

## OpenAPI Specification

Full OpenAPI 3.0 spec available at:
- `GET /openapi.json` (Gateway direct)
- `GET /api/openapi.json` (via nginx)

Import into Postman, Insomnia, or generate client SDKs.