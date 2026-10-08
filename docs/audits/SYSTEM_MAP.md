<<<<<<< HEAD
# Alpha — System Map

**Author:** production-reliability audit, cycle 5
**Date:** 2026-10-05
**Base commit:** `2dbfe90`
**Scope:** the map Phase A requires before any change. Every claim below is
sourced from the owning `AGENTS.md`, a named source file, or a live probe of the
running system. Anything I could not verify is marked **NOT VERIFIED** rather
than inferred.

---

## 1. Service topology

| Service | Port | Role | Bind | Verified this cycle |
|---|---|---|---|---|
| **Nginx** | 2026 | Sole public entry; proxies `/api/langgraph/*` → Gateway `/api/*`, serves the frontend | loopback by default | **NOT VERIFIED** — no Docker daemon on this host (`npipe:////./pipe/docker_engine` refuses) |
| **Gateway** | 8001 | FastAPI REST + embedded LangGraph-compatible runtime | `127.0.0.1` | **VERIFIED LIVE** — `GET /health` 200, `GET /health/ready` 200 `{"database":"ok","checkpointer":"ok"}` |
| **Frontend** | 3000 | Next.js 15.5.25 App Router, production build | loopback | **VERIFIED LIVE** — `GET /` 200 |
| **Provisioner** | 8002 | Optional; sandbox K8s mode only | — | **NOT VERIFIED** — sandbox not configured on this deployment |

`docker version` fails at the named pipe, so the nginx and Provisioner rows
cannot be exercised here. That is an environment limitation, recorded as such —
not a pass.

Both compose files publish nginx as `"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"`,
and `backend/tests/test_compose_default_bind_host.py` pins that for every service
in both files.

---

## 2. The agent chain

```
browser
  → Next.js app router (frontend/app/)
  → ChatView.tsx resolves the workspace view id from ?view=
  → components/sections/<Surface>Section.tsx
  → lib/<surface>.ts  (typed client)
  → lib/http.ts (get/send)  →  lib/api-client.ts (GATEWAY_BASE + CSRF)
  → /api/*  on the Gateway
  → middleware chain
  → LangGraph graph nodes
  → tools → sandbox → checkpointer/store
  → RunJournal → StreamBridge → SSE frames → frontend reducers
```

Named, with file:line:

| Stage | Owner | Location |
|---|---|---|
| Admission | `start_run()` | `app/gateway/services.py:1584` |
| Durable admission | `RunManager.create_or_reject` | `services.py:1853` |
| Task attachment | `record.task = asyncio.create_task(worker)` | `services.py:1906` |
| Worker | `run_agent()` — the single entry every lead run passes through | `alpha/runtime/runs/worker.py:898` |
| Fleet admission (off-loop) | `_admit_run_to_fleet` via `asyncio.to_thread` | `worker.py:926` |
| Journal | `RunJournal(...)` | `worker.py:1089`; class at `alpha/runtime/journal.py:315` |
| Graph assembly | `run_assembly(...)` / `_agent_graph` | `worker.py:1298-1299` |
| Lead agent | `assemble_lead_agent()` | `alpha/agents/lead_agent/agent.py:891` |
| Middleware composition | `build_middlewares(...)` | `agent.py:494` |
| Toolset | `get_available_tools(groups, include_mcp, …)` | `alpha/tools/tools.py:429` |
| Graph execution | `agent.astream` | `worker.py:1415` (single mode) / `:1449` (multi-mode, subgraphs) |
| Checkpointer | `make_checkpointer` | `alpha/runtime/checkpointer/async_provider.py:243` |
| Sandbox | `SandboxProvider.acquire` | `alpha/sandbox/sandbox_provider.py:26` |
| SSE frame build | `format_sse()` | `app/gateway/services.py:152` |
| SSE consumer | `sse_consumer()` | `services.py:2193` |
| Terminal frames | `_terminal_end_frames()` | `services.py:414` |

**SSE frame types** (`alpha/runtime/stream_modes.py:7-15`, the closed public set):
`values`, `messages-tuple`, `updates`, `debug`, `tasks`, `checkpoints`, `custom`,
plus subgraph namespaced as `values|<ns>` (`worker.py:3228`), and the Gateway's
own `heartbeat` (`:178`, 15 s, carries **no** `id:` so `Last-Event-ID` never
moves), `gap` (`stream_replay_gap`), `error`, `end`.

`normalize_stream_modes` raises `UnsupportedStreamModeError` rather than
silently substituting (`stream_modes.py:37-39`) — an honest refusal, not a
downgrade.

---

## 3. Where state lives, and what is NOT cross-process

