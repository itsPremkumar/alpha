# Arena Tournament Engine (`packages/harness/alpha/arena/`)

**Alpha's port of the multi-agent arena pattern.** One task, N sub-agents,
each dealt a different strategy card (reasoning mode × workflow × strategy),
then a single-elimination bracket where every pair attacks, defends, and
revises, and a judge scores both revised solutions on a fixed rubric until
one solution survives.

## What makes this Alpha's rather than a script

* **Durable and resumable.** The whole tournament lives in one JSON document
  written atomically under a cross-process `FileLock` at
  `runtime_home()/arena/<run_id>/state.json`. Any process can pick a run up
  after a Gateway restart, and a run survives its own context compaction.
* **Budget-gated.** Every run carries a measured token budget and a sub-agent
  call budget. The engine estimates the cost of a run *before* it starts and
  refuses to launch above the operator ceiling without an explicit
  `confirm=True`. A silent 39M-token first prompt is a defect, not a
  feature, so it cannot happen here.
* **Capacity-aware.** Waves are sized by the caller and clamped to the
  configured sub-agent capacity, because Alpha's ordinary delegation caps at
  `subagents.max_total_per_run` and `subagent_runtime.max_running`.
* **The model proposes, the arithmetic disposes.** Judges are sub-agents and
  may be wrong, so `decide()` recomputes the winner from the rubric weights
  and overrides a judge whose stated pick contradicts its own scores.
* **Never decides a match itself.** A failed judge is re-run a third time;
  the orchestrator is forbidden from recording a winner of its own.
