### System One indexed computer control (`computer_use/system_one_policy.py`)

The Windows desktop route is optional and disabled by default. With
`system_one.enable_computer_action` enabled, the policy projects UI Automation
elements to bounded semantic fields and operation-specific indexes; it never
sends coordinates, bounding boxes, selectors, UIA handles, typed text, keys or
hotkeys to System One, and the model returns an index only for element-targeted
operations. The model-facing tool re-observes the accessibility tree, compares
the target's semantic identity, then resolves fresh geometry locally before the
sentinel-guarded dispatcher. Keyboard operations require exactly one freshly
observed focused control; `TYPE_TEXT` rechecks focus after the guarded click. A
bounded Laya projection omitting executable elements is rejected before the
operation question; partitioning is reserved for a complete table's target
head. `None`, malformed, low-confidence, truncated, shadow-mode and stale
decisions dispatch nothing. Tests: `tests/test_system_one_computer.py`.

### Request Trace Context (`packages/harness/alpha/trace_context.py`)

`X-Trace-Id` / `alpha_trace_id` is the request-level correlation id and the
`ContextVar` is its only source: every path that reaches a run binds one first,
downstream treats it as a plain `str` with no `if trace_id:` guards, and one
unit of work binds one — never a poller loop. Every other carrier is a derived
output, never read back as input: a caller-sent id is replaced so the persisted
run cannot disagree with header and logs. Use `resolve_trace_id(*carriers)` and
`ensure_trace_context(trace_id)`; never open-code a fallback chain;
`request_trace_context` (HTTP) deliberately does not inherit;
`logging.enhance.enabled` gates log output only, not the id, header or run
metadata. Full contract (binders, crash-recovered-launch divergence, per-step
client binding, CORS/`STARTUP_ONLY_FIELDS`, suites):
[backend/docs/STREAMING.md](../../../backend/docs/STREAMING.md#request-trace-context-packagesharnessalphabetrace_contextpy).

### Managed Lark CLI credentials (`integrations/lark_cli.py`)

App registration and direct app switching replace the per-user Lark credential tree
transactionally. Clear the old OAuth data *before* `lark-cli config init`: on
Linux that command writes the new app secret into the file-backed keychain under
the data directory, so clearing afterward would leave `config.json` with a
dangling keychain reference. The transaction snapshot still supplies previous
OAuth data for logout and restores the complete old tree if a switch step fails.

### Browser Progress Screenshots (`community/browser_automation/`)

Hidden per-action progress frames use JPEG at quality 80 (bounded cost vs lossless
PNG); the explicit `browser_screenshot` tool stays PNG because it is a
user-requested artifact. New automatic capture entry points must reuse the shared
progress encoding in `tools.py` so byte encoding and `.jpg` suffix cannot drift.

### AgentEye live-source adapter (`community/agent_eye/`)

The harness pins AgentEye to an immutable upstream commit (not a branch, legacy
project name or unpinned package) and never constructs or calls upstream
`AgentSearchLite`; its unauthenticated HTTP API and MCP entry points are
excluded. Curated adapter surface, operator allowlist, `allow_fragile_backends`
policy, bounded worker queue, dedupe, provenance, `no_evidence` honesty,
`citations_verified` semantics and the `use_for_deep_research` DDGS fallback:
[backend/docs/AGENT_EYE_RESEARCH.md](../../../backend/docs/AGENT_EYE_RESEARCH.md),
[docs/DEEP_RESEARCH.md](../../../docs/DEEP_RESEARCH.md).

### Embedded Client (`packages/harness/alpha/client.py`)

`AlphaClient` provides in-process access without HTTP or a FastAPI dependency. It
shares Gateway's `alpha` modules, config files, data directories and response
schemas for compatible consumers. Method-to-Gateway-endpoint parity, per-mode
delivery contract and the full streaming design (parallel paths, LangGraph
`stream_mode` semantics, per-id dedup invariants, trace binding):
[backend/docs/STREAMING.md](../../../backend/docs/STREAMING.md).

- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — yields `StreamEvent` over `stream_mode=["values", "messages", "custom"]`; `values` never re-synthesizes delivered AI text, `messages-tuple` AI text is a **delta** (concat per `id`), tool calls/results are emitted once each, `custom` is dual-emitted through `alpha.utils.custom_events`, `end` carries cumulative `usage` counted once per message id.
- **Custom-event invariant** — production Alpha emitters must use `emit_custom_event` / `aemit_custom_event`, never call `StreamWriter` alone. Every built-in payload must carry a non-empty string `type`; typeless payloads remain writer-only and are absent from `astream_events`. The writer runs first and stays authoritative for Gateway, Web UI and embedded-client compatibility; callback dispatch is best-effort and must not break that path. Async graph hooks must await the async helper, not dispatch synchronously on a running event loop.
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`; supports `checkpointer` for cross-turn state; `reset_agent()` forces agent recreation (e.g. after memory or skill changes).
- Cache graphs by effective storage `user_id` in every auth mode because prompts and middleware bind user SOUL, skills, and storage. `stream()` must materialize it before worker or isolated-loop boundaries.

**Tests**: `tests/test_client.py` is offline; `TestGatewayConformance` parses every
dict-returning client method's output through its Gateway Pydantic model (a missing
required field raises `ValidationError` in CI). `tests/test_client_live.py` needs
root `config.yaml`, real credentials and opt-in (`make test-live` /
`ALPHA_RUN_LIVE_TESTS=1`), may create local sandboxes/artifacts/files, is marked
`live`, and is excluded from `make test` and default CI.

### Agent Presets (`config/agent_preset_config.py`)

Named per-session toolset bundles requested per run via the `agent_preset`
configurable/context key without a restart: `standard` is the identity preset
(today's behavior), `minimal` the cheap one (no MCP catalog, no subagents, no
`ask_clarification`). Operators add presets via `AppConfig.agent_presets` (see
`config.example.yaml`); unknown names warn and fall back to `standard` so a typo
cannot break run admission. A preset may only narrow: `tool_groups` override the
custom agent's groups, `include_mcp=False` drops the MCP catalog,
`subagent_enabled=False` forces delegation off, `disabled_tools` extends the
non-interactive filter, and `allow_update_agent=False` withholds `update_agent`. The
resolved preset is recorded in run metadata (`agent_preset`) and the assembly
descriptor (`effective_policies["agent_preset"]`). The bootstrap agent has its own
fixed minimal graph and ignores presets. Tests: `tests/test_agent_presets.py`.

### Self-inventory (`workflow/registry/`, `intelligence/self_inventory.py`, `knowledge/code_index.py`, `ops/config_diagnosis.py`)

Eleven read-only registries behind one `list` / `describe` / `health` protocol:
`identity`, `tools`, `skills`, `mcp`, `models`, `bots`, `commands`,
`capabilities`, `engines`, `wiring`, `memory`. **One source of truth per fact,
never a re-derivation.** `engines` and `wiring` read the generated
`contracts/feature_manifest.json` through the single `manifest_source` loader;
`models` reads the live `AppConfig`. A registry that recomputed any of those
counts itself would be a second, unreviewed answer to a question
`scripts/check_generated_drift.py` already settles — the drift class this repo has
paid for five times (89 → 97 → 99 engines, 127 → 130 → 134 tools, 55 → 57 → 60 → 61
routers, 8 → 9 loops).

- **Availability is what the source declares; health is what we probed.** A
  *declared* model is `available` with `health="unverified"`; an *enabled* MCP
  server likewise. Nothing here executes what it lists, so `health` is
  `unverified` on every descriptor and `version` is `None` unless a source declares
  one (only the runtime identity row does). Collapsing "configured" into "working"
  is how a status line ends up asserting something nobody checked.
- **A broken source must not read as an empty one.** `list()`/`describe()` raise
  `RegistryUnavailable` with the real exception text; the inventory converts that
  into `status="unavailable"` with `count: null` — **never `0`**. "I could not
  look" and "I looked and found nothing" lead to opposite decisions.
- **Availability is per-entry, not per-registry.** `CommandRegistry` gates on
  `has_handler`, because a catalog row with no handler returns
  `UNIMPLEMENTED_STATUS`, never `success` — 461 registered commands and 54
  dispatchable ones are different claims. `BotProfileRegistry` passes
  `include_archived=False` **explicitly**: `list_bots` defaults it to `True` (right
  for a roster view, wrong here), and `retire_bot` is a soft delete, so listing an
  archived Bot advertises a worker that is gone.
- **`EngineRegistry` falls back to a declared submodule.** A namespace has no
  `__init__.py` of its own, so `find_spec("alpha.x")` returns `None` for a healthy
  namespace. Probing only the id would report every namespace as unavailable.
- **`WiringRegistry` namespaces ids per class** (`router:` / `middleware:` /
  `loop:`) because the three sections can legitimately contain the same token, and
  a flat id space lets one class shadow another.
- **The symbol lookup is query-driven, not a prebuilt index.** A whole-repo index
  was built first and was unusable: 195s to build, and its cap truncated
  *alphabetically* inside `alpha/swarm/` so `get_available_tools` returned zero
  results from an index reporting itself healthy. Completeness now depends only on
  whether the query string occurs in a file — bytes prefilter, then AST-parse only
  real hits — and every response carries a `coverage` block. It returns
  names/signatures/`path:line` but **never a body** (67.8% reuse from an interface
  map vs 29.2% from a source dump); TypeScript rows are `extraction="regex"` and
  say so.
- **`config_diagnosis` is read-only and has no `apply`.** `config.yaml` and
  `extensions_config.json` are already API-writable under the dual write locks; a
  model-writable path would be a fourth writer holding the agent's authority rather
  than the operator's — the same property as "a Bot may configure itself, but may not
  widen itself". A missing API key reports the **variable name only**. A disabled
  capability is `info`, because a deliberate operator choice must not train
  operators to ignore warnings.

The model surface is the single `alpha_capability` tool; it is a **lead-agent**
tool and is deliberately absent from `SUBAGENT_TOOLS`. HTTP: `GET
/api/intelligence/inventory`, `GET /api/intelligence/inventory/status`, and the
widened `GET /api/workflows/system/registries` (whose old bare `[:100]` slice
silently truncated `commands`/`engines` and now reports `returned` + `truncated`).
Tests: `backend/tests/test_self_inventory_plane.py`; operations:
[docs/SELF_AWARENESS.md](../../../docs/SELF_AWARENESS.md).

### Invariant Registry (`diagnostics/invariants.py`)

Fail-loud runtime self-checks: `InvariantRegistry` runs named `InvariantCheck`s and
raises `InvariantError` (naming the breaching check) instead of limping on, with
allow/blocklist substring filters; stdlib-only so factories can import it without
cycles. `_complete_assembly` in `agents/lead_agent/agent.py` gates every
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

### Company OS (`packages/harness/alpha/company_os/`)

Durable, multi-tenant autonomous organizations **indexed over** the real
subsystems rather than duplicating them: employees are `alpha.bots` profiles,
projects are `alpha.projects` rows, work items are `alpha.kanban` cards, rooms are
`alpha.groups` rooms, and schedules are indexed but never fired by the loop. The
owner is server-assigned; an ownerless read of another tenant's company is `None`,
never 403. Three properties decide every review: **index never duplicate**,
**measured or `None`** (`MeasurementBasis` travels beside every figure), **a
proposal is never an action**. The `company_operations` supervisor loop is
**default off** and its tick is model-free, so enabling it spends no tokens.
Boundary: this package **cannot enumerate projects** — `get_project_repo` lives
in `app.gateway.deps` and the harness must not import `app.*`, so the Gateway
passes rows in. Full contract, five loop breakers, durability rules:
**[packages/harness/alpha/company_os/AGENTS.md](company_os/AGENTS.md)**. Tests:
`tests/test_company_os_core.py`; frontend `frontend/src/lib/company.test.mjs`.

## Swarm v2 runtime contract

`alpha.swarm` is the lifecycle owner for autonomous swarm plans. Plans are explicit
DAGs with atomic JSON checkpoints, ordered JSONL audit events, bounded blackboard
messages, lease-fenced task attempts, retry/backoff state, measured token/tool
budgets, deterministic reflection, bounded consecutive-failure circuit breaking
and optional evidence-backed consensus. A late worker result is accepted only
while its lease still matches; cancellation, budget exhaustion, dependency
failure and pause/resume are separate states. `auto_replan` is a bounded
deterministic repair-task expansion redirecting blocked dependents — it never
converts a failed task into success. The synchronous model tool may use the
runner's daemon-thread fallback when no event loop is available.

`SwarmCoordinator` serializes state transitions and restores interrupted plans as
paused with fresh-lease requirements; `SwarmScheduler` validates DAGs before
mutation, assigns lease ids, renews live attempts, applies backoff and fences stale
results; `AsyncSwarmRunner` records measured usage, publishes bounded task-result
messages, enforces the consecutive-failure circuit and reports
`budget_exhausted`/`stalled` rather than fabricating completion. `SwarmAggregator`
keeps execution status separate from acceptance: duplicate voters and unverified
evidence cannot produce a clean approval.

Gateway swarm reads and mutations are owner-scoped (`SwarmPlan.owner_id`); the
`swarm` tool derives the owner from request context and cannot self-assert a
verified blackboard message. Model-visible blackboard content is bounded data in the
human input channel, never an elevated system instruction. Worktree paths are
relative metadata; the worker always uses the server project root.
`communication.py`, `consensus.py`, `reflection.py` are harness-layer and never
import `app.*`.

The persistence adapter is deliberately local and process-scoped: atomic and
restart-recoverable for a single Gateway, never a substitute for a shared SQL
lease repository in a multi-worker deployment — never call local JSON checkpoints
cross-process exactly-once. v2 API/tool surfaces expose metrics, task leases,
messages, leader election, consensus and acceptance-verdict fields so operators
can distinguish execution success from acceptance success. Regression coverage:
`backend/tests/test_swarm_engine.py`, `test_swarm_advanced_features.py`,
`test_swarm_worker_execution.py`, `test_swarm_v2_runtime.py`.

### Adaptive mode selection, deliberation, and failure-aware telemetry

`SwarmMode.AUTO` is resolved by `alpha.swarm.strategy.resolve_strategy`, never
hardcoded. Precedence is **explicit > topology (measured DAG shape) > heuristic
(goal text) > fallback**; an explicit mode is honored untouched — a named
topology is a requirement, not a suggestion. `SwarmBenefitEstimator.estimate`
stays byte-identical; structural layers are added on top, never substituted.

**The plan and its recorded strategy must agree.** `plan.metrics["strategy"]` is
written for *every* plan and `plan.mode` must equal `strategy["mode"]`. The first
pass cannot see structure — no plan exists yet — so it routes on goal text; that
estimate then *builds* a graph whose own shape can route elsewhere (a heuristic
picking `parallel` for a phased goal still produces a research/architect/review
chain, measuring `hierarchical`). `SwarmTaskDecomposer._settle` therefore
rebuilds the plan from the measured route: bounded, each rebuild adopting the
mode just resolved, so the pair agrees by construction on exit. Never "optimize"
this away — recording the second answer without rebuilding leaves a plan
labelled `parallel` while its own metrics say `hierarchical`.

- `topology.route_topology` never reads goal text and never infers
  `ENSEMBLE`/`DEBATE` from shape — redundancy is a claim about *intent* a graph
  cannot answer, so those stay semantic or explicit. `max_concurrency` is
  accepted and deliberately unused: routing is about shape; capacity belongs to
  `planner.score_plan`, where makespan is scored.
- **The single-sink fan-in rule must precede the coupling rule.** A reduce is
  the densest acyclic shape there is, so `coupling >= COUPLING_THRESHOLD` is a
  measurement artifact for `depth == 2 and leaf_count == 1`, not a structural
  finding; the coupling verdict should catch a genuine mesh like K2,2 (two
  sinks). Getting the order wrong hands a 2-item batch a 5-node
  leader-sequenced pipeline. `COUPLING_THRESHOLD = 0.30` — a DAG's acyclic
  ceiling is ~0.333 at K2,2; 0.50 would be a dead rule.
- `planner.score_plan` reports `exposed_width` (instant parallelism, capped by
  the plan's own concurrency) separately from `lower_bound_rounds` (idealised
  makespan floor): a 16-way fan-out on 4 slots exposes the same width as a 4-way
  fan-out but needs 4 rounds, and conflating them scores a wider plan as more
  parallel than it can be.

Complexity tiers (`DIRECT`/`SIMPLE`/`ADAPT`/`MEDIUM`/`FULL`) gate machinery
rather than enabling it globally: candidate planning and stigmergy at `MEDIUM`+,
deliberation at `FULL`. `StrategyResolution` gates compare `tier.rank`, **never
the enum member** — `ComplexityTier` is a `StrEnum`, so
`tier >= ComplexityTier.FULL` compares strings and `"full" >= "medium"` is
`False`, silently inverting every gate on the most complex plans.

`deliberation.run_deliberation` applies Wald's sequential test over the same
evidence `evaluate_consensus` sees. With the defaults (`target 0.75 ± 0.10`,
`alpha 0.05`, `beta 0.10`, `max_rounds 5`) ~11 unanimous approvals accept the
hypothesis, 3 unanimous rejections the alternative; anything less reports
`unresolved` with the reason. **A small undecided panel must say "unresolved",
never "approved".**

- Guards may only **block**. Domination, sycophancy, evidence-free approval and
  any disagreement between the sequential test and the explicit policy downgrade
  a result to `manual_review`; nothing can raise a refusal to an approval.
  `SwarmAggregator._apply_deliberation` attaches the full report either way so
  the disagreement stays visible; a malformed payload discloses an `error`
  instead of raising.
- Domination is measured as `max_share × voter_count` (1.0 = an even panel), not
  raw share — a raw share flags every healthy two-voter panel, which holds 50%
  each by definition.
- Sycophancy counts only flips that moved *toward* the prior plurality and
  added no new evidence.

`stigmergy.StigmergicTraceStore` is **advisory context and never a gate**: it
ranks what later workers see and decides nothing, bounded in entries, payload,
key and provenance; amplification is sub-linear and capped, so one worker cannot
farm its own strength by repeating a deposit. `decayed_at` stays separate from
`updated_at` because every read path evaporates: measuring decay from
`updated_at` without advancing it re-applies the window on every read, killing a
popular trace in proportion to how often it was consulted.

`SwarmTelemetry` reports `None`/`unknown` wherever a signal is genuinely not
measurable — a plan with no declared tool outcomes reports
`tool_error_rate = None`, not a reassuring `0.0`. `deliberation_rounds_saved` is
a **round count**; no round-level token meter exists, so never convert it into a
cost figure.

The model surface is the existing `swarm` tool with three new **actions** —
`strategy` (the recorded choice, never a re-derivation that could disagree with
the plan), `telemetry`, `trace` (deposit when `message` is set, otherwise list) —
never new tools, so `BUILTIN_TOOLS`, `contracts/feature_manifest.json` and the
capability counts are unaffected. Regression coverage: `test_swarm_engine.py`
(pinned estimator output), `test_swarm_adaptive_topology.py` (topology, planner,
tiers), `test_swarm_deliberation.py` (SPRT, guards),
`test_swarm_stigmergy_telemetry.py` (traces, telemetry, coordinator wiring).


## Dynamic workflow plane

`alpha.workflow.runtime.DynamicWorkflowEngine` and
`alpha.orchestrator.loop.ExecutionKernel` own typed workflow graphs, waves,
conditional routing, bounded loops, retries, budgets, approval waits, patch OCC,
replanning, replay and compensation. The opt-in
`alpha.orchestrator.dynamic_service.DynamicWorkflowService` composes the existing
intent, capability/resource, bot, DWE and event seams; it never replaces
`RunManager`, bot/group lifecycle ownership or the scheduler. Hosts invoke its
synchronous child execution through their own worker boundary and must not treat
cancelling an await as cancelling a running node. A `DynamicWorkflowBridge`
bound to the Gateway `ExecutionKernel` dispatches every wave (and runtime replan
patches) through that kernel's per-run claim; an explicitly unbound bridge
disables registry fallback so a live digest executor cannot masquerade as a
bound host runner.

Gateway routes are `POST /api/workflows/dynamic/perceive`,
`POST /api/workflows/dynamic/execute`, the `/api/workflows/turns` compatibility
seam (`dynamic=true` opts into the full loop), and `POST /api/bots/{name}/workflow`.
Definitions/runs are server-owner-scoped; request context cannot self-assert
ownership. Registry discovery is a bounded read-only projection, never proof a
provider is connected; missing executors, skills, MCP connections, compensation
callbacks or verification evidence fail or disclose honestly.

The default `alpha.local.digest` executor is a `local_digest_projection`: it hashes
inputs to exercise graph mechanics, keeps `acceptance_passed=false`, never reported
as domain-task acceptance. Real domain work runs through
`alpha.orchestrator.domain_executors` (`alpha.local.model`, `alpha.local.tool`,
`alpha.local.subagent`) — model factory, guarded `ScriptDispatcher`, real
`SubagentExecutor` — **opt-in** via `bind_domain_executors()` and never bound at
import, because they spend money and reach the network; the async-to-sync bridge
refuses when called from a thread with a running event loop rather than
deadlocking. Recurring prompts disclose the missing scheduler handoff rather than
creating a second cron owner.

**Bounded execution and concurrency.** `WorkflowNode.timeout_seconds` is
ENFORCED through `alpha.workflow.execution.run_with_deadline`; a missed deadline
fails the node with the measured overrun and **fences** the in-flight call
(CPython cannot kill a thread), discarding its late result rather than adopting
it; a `MAP`/`REDUCE`/`RACE`/`QUORUM` child inherits the bound **per child**, not
per fan-out. Wave concurrency is **opt-in** via `policies.max_concurrency`: the
engine dispatches a wave on a bounded pool so the scheduler's disjoint
`write_scope` buys real overlap, but an undeclared workflow runs sequentially —
parallel waves reorder the event log and would silently change every existing
definition's replay, projection and hydration. Shared run/graph bookkeeping is
read-modified under process-wide `_STATE_LOCK` with the executor call outside
it; overlapping write scopes defer the later node and
`wave_write_scope_serialized` names the pairs. Measured time is **journalled**
as `node_timed` / `wave_dispatched` events, never in `run.metrics`: replay
requires `run.metrics` reconstructible from the log, and a duration is not
re-derivable from the events that recorded the work.

**Executor-free node kinds.** `CHECKPOINT` (content-addressed state digest),
`GOAL_GATE` (safe-AST acceptance criteria — an unevaluable required criterion
counts as NOT met; the patch validator already refuses to remove or replace it),
`HANDOFF` (real-state contract, `decisions` stays empty), `WAIT` (clamped timer
reporting measured sleep), `EVENT_WAIT` (parks the run in `WAITING_EVENT`),
`PARALLEL` (named member group, all-or-nothing), `SUBWORKFLOW` (a real child run;
only a `completed` child is adopted, self recursion refused) complete on a
measurement the runtime takes itself.

**Waiting, signals, and suspension.** `signal_event` releases only nodes
registered for that exact event and returns them to `READY` (the scheduler admits
only `PENDING`/`READY`, so a node left `WAITING` could never re-dispatch); an
unmatched signal changes no state. `sweep_expired_waits` fails expired waits with
measured age, then applies the same fail-closed policy a wave does, so a wait
nobody satisfies cannot leave a run non-terminal. `suspend_run`/`resume_run` park
and release without inventing a terminal outcome; stepping a parked run returns
its real status.

**Fork, dry run, templates, proposals.** `alpha.workflow.time_travel` forks a new
run from a point in event history, inheriting completed work rather than repeating
it (each fork gets its own workflow id and graph; the source is never mutated);
`simulate_run` dry-runs a graph on a **throwaway** engine so it cannot touch the
caller's definitions, runs, durable sink or budgets — labelled
`dry_run_simulation`, zero tokens, no acceptance verdict, and a parked graph says
so. `alpha.workflow.templates` enforces `draft -> verified -> promoted`, where
`verify` re-checks that a run completed, its graph is structurally identical, and
every succeeded node carried evidence. `alpha.workflow.self_improvement` only
**proposes**: nothing in it mutates a run, graph or template, every suggestion
cites its measured signal, confidence derives from sample count, an unevidenced
completion is reported as `unproven` rather than folded into a success rate, and
the parallelisation / wave-underuse signals require a real independent sibling so
they do not fire on every serial chain.

Workflow events are appended to the durable JSONL sink before listeners run; the Gateway sink is fail-closed, redacts event payloads, validates paths/schema, and exposes durability, projection, hydration, replay and append-only plan history. That local adapter is atomic and restart-recoverable for one Gateway process, not a shared multi-worker lease/exactly-once repository: never claim cross-process exactly-once execution; wave concurrency is likewise process-local. Full operations, API examples and regression suites:
[`docs/DYNAMIC_WORKFLOWS.md`](../../../docs/DYNAMIC_WORKFLOWS.md).

## Guarded source auto-update contract

Local source checkouts may opt into the Phase-2 update engine in
`backend/packages/harness/alpha/evolution/update_engine.py`. It mutates only a local
Git source checkout: a published GitHub release (or an explicitly configured
development branch) resolves to an immutable commit, worktree and remote identity
are checked, the update is fast-forwarded behind a backup ref, success is recorded
only after restart/health checks. The engine is a separate trust boundary and
argv-only: it rejects dirty/diverged worktrees, requires manifest-matching GitHub
remote, allowed branch, fast-forward ancestry and a stable target ref, optionally
verifies signatures, snapshots `config.yaml`/`extensions_config.json` outside Git,
publishes a cross-process maintenance barrier that closes new run admission, invokes
only argv-based hooks, restores declared dependencies on rollback, records honest
pre-mutation recovery and never accepts a client-supplied URL/ref. Manual
confirmation never bypasses `canApply` or other safety gates. `release_check.py`
remains the stable public state/HTTP contract; the Gateway only queues a detached
apply; PATs plus auth-disabled/internal identities never receive admin capability.
State/history/lock files live under `runtime_home()` and never contain GitHub
tokens or secrets. Docker images, Helm releases and the Electron installer remain
orchestrator-owned, never updated in place.

The committed `config/update-policy.json` is a disabled, credential-free template;
unattended deployments keep a mutable operator policy outside the clean checkout
selected with `ALPHA_UPDATE_POLICY_PATH`, and `main` branch tracking is an explicit
operator choice, never the default. Application requires both policy flags plus the
startup-scoped `autonomy.loops.self_update.enabled` setting; the `self_update` loop
registers in `AutonomySupervisor` (an enabled policy registers it automatically, an
explicit `autonomy.loops.self_update` block can override/disable it) and Windows
autostart may register `Alpha_Update`, but the policy is the kill switch. Commands:
`make update-status|check|apply|recover`. Operations and recovery:
`docs/AUTO_UPDATE.md`; tests `backend/tests/test_auto_update.py`.

## Peer network boundary

`alpha.peer_network` is the single harness owner for cross-installation Alpha
identity, discovery, pairing, transport, SQLite conversations, delivery receipts
and topology semantics. `PeerNetworkService` is installation-scoped; never accept
a model/client owner assertion. Discovery cards are untrusted and grant no
credentials. HTTP/WebSocket endpoints are validated before use, messages are
bounded/idempotent, public ingress authenticated by the pairing token in the
Gateway adapter. The optional GitHub adapter publishes public Agent Cards only —
not a mailbox — and libp2p stays an external future seam that must not be
reported active without a real implementation. See
[docs/ALPHA_PEER_NETWORK.md](../../../docs/ALPHA_PEER_NETWORK.md),
`backend/tests/test_peer_network.py`.

## Bot forge

`alpha.bots.forge` builds a Bot as a **transaction**: every step records its own
inverse, any failure rolls the build back to *nothing*, and a Bot is only
reported alive after it answers a smoke test. The point is that a half-built
Bot is indistinguishable from a working one by looking at it, so "the files
exist" and "the Bot works" must never be reported the same way.

- **Pre-flight refusals happen before anything is created.** An unusable
  sandbox backend, a routine faster than the cost floor, a duplicate role or a
  malformed handle is refused with the reason, roster untouched — never a
  partial profile to clean up later.
- **Rollback removes the profile outright.** `retire_bot()` is a soft delete
  that archives in place, leaving a ghost and making the "no partial Bot
  remains" claim in `ForgeResult.reply()` false; forge pops the profile from the
  registry and re-saves instead. Rollback failures are reported per step, never
  swallowed.
- **No model is called.** The workspace survey, overlap check and routine guard
  are deterministic word/rule matching: same inputs, same build.

Guardrails are written into the SOUL at birth by `build_guardrail_soul()` —
approvals, escalation target, where the Bot runs — because approvals that live
only in a config file are approvals nobody reads. `DEFAULT_APPROVALS` is
draft-first; passing an explicit empty list is the operator opting out.

**The thin-query rule is load-bearing.** `_overlap_score` normalises against the
*query's* ceiling, so a one-word role (`Security`) scores 1.0 against any SOUL
that merely shares the word. `check_overlap` therefore requires at least
`survey.MIN_OVERLAP_TERMS` (3) distinct significant terms before refusing; below
that the score is evidence only. Never remove that floor — it is what stops a
legitimate `role="Security"` build being refused by an unrelated leader SOUL.

Supporting modules, each with its own contract:

- `alpha/bots/survey.py` — read-only, shallow, time-bounded, cached workspace
  survey. Only directory names, git remotes and the *head* of README/AGENTS/
  CLAUDE are read; dependency trees are skipped, the home directory is scanned
  only when named in `workspace_roots`, and anything credential-shaped is
  scrubbed before it can reach a Bot's memory. `SurveyResult.clone()` — not
  `to_dict()` — is how a cached result is handed back. The scan is gated on the
  `ALPHA_BOT_SURVEY` kill switch (set to `0`), checked *before* the cache so a
  disabled survey can never be served as a real one; a disabled survey reports
  the reason in `next_steps` instead of claiming it found nothing.
- `alpha/bots/journal.py` — append-only per-Bot work journal (Markdown record +
  `index.json` blocker state); refuses credential-shaped text and private
  reasoning at write time, and a blocker closes itself when the same work is
  recorded as completed. `waiting_on_you()` is the cross-Bot roll-up: the answer
  to "anything waiting on me?" for the whole roster.
- `alpha/bots/portable.py` — `.alphabot.json` export/import with a secret
  scanner that runs at **both** doors (a template can be hand-edited between
  them). Verdicts are CLEAN/WARN/BLOCK; findings name the field and line, never
  the value. Chat history, operator facts and journals never travel in a
  template, exports are confined to one root directory, and nothing is
  overwritten.

The model-facing surface is the existing `bot_roster` tool — new capabilities are
actions on it (`forge`, `teach`, `journal`, `waiting_on`, `share`, `import`,
`doctor`, `sandbox`) rather than new tools, so `BUILTIN_TOOLS`,
`contracts/feature_manifest.json` and the root capability counts are unaffected.
`teach` does **not** install: it validates the draft via
`alpha.skills.authoring.validate_skill_draft`, runs the static scan and queues a
`SkillProposal` — the admin approve gate remains the control, and the reply says
the skill is not active yet. Tests: `backend/tests/test_bots_forge.py`.

## Bot self-service

`bot_roster` also carries the two actions that let a Bot configure *itself*
without an operator in the loop: `update_profile` (identity) and `routine` (its
own automation) — actions on the existing tool, so the tool count and generated
manifest are unchanged.

**The property is "a Bot may configure itself, but may not widen itself."**
These actions deliberately remove the human from the loop, so the refusal path
must be the default rather than what a caller remembers to request — that decides
three things in the tool:

- **`actor` is required, never defaulted.** An empty actor is refused outright
  rather than falling back to the operator: every other default would hand an
  *unidentified* caller more privilege than an identified one.
  `actor=<own handle>` means self-service; a leader handle
  (`SELF_EXTENSION_ACTORS` = alpha/lead/system/server) means editing somebody
  else. It stays a declared string rather than runtime-derived identity — the
  convention `registry._assert_leader` and `war_room` already use — the gate is
  the leader-only field split, not identity attestation.
- **Presentation is self-editable; grants are not.** `_SELF_EDITABLE` is
  `display_name` and `avatar` only. Everything that decides what a Bot may *do*
  is leader-only: `role` (fed to `ToolPermissionGate.check_permission`), `model`
  and `skills` (hashed into `capability_fingerprint()`), `capabilities` (what it
  is offered) and `department`/`reports_to` (org placement). A batch mixing the
  two is refused **whole** - naming the offending fields - rather than partly
  applied, so a caller never has to diff to find out what landed.
- **`update_soul` carries the same actor gate.** `soul` is *not* leader-only (a
  Bot evolving its own persona is the point of self-modification), but the gate
  is still required: writing another Bot's SOUL is how a refused `role` edit
  gets re-applied a moment later as prose. Removing the `actor` parameter from
  `update_soul` would reopen that path, and `update_soul` must not be widened
  back into an unguarded write.

`routine` reuses `forge.plan_routine_guard` for the same 30-minute frequency
floor (`allow_frequent` still overrides it), so a Bot cannot schedule itself into
a token burn `forge` would have refused at birth. Reading routines needs no
`actor`; any write does. `tests/test_bots_selfservice.py` covers all of it
including the negative cases; `tests/test_advanced_bot_tools.py` calls
`update_soul` with an explicit actor, so the gate is pinned there too.
