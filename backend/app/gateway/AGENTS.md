### Gateway API (`app/gateway/`)

Approval-gated slash commands enter through `routers/commands.py`. Treat
request-body `context.approved` as an approval request only: mint the registry
grant through `deps.is_admin_user`, attribute it to the authenticated user, and
apply the same sanitizer to command, auto-trigger, and phase-transition routes.
PATs are not operators. Do not pass client approval flags into command execution.
Contract and regression tests: `docs/COMMAND_HONESTY.md` and
`tests/test_command_route_approval_grants.py`.

FastAPI listens on port 8001; health: `GET /health` (liveness) and `GET /health/ready` (readiness; concurrently probes the ORM engine behind `database:` plus the effective LangGraph checkpointer/Store backend - the legacy `checkpointer:` section, otherwise derived from `database:`, resolved from the startup config snapshot on `app.state`; identical concurrent requests share one in-flight probe pair, and checkpointer connections are serialized behind a strict per-process gate; disk-backed SQLite ORM readiness opens a separate short-lived, non-creating connection so a saturated application pool does not report a false database outage; in-memory SQLite continues to use its existing pooled connection; one bounded deadline; 503 while either is unreachable or the startup backend cannot be resolved; `not_configured` for process-local backends such as `backend=memory`). Set `GATEWAY_ENABLE_DOCS=false` to disable the default `/docs`, `/redoc`, and `/openapi.json` endpoints.

The disk-backed SQLite ORM probe uses a separate `aiosqlite` connection. Closing
that connection is queued behind its worker-thread operations; its short cleanup
wait must not cancel the queued close task, or the connection worker can remain
alive after readiness returns. A timed-out close remains strongly referenced and
finishes when the worker drains.

Durable MCP notifications use internal Agent runs. Keep their trusted delivery instruction outside the user-input boundary, and frame serialized remote events as untrusted before model invocation. Strict thread existence/ownership admission dead-letters events whose task outlives its deleted chat instead of recreating the thread.

**Connectivity lifecycle.** `langgraph_runtime()` owns the network monitor and
the durable parked-session registry, and stops both at `ShutdownPhase.ADMISSION_CLOSED`
inside the planned drain - before the run drain, because a probe in flight during
shutdown would publish a transition into a tearing-down process, and a registry
still claiming rows would act on services that are already going away. The first
probe is awaited *before* the poll loop starts, so a Gateway booting on a dead
network reports `offline` rather than `unknown` one poll later. Startup reclaims
waits whose claim lease outlived the previous process. The wait policy comes from
`config.yaml -> network_wait` (`to_wait_policy`), not from the runtime default, so
the ceiling an operator actually wants is readable in the file — the startup log
names which of the two it resolved (`unbounded …` or `at most N resume attempts`).
It also records
`app.state.network_configured` *before* the branch that installs the monitor,
because "disabled by configuration" and "enabled but absent" are different
operator problems and an operator surface cannot tell them apart from a missing
attribute. A `database.backend:
memory` deployment gets the monitor but not the registry - there is nowhere
durable to record a park - and logs that degradation rather than absorbing it
silently. The Gateway installs **no** resume launcher on `NetworkWaitService`:
continuation belongs to `SafeRunRecoveryService`, whose stop-reason-driven scan
already picks up a `network_waiting` run, and inventing a per-thread resume here
would create a second continuation authority that bypasses the fail-closed
side-effect gate. Owner map:
[packages/harness/alpha/runtime/AGENTS.md](../packages/harness/alpha/runtime/AGENTS.md);
user map: [docs/architecture/durable-runtime.md](../../../docs/architecture/durable-runtime.md).

**The parked-session timeline is a thread surface.** `GET /api/threads/{id}/network-waits`
answers *"what happened to this conversation"* — when the link died
(`first_waited_at`), when the wait ended (`terminal_at`), how long it waited, and
whether it is waiting still — beside the same live connectivity block
`GET /api/ops/network` returns. It is declared on the **Thread Runs** router and
carries `threads:read` with the standard owner check.

Five rules, each a plausible wrong answer:

- **`terminal_at`, never `updated_at`.** `release()` moves `updated_at` every time a
  failed resume writes its next backoff, so reading the recovery time off it would
  report a *scheduled retry* as the moment the link came back. An open row keeps
  `terminal_at` `NULL`, so "still waiting" is a state and not a zero timestamp.
- **Settled rows are returned.** `list_open` answers the wrong question for a
  thread's history: an outage that ended an hour ago is still part of the
  conversation, and a view reading only open rows would show a thread as never
  having been parked the moment it resumed.
- **`reported: false`, not an empty list**, when the process records no per-thread
  timeline (a `database.backend: memory` deployment has nowhere durable to record a
  park; `network.enabled: false` starts no recovery pass). "No outages" would be a
  claim that deployment cannot make.
- **`503`, not an empty list**, when the store itself cannot be read.
- **`bounded` / `max_attempts` travel with it**, so a client can state whether this
  deployment ever gives up instead of hinting at a deadline nobody declared. The
  default (`bounded: false`) is the sentence "for as long as it takes"; `null` is a
  third state and must be rendered as neither promise.

Owner map: [the network guide](../packages/harness/alpha/runtime/network/AGENTS.md);
human surface: `frontend/src/components/NetworkWaitBubbles.tsx`.

**The monitor is read, and can be re-probed.** Until `GET /api/ops/network`
existed, the monitor sat on `app.state.network_monitor` with **no production
caller**: its reading was produced on every poll and died with the tick. That
route and `POST /api/ops/network/recheck` are that caller, and both reach the
monitor through `app.gateway.ops_runtime.network_snapshot` / `_perform_recheck`.
Three rules keep them honest: `latency_ms` averages only the *reachable* targets
and stays `null` (never `0`) when none answered; `allows_network_attempt` is read
from the monitor's own decision rather than re-derived from the state string, so
`unknown` keeps permitting an attempt instead of being rounded into an outage;
and the payload carries `observed_age_seconds` rather than the observation's
`observed_at`, because that stamp is monotonic and publishing it as wall-clock
time would be a confident wrong number. The recheck is bounded by
`asyncio.wait_for` and runs through `NetworkMonitor.recheck()` - the same
serialized transition the poll loop runs - so a click cannot double-count
hysteresis corroboration and publish a state the ladder has not seen twice. Owner
map:
[packages/harness/alpha/runtime/network/AGENTS.md](../packages/harness/alpha/runtime/network/AGENTS.md).

