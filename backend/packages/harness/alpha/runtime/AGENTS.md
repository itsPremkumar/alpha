### Sentinel repair loop (`runtime/sentinel/`)

The sentinel package's local [`AGENTS.md`](sentinel/AGENTS.md) owns repair,
rollback, journal integrity, and analytics invariants. Operator workflow:
[`docs/SENTINEL.md`](../../../../../docs/SENTINEL.md). A red or unknown check
never commits, repair never auto-pushes, and process-local signals are not shared
across Gateway workers. Keep its listed backend and frontend suites aligned.
### Durable runtime layer

The layer map, status, and remaining implementation gaps live in
[`docs/architecture/durable-runtime.md`](../../../../../docs/architecture/durable-runtime.md);
`runtime/sessions/AGENTS.md`, `runtime/network/AGENTS.md`,
`runtime/side_effects/AGENTS.md`, `runtime/supervisor/AGENTS.md`, and
`runtime/shutdown.py`'s adjacent contract own their details. `RunManager` remains
the only run-lifecycle owner; `SafeRunRecoveryService` remains the only safe
continuation authority. The Python `ProcessSupervisor` is not wired into the
Windows launcher: `recovery/` and `start.ps1` own that separate restart chain.
Memory persistence is process-local; do not claim cross-worker exactly-once for
JSON/JSONL state. `NetworkWaitService` records parks but does not launch resumes.

<!-- honesty-claims
side_effect_ledger_sql_repository: exists
side_effect_ledger_production_writer: exists
parked_session_durable_registry: exists
parked_session_resume_launcher: absent
cross_process_exactly_once: exists
supervisor_in_windows_launcher: absent
-->
### Workspace Snapshot Cancellation

`packages/harness/alpha/workspace_changes/recorder.py`: after `_prepare_capture()`
hands off roots, cancellation must drain text scans (`include_text=True`) before
removing the cache the worker may still access. Metadata scans
(`include_text=False`) own no cache: cancel promptly, let the worker continue, and
consume/log its outcome in a completion callback. Prepare-stage cancellation retains
its handoff/reclaim path. Regressions in
`tests/blocking_io/test_workspace_changes_cancellation.py` must cover prompt metadata
cancellation and text-cache drain/cleanup.

### Gateway Stream Framing Contract

- Gateway streams `write_file` and `str_replace` argument deltas in bounded
  batches for multi-mode `messages-tuple` consumers; single-mode message
  consumers retain the original per-chunk contract. Non-message frames flush
  pending batches, and `values` remains an optional complete-state snapshot
  rather than a prerequisite for batching.
- With `stream_subgraphs`, subgraph frames keep their namespace in the SSE event
  name (`values|<ns>`, LangGraph Platform style) instead of impersonating root
  frames — a delegated subagent inherits the parent checkpoint namespace, so
  publishing its `values` snapshot as bare `values` replaces the whole thread
  view in SDK clients (#4399). Root-only consumers (file-tool chunk batcher,
  subagent event persistence, LLM error-fallback detection) ignore namespaced
  frames. The web frontend does not request subgraph streaming; subtask progress
  rides root-namespace `task_*` custom events.

### Persistent project guidance

`run_agent()` resolves a thread's assigned `metadata.alpha_project_id` using
the authenticated owner, then reads the project through the owner-scoped
`project_repo`. The resulting `__alpha_project_context` is server-owned,
stripped from client `context` and `configurable`, and installed only from this
explicit worker argument. An assigned project that cannot be read fails the run
closed rather than silently dropping its instructions. The middleware projects
those instructions as hidden, request-only user-level guidance; they cannot
grant tools, permissions, approvals, or verification claims. Tests:
`tests/test_project_run_context.py`, `tests/test_gateway_services.py`, and
`tests/test_durable_context_middleware.py`.

### Thread-goal continuation progress evidence

`runtime/runs/worker.py` passes `visible_conversation_signature()` to the bounded
goal no-progress counter. That signature includes visible chat and successful
tool receipts while excluding hidden continuation bookkeeping. Do not reduce it
to assistant prose alone: a repeated answer can accompany a new successful
operation, which is progress the evaluator can use. Keep the receipt-change
regression in `tests/test_goal_runtime.py` and the real continuation path in
`tests/test_goal_worker.py` aligned. Successful completion persists the goal
with `status="completed"` and the final evaluation as its receipt; manual clear
still removes it. Keep the worker, Gateway response and chat panel contract
aligned so a terminal run is not mistaken for verified completion without this
evaluator receipt.

### Acceptance verification result event

`runs/verification.py` is the deterministic overlay and
`GET /api/threads/{thread_id}/runs/{run_id}/verification` its only reader;
neither decides a lifecycle — `RunManager` still does. Never equate a terminal
run status with verified acceptance: `success` with criteria and no evidence, and
`error` either way, return `unverified`. The auto-verifier supports explicit
exact-text file readback plus `File artifact at "/path" exists and is non-empty`.
Artifact evidence requires a successful `read_file` event for the exact path
after its latest successful `write_file` in the run, if any; it records a bounded SHA-256 digest,
reports an empty file as `NOT_MET`, and leaves absent, stale, ambiguous, or
oversized readback unverified. It returns `unverified` when *any* criterion is
outside the supported typed forms rather than a partial pass. The first complete measurement is
persisted as a singleton `run.verification` event keyed by a SHA-256 of the
stored criteria, so later reads replay it and changed criteria re-measure. No
criteria is `not_requested`, not a pass; the 32-criterion ceiling is refused, not
truncated. Tests: `tests/test_run_acceptance_criteria.py`,
`tests/test_apex_auto_acceptance.py`, `tests/test_run_usage_endpoint.py`.

