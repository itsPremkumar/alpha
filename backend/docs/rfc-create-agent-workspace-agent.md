# RFC: `create_agent_workspace_agent` — Parameter-Driven SDK Factory API

## 1. Problem Statement

The legacy entrypoint into the harness was `make_lead_agent(config: RunnableConfig)`. Internally:

```
make_lead_agent
  ├─ get_app_config()          ← Reads config.yaml
  ├─ _resolve_model_name()     ← Reads config.yaml
  ├─ load_agent_config()       ← Reads agents/{name}/config.yaml
  ├─ create_chat_model(name)   ← Reads config.yaml (dynamically imports model class)
  ├─ get_available_tools()     ← Reads config.yaml + extensions_config.json
  ├─ apply_prompt_template()   ← Reads skills directory + memory.json
  └─ _build_middlewares()      ← Reads config.yaml (summarization, vision)
```

**6 implicit filesystem I/O dependencies**. Embedding `agent-workspace-harness` as a Python library into external applications previously required provisioning `config.yaml`, `extensions_config.json`, and skill folders.

### Comparison

| | `langchain.create_agent` | `make_lead_agent` | `AgentWorkspaceClient` (Enhanced) |
|---|---|---|---|
| Positioning | Low-level primitive | Internal factory | **Sole Public API** |
| Configuration Source | Pure parameters | YAML files | **Parameters first, config fallback** |
| Built-in Capabilities | None | Sandbox/Memory/Skills/Subagents | **Composable on demand + admin APIs** |
| User Interface | `graph.invoke(state)` | Internal only | **`client.chat("hello")`** |
| Target Audience | LangChain developers | Internal harness | **All Alpha users** |

## 2. Design Principles

### Dependency Injection Best Practices

1. **Parameters as Injection** — Avoid global state; supply dependencies via arguments.
2. **Protocols Over Concrete Classes** — Depend on behavioral interfaces rather than concrete implementations.
3. **Sensible Defaults** — `sandbox=True` equates to `sandbox=LocalSandboxProvider()`.
4. **Layered APIs** — Single-line common usages with escape hatches for advanced customization.

### Layered Architecture

```
    ┌───────────────────────────┐
    │    AgentWorkspaceClient   │  ← Sole Public API (chat/stream + admin)
    └─────────────┬─────────────┘
    ┌─────────────▼─────────────┐
    │      make_lead_agent      │  ← Internal: config-driven factory
    └─────────────┬─────────────┘
    ┌─────────────▼─────────────┐
    │ create_agent_workspace_agent │  ← Internal: parameter-driven factory
    └─────────────┬─────────────┘
    ┌─────────────▼─────────────┐
    │   langchain.create_agent  │  ← Foundation primitive
    └───────────────────────────┘
```

Users control behaviors via three arguments in `AgentWorkspaceClient`:

| Parameter | Type | Responsibility |
|---|---|---|
| `config` | `dict` | Overrides arbitrary keys from config.yaml |
| `features` | `RuntimeFeatures` | Replaces built-in middleware implementations |
| `extra_middleware` | `list[AgentMiddleware]` | Appends custom user middlewares |

Omitting arguments retains standard `config.yaml` resolution (fully backwards-compatible).

## 3. API Design

### 3.1 `AgentWorkspaceClient` — Sole Public API

```python
from agent_workspace.client import AgentWorkspaceClient
from agent_workspace.agents.features import RuntimeFeatures

client = AgentWorkspaceClient(
    # 1. config — overrides arbitrary config.yaml keys
    config={
        "models": [{"name": "gpt-4o", "use": "langchain_openai:ChatOpenAI", "model": "gpt-4o", "api_key": "sk-..."}],
        "memory": {"max_facts": 50, "enabled": True},
        "title": {"enabled": False},
        "summarization": {"enabled": True, "trigger": [{"type": "tokens", "value": 10000}]},
        "sandbox": {"use": "agent_workspace.sandbox.local:LocalSandboxProvider"},
    },

    # 2. features — replaces built-in middleware implementations
    features=RuntimeFeatures(
        memory=MyMemoryMiddleware(),
        auto_title=MyTitleMiddleware(),
    ),

    # 3. extra_middleware — adds user-defined middlewares
    extra_middleware=[
        MyAuditMiddleware(),       # @Next(SandboxMiddleware)
        MyFilterMiddleware(),      # @Prev(ClarificationMiddleware)
    ],
)
```

