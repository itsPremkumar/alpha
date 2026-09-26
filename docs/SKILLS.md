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
- **Review**: Required (`skill-reviewer` skill)
- **Included Skills (24 Public Packages)**:
  1. `academic-paper-review`: Peer review evaluation, methodology assessment, and synthesis of research papers.
  2. `bootstrap`: Full repository scaffolding, boilerplate generation, and project environment setup.
  3. `chart-visualization`: Interactive charts, telemetry graphs, and visual dashboards.
  4. `claude-to-agent-workspace`: Adapter and converter for importing skills and prompts from Claude Code/Codex formats.
  5. `code-documentation`: Automated generation of architecture guides, docstrings, API references, and comments.
  6. `consulting-analysis`: Strategic management frameworks (SWOT, Porter's Five Forces, BCG Matrix, MECE trees).
  7. `data-analysis`: Tabular processing, statistical data modeling, pattern recognition, and trend forecasting.
  8. `deep-research`: Autonomous 5-pass web research, recursive knowledge gap filling, and publication-ready cited briefs.
  9. `find-skills`: Semantic discovery engine locating skills across local and public registries.
  10. `frontend-design`: High-fidelity, accessible UI component generation and modern styling systems.
  11. `github-deep-research`: Repository audits, commit history investigations, and issue triage.
  12. `image-generation`: Multi-modal image prompt synthesis, style matching, and pipeline execution.
  13. `music-generation`: Musical structure design, BPM/key configuration, and audio prompt formulation.
  14. `newsletter-generation`: Curated industry digests, executive summaries, and publication-grade newsletters.
  15. `podcast-generation`: Multi-speaker dialogue scriptwriting and audio storyboarding.
  16. `ppt-generation`: Presentation slide decks, visual outlines, and speaker note generation.
  17. `project-cartographer`: Codebase structural mapping, dependency graphing, and architectural cartography.
  18. `skill-creator`: Autonomous skill synthesis creating reusable skills from successful agent trajectories.
  19. `skill-reviewer`: Security auditing, compliance testing, and trust-tier classification for agent skills.
  20. `surprise-me`: Open-ended creative problem solving, generative ideas, and unexpected technical exploration.
  21. `systematic-literature-review`: PRISMA-compliant academic research reviews with formal citation matrices.
  22. `vercel-deploy-claimable`: One-click instant cloud deployment to Vercel with automated claim URLs.
  23. `video-generation`: Scene-by-scene scriptwriting, camera angle prompts, and video storyboarding.
  24. `web-design-guidelines`: Modern web design heuristics, responsive layouts, and WCAG accessibility standards.

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
`name` and `description` are the only required keys. The validator
(`alpha/skills/validation.py:42` via
`alpha.skills.frontmatter.ALLOWED_FRONTMATTER_PROPERTIES`) **rejects any key
outside this closed set**, so a manifest carrying anything else fails to load:

`name`, `description`, `license`, `allowed-tools`, `argument-hint`,
`required-secrets`, `secrets-autonomous`, `metadata`, `compatibility`,
`version`, `author`

```markdown
---
name: skill-name
description: Brief description of what this skill does
version: 1.0.0
author: Author Name
license: MIT
allowed-tools: [tool_name]        # Restrict the tool surface for this skill
required-secrets: [API_KEY]       # Secrets the skill needs; see required-secrets below
metadata:                        # Free-form operator metadata
  category: research
---
```

There is no `tags`, `requires`, `conflicts`, `min_agent_version`,
`max_agent_version`, `python_version`, `entry_point`, `config_schema` or
`permissions` key. Dependencies, permissions and per-skill configuration are
declared through the mechanisms documented below (`required-secrets`,
`allowed-tools`, and `config.yaml` / `extensions_config.json` per-skill
`config`), not through frontmatter.

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

### Main Entry Point (main.py)
```python
from alpha.skills import Skill, tool, workflow
from alpha.skills.decorators import requires_config

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
python -m alpha.skills.create_skill my-new-skill
```

#### 2. Implement Tools
```python
# tools/my_tool.py
from alpha.skills import tool
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
from alpha.skills import Skill
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
cd backend && python -m alpha.skills.review skills/public/my-skill

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
cd backend && python -m alpha.skills.watch skills/public/my-skill
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
        "api_key": "${BRAVE_SEARCH_API_KEY}"
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
cd backend && python -m alpha.skills.benchmark my-skill
```

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Skill not loading | Invalid SKILL.md | Validate manifest syntax |
| Tool not found | Not registered | Check @tool decorator, entry point |
| Config error | Missing required config | Add to extensions_config.json |
| Dependency conflict | Version mismatch | Pin versions in requirements.txt |
| Permission denied | Missing permissions | Grant via the `allowed-tools` list / `config.yaml` policy — there is no `permissions` frontmatter key |
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
- Per-skill config moved **out** of SKILL.md frontmatter into `config.yaml` /
  `extensions_config.json` — there is no `config_schema` frontmatter key
- Permissions are expressed by the `allowed-tools` frontmatter list and the
  `config.yaml` skill policy, not by a `permissions` frontmatter key

### Breaking Changes
- Review the migration guide in CHANGELOG.md
- Re-validate the edited `SKILL.md` — a key outside the allowed set above is
  rejected, so a manifest copied from an older layout stops loading:
  `cd backend && python -m pytest tests/test_skills_validation.py -q`

---

## Skill Quality Review and CI Waivers

`skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
It uses the harness-layer `review_skill_package` tool and contracts in
`contracts/skill_review/`. Model-visible review data is compact and
tag-neutralized; full raw payloads stay in tool artifacts. See
`backend/AGENTS.md` for the non-activation, SkillScan, and `skill-creator`
ownership boundaries.

CI waivers live in `.github/skill-review-waivers.v1.json` and are enforced by
`scripts/review_changed_public_skills.py`. Pull requests may validate waiver edits
from their head revision, but only the manifest from the trusted base revision
can suppress that run. Entries match one error finding exactly, include the
reviewed file's SHA-256 and an expiry date, remain visible in CI output, and can
never waive blocker findings. An entry may also preapprove future full-file
SHA-256 values, effective only once the manifest change lands in the trusted base
— so relying on a waiver takes two merges: the manifest first, the skill change
after, then promote the consumed hash to `file_sha256` in a follow-up cleanup.