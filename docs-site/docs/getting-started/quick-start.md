---
sidebar_position: 1
title: Quick Start
description: Get Alpha running in 5 minutes
slug: /getting-started/quick-start
---

# Quick Start

Get Alpha running in 5 minutes with Docker.

## Prerequisites

- **Docker** + **Docker Compose v2.24+**
- **Node.js 22+**, **pnpm 11+**
- **Python 3.12+**, **uv**
- **nginx** (project-local binary included)

## 1. Clone & Configure

```bash
# Clone the repository
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

# Copy configuration template
cp config.example.yaml config.yaml

# Edit config.yaml with your API keys
# At minimum, set your OpenAI API key:
# OPENAI_API_KEY: "sk-..."
```

## 2. Start with Docker

```bash
# Initialize Docker environment (first time only)
make docker-init

# Start all services
make docker-start
```

This starts:
- **Frontend** (Next.js) — http://localhost:3000
- **Gateway** (FastAPI) — http://localhost:8001
- **nginx** — http://localhost:2026 (unified entry point)

## 3. Access the UI

Open **http://localhost:2026** in your browser.

You should see the Alpha chat interface. Try typing a message!

## 4. Try Your First Commands

In the chat, try these commands:

```
/help                    # Show all available commands
/goal create "Build a simple REST API with FastAPI"  # Create an autonomous goal
/agent spawn researcher  # Spawn a research subagent
/tools                   # List available tools
/skills list             # List available skills
```

## Next Steps

- [Installation](/getting-started/installation) — Detailed installation guide
- [Configuration](/getting-started/configuration) — Configure Alpha for your environment
- [First Agent](/getting-started/first-agent) — Create and run your first agent
- [Architecture](/architecture) — Deep dive into Alpha's architecture