Usage Examples:

```python
# Usage 1: Zero-config (reads config.yaml)
client = AgentWorkspaceClient()

# Usage 2: Parameter overrides
client = AgentWorkspaceClient(config={"memory": {"max_facts": 50}})

# Usage 3: Replace middleware implementation
client = AgentWorkspaceClient(features=RuntimeFeatures(auto_title=MyTitleMiddleware()))

# Usage 4: Add custom middleware
client = AgentWorkspaceClient(extra_middleware=[MyAuditMiddleware()])

# Usage 5: Standalone SDK without files
client = AgentWorkspaceClient(config={
    "models": [{"name": "gpt-4o", "use": "langchain_openai:ChatOpenAI", ...}],
    "tools": [{"name": "bash", "use": "agent_workspace.sandbox.tools:bash_tool", "group": "bash"}],
    "memory": {"enabled": True},
})
```

### 3.2 `create_agent_workspace_agent` — Internal Factory

```python
def create_agent_workspace_agent(
    model: BaseChatModel,
    tools: list[BaseTool] | None = None,
    *,
    system_prompt: str | None = None,
    middleware: list[AgentMiddleware] | None = None,
    features: RuntimeFeatures | None = None,
    state_schema: type | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    name: str = "default",
    subagent_runtime: SubagentRuntime | None = None,
) -> CompiledStateGraph:
    ...
```

When integrating subagents, callers pass a shared `SubagentRuntime` instance to coordinate middleware limits, `task` tools, execution slots, and batch workers across graphs.

### 3.3 `RuntimeFeatures`

```python
@dataclass
class RuntimeFeatures:
    sandbox: bool | AgentMiddleware = True
    memory: bool | AgentMiddleware = False
    summarization: bool | AgentMiddleware = False
    subagent: bool | AgentMiddleware = False
    vision: bool | AgentMiddleware = False
    auto_title: bool | AgentMiddleware = False
```

| Value | Meaning |
|---|---|
| `True` | Use default middleware (parameters read from config) |
| `False` | Disable the feature |
| `AgentMiddleware` instance | Replace the entire middleware implementation |

### 3.4 Middleware Ordering: `@Next` and `@Prev` Decorators

Custom middlewares declare placement relative to existing middlewares:

```python
from agent_workspace.agents import Next, Prev

@Next(SandboxMiddleware)
class MyAuditMiddleware(AgentMiddleware):
    """Executes immediately after SandboxMiddleware"""
    def before_agent(self, state, runtime):
        ...

@Prev(ClarificationMiddleware)
class MyFilterMiddleware(AgentMiddleware):
    """Executes immediately before ClarificationMiddleware"""
    def after_model(self, state, runtime):
        ...
```

Algorithm for `_insert_extra`:
1. Iterate over `extra_middleware`, inspecting `_next_anchor` / `_prev_anchor`.
2. Reject conflicting declarations with `ValueError`.
3. Insert middlewares with declarations at their target relative position.
4. Append undeclared middlewares directly before `ClarificationMiddleware`.

## 4. Middleware Differences: Lead Agent vs Subagents

The lead agent and subagents share the foundational middleware pipeline (`_build_runtime_middlewares`), but subagents maintain a streamlined set:

| Middleware | Lead Agent | Subagent | Description |
|---|:---:|:---:|---|
| ThreadDataMiddleware | ✓ | ✓ | Shared: creates thread data directories |
| UploadsMiddleware | ✓ | ✗ | Lead only: scans uploaded files |
| SandboxMiddleware | ✓ | ✓ | Shared: acquires and releases sandboxes |
| DanglingToolCallMiddleware | ✓ | ✗ | Lead only: resolves unfulfilled tool calls |
| GuardrailMiddleware | ✓ | ✓ | Shared: authorization check on tools |
| ToolErrorHandlingMiddleware | ✓ | ✓ | Shared: tool error recovery |
| SummarizationMiddleware | ✓ | ✗ | Lead only |
| TodoMiddleware | ✓ | ✗ | Lead only |
| TitleMiddleware | ✓ | ✗ | Lead only |
| MemoryMiddleware | ✓ | ✗ | Lead only |
| ViewImageMiddleware | ✓ | ✗ | Lead only |
| SubagentLimitMiddleware | ✓ | ✗ | Lead only |
| LoopDetectionMiddleware | ✓ | ✗ | Lead only |
| ClarificationMiddleware | ✓ | ✗ | Lead only |