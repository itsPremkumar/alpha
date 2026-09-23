# Development Guide

## Overview

This guide covers setting up a development environment, code conventions, testing, and contributing to Alpha.

## Development Environment Setup

### Prerequisites

| Tool | Version | Install Command |
|------|---------|-----------------|
| Git | 2.40+ | `winget install Git.Git` / `brew install git` / `apt install git` |
| Python | 3.12+ | `winget install Python.Python.3.12` / `brew install python@3.12` / `apt install python3.12` |
| uv | 0.4+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 22+ | `winget install OpenJS.NodeJS` / `brew install node@22` / `apt install nodejs` |
| pnpm | 9+ | `corepack enable && corepack prepare pnpm@latest --activate` |
| Docker | 24+ | Docker Desktop / `apt install docker.io docker-compose` |

### Initial Setup

```bash
# Clone repository
git clone https://github.com/itsPremkumar/agent-workspace-desktop.git
cd agent-workspace-desktop

# Generate config files (REQUIRED)
make config

# Install all dependencies
make install

# Verify setup
make doctor
```

### What `make install` Does

```bash
# Backend
cd backend && uv sync --all-extras --dev

# Frontend
cd frontend && pnpm install

# Pre-commit hooks
pre-commit install
```

## Running Development Stack

### Full Stack (Recommended)
```bash
# Starts: Gateway (8001), Frontend (3000), Nginx (2026)
make dev

# Access at http://localhost:2026
```

### Individual Services

#### Backend Only
```bash
cd backend && make dev
# Gateway at http://localhost:8001
```

#### Frontend Only
```bash
cd frontend && pnpm dev
# Frontend at http://localhost:3000 (Webpack default)
# Or: pnpm dev --turbopack (Turbopack)
```

### Stopping
```bash
make stop
# Or Ctrl+C in each terminal
```

## Project Structure

```
alpha/
├── backend/                 # Python backend
│   ├── app/                 # FastAPI Gateway + IM channels
│   │   ├── api/             # API routes
│   │   ├── channels/        # IM channel integrations
│   │   ├── config.py        # Configuration loading
│   │   ├── main.py          # FastAPI app entry
│   │   └── middleware/      # Custom middleware
│   ├── packages/
│   │   ├── harness/         # Agent framework (alpha.*)
│   │   │   ├── alpha/
│   │   │   │   ├── agent/       # Core agent logic
│   │   │   │   ├── memory/      # Memory systems
│   │   │   │   ├── tools/       # Built-in tools
│   │   │   │   ├── skills/      # Skill system
│   │   │   │   ├── projects/    # Workforce projects
│   │   │   │   ├── bots/        # Bot mode
│   │   │   │   ├── scheduler/   # Scheduled tasks
│   │   │   │   └── extensions/  # Extension system
│   │   └── extension-api/   # Extension contract (agent_workspace_extension_api.*)
│   ├── extensions/sources/  # Installed extension snapshots
│   ├── tests/               # Backend tests
│   └── scripts/             # Backend scripts
├── frontend/                # Next.js frontend
│   ├── src/
│   │   ├── app/             # App Router pages
│   │   ├── components/      # React components
│   │   │   ├── bots/        # Bot-related components
│   │   │   └── sections/    # Workspace sections
│   │   ├── lib/             # Utilities, API clients
│   │   └── types/           # TypeScript types
│   └── tests/               # Frontend tests
├── docker/                  # Docker configurations
│   ├── nginx/               # Nginx config
│   └── provisioner/         # K8s provisioner
├── deploy/                  # Deployment configs
│   └── helm/                # Helm charts
├── electron/                # Desktop app
├── skills/                  # Agent skills
│   ├── public/              # Committed skills
│   └── custom/              # Local skills (gitignored)
├── scripts/                 # Root orchestration scripts
├── contracts/               # JSON contracts
├── tests/                   # Root-level tests
└── docs/                    # Documentation (this folder)
```

## Code Conventions

### Python (Backend)

#### Formatting
```bash
# Format
cd backend && make format

# Check
cd backend && make lint
```
- **Formatter**: Ruff (Black-compatible)
- **Line length**: 100 characters
- **Import style**: isort (Ruff built-in)