* **Learning loop.** Every match outcome is recorded into
  `alpha.reasoning_bank` (the winner's card, its evidence ref the run id),
  and every new run recalls strategies that worked before. The arena gets
  smarter the way the rest of Alpha does: measured, deterministic, no extra
  model call.

## Package structure

```
arena/
├── AGENTS.md              # this file
├── __init__.py            # lazy exports
├── models.py              # typed value types (cards, attacks, defenses, scores, budget, plan)
├── cards.py               # 15×12×12 strategy deck + balanced edge-colouring dealer
├── rubric.py              # 5-criterion rubric, weights, decide() with fatal rule
├── bracket.py             # pure state machine (plan, spawn, pair, advance, judge)
├── prompts.py             # 5 templates (competitor/attacker/defender/judge/final)
├── store.py               # durable JSON store, atomic writes + FileLock, owner-scoped
├── runner.py              # ArenaAgentRunner protocol + ScriptedArenaRunner + SubagentArenaRunner
├── executor.py            # async tournament driver (waves, budgets, failure policy)
├── service.py             # ArenaService lifecycle, cost estimate, confirmation gate, events
├── config.py              # ArenaConfig pydantic model + config resolution
├── task_contract.py       # structured task contract: requirements, constraints, acceptance, safety
├── candidate.py           # candidate artifacts + per-requirement coverage + contribution selection
├── repair.py              # bounded repair loop: CONCEDE/REBUT dispositions, cycles, terminality
├── task_rubrics.py        # per-task weights, hard gates, deterministic judge bias controls
├── evidence.py            # evidence ledger: claim checks, verdicts, gate_from_ledger()
├── synthesis.py           # contribution synthesis: merge plan, CONFLICT, markdown render
├── modes.py               # run modes + budget profiles + the adaptive `auto` router
└── strategies.json        # shipped strategy deck (15 reasoning × 12 workflow × 12 strategy)
```

## Public API

### Models (value types, all frozen dataclasses with `to_dict`/`from_dict`)

* `StrategyCard` — one dealt card: reasoning + workflow + strategy `CardPart`s
* `AttackRecord` / `DefenseRecord` — parsed attack/defense entries
* `VerdictScores` — 5-criterion scores (0-10) + `fatal` flag
* `ArenaPlan` — projected cost: rounds, calls, waves, per-row breakdown
* `ArenaBudget` — measured spend vs. ceilings (calls, tokens, wall seconds)
* `ArenaStatus` / `ArenaPhase` — lifecycle enums

### Core engine (pure, testable, zero I/O)

* `deal_cards(n, seed, deck)` — balanced edge-colouring dealer (Konig + de Werra)
* `bracket.plan(agents, wave, baseline)` — cost projection before spend
* `bracket.new_run(...)` — create draft run state
* `bracket.next_actions(state, limit)` — next wave of ready jobs
* `bracket.record_attack/defense/verdict/final` — state mutations
* `bracket.is_done(state)` — terminal check
* `rubric.decide(a_scores, b_scores, ...)` — winner from scores, overrides judge
* `rubric.weighted_total(scores)` — rubric-weighted total (0-10)
* `prompts.render(kind, values)` — single-pass `{{placeholder}}` substitution

### Persistence

* `ArenaStore(root)` — owner-scoped, atomic writes under `FileLock`
* `store.load(run_id, owner_id)` / `store.save(state)` — read/write with ownership
* `store.list_runs(owner_id)` — bounded summaries, newest first
* `store.read_log(run_id, tail)` / `store.append_log(...)` — bounded event log

### Execution

* `ArenaAgentRunner` protocol — `async run_job(job) -> ArenaJobResult`
* `ScriptedArenaRunner` — deterministic test double
* `SubagentArenaRunner` — wraps `SubagentExecutor` for production
* `ArenaExecutor(store, runner, wave, max_judge_attempts, on_event)` — drives tournament

### Service (model/HTTP surface)

* `ArenaService(config, runner_factory, store)` — lifecycle owner
* `service.resolve_profile(profile/task/agents/wave/safety_level)` — competitor
  count + wave resolution with the reason it chose (see *Modes and profiles*)
* `service.profiles()` — the declared budget profiles and the mode vocabulary,
  listing only what this engine implements
* `service.estimate(agents, wave, baseline, task, profile, mode)` — cost
  projection + confirmation flag
* `service.create(owner_id, thread_id, task, agents, wave, seed, baseline, budgets, profile, mode)` — draft run
* `service.check_start(run_id, owner_id, confirm=False)` /
  `service.check_resume(run_id, owner_id)` — the cheap pre-execution gates,
  which return the loaded state and raise on any refusal (see *Cheap gates*)
* `service.start(run_id, owner_id, confirm=False)` — drive to completion (gate)
* `service.resume(run_id, owner_id)` — continue from mid-bracket
* `service.status/pairings/winner/card/list_runs/stop/delete` — reads & control

### Advanced primitives (task contract → candidates → repairs → synthesis)

Seven sibling modules extend the engine along the plan's second phase. Three of
them are **wired into the executor today**; four are **engine primitives with
tests but no executor wiring yet** — that distinction is load-bearing, and the
summary below says which is which rather than implying all seven run in a
tournament.

**Wired:**

* `task_rubrics.py` — `weights_for(task_type)` /
  `weighted_total_for(...)` pick the per-task rubric (coding 40/20/15/20/5,
  research, creative, analysis; the 30/25/15/20/10 default otherwise).
  `default_gates(task_type)` names the non-tradeable conditions (coding:
  build / tests / no_critical; research: citations / no_contradiction;
  creative: constraints; otherwise: fidelity). `apply_gates(scores, gates)`
  **forces `correctness=0` and `fatal=True`** for a tripped gate — the caller
  still reports *which* gate tripped — so a gated candidate can never present
  as competitive on the weighted total. Bias controls are deterministic, never
  random: `pair_order(seed, round_no, match_no)` decides whether the judge
  sees the pair in its natural order, `swapped_pair()` applies it, and
  `blind_label()` exposes position only.
* `repair.py` — `parse_repairs(text, cycle)` reads the defender's disposition
  lines (`CONCEDE`/`CONCEDED`, `REBUT`/`REBUTTED`, `FIX`/`FIXED`,
  `DEFER`/`DEFERRED`; malformed lines are skipped, never guessed into a
  disposition), `unresolved_attacks(...)` / `fatal_conceded(...)` /
  `is_terminal(...)` bound the repair cycle. **Dispositions are data**: the
  orchestrator records them and never uses them to pick a winner — only the
  rubric does that.
* `modes.py` — the run-mode and budget-profile vocabulary (see *Modes and
  profiles*), including the `auto` router.

**Primitives, not yet wired into the executor** (pure, tested, exported from
`alpha.arena`; a tournament does not call them):

* `task_contract.py` — a structured contract for a run's task:
  `Requirement`/`Constraint`/`AcceptanceCheck`/`TaskContract`, plus the
  `TaskType`, `RequirementPriority`, `VerificationMethod`, `SafetyLevel` and
  `BudgetProfile` vocabularies.
* `candidate.py` — `CandidateArtifact`, `RequirementCoverage`
  (`CoverageStatus`), `Contribution`, `best_contributions(...)`,
  `synthesis_gaps(...)`: what each competitor produced and which requirement
  it covers.
* `evidence.py` — `ClaimCheck` (`Verdict`), `EvidenceLedger`,
  `gate_from_ledger(...)`: the claim→evidence table a research judge can be
  scored against.
* `synthesis.py` — `PatchContribution`/`MergeItem`/`MergePlan`/`SynthesisResult`,
  `plan_merge(...)`, `synthesize(...)`, `render_markdown(...)`: merges the
  surviving contributions into one document. It emits a **`plan_merge` — it
  never executes anything**, and overlapping patches are flagged `CONFLICT`
  rather than silently layered.

Because those four are unwired, the engine will not cite a coverage number,
an evidence verdict or a synthesis result in a live run's output. They are
the primitives the next executor pass builds on; claiming otherwise would be
a fabricated capability.

### Configuration (`config.yaml -> arena`)

```yaml
arena:
  enabled: true                 # bind the arena tool
  default_agents: 4             # competitors per run (1..2160)
  max_agents: 64                # hard ceiling, refused not clamped
  default_wave: 4               # concurrent jobs per wave
  max_wave: 32                  # hard ceiling on wave size
  estimate_tokens_per_call: 60000  # for confirmation gate only
  require_confirmation_over_calls: 100  # runs above this need confirm=True
  max_subagent_calls_per_run: 1000
  max_tokens_per_run: 50000000
  max_wall_seconds_per_run: 7200.0
  max_judge_attempts: 2         # judge retries before honest failure
  reasoning_bank_seed: true     # record outcomes into alpha.reasoning_bank
```

### Model-facing tool: `arena`

```python
@tool("arena", parse_docstring=True)
async def arena_tool(
    runtime,
    action: Literal["estimate","create","start","resume","status",
                    "pairings","winner","card","runs","stop","delete",
                    "profiles"],
    **kwargs
) -> str
```

Actions: `estimate`, `create`, `start`, `resume`, `status`, `pairings`,
`winner`, `card`, `runs`, `stop`, `delete`, `profiles`. All owner-scoped.
`create`/`estimate` also take `profile` and `mode`; `profiles` is the
read-only question "what can a run even be here?" and answers with the
operator-declared profiles and the honest mode vocabulary.

**Read-only costs nothing; execution pays.** Every action except
`start`/`resume` builds `ArenaService(config=config)` with **no runner
factory** — no tool assembly, no config snapshot beyond the arena block. Only
`start`/`resume` call `_executing_service()`, which resolves the `AppConfig`
off-loop (`asyncio.to_thread(get_app_config)`) and assembles the competitors'
toolset through `alpha.utils.assembly_io.run_assembly(get_available_tools, ...
subagent_enabled=False)` — never `loop.run_until_complete`, which raises on a
running loop. Competitors get no delegation tools: a sub-agent cannot spawn a
nested arena. The Gateway router (`app/gateway/routers/arena.py`) mirrors this
with its own `_executing_service(user, thread_id)` and binds the run's
`thread_id` so competitors share the run's workspace.

### HTTP API: `/api/arena`

* `GET /config` — effective config
* `GET /profiles` — the declared budget profiles + the honest mode vocabulary
* `POST /estimate` — cost projection (`task`/`profile`/`mode` optional)
* `POST /runs` — create draft run (`task`/`profile`/`mode` optional)
* `GET /runs` — list runs (owner-scoped)
* `GET /runs/{run_id}` — full summary
* `POST /runs/{run_id}/start` — drive to completion (`confirm` gate)
* `POST /runs/{run_id}/resume` — continue mid-bracket
* `GET /runs/{run_id}/pairings` — current round with per-match progress
* `GET /runs/{run_id}/winner` — champion + final check
* `POST /runs/{run_id}/card` — one competitor's card
* `POST /runs/{run_id}/stop` — stop running run
* `DELETE /runs/{run_id}` — delete (admin-only)

All mutations admin/owner-gated; `PAT` never qualifies as admin.

### Events (in-process bus)

* `arena.run.started` / `arena.run.completed`
* `arena.match.completed` (with `judge_overridden` flag)
* `arena.final.completed` / `arena.budget.exhausted`

### Modes and profiles

`modes.py` owns the vocabulary. `RunMode` is `decide` (run the bracket) and
`plan` (project cost only); `compare` and `synthesize` are **declared but not
implemented**, and `_check_mode` refuses them **by name** at `create`/`estimate`
rather than degrading them into `decide` — a run labelled `synthesize` that
produced no synthesis would be a fabricated capability. `plan` refuses `start`
with `ArenaRunError` pointing at `estimate`, so a zero-spend mode cannot be
mistaken for an executed one.

`Profile` is the operator-declared budget profile: `quick` (3 competitors),
`standard` (5), `deep` (8) — the profile list is data, never computed from a
model's opinion of the task. `ArenaService.resolve_profile` resolves the
competitor count and wave with this precedence, highest first:

1. an explicit `agents`/`wave` argument;
2. a named profile;
3. `profile="auto"` — `modes.route(RouteInputs(...))`, which reads only local
   signals (task length, safety level, remaining budget) and always returns the
   reason it chose;
4. the configured defaults.

An unknown profile name is refused **by name** (`spec_for` raises), and a
profile exceeding an operator ceiling is refused, never clamped: the ceiling is
the operator's call.

### Judge presentation, hard gates, and the repair loop

`ArenaExecutor._prepare_judges` runs once per match, before the judge is
prompted, and does three things:

* **Position bias** — `pair_order(seed, round_no, match_no)` decides the A/B
  presentation deterministically, so a resume replays the identical ordering.
* **Identity bias** — both solutions are copied to neutral paths
  (`judge-<match>-a.md` / `judge-<match>-b.md`) through
  `_copy_for_judge`, so a file name cannot leak which competitor wrote what.
  The copy runs off the event loop (`asyncio.to_thread`).
* **Honest disclosure** — if the neutral copy cannot be made, the judge runs on
  the original paths and the match records `judge_presentation["blind"]=false`.
  Blinding is claimed only when it actually happened.

The judge template asks for `gates: {a: [...], b: [...]}` beside `winner`,
`reason` and `scores`. `ArenaExecutor._parse_gates` reads that block,
`_gated_scores` applies `task_rubrics.apply_gates` per side, and
`_record_repairs` persists the defender's dispositions. Two rules are
load-bearing:

* a tripped hard gate forces `correctness=0` **and** `fatal=True` on that side;
* an `UNVERIFIED` / `NOT_CHECKED` claim never trips a gate by itself — only a
  `CONTRADICTED` one does. "We did not check" is not evidence of failure.

The gate verdict, the bias record and the dispositions all travel in the match
record; `rubric.decide()` is still the only thing that picks a winner, and
`judge_overridden` is still recorded when the arithmetic disagrees with the
judge's stated pick.

### Cheap gates and the runner-binding seam

`ArenaService.check_start(run_id, owner_id, *, confirm)` and
`check_resume(run_id, owner_id)` are the pre-execution gates: they load the
state, check ownership, refuse a terminal run, validate the mode, and fire the
confirmation gate — and they do all of that **before** any runner or tool
assembly exists. `start()`/`resume()` delegate to them through a shared
`_drive(state)`, so there is exactly one code path for the refusal text; both
the tool and the router call `check_*` first and only then build the executing
service, so a refusal costs a state read instead of a tool assembly.

The runner factory stays a seam: `ArenaService(runner_factory=...)` receives a
`SubagentConfig` and returns something with `run_job`, which `_build_runner`
probes once (`factory(None)`) and wraps in `SubagentArenaRunner`. A service
with no factory can create, estimate, gate and read — and refuses to execute
with a real reason rather than a fabricated result.

### Tests

* `tests/test_arena_core.py` — deck, rubric, bracket, prompts, models
* `tests/test_arena_store.py` — persistence, locking, ownership
* `tests/test_arena_executor.py` — full tournament simulation (2/4/8 agents, odd, budget, failures)
* `tests/test_arena_service.py` — service lifecycle, gates, ownership, listing
* `tests/test_arena_advanced.py` — task contract, candidates, evidence,
  synthesis, repair dispositions, hard gates, judge blinding, mode refusals
* `tests/test_arena_profiles.py` — profile resolution, the `auto` router,
  ceiling refusals, the `profiles` tool action, `check_start`/`check_resume`

Run: `uv run pytest tests/test_arena_*.py -v`

## Integration points

* **Sub-agent execution** — wraps `SubagentExecutor`; respects process capacity
  (`subagent_runtime.max_running`, `subagents.max_total_per_run`)
* **Reasoning bank** — records match outcomes (`alpha.reasoning_bank.record`)
  and seeds new runs (`recall`)
* **Events** — publishes on `alpha.events.bus` (non-blocking)
* **Config** — `ArenaConfig` in `AppConfig.arena`; hot-reloadable except ceilings
* **HTTP** — router at `/api/arena`; owner-scoped, admin-gated mutations
* **Tool** — `arena` tool in `BUILTIN_TOOLS`; lead-only, denied to sub-agents

## Design decisions

1. **No silent fallbacks.** Every validation error, budget breach, or parse
   failure raises with the real cause. A run that cannot complete honestly
   stops with a named reason, not a fabricated result.

2. **Deterministic dealer.** Same seed + deck = same card assignment. The
   balanced proper edge-colouring guarantees pairwise uniqueness up to 144
   agents (the deck's workflow × strategy bound).

3. **Judge override.** `decide()` recomputes winner from rubric weights and
   records `judge_overridden` when the judge's stated pick disagrees with
   the arithmetic. The orchestrator never invents a winner.

4. **Budget ceiling, not clamp.** Ceilings are refused with the bound named,
   never silently clamped. `confirm=True` is the only way to exceed the
   confirmation ceiling.

5. **Resumable by design.** One JSON state file, atomic writes, cross-process
   lock. A Gateway restart loses at most one wave of work.

6. **Learning loop.** Match outcomes feed `alpha.reasoning_bank` with the
   winner's card line as strategy and the run id as evidence. New runs
   `recall` similar tasks to seed with proven strategies.

## Related packages

* `alpha.subagents` — `SubagentExecutor`, capacity controller
* `alpha.reasoning_bank` — measured strategy memory
* `alpha.events.bus` — in-process event bus
* `alpha.rubric` / `alpha.critic.rubric` — shared rubric infrastructure
* `alpha.deliberation.council` / `alpha.deliberation.debate` — related multi-agent patterns
* `alpha.benchmarks.arena` — existing benchmark leaderboard (different purpose)

## Files created by this feature

```
packages/harness/alpha/arena/              # new package
├── __init__.py
├── AGENTS.md
├── models.py
├── cards.py
├── strategies.json
├── rubric.py
├── bracket.py
├── prompts.py
├── store.py
├── runner.py
├── executor.py
├── service.py
├── config.py
├── task_contract.py
├── candidate.py
├── repair.py
├── task_rubrics.py
├── evidence.py
├── synthesis.py
└── modes.py
tools/builtins/arena_tool.py               # model-facing tool
app/gateway/routers/arena.py               # HTTP router
config.example.yaml                        # arena: section
tests/test_arena_core.py
tests/test_arena_store.py
tests/test_arena_executor.py
tests/test_arena_service.py
tests/test_arena_advanced.py
tests/test_arena_profiles.py
```

## Regeneration checklist

After adding this package:

1. Run `python backend/scripts/generate_feature_manifest.py` (from the repo root)
   to regenerate `contracts/feature_manifest.json`. `scripts/check_generated_drift.py`
   regenerates it into a temp dir and compares bytes, so a hand-edited manifest
   fails CI. `tests/test_feature_manifest_wiring.py` then pins every entry to a
   real wiring point.
2. Update tool count in `README.md`, `llms.txt`, `llms-full.txt`,
   `docs/FAQ.md`, `docs/COMPARISON.md` — the counts must match the generator's
   own `counts:` line (arena adds one tool and one router).
3. Run `make test` and `make test-blocking-io` to verify
4. Bump `config_version` in `config.example.yaml` if schema changed