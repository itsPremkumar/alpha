# Swarm Mode — Implementation Plan

**Compiled:** 2026-10-17 · **Basis:** direct reading of `alpha/swarm/`
(25 modules, ~8,900 LOC) cross-referenced against
`SWARM_MODE_DEEP_RESEARCH.md` and `KIMI_SWARM_MODE_DEEP_RESEARCH.md`.

Every claim below carries a `file:line` so it can be checked or refuted.

---

## 0. A correction I owe you first

In my earlier swarm research I wrote:

> "Alpha has `workspace_mode: "isolated"` and records/validates
> `worktree_path` in its swarm coordinator, but **does not provision worktrees**."

**That was wrong, and it was the top-ranked gap in that document.** Alpha
provisions worktrees properly:

- `sandbox/worktrees.py:145,147,201` — three `git worktree add` variants
  (existing branch, new branch from base, `--detach`)
- `sandbox/worktree_strategy.py:218` — `select_strategy`, a real policy with
  **7 isolation modes** and **2 assurance levels**, including an explicit
  degraded-copy path that records its verbatim reason (`worktree_strategy.py:195`)
- `swarm/runner.py:158-167` — constructs `CodingWorktreeWorker` with
  `auto_commit`/`simulate_merge` gated on `selection.mergeable`
- `swarm/runner.py:147` — refuses protected branches
- `swarm/runner.py:156-157` — **fails loudly** when assurance is degraded rather
  than pretending a coding task produced a mergeable diff

This code is better than most production swarms. The real gap is narrower and
different — see **G1**.

---

## 1. What is actually built (and good)

Recording this first so the plan doesn't redo existing work.

| Capability | Location | Note |
|---|---|---|
| 5 escalating complexity tiers | `strategy.py:45` | `DIRECT`→`FULL`, each a strict superset |
| Structural topology routing | `topology.py:233` | single-sink rule precedes coupling rule — order-sensitive and correct |
| Candidate plan scoring | `planner.py:93` | 4 bounded candidates, deterministic tie-break |
| **Weighted critical path + capacity bound** | `decomposer.py:424-467` | `estimate_method = "dependency_critical_path_plus_capacity_v1"` |
| Weighted DAG critical path | `decomposer.py:433-461` | uses per-task `estimated_seconds` |
| Adaptive concurrency under 429s | `governor.py:78` | deterministic halving/quartering |
| SPRT deliberation | `deliberation.py:279` | early-stopping statistics, not majority voting |
| Sycophancy + domination detection | `deliberation.py:216,244` | `max_share × voter_count`, not raw share |
| Contract-net auction + leader election | `cnp_auction.py:244,126` | rare in agent frameworks |
| Stigmergy as advisory, never a gate | `stigmergy.py:111` | bounded, decayed, no decay on read |
| Loop detection | `telemetry.py:123` | repeated objectives/results/retries |
| Worktree isolation policy | `sandbox/worktree_strategy.py:218` | mature, fail-loud |
| Durable JSON checkpoints | `runner.py`, `coordinator.py` | atomic, restart-recoverable |

`[I]` **This is a more complete swarm runtime than most commercial products.**
The gaps below are about *measuring the right things*, not missing machinery.

---

## 2. Ranked gaps

### G1 — The isolation policy is fed a hardcoded constant
`swarm/runner.py:155`
```python
selection = select_strategy(TaskSignals(produces_mergeable_diff=True))
```
`select_strategy` exists precisely to decide isolation from **measured task
signals** — it accepts a `TaskSignals` struct (`worktree_strategy.py:105`) and
returns one of 7 modes. The swarm passes `True` unconditionally.

Consequence: **every task that reaches this branch is treated as write-capable**,
and every task that doesn't (`EphemeralSubagentWorker`, `runner.py:168`) gets
**no isolation at all** — including the read-heavy exploration that the industry
consensus says is the *safe* case to parallelise.

**Fix:** derive `TaskSignals` from the task node — `worker_type`,
`capability_tags`, declared `acceptance_criteria`, and whether the objective
contains write verbs vs read verbs. Read-heavy tasks should get an explicit
read-only tier, not fall through to "ephemeral".

**Value:** High. **Risk:** Low (pure policy input; the policy already exists).

### G2 — The measured makespan is discarded at selection time
`decomposer.py:463` computes
```python
plan.critical_path_seconds = estimated_parallel   # weighted, seconds
plan.metrics["estimated_parallel_seconds"] = estimated_parallel
```
`planner.py:93` `score_plan` then ranks candidates using **only**:
- `features.depth` — explicitly documented as "the critical path in *hops*, not
  seconds" (`topology.py:55-56`)
- `task_count`, `edge_count`, `max_width`

**`score_plan` never reads `plan.critical_path_seconds` or
`estimated_seconds`.** A plan with one 600-second task and nine 5-second tasks
scores identically to its inverse.

