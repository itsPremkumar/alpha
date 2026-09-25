# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is Alpha

Alpha is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Windows launcher validation

`start.ps1` calls the installed Next.js CLI directly and uses `uv run --no-sync`
for the Gateway. Startup requires `/health/ready` and HTTP 200 from the frontend;
HTTP errors must not count as readiness. A failed frontend build aborts startup
and stops the Gateway started by this invocation. Regression tests live in
`backend/tests/test_serve_frontend_skip_build.py`. Deployment fixture tests in
`backend/tests/test_gateway_startup.py` resolve Git Bash explicitly on Windows
instead of invoking the WSL shim through bare `bash`.

`start.ps1` checks listening ports through both `Get-NetTCPConnection` and a
`netstat.exe` fallback. Some restricted Windows hosts return a false negative
from the PowerShell cmdlet; without the fallback the launcher starts a second
Gateway which later fails with `WinError 10048` and leaves the frontend unable
to proxy API requests.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it proxies `/api/*` to the Gateway, rewriting
`/api/langgraph/*` onto the Gateway's native routes, and serves the frontend — see
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail. It compresses
HTML and configured textual assets, deliberately leaving SSE, fonts, images, audio, and
video uncompressed at the proxy layer.

Both compose files publish that entry as `"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"`
— **loopback by default**; a bare `"${PORT}:2026"` binds `0.0.0.0`. The root `PORT` is
Docker ingress config only; local orchestration pins Next.js to `3000` so loading `.env`
cannot make `make dev` wait on the wrong port. Nginx (`default_server`, IPv4+IPv6) and
the Gateway (`0.0.0.0:8001`) bind inside the container on purpose: the published nginx
port is the whole external surface, so any new published port needs an explicit bind
address. `backend/tests/test_compose_default_bind_host.py` pins this for every service in
both compose files.

## Repository Map