#### Type Hints
- Required for all public functions
- Use `from __future__ import annotations`
- Prefer `list[str]` over `List[str]` (Python 3.9+)

#### Naming
- **Modules**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: `_leading_underscore`

#### Async/Await
- Use `async def` for I/O operations
- Avoid blocking calls in async functions
- Use `asyncio.to_thread()` for CPU-bound work

#### Error Handling
```python
# Custom exceptions in app/exceptions.py
from app.exceptions import ValidationError, NotFoundError

async def get_thread(thread_id: str) -> Thread:
    thread = await db.get(thread_id)
    if not thread:
        raise NotFoundError(f"Thread {thread_id} not found")
    return thread
```

#### Logging
```python
import structlog

logger = structlog.get_logger(__name__)

logger.info("Thread created", thread_id=thread.id, user_id=user.id)
logger.error("Run failed", run_id=run.id, error=str(e), exc_info=True)
```

### TypeScript (Frontend)

#### Formatting
```bash
# Lint + type check
cd frontend && pnpm check

# Format (if configured)
cd frontend && pnpm format
```

#### Conventions
- **Files**: `PascalCase.tsx` for components, `camelCase.ts` for utilities
- **Components**: Function components with TypeScript interfaces
- **Props**: Explicit interfaces, no `any`
- **State**: Use React hooks, avoid class components
- **Imports**: Absolute imports from `@/` (configured in tsconfig)

#### Component Pattern
```tsx
// components/sections/ChatView.tsx
interface ChatViewProps {
  threadId: string;
  onMessageSend: (content: string) => void;
}

export function ChatView({ threadId, onMessageSend }: ChatViewProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  
  // Event handlers
  const handleSend = useCallback((content: string) => {
    onMessageSend(content);
  }, [onMessageSend]);
  
  return (
    <div className="flex flex-col h-full">
      {/* ... */}
    </div>
  );
}
```

#### API Client
```typescript
// lib/api.ts
export async function createThread(data: CreateThreadRequest): Promise<Thread> {
  const response = await fetch('/api/threads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  
  if (!response.ok) {
    throw new ApiError(await response.json());
  }
  
  return response.json();
}
```

#### SSE Handling
```typescript
// lib/sse-reducer.ts
export function createSSEReducer() {
  return (state: SSEState, event: SSEEvent): SSEState => {
    switch (event.type) {
      case 'agent_message':
        return { ...state, messages: [...state.messages, event.data] };
      case 'tool_call':
        return { ...state, pendingTools: [...state.pendingTools, event.data] };
      // ...
    }
  };
}
```

## Testing

### Backend Tests

#### Structure
```
backend/tests/
├── test_*.py              # Unit tests
├── test_*_e2e.py          # End-to-end tests
├── test_*_blocking_io.py  # Blocking I/O detection
├── fixtures/              # Test fixtures
└── conftest.py            # Pytest configuration
```

#### Running Tests
```bash
# All tests (excludes live/blocking-IO)
cd backend && make test

# Specific test file
cd backend && python -m pytest tests/test_api_threads.py -v

# Specific test function
cd backend && python -m pytest tests/test_api_threads.py::test_create_thread -v

# With coverage
cd backend && python -m pytest --cov=app --cov=packages/harness

# Blocking I/O tests (strict)
cd backend && make test-blocking-io

# Live tests (require API keys)
cd backend && python -m pytest tests/live/ -v
```

#### Writing Tests
```python
# tests/test_api_threads.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_thread(client: AsyncClient):
    response = await client.post("/api/threads", json={"title": "Test"})
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["title"] == "Test"

@pytest.mark.asyncio
async def test_thread_not_found(client: AsyncClient):
    response = await client.get("/api/threads/non-existent")
    assert response.status_code == 404
```

#### Fixtures
```python
# conftest.py
@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
async def db_session() -> AsyncSession:
    async with async_session() as session:
        yield session
```

### Frontend Tests

#### Structure
```
frontend/tests/
├── components/            # Component tests
├── lib/                   # Utility tests
└── integration/           # Integration tests
```

#### Running Tests
```bash
# All tests
cd frontend && pnpm test

# Specific pattern
cd frontend && pnpm rstest run ChatView

# Watch mode
cd frontend && pnpm test --watch
```

