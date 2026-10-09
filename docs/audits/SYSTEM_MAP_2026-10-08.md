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
