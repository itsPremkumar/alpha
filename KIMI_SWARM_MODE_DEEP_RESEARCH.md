# Kimi Agent Swarm — Deep Research

**Compiled:** 2026-10-17 · **Subject:** Moonshot AI's "Agent Swarm" — the
self-directed parallel-agent orchestration shipped with Kimi K2.5 and scaled up
through K2.6 and K3.

**Evidence convention** (same as `SWARM_MODE_DEEP_RESEARCH.md`):

- `[M]` **measured** — primary source, the Kimi K2.5 technical report (arXiv:2602.02276) or official Moonshot documentation.
- `[R]` **reported** — secondary/independent reporting, not verified here.
- `[I]` **inference** — my reasoning from the evidence.

Where sources conflict I say so rather than picking the tidier number.

---

## 1. What it actually is

Agent Swarm is **not** "many agents working together." Moonshot describes it as a
**"scale-out" architecture** in which the *organisational structure itself is
designed by the model* — no predefined roles, no hand-authored workflows `[M]`.

> "It is not a better hammer. It is a reconstruction of the entire workshop." —
> Kimi blog, *Kimi Agent Swarm: 100 Sub-Agents at Scale* `[M]`

The sharpest framing in Moonshot's own copy `[M]`:

> "In 2025, if you walked into any AI conference… you may hear this gospel:
> faster inference, longer context windows, cheaper inference costs. It's as if
> we've spent years perfecting the hammer… while never questioning the fact that
> the carpenter still has only two hands and twenty-four hours in a day."

**The load-bearing distinction** `[I]`: every production multi-agent system
decides *how* to parallelise at inference time from a prompt, a plan, or a
hand-written workflow. Kimi's claim is that **when and whether to parallelise is a
learned policy** — "parallelism is not presumed to be inherently advantageous;
decisions regarding whether, when, and how to parallelize are explicitly learned
through environmental feedback and RL-driven exploration" `[M]`.

That is the whole thesis. Everything else is engineering around it.

---

## 2. Timeline and lineage

| Date | Event |
|---|---|
| 2025-09-05 | Kimi K2 released — 32B active / 1T total |
| 2025-09-26 | "OK Computer" Agent mode |
| **2026-01-27** | **Kimi K2.5 released — Agent Swarm introduced** `[M]` |
| 2026-02-02 | K2.5 technical report on arXiv (2602.02276) `[M]` |
| 2026-02 | Kimi Claw public beta |
| **2026-04-20** | **Kimi K2.6 released + open-sourced — swarm scaled to 300 agents / 4,000 steps** `[M]` |
| **2026-07-16** | **Kimi K3 released — 2.8T params, native vision, 1M context; powers K3 Swarm** `[M]` |
| 2026-07-27 | K3 open-weights release due `[R]` |

### Swarm limits by generation
| Model | Sub-agents | Coordinated steps |
|---|---|---|
| K2.5 | **100** | **~1,500** tool calls `[M/R]` |
| K2.6 / K3 | **300** | **~4,000** `[M]` |

The orchestrator itself stays shallow — roughly **15 high-level steps** while
subagents execute the thousands `[M]`.

### Product modes
K2.5 exposes **four** modes `[R]`: **Instant**, **Thinking**, **Agent**, and
**Agent Swarm**. Swarm is described as a *research preview* `[M]`.

---

## 3. PARL — the actual technical contribution

**Parallel-Agent Reinforcement Learning** is the part that matters, and it is a
genuinely novel training design `[M]`.

### 3.1 Architecture: decoupled, not end-to-end

```
trainable orchestrator  ──creates──►  frozen subagents
        ▲                                    │
        └────── subagent output as ──────────┘
              environmental observation
```

- Sub-agents are **frozen**, instantiated from **fixed intermediate policy
  checkpoints** `[M]`.
- Only the orchestrator is updated by RL; subagent trajectories are **excluded
  from the optimisation objective** `[M]`.

**Why** — two named problems with end-to-end co-optimisation `[M]`:

1. **Credit-assignment ambiguity.** "A correct final answer does not guarantee
   flawless subagent execution, just as a failure does not imply universal
   subagent error." With 100 contributors, which one helped?
2. **Training instability.**

Treating subagent output as an *environmental observation* rather than a
differentiable action **disentangles high-level coordination logic from
low-level execution proficiency** `[M]`.

`[I]` This is the single most transferable idea in the whole system. It maps
directly onto Alpha's problem: Alpha cannot end-to-end train anything, but the
*shape* — freeze the worker, train only the router — is exactly how Alpha's
scenario planner should be validated.

### 3.2 The reward function

```
r_PARL(x,y) = λ₁ · r_parallel  +  λ₂ · r_finish  +  r_perf(x,y)
                 │                 │                 │
       instantiation reward   sub-agent finish    task-level
                                          outcome
```

Each term exists to kill a specific degenerate policy `[M]`:

| Degeneracy | Definition | Term that prevents it |
|---|---|---|
| **Serial collapse** | Orchestrator defaults to single-agent execution — "a local optimum". Sequential is *easier to credit-assign*, so plain RL learns to avoid parallelism even when it is faster | `r_parallel` |
| **Spurious / "fake" parallelism** | Orchestrator "increases parallel metrics dramatically by spawning many subagents without meaningful task decomposition" | `r_finish` |

**The anneal is the key design.** `λ₁` and `λ₂` are **annealed to zero** over
training `[M]`, so:

> "the final policy optimizes for the primary objective."

This is staged reward shaping done properly — the scaffolding is **removed**, not
left as a crutch. A policy that still parallelises at λ₁ = 0 does so because
parallelism *pays*.

`[I]` This is precisely the discipline Alpha uses elsewhere (priority admission
annealed to inert), and it is why Kimi's numbers are credible in a way most
orchestration claims are not: the final model is not being *held* parallel.

### 3.3 Critical Steps — the metric, not the agent count

The headline "300 agents" is a ceiling, not a goal. The trained objective is
**latency**, modelled on the critical path of a computation graph `[M]`:

```
CriticalSteps = Σ_{t=1..T} ( S_main(t) + max_i S_sub,i(t) )
```

- `S_main(t)` — main agent's steps in stage *t* (typically 1)
- `S_sub,i(t)` — steps taken by the *i*-th subagent in that parallel group
- **The stage's duration is governed by its longest-running subagent** — hence
  `max`, not `sum`

What this rewards `[M]`:
- Spawning excess subtasks that **don't shorten the longest branch** yields
  **little benefit**
- **Well-balanced decomposition that shortens the critical path directly reduces
  the score**

> "the orchestrator is encouraged to allocate work across subagents in a way
> that minimizes end-to-end latency, rather than merely maximizing concurrency
> or total work performed" `[M]`

`[I]` This is the most under-appreciated part of the design. "Number of agents"
is a vanity metric that a reward hack trivially maximises — which is precisely
why Kimi had to add `r_finish` as a *second* guard. Critical Steps makes the
hack self-defeating, because spawning a slow subagent *increases* the metric.

### 3.4 Prompt construction — inducing the capability

The orchestrator is trained on synthetic prompts designed to **break sequential
agents** `[M]`:

- **Wide search** — simultaneous exploration of many independent sources
- **Deep search** — multiple reasoning branches with delayed aggregation
- Real-world shapes: long-context document analysis, large-scale file downloading

These are deliberately **hard to complete sequentially within fixed
reasoning-step and tool-call budgets** `[M]`.

The crucial detail `[M]`:

> "the prompts do not explicitly instruct the model to parallelize. Instead, they
> shape the task distribution such that parallel decomposition and scheduling
> strategies are naturally favoured."

**Capability induction through task distribution, not instruction.** That is a
training-methodology result, not a prompting trick — and it is why the learned
policy generalises rather than pattern-matching a `/swarm` command.

### 3.5 Training efficiency notes
- Orchestrator is first trained with **small-size subagents**, then moved to
  larger `[M]`.
