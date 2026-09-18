# Agent Workspace

An independently maintained AI agent project with a Windows desktop app,
agent orchestration, and local development tooling, maintained by
[Prem Kumar](https://github.com/itsPremkumar).

See [References & Credits](#references--credits) for open-source provenance.
The frontend and backend display name is Agent Workspace. The naming migration
is incomplete: package names, commands, installer filenames, and data paths
still use their existing identifiers.

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![Electron](https://img.shields.io/badge/Electron-Desktop-47848F?logo=electron&logoColor=white)](./electron/README.md)

Version: **2.1.0** — backend, frontend, Helm chart, and desktop installer share
one version (verified by `scripts/verify_versions.sh`).

## What is this project?

- **One-click Windows app** — Electron shell that opens straight into chat, no
  login screen, no setup wizard; Gateway + UI run locally in one window.
- **Advanced agent capabilities** — multi-agent collaboration, planning and
  execution harnesses, knowledge systems, and quality councils, all integrated
  and tested on `main`.
- **Security-first runtime** — task boundaries, encrypted checkpoints,
  hardened HTTP headers, and secret-safe configuration.
- **Production operations** — health/readiness probes, operator endpoints,
  pre-flight checks, and a production runbook out of the box.

## What's new in this distribution

### Desktop app (Windows · Electron)

- Installer (`Agent-Workspace-Setup-<ver>.exe`) bundles its own Node.js and `uv`
  runtimes — end users install nothing else.
- Opens directly into the chat composer; first launch auto-provisions Python
  and the backend environment with splash-screen progress.
- Desktop Gateway defaults to port **8201** so it never fights a dev stack;
  per-user data under `%APPDATA%\agent-workspace-desktop\`.
- Full details: [electron/README.md](./electron/README.md).

### Agent capabilities

- **Multi-agent teamwork** — bot mode, multi-agent group chat, collaborative
  kanban, inter-agent messaging, and background process handles.
- **Bot Mode DMs** — fire-and-forget `message_agent` DMs with roster
  validation, server-side attribution, and per-bot inboxes; SOUL protocol
  guarantee keeps custom SOULs DM-capable (`/api/bots/{name}/dm`, Workforce tab).
- **Self-improving skills** — usage telemetry, idle curator lifecycle
  (active → stale → archived, never deleted), `/learn` authoring bar, and
  quarantine trust tiers (`/api/skills/curator/*`, Workforce tab).
- **Project workforce layer** — agent↔project membership, presence, resource
  locks, constitution, ADR memory, handoffs, evidence-gated completion, and a
  project event bus (`/api/projects/{id}/*`, Workforce tab).
- **Continuous execution harness** — goal engine, context engine, tool-call
  repair, trajectory tracking, canvas, and safety guardrails.
- **Planning** — autonomous one-prompt planner, planning-gate hardening,
  ralph-loop execution, orchestrator and memory backends, agent presets.
- **Engineering & research skills** — autonomous reproduction engine, episodic
  experience memory, skill forge, curriculum and agency training, five-pass
  search, delta checkpoints, cognitive blackboard, repo twin, and
  consequence simulation.
- **Knowledge & evaluation** — enterprise knowledge graph, session search,
  skill proposals with review guard, task evaluation suite, and feedback
  export for eval cases.

### Quality, security, and cost control

- **Quality councils** — multi-deliberator review, recursive context-as-data
  reasoning, adaptive autonomy tiers, and evidence matrices.
- **Security plane** — task boundaries, AES-GCM encrypted checkpoints,
  trajectory flight recorder, and scoped credential vault.
- **Cost control** — per-run token budgets, cumulative token metering, and
  real-cost reporting with cache-aware pricing.
- **Shell integration** — Claude/Codex-style shell-hook bridge for agent
  lifecycle events.

### Production operations

- **Windows launcher checks** — `start.ps1` uses the installed Next.js entrypoint,
  starts the Gateway without dependency synchronization, aborts on frontend build
  failure, and requires Gateway readiness plus HTTP 200 from the frontend.
  This is a local single-user launcher, not an authenticated server deployment.
- **Validation status (2026-09-17)** — the frontend production build, TypeScript
  check, and 55 frontend tests passed. Isolated SQLite startup and thread
  create/read/search/delete through the production frontend proxy passed with
  authentication disabled and no model inference. Full backend tests, global
  lint/format checks, and blocking-I/O tests are not green; authenticated flows,
  external integrations, and live model execution remain unverified. These
  checks do not establish full production readiness.

- **Self-repair status** — symptom diagnosis and disk-health checks are available.
  Automatic repair actions and their runtime verifiers are not implemented;
  repair requests return `refused`, never a fabricated `fixed` result.

- Authenticated operator endpoints: `GET /api/ops/version`,
  `GET /api/ops/status` (uptime, server time, docs flag),
  `GET /api/ops/resources`, `GET /api/ops/advice` (autonomy recommendation).
- OpenAI-compatible chat surface: `POST /api/compat/openai/chat/completions`
  (non-streaming; reuses thread + run lifecycle).
- Cron safety: wake-gate preflights, schedule blueprints, incident tracking
  with auto-pause (`/api/scheduled-tasks/blueprints`, `…/incidents`).
- Baseline security headers on every Gateway and frontend response
  (HSTS for HTTPS only).
- `python scripts/prod_check.py` pre-flight gate (versions, config files,
  secrets) plus `make prod-check`.
- Full runbook: [docs/PRODUCTION.md](./docs/PRODUCTION.md).

## Union Alpha model configuration

`config.example.yaml` now selects OpenRouter's `stealth/union-alpha` as its
first/default model. Set `OPENROUTER_API_KEY` in the Gateway environment and
copy the example to a new `config.yaml`, or merge its model entry into an
existing configuration without overwriting other settings. No credentials are
included and live inference has not been verified. Existing installations and
custom-agent model overrides are unchanged.

The public catalog checked on 2026-09-17 advertises a 262,144-token context,
text/image input, and tool calling. This example caps output at 16,384 tokens
and does not enable unsupported reasoning controls or cross-model fallbacks.
The provider is anonymous and may retain prompts and completions; review its
terms before sending sensitive data. Preview availability and pricing can change.

The source-backed improvement roadmap is in
[the implementation plan](refernace/ultimate-harness-implementation-plan.md).

## Quick start

### Option 1: Windows desktop app (end users)

1. Build the installer once: `cd electron && npm install && npm run dist`.
2. Run `electron/dist/Agent-Workspace-Setup-2.1.0.exe` (unsigned → SmartScreen
   **More info → Run anyway**; per-user install, no admin rights needed).
3. On first prompt, add one model API key to
   `%APPDATA%\agent-workspace-desktop\project\config.yaml`, restart, and chat.

### Option 2: Docker (recommended for servers)

```bash
git clone https://github.com/itsPremkumar/agent-workspace-desktop.git
cd agent-workspace-desktop
cp .env.production.example .env   # fill in real secrets
make config                       # generate config.yaml + extensions_config.json
make install                      # backend + frontend dependencies
python scripts/prod_check.py      # pre-flight (warnings advise, failures block)
make up                           # build + start; open http://localhost:2026
make down                         # stop and remove containers
```

### Option 3: Local development

```bash
make config      # copy templates (gitignored) — required before first boot
make install     # install backend + frontend deps + pre-commit hooks
make dev         # Gateway :8001 + frontend :3000 + nginx :2026 (hot-reload)
make stop        # stop all services
```

Then configure at least one model: run `make setup` (interactive wizard, ~2
minutes) or edit `config.yaml` directly — see `config.example.yaml` for every
provider (OpenAI, Anthropic, Gemini, DeepSeek, GLM, Kimi, MiniMax, Ollama,
vLLM, OpenRouter, Codex/Claude CLI logins) plus `make doctor` to verify.

Unattended (Docker/CI/automation) setup needs no TTY — everything resolves
from the environment, including a generated `BETTER_AUTH_SECRET`:

```bash
AGENT_WORKSPACE_SETUP_PROVIDER=deepseek AGENT_WORKSPACE_SETUP_API_KEY=$DEEPSEEK_API_KEY \
  make setup SETUP_ARGS=--non-interactive
# Omit the provider to auto-detect from exported API keys (or local Ollama).
# See scripts/wizard/noninteractive.py for all AGENT_WORKSPACE_SETUP_* variables.
```

Once a model is configured, work is hands-off: send one prompt and the agent
loop runs tools to completion, delegating to subagents in parallel and
continuing toward `/goal` conditions without re-prompting. If a run is
interrupted (restart, timeout, cancel), continue it from its checkpoint with
**Resume** on the **Overview** page (`/workspace/overview`) instead of
replaying the turn. For team work, open the **Team** page
(`/workspace/team`), type one objective, and **Run team** — every bot works
its slice in parallel and the moderator merges a final deliverable
(`POST /api/groups/{name}/runs`).

## Configuration essentials

| File | Purpose | Committed? |
| ---- | ------- | ---------- |
| `config.yaml` | models, tools, sandbox, memory, channels | No (from `config.example.yaml`) |
| `extensions_config.json` | MCP servers + skills (API-editable at runtime) | No (from `extensions_config.example.json`) |
| `.env` | API keys and deployment secrets | No (from `.env.production.example`) |

Key settings: `sandbox` execution mode (local / Docker / provisioner),
`database.backend` (`sqlite` or `postgres` for shared use), IM channels
(Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz),
tracing (LangSmith / Langfuse / Monocle), and token budgets.

## Service topology

| Service | Port | Role |
| ------- | ---- | ---- |
| Nginx | `2026` | Unified entry point — open this in the browser |
| Gateway API | `8001` | FastAPI + embedded agent runtime (`/health`, `/health/ready`, `/api/ops/*`) |
| Frontend | `3000` | Next.js chat UI |
| Desktop stack | `8201` + `3000` | Electron-spawned Gateway + UI (Windows app) |
| Provisioner | `8002` | Optional, Kubernetes sandbox mode only |

## Repository map

```
agent-workspace-desktop/
├── electron/            # Windows desktop app (Electron shell + installer)
├── backend/             # FastAPI Gateway + agent harness (Python)
├── frontend/            # Next.js chat UI
├── docker/              # Compose files, nginx config, provisioner
├── deploy/helm/         # Kubernetes chart
├── skills/              # Agent skills (public/ committed, custom/ local)
├── scripts/             # setup, doctor, prod_check, deploy, support-bundle
├── docs/                # guides incl. PRODUCTION.md runbook
├── config.example.yaml  # full config template
├── PROJECT_GOAL.md      # product vision + verified capability status
└── Makefile             # make setup | dev | up | prod-check | ...
```

Useful commands: `make help`, `make doctor`, `make prod-check`,
`make support-bundle`, `cd backend && make test`, `cd frontend && pnpm check`.

## Contributing

Issues and pull requests are welcome. Please run `make prod-check`, backend
`make test` + `make lint`, and frontend `pnpm check` before submitting, and
keep `README.md`/`AGENTS.md` in sync with behavior changes.

## References & Credits

This project includes work from other open-source projects.
Trademarks and copyrights belong to their respective owners.

- **Base project — DeerFlow**, originally by ByteDance, licensed under MIT.
  Original copyright and license notices are retained in [LICENSE](LICENSE).
  This independently maintained project is not an official upstream release.
- **[OpenHands](https://github.com/All-Hands-AI/OpenHands)** — autonomous
  software-engineering agents; inspiration for this distribution's engineering
  capabilities, reproduction engine, and experience memory.
- **[OpenClaw](https://github.com/openclaw/openclaw)** — personal AI assistant
  framework; inspiration for the context engine, canvas, and tool-call repair
  capabilities integrated here.
- **Oh My OpenAgent (OmO / Sisyphus)** — multi-agent collaboration paradigm;
  inspiration for the group-chat, kanban, and agent-messaging capabilities in
  this distribution.
- **Hermes Agent** — advanced agent reasoning patterns; inspiration for the
  mission compiler, belief engine, cognitive blackboard, and metacognition
  capabilities integrated here.
- **DeepSeek harness tooling** — preset modes, invariant registry, and pruner
  patterns behind this distribution's planning presets and feedback taxonomy
  (pairs with DeepSeek models as a supported provider).
- **Model providers & platforms** — OpenAI, Anthropic, Google, DeepSeek,
  Moonshot AI (Kimi), MiniMax, StepFun, Xiaomi MiMo, Ollama, vLLM, OpenRouter,
  ByteDance Volcengine, and BytePlus InfoQuest, whose APIs and search/crawl
  services power the configurable model and tool layer.
- **Open-source foundations** — LangChain/LangGraph, FastAPI, Starlette,
  Next.js, React, Electron, Nginx, Redis, PostgreSQL, and the container and
  testing ecosystem (Docker, Helm, pytest, Playwright) that make the stack
  runnable and verifiable.
