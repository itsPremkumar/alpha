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
* `service.estimate(agents, wave, baseline)` — cost projection + confirmation flag
* `service.create(owner_id, thread_id, task, agents, wave, seed, baseline, budgets)` — draft run
* `service.start(run_id, owner_id, confirm=False)` — drive to completion (gate)
* `service.resume(run_id, owner_id)` — continue from mid-bracket
* `service.status/pairings/winner/card/list_runs/stop/delete` — reads & control

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
                    "pairings","winner","card","runs","stop","delete"],
    **kwargs
) -> str
```

Actions: `estimate`, `create`, `start`, `resume`, `status`, `pairings`,
`winner`, `card`, `runs`, `stop`, `delete`. All owner-scoped.

### HTTP API: `/api/arena`

* `GET /config` — effective config
* `POST /estimate` — cost projection
* `POST /runs` — create draft run
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

### Tests

* `tests/test_arena_core.py` — deck, rubric, bracket, prompts, models
* `tests/test_arena_store.py` — persistence, locking, ownership
* `tests/test_arena_executor.py` — full tournament simulation (2/4/8 agents, odd, budget, failures)
* `tests/test_arena_service.py` — service lifecycle, gates, ownership, listing

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
tools/builtins/arena_tool.py               # model-facing tool
app/gateway/routers/arena.py               # HTTP router
config.example.yaml                        # arena: section
tests/test_arena_core.py
tests/test_arena_store.py
tests/test_arena_executor.py
tests/test_arena_service.py
```

## Regeneration checklist

After adding this package:

1. Run `scripts/generate_feature_manifest.py` to regenerate
   `contracts/feature_manifest.json`
2. Update tool count in `README.md`, `llms.txt`, `llms-full.txt`,
   `docs/FAQ.md`, `docs/COMPARISON.md`
3. Run `make test` and `make test-blocking-io` to verify
4. Bump `config_version` in `config.example.yaml` if schema changed