#### Writing Tests
```typescript
// tests/components/ChatView.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatView } from '@/components/ChatView';

describe('ChatView', () => {
  it('renders messages', () => {
    render(<ChatView threadId="123" onMessageSend={jest.fn()} />);
    expect(screen.getByText('ChatView')).toBeInTheDocument();
  });
  
  it('calls onMessageSend on submit', () => {
    const onSend = jest.fn();
    render(<ChatView threadId="123" onMessageSend={onSend} />);
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('test message');
  });
});
```

### Integration Tests

#### Playwright (E2E)
```bash
# Install browsers
cd backend && python -m playwright install

# Run E2E tests
cd backend && python -m pytest tests/e2e/ -v
```

## Git Workflow

### Branch Strategy
- `main` - Production-ready code
- `feature/*` - Feature branches
- `fix/*` - Bug fixes
- `release/*` - Release preparation

### Commit Messages
```
type(scope): subject

body (optional)

footer (optional)
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`

Examples:
```
feat(api): add thread branching endpoint
fix(memory): resolve cognitive memory lock contention
docs(readme): update installation instructions
test(backend): add e2e test for scheduled tasks
```

### Pre-commit Hooks
```bash
# Runs on every commit
# - ruff format --check (backend)
# - ruff check (backend)
# - pnpm check (frontend) - if configured
# - trailing whitespace
# - merge conflict markers
# - large files
```

### Pre-push
```bash
# Recommended: run full test suite
cd backend && make test && make lint
cd frontend && pnpm check && pnpm test
```

## Debugging

### Backend Debugging

#### VS Code Launch Configuration
```json
// .vscode/launch.json
{
  "configurations": [
    {
      "name": "Debug Gateway",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--reload", "--port", "8001"],
      "cwd": "${workspaceFolder}/backend",
      "envFile": "${workspaceFolder}/.env"
    },
    {
      "name": "Debug Tests",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["tests/test_api_threads.py", "-v"],
      "cwd": "${workspaceFolder}/backend"
    }
  ]
}
```

#### Debug Commands
```bash
# Debug specific test
cd backend && python -m pytest tests/test_api_threads.py::test_create_thread -v -s --pdb

# Debug with print statements
cd backend && python -c "
from app.config import load_config
config = load_config()
print(config.models)
"
```

### Frontend Debugging

#### VS Code Launch Configuration
```json
{
  "configurations": [
    {
      "name": "Next.js: Debug",
      "type": "node",
      "request": "launch",
      "runtimeExecutable": "pnpm",
      "runtimeArgs": ["dev"],
      "cwd": "${workspaceFolder}/frontend",
      "console": "integratedTerminal"
    }
  ]
}
```

#### Browser DevTools
- React DevTools for component inspection
- Network tab for API calls
- Console for errors/logs
- Application tab for localStorage/cookies

### Database Debugging
```bash
# SQLite (dev)
sqlite3 backend/data/app.db ".schema"
sqlite3 backend/data/app.db "SELECT * FROM threads LIMIT 5;"

# PostgreSQL (Docker)
docker compose -f docker/docker-compose.yaml exec postgres psql -U alpha alpha
```

### Redis Debugging
```bash
# CLI
docker compose -f docker/docker-compose.yaml exec redis redis-cli

# Monitor commands
redis-cli MONITOR

# Keys
redis-cli KEYS "*"
```

## Adding Features

### New API Endpoint

1. **Create route** in `backend/app/api/`
```python
# app/api/threads.py
@router.post("/threads/{thread_id}/branch")
async def branch_thread(thread_id: str, request: BranchRequest):
    # Implementation
    return BranchResponse(...)
```

2. **Add to router** in `backend/app/api/__init__.py`
```python
from . import threads
router.include_router(threads.router, prefix="/threads", tags=["threads"])
```

3. **Add types** in `backend/app/schemas/`
```python
# app/schemas/thread.py
class BranchRequest(BaseModel):
    checkpoint_id: str
    new_title: str | None = None

class BranchResponse(BaseModel):
    new_thread_id: str
```

4. **Write tests** in `backend/tests/`
```python
# tests/test_api_thread_branching.py
@pytest.mark.asyncio
async def test_branch_thread(client: AsyncClient):
    # Test implementation
```

