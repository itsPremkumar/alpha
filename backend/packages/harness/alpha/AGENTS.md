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

### Managed browser-use runtime (`community/browser_use/`)

This opt-in integration runs its nested planner in a managed subprocess/venv;
keep it isolated from the Gateway event loop and terminate the process tree on
cancellation. Readiness must be measured from installed distribution metadata,
not a slow import that could trigger a destructive reinstall. Distinguish task
failure from child-process failure, redact credentials, and never equate its
`completed` result with verified work. Tool and operator contract:
`community/browser_use/`; tests: `tests/test_browser_use*.py`.
### AgentEye live-source adapter (`community/agent_eye/`)

The harness pins AgentEye to an immutable upstream commit (not a branch, legacy
project name or unpinned package) and never constructs or calls upstream
`AgentSearchLite`; its unauthenticated HTTP API and MCP entry points are
excluded. Curated adapter surface, operator allowlist, `allow_fragile_backends`
policy, bounded worker queue, dedupe, provenance, `no_evidence` honesty,
`citations_verified` semantics and the `use_for_deep_research` DDGS fallback:
[backend/docs/AGENT_EYE_RESEARCH.md](../../../backend/docs/AGENT_EYE_RESEARCH.md),
[docs/DEEP_RESEARCH.md](../../../docs/DEEP_RESEARCH.md).

### Deep-agent working plane (`packages/harness/alpha/deepagent/`)

