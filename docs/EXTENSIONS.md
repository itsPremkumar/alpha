# Extensions System Documentation

## Overview

The Extensions system allows operators to extend Alpha's capabilities through third-party Python packages. Extensions can contribute middleware, task lifecycle hooks, system model observers, Gateway services, and FastAPI routers.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
                        Extensions System
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Extension Manager                     │   │
│  │  • Transactional install/upgrade/remove                  │   │
│  │  • Lock file for reproducibility                         │   │
│  │  • Source verification (checksums, signatures)           │   │
│  └────────────────────────────┬────────────────────────────┘   │
│                               │                                  │
│              ┌────────────────┼────────────────┐                │
│              ▼                ▼                ▼                │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐  │
│  │   Middleware    │ │  Task Lifecycle │ │ System Model    │  │
│  │   Contributions │ │     Hooks       │ │   Observers     │  │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘  │
│              ┌────────────────┐ ┌─────────────────┐            │
│              ▼                ▼                ▼                │
│  ┌─────────────────┐ ┌─────────────────┐                      │
│  │ Gateway Services│ │  FastAPI Routers│                      │
│  │ (Background)    │ │  (HTTP Endpts)  │                      │
│  └─────────────────┘ └─────────────────┘                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Extension Types

### 1. Middleware
Request/response processing pipeline extensions.

```python
# extension/middleware.py
from agent_workspace_extension_api import Middleware
from starlette.requests import Request
from starlette.responses import Response

class LoggingMiddleware(Middleware):
    async def process_request(self, request: Request) -> Request:
        # Modify request
        request.state.extension_data = "value"
        return request
    
    async def process_response(self, request: Request, response: Response) -> Response:
        # Modify response
        response.headers["X-Extension"] = "logging"
        return response
```

### 2. Task Lifecycle Hooks
Hook into agent execution phases.

```python
# extension/lifecycle.py
from agent_workspace_extension_api import TaskLifecycleHook
from alpha.types import AgentState

class MetricsHook(TaskLifecycleHook):
    async def on_task_start(self, state: AgentState) -> None:
        # Task starting
        pass
    
    async def on_tool_call(self, state: AgentState, tool_name: str, args: dict) -> None:
        # Tool called
        pass
    
    async def on_tool_result(self, state: AgentState, tool_name: str, result: any) -> None:
        # Tool completed
        pass
    
    async def on_task_end(self, state: AgentState, result: any) -> None:
        # Task completed
        pass
    
    async def on_error(self, state: AgentState, error: Exception) -> None:
        # Error occurred
        pass
```

### 3. System Model Observers
Monitor agent state changes.

```python
# extension/observer.py
from agent_workspace_extension_api import SystemModelObserver
from alpha.types import SystemEvent

class AlertObserver(SystemModelObserver):
    async def on_event(self, event: SystemEvent) -> None:
        if event.type == "token_budget_exceeded":
            await send_alert(event.data)
        elif event.type == "sandbox_error":
            await notify_ops(event.data)
```

### 4. Gateway Services
Long-running background services.

```python
# extension/service.py
from agent_workspace_extension_api import GatewayService
import asyncio

class CleanupService(GatewayService):
    name = "cleanup-service"
    
    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())
    
    async def stop(self) -> None:
        self.task.cancel()
        await self.task
    
    async def run(self) -> None:
        while True:
            await asyncio.sleep(3600)  # Hourly
            await cleanup_old_checkpoints()
```

### 5. FastAPI Routers
Custom HTTP endpoints.

```python
# extension/router.py
from agent_workspace_extension_api import RouterContribution
from fastapi import APIRouter

router = APIRouter(prefix="/api/extensions/my-ext", tags=["my-extension"])

@router.get("/status")
async def status():
    return {"status": "ok"}

@router.post("/action")
async def action(data: ActionRequest):
    return {"result": "done"}

class MyRouter(RouterContribution):
    def get_router(self) -> APIRouter:
        return router
```

## Extension Structure

