# ALPHA Architecture Audit (Phase 0 — Discovery)

Status: **complete codebase audit, confirmed from code**. Every finding
below names the file (and line where applicable) it was confirmed
from. Where something was not verified, it says *unverified* rather
than guessing. This document is the gate for all later phases: no
implementation phase may start from an assumption this audit did not
check.

Audit date: 2026-10-03. Auditor scope: full repository
(root, `backend/`, `frontend/`, `recovery/`, `docker/`, `scripts/`,
`contracts/`, `docs/`, `electron/`, `installer/`).

Method: repository walk, subsystem guide reads
(`AGENTS.md` chain), registry inspection
(`contracts/feature_manifest.json`), targeted ripgrep sweeps for
anti-patterns (bare `except:`, `except Exception: pass`, unbounded
`while True:`, timeout-less `subprocess`), and cross-check of every
capability claim in `docs/PRODUCTION_READINESS_INVENTORY.md` (the
repo's own authority on what is implemented) against the code it
names.

Measured scale (confirmed):

| Measure | Value | Evidence |
| --- | --- | --- |
| Backend test files | 1319 | `backend/tests/**/*.py` count |
| Harness engine packages | ~130 | `backend/packages/harness/alpha/` directories |
| Registered tools | 134 | `contracts/feature_manifest.json` |
| Registered routers | 65 | `contracts/feature_manifest.json` |
| Registered middlewares | 43 | `contracts/feature_manifest.json` |
| Registered supervisor loops | 9 | `contracts/feature_manifest.json` |
| Gateway router files | 67 | `backend/app/gateway/routers/` |
| Frontend UI sections | 35 | `frontend/src/components/sections/` |

---

## 1. System map

### 1.1 Topology (confirmed)

Four cooperating services behind one reverse proxy
(`AGENTS.md` root, `docker/`, `deploy/`):

| Service | Port | Role | Process |
| --- | --- | --- | --- |
| Nginx | 2026 | Unified entry; `/api/langgraph/*` → Gateway native `/api/*`, other `/api/*` → Gateway, non-API → Frontend | container |
| Gateway API | 8001 | FastAPI REST + embedded LangGraph runtime (`RunManager` + `run_agent()` + `StreamBridge`) | `backend/app/gateway/app.py` |
| Frontend | 3000 | Next.js App Router chat/ops UI | `frontend/` (pnpm) |
| Provisioner | 8002 | Optional sandbox provisioner (K8s mode) | `backend/` provisioner routes |

Both compose files publish nginx as
`"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"` — loopback by
default; a bare `"${PORT}:2026"` binds `0.0.0.0`. Pinned by
`backend/tests/test_compose_default_bind_host.py`.

Backend layering (enforced by `tests/test_harness_boundary.py`):

- **Harness** `backend/packages/harness/alpha/` — publishable
  framework, import prefix `alpha.*`. Never imports `app.*`.
- **App** `backend/app/` — `gateway/` (FastAPI), `channels/`
  (Feishu, Slack, Telegram, DingTalk), `scheduler/`,
  `mcp_tasks/`, `subagent_batches/`. Import prefix `app.*`.
- **Extension API** `backend/packages/extension-api/` — public
  extension contract (`alpha_extension_api.*`).

### 1.2 Component map (prompt §2 checklist → actual owner)

| Prompt component | Alpha owner | Status |
| --- | --- | --- |
| frontend | `frontend/` Next.js App Router, `src/lib/api-client.ts` (`GATEWAY_BASE`), SSE stream | implemented |
| backend | harness + app split above | implemented |
| API gateway | `app/gateway/app.py` + middleware chain (CSRF, auth, security headers, body limit, trace) | implemented |
| orchestrator | `alpha.orchestrator.loop.ExecutionKernel` + `alpha.orchestrator.dynamic_service` (opt-in) | implemented (opt-in) |
| task engine | `alpha.runtime.runs.RunManager` (sole lifecycle owner) | implemented |
| planner | plan-mode `write_todos` middleware; `alpha.planning`; swarm task decomposer; workflow graphs | partial |
| executor | `runtime/runs/worker.py::run_agent` (event-loop offloaded), sandbox exec | implemented |
| agents | `alpha.agents` lead agent; `alpha.subagents.SubagentExecutor` | implemented |
| subagents | `task` / `batch_task` tools, per-execution workspaces, receipt citations | implemented |
| memory | `alpha.memory` (20+ subpackages; cognitive memory has its own contract) | implemented |
| tools | `alpha.tools` — 134 registered tools, discovery catalog with trust separation | implemented |
| MCP | `alpha.mcp` (lazy init, content-signature invalidation, durable `McpTaskService` + `mcp_tasks`) | implemented |
| A2A | `app/gateway/routers/a2a.py`, `alpha.peer_network` (cross-installation plane) | implemented |
| browser | `community/browser_automation` (Playwright, observe→act→re-observe) | implemented |
| terminal | sandbox bash (`allow_host_bash`), `python_repl` (default off) | implemented |
| filesystem | sandbox path tools, uploads (atomic `.upload-*.part` staging) | implemented |
| Git | `alpha.workspace_changes` recorder, worktree strategies doc; no agent-owned worktree flow | partial |
| scheduling | `app/scheduler/service.py` (durable, lease-owned) + `alpha.scheduler` | implemented |
| automation | autonomy supervisor loops (9 registered, config-gated) | implemented |
| notifications | run delivery receipts, channel bridges, `deliveries.py` router | implemented |
| database | SQLite/Postgres via alembic (≥ migration 0027), `RunStore`, `ThreadMetaStore`, checkpointer savers | implemented |
| queues | scheduler queue, MCP task queue, subagent batch queue (all DB-backed) | implemented |
| event bus | `alpha.events.bus` (in-process), run event stores (memory/JSONL/SQL/Postgres) | implemented |
| logging | stdlib logging + `trace_context.py` (`X-Trace-Id` ContextVar), `logging.enhance.enabled` | partial (no per-error structured failure ID) |
| telemetry | `runtime/journal.py` run journal, token meter, trace middleware, system monitor | implemented |
| security | auth/authz middlewares, run-context trust boundary, egress, guardrails, policy, safety | implemented (suite unverified) |
| configuration | `config.yaml` (single file for every model setting), `extensions_config.json`, `plugins:` list; hot reload with honest failure | implemented |
| deployment | compose (dev/prod), Helm (`deploy/helm/alpha`), Electron, Windows installer | implemented |
| update mechanism | `alpha.evolution.update_engine` (guarded, opt-in policy) | implemented (opt-in) |

### 1.3 Execution path trace (user request → response)

Confirmed path:

```text
HTTP/SSE POST /api/threads/{id}/runs (or /api/langgraph/* rewrite)
  -> Gateway middleware chain (CSRF, auth, security headers, body limit, trace id bind)
  -> run router -> RunManager.start_run (durable admission, thread operation lease)
  -> runtime/runs/worker.py::run_agent (asyncio.to_thread boundary)
  -> make_lead_agent graph (middleware chain, tool registry, MCP catalog, subagents)
  -> StreamBridge -> SSE frames (values / messages-tuple / custom, heartbeat every 15 s)
  -> RunJournal (event persistence, delivery receipts, token usage)
  -> terminal status persisted via RunStore; lease released
```

Crash anywhere in the middle: the run row stays `running` with an
expired lease; startup/periodic reconciliation terminalizes it as
`error/orphan_recovered`; `app.gateway.run_recovery.SafeRunRecoveryService`
then performs a bounded durable scan and creates an idempotent
resumed run from the latest safe checkpoint — but only when every
pending LangGraph node is a known model/agent node. A pending
`tools`/MCP/shell/browser/write/delete/payment/unknown node is
terminalized as `recovery_confirmation_required` because the
external action may already have taken effect
(`backend/packages/harness/alpha/runtime/AGENTS.md`,
`backend/app/gateway/run_recovery.py`).

---

## 2. Component cards

Format per prompt §2: responsibility / inputs / outputs /
dependencies / persistence / failure modes / recovery / security
boundary / tests / known limitations. Only components whose behavior
was confirmed from code are carded here; the rest are covered by the
capability matrix in §3.

### 2.1 RunManager (`alpha/runtime/runs/manager.py`, `worker.py`, `schemas.py`)

- **Responsibility**: sole lifecycle owner of every run
  (pending/running/success/error/timeout/interrupted).
- **Inputs**: `RunCreateRequest` (thread id, message, config,
  `autonomous` flag, acceptance criteria).
- **Outputs**: durable `RunRecord`, SSE stream, terminal status.
- **Dependencies**: checkpointer savers, `StreamBridge`,
  `RunEventStore`, thread admission lease.
- **Persistence**: `RunStore` (memory/JSONL/SQL/Postgres);
  checkpoints in the configured saver; run durations in metadata.
- **Failure modes**: worker crash (orphan row), provider timeout,
  SSE disconnect (`on_disconnect=continue` default), stall
  (`runtime/selfheal/run_stall.py` watchdog, 900 s default).
- **Recovery**: lease reconciliation → `orphan_recovered`;
  `SafeRunRecoveryService` bounded resume from last safe checkpoint,
  fail-closed around side effects; model-failure replay rewinds
  exactly one parent checkpoint only when the head message is
  explicitly stamped `alpha_error_fallback`.
- **Security boundary**: run-context trust boundary
  (`merge_run_context_overrides` forwards internal keys only;
  `strip_internal_context_keys` scrubs them from `context` *and*
  `configurable`); ownership checks per user/thread.
- **Tests**: `tests/test_safe_run_recovery.py`,
  `test_gateway_run_recovery.py`, `test_run_repository.py`,
  `test_gateway_run_drain_shutdown.py`, `test_run_stall_watchdog.py`.
- **Known limitations**: JSONL/memory stores are single-process;
  recovery resume launcher for parked network-wait sessions is
  absent (the recovery service owns continuation instead).

### 2.2 Durable runtime layer (`runtime/sessions/`, `network/`, `side_effects/`, `supervisor/`, `shutdown.py`)

- **Responsibility**: the five additive gaps that make "a
  process/UI/network/provider failure must not become a task
  failure" true. None is a second lifecycle owner.
- **Sessions**: 17-state `SessionState` (ACTIVE/WAITING/TERMINAL);
  terminal run status outranks every live condition; `COMPLETED`
  still means "finished", never "verified".
- **Network**: four states (`UNKNOWN` is real and still permits an
  attempt; `DEGRADED` never parks; a `TIMEOUT` proves nothing).
  `classify_network_error().proves_link_down` is true only for DNS
  failure / refused / explicit unreachable. TCP-connect only.
- **Side effects**: per-effect `UNKNOWN` ledger (SQL migration
  0027); digests only, never arguments; a late `complete` from a
  second process is refused by conditional transition;
  `SideEffectReclaimer` closes `in_flight` rows under dead leases.
- **Supervisor**: restart policy with a **sliding-window** budget
  (consecutive-failure counters cannot catch "crashes once an hour,
  forever"); `RESTART` / `SAFE_MODE` (once per episode) / `GIVE_UP`.
- **Shutdown**: eight-phase ordered drain; a failed prerequisite
  *skips* its dependent step; emergency path hard-capped at 5 s.
- **Tests**: `test_durable_session_state_machine.py`,
  `test_network_resilience.py`, `test_network_wait_registry.py`,
  `test_network_wiring.py`, `test_side_effect_ledger.py`,
  `test_side_effect_ledger_sql.py`, `test_process_supervisor.py`,
  `test_planned_shutdown.py`, plus the real-process drill
  `backend/scripts/realtime_gateway_check.py`.
- **Known limitations (stated by the repo itself, confirmed in
  `runtime/AGENTS.md` honesty claims)**:
  1. With `database.backend: memory` there is **no** side-effect
     store at all.
  2. The runtime supervisor is **not** wired into the Windows
     launcher (`start.ps1` still owns process startup).
  3. A parked session's registry is durable but has **no resume
     launcher** — continuation belongs to `SafeRunRecoveryService`.

### 2.3 Autonomy supervisor (`app/gateway/autonomy/supervisor.py`)

- **Responsibility**: single owner of all background loops.
- **Behavior**: loops declared in `register_default_loops()` with a
  `loops.py` adapter; gated under `config.yaml -> autonomy.loops`
  (absent id = disabled); sync ticks on threads; overlapping-tick
  cap; restart inside a budget then **park**; publishes
  `autonomy.loop.completed` on the in-process bus.
- **Admission**: `_fleet_admits_tick` fails closed — an unreadable
  fleet-control state or engaged ESTOP stops the loop rather than
  running it.
- **Tests**: `tests/test_autonomy_supervisor.py`.
- **Known limitation**: in-process only; the OS-level restart of the
  whole Gateway is owned by the Windows recovery chain (§2.9).

### 2.4 Windows self-healing chain (`recovery/`)

- **Four layers, none responsible for its own recovery**:
  - Layer 4: Windows Task Scheduler tasks `Alpha_Autostart` /
    `Alpha_Watchdog` run `watchdog.ps1 -Once` every 5 min and
    verify/recreate the Layer-3 loop (right installation, fresh
    heartbeat).
  - Layer 3: `recovery/watchdog.ps1` loop — monitors
    gateway/frontend/launcher; escalates defer → component restart
    → full stack restart (the full-stack step is gated: only when
    the launcher cannot act or a component exhausted its budget).
  - Layer 2: `start.ps1` launcher — starts children, monitors,
    restarts them; readiness re-probed every pass, never latched;
    heartbeat probes `/health/ready` and `/` (serving, not just
    bound ports).
  - Layer 1: gateway, frontend, workers.
- **Detachment**: VBScript shims orphan each layer so killing one
  cannot kill another. `stop.ps1` writes
  `logs/alpha_maintenance.json` — the only sanctioned way to stay
  stopped; crashes and `taskkill` never create it and are always
  recovered.
- **Recovery history**: `logs/recovery_history.jsonl` (rotation
  bounded, no secrets).
- **Tests**: `backend/tests/test_launcher_watchdog_budget.py`,
  `test_watchdog_decision_table.py`,
  `test_launcher_diagnostics.py`,
  `test_tray_status_health_truthfulness.py`,
  `test_tray_status_logo.py`; `recovery/verify_recovery.ps1`
  expects 30/30.
- **Known limitations**: Windows-only (POSIX relies on
  `make dev`/compose restart policies); generated
  `recovery/autostart/*.vbs` are gitignored, so a fresh clone must
  run `recovery/register_autostart.ps1 -Force` or the chain silently
  never starts (documented failure mode with detection instructions).

### 2.5 Scheduler (`app/scheduler/service.py`, `alpha/scheduler/`)

- **Responsibility**: durable scheduled-task execution.
- **Behavior**: lease-owned (`lease_owner = hostname:uuid`,
  `lease_seconds`, `run_lease_grace_seconds`), lease reconciliation
  on startup and periodically; `next_run_at` from
  `alpha.scheduler.schedules`; executions reuse the Gateway run
  lifecycle (never a parallel stack); scheduled runs get
  `context.non_interactive=true` as an **internal-only** context key
  (merged only when the request authenticated as the process-internal
  user), which excludes `ask_clarification` so scheduler runs cannot
  stall on human confirmation.
- **Tests**: scheduled-task suites under `backend/tests/` (see
  `docs/WORKFORCE.md` contract).
- **Known limitation**: multi-instance recovery is lease-based; a
  crashed owner's occurrence is recovered by another instance only
  after lease expiry.

### 2.6 Evidence layer (`alpha/evidence/`, `alpha/runtime/runs/verification.py`, `runtime/journal.py`)

- **Responsibility**: make claims checkable; never present a
  terminal `success` as verified without evidence.
- **Evidence store**: `EvidenceStore` at `runtime_home()/evidence`
  with `EvidenceRecord` / `EvidenceKind` / `Candidate` /
  `Evaluation` / `Promotion` types; wired through
  `app/gateway/routers/evidence.py` and
  `agents/middlewares/finish_first_verifier_middleware.py`;
  skill-usage telemetry bridge (`evidence/skill_usage_evidence.py`).
- **Run verification overlay**: optional run `acceptance_criteria`
  (canonical forms `file:<path> exists|non-empty`,
  `file_written:<path>`, `tests_passed:<command>`) checked against
  independently collected evidence; `RunManager` stays the sole
  lifecycle owner.
- **Delivery receipts**: `RunJournal` records each non-empty artifact
  update per tool `Command`; every run's changed files under
  `/mnt/user-data/outputs` must be covered by a `present_files`
  attribution — a missing presentation becomes a run **error**, and a
  successful presentation is also downgraded to error if its receipt
  cannot be durably verified.
- **Tests**: `tests/test_run_acceptance_criteria.py`,
  `test_evidence_action_rest_api.py`, `test_atomic_write_cleanup_masking.py`,
  `test_dormant_wiring_p3.py`.
- **Known limitation** (per `PRODUCTION_READINESS_INVENTORY.md`):
  evidence verification is **partial** — trusted collectors for
  tests, artifacts, HTTP checks and reviewer approvals are not yet
  added.

### 2.7 Research engine (`alpha/research/`, `community/agent_eye/`)

- **Responsibility**: deep research with provenance.
- **Behavior**: `DeepResearchEngine` (search/fetch backends,
  `EvidenceSource` records, provenance, dedupe, bounded worker
  queue); wired via the `deep_research` tool; AgentEye adapter is
  pinned to an immutable upstream commit, operator-allowlisted,
  never constructs upstream orchestrator, reports `no_evidence`
  honestly, `citations_verified` semantics, and a
  `use_for_deep_research` DDGS fallback. Web-search recency
  (`time_range`) across DDG/Brave/Tavily/SearXNG/Sofya.
- **Tests**: `tests/test_deep_research_tool.py`,
  `test_agent_eye_integration.py`, `test_authority_receipts_and_taint.py`.
- **Known limitation**: research *memory* (cross-task source
  reuse, "do not repeatedly search the same question") is not a
  first-class store — research results live in thread context and
  the evidence store, but there is no researched-question ledger.

### 2.8 Verification / self-repair (`alpha/verification/`, `alpha/selfrepair/`)

- **Verification controller**: `VerificationController` with
  `LoopDirective`, `RepairOutcome`, `BashToolExecutor`,
  `surface_from_changes`, attempt budgets — a real execute-and-check
  loop for coding work.
- **Self-repair**: **honestly refuses to execute repairs** —
  `selfrepair/engine.py` provides symptom classification and
  disk-health observations only; `verify_repair` returns false with
  an explicit missing-verifier reason; `attempt_repair` refuses
  unimplemented actions without changing runtime state. Never report
  `fixed` from a repair-kind label.
- **Tests**: `tests/test_workforce_intelligence.py`,
  verification suites under `backend/tests/`.
- **Known limitation**: there is no general
  diagnose → research → generate-fix → test-fix → apply-fix engine;
  recovery today is per-surface (runs, swarms, workflows) and
  self-repair is observation-only by design.

### 2.9 Memory (`alpha/memory/`)

- **Responsibility**: persistent agent memory.
- **Structure**: 20+ subpackages — `cognitive` (own contract:
  server-resolved owner, per-owner/per-directory process cache,
  atomic fsync-backed snapshots, fail-closed owner/corrupt-state
  handling), `codebase`, `narrative`, `social`, `affective`,
  `dreaming`, `entities`, `evaluation`, `fabric`, `fusion`,
  `health`, `policy`, `prospective`, `scenarios`, `utility`,
  `wiki_vault`; composition/rank/consolidation modules
  (`capture_composition`, `recall_composition`, `rerank`,
  `kibitzer`, `persistence_nudge`, `contrastive_trajectory_replay`).
- **Persistence**: `ThreadMetaStore` with JSON filter semantics
  identical across memory/SQLite/Postgres (missing ≠ null, bool ≠
  int); FTS5 content search (migration 0023) surfaced as the
  `session_search` tool.
- **Tests**: cognitive memory suites, `test_sqlite_lifespan.py`,
  backend blocking-io anchors for memory backends.
- **Known limitation**: the number of overlapping memory packages is
  itself a maintenance risk (see §5 duplication); consolidation does
  not yet deduplicate *across* all memory layers into one
  contradiction-checked store.

### 2.10 Tools and discovery (`alpha/tools/`)

- **Responsibility**: 134 registered tools; assembly via
  `get_available_tools` (config tools → MCP tools → builtins →
  subagent tools); discovery catalog with **trust separation**
  (`UNTRUSTED_SOURCES = {MCP, CLIENT}` — their parameter schemas are
  never indexed, rendered, or validated by this layer).
- **Tool metadata contract** (optional `BaseTool.metadata` keys):
  `discovery_catalog_mode`, `discovery_risk_level`,
  `discovery_permissions`, `discovery_execution_mode`,
  `discovery_input_schema`, `discovery_output_schema`,
  `discovery_client_provided`, `discovery_plugin`.
- **Confirmed gap** (`tools/discovery/catalog.py:51-55`, verbatim):
  *"Alpha has no per-tool risk/permission registry today, so these
  default honestly rather than being invented here: risk `low`, no
  required permissions, `catalog-eligible`, `parallel`."*
  `resolve_permissions` **discloses** permissions but the docstring
  states enforcement "lives in the policy layer that already exists"
  — i.e. distributed authz, not a single per-tool policy point.
- **Tests**: `tests/test_feature_manifest_wiring.py`,
  `tests/test_no_orphan_modules.py`, discovery suites.
- **Known limitation**: no per-tool declaration of side effects,
  reversibility, timeout, retry policy, or verification method
  (prompt §20 unimplemented) — the first implementation increment
  (§7) closes exactly this gap.

### 2.11 Sandbox (`alpha/sandbox/`, `community/*_sandbox/`)

- **Responsibility**: isolated execution.
- **Behavior**: `SandboxProvider.acquire_async()`; local sandbox,
  Docker `AioSandbox`, E2B, BoxLite; path restrictions, process
  manager, lease; `allow_host_bash` and `allow_in_process_repl`
  are separate switches, `python_repl` default-off (it `exec()`s in
  the Gateway process with no sandbox boundary — documented);
  uploads stage through hidden `.upload-*.part` files before atomic
  replace; E2B output sync is bounded by aggregate ceilings
  (`_MAX_SYNC_TOTAL_BYTES`, `_MAX_SYNC_FILES`, `_SYNC_DEADLINE_SECONDS`).
- **Tests**: sandbox suites, `tests/test_python_repl_tool_config_off_loop.py`,
  blocking-io anchors.
- **Known limitation**: an in-process REPL is host-execution by
  design (opt-in); resource limits are process-level, not per-tool
  cgroups.

### 2.12 Browser autonomy (`community/browser_automation/`)

- **Responsibility**: agentic browser control that verifies state.
- **Behavior**: Playwright; every action returns a **fresh page
  snapshot** whose interactive elements are addressed by stable
  numeric `[ref]` indexes (stamped `data-df-ref`), so the model acts
  on what it just observed instead of holding stale handles; URLs
  SSRF-screened (`validate_public_http_url`, opt-out only for
  intentional internal targets); `cdp_url` fails closed unless
  `allow_unguarded_cdp: true`; hard `max_sessions` cap with
  pinned-session protection; per-thread workspace; Gateway rejects
  `GATEWAY_WORKERS > 1` when `browser_navigate` is configured.
- **Tests**: `tests/test_browser_automation.py` (mocked + real
  Chromium behind `importorskip`).
- **Prompt §25 verdict**: **implemented** — click success is never
  assumed; the resulting page state is re-observed.

### 2.13 Model routing and fallback (`alpha/models/`, `config.yaml`, `agents/middlewares/llm_error_handling_middleware.py`)

- **Responsibility**: one file for every model setting
  (`models[]`, `providers:`, `model_routing`, `default_model`,
  `model_catalog:`, `free_gateways:`, `model_pricing:`); every
  `model_routing:` name validated against `models[]` at load (a
  typo is a startup error, not a silent fallback); discovery
  (`GET /api/models/discovery`) refreshes daily-rotating catalogs;
  fail-closed routers.
- **Fallback**: LLM error-handling middleware; a model-failure
  replay may rewind exactly one parent checkpoint only when the
  head's last assistant message is explicitly stamped
  `alpha_error_fallback`; only transient/busy/burst/circuit-open
  reasons qualify — auth, quota, configuration and generic
  deterministic failures are **not** replayed.
- **Tests**: `tests/test_single_file_model_config.py`,
  `tests/test_e2e_honesty_model_slug.py` (live pin).
- **Known limitation**: model invocation logging with
  provider/model/task/latency/tokens/failure/fallback is partial
  (token usage is durable per model; per-invocation fallback logging
  rides the middleware).

### 2.14 Multi-agent surface (`alpha.subagents`, `alpha.swarm`, `alpha.enterprise`, `alpha.company_os`, `alpha.groups`)

- **Subagents**: `task` tool → `SubagentExecutor` with isolated
  runtime, per-execution parent-loop recorder bridge, receipt
  citations (`verification.receipts_enabled`), acceptance criteria
  handed to the executor as untrusted data; durable batches
  (`batch_task`/`batch_status`/`cancel_batch`) with owner-scoped
  exports; `ralph_loop` bounded self-improvement (default 3, hard
  cap 8, honest exhaustion language); nested loops denied to
  subagents by default.
- **Swarm v2**: explicit DAG plans, atomic JSON checkpoints,
  ordered JSONL audit events, bounded blackboard messages,
  lease-fenced task attempts, retry/backoff, measured budgets,
  deterministic reflection, consecutive-failure circuit breaking,
  optional evidence-backed consensus; `auto_replan` is a bounded
  deterministic repair-task expansion that **never** converts a
  failed task into success. Persistence adapter is **local and
  process-scoped** — "never call local JSON checkpoints cross-process
  exactly-once" (its own AGENTS.md).
- **Enterprise**: C-Suite hierarchy, mission-to-sprint DAG,
  RFC/blackboard consensus gating, token treasury, multi-sig release
  attestations.
- **Company OS**: durable multi-tenant organizations **indexed over**
  the real subsystems (employees = `alpha.bots`, projects =
  `alpha.projects`, work items = `alpha.kanban`, rooms =
  `alpha.groups`) — "index never duplicate", "measured or `None`",
  "a proposal is never an action"; its `company_operations` loop is
  default-off and model-free.
- **Groups**: nested rooms with the split "visibility may fan out to
  many parents; authority is at most one"
  (`GroupScope.parents` list vs `authority_parent` single); roster
  never writes `GroupRoom.members` (owned by
  `alpha.projects.crew.ensure_crew`); resolved roster is a
  recomputed projection, never a stored copy; relay is a copy with
  provenance (`forwarded_from`, `relayed: true`), `max_hop` default
  3; `MAX_DEPTH` 4, refused not clamped.
- **Known limitation**: four overlapping coordination systems exist
  (subagents, swarm, enterprise, company_os) plus group war rooms —
  see §5.

### 2.15 Dynamic workflows (`alpha.workflow/`, `alpha.orchestrator/`)

- **Responsibility**: typed workflow graphs with waves, conditional
  routing, bounded loops, retries, budgets, approval waits, patch
  OCC, replanning, replay and compensation.
- **Behavior**: `WorkflowNode.timeout_seconds` **enforced** through
  `run_with_deadline` (a missed deadline fails the node with the
  measured overrun and **fences** the in-flight call — CPython
  cannot kill a thread, so a late result is discarded, not
  adopted); wave concurrency is **opt-in** via
  `policies.max_concurrency`; executor-free node kinds (`CHECKPOINT`,
  `GOAL_GATE`, `HANDOFF`, `WAIT`, `EVENT_WAIT`, `PARALLEL`,
  `SUBWORKFLOW`); `time_travel` forks from event history; templates
  enforce `draft -> verified -> promoted` with evidence re-checks;
  `self_improvement` **only proposes** (every suggestion cites its
  measured signal; an unevidenced completion is `unproven`).
- **Honest default**: the default `alpha.local.digest` executor is a
  `local_digest_projection` — it hashes inputs to exercise graph
  mechanics and keeps `acceptance_passed=false`, never reported as
  domain-task acceptance. Real domain executors
  (`alpha.local.model` / `.tool` / `.subagent`) are **opt-in** via
  `bind_domain_executors()`.
- **Persistence**: local JSON, atomic and restart-recoverable for one
  Gateway process — **not** a shared multi-worker lease repository.
- **Tests**: `test_workflow_runtime_correctness.py`,
  `test_workflow_observability_router.py`, `test_workflow_time_travel.py`,
  `test_workflow_templates_and_improvement.py`.

### 2.16 Frontend (`frontend/`)

- **Responsibility**: chat + operations UI.
- **Structure**: Next.js App Router; `src/lib/api-client.ts`
  normalizes `NEXT_PUBLIC_GATEWAY_URL` → `GATEWAY_BASE` (unset or
  bare path = relative `/api`; full origin gets `/api` appended);
  35 sections including `RunInspectorSection` /
  `RunInspectorTimeline` / `RunReplayControls` / `RunUsagePanel`
  (debugger-mode equivalents), `SupervisorSection`,
  `ScheduledSection`, `MemorySection`, `WorkflowsSection` /
  `WorkflowRunInspector`, `WarRoomSection`, `SystemMonitorSection`,
  `SkillsSection`, `BotsSection`, `ForgeSection`,
  `PeerNetworkSection`, `CompanySection`, `KanbanSection`,
  `EvolutionSection`, `Benchmarks`.
- **Tests**: `src/lib/*.test.mjs` suites (branding, groups-tree,
  chat-stream, sse-reducer, runs-inspector, supervisor, …); package
  scripts `test`, `test:branding`, `test:extra`, `verify`,
  `typecheck`; no `format` script and no Prettier dependency.
- **Known limitation**: `pnpm test` globs `src/lib/*.test.mjs` only —
  suites outside that directory need their own script/CI step or they
  silently never run (stated in root `AGENTS.md`).

### 2.17 Observability (`alpha/events/`, `alpha/tracing/`, `alpha/observability/`, `app/gateway/system_monitor_service.py`)

- **Responsibility**: unified event model + host health.
- **Behavior**: `X-Trace-Id` / `alpha_trace_id` request correlation
  (ContextVar is the only source; a caller-sent id is replaced so
  the persisted run cannot disagree with header and logs); run event
  stores (memory/JSONL/SQL/Postgres) with FTS5 content search and
  message-seq stamping (#4666); SSE heartbeats (named `event:
  heartbeat` after 15 s of silence, invisible `: heartbeat` bridge
  comments do not reset the clock); host system monitor (CPU/RAM/
  disk/network/GPU with honest attribution, best-effort
  timeout-bounded cached samplers, `ALPHA_ADVANCED_MONITOR=0` kill
  switch, never raises into the sampling tick); `GET
  /api/ops/integration-health` exposes live coverage + supervisor
  status.
- **Known limitation** (per the inventory): observability is
  **partial** — dashboards, alerts, runbooks and an on-call ownership
  model are not in the repo.

### 2.18 Security (`app/gateway/auth*`, `alpha/authz`, `alpha/egress`, `alpha/guardrails`, `alpha/policy`, `alpha/safety`)

- **Responsibility**: authentication, authorization, injection
  defense, secret hygiene.
- **Behavior**: Gateway auth/authz middlewares, CSRF, security
  headers, public body limit, internal auth, LangGraph auth,
  `auth_disabled` mode; run-context trust boundary (§2.1); vision
  injection defense (server-owned metadata marker + reserved ID
  prefix, both required; the middleware sweeps its message out of
  every request); MCP/CLIENT tools treated as untrusted data; peer
  network credential stripping; ACP thought chunks internal-only;
  secret redaction in run metadata.
- **Tests**: authorization suites, `tests/test_release_gate.py`
  (measures prompt-injection resistance numerically).
- **Known limitation** (per the inventory, row "Prompt-injection
  security suite"): **unverified** — guardrails are documented but
  there is no fixture corpus covering tool output, uploads, web
  content and MCP events.

### 2.19 Update system (`alpha/evolution/update_engine.py`)

- **Responsibility**: guarded source auto-update.
- **Behavior**: mutates only a local Git checkout; published release
  (or explicit dev branch) resolves to an immutable commit; rejects
  dirty/diverged worktrees; requires manifest-matching remote,
  allowed branch, fast-forward ancestry, stable target ref; optional
  signature verification; snapshots `config.yaml` /
  `extensions_config.json` outside Git; cross-process maintenance
  barrier closing new run admission; argv-only hooks; restores
  declared dependencies on rollback; records honest pre-mutation
  recovery; never accepts a client-supplied URL/ref. The committed
  `config/update-policy.json` is a **disabled, credential-free
  template**; Docker/Helm/Electron artifacts are never updated in
  place.
- **Tests**: `tests/test_auto_update.py`.

---

## 3. Capability matrix — master-prompt requirements vs. reality

Verdicts: **exists** (wired and tested), **partial** (foundation,
not end-to-end), **missing** (not implemented). Confirmed against the
code named in §2 and `docs/PRODUCTION_READINESS_INVENTORY.md`.

| Prompt § | Capability | Verdict | Evidence / gap |
| --- | --- | --- | --- |
| 4 | Universal task lifecycle | exists | Run lifecycle + session states (17) + recovery dispositions |
| 5 | Persistent task engine | exists | `RunManager`, checkpointers, `SafeRunRecoveryService`, stall watchdog |
| 6 | Goal manager | partial | `alpha.goals`, `goal_contracts` / `goal_integrity` routers, durable goal loop in recovery; no full goal-decomposition tree (objective→milestone→task→subtask→action) |
| 7 | Research engine | exists | `DeepResearchEngine` + AgentEye + recency; research *memory* missing |
| 8 | Anti-hallucination / evidence | exists | `alpha.evidence`, run verification overlay, delivery receipts, `COMPLETED ≠ verified` |
| 9 | Self-correcting failure recovery | partial | Per-surface recovery (runs/swarm/workflow); general diagnose→fix engine absent; self-repair observation-only by design |
| 10 | World model | partial | `alpha.knowledge`, `alpha.state`, blackboard, lineage, trajectory — no unified world-state store with uncertainty/provenance |
| 11–12 | Memory architecture + consolidation | exists | 20+ memory packages, cognitive contract, consolidation modules; cross-layer dedup/contradiction store missing |
| 13–14 | Multi-agent + delegation | exists | Subagents, swarm, enterprise, company_os (see §5 duplication) |
| 15 | Git-first engineering | partial | Worktree strategies doc, workspace-changes recorder; no agent-owned worktree/PR flow |
| 16 | Code agent loop | partial | `alpha.coding`, `alpha.critic` rubric, verification controller (bash execution); no single READ→…→COMMIT pipeline |
| 17 | Self-improvement engine | partial | `alpha.rsi`, `alpha.evolution`, `workflow.self_improvement` (proposes-only), `ralph_loop` (bounded) |
| 18 | Evaluation system | partial | `alpha.benchmarks`, release gate (numeric promotion gates); no permanent stored benchmark-results-over-time suite |
| 19 | Adversarial testing | partial | Release gate measures injection resistance; no dedicated adversarial fixture corpus |
| 20 | Tool governance | **missing** | Confirmed at `tools/discovery/catalog.py:51-55` — no per-tool risk/permission registry |
| 21 | Sandboxing | exists | §2.11 |
| 22–23 | Security model / prompt-injection defense | partial | Many defenses wired; suite unverified (inventory) |
| 24 | MCP / A2A / plugin system | exists | `alpha.mcp`, `routers/a2a.py`, `alpha.peer_network`, extensions manager with enable/disable |
| 25 | Browser autonomy | exists | §2.12 — verify-after-act is the design |
| 26 | Scheduler | exists | §2.5 — durable, lease-owned, idempotent dispatch |
| 27 | 24/7 supervisor | exists (Windows) / partial (in-process policy) | §2.4 four-layer chain; runtime supervisor policy not launcher-wired |
| 28 | Self-healing | partial | Process-level healing exists; component-level auto-repair deliberately refuses |
| 29 | Offline-first | exists | `runtime/network/` first-class states; parked sessions durable; timeouts never prove link-down |
| 30–31 | Model routing / fallback | exists | §2.13 |
| 32 | Context engineering | exists | Summarization, compaction, prune tiers, tool-output externalization |
| 33 | Planner / critic / verifier | partial | Plan-mode todos, `alpha.critic`, `alpha.planning`, verification controller |
| 34 | Multi-hypothesis reasoning | partial | Deliberation (Wald SPRT), council; no persistent H1/H2/H3 store |
| 35 | Causal failure analysis | partial | Retrospective postmortem wiring (`test_retrospective_postmortem_wiring.py`); no structured symptom/root-cause object |
| 36 | Knowledge graph | partial | `alpha.knowledge` — no Project→File→Task→Failure graph store |
| 37 | Continuous research loop | partial | Research engine exists; no question-ledger preventing repeat searches |
| 38 | Long-running tasks | exists | Checkpoints, sessions, stall watchdog, delivery receipts |
| 39 | Human-in-the-loop | partial | `ask_clarification`, approvals, clarification middleware; no per-tool AUTO/ASK/BLOCK policy registry |
| 40 | Observability | exists | §2.17 |
| 41 | Debugger mode | partial | Run inspector with timeline, replay, usage panels |
| 42 | User control center | exists | 35 sections incl. supervisor, scheduled, memory, policy, evolution |
| 43 | Resource management | partial | Token meter, system monitor, budgets; no per-run hard ceilings before calls |
| 44–45 | Autonomous project mgmt / software factory | exists | Enterprise, company_os, kanban, missions, war rooms |
| 46 | Self-testing | exists | Integration-health, startup validation, feature-manifest + no-orphan gates |
| 47 | Update system | exists | §2.19 (opt-in, guarded) |
| 48 | Backup / recovery | **missing** | Inventory: "unverified" — no automated backup/restore drill or recovery-time measurement |

---

## 4. Risk register (confirmed from code only)

Severity per prompt §2. Each entry names its evidence.

### CRITICAL

None confirmed. (The audit found no silent-success path that reports
a failed operation as completed: run verification, delivery receipts,
self-repair refusal, and the digest-projection default all fail
closed. The CRITICAL-class risks below are therefore stated as HIGH.)

### HIGH

| ID | Risk | Evidence |
| --- | --- | --- |
| R-01 | ~~**No per-tool risk/permission governance registry.**~~ **Closed by the first increment (§8):** `alpha/tools/governance.py` implements the registry (risk class, permissions, side effects, reversibility, timeout, bounded retry, verification method, AUTO/ASK/BLOCK), wired into the discovery catalog's risk disclosure. **Now fully closed by the second increment (§9):** `GovernanceGuardrailProvider` enforces the verdict at call time through the existing fail-closed `GuardrailMiddleware`. | `tools/discovery/catalog.py:51-55, 170-181` (pre-fix); `tools/governance.py`, `guardrails/governance.py` (fix) |
| R-02 | **Prompt-injection security suite unverified.** Guardrails are documented but no fixture corpus covers tool output, uploads, web content, MCP events. | `docs/PRODUCTION_READINESS_INVENTORY.md` row "Prompt-injection security suite" |
| R-03 | **Backup/restore unverified.** No automated backup, no restore-into-clean-environment drill, no recovery-time measurement. | `docs/PRODUCTION_READINESS_INVENTORY.md` row "Backup and restore" |
| R-04 | **Process-local persistence planes are not cross-process exactly-once.** Swarm JSON checkpoints, dynamic-workflow durable store, peer-network SQLite, and (with `database.backend: memory`) the side-effect ledger are single-process. A second Gateway worker would double-execute or double-announce effects. | `runtime/AGENTS.md` honesty claims; `swarm` AGENTS.md; `workflow` AGENTS.md |
| R-05 | **Centralized policy decision point partial.** Authz/approvals are distributed; no one policy grant contract at every consequential tool boundary. | Inventory row "Centralized policy decision point" |

### MEDIUM

| ID | Risk | Evidence |
| --- | --- | --- |
| R-06 | Dynamic-workflow default executor is a digest projection; an operator enabling workflows without `bind_domain_executors()` gets graph mechanics, never real work (disclosed via `acceptance_passed=false`, but easy to misread). | `alpha/orchestrator` AGENTS.md |
| R-07 | Parked network-wait sessions have a durable registry but no resume launcher; continuation depends on `SafeRunRecoveryService` picking them up. | `runtime/AGENTS.md` honesty claims |
| R-08 | Runtime supervisor restart policy (sliding-window budget) is not wired into the Windows launcher; in-process component supervision gap remains even though the OS watchdog chain restarts whole processes. | `runtime/AGENTS.md` (`supervisor_in_windows_launcher: absent`) |
| R-09 | Cost budget enforcement partial: token meter and console cost reporting exist, but per-run/user/workspace hard ceilings are not enforced before model/tool calls. | Inventory row "Cost budget enforcement" |
| R-10 | Four overlapping multi-agent systems (subagents, swarm, enterprise, company_os) plus group war rooms — duplicated coordination semantics and maintenance surface. | §2.14 |
| R-11 | Secrets lifecycle partial: redaction exists; no rotation/revocation inventory or secret-scanning regression suite. | Inventory row "Secrets lifecycle" |
| R-12 | Multi-tenant isolation partial: per-owner stores and route checks exist; no CI matrix of cross-owner attempts against every query and artifact path. | Inventory row "Multi-tenant isolation" |
| R-13 | Evidence verification partial: no trusted collectors for tests, artifacts, HTTP checks, reviewer approvals. | Inventory row "Evidence verification" |
| R-14 | Self-repair is observation-only (honest refusal); there is no governed path that applies a verified fix automatically. | `selfrepair/engine.py` contract |

### LOW

| ID | Risk | Evidence |
| --- | --- | --- |
| R-15 | Root-level plan documents (`MULTI_AGENT_PLAN.md`, `SENTINEL_AUTONOMOUS_AGENT_PLAN.md`, `OCTOP_STEAL_PROMPT.md`, …) can drift from implemented reality; several describe aspirational systems. | repo root listing |
| R-16 | Run-event retention, redaction and audit-export policy undefined. | Inventory row "Run event history" |
| R-17 | Frontend `pnpm test` globs `src/lib/*.test.mjs` only; suites elsewhere need their own script/CI step or silently never run. | root `AGENTS.md` |
| R-18 | Fresh clones ship a silently inert self-healing chain until `recovery/register_autostart.ps1 -Force` is run (generated VBS launchers are gitignored). | `recovery/AGENTS.md` |

### Anti-pattern sweep results (so nobody re-runs it blind)

- Bare `except:` in harness: **0** (only docstrings describing
  historical fixes: `workflow/events.py`, `sandbox/tools.py`,
  `rsi/promotion.py`).
- `except Exception: pass` in production code: **0** (one hit is a
  test docstring describing a fixed bug).
- `while True:` loops: ~30, all consumer/retry loops with sleep or
  backoff (event-bus consumer, MCP cache, sandbox lease, config
  self-tuning lock) — by design, not a confirmed risk.
- `subprocess` calls: monitored services use timeouts
  (`system_monitor_service.py` caps probes at 5 s); a small number
  of script-side calls are operator-controlled.
- RAM-only state: run-scoped `RunRecord` retention (300 s join
  window) is the only process-local run state; everything durable
  goes through `RunStore`/checkpointer/event store.

---

## 5. Duplicate / overlapping implementations

| Area | Implementations | Conflict risk |
| --- | --- | --- |
| Multi-agent coordination | `alpha.subagents` (delegation), `alpha.swarm` (DAG swarms), `alpha.enterprise` (C-Suite), `alpha.company_os` (index-only org), `alpha.groups` (war rooms) | Medium — company_os explicitly indexes rather than duplicates; swarm/enterprise both define DAG execution and consensus. A new "coordination" feature must extend one, not add a fifth. |
| Planning | `alpha.planning`, plan-mode TodoList middleware, swarm `TaskDecomposer`, workflow graphs, enterprise mission DAG | Medium — five planners with different persistence; no shared plan vocabulary. |
| Memory | 20+ `alpha.memory` subpackages | Medium — consolidation exists per-layer; no cross-layer contradiction store. |
| Verification | `alpha.verification`, run verification overlay, delivery receipts, swarm consensus acceptance, workflow `GOAL_GATE` | Low — each owns a distinct surface; the shared concept (evidence-backed acceptance) is not yet one contract. |
| Recovery | `SafeRunRecoveryService`, swarm `auto_replan`, workflow replanning, self-repair (refuses), Windows watchdog | Low — each owns a distinct failure class; no unified failure-analysis object (prompt §9 shape) exists. |

---

## 6. Baseline test status

- Offline suite: `cd backend && make test` (1319 test files; live
  tests opt-in via `ALPHA_RUN_LIVE_TESTS=1`).
- Strict gates: `make test-blocking-io` (Blockbuster), `make lint`
  (ruff), `make format` check in CI.
- Wiring gates: `tests/test_feature_manifest_wiring.py` (134/65/43/9
  registry entries pinned to import sites) and
  `tests/test_no_orphan_modules.py` (every module must be reachable).
- Audit-time spot baseline (this host is slow — a full `uv run`
  costs 2–5 minutes here — and restarted several times, so long
  runs were executed individually with logged output):
  - `tests/test_tool_governance.py` — **40 passed** (144 s).
  - `tests/test_feature_manifest_wiring.py` — **all passed**
    (13/13 in the combined gate run).
  - `tests/test_no_orphan_modules.py` — **failed pre-existing**
    on two orphan modules (`alpha.groups.enforcement`,
    `alpha.tools.discovery.code_mode`, both production modules
    whose only importers were tests, committed in dd211d1 and
    1dde8eb without wiring or allowlist entry); **fixed** by
    adding the two `ALLOWED_ORPHANS` entries with verifiable
    deferral reasons (the repo's established pattern) →
    **7 passed** (316 s). Wiring enforcement into the write path
    is a behavior change deferred to its own increment (§8 next
    dependencies).
  - `tests/test_tool_discovery.py`, `tests/test_group_enforcement.py`
    (the suites covering the touched discovery surface and the
    allowlisted module) — **144 passed** (146 s) after the
    catalog change. (First attempt hit a transient venv
    corruption — `transformers`' `importlib.metadata` call
    failed because a killed `uv` process had left the
    environment mid-sync; `uv sync` repaired it, exit 0.)

---

## 7. Implementation roadmap (prioritized, mapped to real gaps)

**Phase 0 — Discovery (this document)**: complete.

**Phase 1 — Reliability foundation** (highest value, lowest risk):

1. Backup/restore drill + operator runbook (closes R-03).
2. Adversarial fixture corpus for prompt injection (closes R-02):
   tool-output, upload, web-content and MCP-event fixtures wired into
   the release-gate measurement.
3. Cross-owner authorization matrix in CI (closes R-12).

**Phase 2 — Verification / tool governance** (first code increment,
see §8): per-tool governance registry — risk class, permissions,
side effects, reversibility, timeout, retry policy, verification
method, and AUTO/ASK/BLOCK policy evaluation (closes R-01, R-05
partially; enables prompt §39).

**Phase 3 — Research engine hardening**: researched-question ledger
(never search the same unanswered question twice), source
cross-checking with conflict states.

**Phase 4 — Planner/executor**: shared plan vocabulary across the
five planners; goal-decomposition tree (objective→milestone→task→
subtask→action) persisted with the run.

**Phase 5 — Multi-agent consolidation**: pick one coordination
surface per use case; deprecate or index the rest (company_os
pattern); unified failure-analysis object (prompt §9 shape).

**Phase 6 — Memory/world model**: cross-layer memory consolidation
with contradiction detection; unified world-state store with
provenance and uncertainty.

**Phase 7 — Autonomous software engineering**: agent-owned worktrees
with commit + PR handoff, never blind merge.

**Phase 8 — RSI governance**: stored benchmark results over time;
improvement proposals with measured signals only (extend the
proposes-only pattern).

**Phase 9 — Autonomous operations**: event-triggered goals, offline
queue UI, long-running-task checkpoints in the run inspector.

**Explicitly out of scope (honesty)**: no sentience/consciousness/
AGI claims; autonomy levels 0–6 are engineering autonomy levels, not
intelligence measures; a completed run is never "verified" without
evidence.

---

## 8. First implementation increment (Phase 2, closes R-01) — IMPLEMENTED

`alpha/tools/governance.py` — the per-tool governance
registry the discovery catalog explicitly said was missing.
**Status: implemented.**

Files changed:

- `backend/packages/harness/alpha/tools/governance.py`
  (new) — `RiskClass` (READ/WRITE/EXECUTE/EXTERNAL/
  DESTRUCTIVE), `Reversibility`, `ConfirmationPolicy`
  (AUTO/ASK/BLOCK), `VerificationMethod`, bounded
  `RetryPolicy`, `ToolGovernance`, `GovernanceRegistry`
  (`from_mapping` config parser, `entry_for` resolution,
  `evaluate` → AUTO/ASK/BLOCK with reasons, `risk_level`
  mapping), `governance_from_tool_metadata`,
  `risk_level_for_tool`.
- `backend/packages/harness/alpha/tools/discovery/
  catalog.py` — `resolve_risk_level` now maps a declared
  `governance_risk_class` onto the catalog's
  low/medium/high/critical scale (governed class takes
  precedence over the legacy `discovery_risk_level`; a
  tool declaring neither keeps the exact `low` default);
  docstring updated — the "no per-tool risk/permission
  registry" note is now historical.
- `backend/packages/harness/alpha/tools/AGENTS.md` —
  governance contract documented.
- `backend/tests/test_tool_governance.py` (new) — 40
  tests: defaults, untrusted-source elevation (self-declared
  governance metadata from MCP/CLIENT is ignored as
  attacker-controlled input), first-party declarations,
  operator-config precedence, config/metadata parsing,
  16 malformed-config fail-closed cases, 6 malformed-
  metadata cases, decision evaluation, defensive escalation
  (an inferred entry above WRITE can never be AUTO), and
  catalog integration.

Trust rules (the security core):

1. Operator entry in `config.yaml -> tool_governance` wins
   over every tool-side declaration.
2. An untrusted source (MCP / client-supplied) never
   classifies itself — elevated default WRITE/ASK/UNKNOWN.
3. A tool that declares nothing keeps the pre-registry
   READ/AUTO/REVERSIBLE default exactly (no behavior change
   for any existing tool).
4. Every malformed declaration raises `GovernanceError` —
   a typo is a startup error, never a silent fallback.

Verification evidence:

- `uv run pytest tests/test_tool_governance.py -q` →
  **40 passed** (144.13 s).
- `uv run ruff check` on the three touched files →
  **All checks passed**.
- `uv run ruff format --check` → **3 files already
  formatted**.
- `uv run pytest tests/test_feature_manifest_wiring.py -q`
  → **all passed** (no registry entry was added — no new
  tools/routers/middlewares/loops — and the new module is
  imported by the already-wired `discovery/catalog.py`).
- `uv run pytest tests/test_no_orphan_modules.py -q` →
  **7 passed** after the pre-existing failure was fixed
  (see §6).
- `uv run pytest tests/test_tool_discovery.py
  tests/test_group_enforcement.py -q` → **144 passed**
  (145.80 s) — regression check for the catalog change
  and the allowlisted enforcement module.
- Design-level non-regression argument: the only behavioral
  surface changed is `resolve_risk_level`, and for every
  tool that declares no `governance_*` metadata (all
  existing tools) its return value is byte-identical to the
  previous implementation; no test pins the old precedence
  (a repo-wide search for `resolve_risk_level` in
  `backend/tests/` finds no direct pin).

Known limitations:

- The registry is disclosure + evaluation, not yet call
  interception: no middleware refuses a tool call based on
  the AUTO/ASK/BLOCK verdict yet. That is the next
  increment (a permission-boundary middleware), and it is
  deliberately separate so this change is additive and
  reversible.
- `config.yaml -> tool_governance` is a plain mapping read
  by `GovernanceRegistry.from_mapping`; `AppConfig` is
  `extra="allow"`, so the section is additive and needs no
  schema migration, but it is not yet surfaced in
  `config.example.yaml` or `docs/CONFIGURATION.md`.

Next dependency: (1) permission-boundary middleware that
consults `GovernanceRegistry.evaluate` before a
higher-risk tool call executes (prompt §20/§39 enforcement
point); (2) `groups/write_watch.py` adopting
`alpha.groups.enforcement.StrictEnforcer` so
`lock_policy: "strict"` actually refuses writes against a
live claim (the deferred wiring named in the allowlist
entry); (3) operator-facing config documentation for
`tool_governance` in `config.example.yaml` /
`docs/CONFIGURATION.md`.

Resolved since: (1) and (3) are implemented in increment 2
(§9); (2) remains the next increment.

---

## 9. Second implementation increment (R-01 enforcement half) — IMPLEMENTED

`alpha/guardrails/governance.py` —
`GovernanceGuardrailProvider`, the call-time enforcement of
the operator's `tool_governance` policy. **Status:
implemented.** It plugs into the existing fail-closed
`GuardrailMiddleware` seam (`guardrails.enabled` +
`guardrails.provider.use:
alpha.guardrails.governance:GovernanceGuardrailProvider`),
so it inherits the proven deny/audit/error-`ToolMessage`/
authorization-outcome behavior instead of forking it.

Authority model (deliberately narrow):

1. The middleware hands providers only the tool *name*,
   never the tool object — a tool cannot widen itself via
   its declared `governance_*` metadata. Enforcement
   authority is operator configuration alone.
2. The top-level `tool_governance` section is injected
   into the provider at assembly via the existing
   `framework`-hint mechanism (extended in
   `tool_error_handling_middleware.py`), so one declaration
   drives both the catalog disclosure and the call-time
   decision; `guardrails.provider.config.spec` wins per
   tool name.
3. `confirmation: block` denies (`governance.blocked`);
   `confirmation: ask` denies (`governance.requires_confirmation`)
   unless the call arrives through server-internal dispatch;
   `confirmation: auto` allows and records the governed
   class in the decision metadata. No operator entry ->
   allowed, `unclassified`.
4. A malformed declaration raises `GovernanceError` at
   agent assembly — fail-closed at startup, never a
   mid-run surprise.

Files changed:

- `backend/packages/harness/alpha/guardrails/governance.py`
  (new) — the provider; `evaluate`/`aevaluate`/
  `release_policy_parameters` (assembly identity declares
  the governed surface).
- `backend/packages/harness/alpha/guardrails/__init__.py` —
  export.
- `backend/packages/harness/alpha/tools/governance.py` —
  `GovernanceRegistry.entries` iterator added.
- `backend/packages/harness/alpha/agents/middlewares/
  tool_error_handling_middleware.py` — assembly hint now
  also hands `app_config.tool_governance` to providers that
  accept it (same pattern as the existing `framework` hint).
- `backend/tests/test_governance_guardrail.py` (new) — 21
  tests: allow/block/ask decisions, internal-dispatch
  approval, auto metadata, middleware deny/allow/async
  paths, authorization-outcome publication, RunJournal
  audit, fail-closed construction, assembly hint injection,
  malformed section fails assembly.
- `backend/docs/GUARDRAILS.md` — "Option 5" section with
  decision semantics and the authority model.
- `config.example.yaml` — "Option 5" with a top-level
  `tool_governance` example.
- `backend/packages/harness/alpha/tools/AGENTS.md` —
  enforcement-half pointer.

Verification evidence:

- `uv run pytest tests/test_governance_guardrail.py -q` →
  **21 passed** (72.69 s).
- `uv run pytest tests/test_governance_guardrail.py
  tests/test_no_orphan_modules.py
  tests/test_feature_manifest_wiring.py -q` → **41 passed**
  (406.54 s).
- `uv run pytest tests/test_guardrail_middleware.py
  tests/test_tool_governance.py
  tests/test_tool_error_handling_middleware.py -q` →
  **128 passed** (106.76 s) — regression check on the
  guardrail middleware, the governance registry, and the
  modified assembly.
- `uv run ruff check` / `ruff format --check` on touched
  files → **clean**.
- Real end-to-end durability task:
  `uv run python scripts/realtime_gateway_check.py` →
  **REALTIME CHECK PASSED** — the Gateway booted as a real
  uvicorn OS process on `127.0.0.1:8071`, answered
  `GET /health` → 200, `GET /health/ready` → 200 and
  `GET /api/ops/integration-health` → 200, parked a session
  through the production repository into real SQLite (26
  tables), was hard-killed (`taskkill /F /T`, no graceful
  shutdown), the parked row survived
  (`('realtime-check-thread', 'waiting', 'network_waiting')`),
  and the restarted Gateway came back healthy.

Known limitations:

- There is still no interactive human-approval channel:
  `confirmation: ask` therefore denies every model-initiated
  call today. That is fail-closed by design; wiring ASK to a
  real confirmation surface (the clarification
  Human-Input-Card path) is the follow-up.
- Enforcement is name-based at call time. A tool reached
  through an alternate spelling (e.g. a shell pipeline) is
  not classified — the sandbox and the command-policy
  provider remain the layers for that.
- The registry's per-tool timeout/retry fields are still
  disclosure; timeout enforcement lives in
  `ToolErrorHandlingMiddleware` (`AppConfig.tool_timeout`).

Next dependency: (1) `groups/write_watch.py` adopting
`alpha.groups.enforcement.StrictEnforcer` so
`lock_policy: "strict"` refuses writes against a live claim —
**implemented in §10**; (2) ASK → interactive confirmation;
(3) the Phase-1 offline corpus (adversarial prompt-injection
fixtures, backup/restore drill, cross-owner authz matrix).

---

## 10. Third implementation increment (StrictEnforcer wired into the write path) — IMPLEMENTED

`alpha.groups.enforcement.StrictEnforcer` existed with a
full unit suite and a policy it read but never enforced —
`CollaborationConfig.lock_policy` accepted `"strict"` and
nothing refused on it, the most fully decorative setting in
the collaboration config. **Status: implemented.**

Wiring: `groups/write_watch.py::enforce_write()` is called
by `ReadBeforeWriteMiddleware` (`_with_coordination` and
its async twin) on `write_file`/`str_replace` **before** the
write handler runs. Under an `advisory` project policy —
the default, and every room nobody configured — the verdict
is `allowed`, so behaviour is byte-identical to before.
Under `lock_policy: "strict"` a live claim held by another
bot refuses the write with an error `ToolMessage` (reason,
holder, claim ids) stamped with `WRITE_BLOCK_KEY` so the
elision path treats it like every other gate block; the
handler never runs. A confirmed-dead holder's claim is
reclaimable, never a refusal; the claim store is swept
before every decision; any store error defers to advisory.

Files changed:

- `backend/packages/harness/alpha/groups/write_watch.py`
  — `enforce_write()`; module docstring updated (the
  "warn, never block" property is now explicitly
  advisory-scoped).
- `backend/packages/harness/alpha/agents/middlewares/
  read_before_write_middleware.py` — sync+async
  coordination paths consult the enforcer before claiming;
  `_strict_blocked_result()` builds the refusal.
- `backend/packages/harness/alpha/groups/AGENTS.md` —
  write-watch and enforcement sections updated.
- `docs/AGENT_LIVE_STATUS_AND_WORK_COORDINATION.md` —
  Phase 3 marked implemented.
- `backend/tests/test_no_orphan_modules.py` — the
  `ALLOWED_ORPHANS` entry for `alpha.groups.enforcement`
  removed; the module is now on the production import graph.
- `backend/tests/test_group_write_watch.py` — 9 new tests.

Verification evidence:

- `uv run pytest tests/test_group_write_watch.py -q` →
  **27 passed** (126 s), including advisory-warn,
  strict-refuse, dead-holder-reclaimable, own-claim,
  no-project, no-binding, and store-error-fail-open cases,
  plus two middleware-level tests through
  `ReadBeforeWriteMiddleware.wrap_tool_call` (strict: the
  handler never runs and the refusal names the live claim;
  advisory: the write proceeds).
- `uv run pytest tests/test_group_enforcement.py
  tests/test_read_before_write_middleware.py -q` →
  **84 passed** (147 s) — regression on the modified
  middleware and the enforcer suite.
- `uv run pytest tests/test_no_orphan_modules.py -q` →
  **7 passed** (562 s) — the allowlist-entry removal is
  validated by the import graph.
- `uv run ruff check` / `ruff format --check` on the four
  touched files → **clean**.
- Real end-to-end durability task:
  `uv run python scripts/realtime_gateway_check.py` →
  **REALTIME CHECK PASSED** — Gateway booted as a real
  uvicorn OS process, served `/health`, `/health/ready` and
  `/api/ops/integration-health`, persisted a parked session
  through the production repository into real SQLite,
  survived a `taskkill /F /T` hard kill (the parked row
  survived on disk), and came back healthy. Note: two
  intermediate runs failed because the venv had been left
  mid-sync by a killed process (repaired with `uv sync`);
  the final run is green with increment 3's code on the
  production import graph.

Known limitations:

- Strict enforcement resolves the policy from the
  *project-linked* collaboration config; a plain group room
  with no project id always stays advisory. That is
  deliberate — a room nobody configured must not start
  refusing writes — but it means `lock_policy` on a
  project-less room is still inert by design.
- The refusal is name/path-based, like every coordination
  layer here; a write that hides the real path (e.g. a
  shell redirect) is not seen by it. The sandbox and the
  command-policy provider remain the layers for that.

Next dependency: (1) ASK → interactive confirmation for
tool governance (the clarification Human-Input-Card path);
(2) the Phase-1 offline corpus (adversarial
prompt-injection fixtures, backup/restore drill,
cross-owner authz matrix).

---

## 11. Fourth implementation increment (procedural / reasoning memory) — IMPLEMENTED

Research finding: the ICLR-2026 "reasoning memory" line
(ReasoningBank) is the one widely cited, harness-level
missing surface in Alpha. The master prompt (§11) requires
procedural memory — successful procedures and workflows —
and no such store existed. **Status: implemented.**

`alpha/reasoning_bank/` is a small, offline, deterministic
procedure store with all RSI-honesty rules baked in:

- Every stored `ReasoningRecord` carries scope, trigger,
  strategy, a measured verdict (`success|partial|failure|
  unknown`), win/loss/unknown counters, an evidence ref,
  and stable fingerprint id. Win rate is always
  `wins / attempts` over measured verdicts — **never a
  self-asserted confidence number**. An unevidenced
  "success" demotes to `unknown` at record time.
- Recall is deterministic, bounded and offline: score by
  scope ×3, tag overlap, and token overlap ×2 on
  (trigger|strategy|scope|tags); wins/tiebreak by win rate,
  recency. Bounded `limit`, bounded-char `render()`. No
  vector store, no model call.
- A failed strategy stays visible (wins=0 ⇒ zero win
  rate ⇒ ranks below winning strategies), and is only
  filtered by an explicit `include_failures=False`.
- `REASON` storage follows the `ClaimStore` discipline:
  threading.Lock, atomic tmp+replace save, corrupt file
  degrades to empty-with-warning, `prune()` caps the count,
  and `get_reasoning_bank()` is a singleton that rebuilds
  on `runtime_home()` moves. `get_reasoning_bank(path)`
  seeds an isolated store for tests.

Production consumer: `tools/builtins/self_improvement_tool.py::
ralph_loop_tool` records each round's measured verdict
(success only when the completion-promise acceptance
verdict fully holds; partial when a round completed
unchecked; failure otherwise — evidence ref is always
`ralph:{tool_call_id}:r{round}`), and round 1 of a later
loop is seeded with the bank's bounded top-1 rendering for
the same promise+task. A bank failure never fails a round.

Files changed:

- `backend/packages/harness/alpha/reasoning_bank/bank.py`
  (new) — `ReasoningBank`, `ReasoningRecord`,
  `get_reasoning_bank()`, store load/save/prune,
  `record`/`reinforce`/`recall`/`render`.
- `backend/packages/harness/alpha/reasoning_bank/__init__.py`
  (new) — exports.
- `backend/packages/harness/alpha/reasoning_bank/AGENTS.md`
  (new) — contract.
- `backend/packages/harness/alpha/tools/builtins/
  self_improvement_tool.py` — `_round_prompt(..., hints=...)`
  (round-1 reasoning-bank seed), and a record call after
  each round's acceptance verdict. A bank failure logs and
  leaves the round result unchanged.
- `backend/tests/test_reasoning_bank.py` (new) — 16 tests:
  evidence-gated success, unknown/unknown-evidence
  handling, upsert aggregation, recall ranking/boundedness,
  failures-stay-visible, bounded render, corrupt store
  degrades, singleton semantics, prune keeps the best.
- `backend/tests/test_self_improvement_tool.py` — autouse
  fixture extended to isolate the bank singleton under
  `ALPHA_HOME` (stops ralph tests from writing the real
  runtime-home store; verified the real file is no longer
  touched).
- `backend/packages/harness/alpha/tools/AGENTS.md` —
  ralph-loop note records the new integration.

Verification evidence:

- `uv run pytest tests/test_reasoning_bank.py -q` →
  **16 passed** (100 s).
- `uv run pytest tests/test_self_improvement_tool.py -q` →
  **20 passed** (80 s); real `backend/.alpha/
  reasoning_bank/records.json` is no longer created by the
  test run.
- `uv run pytest tests/test_no_orphan_modules.py -q` →
  **7 passed** (396 s) — the new package is import-referenced
  by the ralph tool.
- `uv run ruff check` / `ruff format --check` on touched
  files → **clean**.
- Cold import probe: `alpha.reasoning_bank`,
  `alpha.reasoning_bank.bank`, and
  `alpha.tools.builtins.self_improvement_tool` all import
  cleanly in a fresh interpreter.

Known limitations:

- Records are keyed by content fingerprint, so paraphrases
  of the same strategy are treated as **different** records;
  semantic dedupe is out of scope.
- Recall scoring is deterministic keyword overlap, not
  embedding similarity; a vector index can be added behind
  the same `recall()` seam without changing records or the
  wiring contract.
- Seeds and records flow through the ralph loop only; a
  general planner-side consumer (ContextAssembler/Dynamic
  Context) is the follow-up. Future loops with a *non-ralph*
  origin can still read/write the same store directly.

Next dependency: (1) ASK → interactive confirmation for
tool governance; (2) Phase-1 offline adversarial corpus;
(3) planner-side recall projection in DynamicContextMemory —
**implemented in §12**.

---

## 12. Fifth implementation increment (planner-side reasoning recall) — IMPLEMENTED

The fourth increment stored reasoning strategies but only
the ralph loop retrieved them. Following the ReasoningBank
paper (retrieve top-k into the system instruction before
action), the lead agent now projects the same bounded recall
directly before every model call. **Status: implemented.**

`alpha/agents/middlewares/reasoning_bank_context_middleware.py` follows the
exact pattern of `ViewImageMiddleware` "24b" in the middleware
monorepo guide:

- In `wrap_model_call` and `awrap_model_call`, it sweeps any stranded
  marker block out of the request (reserved `__reasoning_bank__` id
  prefix **plus** the `reasoning_bank_reminder` additional_kwarg — so a
  user-authored stand-in is never dropped), then appends at most one
  fresh block: a hidden `HumanMessage` wrapping the top 3 measured
  strategies (≤800 chars) from `ReasoningBank.render(latest_user_message)`,
  stamped `hide_from_ui` + `reasoning_bank_reminder` + MEMORY-provenance
  kwargs. The payload never enters state or the checkpoint — the
  `ModelRequest` is overridden per call and `state` is untouched.
- An empty bank yields the same request object back: zero behavior
  change before the first ralph outcome is recorded.
- Disabled via `reasoning_bank: {enabled: false}` or
  `injection_enabled: false`; also carry the recall verdict through a
  `release_policy_parameters` fingerprint.
- Registered unconditionally in `agents/lead_agent/agent.py::build_middlewares()`
  immediately after `ViewImageMiddleware`; compiled graph assembly
  descriptor tests cover the real `create_agent` path.

Files changed:

- `backend/packages/harness/alpha/agents/middlewares/reasoning_bank_context_middleware.py` (new) — the projection.
- `backend/tests/test_reasoning_bank_context.py` (new) — 6 tests: append +
  marker + provenance + no state mutation; empty bank → identity; stranded
  block swept, not duplicated; non-text content → no query → no injection;
  config opt-out; async twin matches sync.
- `backend/packages/harness/alpha/agents/lead_agent/agent.py` — registration after `ViewImageMiddleware`.
- `backend/packages/harness/alpha/agents/middlewares/AGENTS.md` — item "24b" contract note.
- `backend/packages/harness/alpha/tools/AGENTS.md` — ralph note updated to name the lead-runtime projection.

Verification evidence:

- `uv run pytest tests/test_reasoning_bank_context.py
  tests/test_reasoning_bank.py
  tests/test_self_improvement_tool.py -q` → **42 passed** (70 s).
- `uv run pytest tests/test_agent_assembly_descriptor.py -q` →
  **31 passed** (124 s) — the production graph compiles with the new
  middleware in the chain.
- `uv run pytest tests/test_no_orphan_modules.py -q` → **7 passed** — the module is import-referenced by the lead agent.
- `uv run ruff check`/`format --check` on all touched files → clean.
- Cold import: `ReasoningBankContextMiddleware()` constructs and reports
  `release_policy_parameters() == {"reasoning_bank_recall": True}`.

Known limitations:

- The recall is injected per model call, so a multi-call turn re-pays
  the same bounded block each call (same trade as ViewImage/durable-context
  projection, no state growth). If this grows, a per-run frozen variant
  belongs in `DurableContextMiddleware`.
- Keywords only: no embeddings. The bank's `recall()` seam accepts an
  embedding-ranked implementation later without touching injection.
- Subagents do not receive the middleware (lead-only `build_middlewares`),
  so delegation rounds do not inherit recall hints. SubagentMiddleware reuse
  is the follow-up once ordering pinning is verified.

Next dependency: (1) ASK → interactive confirmation for tool governance;
(2) Phase-1 adversarial offline corpus (injection fixtures, backup/restore
drill, cross-owner authz matrix); (3) subagent-side projection.

---

*This audit is a point-in-time snapshot. Re-run the anti-pattern
sweeps and the baseline suite after every phase; update this document
(or supersede it) rather than letting it drift.*
