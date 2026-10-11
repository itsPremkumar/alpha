# Arena tournaments

One task, N sub-agents, one survivor.

Alpha's arena runs a **multi-agent tournament**: every competitor is dealt a
different strategy card (a reasoning mode × workflow × strategy combination),
solves the task independently, writes its solution to a file, and then the
field is cut down by a single-elimination bracket in which every pair attacks,
defends and revises before a judge scores both revised solutions on a rubric.

The point is not "more agents". It is **structured disagreement**: the attacks
are the only part of the process that looks for what is wrong, the repairs are
the only part that is allowed to concede, and the judge sees a blinded,
position-randomised presentation so a solution cannot win on authorship or
file name.

The engine contract lives in
[`backend/packages/harness/alpha/arena/AGENTS.md`](../backend/packages/harness/alpha/arena/AGENTS.md).
This page is the user/operations view.

---

## 1. What a run costs, and why it asks first

Call arithmetic for `n` competitors in a single-elimination bracket:

| Stage | Calls |
| --- | --- |
| Spawn (each competitor solves the task) | `n` |
| Each match (two attacks, two defend-and-revise, one judge) | `5 × (n − 1)` |
| Baseline (optional reference solution) | `+ 1` |

So `n = 8` is **43 sub-agent calls**, each of which is a real, bounded
`SubagentExecutor` delegation with its own toolset.

`arena estimate` computes the projection before anything runs — rounds, calls,
waves, and an estimated token figure (`calls × estimate_tokens_per_call`) —
and reports whether confirmation will be required. A run whose projected call
count exceeds `require_confirmation_over_calls` (default `100`, i.e. roughly
20 competitors) is **refused** until the caller repeats the request with
`confirm=true`. There is no way to spend past the ceiling by accident.

Measured spend is a separate number from the estimate: `ArenaBudget` records
the calls, tokens and wall-seconds actually consumed against
`max_subagent_calls_per_run`, `max_tokens_per_run` and
`max_wall_seconds_per_run`, and an exhausted budget stops the run with the
real reason rather than a fabricated completion.

## 2. Running one

### From chat (the `arena` tool)

The lead agent owns the tool; sub-agents cannot call it, so a competitor
cannot spawn a nested arena.

```
arena action="profiles"                                          # what can a run be here?
arena action="estimate" task="..." profile="auto"                # cost first
arena action="create"  task="..." profile="standard" mode="decide"
arena action="start"   run_id="..." confirm=false
arena action="status"  run_id="..."                              # poll
arena action="pairings" run_id="..."                             # this round, per match
arena action="winner"  run_id="..."
```

