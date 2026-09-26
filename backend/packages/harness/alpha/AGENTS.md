### System One indexed computer control (`computer_use/system_one_policy.py`)

The Windows desktop route is optional and disabled by default. When
`system_one.enable_computer_action` is enabled, the policy projects UI
Automation elements to bounded semantic fields and operation-specific indexes;
it never sends coordinates, bounding boxes, selectors, UIA handles, typed text,
keys, or hotkey values to System One. The model returns an index only for
element-targeted operations. The model-facing tool re-observes the
accessibility tree and compares the target's semantic identity before resolving
fresh geometry locally, then reuses the sentinel-guarded dispatcher. Keyboard
operations additionally require exactly one freshly observed focused control,
and `TYPE_TEXT` rechecks focus after the guarded click before typing. A bounded
Laya projection that omits executable elements is rejected before the
operation question; partitioning is reserved for a complete table's target
head. `None`, malformed, low-confidence, truncated, shadow-mode, and stale
decisions dispatch nothing. Tests: `tests/test_system_one_computer.py`.


The update engine is the Phase-2 continuation of the Phase-1 release checker.
It only mutates a local Git source checkout: published GitHub releases (or an
explicitly configured development branch) are resolved to an immutable commit,
the worktree and remote identity are checked, the update is fast-forwarded
behind a backup ref, and success is recorded only after restart/health checks.
The engine is argv-only, rejects dirty/diverged worktrees, snapshots
`config.yaml`/`extensions_config.json` outside Git, publishes a cross-process
maintenance barrier, restores declared dependencies during rollback, and records
honest pre-mutation recovery. `release_check.py` remains the stable public
state/HTTP contract; the Gateway only queues a detached apply and requires a
real interactive admin. Manual confirmation never bypasses `canApply` or other
safety gates. Never add an in-place Docker/Helm/Electron update path here.
Tests: `tests/test_auto_update.py`; operations: `docs/AUTO_UPDATE.md`.

### Request Trace Context (`packages/harness/alpha/trace_context.py`)

Alpha's request-level correlation id — the `X-Trace-Id` header and the `agent_workspace_trace_id` key. Not Langfuse's trace id, not `run_id`, not the short subagent `trace_id` log label.

**The ContextVar is the only source.** Every path that reaches a run binds one first; downstream treats the id as a plain `str`, no `if trace_id:` guards.

Entry points and binders: Gateway HTTP — `TraceMiddleware`; scheduled occurrence — `ScheduledTaskService._attempt_queued_run` → `launch_scheduled_thread_run`; MCP task notification — `launch_mcp_task_notification_run`; IM inbound — `ChannelManager._worker_loop`; embedded / TUI / CLI turn — `AgentWorkspaceClient.stream()`.

Only the first is HTTP; the rest run outside ASGI, so the binding cannot live in middleware alone. Each scopes **one unit of work**, never a poller loop — a leaked binding on a reused worker task would tag later occurrences with the first id. `ensure_trace_context` inherits, keeping layered scheduled bindings and a manual trigger inside a Gateway request on one trace.

**Every other carrier is a derived output, never read back as an input.** `worker._bind_trace_id` stamps the runtime context and `config["metadata"]`; `services.start_run` stamps the run record; a caller-sent `agent_workspace_trace_id` (`body.metadata`, `body.config.context`) is replaced — honouring it would let the persisted run disagree with the header and the logs. `_SERVER_OWNED_RUNTIME_CONTEXT_KEYS` covers the embedded path and also rejects caller-supplied sandbox lease/scope identities, `redact_config_secrets` scrubs the kwargs echo (`runs.kwargs_json`), and `build_run_config` merges metadata onto a copy so the stamp cannot reach `body.config`. Callers pin an id with `X-Trace-Id`.

Accepted divergence: a crash-recovered scheduled launch reuses its run via the idempotency key without restamping — the record keeps the first attempt's id, the retry's logs a fresh one; restamping would rewrite an existing record. Not a bug. Thread metadata omits the key entirely — a thread spans many runs.