| State | Store | Cross-process safe? |
|---|---|---|
| Conversation checkpoints | LangGraph saver → `{sqlite_dir}/alpha.db` | **yes** on SQL backends; `memory` is process-local |
| Thread metadata | `threads_meta` (SQL) | yes |
| Runs | `runs` (SQL, `RunManager`) | yes — partial unique index on `thread_id` where status is active |
| Run events | `run_events` / JSONL / memory | **conditional** — memory and JSONL are process-local; `GATEWAY_WORKERS > 1` is rejected at startup unless `run_events.backend: db` |
| Swarms | `ALPHA_HOME/swarms/{id}.json` + events JSONL | **no** |
| Dynamic workflows | `runtime_home()/workflow_store` (JSONL plans, JSON leases) | **no** — `PRODUCTION_READINESS_INVENTORY.md:31` states this plainly |
| Peer network | installation-scoped SQLite | **no** — explicitly "restart-recoverable for one installation, not cross-process exactly-once" |
| Files | thread-scoped `user-data/` | filesystem only |
| Cognitive memory | `Paths.user_dir(owner)/cognitive_memory` snapshots | **no** — no cross-worker cache coherence or file locking |
| Goals | three separate JSON stores | **no** |
| Scheduler | `scheduled_tasks` + `scheduled_task_runs` (SQL, leased) | **yes** for the queue; per-job JSONL journals are not |
| Kanban | `.alpha/kanban/boards.json` | **no** — and `_load`/`_save` both use bare `except Exception: pass` |
| Parked sessions | `network_waits` (SQL) | yes |
| Side effects | `tool_side_effects` (SQL schema exists) | schema yes; **the docs disagree on whether a production writer exists** — see §7 |

---

## 4. The self-inventory plane

`alpha.workflow.registry` is the single source per fact. `engines` and `wiring`
re-derive nothing: they read the generated `contracts/feature_manifest.json`
through the one `manifest_source` loader. `models` reads the live `AppConfig`.
`alpha_capability` (one tool) is the model-facing surface; `GET
/api/intelligence/inventory` is the HTTP one.

Three load-bearing invariants:
- an unreadable source reports `count: null`, never `0`;
- `health` is `unverified` on every descriptor, because nothing here executes
  what it lists;
- `alpha.knowledge.code_index` returns names/signatures/`path:line` and **never a
  function body** (the 67.8 %-vs-29.2 % ablation), with TypeScript rows labelled
  `extraction="regex"`.

---

## 5. The workspace view registry

Rule: a view id missing from any of the three places is dead code.

1. **Type union** — `WorkspaceView`, `NavTabs.tsx:38-69`
2. **Tab array** — `WORKSPACE_TABS`, `NavTabs.tsx:82-121`
3. **URL routable list** — `WORKSPACE_VIEW_IDS`, `lib/workspace-view.ts:1-41`

Lazy import (`next/dynamic`, not `React.lazy`, so sections can server-render):
`ChatView.tsx:98-127`, 30 declarations.
Render chain: a **ternary chain**, not a switch, split across two JSX blocks
inside one `ErrorBoundary` — `ChatView.tsx:2269-2296` (sub-tab strip) and
`:2298-2453` (main body).

Cross-check: 31 union members, 31 tab entries, 31 routable ids, 31 render
branches, **0 dead ids**. `frontend/src/lib/workspace-nav.test.mjs` pins the
union↔routable and tabs↔union agreement in both directions, and it passes.

Two shapes that look like mismatches on a naive diff, and are not:
`deliberation` renders `WarRoomRunsSection` (no `DeliberationSection` exists),
and `BotOpsSection` is a lazy import with no top-level view id — it is the
`bots` → `ops` sub-tab.

---

## 6. Layer boundary

- **Harness** `backend/packages/harness/alpha/` → `alpha.*` — the publishable
  framework: orchestration, tools, sandbox, models, MCP, skills, config.
- **App** `backend/app/` → `app.*` — unpublished: the Gateway and the IM channels.
- **Public extension contract** `backend/packages/extension-api/` →
  `alpha_extension_api.*`.

**Rule: App imports alpha; alpha never imports app.** Enforced by
`backend/tests/test_harness_boundary.py`, which is an **AST scan** for the
prefix string `app.` — so it catches `from app.x import y` but would miss a
relative import resolving into `app`, a computed `importlib.import_module`, and
any file that fails to parse (`except SyntaxError: return []`).

---

## 7. Two documentation contradictions found, unresolved by reading

Both are recorded rather than guessed:

1. **Side-effect ledger.** `docs/architecture/durable-runtime.md:490-502` states
   `SqlSideEffectLedger` is constructed by no production module, the table stays
   empty and `list_unknown()` always returns `()`. The harness guide
   (`alpha/runtime/AGENTS.md`) asserts the opposite in an `<!-- honesty-claims -->`
   block (`side_effect_ledger_production_writer: exists`). One document is stale.
   **I did not adjudicate this** — resolving it requires tracing
   `app/gateway/deps.py`, which I did not do. Flagged as an open finding.
