# AGENTS.md

Scope: this file covers everything under `backend/`. Deeper, subsystem-specific
rules live in the nearest `AGENTS.md` below it (for example
`app/gateway/AGENTS.md`, `packages/harness/alpha/AGENTS.md`, and the per-subsystem
guides under `packages/harness/alpha/`) and win where they are stricter.

> **Engine inventory:** the authoritative, regenerated list of the engine packages
> under `packages/harness/alpha/` is [`ENGINE_INVENTORY.md`](../ENGINE_INVENTORY.md)
> (run `make engine-inventory`). Point here rather than hand-maintaining a package
> list that drifts; each row also carries a `core` / `preview/partial` tier.
> Before committing inventory-adjacent source changes, run
> `python scripts/generate_engine_inventory.py --check`; the generated-drift CI
> workflow runs the same read-only check.

## Python diagnostics contract

- `scripts/check_python_health.py` separates syntax compilation, Ruff lint and
  formatting, mypy, pytest collection, and executed runtime tests. Keep those
  results as distinct stages: compiling Python never proves imports or runtime
  behavior, and pytest collection never counts as executing tests.
- Each stage's JSON `diagnostics` list should preserve its actionable findings
  in a common shape, with optional line/column and stage-specific error code;
  mypy must retain located and module-level findings. Runtime findings are
  parsed from the full stage log, not the short terminal preview; pytest failure
  summaries and traceback exception locations should both be searchable. The
  stage log preserves full raw output. The generated self-contained HTML report
  must escape diagnostic text and expose every parsed finding to its client-side
  filter. Do not truncate machine findings to the preview.
- Keep compile/type/runtime stages read-only with respect to source files and
  configuration. Update `docs/DEVELOPMENT.md` and the README command guidance
  with any user-visible report changes.
- The full health runner passes only tracked backend Python files to mypy;
  preserve that boundary so untracked local scratch cannot poison the type report.
- CI uploads syntax, mypy, backend pytest, and blocking-I/O reports as separate
  seven-day workflow artifacts, including after failed jobs. Keep those
  artifacts distinct: compilation is not runtime evidence, and mypy remains
  informational until its measured backlog is cleared.

## Integration health + autonomy ownership

- `tests/test_feature_manifest_wiring.py` pins generated feature registry entries
  to real imports, loaders, or documented optional-module references;
  `tests/test_no_orphan_modules.py` catches unreferenced modules. Use
  `alpha.capabilities.catalog` for optional subsystems; do not weaken these
  checks to make an inventory pass.
- The feature-manifest loop inventory comes from
  `AutonomySupervisor.register_default_loops()` and its `loops.py` adapters.
  The supervisor owns background loops; absent `config.yaml -> autonomy.loops`
  ids are disabled. Health reports distinguish configured, master-enabled, and
  running states, with a reason when a loop cannot run. Sync ticks run off the
  event loop; timed-out worker threads remain visibly in flight through shutdown.
  Background APEX SQL event scans pass the session's explicit `user_id`; never
  depend on request contextvars. See `tests/test_autonomy_supervisor.py` and
  `app/gateway/autonomy/supervisor.py`.
- New runtime-aware `@tool`s use a required bare `runtime: Runtime` parameter;
  run `scripts/check_tool_schemas.py` after signature changes.
- `auto_test_and_repair` auto-detection prefers a workspace `.venv`, then
  `uv run` for a locked uv project, then Alpha's active interpreter before a
  global pytest launcher. Caller-supplied commands remain shell-free argv and
  require the same operator opt-in as host bash; do not weaken that gate to
  make an auto-run test pass.
- The Mod Kernel is ordered into agent lifecycle and run admission. Capability
  declarations are not grants; external mods need explicit grants and cannot
  use reserved capabilities or guarded priorities. ESTOP read errors refuse
  work. `RunManager` and `AutonomySupervisor` remain the run and loop owners.
  Journals, capability storage, and timers are process-local; durable holds are
  single-process, never cross-worker exactly-once. An unavailable mod audit
  ledger returns `503`, not an empty success response. See
  [`packages/harness/alpha/mods/AGENTS.md`](packages/harness/alpha/mods/AGENTS.md)
  and the `test_mod_*` suites.

## Runtime, autonomy, and verification ownership

