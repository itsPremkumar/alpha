# Skills System Documentation

## Overview

The skills system provides extensible capabilities for agents. Skills are Python packages that contribute tools, workflows, and integrations to the agent runtime.

## Skill Architecture

```
┌─────────────────────────────────────────────────────────────────┐
                        Skills System
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │   Public    │    │   Custom    │    │ Integration │        │
│  │  Skills     │    │  Skills     │    │  Skills     │        │
│  │ (Committed) │    │ (Local Only)│    │ (Managed)   │        │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘        │
│         │                  │                  │                │
│         └──────────────────┼──────────────────┘                │
│                            ▼                                   │
│                   ┌─────────────────┐                          │
│                   │  Skill Loader   │                          │
│                   │  (Runtime)      │                          │
│                   └────────┬────────┘                          │
│                            │                                   │
│                   ┌────────▼────────┐                          │
│                   │  Agent Tools    │                          │
│                   │  Registry       │                          │
│                   └─────────────────┘                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Skill Types

### 1. Public Skills
- **Location**: `skills/public/`
- **Version controlled**: Yes
- **Distribution**: Built into repository
- **Review**: Required (skill-reviewer skill)
- **Examples**: `skill-reviewer`, `web-search`, `code-execution`, `file-operations`

### 2. Custom Skills
- **Location**: `skills/custom/` (gitignored)
- **Version controlled**: No
- **Distribution**: Local only
- **Review**: Optional
- **Use case**: Organization-specific, experimental

### 3. Integration Skills
- **Location**: `.agent-workspace/integrations/skills/{provider}/`
- **Managed by**: Extension system
- **Credentials**: Per-user, stored securely
- **Examples**: GitHub, Slack, Jira, Linear, Notion

## Skill Structure

### Directory Layout
```
skill-name/
├── SKILL.md              # Manifest (required)
├── main.py               # Entry point (required)
├── requirements.txt      # Python dependencies
├── pyproject.toml        # Modern packaging (optional)
├── tools/                # Tool implementations
│   ├── __init__.py
│   └── tool_name.py
├── workflows/            # Pre-defined workflows
│   └── workflow_name.py
├── prompts/              # Prompt templates
│   └── template.j2
├── tests/                # Skill tests
│   ├── test_skill.py
│   └── fixtures/
└── assets/               # Static assets
    └── icon.png
```

### SKILL.md Manifest
```markdown
---
name: skill-name
version: 1.0.0
description: Brief description of what this skill does
author: Author Name
license: MIT
tags: [tag1, tag2, category]
requires: []                    # Other skill IDs required
conflicts: []                   # Skill IDs that conflict
min_agent_version: "2.0.0"     # Minimum agent version
max_agent_version: ""           # Maximum (empty = no limit)
python_version: ">=3.12"        # Python version requirement
entry_point: "main:SkillClass"  # Module:Class for loading
config_schema:                  # Configuration schema (JSON Schema)
  type: object
  properties:
    api_key:
      type: string
      description: "API key for external service"
    timeout:
      type: integer
      default: 30
      description: "Request timeout in seconds"
permissions:                    # Required permissions
  - network: https://api.example.com
  - filesystem: read
  - filesystem: write
  - subprocess: python
---

# Skill Name

Detailed description of the skill's capabilities and usage.

## Tools

### `tool_name`
Description of what the tool does.

**Parameters:**
- `param1` (string, required): Description
- `param2` (integer, optional): Description, default: 10

**Returns:** Description of return value

**Example:**
```python
result = await tool_name(param1="value", param2=20)
```

## Workflows

### `workflow_name`
Description of the workflow.

**Steps:**
1. Step description
2. Step description

## Configuration

Describe configuration options and how to set them.

## Examples

Provide usage examples.

## Changelog

### 1.0.0 (2026-09-17)
- Initial release
```

### Main Entry Point (main.py)
```python
from agent_workspace.skills import Skill, tool, workflow
from agent_workspace.skills.decorators import requires_config

class MySkill(Skill):
    """Skill description for the registry."""
    
    name = "my-skill"
    version = "1.0.0"
    description = "Brief description"
    
    # Optional: Configuration validation
    @requires_config("api_key")
    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config["api_key"]
        self.timeout = config.get("timeout", 30)
    
    @tool
    async def my_tool(self, query: str, limit: int = 10) -> dict:
        """Tool description for the agent.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            Dictionary with results
        """
        # Implementation
        return {"results": [], "count": 0}
    
    @workflow
    async def my_workflow(self, topic: str) -> str:
        """Workflow description.
        
        Args:
            topic: Topic to research
            
        Returns:
            Final report
        """
        # Multi-step workflow
        results = await self.my_tool(topic)
        # ... process results
        return "Report content"
    
    async def on_load(self) -> None:
        """Called when skill is loaded."""
        pass
    
    async def on_unload(self) -> None:
        """Called when skill is unloaded."""
        pass