### Directory Layout
```
my-extension/
├── pyproject.toml          # Package metadata
├── src/
│   └── my_extension/
│       ├── __init__.py
│       ├── manifest.py     # Extension manifest
│       ├── middleware.py
│       ├── lifecycle.py
│       ├── observer.py
│       ├── service.py
│       └── router.py
├── tests/
│   └── test_extension.py
├── README.md
└── LICENSE
```

### pyproject.toml
```toml
[project]
name = "my-extension"
version = "1.0.0"
description = "Description of extension"
readme = "README.md"
license = {text = "MIT"}
authors = [{name = "Author", email = "author@example.com"}]
requires-python = ">=3.12"
dependencies = [
    "agent-workspace-extension-api>=2.1.0",
    # Other dependencies
]
classifiers = [
    "Framework :: Alpha Extension",
]

[project.entry-points."alpha.extensions"]
my-extension = "my_extension.manifest:ExtensionManifest"

[tool.uv.sources]
agent-workspace-extension-api = {path = "../../packages/extension-api", editable = true}
```

### Manifest (manifest.py)
```python
from agent_workspace_extension_api import ExtensionManifest, ExtensionConfig

class ExtensionManifest(ExtensionManifest):
    name = "my-extension"
    version = "1.0.0"
    description = "Extension description"
    author = "Author Name"
    
    # Configuration schema (JSON Schema)
    config_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "API key"},
            "interval": {"type": "integer", "default": 300}
        },
        "required": ["api_key"]
    }
    
    # Contributions
    middleware = ["my_extension.middleware:LoggingMiddleware"]
    task_lifecycle = ["my_extension.lifecycle:MetricsHook"]
    system_model_observers = ["my_extension.observer:AlertObserver"]
    gateway_services = ["my_extension.service:CleanupService"]
    routers = ["my_extension.router:MyRouter"]
    
    # Dependencies on other extensions
    depends_on = ["other-extension>=1.0.0"]
    
    # Minimum agent version
    min_agent_version = "2.1.0"
```

## Extension Management

### Installation

#### Via CLI
```bash
# Install from Git
make extension-install SOURCE=git+https://github.com/user/ext.git

# Install from local path
make extension-install SOURCE=./my-extension

# Install from PyPI
make extension-install SOURCE=my-extension@1.0.0

# Install from archive
make extension-install SOURCE=https://example.com/ext.tar.gz
```

#### Via API
```http
POST /api/extensions/install
{
    "source": "git+https://github.com/user/ext.git",
    "config": {"api_key": "${API_KEY}"}
}
```

### Upgrade
```bash
# Upgrade to latest
make extension-upgrade SOURCE=git+https://github.com/user/ext.git

# Upgrade to specific version
make extension-upgrade SOURCE=my-extension@1.1.0
```

### Enable/Disable
```bash
# Enable (requires restart)
make extension-enable NAME=my-extension

# Disable (requires restart)
make extension-disable NAME=my-extension
```

### List Extensions
```bash
make extension-list

# Or via API
GET /api/extensions
```

### Remove
```bash
make extension-remove NAME=my-extension
```

## Configuration

### config.yaml (Operator-Controlled)
```yaml
# plugins list in config.yaml - ONLY place to enable extensions
plugins:
  - "my_extension.manifest:ExtensionManifest"
  - "other_extension:ExtensionManifest"
```
**Security Note**: This list causes code import, so it's deliberately kept out of API-writable config.

### extensions_config.json (Runtime-Editable)
```json
{
  "extensions": {
    "my-extension": {
      "enabled": true,
      "config": {
        "api_key": "${MY_EXT_API_KEY}",
        "interval": 300
      }
    }
  }
}
```

## Extension API Reference

### ExtensionManifest Base Class
```python
class ExtensionManifest:
    name: str                    # Unique identifier
    version: str                 # Semantic version
    description: str             # Human-readable description
    author: str                  # Author name
    config_schema: dict          # JSON Schema for config
    middleware: list[str]        # Import paths to Middleware classes
    task_lifecycle: list[str]    # Import paths to TaskLifecycleHook classes
    system_model_observers: list[str]  # Import paths to Observer classes
    gateway_services: list[str]  # Import paths to GatewayService classes
    routers: list[str]           # Import paths to RouterContribution classes
    depends_on: list[str]        # Extension dependencies
    min_agent_version: str       # Minimum agent version
    max_agent_version: str       # Maximum agent version (optional)
```