2. **`docs/ARCHITECTURE.md` §5's middleware table** lists nine named stages; a
   grep of the whole backend finds definitions for only three of them
   (`TokenBudgetMiddleware`, `SubagentDateContextMiddleware`,
   `DynamicContextMiddleware`). `SecurityEnclaveMiddleware`,
   `SubagentCapacityMiddleware`, `ToolPolicyMiddleware`, `ToolAssemblyMiddleware`,
   `TrajectoryAuditMiddleware` and `RunJournalCallback` have **no definition
   anywhere**. The documented "deterministic sequence of middleware layers" is
   therefore fiction.

---

## 8. What this audit measured on the live system

- Gateway `/health/ready`: 200, `database: ok`, `checkpointer: ok`, warm ~100 ms.
- `/health` liveness: 200. Note `/api/health` **404s** — the live route is `/health`,
  so any check written against `/api/health` is silently hitting nothing.
- `GET /api/bots` → `count: 58` (later 60), **0 duplicate `name`s, 8 colliding
  `display_name`s covering 35 of 58 cards**. This is the measured basis of fix
  F3.
- `GET /api/supervision/fleet` → 200 with four reserved keys alongside the worker
  map. Measured basis of fix F2.
- `GET /api/console/stats` → `total_agents: 0` — and that is **honest**: it counts
  custom agent *profiles*, not bots. The vitals strip's "0 agents" is correct.
- `GET /api/company/status` → **404 "No active organizations found. Bootstrap a
  company first."** — an honest, actionable answer that the UI surfaces verbatim.
- No Docker daemon: nginx `:2026` and Provisioner `:8002` unexercisable here.
=======
# Alpha system map

This map describes the code paths and persistence owners in this checkout. It is
an architecture map, not evidence that the services are currently running.
At capture time there is no `config.yaml`, no backend virtual environment, no
frontend `node_modules`, and no listener on ports 2026, 3000, 8001, or 8002.

## Service topology

| Service | Port | Role and boundary |
| --- | ---: | --- |
| Nginx | 2026 | Sole public entry point. Routes `/api/*` to Gateway and non-API paths to the frontend. Published Docker ingress defaults to loopback. |
| Gateway | 8001 | FastAPI API and embedded LangGraph-compatible runtime; owns run admission, tool execution, persistence, and SSE. |
| Frontend | 3000 | Next.js workspace. Uses the shared API client and normally reaches Gateway through the relative `/api` path served by Nginx. |
| Provisioner | 8002 | Optional sandbox provisioning service; availability depends on operator configuration. |

Sources: root `AGENTS.md` (Service Topology), `backend/AGENTS.md`
(Running the Application), `backend/app/gateway/app.py`, and
`backend/app/gateway/deps.py::langgraph_runtime`.

## Agent execution path

```text
Workspace tab / ChatView
  -> frontend/src/lib API or chat client
  -> Nginx /api (or direct Gateway in development)
  -> app.gateway.routers.thread_runs
  -> existing run admission / RunManager lifecycle
  -> alpha.runtime.runs.worker.run_agent
  -> alpha.agents.make_lead_agent and configured middleware/graph
  -> assembled tools -> tool policy -> sandbox/provider boundary
  -> configured LangGraph checkpointer/store and run/event persistence
  -> StreamBridge SSE -> frontend stream reducer / activity and receipts
```

The run worker is background work owned by `RunManager`, not work scoped to an
open browser connection. `thread_runs.py` owns run creation, streaming, status,
cancellation, resume, and event routes. The frontend requests `values`,
`messages-tuple`, and `custom` stream modes for state, message chunks, and
subagent/task events. A missing terminal frame is not a successful run.

Sources: `backend/app/gateway/routers/thread_runs.py`,
`backend/packages/harness/alpha/runtime/runs/manager.py`,
`backend/packages/harness/alpha/runtime/runs/worker.py`,
`backend/packages/harness/alpha/agents/__init__.py`,
`frontend/src/AGENTS.md` (Data Flow), and
`frontend/src/lib/sse-reducer.ts`.

## State and durability owners