```
agent-workspace/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── extensions/sources/         # Deployable snapshots of locally installed Python extensions
│   ├── packages/extension-api/     # agent-workspace-extension-api package (import: agent_workspace_extension_api.*) — public extension contract
│   ├── packages/harness/           # agent-workspace-harness package (import: alpha.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
│                                    # Managed integration skill packs are global at .agent-workspace/integrations/skills/{provider}/
│                                    # Integration credentials and enabled state remain per-user
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status, skill review)
├── examples/agent-workspace-extension-example/ # Standalone package demonstrating all extension contribution kinds
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Third-party extensions are loaded from a top-level `plugins:` list in `config.yaml`
(operator-controlled on purpose — that list causes code to be imported, so it is deliberately
kept out of the API-writable `extensions_config.json`). Packaged extensions can contribute
middleware, task lifecycle, system-model observers, Gateway services, and FastAPI HTTP
routers; the [reference extension](examples/agent-workspace-extension-example/) demonstrates all
five. Manage them with `alpha extensions install/upgrade/list/enable/disable/remove` or the root
`make extension-*` wrappers. Every mutation requires a Gateway restart, and both build
hooks and extension code execute with Gateway privileges, so only trusted operator sources
belong in this path. The manager transaction, accepted source forms, lock discipline, and
contribution contract live in
[the extensions guide](backend/packages/harness/alpha/extensions/AGENTS.md).

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:
- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.
- CI waivers live in `.github/skill-review-waivers.v1.json` and are enforced by
  `scripts/review_changed_public_skills.py`. Pull requests may validate waiver
  edits from their head revision, but only the manifest from the trusted base
  revision can suppress that run. Entries match one error finding exactly,
  include the reviewed file's SHA-256 and an expiry date, remain visible in CI
  output, and can never waive blocker findings. An entry may also preapprove
  future full-file SHA-256 values, effective only once the manifest change lands
  in the trusted base — so relying on a waiver takes two merges: the manifest
  first, the skill change after, then promote the consumed hash to `file_sha256`
  in a follow-up cleanup.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. That key, `disable_clarification`, and `github_token` are honored only for internally-authenticated callers; client-supplied copies are dropped from both `body.context` and `body.config`.
- Busy scheduled occurrences are persisted as `queued`; `launching` is a short lease-fenced claim, `running` remains the normal Gateway run lifecycle, and `scheduler.queue_timeout_seconds` bounds the durable wait. Do not reintroduce skip-on-overlap or count waiting rows against `max_concurrent_runs`.

Workforce layer (Bot Mode + self-improvement + projects):
- Harness: `projects/` (membership, locks, constitution, decisions, handoffs, goals,
  conflicts — file-backed under `runtime_home()/projects/`, see its `AGENTS.md`),
  `bots/dm.py` + `bots/inbox.py`, `skills/{usage,curator,authoring}.py`,
  `learning/review_queue.py`, `deliberation/moa.py`,
  `scheduler/{wake_gate,blueprints,incidents,guards}.py`.
- Per-turn injections (bot roster, repo context) ride `DynamicContextMiddleware`
  reminders keyed off runtime context (`bot_name`, `repo_root`) — never the static
  system prompt (prefix-cache rule).
- Gateway: `/api/projects/{id}/*`, `/api/bots/{name}/dm|inbox|chat`,
  `/api/skills/curator|usage|tiers`, `/api/{council,policy,missions,benchmarks,
  evolution}/*`, `/api/threads/{id}/undo`, `/api/console/insights`,
  `/api/ops/advice`, Signal channel (`app/channels/signal.py`).
- Frontend: `src/lib/workforce.ts` + `WorkforceSection` behind the `workforce` NavTab.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users); unattended: make setup SETUP_ARGS=--non-interactive (AGENT_WORKSPACE_SETUP_* env)
make doctor      # Check configuration and system requirements
make prod-check  # Production readiness pre-flight (versions, config files, secrets)
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make extension-install SOURCE=...  # Install and enable a trusted Python extension
make extension-upgrade SOURCE=...  # Replace an installed extension and keep its config
make extension-list                # List configured Python extensions
make extension-enable NAME=...     # Enable an installed extension (restart required)
make extension-disable NAME=...    # Disable without uninstalling (restart required)
make extension-remove NAME=...     # Remove package and config entry (restart required)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized); SKIP_FRONTEND_BUILD=1 reuses the last frontend build
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Production startup runs the image's pre-built environment (`uv run --no-sync`)
and makes `make up` wait for the Gateway `/health` probe before printing its
banner; a readiness failure must surface Compose status and recent Gateway logs
rather than claim the stack is running (see Service Topology).

Docker log and restart commands resolve `AGENT_WORKSPACE_ROOT` from the current
checkout before invoking Compose, matching the start and stop commands.

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Default backend suite; excludes live and blocking-I/O tests
cd backend && make test-blocking-io  # Strict blocking-I/O suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server: Webpack by default (pnpm dev --turbopack for Turbopack)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

Host pnpm calls use `scripts/pnpm.py`: native Windows tries `pnpm.cmd` before
`pnpm`; POSIX reverses the order. Its Corepack fallback applies the same ordering
to `corepack.cmd` and `corepack`. The runner operates from `frontend/` so
Corepack honors its pinned package-manager version.

### Prerequisites before `make dev`

`make dev` does **not** generate config files. First-time setup order:

```bash
make config      # copy config.example.yaml -> config.yaml and extensions_config.example.json -> extensions_config.json (both gitignored)
make install     # install frontend + backend deps and pre-commit hooks
make dev         # then start everything
```

Without `config.yaml` present, services fail to boot. `config.yaml` / `extensions_config.json`
may be edited at runtime via the Gateway API but are gitignored, so never commit them.

### Run a single test

```bash
# Backend (pytest); run one file or one test function
cd backend && python -m pytest tests/test_compose_default_bind_host.py -q
cd backend && python -m pytest tests/path/to/test.py::test_func -q

# Frontend (rstest)
cd frontend && pnpm rstest run <pattern>     # e.g. pnpm rstest run my-component
```

### Logs

- Docker stack: `make docker-logs` (or `docker compose -f docker/... logs -f <svc>`).
- Local `make dev`: each service logs to its own terminal pane. Frontend dev-server
  errors surface in the browser console at `localhost:3000`; backend tracebacks appear
  in the Gateway terminal.

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)**
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Union Alpha configuration baseline

The first model in `config.example.yaml` is `union-alpha`, using
`langchain_openai:ChatOpenAI`, OpenRouter's `stealth/union-alpha` API slug, and
`$OPENROUTER_API_KEY`. Do not use the CLI-qualified
`openrouter/stealth/union-alpha` as the OpenRouter API slug. Catalog metadata
was checked on 2026-09-17; live account access is not established. The offline
contract is pinned by `backend/tests/test_model_config.py`.

The current frontend package declares `pnpm typecheck`, but does not declare
`pnpm check`, lint, or test scripts. Older command examples above describe the
previous frontend and are not verified gates for this checkout. Restore those
scripts and their tests before treating inherited CI workflows as working.

## Cognitive-memory owner contract

Production cognitive memory requires a server-resolved owner and stores state
under `Paths.user_dir(owner) / "cognitive_memory"` (`users/{owner}/cognitive_memory`).
`backend/packages/harness/alpha/memory/cognitive/engine.py:41` owns the per-owner,
per-directory process cache, locks and atomic fsync-backed snapshots; missing
owners and corrupt-state load failures fail closed. Explicit `storage_dir`
instances stay independent of the cache. Retained persistent records, not display
pages, belong in snapshots; working memory stays ephemeral. These locks do not
provide multi-process coherence. Keep HTTP/model-supplied owners and paths out of
this boundary; see `backend/packages/harness/alpha/memory/cognitive/AGENTS.md`
for the detailed contract and current semantic-capacity caveat.

## Integration health contract

Every capability in this repo is discoverable and continuously verified:

- `contracts/feature_manifest.json` — generated by
  `backend/scripts/generate_feature_manifest.py`; proves all 116 tools, 55
  routers, 40 middlewares and 5 supervisor loops are wired. Regenerate after any
  registry change; `tests/test_feature_manifest_wiring.py` pins every entry.
- `tests/test_no_orphan_modules.py` — AST reference scan that fails the build if
  any module exists with no import, no dotted-string loader path, no config
  `use:` entry, and no allowlist reason. See `alpha.capabilities.catalog` for how
  optional subsystems earn a production reference without being on by default.
- `GET /api/ops/integration-health` — live coverage + supervisor status; the
  frontend Integration tab renders it.
- `backend/scripts/check_tool_schemas.py` — run after any tool signature change;
  every `@tool` that needs runtime access must use `runtime: Runtime` as a bare
  required first parameter (never `Runtime | None = None`; the union forces
  pydantic to schema-generate `ToolRuntime`'s `Callable` fields and breaks the
  entire tool list for the LLM).

## Autonomy supervisor contract

`backend/app/gateway/autonomy/supervisor.py` is the single lifecycle owner for
background loops (sentinel, perpetual, review_queue, skill_curator,
enterprise_heartbeat; swarm is telemetry-only/on-demand). Loops are declared in
`register_default_loops()`, gated by `config.yaml -> autonomy.loops` (absent id
= disabled), run sync ticks on threads, restart inside a budget then park, and
publish `autonomy.loop.completed` on the in-process bus (`alpha/events/bus.py`).
Flags off = zero tasks. Started from the gateway lifespan after scheduler/channel
services; stopped first on shutdown. `GET /api/ops/integration-health` exposes
`status()` live; `tests/test_autonomy_supervisor.py` pins the invariants.

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
- **Skill text encoding** — treat `SKILL.md` and other textual skill resources as UTF-8;
  Python utilities that read or write them must pass `encoding="utf-8"` rather than
  relying on the platform locale.
- **Version sources must stay in lockstep** — a release version must match identically in
  `backend/pyproject.toml`, `frontend/package.json`, and `deploy/helm/agent-workspace/Chart.yaml`
  (`version` + `appVersion`). Pushing a `v*` git tag triggers CI that runs
  `scripts/verify_versions.sh` and **blocks all publishing** if any source drifts. Before
  bumping a version, run `scripts/bump_version.sh <ver>` (aligns all four at once) and
  `scripts/verify_versions.sh <ver>` to catch drift early. See [RELEASING.md](RELEASING.md).
- **Don't edit `CLAUDE.md`** — it only contains `@AGENTS.md`. All agent guidance changes
  belong here in `AGENTS.md`; `CLAUDE.md` is a thin import shim.
