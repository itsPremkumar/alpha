---
sidebar_position: 1
title: Overview
description: Skills system in Alpha
slug: /guides/skills
---

# Skills

Alpha's skill system allows extending agent capabilities through reusable, composable skills.

## What are Skills?

Skills are self-contained packages that extend agent capabilities:
- **Tools**: Custom functions the agent can call
- **Prompts**: Specialized prompts for specific tasks
- **Workflows**: Pre-defined multi-step processes
- **Knowledge**: Domain-specific knowledge

## Skill Structure

```
skill-name/
├── SKILL.md          # Manifest
├── scripts/          # Tool implementations
├── templates/        # Prompt templates
├── references/       # Reference docs
└── tests/            # Test cases
```

## SKILL.md Manifest

```markdown
# SKILL.md

name: "my-skill"
description: "Skill description"
version: "1.0.0"
author: "Author Name"
license: "MIT"

capabilities:
  - web_search
  - code_execution

tools:
  - name: "my_tool"
    description: "Tool description"
    parameters:
      type: object
      properties:
        input:
          type: string
    execute: |
      # Tool implementation
      return {"result": "success"}

prompts:
  - name: "my_prompt"
    template: |
      You are a helpful assistant.
      Task: {{task}}

workflows:
  - name: "my_workflow"
    steps:
      - tool: my_tool
        args:
          input: "{{input}}"

requirements:
  - python>=3.10
  - requests>=2.28
```

## Skill Structure

| File/Directory | Purpose |
|----------------|---------|
| `SKILL.md` | Manifest with metadata |
| `scripts/` | Tool implementations |
| `templates/` | Prompt templates |
| `references/` | Reference docs |
| `tests/` | Test cases |

## Installation

```bash
# Install from local path
/skill install ./my-skill

# Install from registry
/skill install my-skill

# List installed skills
/skills list
```

## Skill Discovery

```bash
# List all available skills
/skills list

# Search skills
/skills search "web search"

# Inspect skill
/skills inspect my-skill
```

## Skill Categories

| Category | Description |
|----------|-------------|
| `research` | Web research, data gathering |
| `coding` | Code generation, review |
| `analysis` | Data analysis, visualization |
| `writing` | Content generation, editing |
| `automation` | Workflow automation |
| `security` | Security scanning, auditing |

## Built-in Skills

| Skill | Description |
|-------|-------------|
| `web-search` | Web search and scraping |
| `code-review` | Code review and analysis |
| `data-analysis` | Data analysis and visualization |
| `documentation` | Documentation generation |
| `testing` | Test generation and execution |

## Creating Custom Skills

### 1. Create Skill Directory

```bash
mkdir my-skill
cd my-skill
```

### 2. Create SKILL.md

```markdown
# SKILL.md

name: "my-custom-skill"
description: "Custom skill for specific task"
version: "1.0.0"
author: "Your Name"
license: "MIT"

capabilities:
  - custom_capability

tools:
  - name: "my_tool"
    description: "Custom tool"
    parameters:
      type: object
      properties:
        input:
          type: string
    execute: |
      # Tool implementation
      return {"result": "success"}
```

### 3. Create Tool Scripts

```python
# scripts/my_tool.py
async def execute(input: str) -> dict:
    """Custom tool implementation"""
    result = process(input)
    return {"result": result}
```

### 4. Install and Test

```bash
/skill install ./my-skill
/skill test my-skill
```

## Skill Permissions

```yaml
# config.yaml
skills:
  enabled: true
  allowed_skills:
    - "web-search"
    - "code-review"
    - "my-custom-skill"
```

## Skill Marketplace

- **Public skills**: Community-contributed skills
- **Private skills**: Organization-internal skills
- **Versioning**: Semantic versioning supported

## Skill Testing

```bash
# Run skill tests
/skill test my-skill

# Run with coverage
/skill test my-skill --coverage
```

## Best Practices

1. **Single responsibility**: One skill, one purpose
2. **Clear documentation**: Comprehensive SKILL.md
2. **Error handling**: Graceful error handling
3. **Testing**: Unit tests for all tools
4. **Versioning**: Semantic versioning
5. **Dependencies**: Minimal, explicit dependencies

## Next Steps

- [Creating Custom Skills](/guides/skills/creating-skills)
- [Skill Marketplace](/guides/skills/marketplace)
- [Skill Testing](/guides/skills/testing)