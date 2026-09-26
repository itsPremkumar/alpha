# API Reference

## Base URLs

| Environment | Base URL |
|-------------|----------|
| Local Dev (Nginx) | `http://localhost:2026/api` |
| Local Dev (Gateway Direct) | `http://localhost:8001` |
| Docker Production | `http://localhost:2026/api` |
| Desktop App | `http://localhost:8201/api` |

## Authentication

### Session Authentication (Browser)
- Automatic via Better Auth session cookies
- No header required for browser requests

### API Key Authentication (Programmatic)
```bash
curl -H "Authorization: Bearer <api-key>" http://localhost:8001/api/threads
```

### Internal Authentication (Scheduled Tasks, Bots)
- Special internal tokens
- Honored only for server-side callers
- Client-supplied copies dropped from requests

## Core Endpoints

### Threads

#### Create Thread
```http
POST /api/threads
Content-Type: application/json

{
  "title": "Optional title",
  "metadata": {}
}
```

**Response** (201):
```json
{
  "id": "thread-uuid",
  "title": "Optional title",
  "created_at": "2026-09-17T10:00:00Z",
  "updated_at": "2026-09-17T10:00:00Z",
  "metadata": {},
  "status": "active"
}
```

#### List Threads
```http
GET /api/threads?limit=20&offset=0&status=active
```

**Response** (200):
```json
{
  "threads": [...],
  "total": 100,
  "limit": 20,
  "offset": 0
}
```

#### Get Thread
```http
GET /api/threads/{thread_id}
```

**Response** (200):
```json
{
  "id": "thread-uuid",
  "title": "Thread title",
  "created_at": "2026-09-17T10:00:00Z",
  "updated_at": "2026-09-17T10:30:00Z",
  "metadata": {},
  "status": "active",
  "messages": [...],
  "checkpoints": [...]
}
```

#### Delete Thread
```http
DELETE /api/threads/{thread_id}
```
**Response** (204): No content

#### Update Thread
```http
PATCH /api/threads/{thread_id}
Content-Type: application/json

{
  "title": "New title",
  "metadata": {"key": "value"}
}
```

### Runs

#### Start Run
```http
POST /api/threads/{thread_id}/runs
Content-Type: application/json

{
  "input": "User message or structured input",
  "config": {
    "model": "model-name",
    "tools": ["tool1", "tool2"],
    "context": {},
    "non_interactive": false,
    "disable_clarification": false,
    "github_token": "optional"
  },
  "metadata": {}
}
```

**Response** (201):
```json
{
  "run_id": "run-uuid",
  "thread_id": "thread-uuid",
  "status": "running",
  "created_at": "2026-09-17T10:00:00Z"
}
```

#### Stream Run Events (SSE)
```http
GET /api/threads/{thread_id}/runs/{run_id}/stream
Accept: text/event-stream
```

**Event Stream Format**:
```
event: run_started
data: {"run_id": "run-uuid", "thread_id": "thread-uuid"}

event: agent_message
data: {"role": "assistant", "content": "Hello!", "tool_calls": []}

event: tool_call
data: {"name": "search_web", "arguments": {"query": "..."}, "id": "call-uuid"}

event: tool_result
data: {"call_id": "call-uuid", "result": "...", "error": null}

event: checkpoint
data: {"checkpoint_id": "cp-uuid", "step": 5}

event: run_completed
data: {"run_id": "run-uuid", "output": "Final result"}

event: run_failed
data: {"run_id": "run-uuid", "error": "Error message"}
```

#### Get Run Status
```http
GET /api/threads/{thread_id}/runs/{run_id}
```

**Response** (200):
```json
{
  "run_id": "run-uuid",
  "thread_id": "thread-uuid",
  "status": "running|completed|failed|cancelled",
  "input": "...",
  "output": "...",
  "error": null,
  "created_at": "2026-09-17T10:00:00Z",
  "completed_at": "2026-09-17T10:05:00Z",
  "token_usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "total_tokens": 1500,
    "cost_usd": 0.0015
  }
}
```

#### Resume Run from Checkpoint