**Do not open-code fallback chains.** Two helpers own the resolution order:

- `resolve_trace_id(*carriers)` — first usable carrier, else ambient. For ids travelling as data in `runtime.context`; ContextVars do not survive a bare thread hop.
- `ensure_trace_context(trace_id)` — reuse the surrounding scope, else start a self-contained one. For boundary crossings (`SubagentExecutor._aexecute`, the memory `trace_context_manager` hook) and non-HTTP entry points; no argument mints a scoped id.

`request_trace_context` (HTTP) deliberately does **not** inherit: a crafted header must not fall back to the previous request's id.

`get_current_trace_id()` stays nullable only for the logging filter (pre-entry-point records render as `trace_id=-`); everything else uses `ensure_trace_id()`/`resolve_trace_id()`.

`AgentWorkspaceClient.stream()` binds per `next()` step and around `inner.close()`, never across a `yield`: a sync generator shares the caller's context, so a scope held across yields would leak the id and break on cross-context GC finalization.

`logging.enhance.enabled` gates **log output only** (`trace_id` field presence and format) — not the id, the header, or the run metadata — so `TraceMiddleware` reads no `AppConfig`; `logging` stays restart-required (`STARTUP_ONLY_FIELDS["logging"]`). `X-Trace-Id` is in `CORS_EXPOSED_HEADERS` (not safelisted). Unhandled-exception 500s keep the header — `TraceMiddleware` sends its own plain 500 (CORS-opaque, see its docstring) before re-raising; mid-stream failures propagate unchanged.

Tests: the `tests/test_trace_*` and `tests/test_worker_trace_binding.py` suites, `test_gateway_services.py`, `test_run_metadata_secret_safety.py`, plus the Langfuse suites in `tracing/AGENTS.md`.

### Managed Lark CLI credentials (`integrations/lark_cli.py`)

App registration and direct app switching replace the per-user Lark credential
tree transactionally. Clear the old OAuth data before running `lark-cli config
init`: on Linux that command writes the new app secret into the file-backed
keychain under the data directory, so clearing the directory afterward would
leave `config.json` with a dangling keychain reference. The transaction snapshot
still supplies the previous OAuth data for logout and restores the complete old
tree if any switch step fails.

### Browser Progress Screenshots (`community/browser_automation/`)

Hidden per-action browser progress frames use JPEG at quality 80 to keep their
storage and transfer cost bounded relative to lossless PNG. The explicit
`browser_screenshot` tool remains PNG because it creates a user-requested
artifact. New automatic capture entry points must reuse the shared progress
encoding definition in `tools.py` so the byte encoding and `.jpg` suffix cannot
drift.

### AgentEye live-source adapter (`community/agent_eye/`)

The root `backend` project pins AgentEye to immutable upstream commit
`455404ab9fd2ebba3c4c2e1e07932ed738a0c510` because the advertised `6.5.1`
release is not published on PyPI. Do not replace this with a branch, the
legacy project name, or an unpinned package. Alpha deliberately does **not**
construct or call upstream `AgentSearchLite`: that orchestrator writes a
process-global config file, duplicates/inflates its backend registry, contains
known dead endpoints, and can amplify one research call into thousands of
requests. Its unauthenticated HTTP API and MCP entry points are also excluded.

`provider.py` imports only curated fixed-endpoint functions from AgentEye and
pure `ranking.py` / `extractors.py` helpers. `agent_eye_search` and
`agent_eye_sources` are config-defined `web`-group tools. Every call passes
through the operator allowlist, per-backend failure isolation, a bounded worker
queue, total timeout, result/snippet caps, canonical URL deduplication, and
provenance (`source`, `sources`, `source_count`). Process-wide AgentEye caches,
cache-clear/index tools, arbitrary URL fetch/crawl, and query-independent
feeds are not exposed. Scraped/volunteer-instance providers are fragile and
require `allow_fragile_backends: true`; the default uses stable API/search
families only. "No API key" never means unlimited, reliable, or unrestricted.