Other actions: `resume` (continue a run that stopped mid-bracket), `card`
(one competitor's strategy card), `runs` (list), `stop`, `delete`.

### Over HTTP (`/api/arena`)

| Route | Purpose |
| --- | --- |
| `GET /api/arena/config` | effective config |
| `GET /api/arena/profiles` | declared profiles + the honest mode vocabulary |
| `POST /api/arena/estimate` | cost projection (`task`, `profile`, `mode` optional) |
| `POST /api/arena/runs` | create a draft run |
| `GET /api/arena/runs` | list your runs |
| `GET /api/arena/runs/{id}` | full summary |
| `POST /api/arena/runs/{id}/start` | drive to completion (`confirm` body field) |
| `POST /api/arena/runs/{id}/resume` | continue mid-bracket |
| `GET /api/arena/runs/{id}/pairings` | current round with per-match progress |
| `GET /api/arena/runs/{id}/winner` | champion + final check |
| `POST /api/arena/runs/{id}/card` | one competitor's card |
| `POST /api/arena/runs/{id}/stop` | stop a running run |
| `DELETE /api/arena/runs/{id}` | delete (admin only) |

Everything is **owner-scoped**: a run is only visible and mutable to the
identity that created it, and `PAT` callers never qualify as admin.

### Durability

A run is one JSON document written atomically under a cross-process
`FileLock` at `runtime_home()/arena/<run_id>/state.json`, plus a bounded event
log beside it. A Gateway restart, a crash, or the run's own context compaction
loses at most one wave of work; `resume` picks the bracket up exactly where it
stopped, and the deterministic card deal and pair ordering replay identically.

## 3. Profiles and modes

**Profiles are operator data, not a model's opinion.** The shipped set:

| Profile | Competitors | Use |
| --- | --- | --- |
| `quick` | 3 | cheap comparison |
| `standard` | 5 | the default |
| `deep` | 8 | 43 calls; expect to confirm |

`profile="auto"` routes on **local signals only** — task length, declared
safety level, remaining budget — and always returns the reason it chose. The
resolution precedence is: explicit `agents`/`wave` > named profile > `auto` >
configured defaults. An unknown profile name is refused **by name**, and a
profile that would exceed an operator ceiling is **refused, never clamped** —
the ceiling belongs to the operator.

**Modes:**

* `decide` — run the bracket, produce a champion. Implemented.
* `plan` — project the cost and spend nothing. `start` on a `plan` run is
  refused with a pointer to `estimate`, because a mode that spends nothing is
  not an execution.
* `compare`, `synthesize` — **declared by the vocabulary but not
  implemented.** They are refused by name at create/estimate time rather than
  quietly downgraded to `decide`: a run labelled `synthesize` that produced no
  synthesis would be a capability claim the engine cannot keep.

## 4. Judge quality: blinding, hard gates, repairs

Three mechanisms, all deterministic and all recorded in the match:

* **Blinded, position-randomised presentation.** Before the judge is prompted,
  both solutions are copied to neutral paths (`judge-<match>-a.md` /
  `judge-<match>-b.md`), and `pair_order(seed, round, match)` decides whether
  A and B are presented in their natural order. Same seed + same bracket →
  same presentation, so a resume is bit-identical. If the neutral copy cannot
  be made, the judge runs on the original paths and the match records
  `judge_presentation.blind = false` — blinding is claimed only when it
  happened.

* **Hard gates are not tradeable.** The judge can report per-side gates (for a
  coding task: builds, tests pass, no unresolved critical defect; for research:
  every claim sourced, no claim contradicting the evidence ledger). A tripped
  gate **forces `correctness = 0` and `fatal = true`** on that side, so a
  solution that fails its tests cannot be averaged into the win by being
  beautifully written. Per-task rubric weights ride along with this (coding
  40/20/15/20/5, research 30/25/20/15/10, creative 15/25/20/10/30, analysis
  35/25/15/15/10; the 30/25/15/20/10 default otherwise).

* **The judge proposes, the arithmetic disposes.** `decide()` recomputes the
  winner from the rubric weights and overrides a judge whose stated pick
  contradicts its own scores; the match records `judge_overridden`. The
  orchestrator is never allowed to invent a winner: a judge that keeps failing
  is retried up to `max_judge_attempts`, and then the run fails honestly.

Attacks and defences are parsed into structured records. A defender answers
each attack with a **disposition** (`CONCEDE`, `REBUT`, `FIX`, `DEFER`), and a
bounded repair cycle revises the solution for every conceded flaw. Those
dispositions are data for the record and the repair loop — they are never used
to decide who wins; only the rubric does that.

## 5. Cost controls

```yaml
arena:
  enabled: true                     # bind the arena tool
  default_agents: 4
  max_agents: 64                    # hard ceiling, refused not clamped
  default_wave: 4                   # concurrent jobs per wave
  max_wave: 32
  estimate_tokens_per_call: 60000   # for the confirmation estimate only
  require_confirmation_over_calls: 100
  max_subagent_calls_per_run: 1000
  max_tokens_per_run: 50000000
  max_wall_seconds_per_run: 7200.0
  max_judge_attempts: 2
  reasoning_bank_seed: true
```

Waves are sized by the caller and capped by the configured sub-agent capacity
(`subagent_runtime.max_running`, `subagents.max_total_per_run`), because an
arena is ordinary Alpha delegation — it does not get its own execution pool.

With `reasoning_bank_seed: true`, each match outcome is recorded into
`alpha.reasoning_bank` (the winner's strategy card as the strategy, the run id
as evidence) and later runs recall proven strategies. That is the only
"learning" in the system: measured, deterministic, no extra model call.

## 6. Honesty boundaries

Things this engine deliberately does **not** claim:

* **`compare` and `synthesize` do not run.** They are named in the mode
  vocabulary and refused by name; the primitives exist (`alpha.arena.synthesis`
  produces a merge *plan* and never executes anything) but no tournament
  consumes them yet. Neither will a live run cite a requirement-coverage
  number or an evidence-ledger verdict, because `alpha.arena.candidate` and
  `alpha.arena.evidence` are engine primitives with tests, not executor wiring.
* **A champion is a judged winner, not a verified solution.** The rubric and
  the hard gates make the judgement defensible; nothing here re-executes the
  winning solution to prove it.
* **Local durability, not cross-process exactly-once.** The store's lock is
  filesystem-local. Two Gateways sharing one `runtime_home()` coordinate; two
  hosts on different filesystems do not.
* **A failed spawn is a recorded drop; a failed attack is "NO ATTACKS"; a
  failed defence leaves the original standing; a failed judge is retried and
  then fails the run.** The failure policy is per-stage and recorded, so a
  bracket can never quietly award a match nobody judged.

## 7. Tests

```bash
cd backend
uv run pytest tests/test_arena_*.py -v
```

| Suite | Covers |
| --- | --- |
| `test_arena_core.py` | deck, dealer, rubric, bracket state machine, prompts |
| `test_arena_store.py` | atomic writes, locking, ownership, bounded logs |
| `test_arena_executor.py` | full tournaments (2/4/8/odd), budgets, failure policy |
| `test_arena_service.py` | lifecycle, confirmation gate, ownership, listing |
| `test_arena_advanced.py` | contract, candidates, evidence, synthesis, repairs, gates, blinding, mode refusals |
| `test_arena_profiles.py` | profile resolution, the `auto` router, ceiling refusals, cheap gates |

All of them run with a **scripted runner** — zero model calls, deterministic
outcomes. The production runner (`SubagentArenaRunner` → `SubagentExecutor`) is
seams and adapters, so the tournament logic under test is the tournament logic
that ships.

## 8. Related

* Engine contract: [`backend/packages/harness/alpha/arena/AGENTS.md`](../backend/packages/harness/alpha/arena/AGENTS.md)
* Sub-agent delegation: [`backend/packages/harness/alpha/subagents/AGENTS.md`](../backend/packages/harness/alpha/subagents/AGENTS.md)
* Tool governance: [`backend/packages/harness/alpha/tools/AGENTS.md`](../backend/packages/harness/alpha/tools/AGENTS.md)
* Strategy memory: `alpha.reasoning_bank`