**Safe run continuation** is owned by `app/gateway/run_recovery.py`, not by`RunManager` or an individual router. `RunManager` remains the sole lifecycle owner and the only producer of orphan/shutdown terminal states; the Gateway service consumes those durable states, checks the current checkpoint through a graph-required `CheckpointStateAccessor` (raw full-mode blobs cannot prove `next`/`tasks` and are rejected for recovery), and launches continuation through the same trusted `start_run` boundary used by scheduled/internal work. Only pending model/agent nodes auto-resume. Tool, MCP, custom, shell, browser, write/delete, payment, unknown, ownerless, stale-thread, exhausted, or malformed-checkpoint cases become explicit CAS-fenced stop reasons and never replay automatically. Scheduled-task and durable MCP-notification rows are also excluded because their own queue/dispatcher owns occurrence identity and completion accounting; durable cancellation requests are excluded as an explicit stop fence even if shutdown writes a recoverable-looking reason. Each continuation has a deterministic idempotency key and a persisted attempt number, so a crash between checkpoint inspection and admission cannot create duplicate workers. A terminal checkpoint holding only an **active durable goal** (no pending graph nodes) is classified as the synthetic `goal_continuation` resume and stamps `RECOVERY_RESUME_KIND_GOAL_CONTINUATION` (`alpha.runtime.runs.manager`) into `auto_recovery` metadata; the worker alone decides whether that skips the bare entry-node replay, and only after live re-verification (head/selector/snapshot agreement, no pending nodes, active goal, durable end-of-turn receipt) with every failed check falling through to the ordinary stream path — the marker is a claim the worker audits, never a grant. Network-disconnected SSE clients do not cancel creator runs by default; `POST .../cancel` remains the explicit stop path. See `packages/harness/alpha/runtime/AGENTS.md` for the full state/reason contract.

CORS is same-origin by default when requests enter through nginx on port 2026. Split-origin or port-forwarded browser clients must opt in with `GATEWAY_CORS_ORIGINS` (exact origins); Gateway `CORSMiddleware` and `CSRFMiddleware` both read that variable so browser CORS and auth-origin checks stay aligned. Those clients also need `CORS_EXPOSED_HEADERS` (`csrf_middleware.py`): run-creating routes return the run's id in `Content-Location`, which is not CORS-safelisted, so JS cannot read it unless it is exposed — and the LangGraph SDK resolves run metadata from that header alone, so withholding it breaks `useStream`'s `onCreated` and thread-gated actions.

Browser auth sessions are owned by `app.gateway.auth.session_cookie`. Login accepts a `remember_me` form flag, but the Gateway never stores passwords. `SessionCookiePolicy` persists the `HttpOnly access_token` cookie only for HTTPS/trusted-forwarded HTTPS, direct-host localhost HTTP, or explicit operator opt-in for insecure persistence; public HTTP sandbox URLs degrade to session cookies.

Response hardening is owned by `app.gateway.security_headers_middleware.SecurityHeadersMiddleware` (pure-ASGI, headers attached at `http.response.start` so SSE is covered). It sets `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: SAMEORIGIN`, and `Permissions-Policy: camera=(), geolocation=()` on every response without overwriting existing values; `Strict-Transport-Security` only for HTTPS requests (direct scheme or `X-Forwarded-Proto: https`). `microphone` is deliberately left out of the permissions policy because workspace voice-input needs the grant. Registered LAST in `create_app()` so it is outermost and also covers `AuthMiddleware`/`CSRFMiddleware` rejections and the trace fallback 500.

Session-creating handlers stamp the final `max_age` on `request.state`; CSRF cookie creation mirrors it so the double-submit pair expires together, including re-issue after password changes and OIDC callbacks. A small `HttpOnly` preference cookie preserves the remember choice across re-issues. Logout clears all auth cookies and suppresses CSRF re-issue on the logout response.

Personal Access Tokens (`app.gateway.auth.pat`, `Authorization: Bearer dfp_...`) run as their owning user: an invalid Bearer is a hard 401 with no cookie fallback, which keeps `CSRFMiddleware`'s Bearer skip safe (origin checks still run). Scopes narrow within the allowlisted threads/runs/projects routes (including `POST /api/threads/{id}/move`); every other authenticated route 403s PAT callers (admin included). PAT management and `/change-password` require session auth; only SHA-256 digests are stored (`0017`).

Thread→project membership is written by thread creation (`POST /api/threads` with
a validated `project_id`), branch creation (the new row inherits the source
thread's project; an archived/deleted project degrades the branch to unassigned
instead of failing), and explicit moves (`POST /api/threads/{id}/move`); run
admission never modifies membership. The server-reserved `alpha_project_id`
metadata key is a read-only exposure of the `threads_meta.project_id` column and
is stripped from client writes.

Localhost persistence deliberately reads the direct request `Host` and ignores `Forwarded` / `X-Forwarded-Host`. Scheme and auth-origin reconstruction still consume forwarding headers. The bundled nginx sets `X-Forwarded-Proto`, but preserves an upstream HTTPS value and does not overwrite every forwarded header, so the outer trusted proxy must replace or strip client-supplied forwarding headers before traffic reaches Alpha.

Standalone local LangGraph Studio is recognized only through the upstream
`Auth.types.StudioUser` principal type, never by its reusable identity string.
The type is resolved once at import; an older SDK without it degrades to normal
owner scoping.
For that principal's assistant reads/searches, `langgraph_auth.add_owner_filter`
selects genuine server-registered assistants plus assistants owned by Studio;
all other resources remain owner-scoped. Assistant create/update handlers make
both `user_id` and `created_by=user` server-owned, because LangGraph gives
`created_by=system` privileged ownership semantics during run creation. The
custom application module in `langgraph_studio.py` is imported before the
locked in-memory runtime lifespan. At that pre-runtime boundary it derives
genuine system assistant IDs from the CLI-provided graph registry, removes
their persisted active/version rows so graph registration recreates them, and
demotes every other legacy `created_by=system` marker in both active assistants
and version history. This must happen before runtime 0.30.0 loads and purges
system-marked rows; a user application lifespan is too late. LangGraph executes
this file-backed custom app without first registering its module in
`sys.modules`; keep its annotations eager so dataclass processing remains
compatible with that loader, and preserve the direct file-loader regression
test. An empty graph registry or absent persistence file is a no-op, while
persistence parse/write errors fail startup closed. The harness requires
in-memory runtime 0.30.0 or newer, and a persisted store containing no
expected registered assistant row emits a drift warning so changes to
LangGraph's internal persistence contract are observable. With current
create/update writes and all legacy versions sanitized, ordinary
owner-scoped assistant version selection remains enabled.