- The RL framework supports **dynamically adjusting the inference instance ratio
  between subagents and the orchestrator** to maximise cluster utilisation `[M]`.

---

## 4. Measured results

### 4.1 Quality — Agent Swarm vs single-agent vs proprietary `[M]`

| Benchmark | K2.5 Agent Swarm | K2.5 single | Claude Opus 4.5 | GPT-5.2 | GPT-5.2 Pro |
|---|---|---|---|---|---|
| **BrowseComp** | **78.4** | 60.6 | 37.0 | 65.8 | 77.9 |
| **WideSearch** (Item-F1) | **79.0** | 72.7 | 76.2 | — | — |
| **In-house Swarm Bench** | **58.3** | 41.6 | 45.8 | — | — |

Absolute gains over single-agent `[M]`:
- BrowseComp: **+17.8**
- WideSearch: **+6.3**
- In-house Swarm Bench: **+16.7**

**The in-house benchmark is the one to read sceptically** `[I]`: it was built by
the same team, and the paper says its tasks are "explicitly designed to reward
parallel decomposition" `[M]`. The largest gain is on the benchmark most
favourable to the method. **BrowseComp is the honest external number** (+17.8
over its own single-agent, beating GPT-5.2 Pro) — and notably BrowseComp is a
*third-party* benchmark.

The in-house bench's four domains `[M]`: WildSearch, Batch Download, WideRead
(>100 input documents), Long-Form Writing (>100k words).

### 4.2 Latency — the 4.5× claim, unpacked

`[M]` On WideSearch, 3×–4.5× faster than single-agent. But the scaling figure is
more interesting than the headline (Figure 8) `[M]`:

| Target Item-F1 | Single agent | Agent Swarm |
|---|---|---|
| 30% | ~1.8× baseline | 0.6–1.6× |
| 70% | **>7.0× baseline** | 0.6–1.6× |

`[I]` **Agent Swarm's latency is near-flat as difficulty rises; the single agent
grows super-linearly.** The value is not "4.5× on average" — it is that the gap
*widens exactly where the work gets hard*. That is the correct shape for the
claim.

### 4.3 K2.5 → K2.6 swarm gains `[R]`
| Benchmark | K2.5 | K2.6 |
|---|---|---|
| BrowseComp (Agent Swarm) | 78.4 | **86.3** |
| SWE-Bench Pro | 50.7 | **58.6** |
| Terminal-Bench 2.0 | 50.8 | **66.7** |
| Toolathlon | 27.8 | **50.0** |

### 4.4 K2.5 general benchmarks `[M]` (selected)
| Benchmark | K2.5 | Claude Opus 4.5 | GPT-5.2 (xhigh) | Gemini 3 Pro |
|---|---|---|---|---|
| HLE-Full w/ tools | **50.2** | 43.2 | 45.5 | 45.8 |
| AIME 2025 | 96.1 | 92.8 | **100** | 95.0 |
| GPQA-Diamond | 87.6 | 87.0 | **92.4** | 91.9 |
| MMLU-Pro | 87.1 | **89.3** | 86.7 | **90.1** |
| BrowseComp (single) | **60.6** | 37.0 | 65.8 | 37.8 |
| Seal-0 | **57.4** | 47.7 | 45.0 | 45.5 |
| OSWorld-Verified | 63.3 | **66.3** | — | — |
| WebArena | 58.9 | **63.4** | — | — |

`[I]` Honest reading: K2.5 **wins on agentic/search benchmarks** (BrowseComp,
Seal-0, GDPVal-adjacent agentic tasks) and **trails on general knowledge and
computer-use**. It is not a general SOTA model; it is an agentic-specialist
release. Claiming otherwise from this table would be wrong.

---

## 5. The underrated idea: swarm as context management

The paper argues Agent Swarm is *"a kind of proactive and intelligent context
management"* `[M]`, and this may matter more than the latency numbers.

