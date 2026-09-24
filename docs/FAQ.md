# Alpha FAQ

Short, citable answers about [Alpha](https://github.com/itsPremkumar/alpha). For agent-friendly summaries see [`llms.txt`](../llms.txt). Long-form guides live in the [documentation index](README.md).

## What is Alpha?

Alpha is an open-source autonomous multi-agent AI operating system. A LangGraph-based Python backend (FastAPI Gateway + agent runtime) pairs with a Next.js 15 workspace UI and an Electron Windows desktop shell. It runs long-horizon tasks with sandboxed execution, persistent memory, subagent delegation, 120+ native tools, MCP extensions, and 24 public skills. Maintained by [Prem Kumar](https://github.com/itsPremkumar) under MIT.

## What is the tech stack?

Backend: Python 3.12+, LangGraph agent runtime, FastAPI Gateway. Frontend: Next.js 15, React 19, Tailwind. Desktop: Electron. Edge: Nginx reverse proxy. Persistence: SQLite/PostgreSQL, vector memory, AES-GCM encrypted checkpoints. Optional sandbox provisioner for Docker/Kubernetes modes.

## How do I run Alpha locally?

1. `make config` — copies `config.example.yaml` to `config.yaml` and `extensions_config.example.json` to `extensions_config.json` (both gitignored).
2. `make install` — installs backend + frontend dependencies.
3. `make dev` — starts Gateway (`:8001`), Frontend (`:3000`), Nginx (`:2026`); open `http://localhost:2026`.
Full guide: [Getting started](GETTING_STARTED.md). Without `config.yaml`, services fail to boot.

## Which deploy option should I choose?

- **Windows desktop (Electron):** one-click app in `electron/`, own Node.js + `uv` runtimes. Best for single-user desktop use.
- **Docker Compose:** `make up` / `make down`, browser at `http://localhost:2026`. Best for reproducible team deployments.
- **Bare-metal local dev:** `make dev` with hot reload. Best for active development.
- **CI/headless:** `make setup SETUP_ARGS=--non-interactive` with `AGENT_WORKSPACE_SETUP_*` env vars.

## What ports and routes does it use?

Nginx `:2026` is the single public entry. It proxies `/api/*` to the Gateway (`:8001`) — rewriting `/api/langgraph/*` onto native Gateway routes — and serves the Frontend (`:3000`). Direct access without Nginx: Gateway `http://localhost:8001`, Frontend `http://localhost:3000`.

## How is configuration layered?

Three files: `config.yaml` (models, tool groups, sandbox, channels; operator-edited), `extensions_config.json` (MCP servers + skills; API-editable at runtime), `.env` (API keys, DB strings, secrets; never commit). Copy both templates via `make config`. Schema and resolution order: [Configuration](CONFIGURATION.md).

## What can the agent actually do?

Deep research (5-pass search, citation contract, gap filling), swarm/multi-agent rooms with Kanban and A2A messaging, planners and goal-integrity gates, code repair with AST editing and git checkpoints, sandboxed Python/Bash, browser automation, scheduled cron tasks, and omnichannel messaging (Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz) plus an OpenAI-compatible chat endpoint.

## How many tools, skills, and endpoints ship?

120+ built-in tools (127 pinned in the manifest), 24 public skills in `skills/public/`, and 57 Gateway routers in `backend/app/gateway/routers/`. The generated [`contracts/feature_manifest.json`](../contracts/feature_manifest.json) pins the full registry; regenerate it after any registry change.

## How do I add models, MCP servers, or skills?

Models go in `config.yaml` (`models:` list, `$ENV_VAR` for keys). MCP servers and skills go in `extensions_config.json` (editable via Settings or `PUT/PATCH /api/mcp/config`). Third-party Python extensions use the top-level `plugins:` list in `config.yaml` (operator-controlled; requires Gateway restart).

## How is security handled?

Astra enclave + credential vault, smart command approval gate, emergency stop, trajectory flight recorder, artifact lineage tracing, per-run token budgets, and per-thread sandbox isolation. Never put secrets in `config.yaml` literals — use `$VAR` + `.env`.

## Is Alpha production ready?

Partially. The run lifecycle, auth/thread ownership, health probes (`/health`, `/health/ready`), and wiring checks are implemented; evidence verification, centralized policy grants, backup/restore drills, and release scorecards are still open. See [Production readiness inventory](PRODUCTION_READINESS_INVENTORY.md) and [Production guide](PRODUCTION.md) before exposing a deployment.

## How does Alpha differ from DeerFlow / OpenHands / Claude Code?

Alpha re-architects the DeerFlow base (see [Upstream credits](../README.md#12-upstream-credits--open-source-provenance)) into a full OS: persistent multi-session checkpointing, swarm workforce with presence/locks/constitutions, frontier cognition (AVO, MoA, ToM, dreaming), and a Windows desktop + Docker + bare-metal topology behind one Nginx entry — rather than a single coding CLI.

## Where do I get help or report issues?

Run `make doctor` and `make prod-check`, check [Troubleshooting](TROUBLESHOOTING.md), then open an issue at [github.com/itsPremkumar/alpha](https://github.com/itsPremkumar/alpha) with the redacted support bundle (`make support-bundle`), trace id (`X-Trace-Id` header), and relevant Gateway/Frontend logs.