5. **Update frontend** in `frontend/src/lib/api.ts`
```typescript
export async function branchThread(threadId: string, data: BranchRequest): Promise<BranchResponse> {
  return fetch(`/api/threads/${threadId}/branch`, {
    method: 'POST',
    body: JSON.stringify(data),
  }).then(r => r.json());
}
```

6. **Update documentation** in `docs/API.md`

### New Skill

1. **Create skill directory** in `skills/public/` or `skills/custom/`
```
skills/public/my-skill/
├── SKILL.md           # Skill manifest
├── main.py            # Skill implementation
├── requirements.txt   # Dependencies
└── tests/             # Skill tests
```

2. **Skill Manifest** (SKILL.md)
```markdown
---
name: my-skill
version: 1.0.0
description: Does something useful
author: Your Name
tags: [utility, data]
requires: []
---

# My Skill

Description of what this skill does.

## Tools

- `tool_name`: Description
```

3. **Implementation** (main.py)
```python
from alpha.skills import Skill, tool

class MySkill(Skill):
    name = "my-skill"
    
    @tool
    async def tool_name(self, param: str) -> str:
        """Description of tool."""
        return f"Result: {param}"
```

4. **Test** (tests/test_my_skill.py)
```python
def test_my_skill():
    skill = MySkill()
    result = skill.tool_name("test")
    assert result == "Result: test"
```

5. **Register** in `extensions_config.json` or via API

### New MCP Server

1. **Add to extensions_config.json**
```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["-m", "my_mcp_server"],
      "env": {"API_KEY": "${MY_API_KEY}"},
      "enabled": true
    }
  }
}
```

2. **Or install as extension**
```bash
make extension-install SOURCE=git+https://github.com/user/mcp-server.git
```

## Performance Profiling

### Backend
```bash
# Profile memory
cd backend && python scripts/sandbox_memory_profile.py

# Profile blocking I/O
cd backend && python scripts/detect_blocking_io_static.py

# CPU profiling
cd backend && python -m cProfile -o profile.stats -m pytest tests/test_perf.py
```

### Frontend
```bash
# Build analysis
cd frontend && pnpm build && npx @next/bundle-analyzer

# React DevTools Profiler
# Record interaction, analyze render times
```

## Common Development Tasks

### Database Migrations
```bash
# Create migration
cd backend && make migrate-rev MESSAGE="add user preferences"

# Apply migrations
cd backend && make migrate-upgrade

# Rollback
cd backend && make migrate-downgrade
```

### Configuration Changes
```bash
# Update config.example.yaml
# Update config-upgrade.sh for migrations
# Test with make config && make dev
```

### Adding Dependencies
```bash
# Backend
cd backend && uv add package-name
# Or for dev: uv add --dev package-name

# Frontend
cd frontend && pnpm add package-name
# Or for dev: pnpm add -D package-name
```

### Updating Dependencies
```bash
# Backend
cd backend && uv lock --upgrade
cd backend && uv sync --all-extras --dev

# Frontend
cd frontend && pnpm update
cd frontend && pnpm install
```

## Troubleshooting Development

### Common Issues

| Issue | Solution |
|-------|----------|
| `make config` fails | Check templates exist, permissions |
| `make install` fails | Check Python/Node versions, clear caches |
| `make dev` port conflict | Kill existing processes, check ports |
| Frontend not hot-reloading | Check Webpack/Turbo config, file watchers |
| Backend not reloading | Check uvicorn --reload, file permissions |
| Tests fail | Check test DB, fixtures, async issues |
| Type errors | Run `make lint` / `pnpm check`, fix types |

### Reset Development Environment
```bash
# Full reset
make stop
cd backend && rm -rf .venv .pytest_cache .ruff_cache
cd ../frontend && rm -rf node_modules .next
cd .. && make install
make config
make dev
```

## Contributing

### Pull Request Process
1. Fork repository
2. Create feature branch
3. Make changes with tests
4. Run full test suite
5. Update documentation
6. Submit PR with description
7. Address review comments
8. Merge after approval

### Code Review Checklist
- [ ] Tests pass
- [ ] Linting passes
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Performance considered
- [ ] Security reviewed
- [ ] Accessibility considered (frontend)

### Release Process
See [RELEASING.md](../RELEASING.md)