### Middleware Interface
```python
class Middleware:
    async def process_request(self, request: Request) -> Request:
        """Process incoming request. Return modified request."""
        return request
    
    async def process_response(self, request: Request, response: Response) -> Response:
        """Process outgoing response. Return modified response."""
        return response
```

### TaskLifecycleHook Interface
```python
class TaskLifecycleHook:
    async def on_task_start(self, state: AgentState) -> None:
        pass
    
    async def on_tool_call(self, state: AgentState, tool_name: str, args: dict) -> None:
        pass
    
    async def on_tool_result(self, state: AgentState, tool_name: str, result: any) -> None:
        pass
    
    async def on_task_end(self, state: AgentState, result: any) -> None:
        pass
    
    async def on_error(self, state: AgentState, error: Exception) -> None:
        pass
    
    async def on_checkpoint(self, state: AgentState, checkpoint_id: str) -> None:
        pass
    
    async def on_resume(self, state: AgentState, checkpoint_id: str) -> None:
        pass
```

### SystemModelObserver Interface
```python
class SystemModelObserver:
    async def on_event(self, event: SystemEvent) -> None:
        """Handle system event."""
        pass
```

### SystemEvent Types
```python
class SystemEvent:
    type: str  # Event type
    timestamp: datetime
    data: dict  # Event-specific data
    thread_id: str | None
    run_id: str | None

# Event types:
# - task_started, task_completed, task_failed
# - tool_called, tool_completed, tool_failed
# - checkpoint_created, run_resumed
# - token_budget_warning, token_budget_exceeded
# - sandbox_error, model_error
# - skill_loaded, skill_unloaded
# - project_created, project_updated
# - bot_dm_received, team_run_started
```

### GatewayService Interface
```python
class GatewayService:
    name: str  # Unique service name
    
    async def start(self) -> None:
        """Start the service."""
        pass
    
    async def stop(self) -> None:
        """Stop the service gracefully."""
        pass
    
    async def health_check(self) -> dict:
        """Return health status."""
        return {"status": "healthy"}
```

### RouterContribution Interface
```python
class RouterContribution:
    def get_router(self) -> APIRouter:
        """Return FastAPI router."""
        pass
```

## Security Model

### Trust Boundaries
```
┌────────────────────────────────────────────────────────────┐
│                    TRUSTED CODE                            │
│  • Core agent-workspace packages                           │
│  • Operator-installed extensions (config.yaml plugins)     │
│  • Full system access                                      │
├────────────────────────────────────────────────────────────┤
│                    UNTRUSTED CODE                          │
│  • User-uploaded skills (sandboxed)                        │
│  • MCP servers (isolated processes)                        │
│  • Limited capabilities                                    │
└────────────────────────────────────────────────────────────┘
```

### Extension Privileges
- **Full Gateway access** - Same process, same permissions
- **Database access** - Via dependency injection
- **Configuration access** - Full config.yaml + extensions_config.json
- **Network access** - Unrestricted
- **Filesystem access** - Unrestricted (within container)

### Security Requirements for Extensions
1. **Source verification** - Checksums, signatures
2. **Dependency pinning** - Exact versions in lockfile
3. **Code review** - Operator responsibility
4. **Sandboxing** - Not provided (operator must trust)
5. **Audit logging** - All extension actions logged

## Lock File

### extensions.lock.json
```json
{
  "version": 1,
  "extensions": {
    "my-extension": {
      "source": "git+https://github.com/user/ext.git@abc123",
      "version": "1.0.0",
      "checksum": "sha256:abc123...",
      "dependencies": {
        "agent-workspace-extension-api": "2.1.0"
      },
      "installed_at": "2026-09-17T10:00:00Z",
      "installed_by": "user-uuid"
    }
  },
  "metadata": {
    "lock_version": 1,
    "created_at": "2026-09-17T10:00:00Z",
    "agent_version": "2.1.0"
  }
}
```