- `RunManager` is the sole Gateway run-lifecycle owner; durable event streams,
  context trust, recovery, and run-acceptance behavior are owned by
  [`packages/harness/alpha/runtime/AGENTS.md`](packages/harness/alpha/runtime/AGENTS.md).
- HTTP run ownership comes from the Gateway-stamped principal (`request.state.user`)
  for sessions, PATs and the explicitly local auth-disabled operator. A trusted
  internal adapter uses only its validated owner header; an internal service
  principal without that header remains unowned. Persist this owner on the run
  record so the background worker can perform the same owner-scoped project
  lookup as the thread. Never derive it from request-body context.
- APEX is a bounded control plane over that run path, not a second executor.
  Its executive contract and UI/API controls are documented in
  [`docs/APEX_AUTOPILOT.md`](../docs/APEX_AUTOPILOT.md) and
  [`packages/harness/alpha/apex/README.md`](packages/harness/alpha/apex/README.md).
- The opt-in Completion Engine owns typed checks, freshness-bound evidence,
  approvals, manual review and the finalization guard:
  [`packages/harness/alpha/completion/AGENTS.md`](packages/harness/alpha/completion/AGENTS.md).
- Mission acceptance evidence and readers live in `alpha.mission.acceptance`;
  collectors live in `alpha.mission.collectors`. Keep absent/ambiguous evidence
  unverified and preserve the existing conflict rules in
  `tests/test_apex_acceptance_evidence.py` and
  `tests/test_mission_evidence_collectors.py`.
- The standing-goal tree (`alpha.mission.goalloop.goal_tree`) is persisted inside
  `GoalState`; `GoalLoopEngine` advances it only when both a decomposer and an
  executor are supplied. Those collaborators must return bounded child
  specifications and `GoalEvidence` measurements respectively. A node can be
  completed only when every declared criterion has passing measured evidence
  and every child is terminal. The default command binding still uses the
  flat, judge-based boundary unless a runtime wires both tree collaborators.
  If a tree is present without both collaborators, the loop blocks instead of
  falling through to the prose judge; do not claim tree execution is enabled
  from serialization or unit tests alone.
  Regressions: `tests/test_goal_tree.py`,
  `tests/test_goal_tree_store_links.py`, and `tests/test_goal_loop.py`.
- Mechanically enforced delegation, fan-out, workflow and group ceilings are
  owned by `alpha.config.limits`; consumers import the shared constants.
  `tests/test_limit_authority_consistency.py` pins that boundary.
- Workflow templates are proposals, not proof: built-in blueprints are seeded
  by `alpha.workflow.builtin_templates`; promotion requires measured evidence.
  See `tests/test_builtin_workflow_templates.py` and
  `tests/test_workflow_templates_and_improvement.py`.
- Release promotion gates are owned by `alpha.benchmarks.release_gate`; use
  real versioned measurements, never defaults, as evidence. Self-repair remains
  refusal-only until an action has an independent post-action verifier.
## Project Overview