**Fix:** add a makespan term to `PlanScore` using the already-computed
`critical_path_seconds`, and make the tie-break prefer lower predicted wall-clock
before lower task count.

**Value:** High — this is the single cheapest real improvement in the plan.
**Risk:** Low. `score_plan` is pure arithmetic; the number already exists.

### G3 — Serial collapse and spurious parallelism are unmeasured
Kimi's PARL had to add two reward terms specifically to suppress these two
degenerate policies. Alpha's `telemetry.py` detects *loops* (repeated
objectives/results, `telemetry.py:123`) but has no signal for either:

- **Serial collapse** — the swarm spawned 8 subagents and ran them one at a
  time. Cost: 8× the wall-clock, same quality.
- **Spurious parallelism** — 30 subagents, 2 meaningful completions. Cost with
  no benefit.

**Fix:** two new `SwarmTelemetry` signals:
- `parallelism_realised`: mean in-flight workers per dependency stage
- `subtask_completion_ratio`: completed ÷ spawned, excluding retried
- **not** a pass/fail gate — advisory, like stigmergy

**Value:** High. These are exactly MAST FM-1.3 (step repetition) and FM-2.5
(ignored other agent's input), and they are currently invisible.
**Risk:** Low (read-only measurement).

### G4 — The governor advertises model names that are not used, and not in `config.yaml`
`governor.py:36-41`
```python
MODEL_TIERS = {
    "frontier": "claude-3-7-sonnet",
    "fast": "gemini-2.5-flash",
    "local": "ollama/qwen2.5-coder",
    "verifier": "gpt-4o",
}
```
`resolve_model_for_role()` has **zero production callers**. The runner resolves
models correctly through `resolve_subagent_model_name(..., app_config=...)`
(`runner.py:182-195`) and `task_node.model_override`.

But `get_status()` returns these stale names, and it **is served**:
- `app/gateway/routers/swarms.py:621` — `GET /api/swarms/governor/status`
- `tools/builtins/swarm_tool.py:314` — the model's own `governor` action

So an operator surface and the **model itself** are told the swarm routes to
`claude-3-7-sonnet` and `gpt-4o`. The root `AGENTS.md` rule is: *"Every model name
Alpha knows lives in `config.yaml`, never in code."*

**Fix:** delete `MODEL_TIERS` and `resolve_model_for_role`; have `get_status()`
report the *actual* resolved models from the plan's tasks plus
`routing_method: "config_yaml_task_override_v1"`.

**Value:** High — this is a live honesty defect on two surfaces, and the
violation is of the repo's own root rule.
**Risk:** Very low (deletes dead code; the route keeps working).

### G5 — Role→model routing is keyword-matched on the role name
`governor.py:53-59` matches substrings: `"judge"`, `"synthesizer"`, `"architect"`
→ frontier. This is exactly the fragility the swarm research flagged — a
specialist renamed `quality_gate` silently drops from the verifier tier.

**Fix:** replace with `capability_tags` matching (`team.py:466`
`required_capability_tags` already derives tags from the task) against
configured role→model bindings in `config.yaml`.

**Value:** Medium. **Risk:** Low, but it *is* a behaviour change for anyone
relying on the keyword path — so it should ship behind the G4 cleanup, not with it.

### G6 — No cost projection before a swarm fan-out
Arena refuses above `require_confirmation_over_calls` before spend. The swarm
tool has `budget_pressure` (`telemetry.py:94`) but only *after* running.

**Fix:** a pre-launch projection from `SwarmBudget` × `estimated_speedup`,
surfacing worst-case spend, and refuse above a configured threshold.

**Value:** High. **Risk:** Low.

### G7 — Read-heavy and write-heavy fan-out are not distinguished
From the research: parallel subagents are "much safer for read-heavy work"
(exploration, triage, log analysis, security review) than write-heavy. Alpha's
swarm has no such distinction — it either gets a worktree or an ephemeral worker.

**Fix:** follows from G1; add an explicit `read_only` assurance to the isolation
tier so a 20-way read fan-out is a first-class, cheap, safe shape.

---

## 3. Modes: current vs proposed

### Current (8)
`models.py:14-22`

| Mode | Actually implemented? |
|---|---|
| `AUTO` | yes — `strategy.resolve_strategy` |
| `PARALLEL` | yes — `decomposer.py:125` |
| `MAP_REDUCE` | yes — `decomposer.py:125` (same branch as PARALLEL) |
| `SCATTER_GATHER` | yes — `decomposer.py:129` |
| `ENSEMBLE` | yes — `decomposer.py:129` (same branch) + consensus |
| `DEBATE` | yes — `decomposer.py:127` + SPRT |
| `CODING_WORKTREE` | yes — `decomposer.py:131` + isolation |
| `HIERARCHICAL` | **not branched in the decomposer**; hierarchy emerges from DAG depth / `parent_task_id` |

`[I]` `MAP_REDUCE` and `PARALLEL` share a branch, and `ENSEMBLE` and
`SCATTER_GATHER` share one. They are **distinct labels over two decompositions**.
That is defensible but should be stated, not implied.

### Proposed additions

Each is justified by a specific research finding, not by novelty.

| New mode | Why | Anchor |
|---|---|---|
| `critical_path_split` | decompose so the **longest** branch shrinks, not the average | Kimi `CriticalSteps`; Alpha's own unused `critical_path_seconds` |
| `read_fanout` | N read-only workers, no isolation, cheap, safe to scale wide | industry: read-heavy parallel is the safe case; Alpha's G7 |
| `sharded_context` | each worker gets a bounded private context; only relevant outputs return | Kimi "context sharding" — and Alpha's `deepagent/` already does this |
| `adversarial_review` | producer/judge split with the judge unable to see the producer's rationale | Anthropic's CitationAgent; defeats the game-of-telephone |

`[I]` **Recommendation: add `read_fanout` and `critical_path_split` first.**
`sharded_context` is mostly a *labelling* change over `deepagent/`, and
`adversarial_review` overlaps the existing `DEBATE` + consensus path.

---

## 4. Phased plan

Each phase is independently shippable and independently verifiable.

### Phase A — honesty first (no behaviour change)
1. **G4** — delete dead `MODEL_TIERS`/`resolve_model_for_role`; report real models.
2. **G6** — pre-launch cost projection + refusal.
3. Tests: the governor route must no longer contain a hardcoded model name; a
   refused launch must leave the plan untouched and say why.

**Gate:** `GET /api/swarms/governor/status` names only models present in
`config.yaml -> models[]`.

### Phase B — measure the right thing
4. **G2** — makespan term in `score_plan`, tie-break on predicted wall-clock.
5. **G3** — `parallelism_realised` + `subtask_completion_ratio` in telemetry.

**Gate:** a candidate set containing one faster-but-fewer-tasks plan selects the
faster plan; a serial swarm reports `serial_collapse: true`.

### Phase C — isolation becomes a decision
6. **G1** — real `TaskSignals` from task shape.
7. **G7** — explicit read-only assurance.
8. **G5** — capability-tag routing, after G4 lands.

**Gate:** two concurrent writers never collide; a read-only worker physically
cannot write.

### Phase D — new modes
9. `read_fanout`, then `critical_path_split`, each with a UI row and a test.
10. `sharded_context` labelling over `deepagent/`.
11. `adversarial_review` only if `DEBATE` is measurably insufficient.

### Phase E — measured, then optimised
12. Run each new mode live and record predicted vs realised makespan.
13. Only then consider tuning `score_plan` weights or enabling learned routing.

`[I]` **Phase E exists because of what happened with the delegation-tool deferral
bug**: a system can pass every test and still be broken in production. Every
phase above should be verified by *running* a swarm, not by reading the source.

---

## 5. What I deliberately did not propose

- **Not adopting PARL.** Alpha has no training pipeline. The *transferable* parts
  are the objective (G2) and the failure taxonomy (G3) — not the algorithm.
- **Not unifying the swarm and subagent admission pools.** Still the open §2.4
  decision. The `select_strategy`/governor work above does not depend on it, so
  it should stay unblocked until someone decides.
- **Not raising `max_tasks: 256` or `max_concurrency: 8`.** Kimi's 300 subagents
  are backed by a trained orchestrator and a critical-path objective. Alpha has
  neither; raising the ceiling without G2/G3 would produce precisely the
  over-delegation Kimi's `r_finish` term was written to suppress.
- **Not adding agent-to-agent messaging.** Claude Code's inbox is a real
  primitive, but Alpha has `SwarmMessageBus` (`communication.py:121`) and
  stigmergy. Adding a second channel is duplication.

---

## 6. Limitations of this analysis

- **Read, not executed.** I read every swarm module's structure and its
  decision-relevant bodies, but I did not run a swarm. Phases A–C are
  implementation proposals against source, not against observed behaviour.
- **No profile of a real swarm run.** I did not capture actual
  `peak_in_flight`, task durations, or token spend, so I cannot say how far the
  predicted makespan is from realised makespan — which is the number that would
  validate or refute G2.
- **The governor's dead-code finding is from a caller search**, not a runtime
  trace. A dynamically-reached path could exist that a text search misses
  (unlikely, but the delegation-tool deferral bug is a standing reminder).
- **`HIERARCHICAL` was not traced end-to-end.** I established it is not branched
  on in the decomposer; I did not prove that hierarchy is therefore handled
  correctly elsewhere.
- **No comparison against `alpha.workflow` or `alpha.gro`.** There are three
  overlapping orchestration engines in this repo; a plan that unifies them is a
  different and much larger document.