```

## Built-in Tools

### Core Tools (Always Available)
| Tool | Description |
|------|-------------|
| `search_web` | Web search via multiple providers |
| `visit_url` | Fetch and extract content from URL |
| `execute_code` | Run Python code in sandbox |
| `read_file` | Read file from workspace |
| `write_file` | Write file to workspace |
| `list_files` | List directory contents |
| `glob_files` | Find files by pattern |
| `grep_files` | Search file contents |
| `bash` | Execute shell command |
| `ask_clarification` | Ask user for clarification |

### Skill-Provided Tools
Skills register tools dynamically. Example categories:

#### Data & Analysis
- `sql_query` - Execute SQL queries
- `data_analysis` - Pandas-based analysis
- `chart_generation` - Create visualizations

#### Development
- `github_api` - GitHub REST/GraphQL API
- `git_operations` - Git repository operations
- `code_review` - Automated code review
- `test_generation` - Generate unit tests

#### Communication
- `send_email` - Send emails
- `slack_message` - Post to Slack
- `create_issue` - Create issue in tracker

#### Research
- `academic_search` - Search academic papers
- `patent_search` - Search patents
- `news_search` - Search news articles

## Skill Development

### Creating a New Skill

#### 1. Use the Template
```bash
# Generate from template
cd skills/public
python -m agent_workspace.skills.create_skill my-new-skill
```

#### 2. Implement Tools
```python
# tools/my_tool.py
from agent_workspace.skills import tool
from pydantic import BaseModel, Field

class MyToolInput(BaseModel):
    query: str = Field(..., description="Search query")
    max_results: int = Field(10, ge=1, le=100)

@tool
async def my_tool(input: MyToolInput) -> dict:
    """Search for information."""
    # Validate input (automatic via Pydantic)
    # Call external API
    # Return structured result
    return {"results": [], "total": 0}
```

#### 3. Register Tools
```python
# main.py
from agent_workspace.skills import Skill
from .tools.my_tool import my_tool

class MySkill(Skill):
    name = "my-skill"
    
    # Tools are auto-discovered from @tool decorators
    # Or explicitly register:
    tools = [my_tool]
```

#### 4. Add Tests
```python
# tests/test_my_skill.py
import pytest
from skills.public.my_skill.main import MySkill

@pytest.fixture
def skill():
    return MySkill(config={"api_key": "test"})

@pytest.mark.asyncio
async def test_my_tool(skill):
    result = await skill.my_tool("test query", 5)
    assert "results" in result
    assert isinstance(result["results"], list)
```

#### 5. Run Skill Review
```bash
# Automated review
cd backend && python -m agent_workspace.skills.review skills/public/my-skill

# Or via API
curl -X POST http://localhost:8001/api/skills/review \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"skill_path": "skills/public/my-skill"}'
```

### Skill Review Criteria

#### Blocker (Must Fix)
- Security vulnerabilities (eval, exec, shell injection)
- Hardcoded secrets
- Missing input validation
- Unbounded resource usage
- Breaking changes without migration

#### Error (Should Fix)
- Missing type hints
- Incomplete docstrings
- Poor error handling
- Non-standard patterns
- Missing tests

#### Warning (Consider)
- Performance improvements
- Better abstraction
- Additional documentation
- Configuration validation

#### Info (Optional)
- Style suggestions
- Alternative approaches
- Future enhancements

### Publishing Skills

#### Public Skills (Repository)
1. Create PR to `skills/public/`
2. Pass skill review (CI)
3. Merge to main
4. Available to all users

#### Custom Skills (Local)
```bash
# Enable in extensions_config.json
{
  "skills": {
    "enabled": ["my-custom-skill"],
    "directories": ["skills/public", "skills/custom"]
  }
}
```

#### Integration Skills (Managed)
```bash
# Install via extension manager
make extension-install SOURCE=git+https://github.com/org/integration-skills.git

