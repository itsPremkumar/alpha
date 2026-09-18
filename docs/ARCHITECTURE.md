# System Architecture

## Overview

Agent Workspace (Agent Workspace) is a full-stack AI agent platform built on LangGraph, featuring a multi-agent orchestration system with sandboxed execution, persistent memory, and extensible tool integration. The system operates in per-thread isolated environments with a unified gateway API and Next.js frontend.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT WORKSPACE ARCHITECTURE                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Client     │     │   Client     │     │   Client     │     │   Client     │
│  (Browser)   │     │  (Electron)  │     │  (Telegram)  │     │   (Slack)    │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │                    │
       └────────────────────┼────────────────────┼────────────────────┘
                            ▼
                   ┌──────────────────┐
                   │    Nginx         │
                   │  (Port 2026)     │
                   │  Reverse Proxy   │
                   └────────┬─────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│   Frontend       │ │  Gateway API │ │   Provisioner    │
│  (Next.js)       │ │  (FastAPI)   │ │  (Port 8002)     │
│  (Port 3000)     │ │  (Port 8001) │ │  (K8s Sandbox)   │
└──────────────────┘ └──────┬───────┘ └──────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│  Lead Agent      │ │  Subagent    │ │   Memory         │
│  (LangGraph)     │ │  Executor    │ │   System         │
└──────────────────┘ └──────────────┘ └──────────────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│  Sandbox         │ │  Tools       │ │   Persistence    │
│  Providers       │ │  (MCP/Skills)│ │   (SQLite/       │
│  (Local/Docker)  │ │              │ │    PostgreSQL)   │
└──────────────────┘ └──────────────┘ └──────────────────┘
```

## Service Topology

| Service | Port | Role |
|---------|------|------|
| **Nginx** | `2026` | Unified reverse proxy entry point — single public port |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph agent runtime |
| **Frontend** | `3000` | Next.js chat UI (App Router) |
| **Provisioner** | `8002` | Optional — Kubernetes sandbox mode only |
| **Desktop Gateway** | `8201` | Electron-spawned Gateway (Windows app) |

## Request Routing (Nginx)

```
Nginx (2026)
├── /api/langgraph/*  → Gateway embedded runtime (rewritten to /api/*)
├── /api/* (other)    → Gateway REST API
└── / (non-API)       → Frontend (Next.js)
```

## Core Components

### 1. Gateway API (FastAPI)

The Gateway is the central orchestration layer providing:
- **REST API** for frontend integration
- **Embedded LangGraph Runtime** for agent execution
- **WebSocket/SSE** streaming for real-time updates
- **Authentication & Authorization** (Better Auth)
- **File Upload/Download** with thread isolation
- **MCP Server Management** (stdio, SSE, HTTP transports)
- **Skill Lifecycle Management** (install, enable, review)
- **Memory System API** (extraction, retrieval, configuration)
- **Operator Endpoints** (`/api/ops/*` for health, status, resources)

### 2. Lead Agent (LangGraph)

Single LangGraph agent (`lead_agent`) created via `make_lead_agent(config)`:

```
Lead Agent
├── Dynamic Model Selection (thinking/vision support)
├── Middleware Chain (9 middlewares in strict order)
├── Tool System (sandbox, MCP, community, built-in, skills)
├── Subagent Delegation (parallel task execution)
└── System Prompt (skills injection, memory context, workspace guidance)
```

#### Middleware Chain (Execution Order)

| # | Middleware | Purpose |
|---|-----------|---------|
| 1 | **ThreadDataMiddleware** | Creates per-thread isolated directories (workspace, uploads, outputs) |
| 2 | **UploadsMiddleware** | Injects newly uploaded files into conversation context |
| 3 | **SandboxMiddleware** | Acquires sandbox environment for code execution |
| 4 | **SummarizationMiddleware** | Reduces context when approaching token limits (optional) |
| 5 | **TodoListMiddleware** | Tracks multi-step tasks in plan mode (optional) |
| 6 | **TitleMiddleware** | Auto-generates conversation titles from first user request |
| 7 | **MemoryMiddleware** | Queues conversations for async memory extraction |
| 8 | **ViewImageMiddleware** | Injects image data for vision-capable models (conditional) |
| 9 | **ClarificationMiddleware** | Intercepts clarification requests (must be last) |

### 3. Sandbox System

Per-thread isolated execution with virtual path translation:

```
Providers:
├── LocalSandboxProvider    → Filesystem isolation (default)
└── AioSandboxProvider      → Docker containers (async, warm pools)

Virtual Paths:
├── /mnt/user-data/workspace    → Thread workspace directory
├── /mnt/user-data/uploads      → Thread uploads directory
├── /mnt/user-data/outputs      → Thread outputs directory
└── /mnt/skills                 → Skills directory (skills/public + skills/custom)

Tools:
├── bash (disabled by default in LocalSandboxProvider)
├── ls
├── read_file
├── write_file (overwrite + append modes)
└── str_replace (serialized read-modify-write per sandbox+path)
```

### 4. Subagent System

Async task delegation with concurrent execution:

- **Built-in Agents**: `general-purpose` (full toolset), `bash` (shell specialist)
- **Concurrency**: Max 3 subagents per turn, 15-minute timeout
- **Execution**: Background thread pools with status tracking + SSE events
- **Flow**: Agent calls `task()` tool → executor runs subagent → polls completion → returns result

### 5. Memory System

LLM-powered persistent context retention:

- **Automatic Extraction**: Analyzes conversations for user context, facts, preferences
- **Scope-Safe Writes**: Middleware extracts only durable, descriptive user-level facts
- **Atomic Replacements**: Contradiction removal linked to replacement survives gates
- **Structured Storage**: User context (work, personal, top-of-mind), history, confidence-scored facts
- **Debounced Updates**: Batches updates to minimize LLM calls
- **System Prompt Injection**: Top facts + context injected into agent prompts
- **Storage**: JSON file with mtime-based cache invalidation

### 6. Tool Ecosystem

| Category | Tools |
|----------|-------|
| **Sandbox** | `bash`, `ls`, `read_file`, `write_file`, `str_replace` |
| **Built-in** | `present_files`, `ask_clarification`, `view_image`, `task` (subagent) |
| **Community** | Tavily (web search), Jina AI (web fetch), Crawl4AI, Firecrawl, fastCRW, DuckDuckGo (images) |
| **MCP** | Any MCP server (stdio, SSE, HTTP transports) |
| **Skills** | Domain-specific workflows injected via system prompt |

### 7. IM Channel Integrations

| Platform | Streaming | Features |
|----------|-----------|----------|
| **Feishu/Lark** | Full streaming | Card updates, typing indicators, topic serialization |
| **Slack** | Final response | Runs.wait() response path |
| **Telegram** | Final response | Runs.wait() response path |
| **Discord** | Typing indicators | Dedicated event loop, bounded cancellation |
| **WeChat/WeCom/DingTalk/Buzz** | Varies | Platform-specific adapters |

### 8. Persistence Layer

- **Checkpointer**: LangGraph-compatible checkpoint storage
- **Store Engines**: SQLite (default) / PostgreSQL (shared use)
- **Schema Migrations**: Alembic (auto-run on Gateway startup)
- **Thread Data**: Isolated per-thread directories under `users/{user_id}/threads/{thread_id}/`

## Data Flow

### Chat Request Flow

```
User Message
    │
    ▼
Nginx (2026) → /api/langgraph/runs/stream
    │
    ▼
Gateway Router → RunManager.run_agent()
    │
    ▼
StreamBridge → Lead Agent (LangGraph)
    │
    ├── Middleware Chain (1-9)
    ├── Model Call (with tools)
    ├── Tool Execution (sandbox/MCP/skills)
    ├── Subagent Delegation (parallel)
    └── Memory Extraction (async)
    │
    ▼
SSE Stream → Frontend → User
```

### File Upload Flow

```
POST /api/threads/{id}/uploads
    │
    ▼
UploadsMiddleware
    │
    ├── Validate file type (PDF/PPT/Excel/Word → Markdown via markitdown)
    ├── Reject directories (all-or-nothing)
    ├── Stage as .upload-*.part (atomic replace after validation)
    ├── Store in thread-isolated directory
    ├── Handle duplicates (_N suffix)
    └── Return upload metadata
    │
    ▼
Next Turn → UploadsMiddleware injects files into context
```

## Configuration Architecture

```
config.yaml (gitignored, root)
├── models[]              # LLM configurations
├── tools[]               # Tool definitions
├── tool_groups[]         # Logical tool groupings
├── sandbox               # Execution provider config
├── skills                # Skills directory paths
├── title                 # Auto-title generation
├── summarization         # Context summarization
├── subagents             # Subagent system config
├── memory                # Memory system settings
├── scheduler             # Scheduled tasks
├── channels              # IM channel configs
├── tracing               # LangSmith/Langfuse
└── extensions            # Third-party extensions

extensions_config.json (gitignored, root, API-writable)
├── mcpServers{}          # MCP server configurations
└── skills{}              # Skill enable/disable state
```

## Security Architecture

- **Task Boundaries**: Per-thread isolation prevents cross-contamination
- **Encrypted Checkpoints**: AES-GCM encryption for sensitive data
- **Credential Vault**: Scoped secret management
- **Security Headers**: HSTS, CSP, X-Frame-Options on all responses
- **Operator Authentication**: Better Auth for `/api/ops/*` endpoints
- **Input Validation**: All API inputs validated and sanitized
- **Sandbox Isolation**: Docker containers or filesystem jails

## Scalability Considerations

- **Horizontal Scaling**: Gateway stateless (except scheduler multi-instance mode)
- **Database**: PostgreSQL required for multi-instance scheduler
- **Redis**: Optional for distributed caching (not currently implemented)
- **Load Balancing**: Nginx handles connection distribution
- **Connection Pooling**: SQLAlchemy async pools for database

## Technology Stack

| Layer | Technology |
|-------|------------|
| **Agent Framework** | LangGraph 1.0.6+ |
| **LLM Abstraction** | LangChain 1.2.3+ |
| **API Framework** | FastAPI 0.115.0+ |
| **MCP Support** | langchain-mcp-adapters |
| **Sandbox** | agent-sandbox |
| **Document Conversion** | markitdown |
| **Web Search** | tavily-python, firecrawl-py |
| **Frontend** | Next.js 15 (App Router), React 19 |
| **Styling** | Tailwind CSS |
| **Desktop** | Electron |
| **Container** | Docker, Docker Compose |
| **Orchestration** | Kubernetes (Helm chart) |
| **Database** | SQLite / PostgreSQL (SQLAlchemy + Alembic) |
| **Package Mgmt** | uv (Python), pnpm (Node.js) |