The contrast is with **reactive** context strategies — Hide-Tool-Result, Summary,
Discard-all — which "react to context overflow by compressing or discarding
accumulated histories" and are "inherently reactive" `[M]`.

Swarm instead gives `[M]`:
- Long-horizon tasks decomposed into **semantically isolated** subtasks
- Each subagent with a **bounded local context** and **independent working memory**
- Subagents "perform local reasoning **without directly mutating or
  contaminating the global context** of the central orchestrator"
- **Only task-relevant outputs** — not full interaction traces — routed back

> "This design induces **context sharding** rather than context truncation,
> allowing the system to scale effective context length along an **additional
> architectural dimension** while preserving modularity, information locality,
> and reasoning integrity." `[M]`

Measured: Agent Swarm **beats Discard-all on both efficiency and accuracy** on
BrowseComp `[M]`.

`[I]` This is the deepest idea in the paper and it is independent of parallelism.
Anthropic reaches the same place by *externalising* state to memory and building a
dedicated CitationAgent; Kimi reaches it by *sharding*. Both are attacking the
same wall. Notably, this is also the exact gap I found in Alpha — synthesis
metrics living on the deep-handoff contract while the ordinary `task` path keeps
everything.

---

## 6. Model specification

| | K2.5 | K2.6 | K3 |
|---|---|---|---|
| Total params | 1.04T `[M]` | 1T `[R]` | **2.8T** `[M]` |
| Active params | 32B `[M]` | 32B `[R]` | — |
| Experts | 384, 8 active/token (sparsity 48) `[M]` | — | — |
| Context | 256K `[R]` | 256K `[R]` | **1M** `[M]` |
| Modality | native multimodal | native multimodal | native vision `[M]` |
| Licence | Modified MIT `[R]` | Modified MIT | Modified MIT |
| Weights | released | open-sourced 2026-04-20 | released 2026-07-27 `[R]` |

Other K2.5 architecture details `[M]`: `MoonViT-3D` vision encoder (NaViT packing,
SigLIP-SO-400M init), shared image/video embedding space, 4× temporal compression
via grouping consecutive frames; joint pre-training on ~15T vision-text tokens;
MuonClip optimiser with QK-Clip.

`[R]` K2.5 ships in **native INT4** (~595 GB) via quantisation-aware training;
Unsloth's dynamic 1.8-bit reportedly brings it to ~240 GB, runnable on one 24 GB
GPU with RAM offload at ~10 tok/s.

---

## 7. Cost — the "swarm tax"

This is the part most marketing omits.

`[R]` Independent reviewers call it the **"swarm tax"**: you pay for every
subagent's context and output, not just one model's.

### The verbosity problem `[R]`
> "When Artificial Analysis evaluated K2.5, the model generated **89 million
> output tokens. The median for comparable models is 14 million.** At $0.60/$3.00
> per million, the per-token price looks cheap, but when the model produces **6×
> more tokens per task**, effective costs still spike."

This is consistent with the swarm design `[I]`: `CriticalSteps` rewards
*minimising latency*, and there is no term penalising **total** token spend. A
critical-path-optimal decomposition can still be extremely expensive — the slow
subagent is the only one that costs you.

### Pricing `[R]` — **sources conflict, reported as found**

| Model | Input | Cache-hit | Output | Context |
|---|---|---|---|---|
| Kimi K3 | $3.00 | $0.30 | $15.00 | 1M |
| Kimi K2.6 | **$0.95 or $0.55** | $0.16 | **$4.00 or $2.65** | 256K |
| Kimi K2.7 Code | $0.95 | $0.19 | $4.00 | 256K |
| Kimi K2.5 (retired 2026-08-31 `[R]`) | $0.60 | $0.10 | $3.00 | 256K |

⚠️ **The K2.6 row has two different published figures.** CloudZero lists
$0.95/$4.00; mayhemcode lists $0.55/$2.65; Venice lists its own hosting at
$0.75/$3.50. I have not resolved this — verify against Moonshot's pricing page
before budgeting.