| State | Owner / location | Durability boundary |
| --- | --- | --- |
| Conversation graph checkpoints and long-term graph store | `langgraph_runtime()` builds these from `config.yaml` through `make_checkpointer()` and `make_store()`; supported configured backends include memory, SQLite, and PostgreSQL. | Memory is process-local; SQLite/PostgreSQL persist according to configured backend. |
| Run records and run event history | `RunManager` with the Gateway run repository and event store, initialized from the configured database. | Persistent when a durable DB backend is configured; runtime in-memory mode does not provide restart durability. |
| Uploads and generated workspace files | Gateway upload/artifact and sandbox paths under the configured Alpha/runtime or workspace roots. | Filesystem persistence; isolation and lifecycle depend on the relevant thread/owner path and configured sandbox. |
| Project/workforce data | Project services and configured persistence repositories; file-backed project state lives below `runtime_home()/projects/`. | Local runtime files are not a cross-process transaction or exactly-once coordinator. |
| Cognitive memory | `get_paths().user_dir(owner) / "cognitive_memory"`; atomic snapshots and a per-directory process cache. | Owner-scoped disk state; process locks do not provide multi-process coherence. |
| Scheduled-task definitions and occurrences | `ScheduledTaskService` and the scheduled-task persistence tables; execution reuses the Gateway `RunManager` path. | Durable queue/lease behavior depends on the configured database and scheduler mode. |
| Dynamic-workflow journals, run projections, plans, and leases | `runtime_home()/workflow_store/` (JSONL events and atomic JSON projections); plan data below `workflow_store/plans/`. | Restart-recoverable for one Gateway process; event-log and wave/lease coordination are not a shared multi-worker exactly-once runtime. |
| Configuration | Operator-managed `config.yaml` and `extensions_config.json`; neither is committed. | Filesystem configuration. Startup-only components are frozen at Gateway bootstrap. |

Sources: `backend/app/gateway/deps.py::langgraph_runtime`,
`backend/packages/harness/alpha/runtime/checkpointer/async_provider.py`,
`backend/packages/harness/alpha/workflow/event_log.py`,
`backend/packages/harness/alpha/workflow/plan_graph.py`,
`backend/packages/harness/alpha/runtime/AGENTS.md`,
`backend/packages/harness/alpha/memory/cognitive/AGENTS.md`,
`backend/app/scheduler/service.py`, and `docs/WORKFORCE.md`.

## Self-inventory plane

`alpha.workflow.registry` is the read-only capability registry. Its
`manifest_source` is the single loader for generated
`contracts/feature_manifest.json`; the engines and wiring registries project
that manifest rather than reconstructing their own counts. The model-facing
`alpha_capability` tool and `GET /api/intelligence/inventory` expose bounded
inventory/diagnosis. Listing a capability does not execute it: descriptor
health remains `unverified`, unreadable counts remain `null`, and config
diagnosis proposes changes without applying them.

Sources: `docs/SELF_AWARENESS.md`, `backend/AGENTS.md` (Integration health +
autonomy ownership), and `backend/scripts/generate_feature_manifest.py`.

## Workspace view registry

The three required wiring points are:

1. The `WorkspaceView` union and `WORKSPACE_TABS` in
   `frontend/src/components/NavTabs.tsx` define identifiers and navigation.
2. Lazy section imports in `frontend/src/components/ChatView.tsx` load sections.
3. `ChatView`'s render dispatch maps each selected view identifier to its
   section.

A section lacking any required registration is unreachable/dead code. This map
does not assert that every tab or nested tab has been exercised in a browser.

Sources: `frontend/src/AGENTS.md`, `frontend/src/components/NavTabs.tsx`, and
`frontend/src/components/ChatView.tsx`.

## Dynamic workflow boundary

Dynamic workflows use the workflow engine, append-only JSONL event journal,
materialized projections, fenced attempt leases, recovery routes, and
owner-scoped Gateway APIs. The local digest executor is explicitly a
`local_digest_projection`: it evaluates graph mechanics, not domain work, and
does not satisfy acceptance. The workflow journal, wave concurrency, and lease
store are not a distributed executor or cross-process exactly-once guarantee.

Sources: `docs/ALPHA-WORKFLOW-ARCHITECTURE.md`,
`docs/ALPHA-WORKFLOW-CURRENT-STATE.md`, and
`backend/packages/harness/alpha/workflow/dynamic_bridge.py`.

## Environment observation

At the initial baseline, the worktree had no runtime config, dependencies were
not installed, and no local listener answered on the four service ports. Setup
later seeded ignored local config and installed dependencies. During the final
local probe, Gateway `/health/ready` and the frontend root both returned HTTP
200 on ports 8001 and 3000; Nginx was absent and port 2026 did not answer.
Readiness is not evidence of an authenticated user task, a live model-backed
run, scheduler firing, or a full subsystem matrix. The configured OpenRouter
`union-alpha` probe also failed with HTTP 402 for insufficient credits. These
are limits on the evidence, not successful verification results.
>>>>>>> mitevs1949-spec-production-workflow-app