Automatic safe recovery handles model-only pending checkpoints. This endpoint
remains the manual path for `recovery_confirmation_required`,
`recovery_exhausted`, `recovery_no_work`, ownership failures, or any other run
that requires review.
```http
POST /api/threads/{thread_id}/runs/{run_id}/resume
Content-Type: application/json

{
  "checkpoint_id": "cp-uuid",
  "input": "Optional new input"
}
```

#### Cancel Run
```http
POST /api/threads/{thread_id}/runs/{run_id}/cancel
```
**Response** (200): `{"status": "cancelled"}`

#### Undo Last Turn
```http
POST /api/threads/{thread_id}/undo
```
**Response** (200): New run ID for continuation

### Messages

#### Get Messages
```http
GET /api/threads/{thread_id}/messages?limit=50&before=message_id
```

**Response** (200):
```json
{
  "messages": [
    {
      "id": "msg-uuid",
      "thread_id": "thread-uuid",
      "role": "user|assistant|system|tool",
      "content": "Message content",
      "tool_calls": [],
      "tool_call_id": null,
      "created_at": "2026-09-17T10:00:00Z",
      "metadata": {}
    }
  ],
  "has_more": true
}
```

#### Send Message (Convenience)
```http
POST /api/threads/{thread_id}/messages
Content-Type: application/json

{
  "role": "user",
  "content": "Message content",
  "metadata": {}
}
```
Creates message and starts run automatically.

### Checkpoints

#### List Checkpoints
```http
GET /api/threads/{thread_id}/checkpoints
```

**Response** (200):
```json
{
  "checkpoints": [
    {
      "id": "cp-uuid",
      "thread_id": "thread-uuid",
      "run_id": "run-uuid",
      "step": 5,
      "state": {},
      "created_at": "2026-09-17T10:00:00Z"
    }
  ]
}
```

#### Get Checkpoint
```http
GET /api/threads/{thread_id}/checkpoints/{checkpoint_id}
```

### Agent Configuration

#### Get Available Models
```http
GET /api/models
```

**Response** (200):
```json
{
  "models": [
    {
      "name": "primary",
      "provider": "openrouter",
      "model": "stealth/union-alpha",
      "max_tokens": 16384,
      "supports_tools": true,
      "supports_vision": true,
      "supports_reasoning": false
    }
  ]
}
```

#### Get Available Tools
```http
GET /api/tools
```

**Response** (200):
```json
{
  "builtin": [
    {"name": "search_web", "description": "Search the web", "parameters": {}}
  ],
  "mcp": [
    {"server": "server-name", "tools": [...]}
  ],
  "skills": [
    {"id": "skill-id", "name": "Skill Name", "tools": [...]}
  ]
}
```

### Health & Operations

#### Liveness
```http
GET /health
```
**Response** (200): `{"status": "ok"}`

#### Readiness
```http
GET /health/ready
```
**Response** (200):
```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "models": "ok"
  }
}
```

#### Version
```http
GET /api/ops/version
```
**Response** (200):
```json
{
  "version": "2.1.0",
  "git_sha": "abc123",
  "build_date": "2026-09-17T10:00:00Z",
  "python_version": "3.12.0",
  "node_version": "22.0.0"
}
```

#### Status
```http
GET /api/ops/status
```
**Response** (200):
```json
{
  "uptime_seconds": 3600,
  "server_time": "2026-09-17T11:00:00Z",
  "docs_available": true,
  "active_threads": 5,
  "active_runs": 2
}
```

#### Resources
```http
GET /api/ops/resources
```
**Response** (200):
```json
{
  "cpu_percent": 25.5,
  "memory_percent": 45.2,
  "disk_percent": 30.1,
  "network_io": {"bytes_sent": 1000000, "bytes_recv": 2000000}
}
```

#### Advice
```http
GET /api/ops/advice
```
**Response** (200):
```json
{
  "recommendation": "scale_up",
  "reason": "High CPU usage detected",
  "actions": ["Add Gateway replica", "Check for runaway runs"]
}
```

### Workforce (Projects, Bots, Teams)

