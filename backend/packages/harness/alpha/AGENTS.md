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

