---
sidebar_position: 4
title: First Agent
description: Create and run your first agent
slug: /getting-started/first-agent
---

# Your First Agent

Create and run your first agent in Alpha.

## Prerequisites

- Alpha running (see [Quick Start](/getting-started/quick-start))
- API key configured in `config.yaml`

## Option 1: Web UI

1. Open http://localhost:2026
2. Click "New Chat"
3. Type a message and press Enter
4. Alpha will respond using the default agent

## Option 2: CLI

```bash
# Start a new thread
alpha chat "Hello, world!"

# Or with a specific model
alpha chat --model gpt-4 "Explain quantum computing"
```

## Option 3: API

```bash
# Create a thread
curl -X POST http://localhost:8001/api/threads \
  -H "Content-Type: application/json" \
  -d '{"title": "My First Thread"}'

# Send a message
curl -X POST http://localhost:8001/api/threads/{thread_id}/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello, world!"}'
```

## Your First Goal

Create an autonomous goal:

```
/goal create "Build a simple REST API with FastAPI"
```

This will:
1. Create a mission with the objective
2. Decompose it into subgoals
3. Execute autonomously

## Your First Subagent

Choose a specialist from the **Subagent mode** selector beside the composer, then
ask Alpha to research the topic. A specialist agent can:
1. Researches the topic
2. Returns findings
3. Can be used in your main conversation

## Your First Swarm

For independent work, ask Alpha to coordinate a swarm and state the task,
desired number of workers, shared project scope, and acceptance criteria. Alpha
uses the available swarm tool when the request can be safely decomposed.

## Using the Slash Command Palette

Type `/` in the composer to inspect the slash-command catalog. Catalog entries
without a bound handler are marked unavailable; only commands shown as
executable should be submitted as slash commands. Subagent and swarm work is
configured through the composer and natural-language task request. The palette supports:

- **Command families**: Type `/goal` to see all goal-related commands
- **Subcommand expansion**: Click `→` or press `→` on a parent command to expand subcommands
- **Real-time filtering**: Type `/g` to filter commands starting with "g"
- **Server-side preview**: In group chats, see live resolution of @mentions

## Next Steps

- [Architecture](/architecture) - Understand Alpha's architecture
- [Tools](/guides/tools/built-in-tools) - Explore available tools
- [Skills](/guides/skills/overview) - Create custom skills
- [Memory](/guides/memory/working-with-memory) - Work with persistent memory
- Subagents - Learn about subagent delegation