The advanced `alpha.swarm` v2 surface is documented in detail in [`API_REFERENCE.md`](API_REFERENCE.md#swarms--group-chat-api) and [`WORKFORCE.md`](WORKFORCE.md#autonomous-swarms--dynamic-topologies). It adds owner-scoped plan admission, lease-fenced task claims/completion, bounded messages/events, budgets, metrics, SSE audit streaming, and explicit pause/resume/cancel/replan operations under `/api/swarms`.

#### Projects

##### List Projects
```http
GET /api/projects
```

##### Create Project
```http
POST /api/projects
Content-Type: application/json

{
  "name": "Project Name",
  "description": "Project description",
  "constitution": "Project rules and guidelines"
}
```

##### Get Project
```http
GET /api/projects/{project_id}
```

##### Update Project
```http
PATCH /api/projects/{project_id}
```

##### Delete Project
```http
DELETE /api/projects/{project_id}
```

##### Project Members
```http
GET /api/projects/{project_id}/members
POST /api/projects/{project_id}/members
DELETE /api/projects/{project_id}/members/{user_id}
```

##### Project Locks
```http
GET /api/projects/{project_id}/locks
POST /api/projects/{project_id}/locks
DELETE /api/projects/{project_id}/locks/{lock_id}
```

##### Project Events
```http
GET /api/projects/{project_id}/events
```

##### Project Context Files
```http
GET /api/projects/{project_id}/context
POST /api/projects/{project_id}/context
DELETE /api/projects/{project_id}/context/{file_id}
```

##### Project Handoffs
```http
GET /api/projects/{project_id}/handoffs
POST /api/projects/{project_id}/handoffs
```

##### Project Decisions (ADRs)
```http
GET /api/projects/{project_id}/decisions
POST /api/projects/{project_id}/decisions
```

##### Project Evidence
```http
GET /api/projects/{project_id}/evidence
POST /api/projects/{project_id}/evidence
```

#### Bots

##### List Bots
```http
GET /api/bots
```

##### Get Bot
```http
GET /api/bots/{bot_name}
```

##### Send DM to Bot
```http
POST /api/bots/{bot_name}/dm
Content-Type: application/json

{
  "content": "Message to bot",
  "thread_id": "optional-existing-thread",
  "context": {}
}
```

##### Get Bot Inbox
```http
GET /api/bots/{bot_name}/inbox
```

##### Bot Chat (Interactive)
```http
POST /api/bots/{bot_name}/chat
Content-Type: application/json

{
  "message": "User message",
  "thread_id": "optional"
}
```

#### Teams (Group Chat)

##### List Teams
```http
GET /api/groups
```

##### Create Team
```http
POST /api/groups
Content-Type: application/json

{
  "name": "team-name",
  "members": ["bot1", "bot2", "bot3"],
  "moderator": "moderator-bot"
}
```

##### Run Team
```http
POST /api/groups/{group_name}/runs
Content-Type: application/json

{
  "objective": "Team objective",
  "context": {}
}
```

##### Get Team Run
```http
GET /api/groups/{group_name}/runs/{run_id}
```

##### Stream Team Run
```http
GET /api/groups/{group_name}/runs/{run_id}/stream
```

### Skills

#### List Skills
```http
GET /api/skills
```

#### Get Skill
```http
GET /api/skills/{skill_id}
```

#### Create Skill
```http
POST /api/skills
Content-Type: application/json

{
  "name": "Skill Name",
  "description": "What this skill does",
  "code": "Python code for skill",
  "requirements": ["package1", "package2"]
}
```

#### Update Skill
```http
PATCH /api/skills/{skill_id}
```

#### Delete Skill
```http
DELETE /api/skills/{skill_id}
```

#### Skill Curation
```http
GET /api/skills/curator/stats
GET /api/skills/curator/recommendations
POST /api/skills/curator/promote
POST /api/skills/curator/archive
```

#### Skill Usage
```http
GET /api/skills/usage
GET /api/skills/tiers
```

#### Skill Authoring (/learn)
```http
POST /api/skills/author
Content-Type: application/json

{
  "prompt": "Description of desired skill",
  "examples": ["example1", "example2"]
}
```

### Scheduled Tasks

#### List Scheduled Tasks
```http
GET /api/scheduled-tasks
```

#### Create Scheduled Task
```http
POST /api/scheduled-tasks
Content-Type: application/json

{
  "name": "Daily Report",
  "cron": "0 9 * * *",
  "prompt": "Generate daily summary",
  "config": {},
  "enabled": true
}
```

#### Get Scheduled Task
```http
GET /api/scheduled-tasks/{task_id}
```

#### Update Scheduled Task
```http
PATCH /api/scheduled-tasks/{task_id}
```

#### Delete Scheduled Task
```http
DELETE /api/scheduled-tasks/{task_id}
```

#### Task Occurrences
```http
GET /api/scheduled-tasks/{task_id}/occurrences
```

#### Schedule Blueprints
```http
GET /api/scheduled-tasks/blueprints
POST /api/scheduled-tasks/blueprints
```

#### Incidents
```http
GET /api/scheduled-tasks/incidents
POST /api/scheduled-tasks/incidents/{incident_id}/resolve
```

### Memory

#### Search Memory
```http
POST /api/memory/search
Content-Type: application/json

{
  "query": "Search query",
  "limit": 10,
  "filters": {}
}
```

#### Get Memory Stats
```http
GET /api/memory/stats
```

#### Cognitive Memory
```http
GET /api/memory/cognitive/{owner}
POST /api/memory/cognitive/{owner}
```

### Knowledge Graph

#### Query Graph
```http
POST /api/knowledge/query
Content-Type: application/json

{
  "cypher": "MATCH (n) RETURN n LIMIT 10"
}
```

#### Graph Stats
```http
GET /api/knowledge/stats
```

### Files

#### Upload File
```http
POST /api/files/upload
Content-Type: multipart/form-data

file: <binary>
thread_id: "optional"
```

#### List Files
```http
GET /api/files?thread_id=thread-uuid
```

#### Get File
```http
GET /api/files/{file_id}
```

#### Delete File
```http
DELETE /api/files/{file_id}
```

### Compatibility (OpenAI)

#### Chat Completions
```http
POST /api/compat/openai/chat/completions
Content-Type: application/json

{
  "model": "model-name",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1000
}
```

**Response** (200):
```json
{
  "id": "chatcmpl-uuid",
  "object": "chat.completion",
  "created": 1699999999,
  "model": "model-name",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Hello!"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
}
```

### Console & Insights

#### Console Insights
```http
GET /api/console/insights
```

#### Model Health
```http
GET /api/models/local/health
```

## Error Responses

### Standard Error Format
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {}
  }
}
```

### Common Error Codes
| Code | HTTP Status | Description |
|------|-------------|-------------|
| `VALIDATION_ERROR` | 400 | Request validation failed |
| `UNAUTHORIZED` | 401 | Authentication required |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `CONFLICT` | 409 | Resource conflict |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server error |
| `SERVICE_UNAVAILABLE` | 503 | Service temporarily unavailable |
| `MODEL_ERROR` | 502 | Upstream model error |
| `SANDBOX_ERROR` | 500 | Sandbox execution failed |
| `CHECKPOINT_NOT_FOUND` | 404 | Checkpoint doesn't exist |
| `RUN_NOT_FOUND` | 404 | Run doesn't exist |
| `THREAD_NOT_FOUND` | 404 | Thread doesn't exist |

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| Thread creation | 60/min |
| Run creation | 30/min |
| Streaming | 10 concurrent |
| File upload | 10/min |
| API calls | 300/min |

## Dynamic Workflows

The DWE is an opt-in correlated orchestration plane; it does not replace the
Gateway `RunManager` parent lifecycle. Full semantics and honesty boundaries are
documented in [`DYNAMIC_WORKFLOWS.md`](DYNAMIC_WORKFLOWS.md).

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/workflows/dynamic/perceive` | Preview intent, decomposition, waves, and resources without starting a run |
| `POST` | `/api/workflows/dynamic/execute` | Compile and optionally execute a dynamic graph |
| `POST` | `/api/workflows/turns` | Run a paradigm turn; send `dynamic: true` for the full loop |
| `POST` | `/api/bots/{name}/workflow` | Run the shared service in bot mode for a validated bot |
| `GET` | `/api/workflows/system/registries` | Inspect bounded capability/resource registry health |
| `POST` | `/api/workflows/runs/{run_id}/step` | Advance one scheduling wave |
| `POST` | `/api/workflows/runs/{run_id}/cancel` | Cancel a live workflow run |
| `POST` | `/api/workflows/runs/{run_id}/approvals/{node_id}` | Resolve the exact active approval request |
| `POST` | `/api/workflows/runs/{run_id}/patch` | Apply a typed, version-checked graph patch |
| `POST` | `/api/workflows/runs/{run_id}/replan` | Propose/apply a repair patch and optionally resume |
| `POST` | `/api/workflows/runs/{run_id}/compensate` | Run only real compensation callbacks |
| `GET` | `/api/workflows/runs/{run_id}/events` | Read the live event projection |
| `GET` | `/api/workflows/runs/{run_id}/events/durable` | Read validated JSONL records and corrupt-tail disclosures |
| `POST` | `/api/workflows/runs/{run_id}/replay` | Fold the log and compare covered fields |
| `GET` | `/api/workflows/system/durability` | Inspect sink attachment, write failures, and persisted runs |
| `POST` | `/api/workflows/hydrate` | Install valid persisted projections into this process |
| `GET/POST` | `/api/workflows/{workflow_id}/plans` | Read/record append-only graph revisions |