# Or via UI: Settings → Skills → Install Integration
```

## Skill Lifecycle

### Loading
1. Scan skill directories on startup
2. Validate manifest (SKILL.md)
3. Check dependencies (requirements.txt)
4. Load entry point class
5. Register tools in registry
6. Run `on_load()` hook

### Execution
1. Agent requests tool
2. Registry finds skill
3. Instantiate skill (with config)
4. Execute tool method
5. Return result

### Unloading
1. Run `on_unload()` hook
2. Cleanup resources
3. Remove from registry

### Hot Reload (Development)
```bash
# Watch for changes
cd backend && python -m agent_workspace.skills.watch skills/public/my-skill
```

## Skill Curation System

### Trust Tiers
| Tier | Description | Capabilities |
|------|-------------|--------------|
| Quarantine | New/unreviewed | Limited tools, no network |
| Reviewed | Passed review | Full tools, network allowed |
| Trusted | Widely used, audited | Elevated permissions |
| Core | Built-in | Full system access |

### Curation API
```bash
# Get curation stats
GET /api/skills/curator/stats

# Get recommendations
GET /api/skills/curator/recommendations

# Promote skill
POST /api/skills/curator/promote
{"skill_id": "skill-name", "tier": "reviewed"}

# Archive skill
POST /api/skills/curator/archive
{"skill_id": "skill-name"}
```

### Usage Telemetry
- Tool call counts
- Success/failure rates
- Latency percentiles
- User feedback scores
- Auto-archived after 90 days inactive

## Skill Authoring (/learn)

### Interactive Skill Creation
1. User types `/learn` in chat
2. Describe desired capability
3. Agent generates skill scaffold
3. User refines via conversation
4. Skill saved to `skills/custom/`
5. Immediately available

### Example Flow
```
User: /learn Create a skill that fetches stock prices from Alpha Vantage

Agent: I'll create a stock-price skill. Let me generate the structure...

[Generates SKILL.md, main.py, tools/stock_price.py, tests/]

User: Add support for historical data

Agent: Adding historical_data tool with date range parameters...

[Updates files]

User: Save it

Agent: Skill saved to skills/custom/stock-price. You can now use @stock_price in chat.
```

## Configuration

### Skill Config (extensions_config.json)
```json
{
  "skills": {
    "enabled": ["web-search", "code-execution", "my-custom-skill"],
    "disabled": ["deprecated-skill"],
    "directories": [
      "skills/public",
      "skills/custom",
      ".agent-workspace/integrations/skills"
    ],
    "config": {
      "web-search": {
        "provider": "brave",
        "api_key": "${BRAVE_API_KEY}"
      },
      "my-custom-skill": {
        "api_key": "${MY_API_KEY}",
        "timeout": 60
      }
    }
  }
}
```

### Per-Skill Config (config.yaml)
```yaml
skills:
  my-skill:
    api_key: "${MY_API_KEY}"
    timeout: 30
    cache_ttl: 300
```

## Testing Skills

### Unit Tests
```bash
# Run skill tests
cd skills/public/my-skill && python -m pytest tests/ -v
```

### Integration Tests
```bash
# Test with running agent
cd backend && python -m pytest tests/skills/test_my_skill_integration.py -v
```

### Load Testing
```bash
# Benchmark skill performance
cd backend && python -m agent_workspace.skills.benchmark my-skill
```

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Skill not loading | Invalid SKILL.md | Validate manifest syntax |
| Tool not found | Not registered | Check @tool decorator, entry point |
| Config error | Missing required config | Add to extensions_config.json |
| Dependency conflict | Version mismatch | Pin versions in requirements.txt |
| Permission denied | Missing permissions | Add to SKILL.md permissions |
| Timeout | Slow external API | Increase timeout, add caching |

### Debug Commands
```bash
# List loaded skills
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/skills

# Get skill details
curl -H "Authorization: Bearer <token>" http://localhost:8001/api/skills/my-skill

# Test tool directly
curl -X POST http://localhost:8001/api/skills/my-skill/tools/my_tool \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'

# View skill logs
docker compose logs gateway | grep "my-skill"
```

## Best Practices

### Design Principles
1. **Single responsibility** - One skill, one domain
2. **Composability** - Tools work independently
3. **Idempotency** - Safe to retry
4. **Observability** - Log inputs/outputs
5. **Graceful degradation** - Handle failures

### Performance
- Cache external API responses
- Use connection pooling
- Batch operations when possible
- Async/await for I/O
- Set reasonable timeouts

### Security
- Never log secrets
- Validate all inputs
- Use least privilege
- Sanitize outputs
- Audit dependencies

### Maintainability
- Comprehensive tests (>80% coverage)
- Clear documentation
- Semantic versioning
- Changelog maintenance
- Deprecation notices

## Migration Guide

### From v1 to v2
- Entry point changed from `skill.py` to `main.py`
- `@tool` decorator now requires type hints
- Config schema moved to SKILL.md
- Permissions field added

### Breaking Changes
- Check `max_agent_version` in manifest
- Test with `make test-skills`
- Review migration guide in CHANGELOG.md