### Lock Discipline
- **Always generated** on install/upgrade
- **Committed to git** (if repo tracks extensions)
- **Verified on startup** - mismatch blocks startup
- **Atomic updates** - Transactional install

## Building Extensions

### Development Setup
```bash
# Create extension template
cd examples/agent-workspace-extension-example
cp -r ../my-extension

# Install in development mode
cd backend
uv pip install -e ../my-extension

# Test with dev server
make dev
```

### Testing Extensions
```python
# tests/test_extension.py
import pytest
from fastapi.testclient import TestClient
from my_extension.manifest import ExtensionManifest

def test_manifest():
    manifest = ExtensionManifest()
    assert manifest.name == "my-extension"
    assert manifest.version == "1.0.0"

def test_router(app):
    client = TestClient(app)
    response = client.get("/api/extensions/my-ext/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_middleware(app):
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.headers.get("X-Extension") == "logging"
```

### Publishing Extensions

#### Private PyPI
```bash
# Build
cd my-extension && uv build

# Publish
uv publish --index private-pypi
```

#### Git Source
```bash
# Tag release
git tag v1.0.0
git push origin v1.0.0

# Users install via
make extension-install SOURCE=git+https://github.com/user/ext.git@v1.0.0
```

## Reference Extension

The `examples/agent-workspace-extension-example/` demonstrates all five contribution types:

```
agent-workspace-extension-example/
├── pyproject.toml
├── src/
│   └── agent_workspace_extension_example/
│       ├── __init__.py
│       ├── manifest.py
│       ├── middleware.py      # Request/response logging
│       ├── lifecycle.py       # Task metrics collection
│       ├── observer.py        # System event alerts
│       ├── service.py         # Background cleanup
│       └── router.py          # Custom /api/ext/* endpoints
└── tests/
```

## Troubleshooting

### Extension Not Loading
```bash
# Check config.yaml plugins list
grep -A 10 "plugins:" config.yaml

# Check extension installed
make extension-list

# Check logs
docker compose logs gateway | grep "extension"

# Verify manifest import
cd backend && python -c "from my_extension.manifest import ExtensionManifest; print(ExtensionManifest())"
```

### Version Conflicts
```bash
# Check lock file
cat extensions.lock.json

# Reinstall with fresh lock
rm extensions.lock.json
make extension-install SOURCE=...
```

### Runtime Errors
```bash
# Check extension logs
docker compose logs gateway | grep "my-extension"

# Disable problematic extension
make extension-disable NAME=my-extension
# Restart gateway
docker compose restart gateway
```

### Performance Impact
```bash
# Profile extension overhead
cd backend && python -m cProfile -o ext_profile.stats \
  -c "from app.main import app; import asyncio; asyncio.run(app.router.startup())"

# Analyze
python -c "import pstats; p = pstats.Stats('ext_profile.stats'); p.sort_stats('cumulative').print_stats(20)"
```

## Best Practices

### Development
1. **Minimal dependencies** - Only what's needed
2. **Clear interfaces** - Use type hints, document APIs
3. **Error handling** - Graceful degradation
4. **Observability** - Log key operations
5. **Configuration** - Use config_schema, validate early

### Distribution
1. **Semantic versioning** - Breaking changes = major version
2. **Changelog** - Document changes per version
3. **Compatibility** - Test against min/max agent versions
4. **Documentation** - README with usage examples

### Operations
1. **Staging first** - Test in dev before prod
2. **Rollback plan** - `make extension-upgrade SOURCE=...@prev-version`
3. **Monitoring** - Add health checks to services
4. **Resource limits** - Set memory/CPU limits for services

## Migration Guide

### From v1 Extensions
- Manifest class renamed from `Extension` to `ExtensionManifest`
- Entry point changed from `extension` to `alpha.extensions`
- Config schema now uses JSON Schema (was custom)
- Middleware signature changed (Request/Response objects)

### Breaking Changes Checklist
- [ ] Update `min_agent_version` in manifest
- [ ] Test all contribution types
- [ ] Verify config migration
- [ ] Update dependencies
- [ ] Run full test suite