Alpha's port of LangChain's [`deepagents`](https://github.com/langchain-ai/deepagents)
state backend, and the reason a long multi-step run can keep its own work: a
bounded, thread-scoped virtual filesystem that lives in **graph state**, not on
disk and not in a sandbox.

On a long run the agent's findings live in the message tail, and the tail is what
compaction eats. A note written here is a state channel — it survives compaction,
checkpoint/resume and a Gateway restart, and `DeepAgentContextMiddleware` projects
it back as a bounded index (path, measured size, the model's own one-line
purpose) into every model request, for the **lead agent and every delegated
subagent** alike. A child that reads what its parent already established does not
re-derive it in an isolated context window.

The model surface is **one** tool with an `action` (`deepagent_workspace`), bound
only while `config.yaml -> deepagent.enabled`. Contract, the four load-bearing
invariants (order is recency; a delete is an operation not a tombstone; every
bound is a refusal never a clamp; addresses only never source), and why the
channel ceiling is hard while configuration may only narrow it:
**[packages/harness/alpha/deepagent/AGENTS.md](deepagent/AGENTS.md)**. Tests:
`backend/tests/test_deepagent_working_plane.py`.

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

Eleven read-only registries behind one `list` / `describe` / `health` protocol,
with `alpha.workflow.registry` the single source per fact: `engines` and `wiring`
read the generated `contracts/feature_manifest.json` through the one
`manifest_source` loader rather than recomputing a count
`scripts/check_generated_drift.py` already settles.

The root guide's self-inventory plane section owns those invariants, and this
file points rather than restating them.

Model surface: the single `alpha_capability` tool, lead-agent-only and absent from
`SUBAGENT_TOOLS`. HTTP: `GET /api/intelligence/inventory`. Tests:
`backend/tests/test_self_inventory_plane.py`; operations:
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

## Durable state under `harness/` (`continuous/`, `continual/`)

### Natural-language command triggers

`commands/autonomous_engine.py` calls `command_registry.execute` when a rule
matches and `auto_execute` is enabled. Treat a persistent command such as
`/goal create` as a side effect: require an explicit action verb, not a bare
mention of "goal", "objective", "mission", or "milestone". A negated request
must not fire. Keep `tests/test_autonomous_commands.py` pinned for incidental,
negated, and explicit creation wording. The chat UI must display the returned
execution status and output; a matched intent is not evidence that a command
ran successfully. `/loop pause` and `/loop resume` must target a requested goal
id or refuse ambiguity when multiple loops match; never select the first row
from a persistent store as an implicit target. Verify the transition was saved
before reporting it as complete.

A JSON-backed store that cannot be read must report **unknown**, never empty.
The two lead to opposite decisions, so the difference has to survive to the
caller — a bare `except Exception: pass` in `_load` is not a safe default here,
it is a silent failure with a green suite beside it.

Three stores carried exactly that shape and now carry the same disclosure:

- **`continuous/store.py` (`GoalStore`)** backs the model-facing `goal_engine`
  tool and the `/loop:*` commands, both of which answer from `list_goals()`. A
  corrupt `goals.json` used to answer `"No active autonomous goals."` — telling
  the model there was no work when the truth was that its work could not be
  read. It exposes `load_error` / `is_degraded`, and `_save` returns whether the
  write landed (`is_durable`, `save_error`): a full disk used to return a `Goal`
  as though persisted, so `resume` reported success on state that vanished on
  restart. `goal_engine` now refuses on a degraded store and refuses to claim a
  resume it could not persist.
- **`continual/state.py` (`HarnessState`)** backs `ContinualHarnessMiddleware`,
  which is appended to **every** lead agent and builds its system-reminder from
  `self.entries`. A corrupt state file therefore injected *no* reminder at all —
  the agent silently lost every persisted failure rule it had been given.
  `load()` stages into a local mapping and adopts it **only on full success**:
  the previous code reset `self.entries` first, so one malformed entry partway
  through left a half-populated state that the next `add_entry` → `save()`
  wrote back, destroying the entries that *had* parsed. That was real data loss.
- **`continual/snapshots.py` (`HarnessSnapshotManager`)** answers
  `harness_refine action="rollback"` from its manifest. An unreadable index used
  to return `[]`, i.e. "No snapshots available", while the snapshot files sat on
  disk; `create_snapshot` then rewrote the manifest from `[]` and orphaned every
  prior snapshot. The payload files are never at risk — only the index — and the
  return signature is unchanged, so the log line is what distinguishes this from
  the old behaviour.

The house style these three were brought up to is already set by the
structurally identical `groups/claims.py` and `bots/registry.py`, which log with
`exc_info=True`. **Absent is not corrupt**: a missing file leaves
`is_degraded` false, or a first run would report a fault. Tests:
`tests/test_harness_state_durability.py`.

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
Specialist requirements are inferred from each task directive using
`alpha.capabilities.eligibility.CAPABILITY_KEYWORDS` and matched against the
live `specialists:` roster with a hard capability floor. Keep the inferred tags
aligned to the shipped specialist capability vocabulary and pin end-to-end
composition in `backend/tests/test_team_composition.py`; a completed swarm with
zero specialist assignments is not evidence of specialist execution.
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


## Canonical task contract (`tasks/`)

`alpha.tasks` is the **neutral projection target** that lets a task be
described, budgeted, handed off and verified the same way whichever engine ran
it. It exists because Alpha grew **seven** schedulers and **three**
incompatible task vocabularies — `WorkflowNode.status` (12 values),
`SwarmTaskNode` (a typed `TaskNodeState` enum **and** a bare `status: str`
carrying 10 more, on one object), and `graph.TaskNode.status` (10) — with
nothing translating between them. The measured baseline is
`docs/architecture/multi-agent-baseline.md`.

It is **additive and owns no lifecycle**. `RunManager` remains the sole
run-lifecycle owner, `alpha.mission.acceptance` remains the only producer of a
terminal *passed*, `alpha.errors.registry` remains the sole owner of error
codes, and each engine keeps its own task model **and** its own scheduler. The
contract is a shape, not a seventh scheduler, and
`tasks/tests`-level `TestPackageBoundary` asserts the first three against the
parsed AST — a text grep cannot tell a docstring recording *who owns what* from
an actual import.

Three properties are load-bearing:

- **An unmappable status refuses, never defaults.**
  `UnmappableStatus` names the engine *and* the offending value. The
  alternative — defaulting to `pending` — turns a state nobody recognises into
  a task that looks **ready to run**, which is the exact failure the package
  exists to remove.
- **The status maps are exhaustive by construction.** Each adapter covers every
  member of its engine's live enum, and the tests enumerate those enums
  (including both swarm vocabularies) and fail on a gap. A gap turns an
  ordinary state change into a refusal mid-run.
- **The vocabulary is the narrowest common core, not the union.** A union drifts
  toward whichever engine grew last. `native_status` / `native_type` preserve
  what the engine actually said, so a projection is **lossless**: swarm's
  `completed` becomes `succeeded` in the contract and comes back as `completed`.
  `SwarmTaskNode`'s `partial_success` maps to `RUNNING`, never to `FAILED` or
  `SUCCEEDED`, because the engine never reached a terminal verdict.

Bounds are refused, not clamped; `max_cost_usd=0.0` is a **real ceiling**
meaning paid calls are blocked, and `UNBOUNDED` is a distinct sentinel from
`None` ("explicitly unbounded" vs "unstated") so no caller can encode
unboundedness as `0`. The contract is frozen, so `with_status()` /
`with_attempt()` return new values and two engines cannot overwrite each other.
Full contract: [packages/harness/alpha/tasks/AGENTS.md](tasks/AGENTS.md). Tests:
`tests/test_task_contract.py`. Declared in `alpha.capabilities.catalog` as
`task_contract` / `task_contract_adapters`.


## Group-Relative Orchestration (`gro/`)

`alpha.gro` implements the GRPO-inspired orchestration plan
([`docs/ALPHA_GRPO_INSPIRED_ORCHESTRATION_PLAN.md`](../../../docs/ALPHA_GRPO_INSPIRED_ORCHESTRATION_PLAN.md)):
one objective, a bounded group of candidates executed under *distinct*
strategies, then selection by **hard gates first, absolute score second,
relative rank third**. Full contract, module map and invariants:
[packages/harness/alpha/gro/AGENTS.md](gro/AGENTS.md). The load-bearing rules
in one paragraph each:

- **Absolute gates decide eligibility; relative comparison only ranks the
  eligible.** A required gate that is `unknown` is not a pass, `R_i = Q_i −
  median(Q)` is written as a diagnostic only, and a group where nothing passes
  ends as an explicit `no_acceptable_candidate`: one bounded repair round
  (`gro.max_repair_rounds`, default 1) then a `partial_result` labelled
  **UNVERIFIED** — never "the best of a bad lot, verified". No declared
  acceptance criteria means an UNVERIFIED winner (`GroReport.verified=False`);
  the engine never invents criteria, and final verification demotes a
  provisional winner, never promotes one.
- **GRO is deliberately distinct from `alpha.arena`.** Arena is a bracket
  tournament (attack/defend/judge between pairs); GRO is a single-wave group
  comparison with gate-first eligibility and failure-directed repair. Neither
  may grow selection rules the other owns, and the two stores are separate.
- **The model surface is two actions on the existing `swarm` tool** —
  `compete` (`goal` + `items_json` whose entries are acceptance criteria;
  task type, risk tier and group size are policy-computed, never caller-
  declared) and `compete_status` (`swarm_id` is the GRO run id; omit it to
  list your runs newest first). No new tool, so `BUILTIN_TOOLS`,
  `contracts/feature_manifest.json` and the capability counts are untouched.
  `compete` refuses while `gro.enabled` is false (default off);
  `compete_status` stays readable because it is read-only.
- **Candidate lanes execute as real subagents.** `SubagentExecutionAdapter`
  runs candidates through `SubagentExecutor`, inheriting the process-wide
  admission controller, sandbox leases, receipts and usage accounting, so GRO
  cannot exceed the operator's subagent ceiling. The candidate tool pool is
  assembled once per run via `run_assembly(get_available_tools, …)` with the
  `swarm` tool stripped by name (no nested tournaments); an empty pool is a
  refusal before any lane runs, never a silent tool-less candidate.
- **A timeout is a fence, not a kill.** CPython cannot stop a thread: a lane
  past its bound is declared `timeout` and its late result is *discarded* —
  adopting it would credit work the run already declared late. Every
  candidate gets an outcome (completed, failed, timed-out, fenced-late or
  never-started after early stop / deadline); omission would read as success
  to any later ranker.
- **Store honesty.** Runs persist owner-scoped under
  `runtime_home()/gro/<owner>/<run_id>.json` (single-process JSON, never
  cross-process exactly-once); the JSONL event journal is appended live while
  the document lands at completion, so `compete_status` reads a journalled
  interrupted run's tail and says so. Absent is not corrupt: an unreadable
  document is disclosed through `is_degraded`/`load_error` and never read as
  "no runs", and an unpersisted report says it could not be persisted.
- **Bounds are refused, never clamped** — goal/criteria size, candidate
  counts (bounded by `gro.max_candidates_per_task`), duplicate or unknown
  strategy ids, and budgets. Risk is computed from the goal, so externally
  visible or destructive work parks as `needs_human` with nothing executed
  while `require_approval_for_external_side_effects` is on. Token spend is
  recorded but never enters the score.

Tests: `backend/tests/test_gro_core.py`, `test_gro_evaluation.py`,
`test_gro_runner.py`, `test_gro_tool.py`.


## Dynamic workflow plane

`alpha.workflow.runtime.DynamicWorkflowEngine` and
`alpha.orchestrator.loop.ExecutionKernel` own graph execution; the optional
service composes them but never replaces `RunManager`, bot/group ownership or the
scheduler. Host/domain executors are opt-in; the default `alpha.local.digest`
executor is always a local graph projection, never domain-task acceptance.
Unbound commands, verifiers or external adapters must remain `not_run` or fail
with their actual reason; no graph status or model summary proves acceptance.

Architecture, supported node kinds and API: [`docs/DYNAMIC_WORKFLOWS.md`](../../../../docs/DYNAMIC_WORKFLOWS.md),
[`docs/ALPHA-WORKFLOW-ARCHITECTURE.md`](../../../../docs/ALPHA-WORKFLOW-ARCHITECTURE.md),
and [`docs/ALPHA-WORKFLOW-CURRENT-STATE.md`](../../../../docs/ALPHA-WORKFLOW-CURRENT-STATE.md).
Keep runtime, workflow swarm, verifier and template promotion tests aligned:
`tests/test_workflow_runtime_correctness.py`, `test_workflow_swarm_node.py`,
`test_workflow_verification.py`, `test_workflow_templates_and_improvement.py`.
## Governed variation engine boundary (`avo/`)

`alpha.avo` already decided *which* variation to try and *whether it looked good
afterwards*; the governance layer answers the question no part of it could:
**whether a proposed variation may run at all.** One rule holds the design
together — **the model proposes, the server disposes.**

- **Risk is computed, never declared.** `policy.py` derives risk from what a
  proposal touches, what it can undo and what it spends, so a `ProposedAction`
  cannot lower its own risk by describing itself carefully. `contracts.py` is
  `extra="forbid"` and digests every field, so a field added between the request
  and the decision changes the **digest**, not the grant.
- **Approval is required, not requested.** An action the gate decides needs an
  operator parks in `WAITING_APPROVAL` with a **non-terminal** `BLOCKED` report,
  so a parked action and a rejected one stay distinguishable by state rather than
  prose. There is deliberately **no second complexity ceiling**:
  `TaskFeatures.estimated_complexity` is it, and
  `TooComplexForGovernance`/`COMPLEXITY_CEILING` were removed rather than left to
  race it.
- **A tier that did not run is never a tier that passed.** `evaluation.py` keeps
  `skipped` and `inconclusive` separate from `passed`, so a short-circuited
  pipeline cannot read as a clean sweep.
- **Every attempt is receipted, denials included.** `receipts.py` is an
  append-only tamper-evident chain, and the model cannot mint one — so "what
  happened and why" is a record rather than a retelling.
- **Profiles re-resolve on resume.** `checkpointing.py` re-resolves capability,
  evaluator and approval profiles when a run continues, which stops a checkpoint
  preserving a grant revoked while the run was parked.
- **Budgets are ceiling-owned.** `budgets.py` reserves, charges, releases and
  reconciles; there is no `reset` and no `grant_more`, and exhaustion reports
  measured figures, not a rounded-down remainder.
- **Paths are confined by refusal, not by repair.** `paths.py` normalises first
  and then refuses traversal, absolute, URL and symlink escapes; a directory grant
  covers its subtree and nothing outside it.
- **The router records why, not just that.** `router.py` records every unmet
  condition as a refusal *reason*, so a run declines naming what it needed rather
  than one opaque no. `lifecycle.py` restricts transitions to a closed graph, so a
  model actor can neither enter nor leave a terminal state.

`profiles.py` resolves default-deny profiles **server-side by id**: three path
lists, one action allow-list and one network policy, where an empty list denies
everything rather than everything having to be named. `alpha_self_update` is
present and **`enabled=False`** with `allowed_action_types=[]` — a capability that
exists but cannot be enabled is more honest than one that is silently reachable,
and real self-update belongs to the guarded auto-update contract below.

Three boundaries must not be crossed: `scorer_authority.py` stays server-owned,
because a candidate that authors its own fitness function selects for confidence
rather than correctness; `commit_gate.py` stays the only writer of a promoted
version; and nothing here may promote a variation by itself. Tests:
`tests/test_avo_governance.py`, `tests/test_avo_governance_layer.py`.

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
- Forge accepts an avatar label and a per-Bot model only from the operator's
  existing `config.yaml -> models[]`; unknown models are refused before a
  roster mutation. The `bot_roster` tool must pass these fields through. An
  avatar label is not a generated photo. A build without a caller-supplied
  smoke probe is created but explicitly **unverified**, never reported alive.
  The lead-agent specialist setup path searches and audits candidate skills,
  installs only through Skills Hub, inspects the persisted profile, and runs a
  bounded task addressed to the new Bot before reporting runtime behavior.

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
