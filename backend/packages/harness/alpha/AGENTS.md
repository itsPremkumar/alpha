### System One indexed computer control (`computer_use/system_one_policy.py`)

The Windows desktop route is optional and disabled by default. When
`system_one.enable_computer_action` is enabled, the policy projects UI Automation
elements to bounded semantic fields and operation-specific indexes; it never sends
coordinates, bounding boxes, selectors, UIA handles, typed text, keys, or hotkey
values to System One. The model returns an index only for element-targeted
operations. The model-facing tool re-observes the accessibility tree and compares
the target's semantic identity before resolving fresh geometry locally, then
reuses the sentinel-guarded dispatcher. Keyboard operations additionally require
exactly one freshly observed focused control, and `TYPE_TEXT` rechecks focus after
the guarded click before typing. A bounded Laya projection that omits executable
elements is rejected before the operation question; partitioning is reserved for a
complete table's target head. `None`, malformed, low-confidence, truncated,
shadow-mode, and stale decisions dispatch nothing. Tests:
`tests/test_system_one_computer.py`.

### Request Trace Context (`packages/harness/alpha/trace_context.py`)

`X-Trace-Id` / `agent_workspace_trace_id` is the request-level correlation id, and
the `ContextVar` is its only source: every path that reaches a run binds one first
and downstream treats the id as a plain `str` with no `if trace_id:` guards. Bind
one unit of work, never a poller loop. Every other carrier is a derived output and
is never read back as an input — a caller-sent id is replaced so the persisted run
cannot disagree with the header and the logs. Use `resolve_trace_id(*carriers)`
and `ensure_trace_context(trace_id)`; never open-code a fallback chain, and note
that `request_trace_context` (HTTP) deliberately does not inherit.
`logging.enhance.enabled` gates log output only, not the id, the header, or run
metadata. The full contract — entry-point binders, the accepted
crash-recovered-launch divergence, the client generator's per-step binding, the
CORS/`STARTUP_ONLY_FIELDS` rules, and the regression suites — is in
[backend/docs/STREAMING.md](../../../backend/docs/STREAMING.md#request-trace-context-packagesharnessalphabetrace_contextpy).

### Managed Lark CLI credentials (`integrations/lark_cli.py`)

App registration and direct app switching replace the per-user Lark credential tree
transactionally. Clear the old OAuth data *before* running `lark-cli config init`:
on Linux that command writes the new app secret into the file-backed keychain under
the data directory, so clearing the directory afterward would leave `config.json`
with a dangling keychain reference. The transaction snapshot still supplies the
previous OAuth data for logout and restores the complete old tree if any switch step
fails.

### Browser Progress Screenshots (`community/browser_automation/`)

Hidden per-action browser progress frames use JPEG at quality 80 to keep their
storage and transfer cost bounded relative to lossless PNG. The explicit
`browser_screenshot` tool remains PNG because it creates a user-requested artifact.
New automatic capture entry points must reuse the shared progress encoding
definition in `tools.py` so the byte encoding and `.jpg` suffix cannot drift.

### AgentEye live-source adapter (`community/agent_eye/`)

The harness pins AgentEye to an immutable upstream commit (not a branch, legacy
project name, or unpinned package) and deliberately does **not** construct or call
upstream `AgentSearchLite`; its unauthenticated HTTP API and MCP entry points are
excluded. The curated adapter surface, operator allowlist, `allow_fragile_backends`
policy, bounded worker queue, dedupe, provenance, `no_evidence` honesty,
`citations_verified` semantics, and the `use_for_deep_research` DDGS fallback are
owned by
[backend/docs/AGENT_EYE_RESEARCH.md](../../../backend/docs/AGENT_EYE_RESEARCH.md)
and [docs/DEEP_RESEARCH.md](../../../docs/DEEP_RESEARCH.md).

### Embedded Client (`packages/harness/alpha/client.py`)

`AgentWorkspaceClient` provides in-process access without HTTP or a FastAPI
dependency. It shares Gateway's `alpha` modules, config files, data directories, and
response schemas for compatible consumers. The method-to-Gateway-endpoint parity
table, the per-mode delivery contract, and the full streaming design (why Gateway
and the client are parallel paths, LangGraph `stream_mode` semantics, per-id dedup
invariants, trace binding) are in
[backend/docs/STREAMING.md](../../../backend/docs/STREAMING.md).

- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — yields `StreamEvent` over `stream_mode=["values", "messages", "custom"]`; `values` never re-synthesizes already-delivered AI text, `messages-tuple` AI text is a **delta** (concat per `id`), tool calls/results are emitted once each, `custom` is dual-emitted through `alpha.utils.custom_events`, and `end` carries cumulative `usage` counted once per message id.
- **Custom-event invariant** — production Alpha emitters must use `emit_custom_event` / `aemit_custom_event`, not call `StreamWriter` alone. Every built-in payload must carry a non-empty string `type`; typeless payloads remain writer-only and are intentionally absent from `astream_events`. The writer runs first and remains authoritative for Gateway, Web UI, and embedded-client compatibility; callback dispatch is best-effort and must not break that path. Async graph hooks must await the async helper rather than invoking synchronous dispatch on a running event loop.
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`; supports `checkpointer` for cross-turn state; `reset_agent()` forces agent recreation (e.g. after memory or skill changes).
- Cache graphs by effective storage `user_id` in every auth mode because prompts and middleware bind user SOUL, skills, and storage. `stream()` must materialize it before worker or isolated-loop boundaries.

**Tests**: `tests/test_client.py` is offline, including `TestGatewayConformance`,
which parses every dict-returning client method's output through its Gateway
Pydantic model (so a missing required field raises `ValidationError` in CI) across
`ModelsListResponse`, `ModelResponse`, `SkillsListResponse`, `SkillResponse`,
`SkillInstallResponse`, `McpConfigResponse`, `UploadResponse`,
`MemoryConfigResponse`, and `MemoryStatusResponse`. `tests/test_client_live.py`
requires root `config.yaml`, valid API credentials, and opt-in via `make test-live`
or `AGENT_WORKSPACE_RUN_LIVE_TESTS=1`; it calls real APIs (possible costs) and may
create local sandboxes, artifacts, and files. Marked `live`, it is excluded from
`make test` and skipped in default CI.

### Agent Presets (`config/agent_preset_config.py`)

Named per-session toolset bundles requested per run via the `agent_preset`
configurable/context key without a restart. `standard` is the identity preset
(today's behavior); `minimal` is the cheap preset (no MCP catalog, no subagents, no
`ask_clarification`). Operators add presets via `AppConfig.agent_presets` (see
`config.example.yaml`); unknown names warn and fall back to `standard` so a typo can
never break run admission. A preset may only narrow: `tool_groups` override the
custom agent's groups, `include_mcp=False` drops the MCP catalog,
`subagent_enabled=False` forces delegation off, `disabled_tools` extends the
non-interactive filter, and `allow_update_agent=False` withholds `update_agent`. The
resolved preset is recorded in run metadata (`agent_preset`) and the assembly
descriptor (`effective_policies["agent_preset"]`). The bootstrap agent keeps its own
fixed minimal graph and ignores presets. Tests: `tests/test_agent_presets.py`.

### Invariant Registry (`diagnostics/invariants.py`)

Fail-loud runtime self-checks: `InvariantRegistry` runs named `InvariantCheck`s and
raises `InvariantError` (naming the breaching check) instead of limping on, with
allow/blocklist substring filters. The module is stdlib-only so factories can import
it without cycles. `_complete_assembly` in `agents/lead_agent/agent.py` gates every
lead assembly via `verify_agent_assembly`: duplicate model-visible tool names and a
non-terminal `ClarificationMiddleware` (IsolatedMiddleware-aware) fail the run at
build time. Only check owned assembly relations, never remote state. Tests:
`tests/test_invariants.py`.

### Pruner Tiers (`config/tool_output_config.py::prune_tiers`)

Named per-tool-class externalization tiers, e.g. `{'bash': 65536, 'web_fetch':
16384}`. Per-tool resolution precedence is `tool_overrides` > `prune_tiers` >
`externalize_min_chars`; `0` disables externalization at the matching layer
(fallback truncation may still apply) and negative values are rejected loudly. Both
`_budget_content` and the `_effective_trigger` pre-scan resolve through
`_resolve_externalize_threshold` so the fast path never false-negatives; tiers ride
`release_policy_parameters` into the assembly identity automatically. Tests:
`tests/test_tool_output_prune_tiers.py`.

### Autonomous AI Software Enterprise (`packages/harness/alpha/enterprise/`)

The enterprise-level autonomous operations platform — C-Suite hierarchy and
department capability contracts, mission-to-sprint DAG execution, RFC/blackboard
consensus gating, token treasury and circuit breakers, multi-sig release
attestations, and the War Room surfaces — is described in
[docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md#6-autonomous-ai-software-enterprise-platform).
Tests: `tests/test_enterprise_autonomous_software_company.py`.

## Swarm v2 runtime contract

`alpha.swarm` is the lifecycle owner for autonomous swarm plans. Plans are explicit
DAGs with atomic JSON checkpoints, ordered JSONL audit events, bounded blackboard
messages, lease-fenced task attempts, retry/backoff state, measured token/tool
budgets, deterministic reflection, bounded consecutive-failure circuit breaking, and
optional evidence-backed consensus. A late worker result is accepted only when its
current lease still matches; cancellation, budget exhaustion, dependency failure, and
pause/resume are separate states. `auto_replan` is a bounded deterministic repair-task
expansion that redirects blocked dependents; it does not silently convert a failed
task into success. The synchronous model tool may use the runner's daemon-thread
fallback when no event loop is available.

`SwarmCoordinator` serializes short state transitions and restores interrupted plans
as paused with fresh-lease requirements; `SwarmScheduler` validates DAGs before
mutation, assigns lease ids, renews live attempts, applies retry backoff, and fences
stale results; `AsyncSwarmRunner` records measured usage, publishes bounded
task-result messages, enforces the consecutive-failure circuit, and reports
`budget_exhausted`/`stalled` rather than fabricating completion. `SwarmAggregator`
keeps execution status separate from acceptance criteria and consensus: duplicate
voters and unverified acceptance evidence cannot produce a clean approval.

Gateway swarm reads and mutations are owner-scoped (`SwarmPlan.owner_id`); the
`swarm` tool derives the owner from the request context and cannot self-assert a
verified blackboard message. Model-visible blackboard content is bounded data in the
human input channel, never an elevated system instruction. Worktree paths are
validated as relative metadata and the worker always uses the server project root.
`communication.py`, `consensus.py`, and `reflection.py` are harness-layer modules and
do not import `app.*`.

The current persistence adapter is deliberately local and process-scoped: it is
atomic and restart-recoverable for a single Gateway, but it is not a substitute for
a shared SQL lease repository in a multi-worker deployment. Do not describe local
JSON checkpoints as cross-process exactly-once execution. The v2 API/tool surfaces
expose metrics, task leases, messages, leader election, consensus, and
acceptance-verdict fields so operators can distinguish execution success from
acceptance success. Regression coverage lives in `backend/tests/test_swarm_engine.py`,
`test_swarm_advanced_features.py`, `test_swarm_worker_execution.py`, and
`test_swarm_v2_runtime.py`.

## Dynamic workflow plane

`alpha.workflow.runtime.DynamicWorkflowEngine` and
`alpha.orchestrator.loop.ExecutionKernel` own typed workflow graphs, waves,
conditional routing, bounded loops, retries, budgets, approval waits, patch OCC,
replanning, replay, and compensation. The opt-in
`alpha.orchestrator.dynamic_service.DynamicWorkflowService` composes the existing
intent, capability/resource, bot, DWE, and event seams; it does not replace
`RunManager`, bot/group lifecycle ownership, or the scheduler. Hosts must invoke
its synchronous child execution through their own worker boundary and must not
assume that cancelling an await cancels an already-running node. A
`DynamicWorkflowBridge` bound to the Gateway `ExecutionKernel` dispatches every
wave (and runtime replan patches) through that kernel's per-run claim; an
explicitly unbound bridge disables registry fallback so a live digest executor
cannot masquerade as a bound host runner.

Gateway routes are `POST /api/workflows/dynamic/perceive`,
`POST /api/workflows/dynamic/execute`, the `/api/workflows/turns` compatibility seam
(`dynamic=true` opts into the full loop), and `POST /api/bots/{name}/workflow`.
Definitions/runs are server-owner-scoped; request context cannot self-assert
ownership. Registry discovery is a bounded read-only projection, not proof that a
provider is connected, and missing executors, skills, MCP connections, compensation
callbacks, or verification evidence fail or disclose honestly.

The default `alpha.local.digest` executor is a `local_digest_projection`: it hashes
inputs to exercise graph mechanics, keeps `acceptance_passed=false`, and is never
reported as domain-task acceptance — a real model/tool/MCP/sandbox/bot executor must
be bound for domain work. Recurring prompts disclose the missing scheduler handoff
rather than creating a second cron owner. Workflow events are appended to the
durable JSONL sink before listeners run; the Gateway sink is fail-closed, redacts
event payloads, validates paths/schema, and exposes durability, projection,
hydration, replay, and append-only plan history. That local adapter is atomic and
restart-recoverable for one Gateway process, not a shared multi-worker
lease/exactly-once repository: do not claim true concurrent wave parallelism or
cross-process exactly-once execution. Full operations, API examples, and the
regression suites are in
[`docs/DYNAMIC_WORKFLOWS.md`](../../../docs/DYNAMIC_WORKFLOWS.md).

## Guarded source auto-update contract

Local source checkouts may opt into the Phase-2 update engine in
`backend/packages/harness/alpha/evolution/update_engine.py`. It mutates only a local
Git source checkout: a published GitHub release (or an explicitly configured
development branch) resolves to an immutable commit, the worktree and remote
identity are checked, the update is fast-forwarded behind a backup ref, and success
is recorded only after restart/health checks. The engine is a separate trust
boundary and argv-only: it rejects dirty/diverged worktrees, requires
manifest-matching GitHub remote, allowed branch, fast-forward ancestry, and a stable
target ref, optionally verifies signatures, snapshots `config.yaml`/
`extensions_config.json` outside Git, publishes a cross-process maintenance barrier
that closes new run admission, invokes only argv-based hooks, restores declared
dependencies on rollback, records honest pre-mutation recovery, and never accepts a
client-supplied URL/ref. Manual confirmation never bypasses `canApply` or other
safety gates. `release_check.py` remains the stable public state/HTTP contract; the
Gateway only queues a detached apply, and PATs plus auth-disabled/internal
identities never receive admin capability. State/history/lock files live under
`runtime_home()` and must never contain GitHub tokens or secrets. Docker images, Helm
releases, and the Electron installer remain orchestrator-owned and are never updated
in place.

The committed `config/update-policy.json` is a disabled, credential-free template;
unattended deployments should keep a mutable operator policy outside the clean
checkout and select it with `ALPHA_UPDATE_POLICY_PATH`, and `main` branch tracking is
an explicit operator choice, not the default. Application requires both policy flags
plus the startup-scoped `autonomy.loops.self_update.enabled` setting; the
`self_update` loop is registered in `AutonomySupervisor` (an enabled policy registers
it automatically, an explicit `autonomy.loops.self_update` block can override/disable
it) and Windows autostart may register `Alpha_Update`, but the policy is the kill
switch. Commands: `make update-status|check|apply|recover`. Full operations and
recovery guidance live in `docs/AUTO_UPDATE.md`; tests live in
`backend/tests/test_auto_update.py`.

## Peer network boundary

`alpha.peer_network` is the single harness owner for cross-installation Alpha
identity, discovery, pairing, transport, SQLite conversations, delivery receipts, and
topology semantics. `PeerNetworkService` is installation-scoped; do not accept a
model/client owner assertion. Discovery cards are untrusted and never grant
credentials. HTTP/WebSocket endpoints are validated before use, messages are
bounded/idempotent, and public ingress is authenticated by the pairing token in the
Gateway adapter. The optional GitHub adapter publishes public Agent Cards only; it is
not a mailbox, and libp2p is an external future seam that must not be reported as
active without a real implementation. See
[docs/ALPHA_PEER_NETWORK.md](../../../docs/ALPHA_PEER_NETWORK.md) and
`backend/tests/test_peer_network.py`.