**Dynamic workflow routes**: `workflows.py` owns the opt-in dynamic plane:
`POST /dynamic/perceive` (read-only preview), `POST /dynamic/execute` (compile and
execute), `POST /turns` with `dynamic=true` (compatibility turn seam), and the
bot-mode `POST /api/bots/{name}/workflow` adapter. Definitions and runs are
owner-scoped for real requests; request bodies cannot self-assert an owner.
The registry endpoint is a bounded projection, not a live connection claim.
`workflows.py` also owns step/cancel/approval/patch/replan/compensation,
append-only plan history, event/replay/projection/hydration, and fail-closed
durable-sink status.

The observability and control routes are declared **before `/{workflow_id}`** so
the single-segment `/simulate` path is not swallowed by the definition-id
catch-all: `GET /runs/{id}/history` (ordered replayable timeline with stable
1-based indexes a fork can quote back), `GET /runs/{id}/report` (measured
execution report plus provenance), `POST /runs/{id}/fork`,
`POST /simulate`, `POST /runs/{id}/suspend`, `.../resume`, `.../signals`,
`.../sweep-waits`, and `GET /system/executors`. Status mapping: a bad fork point
is 400 with the real reason, an unknown run is 404, resuming a run that is not
suspended is 409, and `ForkError` from an unregistered workflow is 404. The
report and the simulation payload carry **no acceptance verdict** — a completed
run is not a verified run, and a dry run is labelled `dry_run_simulation`.
`/system/executors` distinguishes `bound` from `domain_bound` so the opt-in
nature of the real model/tool/subagent executors stays visible. The local event
store and the wave executor's concurrency are both restart-recoverable and
process-local for one worker, not cross-process exactly-once coordination. See
`docs/DYNAMIC_WORKFLOWS.md` and the dynamic workflow tests before changing these
boundaries.

**Routers**:

| Router | Endpoints |
|--------|-----------|
| **Models** (`/api/models`) | `GET /` - list models; `GET /{name}` - model details |
| **Features** (`/api/features`) | `GET /` - UI capabilities: hot-reloaded agents, guarded browser, startup MCP tasks, and separate batch repository/worker states so history stays readable without a worker |
| **Console** (`/api/console`) | Read-only cross-thread observability for the current user (the data layer for an operations dashboard or external monitoring): `GET /stats` - headline counters (runs/threads/agents/tokens/cost); `GET /runs` - paginated run history joined with thread titles (per-run cost); `GET /usage` - zero-filled daily token series + per-model breakdown with spend. Queries `runs`/`threads_meta` directly as a reporting layer (no new `RunStore` methods); requires a SQL database backend — returns 503 on `database.backend: memory`. Real-cost estimation reads optional `models[*].pricing` (`currency`, `input_per_million`, `output_per_million`, `input_cache_hit_per_million`; `ModelConfig` is `extra="allow"`, so no schema change) and prices each run from its `token_usage_by_model` input/output split. Pricing is **cache-aware**: `RunJournal` accumulates prompt-cache hits from `usage_metadata.input_token_details.cache_read` into a sparse `cache_read_tokens` bucket key (also threaded through `SubagentTokenCollector` → `record_external_llm_usage_records`), and cache-hit input tokens are billed at `input_cache_hit_per_million` (omitted → billed at the miss price, a conservative upper bound). All priced models must use one currency; mixed currencies disable cost reporting and leave cost/currency fields null instead of producing invalid aggregates. Legacy rows fall back to run-level totals at `model_name`; unpriced models yield `cost: null` and cost fields are null when no pricing is configured |
| **Ops** (`/api/ops`) | Authenticated deployment metadata for operators and monitoring (behind default auth like `/api/features`, unlike public `/health`): `GET /version` - service name plus installed `alpha-harness` package version (legacy `alpha` fallback; `"unknown"` without package metadata); `GET /status` - `ok` plus process uptime, UTC server time, and the `GATEWAY_ENABLE_DOCS` flag (expected `false` in production); `GET /resources` - stdlib-only host CPU/memory/disk/load snapshot with `null` for anything the OS will not expose; `GET /advice` - resource-driven worker/model recommendation; `GET /runtime` - durable-runtime facts: the previous process's ordered-drain outcome plus the live connectivity reading and durable parked-session counts; `GET /network` - the connectivity reading on its own: four-state link state, **measured per-endpoint round-trip** (`latency_ms` is the mean over the *reachable* targets and is `null`, never `0`, when none answered), `allows_network_attempt` (read from the monitor's own decision, so `unknown` stays `true`), `observed_age_seconds`, and a `retry` block disclosing the automatic re-probe schedule; `POST /network/recheck` - the manual "Retry", which forces one measurement through `NetworkMonitor.recheck()` (the same serialized transition the poll loop runs, so a click cannot double-count hysteresis corroboration) and reports `performed:false` with a reason rather than a quiet success. Every block carries `reported` + a machine-readable `reason`, so "not measured" never renders as healthy. `GET /network` and `POST /network/recheck` share one builder with `GET /runtime`, so the two surfaces cannot drift. `GET /ops/context-windows` - every configured model's declared **input** context window and the usable window derived from it by subtracting `context_window.output_reserve_tokens` + `next_turn_reserve_tokens`, plus the larger-window escalation target `alpha.runtime.context_exhaustion.escalation_candidate` would pick. A model that declares no window reports `null` and is named in `notes` rather than silently reading as a small window; a deployment with `context_window.enabled: false` reports `reported=false` with `context_window_disabled` rather than an empty model list. `escalation_enabled` is `null` when no policy section exists at all, which is a third state distinct from "no larger model exists". Tests: `tests/test_context_usage_gateway.py`, `tests/test_context_window.py`; contract: `packages/harness/alpha/runtime/AGENTS.md`. |
| **Side effects** (`/api/side-effects`) | The effect journal and reconciliation queue over `alpha.runtime.side_effects`. `GET /summary` - counts by status and by level, the `unknown` count, and the oldest-`unknown` age (each `null`, never `0`, when unreported); `GET /` - a bounded page filterable by `status`/`level`/`tool_name`/`run_id`/`thread_id`/`user_id` with `limit` default 50 and `ge=1, le=500`, served **oldest-first** when `status=unknown` (and no other filter) so the queue is worked oldest-first, newest-first otherwise; `GET /{tool_call_id}` - one entry; `POST /{tool_call_id}/reconcile` - record `{verdict, reason}` (`confirmed_success`/`confirmed_failure`/`undetermined`; reason 1-2000 chars and required) on an `UNKNOWN` entry. **`/summary` is declared before `/{tool_call_id}`** for the same Starlette registration-order reason the skills and workflow collection routes precede their catch-alls, or `GET /side-effects/summary` is read as an id and 404s. Reads are authenticated and owner-scoped: an admin sees every row, a member sees only rows whose `user_id` matches theirs, rows with an empty `user_id` are admin-only, and a member with no caller id is 401. `POST .../reconcile` is admin-only (`is_admin_user`; a PAT never qualifies). Entries are an **explicit 18-key projection** (`_entry_to_wire`, never `__dict__`): SHA-256 digests only, so arguments and results never cross this API. Reconcile is single-shot - a settled or in-flight entry refuses with `409 side_effect_transition_refused` carrying the ledger's own words, so an `undetermined` verdict that reopens the entry is reported rather than assumed. Errors: `503 side_effect_store_unavailable` / `side_effect_store_unreadable` (a `database.backend: memory` deployment installs no ledger - this is "could not look", not "nothing recorded"), `404 side_effect_not_found`, `422 invalid_filter`, `403 forbidden`. Human surface: the `effects` workspace view (`frontend/src/lib/side-effects.ts`, `components/sections/EffectsSection.tsx`). Tests: `tests/test_side_effects_router.py`. |
| **Completion** (`/api/completion`) | The acceptance-contract ledger over `alpha.completion`. `GET /health` - engine posture (enabled/mode/`enforcing`, ledger readability, limits, policy id); `GET /checks` - the registered check names plus the scope/task/gate/event vocabularies a planner writes against; `GET /scopes` - newest-first summaries filterable by `thread_id`/`status`/`owner_id`; `POST /scopes` - open one (201, server-assigned owner); `GET /scopes/{id}` - the full live projection; `GET /scopes/{id}/contract|events|reconciliation|finalization/preview` and `GET /scopes/{id}/gates/{gid}/approval`; `POST /scopes/{id}/start|cancel|contract/revise|tasks|schedule|dispatch|recover|finalize`, `.../dispatches/{did}/confirm|launch-failed|return`, `.../tasks/{tid}/block|verify`, `.../gates/{gid}/run|approve|revoke|review`. Reads are owner-scoped (an empty `owner_id` is admin-only); **every mutation is admin-gated** (`is_admin_user`; a PAT never qualifies) because writing this ledger is what decides whether work counts as done. **The fixed routes are declared before `/scopes/{scope_id}`** — the same Starlette registration-order rule as `skills/{skill_name}`. Three honesty rules the tests pin: a worker's `return` is recorded as `returned` and never `verified` (only the task's own gates do that); `finalize` in observe mode reports `enforced: false` and leaves the status alone rather than claiming a completion it did not apply; and an unreadable store is `503` carrying its real reason, never an empty list. Errors: `404 completion_not_found`, `403 forbidden`, `422 invalid_input`/`contract_invalid` (with every finding) /`path_escape`, `409` carrying the engine's own code (`illegal_transition`/`scope_state_error`/`budget_exhausted`/`approval_required`…), `503` with the store error's code. Human surface: the completion workspace view. Tests: `tests/test_completion_router.py`. |
| **Predictive RSI** (`/api/rsi/predictive`) | `GET /status` - the advisory-forecast service's posture: mode, predictor identity and tier, and the ledger's standing, with `influences_selection` and `promotion_authority` both `false` in every shipped mode so "this cannot change a selection" is checkable rather than assumed. `GET /calibration` - whether forecasts have held up: Brier, calibration error, coverage, high-confidence failures. Two rules, each because the alternative produces a specific lie: an **unreadable** ledger is `503` carrying its own reason, never an empty `200` (a corrupt store and a store with nothing in it drive opposite operator decisions; a *missing* file is genuinely empty, so the two stay distinguishable), and **uncalibrated is reported as `null`** with `calibration_known: false` — never as a flattering number. Authenticated, `GET` only (a test pins that the router exposes no mutating verb), and all filesystem work runs through `asyncio.to_thread`. Installation-scoped self-improvement telemetry, so authenticated but not owner-partitioned. Contract: `packages/harness/alpha/rsi/predictive/AGENTS.md`; tests: `tests/test_rsi_predictive_router.py`. |
| **MCP** (`/api/mcp`) | GET /config - raw/masked; PUT /config - bulk; PATCH /config - toggle; POST /config/servers - add; PUT /config/server - replace; DELETE /config/servers/{server_name:path} - bodyless. Validate expanded, save raw; reload/reset; invalid -> 400. |
| **MCP Tasks** (`/api/threads/{id}/mcp-tasks`) | `GET /` - current user's durable tasks for one owned thread; `GET /{task_id}` - bounded result/input/status-error/cancellation-error detail, including cancellation attempt count, without remote task IDs or driver configuration |
| **Skills** (`/api/skills`) | `GET /` - list; `GET /{name}` - inspect; `PUT /{name}` - toggle; `POST /install` - install a thread-local .skill archive; `POST /install/upload` - admin-only multipart, authorized before parsing and capped at a 100 MiB file plus 1 MiB framing; `POST /reload` - invalidate process-local cache after trusted filesystem changes. The single-segment collection routes (`/tiers`, `/usage`, `/curator`, plus `/retrieve`, `/graph`, `/custom`, `/proposals`) are declared **before** the `/skills/{skill_name}` catch-all, for the same reason the dynamic-workflow control routes precede `/{workflow_id}`: Starlette matches in registration order, so a catch-all declared first swallows them and answers `404 Skill 'tiers' not found` — indistinguishable from an absent skill. `GET /skills/{name}` therefore returns a 404 carrying `code: "skill_not_found"` next to its string `detail`, so a caller can tell an absent resource from Starlette's route-level `{"detail": "Not Found"}` (which has no `code`). The `code` sits beside a plain-string `detail` because the frontend only renders a string `detail`. `tests/test_skills_router_route_order.py` pins the ordering and both 404 shapes. |
| **Subagents** (`/api/subagents`) | Admin managed-worker CRUD and listing. |
| **Bots** (`/api/bots`) | Registry CRUD, plus the per-bot model configuration: `GET /{name}/model-config` - stored block + resolved plan with per-value provenance, the precedence ladder, the server's limits, and `known_models` (the validated `models[]` names, sorted — the picker uses this rather than the wider `GET /api/models`); `PUT /{name}/model-config` (admin) - validate against `models[]` and store the canonical form; `DELETE /{name}/model-config` (admin) - clear the block so the bot inherits; `POST /{name}/model-config/preview` - validate + resolve a candidate **without saving**. A refusal is `422` with `detail={message, issues}`, each issue `{code, field, message, severity}`, so one save reports every problem. `model_config` is writable only through these validated routes, never generic `PATCH /{name}`. Contract: `docs/BOT_MODEL_CONFIG.md`. **Fleet working state** — two read-only routes declared before the `/{name}` catch-all: `GET /health/overview` (the heartbeat/liveness verdict per bot, keyed on the lowercased `bot_name`; a summary block read key by key so a missing state renders `null`, never `0`) and `GET /working` (which bots have a run in flight, with `run_id`, `thread_title`, live `elapsed_seconds` and the store's `status`/`model_name` verbatim). `/working` attributes only by `RunRow.assistant_id`; missing identities are counted as unattributed and non-run rows are excluded. Memory storage returns 503 because it cannot measure fleet activity; store read failures return 200 with `reported: false` and null counts. Status and timestamp normalization are shared with `routers/console`; duration is measured at read time. Tests: `tests/test_bots_working_runs.py`. **Per-bot relations** — two read-only `/{name}/...` routes, each `asyncio.to_thread` + `_validate_bot_name` and each modelled on `bot_projects`. They are **two-segment paths, so the single-segment `/{name}` catch-all cannot swallow them and their declaration order is not load-bearing** — unlike `/working`, `/organization-chart` and `/events`, which are single-segment and must precede it. `GET /{name}/groups` (`@require_permission("threads", "read")` **with `request: Request` in the signature** — without it `authz.py` resolves nothing and calls through unchecked; the `alpha.groups.service` imports are inner so the route import cannot load the intent table early) lists the rooms this bot appears in, per-room roster failures landing in `unreadable[]` instead of failing the read, `origins` split across the `direct` / `rule_matched` / `inherited` / `excluded` / `expired` buckets, `effective` saying whether it can take part, `path` = breadcrumbs + room, and `direct_count` travelling **beside** `effective_count` because "3 direct" over six visible members is a fabricated count; and `GET /{name}/companies` (no permission decorator, matching `companies.py` — tenancy is the boundary there — with `request: Request` used only to resolve `_owner(request)` imported from `routers/companies`) lists employment rows whose `agent_handle` matches, emitting the company summary plus the full `employment.model_dump(mode="json")` and `role`/`unit` dumps or `null`, and keeping a **terminated** employment so "no longer works here" stays distinguishable from "never worked here". `list_rooms()` failing is 503 and `CompanyStoreUnreadable` is 503 — **never an empty list** — and both envelopes carry `unreadable[]`, a checked-count and a `note`. Tests: `tests/test_bot_groups_route.py`, `tests/test_bot_companies_route.py`. |
| **Bot project membership** (`/api/bots/{name}/projects`) | Reverse project-crew read; returns role/status and names unreadable project reads rather than presenting them as non-membership. Requires `projects:read`; covered by `tests/test_bot_projects_route.py`. |
| **Integrations** (`/api/integrations`) | `GET /lark/status` - inspect managed Lark/Feishu CLI integration state, including `sandbox_runtime_mode` / `sandbox_runtime_ready` (whether `lark-cli` will actually be present in the sandbox at chat time); `POST /lark/install` - admin-only install of the official `lark-*` managed skill pack; `POST /lark/config/start` and `/lark/config/complete` - internal first-time Lark connection setup; `POST /lark/config/credentials` - atomically switch the caller's per-user Lark app after validating the new `app_id`/`app_secret` through the official CLI's live tenant-token probe, revoke/remove the previous OAuth tokens, and restore the prior credential tree if the switch fails; `POST /lark/auth/start` and `/lark/auth/complete` - browser device-flow user authorization without terminal access, with optional `domains` / exact `scope` for incremental permission grants. Config and auth flows carry a server-issued, per-user generation persisted under the credential lock; a rejected direct switch leaves the current generation unchanged, stale completions return 409, and browser re-registration uses the same token-clearing/revocation transaction as direct credential switches. |
| **Memory** (`/api/memory`) | `GET /` - memory data; `POST /reload` - force reload; `GET /config` - config; `GET /status` - config + data |
| **Uploads** (`/api/threads/{id}/uploads`) | `POST /` - upload files (auto-converts PDF/PPT/Excel/Word); non-mounted sandbox sync uses a non-releasing request lease; `GET /list` - list; `DELETE /{filename}` - delete |
| **Threads** (`/api/threads/{id}`) | `DELETE /` - remove Alpha-managed local thread data after LangGraph thread deletion; `POST /branches` - branch a completed assistant turn with a replay checkpoint; inherited titles take next-free displayed sibling suffixes, including explicit/renamed ones, while explicit titles stay unchanged. Durable `branch` admission rejects races. Workspace files are not checkpointed, so the branch only best-effort copies the current workspace when branching from the **latest** turn (`workspace_clone_mode="current_thread_best_effort"`); branching from an older/historical turn skips the copy (`workspace_clone_mode="skipped_historical_turn"`) so the branch never inherits files that only exist in a later timeline. Thread-scoped channels (`sandbox`, `thread_data`, `task_history`) are excluded from branches: the parent's `sandbox_id` binds path mappings and the release lifecycle to the parent's workspace, so the branch lazily acquires its own sandbox instead. Branch creation also seeds the new thread's run-event feed from the branch checkpoint's visible messages (`history_seed_mode` in the response): the thread feed reads run_events, not checkpoints, so without the seed the inherited history disappears from the UI after the branch's first run (#4380). Seeded rows are grouped into one synthetic run per inherited turn (`branch-seed-{thread_id}-{n}`, a new turn opening at every persisted human message, including an allowlisted hidden `ask_clarification` reply) because `run_id` is a turn identity to the feed's consumers, not a provenance tag: regenerating an inherited answer supersedes that row's whole `run_id` in `GET /messages/page`, so one shared id for the entire seed deleted the complete inherited history on a branch's first regenerate (#4458); `GET /goal`, `PUT /goal`, `DELETE /goal` - read, set, and clear the active thread goal; `POST /compact` - manually summarize older active context into `summary_text` and retain the recent message window, blocked while a run is in flight; unexpected failures are logged server-side and return a generic 500 detail |
| **Artifacts** (`/api/threads/{id}/artifacts`) | `GET /{path}` - stream regular text and binary artifacts with `FileResponse`, including byte-`Range` 206/416 behavior used by bounded text previews and media seeking; active content (`text/html`, `text/xml`, `application/xml`, `text/xsl`, any `+xml` type such as XHTML/SVG; `.skill` members too) is always forced as a download attachment to reduce XSS risk; `?download=true` still forces download for other file types. `PUT /{path}` atomically replaces an existing UTF-8 text file under `/mnt/user-data/outputs` when its expected SHA-256 still matches; active runs conflict, and non-mounted sandbox providers receive the same update under a request lease. The outputs-only rule is `path_utils.resolve_outputs_confined_path`, shared with IM-channel attachment delivery: it collapses `..` before the prefix check and re-checks the resolved host path against the resolved outputs root, since `resolve_thread_virtual_path` only confines to `user-data/`; a percent-encoded `..` or a symlink planted in `outputs/` must not reach a sibling `uploads/` file. Atomic replacement applies the existing POSIX permission handling when descriptor-based APIs are available and otherwise keeps the platform-native temporary-file permissions (Windows). |
| **Suggestions** (`/api/suggestions`) | `GET /config` - returns global suggestions config boolean; `POST /threads/{id}/suggestions` - generate follow-up questions; rich list/block model content is normalized and inline reasoning (`<think>...</think>`, including unclosed/truncated blocks from reasoning models like MiniMax-M3) is stripped before JSON parsing |
| **Input Polish** (`/api/input-polish`) | `POST /` - rewrite a composer draft before it is sent. This is a short authenticated `runs:create` LLM request using `input_polish` config; it does not create a LangGraph run, persist a message, or modify thread state. Shares the non-graph one-shot LLM path (`alpha.utils.oneshot_llm.run_oneshot_llm`) with the suggestions route so model build + Langfuse metadata + invoke stay in one place; validates the same stripped view of the draft it sends to the model, and preserves literal `<think>` substrings in the rewrite (`strip_think_blocks(truncate_unclosed=False)`) |
| **Thread Runs** (`/api/threads/{id}/runs`) | `POST /` - create background run; `POST /stream` - create + SSE stream; `POST /wait` - create + block. Before the first journaled run, seed an empty feed from a checkpoint so legacy checkpoint-only history keeps its order and visibility; skip absent checkpoints or populated feeds. `POST /regenerate/prepare` - prepare clean input + checkpoint metadata for regenerating the latest completed or interrupted assistant answer, carrying the latest non-empty thread title in graph input so resuming an older checkpoint cannot roll back a later manual rename (#4457); `POST /edit-regenerate/prepare` - prepare a checkpoint replay from the latest editable human turn with a replacement user message and edit replay metadata; it carries the current thread title the same way, but only when the replay base already has one — an untitled base belongs to a thread the title middleware has not named yet, so pinning the current title there would keep a name generated from the prompt the edit just replaced; `GET /` - newest 100 runs as an array; `GET /page` - keyset history page `{data, has_more, next_before_created_at, next_before_run_id}`; `GET /{rid}` - run details; `POST /{rid}/cancel` - cancel; `GET /{rid}/join` - join SSE; `GET /{rid}/stream` hides action/wait; GET action 405 pre-owner; POST needs `runs:cancel`; `GET /{rid}/messages` - paginated per-run messages `{data, has_more}`; `GET /{rid}/events` - full event stream; `GET /{rid}/workspace-changes` - workspace/output file change summary and optional diffs; `GET/POST /{rid}/artifacts/archive` - receipt manifest / bounded ZIP; `GET /../messages` - legacy thread message array; `GET /../messages/page` - backward thread-global `seq` history page with middleware/subagent-AI/successful-regenerate/edit-replay filtering and page-run-scoped feedback enrichment; subagent AI callbacks remain available through run events while parent `task` ToolMessages stay visible for card restoration; `GET /../token-usage` - aggregate tokens plus an optional `context_usage` block. Context usage approximately counts messages from the latest materialized thread state through `build_thread_checkpoint_state_accessor`, so full and delta checkpoint modes expose the same input. `context_usage.percentage` divides that count by the **usable input window** of the latest run's model (`models[].context_window` minus the `context_window` section's two reserves), and `context_usage.pressure` carries the derived band (`unknown` / `nominal` / `elevated` / `critical` / `over`), the headroom in tokens, and a machine-readable `reason`. `pressure` is `null` — never a default band — when the Gateway has no `context_window` policy, and a model that declares no window yields `band: "unknown"` with every derived field `null`, because an undeclared window is not a small one. See `packages/harness/alpha/runtime/AGENTS.md` for the occupancy/headroom contract and the exhaustion ladder. |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` - upsert feedback; `DELETE /` - delete user feedback; `POST /` - create feedback; `GET /` - list feedback; `GET /stats` - aggregate stats incl. per-category breakdown; `DELETE /{fid}` - delete specific. Create/upsert accept an optional `category` (correctness, completeness, grounding, format, latency, other; migration `0024`, NULL = uncategorized legacy rows); unknown categories 400. Offline export of categorized rows to versioned eval-case manifests: `scripts/benchmark/feedback_export/export.py` (`--input-json` rows + optional `--histories-json`, `--category`/`--rating` filters, `--output-dir` manifest.json; protocol `feedback-export-v1`) |
| **Runs** (`/api/runs`) | `POST /stream`, `/wait` - stateless runs requiring `runs:create`; optional body `thread_id` is owner-checked. Scheduled-task create/update/resume/trigger also require `threads:write` plus `runs:create`. `GET /{rid}/messages`, `/feedback` - run messages/feedback |
| **GitHub Webhooks** (`/api/webhooks/github`) | `POST /` - receive GitHub App / repo webhook deliveries. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`; exempt from auth + CSRF because authenticity is enforced by HMAC. The route is fail-closed: mounted only when `GITHUB_WEBHOOK_SECRET` is set, or when explicit dev opt-in `ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS=1` is set. Recognized events include `ping`, `issues`, `issue_comment`, `pull_request`, `pull_request_review`, and `pull_request_review_comment`; unknown events return 200 with `handled=false`. Fan-out runtime failures return 503, keeping the delivery recorded as failed for manual/API/scripted redelivery (GitHub does not automatically retry any failed delivery, 5xx included); permanent/non-retryable conditions such as `channels.github.enabled: false`, unknown events, malformed payloads, or unavailable channel service return 200 with a skipped/handled response. |
| **GitHub Event-Driven Agents** | Custom agents can declare a `github:` block in their `config.yaml` to bind to repos and event triggers. Webhook fan-out publishes one `InboundMessage` per matching binding to the channel bus; `GitHubChannel` routes those messages through `ChannelManager`. The response `dispatch` summarizes matched/fired/skipped agents. |

Thread identifiers use the shared `alpha.utils.thread_id` contract
`^[A-Za-z0-9_-]{1,64}$`. Caller-provided opaque IDs remain supported; UUIDs
are generated only for `None`, while explicit empty strings fail validation.
Gateway creation and state-producing request boundaries, embedded-client
entry points, filesystem/upload/event-store consumers, scheduled launches,
and the standalone Provisioner enforce the same contract before persistence
or workspace initialization. Route-addressable legacy IDs remain accepted by
pure reads and cleanup/control endpoints; deleting one best-effort removes
metadata and checkpoints but skips local filesystem cleanup, so the raw value
is never interpolated into a host path. New runs, workspace/sandbox
operations, and other state-producing mutations remain blocked.

**Message feed seq** (#4666): streaming `values` frames, `GET
/threads/{id}/state`, and `POST /threads/{id}/history` stamp serialized
messages with `additional_kwargs.alpha_seq` so clients can place
checkpoint-kept messages against the paged feed; the REST reads resolve the
store via `threads.py::_optional_run_event_store` (a feed-less deployment
still reads threads), and `services.py::normalize_input` strips the
server-owned key from client input (#4380). Mechanism and identity rule:
`packages/harness/alpha/runtime/AGENTS.md`.

**Workspace change review**: `packages/harness/alpha/workspace_changes/`
captures a pre-run and post-run snapshot of the thread-owned `workspace` and
`outputs` directories. `runtime/runs/worker.py` performs the filesystem scan via
`asyncio.to_thread` and writes a `workspace_changes` event with category
`workspace` when changes exist. Uploads are intentionally excluded. Text diffs
are size-limited; binary, large, and sensitive-looking paths are persisted as
metadata only. Internal process-feedback directories never count as changes:
the scanner's `EXCLUDED_DIR_NAMES` drops `BROWSER_FRAMES_DIRNAME` (transient
browser screenshots) and `TOOL_RESULTS_DIRNAME` (the tool-output budget
middleware's default externalization subdir, `constants.py` is the shared
source of truth for both writers and the scanner), and the worker threads the
configured `tool_output.storage_subdir` through the snapshot capture as an
extra excluded dir name so custom storage locations stay excluded too.

**Run delivery receipts**: the worker derives delivery requirements from the
run's workspace snapshots rather than a client request option (files
created/modified under `/mnt/user-data/outputs`, minus internal
process-feedback exclusions) and idempotently persists a run-scoped
`run.delivery` receipt before the terminal run status; missing or
unverifiable `present_files` coverage downgrades the run to error, while runs
without changed outputs keep ordinary chat behavior. Journal mechanics
(callback attribution, receipt idempotency and retries, orphan recovery):
`packages/harness/alpha/runtime/AGENTS.md`. Multi-worker deployments
require `run_events.backend: db` for shared, ordered delivery events; the
startup gate rejects process-local memory and JSONL event stores when
`GATEWAY_WORKERS > 1`.

**RunManager / RunStore boundary.** `RunManager` is the sole run-lifecycle owner; its durable state, lease, cancellation, replay, stream-retention, and recovery invariants live in `packages/harness/alpha/runtime/AGENTS.md`. Gateway callers must `await RunManager.get()`, and thread metadata becomes `running` only after `try_start()` admits the worker. Keep HTTP admission and presentation rules here: scope idempotency keys to authenticated owner + thread, validate checkpoint ownership before branching, reserve out-of-run checkpoint writes through `services.reserve_checkpoint_write()`, and have `/wait` drain the stream bridge rather than infer completion from task presence. Thread-goal continuation is bounded and evidence-based as described in `runtime/goal.py`; run-event changes update the producer, catalog, JSON contract, backend event-stream guide, and conformance test together.

**`@require_permission` owner checks and in-process composition.** `authz.require_permission`
binds the wrapped handler's arguments once and resolves **both** `request` and the
`owner_check` `thread_id` from that binding, not from `kwargs` alone. FastAPI dispatches
every route all-keyword, so an ordinary route is unaffected; the binding exists because
in-process composers call decorated handlers positionally. `routers/openai_compat.py` is
the live case: it calls `threads.create_thread` and `thread_runs.wait_run` directly, so
both `@require_permission` layers run on the compat call and that surface keeps exactly
the admission contract of the routes it delegates to. Reading `thread_id` from `kwargs`
alone made the owner check raise `ValueError` on a positional id, which 500'd
`POST /api/compat/openai/chat/completions` for every caller that names a `thread_id` - a
silently-dead delegation, and one line away from a bypass had the decorator been
"fixed" by skipping the check instead. `require_thread_owner` remains the required
counterpart for routes whose thread identity arrives in a body or query value.
Regression coverage: `tests/test_sec_audit_openai_compat.py`.

**Thread lifecycle**: Before changing branching, regeneration, edit replay, or
archive/search behavior, read [Thread lifecycle invariants](../../docs/THREAD_LIFECYCLE.md).
It owns lineage and settled-checkpoint rules, legacy fallback boundaries, archive
filtering before pagination, owner isolation, and activity-time preservation.

## Free local real-time voice contract

Voice is an end-to-end conversation path, not a second agent runtime. The browser owns
microphone/playback state; the Gateway owns VAD endpointing and local speech inference; the
final transcript enters the existing thread-run/SSE pipeline. Chat graph events and binary
audio remain separate transports.

`voice.routing.mode: local_only` is the default for TTS/STT. It must skip configured remote
T1 speech models and keyless/network T2 speech providers before T3; a missing local asset
fails honestly rather than silently sending audio to a cloud speech service. Only the LLM
may use a paid API. Browser `SpeechRecognition`/`speechSynthesis` are not fallbacks. The frontend document
must send `Permissions-Policy: microphone=(self)`, and the first explicit mic/speaker
control must prime one shared Web Audio output before automatic playback. Mic/speaker
access state and blocked-permission/device errors remain visible and honest. The Electron
shell must explicitly grant microphone-only capture to the exact local Alpha origin and
deny camera or mixed audio/video requests through both permission request/check handlers.

`alpha.multimodal.chain` remains the single T1→T2→T3 capability seam. Runtime speech models
are process-cached and bounded: `faster-whisper`, Kokoro, and Piper assets live under
`Paths`/`runtime_home()`'s `voice/models` tree, are installed by `make voice-setup`, and
are never downloaded implicitly by a request. Client TTS input is a safe voice ID, never
an arbitrary model path. `voice.enabled=false`, WebSocket authentication/origin checks,
`runs:create`, session/frame/utterance limits, and stale-partial suppression apply to every
speech operation including direct PTT transcription; PTT, wake-word, and real-time
conversation may share the socket only through explicit state transitions. A client
disconnect during either receive or event send is normal navigation: release the
process-local voice-session slot without logging an unhandled ASGI failure.

**TTS engine selection.** `voice.tts.engine` is `piper` (the real-time default) or
`kokoro` (more natural, much slower on CPU), and `run_t3` dispatches on that field. Measured
on the CPU-only host this was developed on (8 physical cores, no GPU provider), the same
~3.5 s sentence took Piper **1.7 s idle / ~4.3 s under memory pressure** and Kokoro
**~70 s** (~18x slower than real time), and Kokoro's ONNX decoder did not improve from 2 to
16 threads. The default is therefore Piper — a conversation loop that cannot keep up with
its own speech is not a conversation — and the docs state the measurement instead of
calling Kokoro "CPU real time". Never re-default to Kokoro without re-measuring on the
deployment host, and never quote a latency number without the host it came from: the same
build varied 2.5x purely from memory pressure. The two engines have
**disjoint voice-ID namespaces** — Kokoro names (`af_bella`) and Piper ids
(`en_US-lessac-medium`) are both `^[A-Za-z0-9_-]{1,64}$`, so only the engine choice keeps
them apart. The router therefore resolves the voice *and* the model path from the engine:
`engine: kokoro` serves `tts.kokoro_voice` + `tts.kokoro_model_path`, `engine: piper` serves
`tts.voice` + `tts.model_path`. Never let a Piper id reach Kokoro or a Kokoro name reach
Piper; a resolution failure must surface as an honest skip row, not a silent cross-engine
fallback. `GET /api/multimodal/capabilities` lists **both** engines as separate T3 rows so
one being unavailable is visible without disabling the other.

**Wake word.** openWakeWord ships in the `voice` extra and runs on-device through its ONNX
path only. It declares a Linux-only `tflite-runtime` dependency with no cp312+ wheel; the
workspace `override-dependencies` in `backend/pyproject.toml` constrains that transitive
requirement to interpreters that have wheels, so one lock resolves on every platform.
Remove that override and Linux deployments on Python 3.12+ stop resolving at all. `armed`
is true only once frames have actually flowed — a silent session is never reported as
wake-armed, and a missing engine reports `not_installed` instead of a faked armed state.

`make dev` runs a plain `uv sync` unless `UV_EXTRAS=voice` is set, which silently *removes*
every speech package on restart; the setup and docs own that instruction, and
`make voice-verify` restates it.

**Verification layers.** `make voice-setup`/`make voice-verify` prove *assets* (package installed,
model files present, checksummed, right size; no weights loaded). Behaviour is proven separately:
`backend/tests/test_wakeword_e2e.py` covers wake-word honesty against the real engine where it is
installed (honest `not_installed` otherwise), and `tests/test_kokoro_tts_engine.py` covers the
second TTS engine's dispatch, argument mapping, and honest skip rows without loading weights. A
green asset check is never evidence that a turn is conversational — the latency numbers in
docs/VOICE_CONVERSATION.md are measurements from one host, not a guarantee.

The public nginx configurations must forward Upgrade/Connection for
`/api/multimodal/voice` before their generic `/api/` locations. Frontend tests must pin
single-microphone ownership, interim/final protocol handling, automatic submission through
`sendMessage`, sentence-level speech queueing, per-message/manual playback sharing that
same queue, cancellation, and resume-after-playback. Backend tests pin local-only tier
enforcement, model cache reuse, endpointing, authorization, and bounds. Setup and
operations are documented in `docs/VOICE_CONVERSATION.md`; tests are
`test_multimodal_chain.py`, `test_multimodal_router.py`, `test_multimodal_realtime.py`,
the local speech runtime tests, and `test_setup_voice_models.py`.

## Alpha peer-network routes

`routers/peer_network.py` owns the separate Alpha-to-Alpha session. Management
routes use `threads:read/write`; only exact Agent Card, pairing, inbound, and
WebSocket paths are public. Public pairing/inbound requests must validate the
peer token inside `alpha.peer_network.service`; never replace that with browser
session auth or trust a sender/owner field. The service starts before the
autonomy supervisor and stops after it, with the bounded shutdown hook.

Those two public POST routes are also the only body-capped ingress in the
Gateway, and the cap cannot live in the handler: FastAPI materializes a
Pydantic body model *before* it resolves any route dependency, so a guard inside
a route runs after the whole body is already buffered.
`public_body_limit_middleware.PublicBodyLimitMiddleware` therefore wraps the
ASGI `receive` channel and is registered by `create_app()`. The ceiling
(`_MAX_PUBLIC_BODY_BYTES`) and the exact path set
(`_PUBLIC_BODY_LIMITED_PATHS`) are owned by `routers/peer_network.py` and
imported by the middleware, so the documented 512 KiB limit and the enforced
one cannot drift. `_check_public_body_size` remains the
*declared*-`Content-Length` fast path, not a bound: that header is
caller-controlled and absent for `Transfer-Encoding: chunked` and ordinary
HTTP/2 bodies. The middleware raises `HTTPException(413)` rather than a
bespoke error so Starlette's `ExceptionMiddleware` renders it and FastAPI's
body-parse wrapper cannot flatten it into a misleading 400. The path set stays
exact — a prefix would silently extend the ceiling to future routes under the
namespace. Regression coverage:
`tests/test_sec_audit_peer_public_body.py`.
Discovery and GitHub cards are untrusted metadata; pairing and delivery are
separate states. Tests: `tests/test_peer_network.py`,
`tests/test_auth_middleware.py`, `tests/test_csrf_middleware.py`, and
`tests/test_gateway_lifespan_shutdown.py`. Operations:
`docs/ALPHA_PEER_NETWORK.md`.
