---
sidebar_position: 1
title: Overview
description: Guide to agents and subagents in Alpha
slug: /guides/agents
---

# Agents Guide

Alpha provides a flexible agent system with support for lead agents, subagents, and specialized agent profiles.

## Agent Types

| Type | Description | Use Case |
|------|-------------|----------|
| **Lead Agent** | Main orchestrator | Primary conversation handler |
| **Subagents** | Specialized delegates | Task-specific work |
| **Roster Bots** | Pre-configured agents | Role-based specialists |

## Lead Agent

The lead agent is the primary orchestrator that handles user conversations, manages tools, and coordinates subagents.

### Configuration

```yaml
# config.yaml
models:
  - name: "gpt-4"
    provider: "openai"
    model: "gpt-4-turbo-preview"
    is_default: true
    context_window: 128000
    max_tokens: 4096

autonomy:
  profiles:
    off:
      enabled: false
    assist:
      enabled: true
    autonomous:
      enabled: true
    apex_max:
      enabled: true
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `model_name` | string | `gpt-4` | Default model |
| `thinking_enabled` | boolean | `false` | Enable extended thinking |
| `reasoning_effort` | string | `medium` | Reasoning level |
| `subagent_enabled` | boolean | `false` | Enable delegation |
| `max_concurrent_subagents` | integer | `3` | Max parallel subagents |

## Subagents

Subagents are specialized agents that can be spawned to handle specific tasks.

### Spawning Subagents

```bash
# Via slash command
/subagent spawn researcher "Research best practices for FastAPI"

# Via API
POST /api/subagents/spawn
{
  "role": "researcher",
  "objective": "Research best practices for FastAPI"
}
```

### Built-in Subagent Types

| Role | Description | Capabilities |
|------|-------------|--------------|
| `researcher` | Web research | `web_search`, `code_execution` |
| `coder` | Code implementation | `code_execution`, `file_write` |
| `reviewer` | Code review | `code_execution`, `file_read` |
| `analyst` | Data analysis | `data_analysis`, `visualization` |

### Custom Subagents

Create custom subagents by defining a skill:

```markdown
# SKILL.md
name: "custom-researcher"
description: "Specialized researcher for API documentation"
role: "researcher"
capabilities:
  - web_search
  - code_execution
model: "gpt-4"
config:
  temperature: 0.3
  max_tokens: 4096
```

### Subagent Configuration

```yaml
subagents:
  enabled: true
  max_total_per_run: 10
  max_concurrent: 3
```

### Subagent Limits

| Limit | Default | Description |
|-------|---------|-------------|
| `max_total_per_run` | 10 | Max subagents per run |
| `max_concurrent` | 3 | Max parallel subagents |
| `max_delegation_depth` | 5 | Max delegation depth |

## Roster Bots

Roster bots are pre-configured agents available for direct messaging and @mentions.

### Configuration

```yaml
# config.yaml
bots:
  - name: "researcher"
    display_name: "Research Agent"
    role: "researcher"
    department: "research"
    model: "gpt-4"
    capabilities: ["web_search", "code_execution"]
```

### Direct Messaging

Start a direct conversation with a bot:

```bash
# Via CLI
alpha chat --bot researcher "Research REST API best practices"

# Via API
POST /api/threads
{
  "bot_name": "researcher",
  "message": "Research REST API best practices"
}
```

### @mentions

Tag bots in group chats:

```
@researcher Please research FastAPI best practices
@coder Implement the user authentication module
```

## Agent Profiles

Configure agent behavior profiles:

```yaml
profiles:
  default:
    temperature: 0.7
    max_tokens: 4096
  researcher:
    temperature: 0.3
    max_tokens: 8192
  coder:
    temperature: 0.2
    max_tokens: 8192
```

## Next Steps

- [Creating Custom Agents](/guides/agents/creating-agents)
- [Subagent Spawning](/guides/agents/spawning-subagents)
- [Agent Communication](/guides/agents/agent-communication)