Dynamic responses include the real run status and acceptance disclosure. The
built-in `alpha.local.digest` executor is a local graph projection and returns
`acceptance_passed: false`; it is not evidence that a domain task was completed.
Workflow definitions/runs are owner-scoped for authenticated HTTP requests.
Recurring prompts disclose that scheduler handoff is host-owned rather than
silently creating a second cron loop. Local JSONL/plan persistence is
restart-recoverable for one Gateway process, not a multi-worker exactly-once
lease repository.



### Real-time Updates
```http
GET /api/ws/threads/{thread_id}
```
Receives: thread updates, run events, message updates

### Bot Presence
```http
GET /api/ws/bots
```
Receives: bot online/offline, status changes

## SDK Examples

### Python
```python
import httpx

client = httpx.Client(base_url="http://localhost:8001", timeout=60.0)
client.headers["Authorization"] = "Bearer <api-key>"

# Create thread
thread = client.post("/api/threads", json={"title": "Test"}).json()
thread_id = thread["id"]

# Start run
run = client.post(f"/api/threads/{thread_id}/runs", json={
    "input": "Hello, world!",
    "config": {"model": "primary"}
}).json()
run_id = run["run_id"]

# Stream events
with client.stream("GET", f"/api/threads/{thread_id}/runs/{run_id}/stream") as response:
    for line in response.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            print(event)
```

### JavaScript/TypeScript
```typescript
const baseUrl = 'http://localhost:8001';
const headers = { 'Authorization': 'Bearer <api-key>' };

// Create thread
const thread = await fetch(`${baseUrl}/api/threads`, {
  method: 'POST',
  headers: { ...headers, 'Content-Type': 'application/json' },
  body: JSON.stringify({ title: 'Test' })
}).then(r => r.json());

// Stream run
const run = await fetch(`${baseUrl}/api/threads/${thread.id}/runs`, {
  method: 'POST',
  headers: { ...headers, 'Content-Type': 'application/json' },
  body: JSON.stringify({ input: 'Hello!' })
}).then(r => r.json());

const eventSource = new EventSource(
  `${baseUrl}/api/threads/${thread.id}/runs/${run.run_id}/stream`,
  { headers }
);

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data);
};
```

## Versioning

- API version indicated in `/api/ops/version`
- Breaking changes require major version bump
- Deprecation notices in CHANGELOG.md
- Backward compatibility maintained within major version