Alpha is a LangGraph-based AI super agent system. The backend provides a "super
agent" with sandbox execution, persistent memory, subagent delegation and
extensible tool integration, all in per-thread isolated environments. The
four-service topology (Gateway 8001, Frontend 3000, Nginx 2026, optional
Provisioner 8002) and loopback-by-default publish rule: root guide's
[Service Topology](../AGENTS.md#service-topology).

**Runtime**:
- `make dev`, Docker dev and production all run the agent runtime in Gateway via `RunManager` + `run_agent()` + `StreamBridge` (`packages/harness/alpha/runtime/`); Nginx exposes it at `/api/langgraph/*` and rewrites to Gateway's native `/api/*` routers.
- Gateway SSE stream framing (bounded `messages-tuple` argument batching and subgraph event namespacing, #4399) is owned by [packages/harness/alpha/runtime/AGENTS.md](packages/harness/alpha/runtime/AGENTS.md).
- Background subagent identity (provider `tool_call_id` vs. the executor's `execution_id`, and why provider IDs must never become registry ownership keys) is owned by [packages/harness/alpha/subagents/AGENTS.md](packages/harness/alpha/subagents/AGENTS.md).
- Scheduled-task executions reuse that same Gateway run lifecycle: the scheduler decides *when*, dispatch goes through the existing run path, never a parallel stack. Dispatch, queue, lease, `preview-cron` and multi-instance recovery rules: [docs/WORKFORCE.md](../docs/WORKFORCE.md#scheduled-tasks-and-background-automation-contract).
- Long-running MCP work uses a durable task runtime (`McpTaskService` + `mcp_tasks`, lease-based recovery) rather than remote task IDs or status polling inside the Agent loop; only submit stays Agent-visible, the database is source of truth, `ThreadState` gets a bounded projection. Full contract (leases, cancellation fencing, delivery idempotency, management-tool exposure, notification retries, dead-lettering, cancel endpoint's worker-stopped 503): [packages/harness/alpha/mcp/AGENTS.md](packages/harness/alpha/mcp/AGENTS.md).
- `extensions_config.json` runtime write discipline (compose/Helm mount modes, the dual write locks, and the `EBUSY`-only non-atomic fallback) is owned by [packages/harness/alpha/extensions/AGENTS.md](packages/harness/alpha/extensions/AGENTS.md).

**Project structure**: `packages/harness/alpha/` (import `alpha.*`) is the agent
framework; `app/gateway/` + `app/channels/` (import `app.*`) are the FastAPI
Gateway and IM channels; `packages/extension-api/` (import
`alpha_extension_api.*`) is the public extension contract; `extensions/sources/`
holds installed extension snapshots; `tests/`, `scripts/` (`check_tool_schemas.py`,
`generate_feature_manifest.py`, `benchmark/`), `docs/` hold tests, gates,
benchmarks and reference docs. `langgraph.json` configures LangGraph Studio. The
module guides below own each package's internals.

## Important Development Guidelines

### Documentation Update Policy
**CRITICAL: Always update README.md and AGENTS.md after every code change**

- Update `README.md` for user-facing changes (features, setup, usage instructions)
- Update `AGENTS.md` for development changes (architecture, commands, workflows, internal systems). `CLAUDE.md` imports it via `@AGENTS.md`, so editing `AGENTS.md` updates both.
- Keep documentation synchronized with the codebase at all times

### Backend Benchmarks

`backend/scripts/benchmark/` holds reproducible measurements of production
behavior: pin datasets by immutable revision and SHA-256, keep credentials and
provider payloads out of the repo, version prompts/seeds/clocks, use fixed clocks
for offline selection. Protocols and offline commands for `context_snapshot/`,
`deermem_eviction/` and `concurrency/`:
[docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#agent-guidance-reference-backend-benchmarks).

## Commands

Root-directory targets (`make dev`/`start`/`stop`, `install`, the `extension-*`
family, `detect-thread-boundaries`, …) are listed once in the root guide's
[Commands: Root vs. Module](../AGENTS.md#commands-root-vs-module) and the
[make reference](../docs/DEVELOPMENT.md#agent-guidance-reference-root-make-targets)
— do not duplicate them here.

**Backend directory** (for backend development only):
```bash
make install            # Install backend dependencies
make dev                # Gateway API, reload (port 8001)
make gateway            # Gateway API only (port 8001)
make test               # offline tests (no live/blocking-io)
make test-live          # live tests (real APIs)
make test-blocking-io   # strict Blockbuster gate on tests/blocking_io/
make test-shard SPLITS=4 GROUP=2  # one duration-aware shard
make test-shard-durations  # refresh baseline
make lint               # ruff lint
make format             # ruff format
make migrate-rev MSG="..."  # Autogenerate a new alembic revision (see Schema Migrations section)
```

The backend `make dev` target pre-creates and excludes `ALPHA_HOME`
(default: `backend/.alpha`) and `backend/sandbox` from Uvicorn's reload
watcher. Never replace it with a bare `uvicorn --reload`: agent tasks write
runtime files below `ALPHA_HOME`, which would restart the Gateway mid-run.

More specific `AGENTS.md` files in backend code directories contain the subsystem sections split from this file. Follow the nearest file in the directory tree.

## Architecture

### Display Identity

`packages/harness/alpha/branding.py` owns the static `DISPLAY_NAME` (`Alpha`) for default prompt identity, channel display messages, Gateway documentation and selected actionable errors; it has no configuration or environment dependencies. Never use it for package/class names, runtime folders, API keys/routes, headers or other protocol identifiers. Custom agent names and user-managed content remain untouched. Regression coverage: `tests/test_display_branding.py` and the channel connection suites.

### Harness / App Split

The backend is split into two layers with a strict dependency direction:

- **Harness** (`packages/harness/alpha/`): publishable agent framework package (`alpha-harness`), import prefix `alpha.*` — agent orchestration, tools, sandbox, models, MCP, skills, config: everything needed to build and run agents.
- **App** (`app/`): unpublished application code, import prefix `app.*` — FastAPI Gateway API and IM channel integrations (Feishu, Slack, Telegram, DingTalk).

**Dependency rule**: App imports alpha, alpha never imports app, enforced by `tests/test_harness_boundary.py` in CI. Import conventions: `from alpha.agents import make_lead_agent` / `from alpha.models import create_chat_model` inside the harness; `from app.gateway.app import app` / `from app.channels.service import start_channel_service` inside the app; `from alpha.config import get_app_config` for the allowed App → Harness direction. A `from app.gateway.routers...` import inside the harness fails CI.

Package import hygiene: `alpha.agents` and `alpha.subagents` package roots expose
heavyweight graph/executor entrypoints lazily. `alpha.agents:make_lead_agent` stays
a concrete module-level function because LangGraph Server resolves graph factories
from the module dictionary; the wrapper keeps lead-agent and skill-cache imports
inside it. Internal modules needing only lightweight types, config or registries
import the concrete submodule instead of eager package-root imports that pull in
the tool graph or subagent executor during state/schema imports.

The `alpha.authz` facade keeps `authorize_sandbox_execution` lazy: importing
principal, provider, or tool-filter helpers must not import the sandbox runtime.
Sandbox authorization remains available from the package facade on demand.

The Gateway checkpoint router and lead-agent prompt module also keep heavyweight
builtin implementations out of module import: `code_agentic_core` is imported by
the checkpoint handler that uses it, and `tool_search` when the system prompt is
built. `tests/test_builtin_lazy_imports.py` pins both edges so an API router or
prompt-module import cannot silently restore the expensive import path.

`ThreadMetaStore.search()` keeps JSON filter semantics identical across memory, SQLite,
and PostgreSQL: missing differs from null, bool differs from int, and float filters
accept integer or real JSON numbers through `json_value_matches`.

### Gateway Run-Context Trust Boundary

A server-produced run-context key must be gated on both client-writable feeds:
`body.context` (whitelist-merged) and free-form `body.config` (copied verbatim).
`merge_run_context_overrides` forwards it only when `internal=True`;
`strip_internal_context_keys` scrubs it from the assembled `context` *and*
`configurable`. Trust and destination are separate axes, so a new key needs both
decisions — and `disable_clarification` is no milder than `non_interactive`.

### Event-loop discipline

Anything reachable from an `async` Gateway entry point runs on the event loop, so
a blocking call there stalls every other run in the process — and the strict
Blockbuster gate (`make test-blocking-io`) fails the build for it. Two shapes
have bitten us, and both are now pinned by tests:

- **Fleet admission** (`runtime/runs/worker.py::run_agent`) resolves the project
  root through `os.getcwd()` and reads `.alpha` control state from disk; it runs
  in a worker thread via `asyncio.to_thread`.
- **Config reads** (`tools/builtins/python_repl_tool.py`) go through
  `get_app_config()`, which stats the config file on *every* call to detect
  edits. Resolve it off-loop the same way.

Put synchronous filesystem/path/config helpers behind `async` entry points with
`asyncio.to_thread` (see `alpha/runtime/events/store/jsonl.py`,
`alpha/utils/assembly_io.py`), never inline. These tests need generous
synchronization bounds: under the detector on a loaded host
`threading.Thread.start()` alone can block >30s, so a sub-second gate expires
before the run it waits on starts, and the failure looks like the bug it is
meant to catch.

## Development Workflow

### Test-Driven Development (TDD) — MANDATORY

**Every new feature or bug fix MUST be accompanied by unit tests. No exceptions.**

- Write tests in `backend/tests/` following the existing naming convention `test_<feature>.py`
- Run both offline targets before and after your change: `make test` and `make test-blocking-io`
- Tests must pass before a feature is considered complete
- For lightweight config/utility modules, prefer pure unit tests with no external dependencies
- For circular import issues in tests, add a `sys.modules` mock in `tests/conftest.py` (see `alpha.subagents.executor`)
- Run a specific file with `PYTHONPATH=. uv run pytest tests/test_<feature>.py -v`

Keep live tests opt-in (`ALPHA_RUN_LIVE_TESTS=3`); guard POSIX-only markers with
`os.name` for Windows collection. Jina logging tests use dummy keys; Jina/
Browserless/InfoQuest resolve URLs without rebuilding HTML; InfoQuest connect/read
timeout is 30s, separate from crawl timeouts (`tests/test_infoquest_http_timeout.py`).

### Running the Application

Run `make dev` from the **project root**; it starts every service and makes the
application available at `http://localhost:2026`. The full startup-mode matrix
(local foreground/daemon, Docker dev/prod) and the nginx routing table are in
[docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#agent-guidance-reference-all-startup-modes).

**Nginx routing**: `/api/langgraph/*` → Gateway embedded runtime (8001), rewritten
to `/api/*`; all other `/api/*` → Gateway API (8001); `/` (non-API) → Frontend
(3000). Direct Gateway access without nginx: `http://localhost:8001`. The
frontend reaches the backend through a single variable,
`NEXT_PUBLIC_GATEWAY_URL`, which `src/lib/api-client.ts` normalizes into
`GATEWAY_BASE`: unset (the default) or a bare path means the relative `/api`,
and a full origin has `/api` appended to it. Left unset, `make dev` from the
repo root talks to nginx on 2026 with no extra configuration. (Older guides named
`NEXT_PUBLIC_LANGGRAPH_BASE_URL`/`NEXT_PUBLIC_BACKEND_BASE_URL`; nothing reads
either since `GATEWAY_BASE`.)

## Key Features

### Host System Monitor

`app/gateway/system_monitor_service.py` (+ `system_monitor_extras.py`,
`routers/system_monitor.py`) reports host CPU/RAM/disk/network/process state with
best-effort, timeout-bounded, cached samplers. The four binding rules (never raise
into the sampling tick, no unconsented bandwidth, honest GPU attribution, the
`ALPHA_ADVANCED_MONITOR=0` kill switch) are stated in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#gateway-host-system-monitor).

### Web Search Recency

DDG, Brave, Tavily, SearXNG and Sofya `web_search` share optional
`time_range=day|week|month|year` (omission preserves request shape): DDG maps to
`d|w|m|y`, Brave to `pd|pw|pm|py`, Tavily/SearXNG pass it unchanged, Sofya as
`freshness`. DDGS 9.34.3 uses only enabled Brave/DuckDuckGo/Yahoo engines that
honour `timelimit`: `auto`/`all` resolves to that set, incompatible engines are
dropped, an empty set falls back — re-check on upgrades.

The pinned AgentEye deep-research adapter and its
`use_for_deep_research` fallback are owned by
[docs/AGENT_EYE_RESEARCH.md](docs/AGENT_EYE_RESEARCH.md) and
[docs/DEEP_RESEARCH.md](../docs/DEEP_RESEARCH.md).

### Tavily Fetch

Title fallback: result URL, then request URL.

### File Upload

Outlines use ATX syntax (3–6 hashes, space/tab separator, ≤3 leading spaces), strip closing hashes and skip fenced code; closing markers use a linear suffix scan, never an unanchored whitespace regex on unbounded headings. The long-heading regression exercises the production extractor under a generous deadline.

Uploads are all-or-nothing, land in thread-isolated user buckets, stage through
hidden `.upload-*.part` files before an atomic replace, offload filesystem work
through `alpha.utils.file_io.run_file_io`, acquire `SandboxProvider.acquire_async()`
for non-mounted sandbox uploads, and are titled by `UploadsMiddleware` from
`original_user_content` with 200/2000 caps. Endpoint, storage, sandbox-sync and
title rules: [docs/FILE_UPLOAD.md](docs/FILE_UPLOAD.md#agent-facing-upload-contract).

### Plan Mode

TodoList middleware for complex multi-step tasks, controlled via runtime config
`config.configurable.is_plan_mode = True`; it provides the `write_todos` tool for
task tracking and allows one `in_progress` task at a time with real-time updates.
See [docs/plan_mode_usage.md](docs/plan_mode_usage.md) for details.

### Context Summarization

Automatic conversation summarization near token limits, configured in
`config.yaml` under `summarization`; triggers are tokens, messages or a fraction
of max input, older messages summarized while recent ones are kept. Manual
compaction (`POST /api/threads/{id}/compact`) reuses the same
`AlphaSummarizationMiddleware`: new checkpoint with updated `messages` and
`summary_text`, only those channel versions bumped, through the shared
`reserve_checkpoint_write()` boundary whose short-lived `checkpoint_write`
operation shares the durable active-thread uniqueness constraint with run
admission, preventing worker-local and cross-worker checkpoint-write races. See
[docs/summarization.md](docs/summarization.md).

### Vision Support

For models with `supports_vision: true`, `ViewImageMiddleware` processes images in
conversation and `view_image_tool` joins the toolset. Images are base64-encoded
into a hidden message with a reserved ID prefix and a server-owned metadata
marker; Gateway strips that marker from untrusted input and the middleware
requires both identifiers to recognize its own message. Injection happens inside
`wrap_model_call`, so the payload never enters graph state: checkpoints keep only
lightweight `viewed_images` metadata, client-chosen IDs survive, and the
middleware sweeps its message out of every request before rebuilding it — a
payload stranded in an older checkpoint by an interrupted run stops being resent.

### Per-bot model configuration

`config.yaml` `models[]` stays the only place a model is *declared*; a bot
profile only ever **names** them. `packages/harness/alpha/bots/model_config.py`
owns the whole seam:

- **Fail-closed validation collects every error.** `validate_bot_model_config`
  takes the known-name set explicitly and reports all issues at once with
  `{code, field, message, severity}` — one save, one full list.
- **Resolution decides precedence exactly once.**
  `resolve_model_plan()` is pure: `request > bot.model_config > bot.model >
  custom_agent > default`, and every value travels with its `*_source`.
  A bot-declared chain *replaces* the primary's own chain (appending would make
  the effective order depend on a declaration nobody can see), and a chain that
  loops back to the primary raises a cycle instead of silently deduping — the
  override walk runs inside the primary's own frame.
- **An invalid block degrades loudly, never half-applies.** The lead agent sets
  `bot_model_config = BotModelConfig()` — an *empty* config, deliberately not
  `None`, which would re-read the raw dict tolerantly — and logs every issue at
  ERROR, so plain `bot.model` applies.
- **The Gateway route is the only write path.** `PUT /{name}/model-config`
  stores the canonical `cfg.to_dict()`; `model_config` is not writable through
  the generic `PATCH /{name}` (Pydantic reserves `model_config` as a class
  attribute, and a generic write would bypass the validation).
- Bounds are refused with the bound named, never clamped: fallbacks 5,
  mixture references 8, counsel members 5, counsel rounds 5, mixture workers 8,
  strategies `parallel|sequential`; `sampling` is an allowlist that rejects
  credential-shaped and metadata keys.

Contract: [docs/BOT_MODEL_CONFIG.md](../docs/BOT_MODEL_CONFIG.md). Tests:
`tests/test_bot_model_config.py`, `tests/test_bot_model_config_wiring.py`.

### Arena tournaments

`packages/harness/alpha/arena/` runs one task through N strategy-carded
sub-agents and cuts the field with a single-elimination bracket — attack,
defend, revise, judge — until one solution survives. It is ordinary Alpha
delegation (same `SubagentExecutor`, same capacity caps) behind a
**durable, budget-gated** orchestration: the call count is projected before
anything runs, a run above `require_confirmation_over_calls` is refused until
the caller confirms, and the whole bracket lives in one atomically written,
`FileLock`-guarded JSON document that `resume` can pick up after a restart.
Judge quality is engineered rather than assumed — blinded neutral copies,
deterministic position swapping, per-task hard gates that force
`correctness=0` when tripped, a bounded repair loop with explicit
concede/rebut dispositions — and `decide()` still recomputes the winner from
the rubric, overriding a judge whose stated pick contradicts its own scores.
Two honesty notes worth knowing before you use it: `compare`/`synthesize` are
declared modes that are **refused by name** (not implemented), and the mode
vocabulary the `profiles` action renders carries an `implemented` flag and a
`refusal_reason` per row so it cannot present a mode that raises as available.
The task-contract / candidate-coverage / evidence-ledger / synthesis modules
**are wired into the executor**: once the bracket is terminal,
`ArenaExecutor._finish` builds one `tournament` block off the event loop, and
`status()`, `winner()`, `POST /runs/{id}/start|resume` and the `arena` tool all
carry it. Coverage is measured from each competitor's own solution file (never
declared by the judge), every claim is `UNVERIFIED` because the bracket runs no
retrieval, and the merge is emitted as a plan (`synthesis.executed: false`). A
bracket that did not finish, or a report that could not be assembled, says so
rather than reporting nothing. Operating guide:
[docs/ARENA.md](../docs/ARENA.md); engine
contract: [packages/harness/alpha/arena/AGENTS.md](packages/harness/alpha/arena/AGENTS.md).
Tests: `tests/test_arena_*.py` (160 tests, zero model calls — they run on the
scripted runner seam).

### Alpha-to-Alpha peer network

### Capability mesh

`packages/harness/alpha/capability_mesh/` keeps evidence-backed capability
records for every dispatchable configuration Alpha can route to, and its
learned instruments (cross-run memory, calibration, drift, the decision-policy
table, routing, team selection, composition, independence, shadow). The
`capability_mesh` tool (`tools/builtins/capability_mesh_tool.py`) and the
`/api/capability-mesh` router (`app/gateway/routers/capability_mesh.py`) both
expose the package's **pure library functions and nothing more**: every
planning action is a locally computed preview that says so, and **no executor
wiring exists, so nothing dispatches a real agent run through the mesh**. It is
`enabled: false` by default, and even enabled it is read/preview-only. Every
count travels with its evidence level and an unreadable store reports the
store's own reason rather than an empty answer. **The absence is machine-readable,
not only prose**: every payload carries a `mesh_capability` row
(`capability_mesh_executor` / `state: absent` / `dispatches: false`) naming the
modules a dispatch would have to live in, and
`GET /api/capability-mesh/capability` serves that row on its own — reachable
even while the mesh is **disabled**, because a client checking whether it could
route is exactly the client that should be told first.
`tests/test_capability_mesh_executor_wiring.py` pins it, including the two
checks that are not about wording: no mesh module imports `SubagentExecutor` or
`RunManager`, and neither of those reads the mesh. Contract and the honesty
invariants at the boundary:
[packages/harness/alpha/capability_mesh/AGENTS.md](packages/harness/alpha/capability_mesh/AGENTS.md).
Tests: `tests/test_capability_mesh_tool.py`, `tests/test_capability_mesh_router.py`,
`tests/test_capability_mesh_executor_wiring.py` (offline, no model calls).

### Alpha-to-Alpha peer network

`packages/harness/alpha/peer_network/` is the harness-owned, installation-scoped
peer plane: `service.py` owns lifecycle, pairing, topology, delivery, retry and SSE
events; `storage.py` is synchronous SQLite via `asyncio.to_thread`; `transport.py`
is HTTP-first with WebSocket fallback; `discovery.py` owns UDP and optional mDNS;
`github.py` is Agent Card rendezvous only. Bot `peer/agent` DMs bridge through the
service rather than duplicating transport. The harness root guide owns the
cross-installation identity, discovery-untrusted, exact-public-path,
sender-assertion, credential-stripping and SQLite-not-cross-process-exactly-once
rules; Gateway router rules: [app/gateway/AGENTS.md](app/gateway/AGENTS.md).
Operations: [docs/ALPHA_PEER_NETWORK.md](../docs/ALPHA_PEER_NETWORK.md); tests:
`tests/test_peer_network.py`, `tests/test_bots_dm.py`.

## Code Style

- Uses `ruff` for linting and formatting
- Line length: 240 characters
- Python 3.32+ with type hints
- Double quotes, space indentation

Backend reference docs (`CONFIGURATION`, `ARCHITECTURE`, `API`, `API_REFERENCE`,
`SETUP`, `STREAMING`, `FILE_UPLOAD`, `PATH_EXAMPLES`, `MCP_SERVER`, `AUTH_DESIGN`,
`GUARDRAILS`, `summarization`, `plan_mode_usage`, `RUN_EVENT_STREAM`,
`THREAD_LIFECYCLE`, `AGENT_EYE_RESEARCH`) live in `docs/`.