Also note `[R]`: cache **writes** are billed separately from hits ($3.00/M on the
5-minute tier), so "cold agent prefixes cost before any hit saves."

`[I]` For a 300-agent run, per-token price is close to irrelevant next to total
volume. Alpha's existing cost-projection work (Arena refuses above
`require_confirmation_over_calls`) is the more important precedent here.

---

## 8. Real-world experience and criticisms

**Reported limitations:**
- Swarm is **compute-heavy**; analysts describe it as "a premium feature rather
  than a quick-chat tool" `[R]`
- **Video input was unsupported** in early K2.5 swarm testing, blocking a
  video-recipe demo `[R]` — despite the model being natively multimodal
- **Subscription quota** — Allegretto ($39/mo) advertises 50 swarm uses/month,
  which users report exhausting far faster than advertised `[R]`
- Swarm requires **credits** — it is not free-tier accessible `[R]`
- Independent reviewers report the model "falls flat" for everyday and custom
  day-to-day tasks `[R]`

**On the benchmarks themselves `[R]`:** commentary circulating in 2026 calls out
benchmark-gaming by some Chinese labs and Meta/Llama 4 specifically. I flag this
as **an unverified accusation, not a finding** — but it is a reason to weight
third-party benchmarks (BrowseComp, WideSearch) over in-house ones (the Swarm
Bench), which the paper's own numbers already support.

---

## 9. What is genuinely novel vs prior art

Being precise here matters, because the system is marketed as a reconstruction.

| Claim | Verdict |
|---|---|
| Multiple agents under one lead | **Not novel** — years old `[I]` |
| Parallel fan-out | **Not novel** — LangGraph, CrewAI, Alpha all do it |
| Measuring critical path instead of agent count | **Novel and well-chosen** `[M]` |
| Training the *orchestrator* to decide parallelism | **The real contribution** `[M]` |
| Freezing subagents to fix credit assignment | **Novel; a training-design trick that works** `[M]` |
| Annealing scaffolding rewards to zero | **Established practice, correctly applied** `[I]` |
| Context sharding vs truncation | **Reframing more than invention** `[M]` |

`[I]` Two honest caveats:

1. **The mechanism is model-side.** Alpha cannot adopt PARL — it has no training
   pipeline. What Alpha *can* adopt is the **objective** (critical-path
   minimisation instead of agent count) and the **failure taxonomy** (serial
   collapse, spurious parallelism) as things to measure.
2. **Most of the benefit is latency, not quality.** On WideSearch the quality
   gain is +6.3 while BrowseComp is +17.8 — and BrowseComp's gain is entangled
   with far more compute. `[I]` "4.5× faster" and "17.8 points better" are
   different claims and only the first is cleanly demonstrated as *caused* by
   parallelism.

---

## 10. Implications for Alpha

Concrete, ordered by value:

1. **Adopt CriticalSteps as the swarm objective.** Alpha's `SwarmBenefitEstimator`
   and `planner.score_plan` currently measure different things. `max_i S_sub,i(t)`
   per stage is the metric that makes over-delegation self-defeating. Alpha's
   `topology.py` already computes `exposed_width` and `lower_bound_rounds` —
   this generalises them into a single latency measure.