### Safe Run Auto-Recovery

Continuation details: [`app/gateway/AGENTS.md`](../../../../../app/gateway/AGENTS.md#safe-run-continuation)
and [`docs/architecture/durable-runtime.md`](../../../../../docs/architecture/durable-runtime.md).
Only known-safe model/agent checkpoints resume; side effects, unknown state,
ownership failures, and cancellation stop. `RunManager` owns lifecycle; resumes
use normal trusted admission. Keep Gateway/recovery suites aligned.

### Stream Bridge Heartbeats

Memory and Redis bridges take their default idle heartbeat cadence from the startup-only `stream_bridge.heartbeat_interval_seconds` setting. Keep the default on the bridge instance so SSE, `/wait`, and internal subscribers stay aligned; an explicit `subscribe(..., heartbeat_interval=...)` remains a per-subscription override.

The bridge sentinel is invisible to browsers (`: heartbeat` SSE comment), so the client-facing signal is `app/gateway/sse.py::with_heartbeats`: after `SSE_HEARTBEAT_SECONDS` (15 s) with no real event it emits a named `event: heartbeat` on the four run-stream mounts. Bridge comments pass through without resetting that clock, and the frame carries no `id:` so the `Last-Event-ID` rejoin cursor never moves. Tests: `tests/test_sse_heartbeat.py`.

### Run Stall Watchdog (`runtime/selfheal/run_stall.py`)

Inflight `status=running` rows whose `updated_at` (bumped by every progress snapshot) lags `run_stall.timeout_seconds` (default 900 s; scanned every 30 s) are cancelled through the one universal primitive (`RunManager.cancel`) and persisted as terminal `error` with `stop_reason="stalled"`; a double-read lets late progress win. Built from `config.run_stall` in `app/gateway/deps.py`, stopped first inside the composed `close_admission()` step; startup-only in `config/reload_boundary.py`. Tests: `tests/test_run_stall_watchdog.py`.

### Token Meter (`runtime/token_meter.py`)

Process-local cumulative token ledger (DeepSeek-Harness-style `ctx.tokenMeter`): `TokenMeter.record()` accumulates per `(user_id, thread_id, model)` scope with a bounded scope cap (oldest-inserted evicted), `snapshot()` aggregates over wildcard filters, `check_budget()` compares against a token budget (non-positive budgets fail closed), and `reset()` clears scopes. `TokenUsageMiddleware` feeds the process-global meter from every response carrying `usage_metadata` totals (side-effect only; injectable per-instance for tests). Per-run enforcement stays in `TokenBudgetMiddleware`; durable reporting stays in `runs.token_usage_by_model` + the console `/usage` route — the meter is for in-process budgets and live guards, never billing. Tests: `tests/test_token_meter.py`.

The meter counts **spend**. `runtime/context_window.py` counts **occupancy** — how much of one model's window a thread currently fills — and the two must not be conflated: a cheap model on a small window can be at 95% occupancy while a flagship on a 1M window sits at 4%, and only the second is a pressure reading.

### Context windows: pressure, headroom, and the exhaustion ladder (`runtime/context_window.py`, `runtime/context_exhaustion.py`)

The context-window account, and the ordered plan for when a window fills. Two pure modules with no I/O and no clock: `context_window.py` derives *how full a thread is*, `context_exhaustion.py` declares *what to do about it*. Neither performs an action, so neither can claim a compaction that will not run.

**Three numbers that are routinely collapsed into one.** The **declared** window an operator wrote (`models[].context_window`), the **usable input** window left after the response is paid for, and the **occupancy** the thread's messages measure. `alpha.models.discovery` already separates the provider-side three (`context_length` / `endpoint_context_length` / `endpoint_max_completion_tokens`) because collapsing them is the documented cause of wrong thresholds; this module carries the same split one layer in.

- **`models[].context_window` is an INPUT window.** This is a correction, not a restatement. The field was documented as "total (prompt + completion)" while every consumer read it as input-only: `models/factory.py` puts it in the langchain profile as `max_input_tokens`, the summarization fraction triggers resolve against it, and the Gateway usage percentage divides the *input* message count by it. An operator who read the old description and declared the total understated occupancy by exactly their output reservation, and the summarization fraction fired late by the same amount. The total is now derived (`usable = declared - output_reserve - next_turn_reserve`), never declared twice.
- **`unknown` is a real band and never degrades into `nominal`.** A deployment that declared no `context_window` reports `band: "unknown"` with every derived field `None` and `reason: "context_window_not_declared"`. `band_rank` sorts `unknown` *below* `nominal` so a caller choosing "the tighter of these two readings" lands on the unmeasured one. This is the same negative rule `runtime/network/` applies to connectivity.
- **The ceiling is not the problem.** `over` defaults to 0.95 of the *usable* window because a window filled to capacity is a window the model reasons badly over, long before the provider refuses the request. Every threshold is fail-closed at load (`config/context_window_config.py`): a percent-style `80` where `0.8` was meant, a non-finite float, or a set that is not strictly increasing is refused rather than reaching a state where the guard looks working.
- **An unset threshold never blocks a higher band.** `critical_fraction=None` with `over_fraction` set still reports `over` at 96%, because a missing middle rung must not disable the one above it — the same fail-open-toward-evidence rule the network hysteresis applies. A threshold outside `(0, 1]` is ignored rather than clamped, so a mis-typed `1.5` cannot read as "over at 150% of the window".
- **A reservation that would eat the whole declared window is clamped to a floor and disclosed**, never silently reduced to zero. A zero usable window reads as "always over" and would compact every thread on every turn.

**The ladder is ordered from least destructive to most**, and the order *is* the decision: `compact` → `evict_tool_outputs` → `escalate_window` → `park` → `fail_closed`. Two properties are load-bearing:

- **Availability never reorders the ladder.** An unavailable rung stays in the plan carrying `available=False` and its own reason, so `unavailable_reasons` names every skipped rung. Dropping it would let a plan of three available rungs read as "the only three options", which is how a parked run gets mistaken for an exhausted one.
- **`fail_closed` is the only always-available rung**, which makes `first_available` total — a caller can always act. It also means a plan whose first available rung is the terminal one is a plan where every recovery rung was refused, which is why `unavailable_reasons` exists and why a caller reaching `fail_closed` should log it.
- **`critical` is pre-empted by the cheapest rung only.** Escalating to a more expensive model for a thread that merely *approached* the ceiling is the wrong trade, so the `critical` plan is `compact` + `fail_closed`.

**This module decides, it does not do.** Compaction belongs to `runtime/context_compaction.py` and the summarization middleware; parking belongs to `SafeRunRecoveryService` through the normal stop-reason path. The two durable stop reasons (`context_window_parked`, `context_window_exhausted`) are declared in `CONTEXT_WINDOW_STOP_REASONS` and are the only rungs that reach durable run state — the three before them are recovery attempts that leave the run running. Nothing here is a second continuation authority.

**Wiring, and what is *not* wired.** `config.yaml -> context_window` is a hot-reloadable `AppConfig` section (not startup-only: changing a threshold moves the moment compaction fires and nothing else). `GET /api/ops/context-windows` projects the whole configured catalog — every model's declared and usable window plus its escalation target — and `GET /api/threads/{id}/runs/token-usage` carries the live pressure block in `context_usage.pressure` (``None`` when the Gateway reports no policy, never a default band). **The ladder is declared but not yet executed:** no production site calls `plan_context_recovery`, so a window that overflows today is still handled by the summarization middleware's own trigger and, failing that, a provider error. That gap is stated here so the declared plan is not read as an implemented one; wiring it means calling `compact_thread_context` from the worker's overflow path, which is a separate change with its own admission and side-effect review. Tests: `tests/test_context_window.py`, `tests/test_context_exhaustion.py`, `tests/test_context_window_config.py`, `tests/test_context_usage_gateway.py`; frontend `src/lib/context-window.test.mjs`.

### Checkpoint Channel Modes (`full` / `delta`)

**SQLite write contention fails bounded.** The checkpointer's connection opener applies the shared `SQLITE_BUSY_TIMEOUT_MS` (30 000) and `SQLITE_CONNECT_PRAGMAS` from `runtime/store/_sqlite_utils.py`, so a contended database errors instead of hanging a run. Tests: `tests/test_checkpointer_busy_timeout.py`.

Checkpointer storage runs in one of two channel modes, selected by `checkpoint_channel_mode` in `config.yaml` (default `full`). `delta` mode adopts LangGraph 1.2's `DeltaChannel` for `messages`: checkpoints store a sentinel + per-step writes instead of the full message list, so storage/serde grows O(N) instead of O(N²) in turns. All checkpointer backends (memory/sqlite/postgres) serve both modes unchanged — the semantics live in the compiled graph's channel table, not in the saver.

**Mode is process-frozen and restart-required.** `make_lead_agent` and the embedded `AlphaClient` freeze the resolved mode (`runtime/checkpoint_mode.py::freeze_checkpoint_channel_mode`) before compiling the graph with the mode-matched schema (`agents/thread_state.py::get_thread_state_schema`, plus `adapt_state_schema_for_mode` / `normalize_middleware_state_schemas` for middleware state). Adapted middleware schemas are cached by schema, mode, and resolved snapshot frequency so a pre-freeze ephemeral graph cannot leave a stale default-frequency schema behind. A second, different mode or frequency in the same process raises `CheckpointModeReconfigurationError`. To switch: edit config, restart.

**Delta snapshot cadence is configurable but frozen with the mode.** `database.checkpoint_delta.snapshot_frequency` (default `10`) sets the `DeltaChannel` snapshot cadence. It is frozen alongside the mode (`freeze_checkpoint_snapshot_frequency`; non-positive direct inputs raise `ValueError`, while a frozen-value mismatch raises `CheckpointModeReconfigurationError`), restart-required, and must match across every process sharing one checkpoint database — the cadence lives in each compiled graph's channel table and is deliberately NOT stamped into checkpoint metadata, so the mode-compatibility marker and full -> delta migration semantics are unchanged. Schema helpers resolve it explicit-arg -> frozen -> default, and every schema/graph cache (`_delta_thread_state_schema`, `_adapt_state_schema_for_delta`, the client agent-config key, the gateway accessor-graph cache) keys on the resolved value.

**Compiled-graph cache cap is configurable and hot-reloadable.** `database.checkpoint_graph_cache.accessor_graph_max` (default `64`) bounds the gateway accessor-graph cache, which clears wholesale at the cap. The cap is re-read on every eviction check (`resolve_checkpoint_graph_cache_max`), so a config.yaml reload takes effect without a restart — a size change never affects graph semantics, only eviction timing.

**Compatibility is asymmetric and fail-closed.** Every checkpoint written in delta mode carries metadata marker `alpha_checkpoint_channel_mode: "delta"` (injected via `inject_checkpoint_mode`; absence of marker = full, so pre-feature checkpoints need no migration). Before any state read/write, `ensure_checkpoint_mode_compatible` rejects a full-mode process opening a delta thread with `CheckpointModeMismatchError` (surfaced as HTTP 409 with the cause and thread id by the threads router; `CheckpointModeReconfigurationError` maps to 503) — a full-mode raw read of a delta blob would silently return empty/partial `messages`. The reverse direction is allowed: delta-mode processes read full checkpoints transparently (old full checkpoints seed the delta channel), so full → delta is the smooth migration path; delta → full requires materializing/converting the data first. Detection also honors upstream's `counters_since_delta_snapshot.messages` metadata, and an explicit config marker takes precedence over any ambient context value.

**Never bypass `CheckpointStateAccessor` (`runtime/checkpoint_state.py`) for thread-state access.** It is the single choke point binding graph + checkpointer + mode: it injects the mode marker into configs, runs the compatibility check before every `get`/`update`/`history`, and returns materialized state (delta checkpoints lack `channel_values.messages` — raw `get_tuple` reads see a sentinel). Gateway `services.py` builds and passes the accessor; thread-owned reads (state/history/regeneration) must use `build_thread_checkpoint_state_accessor` so the recorded assistant's middleware schema materializes every channel. `history(limit)` semantics: `0` means zero items (explicit empty), `None` means unlimited — do not pass `limit=0` through to `graph.get_state_history`. Assistant metadata lookup is fail-closed for mutation accessors so a store outage cannot silently select the default schema and discard extension channels. In `full` mode the read path degrades to a raw checkpointer read (`_RawCheckpointReadAccessor`) when the agent factory cannot build the graph (bad model config, MCP outage) — full checkpoints carry complete `channel_values`, so reads don't need the graph; degraded snapshots take `created_at` from the standard checkpoint `ts` field, falling back to metadata only for compatibility. The delta gate still applies on the degraded path; `next`/`tasks` degrade to empty and thread status falls back to the stored status because task presence is not derivable, while delta mode has no fallback (materialization needs the channel table).

**Replay checkpoint lookup prefers lineage and degrades only for an explicitly missing legacy parent link.** Branch and regenerate paths first walk `parent_config`, which prevents a global chronological scan from selecting a sibling created by regeneration. `CheckpointParentMissingError` alone enables the bounded newest-first history fallback in `app/gateway/checkpoint_lineage.py`; cycles, dangling/non-addressable parents, target mismatches, and depth exhaustion raise `CheckpointLineageIntegrityError` and fail closed instead of selecting a sibling. The compatibility scans request 400 raw checkpoints so up to 200 duration-only entries do not consume the effective branch-history budget; the fallback scans oldest-to-newest internally, skips duration-only checkpoints, and accepts only checkpoints with an addressable id as the replay base. A source history with no discoverable pre-user checkpoint preserves the historical single-checkpoint branch behavior instead of rejecting the branch; regeneration remains unavailable for that inherited response. Existing single-checkpoint branches are not mutated by regenerate preparation, and no raw checkpoint tuple is copied across threads because delta state depends on ancestry and pending writes. Regenerate source-run lookup uses the current thread's exact event, then the server-stamped `run_id` on the copied human message, then verified RunManager content matching; it does not read parent-thread events. When an interrupted response was streamed but never checkpointed, regeneration accepts only the latest visible human message's server-stamped `run_id` after verifying that it belongs to the same thread and still has `interrupted` status. Storage or checkpoint-mode failures are not treated as a missing base and still fail closed.

**A delta-mode run cannot fork; `runtime/runs/worker.py` linearizes the resume instead.** Resuming from an older checkpoint (regenerate, or any client-supplied `checkpoint`) forks the lineage, and delta state for a fork is not materializable: `BaseCheckpointSaver.get_delta_channel_history` — and the bespoke overrides in `InMemorySaver`/`PostgresSaver` — collect **every** `pending_writes` entry stored on each on-path ancestor, but a shared parent also carries the writes of the sibling child that was abandoned. Those writes replay into the fork, so the run starts from a message list still containing the answer it was supposed to replace (#4458: regenerating in a branched thread showed the superseded assistant message beside the new one after a reload; reproduced on postgres, sqlite, and the in-memory saver). Write-to-child ownership belongs to the upstream delta contract, so Alpha does not reimplement the walk: `_linearize_delta_checkpoint_resume` materializes the requested checkpoint's complete state and writes every channel onto the **current head** (which has no siblings) through the state mutation graph, using `Overwrite` for reducer channels and resetting newer head-only channels to their schema default (or `None` when no constructible default exists); it then drops the `checkpoint_id` selector and lets the run proceed linearly, while the abandoned turn stays in history as the rewritten head's ancestry. The worker holds `_checkpoint_thread_lock` across `_capture_rollback_point` and the optional linear rewrite, making the rollback snapshot and rewrite atomic with graph streaming and the preceding run's duration-metadata checkpoint write. Capture preserves the complete real pre-run state; cancel-with-rollback then linearly replaces the current delta head with that captured state rather than forking the now-shared pre-run checkpoint, so the abandoned turn is restored without replaying the resume sibling's writes. The worker also recomputes the current-run message boundary from the rewritten state and fails closed (an unreadable resume checkpoint raises rather than falling back to the corrupt fork). `full` mode keeps forking — its checkpoints carry complete `channel_values` and need no replay — so LangGraph branching semantics are unchanged there. Root namespace only; subgraph namespaces are left alone.

**Wholesale state replacement uses a state-only mutation graph + `Overwrite`.** `update_state` values pass through channel reducers (`add_messages` merge in full, append in delta), so replacing reducer values requires `Overwrite` rather than an ordinary update. Full-mode rollback and context compaction replace `messages`; delta resume and delta rollback replace every materialized channel and reset current-head-only channels to their schema default (or `None`). These writes go through `build_state_mutation_graph(as_node, mode, state_schema)`, and `state_schema` MUST be the thread's effective schema (`graph_state_schema(assistant_graph)`), because the base-ThreadState fallback silently discards written channels contributed by custom `AgentMiddleware.state_schema`. Channels absent from a full-mode fork write inherit the parent's channel blobs, so middleware channels survive rollback/compaction (locked by `test_rollback_preserves_middleware_contributed_channels` and `test_compact_thread_context_preserves_middleware_contributed_channels`). The compiled mutation graph has one no-op node (entry = finish) whose checkpoint machinery (channels/versions/metadata) is identical to the agent graph's but schedules no pending tasks, so the restored/compacted head stays idle instead of re-triggering the agent. Never hand-write checkpoints via `checkpointer.aput` for this; raw writers elsewhere must preserve checkpoint parentage — severed ancestry breaks delta replay (see `runtime/runs/worker.py` writer parenting and `checkpoint_patches.py`).

**Run rollback flow** (`runtime/runs/worker.py`): `_capture_rollback_point` materializes the complete pre-run state via the accessor and captures raw `pending_writes` via `aget_tuple` into an immutable `RollbackPoint` before the run starts — capture failure disables rollback (fail-closed), never restores partial state. In `full` mode, cancel-with-rollback forks from the pre-run checkpoint via the mutation graph and inherits non-message channels from that parent. In `delta` mode, forking is unsafe once the cancelled path has attached sibling writes to the pre-run checkpoint, so rollback replaces every captured channel on the current head, using `Overwrite` for reducers and schema defaults for current-head-only channels. Both modes reattach only the captured pre-run pending writes to the restored checkpoint. Edit replay runs (`metadata.replay_kind="edit"`) also restore the pre-run checkpoint on failed, timed-out, or interrupted completion and publish the restored `values` snapshot to the stream before `end`, so clients do not remain on a transient edited branch when the replay did not produce a successful replacement.

**Message feed seq stamping** (#4666): a checkpoint carries no position of its
own and loses messages to summarization, so a client merging a `values` frame
with the seq-ordered `run_events` feed cannot place a checkpoint-kept message
once the feed's loaded page window no longer reaches back to it.
`RunEventStore.get_message_seqs(thread_id, identities)` resolves the seq the
store already assigned, keyed by
`runtime/events/message_identity.py::message_identity` — the backend half of
the identity rule `frontend/src/core/threads/hooks.ts::messageIdentity` applies
(tool messages by `tool_call_id`; `X` / `X__user` human copies collapse to
one). The two halves must stay in sync: a mismatch is silent, degrading
placement rather than raising. `runs/worker.py::_MessageSeqStamper` attaches
the result as `additional_kwargs.alpha_seq` on root `values` frames only —
subgraph frames are not part of the thread feed's ordering, and nothing is
written back to the checkpoint. The run-scoped cache makes the compaction frame
the only one that costs a lookup, and the stamper soft-resolves the user id
once at build time — like the worker's write paths beside it — so a launch path
that never inherits the auth contextvar (a null-owner scheduled task) still
stamps instead of the db store's strict `AUTO` default raising per frame. A
resolved seq is cached for the run (earliest-seq-wins makes it final), but a
miss is not: a message this run produces reaches a frame before `RunJournal`
flushes it, so it misses and is persisted moments later. Misses are re-asked
when `RunJournal.feed_generation` — bumped once per successful event-store
write, never while the buffer merely fills — shows the feed gained rows, which
keeps the retry bounded by writes rather than by frames and makes a failed
lookup cost one generation instead of the run. REST
reads (`GET /threads/{id}/state`, `POST /threads/{id}/history`) stamp through
`events/message_seq.py::stamp_messages_with_seq`, the request-scoped
counterpart: everything a checkpoint still holds is already persisted, so one
batched lookup resolves the whole list. The db store prefilters candidate rows
in SQL (a LIKE clause per wanted raw id, wildcards escaped; an id `json.dumps`
would escape falls the set back to the full scan) so a wanted identity absent
from the feed — a message still streaming — does not force a full
fetch-and-decode of every message row's tool outputs on long threads.
`alpha_seq` is server-owned display metadata: the gateway strips it from
client input, because a welded-in seq goes stale when a fork re-seeds the feed
(#4380).

**LLM response callback coalescing** (`runtime/journal.py`): a provider may fire
`on_llm_end` twice for one LangChain run id, first without usage (or with all token
counts zero) and immediately again with usage populated. The first callback's generation
set is always canonical: `RunJournal` stages only its response events and immutable
message-summary fields while retaining the first caller, and applies that callback's
fallback state and tool-call bookkeeping immediately; those effects remain canonical.
It must not retain provider-owned message objects because a provider may mutate and
reuse the same response for the usage replay. Usage metadata is deep-snapshotted,
including nested token-detail mappings, before it enters a staged or buffered event.
An adjacent same-id positive-usage replay may enrich only each corresponding staged
event's metadata/content usage fields. Replay
generation-count differences never add, remove, or replace canonical messages. The next
unrelated event, an effective buffer size (committed plus pending events) reaching the
flush threshold, or an explicit flush commits the staged unit and updates the message
summary. Once that ordering boundary is crossed, a late usage replay can still update the
authoritative run token summary, but it cannot mutate the append-only message event,
caller attribution, fallback state, or tool-call bookkeeping. Closed journals return
from `on_llm_end` before inspecting the response or touching any run state.

**Tool-result event status** (`runtime/journal.py`): LangChain's `on_tool_end`
fires inside the tool handler, before the outer `ToolErrorHandlingMiddleware`
adds `alpha_tool_meta`. The journal therefore calls the shared
`normalize_tool_message()` classifier before persisting a live tool callback or a
history-seeded tool row. Keep that one classifier as the source of status; do not
add a second journal-only parser. This prevents a returned
`Error: File not found` body from being recorded as a successful call. Regression:
`tests/test_run_failure_is_not_reported_as_success.py`.

**Run delivery receipts** (`runtime/journal.py` + `runs/worker.py`):
`RunJournal` records each non-empty artifact update once per tool `Command` for
the terminal `run.delivery` event. When a command contains multiple messages, a
unique tool name resolved from matching `ToolMessage` entries supplies
attribution; additional command messages do not duplicate artifact paths or
counts. If multiple different tool names resolve for one flat artifact update,
the paths remain counted but unattributed because the command does not carry a
per-path mapping. `RunJournal` callbacks set `run_inline=True`: they do only
in-memory bookkeeping or schedule async writes, and staying on the run's
event-loop thread serializes parallel tool callbacks before terminal delivery
recording and flushing. Each worker creates a separate journal per run before
cancellable/fallible preflight work, so checkpoint compatibility failures and
cancellation while waiting for prior finalization still emit a zero-delivery
receipt. The worker flushes ordinary journal events, idempotently persists the
run-scoped receipt, and only then persists the staged terminal run status. A
receipt failure is retried on a short bounded schedule while the owning worker
still knows the real outcome and holds the lease. Delivery candidates are every
regular file created or modified under `/mnt/user-data/outputs`; internal
process-feedback files are excluded (the scanner's `EXCLUDED_DIR_NAMES` plus
the configured `tool_output.storage_subdir`), so a run that only externalized
oversized tool outputs does not fail delivery. At least one candidate must be
covered by a path attributed by the journal to `present_files`; presenting only
an unrelated pre-existing path does not satisfy delivery. Receipts for such
runs add `produced_paths`, `presented_paths`, `matched_paths`, `verification`,
`stage`, and `satisfied` to the Slice 1 fact fields. Missing a matching
presentation becomes a run error; a successful presentation is also downgraded
to error if its receipt cannot be durably verified. Runs without changed
outputs preserve ordinary chat behavior and the original receipt shape. Orphan
recovery first atomically claims an expired lease, then uses the same singleton
write to backfill a zero-delivery receipt — a stale recovery scan cannot
overwrite a live run's later detailed receipt, an event-store outage does not
undo the terminal takeover, and an existing detailed receipt is preserved when
a worker crashed after writing it. Event stores serialize `put_if_absent` with
ordinary thread writers: memory and JSONL provide the documented
single-process guarantee, while the DB store adds per-thread in-process locks
and PostgreSQL advisory locks for cross-process writers. Moving journal
construction ahead of preflight is receipt-only on early failure paths: a
separate boundary flag preserves the previous completion-data semantics, so
checkpoint incompatibility or cancellation while waiting for an older
finalizing run does not persist an empty completion snapshot. Worker tests pin
one accumulated receipt across multiple goal-continuation `_stream_once` calls;
journal tests drive LangChain's real async callback dispatcher against a single
journal to pin serialized, deduplicated parallel tool callbacks.

**Deferred-tool promotion event deduplication** (`runtime/journal.py`): one
`RunJournal` owns the lead graph's run-scoped atomic promotion claim. Parallel
`tool_search` Sends read the same pre-step state, so state diffing alone can
label the same schema as new more than once even though the `promoted` reducer
unions it once. Producers claim sorted candidate names before appending
`middleware:tool_promotion`; later overlapping decisions emit only their
unclaimed remainder. Ordinary task-tool subagents use an equivalent claim on
their per-execution parent-loop proxy, preserving separate events when two
different delegated agents promote the same tool. The active catalog is fixed
for one graph execution, so the claim needs no persisted catalog hash.

**`limit` is a row count, not a slice bound** (`runtime/events/store/`): SQL backends express a "latest page" as `ORDER BY ... DESC LIMIT n`, so `limit=0` returns **zero** rows, and the `list_messages` / `list_messages_by_run` contracts describe `limit` as a record count. The memory and JSONL backends originally sliced with `rows[-limit:]`, which cannot express that — Python evaluates `-0 == 0`, so `rows[-0:]` is `rows[0:]` and a caller asking for zero messages was handed the entire feed back, on the *default* `run_events.backend=memory`. Every "latest page" slice in those two backends now goes through `events/store/base.py::take_latest`, the single owner of that rule, so the three implementations of one interface cannot drift on the zero case. Do not reintroduce a raw `[-limit:]` in a store; add a test beside `tests/test_rt_audit_event_store_limit.py` instead.

**Targeted run-event attribution** (`runtime/events/store/`):
`RunEventStore.find_latest_ai_message_run_ids()` has a complete-or-error
contract. Its default implementation walks `list_messages()` backward in
1000-row pages, preserves the first page's high-watermark through the exclusive
`before_seq` cursor, and raises when a full page has no safe progressing `seq`.
Memory and database stores use that bounded path; the JSONL store overrides it
with one complete thread-log read because each JSONL page would otherwise
rescan every run file. The default and JSONL paths share the public
`normalize_message_ids()` and `match_ai_message_run_id()` helpers from
`events/store/base.py`. Database owner filtering is inherited on every page.
Callers may use a missing key as proof that no valid AI event exists only after
an ordinary return, never after an exception. A caller that crosses a run or
checkpoint-write admission boundary must repeat the complete audit after
admission; a pre-admission exact hit can be superseded by a later event just as
a pre-admission miss can become an exact hit.

**Content search** (`RunEventStore.search_message_content`, `events/search.py`,
migration `0023_run_events_fts`): zero-embedding recall over
`category="message"` rows — SQLite FTS5 (`run_events_fts` + triggers, human/ai
ranked above tool output) / Postgres `tsvector` + GIN / substring scan on
memory/JSONL. SQL backends enforce the explicit `user_id`; blank queries
return [] without storage work. Db store self-heals missing FTS objects at
runtime (fresh stamped DBs) with a LIKE fallback. Surfaced as the
`session_search` tool (lead-only) and `POST /api/threads/search/content`.

Gateway `POST /api/threads/{id}/history` uses that lookup to migrate legacy AI
messages. An exhaustive miss preserves the human-boundary fallback; an
incomplete lookup removes unproven synthesized IDs. Its metadata-only
write-on-read cache stores `run_message_ids` for every audited AI ID (including
exhaustive misses) plus required `run_durations`; duration presence alone does
not prove attribution. Historical `body.before` reads write the audit to the
head, and the merge may retain IDs no longer in materialized history, which
readers ignore. Migration must acquire the durable `checkpoint_write`
reservation, then repeat the whole message audit and batch-reload required run
rows before persisting. Post-admission exact hits replace foreground exact or
boundary mappings, and recomputed final durations replace foreground snapshots.
Successful workers keep their durable run row active through the final duration
checkpoint write, so a peer migration cannot enter during terminalization.
The first `RunManager.list_by_thread()` hydration page uses a 100-row floor or
the number of required IDs, whichever is larger; missing exact runs use targeted
`get()` calls.

**Terminal run cleanup explicitly breaks graph-scoped references while preserving the existing `RunRecord` grace period.** Every `agent.astream()` iterator is closed in `_stream_once`, including abort/exception/early-break paths. A close failure after an abort is warning-only and cannot replace the user-requested `interrupted` outcome; normal-completion close failures still surface, and an in-flight stream exception remains authoritative over a secondary close failure. Journal construction and cancellable preflight work (including MCP task projection and the prior-finalization wait) live inside the worker's guarded body, so cancellation before agent startup still terminalizes the run and closes its stream. `run_agent()` wraps the complete terminal-finalization sequence in an outer teardown guard, so cancellation or failure from any terminal-stage await cannot skip `RunJournal.close()`, removal of the journal, `__pregel_runtime`, and internal runtime-context values from every runnable config, or release of local graph/payload references. That guard schedules bridge cleanup, run-record cleanup, and cyclic GC even when interruption happens before the terminal stream marker or terminal publication itself fails, so neither a cancelled observer nor a delivery-backend outage can strand process-local run state. A non-`Exception` `BaseException` caught while awaiting the completion hook or task-stop notification (including host-task cancellation) is deferred through the ordinary remaining finalization, with the first interruption preserved and every caught host-task `CancelledError` balanced by calling `Task.uncancel()` until the current task’s cumulative cancellation count is clear. Task-stop fan-out runs in one child task and every host wait uses `shield`, so repeated cancellation of the worker cannot cancel that fan-out or skip later observers; the worker keeps awaiting the same child task. A rogue observer that raises its own `CancelledError` remains contained by the extension dispatcher and distinguishable from host cancellation. This guarantee applies only to cancellation caught during those hook stages: clearing the finalizing barrier and publishing END remain direct awaits, so another cancellation in the subsequent critical tail retains forceful-termination semantics instead of creating an unbounded shield. If that tail completes without another interruption, the first deferred interruption is re-raised after END; a barrier-clear failure prevents END publication, while an END failure is raised after the barrier is clear. `RunJournal.flush()` clears its `_pending_progress_task` after awaiting or cancelling it; ordinary `close()` detaches the event store/progress reporter and clears callback bookkeeping only after that flush succeeds, preserving the buffer for retry on a transient store failure. A fenced worker instead calls `close(flush=False)`, which cancels pending journal work and detaches without initiating another event-store write after lease ownership is lost; its final detach runs even if a second cancellation interrupts pending-task shutdown. `RunManager.cleanup(run_id)` retains the process-local `RunRecord`, completed task, and request payload for its default 300-second local join/status window before releasing them. Durable history remains in `RunStore`; `StreamBridge` data keeps its separate 60-second late-subscriber window, and both cleanup coroutines run in a fresh empty `contextvars.Context`. A contextless full cyclic-GC pass, coalesced to at most once every 10 seconds and dispatched through the default executor, bounds the lifetime of unreachable LangGraph callback/loop cycles without synchronously walking the heap in the event-loop timer; passes taking at least 100 ms are logged at INFO because CPython GC may still impose interpreter-level pauses.

**Where things live**:
- `runtime/checkpoint_mode.py` — mode + snapshot-frequency freeze, marker injection, delta detection, compatibility gate, both error types
- `runtime/checkpoint_state.py` — `CheckpointStateAccessor`, `build_state_mutation_graph`, `RollbackPoint`
- `checkpoint_patches.py` (package root) — checkpoint-machinery patches: delta-history folding for `InMemorySaver` (delegating to the base walk), stable message IDs across materialization, upstream first-write drop fix, and `BinaryOperatorAggregate` unwrapping an `Overwrite` first write into an empty (MISSING) channel — Union-typed reducer channels (`sandbox`/`goal`/`todos`/`promoted`) have no constructible default, so a replace-style write into a fresh branch thread or a never-written channel stored the wrapper literally and crashed the next consumer (#4380; probe-guarded, stands down if upstream fixes it)
- `agents/thread_state.py` — `ThreadState`/`DeltaThreadState`, `delta_messages_field` / `DELTA_MESSAGES_FIELD` (`DeltaChannel` at the configured `snapshot_frequency`, default 10), schema adaptation helpers
- `runtime/context_compaction.py` — compaction via accessor + mutation graph (reference consumer)
- `runtime/checkpoint_cache/` + `runtime/checkpointer/cached_saver.py` — delta-mode checkpoint history cache; checkpoint state reads MUST go through `CheckpointStateAccessor`, and the checkpointer may be a `CachedHistorySaver` wrapper — never rely on concrete saver types
- Tests: `tests/test_checkpoint_mode.py` (freeze/detect/gate), `tests/test_checkpoint_state.py` (accessor/mutation graph), `tests/test_delta_channel_checkpointers.py` (saver parity), `tests/test_threads_checkpoint_mode.py`, `tests/test_gateway_checkpoint_mode.py` (dual-mode e2e parity), `tests/test_context_compaction.py` (mutation-graph write, no scheduling), `tests/test_run_worker_rollback.py`, `tests/test_cached_history_saver.py` + `tests/test_cached_history_saver_integration.py` (history cache)

**Checkpoint channel benchmarks** live in
[`scripts/benchmark/checkpoint/`](../../../../../scripts/benchmark/checkpoint/README.md)
(protocol, commands and operational limits in that directory's `README.md`).
`bench_channels.py` pairs `full`/`delta` message-only StateGraphs in fresh child
processes; `bench_production.py` runs the real lead-agent graph and the real
Gateway `state`/`history` route stack. Paired modes must materialize identical
state or the pair's performance data is rejected; storage fields become `null`
with `storage_stats_error` when a saver's diagnostic layout changes while the
timing/correctness row stays valid, and timing thresholds are not CI gates. The
default 1 GiB estimated full-payload cap skips oversized pairs
(`--allow-large-cases` only on provisioned machines; `--modes delta` bypasses
it), and large matrices need an explicit `--timeout-seconds` plus a real-disk
`TMPDIR`. Harness tests: `tests/test_bench_checkpoint_production.py`,
`tests/test_summarize_checkpoint_production.py`.