When `agent_eye_search.use_for_deep_research` is enabled,
`alpha.research.backends.default_search_fn` tries this adapter and retains
DDGS as a real fallback. `default_fetch_fn` uses `safe_fetch.py`, which
re-validates every redirect with Alpha's public-URL policy, caps redirects and
response bytes, and only then runs AgentEye's pure HTML/JSON-LD extractor.
Deep research returns `no_evidence` when discovery is empty and never invents a
citation or `example.org` fallback; `citations_verified` counts sources with
semantic support verdicts, not merely registered URLs. Report output accepts a
filename only and writes beneath the resolved thread outputs directory.
Tests: `backend/tests/test_agent_eye_integration.py`,
`test_deep_research_engine.py`, and `test_deep_research_tool.py`.

### Embedded Client (`packages/harness/alpha/client.py`)

`AgentWorkspaceClient` provides in-process access without HTTP or a FastAPI dependency. It shares Gateway's `alpha` modules, config files, data directories, and response schemas for compatible consumers.

**Agent Conversation**:
- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — subscribes to LangGraph `stream_mode=["values", "messages", "custom"]` and yields `StreamEvent`:
  - `"values"` — state snapshot (title, messages, artifacts, summary_text); `summary_text` is the current summary or `None` when absent and is forwarded on every snapshot, including unchanged summaries and resets. AI text already delivered via `messages` mode is **not** re-synthesized here to avoid duplicate deliveries; serialized `ToolMessage` entries preserve a non-`None` native `artifact`
  - `"messages-tuple"` — per-chunk update: for AI text this is a **delta** (concat per `id` to rebuild the full message); tool calls and tool results are emitted once each, and tool results preserve a non-`None` native `artifact`
  - `"custom"` — forwarded from `StreamWriter`; Alpha-built-in custom events are dual-emitted through `alpha.utils.custom_events`, so `astream_events(version="v2")` consumers also receive one `on_custom_event` with `name=payload["type"]` and the unchanged payload as `data`
  - `"end"` — stream finished (carries cumulative `usage` counted once per message id)