2. **Add the two degenerate-policy detectors to telemetry.** Serial collapse
   (orchestrator using 1 subagent) and spurious parallelism (many subagents,
   few meaningful completions) are **named, measurable, and currently
   unmeasured** in Alpha. They are exactly Alpha's MAST FM-1.3 (step repetition)
   and FM-2.5 (ignored other agent's input).

3. **Expose "context sharding" as an explicit design claim.** Alpha already has
   `deepagent/` — a bounded thread-scoped virtual filesystem in graph state, with
   `DeepAgentContextMiddleware` projecting it to lead *and* every subagent. That
   is closer to Kimi's context sharding than most systems get, and it is
   currently documented as a *memory* feature rather than a *context-management*
   strategy.

4. **Measure token spend against latency.** Alpha's priority admission optimises
   ordering; it does not yet price the critical path. A swarm that is 4× faster
   and 6× more expensive is a **decision**, and Alpha should be able to present
   it as one.

5. **Fix the synthesis-contract asymmetry** (carried over from the swarm research
   doc). Kimi's "only task-relevant outputs, not full traces" is the correct rule
   and Alpha applies it only on the deep-handoff path.

---

## 11. Limitations of this research

- **The paper is by the vendor.** Every `[M]` figure above is Moonshot's own
  evaluation of its own model. BrowseComp and WideSearch are third-party
  benchmarks; the in-house Swarm Bench is not and shows the largest gain.
- **No reproduction.** I did not run K2.5/K2.6/K3, nor reimplement PARL.
- **PARL hyperparameter values are not public** — λ₁, λ₂, m, λ, ρ are named in
  the paper but their values are not disclosed `[M]` (absent, not redacted). An
  independent implementation cannot faithfully retrain.
- **Pricing conflicts unresolved** — the K2.6 row has two published figures.
- **Third-party PARL implementations are unofficial.** The most-visible GitHub
  implementation explicitly disclaims affiliation with Moonshot and is built
  from the paper alone `[R]`.
- **K3 details are thin.** Open weights were due 2026-07-27; I have not verified
  what was actually published or whether K3 preserves the K2.5 PARL recipe.
- **Not covered:** K3-specific swarm architecture changes, Kimi Work / Kimi Code
  product surfaces in depth, the Moonshot infrastructure behind 300 parallel
  agents, licensing terms for the swarm feature specifically, and Chinese-market
  distribution/regulatory context.

---

## 12. Sources

**Primary**
- Kimi Team, *Kimi K2.5: Visual Agentic Intelligence*, arXiv:2602.02276v1 (02 Feb 2026) — https://arxiv.org/html/2602.02276v1 · https://huggingface.co/moonshotai/Kimi-K2.5
- Kimi, *Kimi Agent Swarm: 100 Sub-Agents at Scale* — https://www.kimi.ai/blog/agent-swarm
- Kimi Help Center, *Agent Swarm* — https://www.kimi.com/en/help/agent/agent-swarm
- Kimi Help Center, *Kimi Agent Overview* (release timeline) — https://www.kimi.ai/help/agent/agent-overview
- Moonshot AI — https://www.moonshot.ai

**Secondary** *(cited as `[R]`; several conflict — see §7)*
- InfoQ, *Moonshot AI Releases Open-Weight Kimi K2.5…*
- MorphLLM, *Kimi K2.5 Agent Swarm: 100 Parallel Sub-Agents, Benchmarks*
- mayhemcode, *Kimi Agent Swarm Review* (the "swarm tax"; K2.6 pricing variant)
- CloudZero, *Kimi API pricing in 2026* (K2.6 pricing variant)
- Zentor, *Kimi K3 Agent Swarm: 300 Parallel Agents* (reward balance ~80/20)
- DataCamp, *Kimi K2.5 and Agent Swarm: A Guide With Four Practical Examples*
- Andrey Lukyanenko, *Kimi K2.5 Review* (paper review)
- Maxime Labonne, *Kimi K2.5: Still Worth It After Two Weeks?* (verbosity, 89M vs 14M tokens)
- DeepInfra, *Kimi K2.6 API Benchmarks*; Venice, *Kimi K2.6*
- Phil Schmid, *How Kimi, Cursor, and Chroma Train Agentic Models with RL* (reward hacking: serial collapse / spurious parallelism)
- Reddit r/kimi, r/AIGuild — user reports (quota, practical limits)
- HackerNoon, Hackernoon; Towards AI (Feb 2026 technical deep-dive)

**Alpha-side**
- `SWARM_MODE_DEEP_RESEARCH.md` (companion; same evidence convention)
- `backend/packages/harness/alpha/swarm/` — `estimator.py`, `topology.py`, `planner.py`, `telemetry.py`
- `backend/packages/harness/alpha/deepagent/AGENTS.md`