- **Custom-event invariant** — production Alpha emitters must use `emit_custom_event` / `aemit_custom_event`, not call `StreamWriter` alone. Every built-in payload must carry a non-empty string `type`; typeless payloads remain writer-only and are intentionally absent from `astream_events`. The writer runs first and remains authoritative for Gateway, Web UI, and embedded-client compatibility; callback dispatch is best-effort and must not break that path. Async graph hooks must await the async helper rather than invoking synchronous dispatch on a running event loop.
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`
- Cache graphs by effective storage `user_id` in every auth mode because prompts and middleware bind user SOUL, skills, and storage. `stream()` must materialize it before worker or isolated-loop boundaries.
- Supports `checkpointer` parameter for state persistence across turns
- `reset_agent()` forces agent recreation (e.g. after memory or skill changes)
- See [docs/STREAMING.md](../../../docs/STREAMING.md) for the full design: why Gateway and AgentWorkspaceClient are parallel paths, LangGraph's `stream_mode` semantics, the per-id dedup invariants, and regression testing strategy

**Gateway Equivalent Methods** (replaces Gateway API):

| Category | Methods | Return format |
|----------|---------|---------------|
| Models | `list_models()`, `get_model(name)` | `{"models": [...]}`, `{name, display_name, ...}` |
| MCP | `get_mcp_config()`, `update_mcp_config(servers)` | `{"mcp_servers": {...}}` |
| Skills | `list_skills()`, `get_skill(name)`, `update_skill(name, enabled)`, `install_skill(path)` | `{"skills": [...]}` |
| Goals | `get_goal(thread_id)`, `set_goal(thread_id, objective, max_continuations=8)`, `clear_goal(thread_id)` | `{"goal": {...}}` or `{"goal": None}` |
| Memory | `get_memory()`, `reload_memory()`, `get_memory_config()`, `get_memory_status()` | dict |
| Uploads | `upload_files(thread_id, files)`, `list_uploads(thread_id)`, `delete_upload(thread_id, filename)` | `{"success": true, "files": [...]}`, `{"files": [...], "count": N}` |
| Artifacts | `get_artifact(thread_id, path)` → `(bytes, mime_type)` | tuple |

**Gateway differences**: Upload takes local `Path`, not `UploadFile`, rejects directories before copying, and reuses one conversion worker inside an active event loop. Artifacts return `(bytes, mime_type)`, not HTTP Response. Gateway alone deletes `.agent-workspace/threads/{thread_id}` after LangGraph thread deletion; the client has no equivalent. `update_mcp_config()` and `update_skill()` invalidate the cached agent.

**Tests**: `tests/test_client.py` is offline, including `TestGatewayConformance`.
`tests/test_client_live.py` requires root `config.yaml`, valid API credentials,
and opt-in via `make test-live` or `AGENT_WORKSPACE_RUN_LIVE_TESTS=1`. It calls real
APIs (possible costs) and may create local sandboxes, artifacts, and files.
Marked `live`, it is excluded from `make test` and skipped in default CI.

**Gateway Conformance Tests** (`TestGatewayConformance`): Parse every dict-returning client method's output through its Gateway Pydantic model so missing required fields raise `ValidationError` in CI. Covers: `ModelsListResponse`, `ModelResponse`, `SkillsListResponse`, `SkillResponse`, `SkillInstallResponse`, `McpConfigResponse`, `UploadResponse`, `MemoryConfigResponse`, `MemoryStatusResponse`.

### AIO Sandbox Network Policy

Restricted AIO keeps sandboxes internal; a per-sandbox, ICC-disabled sidecar
handles egress and its token-authenticated API relay. Parse headers strictly;
reject policy-denied names before DNS and try all validated answers. Claim the
oldest unsurfaced denial; subagent/non-interactive runs drain and deny. Approvals
never replay tools; policy labels fence reuse. CONNECT/SNI cannot inspect
encrypted authority. Discovery and enumeration are read-only, including on a
policy or network-mode mismatch; only the provider may replace it after the
orphan grace, local teardown reservation, and cross-instance teardown lease.
Destroy the sandbox, sidecar, and both networks together.

### E2B Mount Uploads

E2B uploads host mounts during sandbox creation using binary file objects.
Per-mount limits: 100 MiB/file, 512 MiB total, 2,000 files. The full creation
pass shares a 512 MiB / 2,000-file budget across skill projections and mounts.

The pass has a cooperative deadline controlled by
``mount_upload_deadline_seconds`` (default: 120 seconds). The provider checks it before
each mount, during directory preflight, and before each SDK write. The deadline
does not interrupt active filesystem or E2B SDK calls.

The provider checks mount limits before upload. It rechecks each opened file descriptor against its preflight size before SDK upload.

For policy-scoped turns, clearing the four managed remote skill categories and
uploading their prepared projection is one per-user/thread/skills-root critical
section, shared with acquire and release. The provider snapshots that canonical
root at startup and carries it through warm-pool identity and E2B metadata; a VM
from another root is never adopted. A second policy sync cannot reset the remote
tree until the first upload pass has completed.

An invalid mount does not block later mounts.

Each successful upload logs its source, destination, file count, byte count, and elapsed time.

A stopped pass logs its limit reason and elapsed time. It reports attempted and completed upload totals separately.

After creation, ``E2BSandbox.mount_upload_result`` holds a ``MountUploadResult``.
``result.truncated`` is true only for resource-limit stops (deadline, file count,
bytes), not logged mount failures (missing paths, SDK errors). A provider-level
map preserves creation results within the Gateway process; ``None`` on a
reclaimed sandbox means unavailable.

### Workspace Snapshot Cancellation (`workspace_changes/recorder.py`)

After `_prepare_capture()` hands off roots, cancellation must drain text scans
(`include_text=True`) before removing the cache the worker may still access.
Metadata scans (`include_text=False`) own no cache: cancel promptly, let the worker
continue, and consume/log its outcome in a completion callback. Prepare-stage
cancellation retains its handoff/reclaim path. Regressions in
`tests/blocking_io/test_workspace_changes_cancellation.py` must cover prompt
metadata cancellation and text-cache drain/cleanup.

### Agent Presets (`config/agent_preset_config.py`)

Named per-session toolset bundles (DeepSeek-Harness-style preset modes),
requested per run via the `agent_preset` configurable/context key without a
restart. `standard` is the identity preset (today's behavior); `minimal` is
the cheap preset (no MCP catalog, no subagents, no `ask_clarification`).
Operators add presets via `AppConfig.agent_presets` (see `config.example.yaml`);
unknown names warn and fall back to `standard` so a typo can never break run
admission. A preset may only narrow: `tool_groups` override the custom agent's
groups, `include_mcp=False` drops the MCP catalog, `subagent_enabled=False`
forces delegation off, `disabled_tools` extends the non-interactive filter, and
`allow_update_agent=False` withholds `update_agent`. The resolved preset is
recorded in run metadata (`agent_preset`) and the assembly descriptor
(`effective_policies["agent_preset"]`). The bootstrap agent keeps its own fixed
minimal graph and ignores presets. Tests: `tests/test_agent_presets.py`.

### Invariant Registry (`diagnostics/invariants.py`)

Fail-loud runtime self-checks (DeepSeek-Harness-style `ctx.invariants`):
`InvariantRegistry` runs named `InvariantCheck`s and raises `InvariantError`
(naming the breaching check) instead of limping on, with allow/blocklist
substring filters. The module is stdlib-only so factories can import it without
cycles. `_complete_assembly` in `agents/lead_agent/agent.py` gates every lead
assembly via `verify_agent_assembly`: duplicate model-visible tool names and a
non-terminal `ClarificationMiddleware` (IsolatedMiddleware-aware) fail the run
at build time. Only check owned assembly relations, never remote state.
Tests: `tests/test_invariants.py`.

### Pruner Tiers (`config/tool_output_config.py::prune_tiers`)

Named per-tool-class externalization tiers (DeepSeek-Harness-style pruner
tiers, e.g. `{'bash': 65536, 'web_fetch': 16384}`). Per-tool resolution
precedence: `tool_overrides` > `prune_tiers` > `externalize_min_chars`; `0`
disables externalization at the matching layer (fallback truncation may still
apply) and negative values are rejected loudly. Both `_budget_content` and the
`_effective_trigger` pre-scan resolve through `_resolve_externalize_threshold`
so the fast path never false-negatives; tiers ride `release_policy_parameters`
into the assembly identity automatically. Tests:
`tests/test_tool_output_prune_tiers.py`.

### Autonomous AI Software Enterprise (`packages/harness/alpha/enterprise/`)

Enterprise-level autonomous operations platform:
1. **Dynamic Enterprise Hierarchy & C-Suite Swarm**:
   - 4 leadership roles: Executive Director (CEO), Lead Architect (CTO), Product & Market Strategist (CPO), and Quality & Security Director (CISO).
   - 5 core departments: Engineering, Architecture, Security, Performance, and Documentation with leader-worker reporting trees and capability contracts (`CapabilityContract`).
2. **Mission-to-Sprint Pipeline & Dynamic DAG Execution**:
   - Strategic missions decompose into Epics, Technical Specs with Definition of Done (DoD), and dynamic DAG sprints with topological dependency layers.
3. **Cross-Department Blackboard & RFC Protocol**:
   - Formal RFC proposals, multi-agent reviews, and epistemic debate threads with epistemic confidence weighting. Consensus gating enforces quorum and a >= 0.75 score before dynamic execution.
4. **Department Token Treasury & Fiscal Governance**:
   - Allocations, rolling burn rate monitoring (TPM), ROI velocity tracking, and automated circuit breakers that throttle rogue or exhausted departments.
5. **Quality Council Quorum & Cryptographic Multi-Sig Releases**:
   - 3 required cryptographic attestations (`CTO_ARCH`, `SWE_BENCHMARK`, `CISO_ASTRA`) verifying holdout benchmark pass rate >= 90% and AST boundary isolation before zero-downtime hot-swap promotion.
6. **Continuous Discovery, Latency Profiling & AST Boundary Scans**:
   - Integrated into cyclic heartbeats with Keel-style auto-recovery from stagnation.
7. **Gateway REST API & Next.js War Room UI**:
   - Mounted at `/api/enterprise/*` and `/api/gateway/enterprise/*`. Visualized in War Room tab.
Tests: `tests/test_enterprise_autonomous_software_company.py`.

## Swarm v2 runtime contract

`alpha.swarm` is the lifecycle owner for autonomous swarm plans. Plans are explicit
DAGs with atomic JSON checkpoints, ordered JSONL audit events, bounded blackboard
messages, lease-fenced task attempts, retry/backoff state, measured token/tool
budgets, deterministic reflection, bounded consecutive-failure circuit breaking,
and optional evidence-backed consensus. A late worker result is accepted only when its
current lease still matches; cancellation, budget exhaustion, dependency failure, and
pause/resume are separate states. `auto_replan` is a bounded deterministic repair-task
expansion that redirects blocked dependents; it does not silently convert a failed task
into success. The synchronous model tool may use the runner's daemon-thread fallback when
no event loop is available.

Gateway swarm reads and mutations are owner-scoped (`SwarmPlan.owner_id`); the
`swarm` tool derives the owner from the request context and cannot self-assert a
verified blackboard message. Model-visible blackboard content is bounded data in
the human input channel, never an elevated system instruction. Worktree paths are
validated as relative metadata and the worker always uses the server project root.

The current persistence adapter is deliberately local and process-scoped: it is
atomic and restart-recoverable for a single Gateway, but it is not a substitute
for a shared SQL lease repository in a multi-worker deployment. Do not describe
local JSON checkpoints as cross-process exactly-once execution. The v2 API/tool
surfaces expose metrics, task leases, messages, leader election, consensus, and
acceptance-verdict fields so operators can distinguish execution success from
acceptance success. Regression coverage lives in
`backend/tests/test_swarm_engine.py`, `test_swarm_advanced_features.py`,
`test_swarm_worker_execution.py`, and `test_swarm_v2_runtime.py`.

## Dynamic workflow plane

`alpha.workflow.runtime.DynamicWorkflowEngine` and
`alpha.orchestrator.loop.ExecutionKernel` own typed workflow graphs, waves,
conditional routing, bounded loops, retries, budgets, approval waits, patch OCC,
replanning, replay, and compensation. The opt-in
`alpha.orchestrator.dynamic_service.DynamicWorkflowService` composes the existing
intent, capability/resource, bot, DWE, and event seams; it does not replace
`RunManager`, bot/group lifecycle ownership, or the scheduler. Hosts must invoke
its synchronous child execution through their own worker boundary and must not
assume that cancelling an await cancels an already-running node.

Gateway routes are `POST /api/workflows/dynamic/perceive`,
`POST /api/workflows/dynamic/execute`, the existing `/api/workflows/turns`
compatibility seam (`dynamic=true` opts into the full loop), and
`POST /api/bots/{name}/workflow`. Definitions/runs are server-owner-scoped;
request context cannot self-assert ownership. Registry discovery is a bounded
read-only projection, not proof that a provider is connected. Missing executors,
skills, MCP connections, compensation callbacks, or verification evidence fail
or disclose honestly.

The default `alpha.local.digest` executor is explicitly a
`local_digest_projection`: it hashes inputs to exercise graph mechanics and is
never reported as domain-task acceptance. A real model/tool/MCP/sandbox/bot
executor must be bound for domain work. Recurring prompts disclose the missing
scheduler handoff rather than creating a second cron owner.

Workflow events are appended to the durable JSONL sink before listeners run;
the Gateway sink is fail-closed, redacts event payloads, validates paths/schema,
and exposes durability, projection, hydration, replay, and append-only plan
history. The local adapter is atomic and restart-recoverable for one Gateway
process, not a shared multi-worker lease/exactly-once repository. Do not claim
true concurrent wave parallelism or cross-process exactly-once execution until
those coordination boundaries are implemented. Full operations and API examples
are in [`docs/DYNAMIC_WORKFLOWS.md`](docs/DYNAMIC_WORKFLOWS.md); regression
coverage is in `backend/tests/test_dynamic_workflow_service.py`,
`test_dynamic_workflow_router.py`, `test_dynamic_workflow_engine.py`,
`test_workflow_dag_edges.py`, `test_workflow_durability_router.py`, and
`test_bot_dynamic_workflow.py`.

## Guarded source auto-update contract

Local source checkouts may opt into the Phase-2 update engine in
`backend/packages/harness/alpha/evolution/update_engine.py`. The committed
`config/update-policy.json` is a disabled, credential-free template; real
unattended deployments should keep a mutable operator policy outside the clean
checkout and select it with `ALPHA_UPDATE_POLICY_PATH`. `main` branch tracking
is an explicit operator choice, not the production default. The engine is a
separate trust boundary: it requires a clean worktree, manifest-matching
GitHub remote, allowed branch, fast-forward ancestry, stable target ref,
optional signature verification, backup ref, and post-restart health checks. It
uses argv-only subprocesses and never accepts a client-supplied URL/ref.
Gateway apply routes only queue a detached transaction and are admin-only; PATs
and auth-disabled/internal identities never receive admin capability. A
runtime-home maintenance barrier closes new run admission before mutation, and
manual confirmation never bypasses `canApply` or other safety gates.
State/history/lock files live under `runtime_home()` and must never contain
GitHub tokens or secrets. Docker images, Helm releases, and the Electron
installer remain orchestrator-owned and are not updated in place.

The `self_update` loop is registered in `AutonomySupervisor`; an enabled
policy causes the Gateway to register it automatically, while an explicit
`autonomy.loops.self_update` block can override/disable it. Windows autostart
may register `Alpha_Update`, but the policy is the kill switch. Commands:
`make update-status`, `make update-check`, `make update-apply`, and
`make update-recover`. Full operations and recovery guidance live in
`docs/AUTO_UPDATE.md`; tests live in `backend/tests/test_auto_update.py`.

## Guarded source auto-update

The Phase-2 updater lives in `packages/harness/alpha/evolution/update_engine.py`
and is consumed by the `self_update` autonomy loop plus the operator CLI. It is
a source-checkout transaction, not an in-place Docker/Helm/Electron updater.
The default `config/update-policy.json` is a disabled template; unattended
operators should keep the mutable policy outside the checkout and select it
with `ALPHA_UPDATE_POLICY_PATH`. Application requires both policy flags plus
the startup-scoped `autonomy.loops.self_update.enabled` setting. The engine
refuses dirty or non-fast-forward worktrees, validates the manifest-matching
GitHub remote, creates a Git backup ref, keeps config snapshots under runtime
home, publishes a cross-process maintenance barrier, invokes only argv-based
hooks, restores declared dependencies on rollback, and marks success only after
local health checks. Gateway apply/recover routes are admin-only and only queue
detached work; auth-disabled/internal/PAT identities cannot mutate source.
Tests: `tests/test_auto_update.py`; operations: `docs/AUTO_UPDATE.md`.

### Swarm v2 runtime

The `alpha.swarm` package owns swarm lifecycle and execution. `SwarmCoordinator`
serializes short state transitions, writes atomic plan snapshots and ordered
JSONL events, and restores interrupted plans as paused with fresh-lease
requirements. `SwarmScheduler` validates DAGs before mutation, assigns lease
IDs, renews live attempts, applies retry backoff, and fences stale results.
`AsyncSwarmRunner` records measured usage, publishes bounded task-result messages,
enforces the consecutive-failure circuit, handles pause/resume, and reports
`budget_exhausted`/`stalled` rather than fabricating completion. When
`auto_replan` is enabled, a terminal failure can receive one deterministic repair
task; blocked dependents are redirected to that task, while the original failure
remains auditable. `SwarmAggregator` keeps execution status separate from
acceptance criteria and optional evidence-backed consensus; duplicate voters and
unverified acceptance evidence cannot produce a clean approval.

The synchronous `swarm` tool can start the same runner through a daemon-thread
event-loop fallback when it is invoked outside FastAPI's loop; Gateway requests
remain on the Gateway loop. `communication.py`, `consensus.py`, and `reflection.py` are harness-layer modules;
they do not import `app.*`. Gateway routes are owner-scoped and the model-visible
`swarm` tool derives its owner from runtime context. Blackboard content is
untrusted data and is placed in the worker input channel, never appended to the
system prompt. The local JSON/JSONL store is atomic and restart-recoverable for a
single process only; a multi-worker deployment still requires a shared SQL lease
repository before claiming cross-process exactly-once execution. Tests:
`tests/test_swarm_v2_runtime.py` plus the existing swarm engine/worker suites.

### Dynamic workflow plane

`alpha.workflow.runtime.DynamicWorkflowEngine` remains the graph runtime;
`alpha.orchestrator.loop.ExecutionKernel` adds per-run claims, executor
dispatch, mode journaling, handoff contracts, and fail-closed policy. The
opt-in `alpha.orchestrator.dynamic_service.DynamicWorkflowService` composes
perception, registry discovery, decomposition, resource assembly, graph
compilation, and execution without replacing `RunManager` or creating a second
parent lifecycle. The service is synchronous; async hosts must use an explicit
worker boundary and must not claim that cancelling an await stopped a running
node.

Gateway workflow routes are owner-scoped for real HTTP requests. Dynamic
perception is preview-only; dynamic execution, the `dynamic=true` turn seam,
and the bot workflow route share the same executor and evidence rules. A
`DynamicWorkflowBridge` that receives the Gateway `ExecutionKernel` starts and
dispatches every wave through that kernel's per-run claim (and uses the same
claim for runtime replan patches); an explicitly unbound bridge disables
registry fallback so a live digest executor cannot masquerade as a bound host
runner. The default digest executor is explicitly a
`local_digest_projection` and must keep `acceptance_passed=false`; real
model/tool/MCP/sandbox/bot executors are host bindings. Recurring prompts
disclose the missing scheduler handoff rather than creating a competing
automation loop.

The Gateway event dispatcher writes through a redacting, schema-checked,
fail-closed durable JSONL sink. `workflows.py` exposes bounded registry,
durability, event, projection, hydration, replay, plan-history, approval,
replan, compensation, cancellation, and ownership surfaces. Local persistence is
atomic/restart-recoverable for one process only; it is not a shared lease store
or a promise of multi-worker exactly-once execution. Tests:
`tests/test_dynamic_workflow_service.py`, `test_dynamic_workflow_router.py`,
`test_dynamic_workflow_engine.py`, `test_workflow_dag_edges.py`,
`test_workflow_durability_router.py`, and `test_bot_dynamic_workflow.py`.
Operations: [`docs/DYNAMIC_WORKFLOWS.md`](../../docs/DYNAMIC_WORKFLOWS.md).

## Peer network boundary

`alpha.peer_network` is the single harness owner for cross-installation Alpha
identity, discovery, pairing, transport, SQLite conversations, delivery
receipts, and topology semantics. `PeerNetworkService` is installation-scoped;
do not accept a model/client owner assertion. Discovery cards are untrusted and
never grant credentials. HTTP/WebSocket endpoints are validated before use,
messages are bounded/idempotent, and public ingress is authenticated by the
pairing token in the Gateway adapter. The optional GitHub adapter publishes
public Agent Cards only; it is not a mailbox. libp2p is an external future seam
and must not be reported as active without a real implementation. See
`docs/ALPHA_PEER_NETWORK.md` and `backend/tests/test_peer_network.py`.
