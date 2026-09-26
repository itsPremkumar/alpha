# Learned Orchestration: Sakana Fugu, Trinity, Conductor

Source: Sakana AI, "Sakana Fugu Technical Report," arXiv:2606.21228v2 (23 June 2026). CC BY 4.0.

This is the most directly transferable material in this folder. It is a published, reproducible result
showing that **orchestration is a scaling axis independent of model scale** — and alpha is better
positioned to exploit it than almost any project, because alpha already has the swarm, groups,
deliberation and capability-dispatch machinery that Fugu had to build.

## The core claim

> "If capability can be amplified by composing existing models rather than only by training larger ones,
> then progress in AI need not depend solely on access to the largest training runs."

Measured, on benchmarks the orchestrator's own workers were not tuned for:

| Benchmark | Fugu-Ultra | Fugu | Claude Opus 4.8 | Gemini 3.1 | GPT-5.5 |
|---|---|---|---|---|---|
| SWE-Bench Pro | **73.7** | 59.0 | 69.2 | 54.2 | 58.6 |
| Terminal Bench 2.1 | **82.1** | 80.2 | 74.6 | 70.3 | 78.2 |
| LiveCodeBench | **93.2** | 92.9 | 87.8 | 88.5 | 85.3 |
| LiveCodeBench Pro | **90.8** | 87.8 | 84.8 | 82.9 | 88.4 |
| Humanity's Last Exam | **50.0** | 47.2 | 49.8 | 44.4 | 41.4 |
| CharXiv Reasoning | **86.6** | 85.1 | 84.2 | 83.3 | 84.1 |
| GPQA Diamond | 95.5 | **95.5** | 92.0 | 94.3 | 93.6 |
| SciCode | 58.7 | **60.1** | 53.5 | 58.9 | 56.1 |
| Long Context Reasoning | 73.3 | **74.7** | 67.7 | 72.7 | 74.3 |
| MRCR v2 | 93.6 | 86.6 | 87.9 | 84.9 | 94.8 |

The orchestrator **beats every model in its own worker pool** on agentic coding. That is the result that
matters: it is not a better model, it is a better *allocator*.

Honesty note carried from the report itself: *"All scores other than Fugu's are reported by the model
providers."* Only Fugu's numbers are first-party. Fable 5 and Mythos Preview are excluded from the pool
because they are not publicly accessible, so the comparison is not against the true frontier.

## Architecture: Fugu (latency-aware)

Builds on **Trinity** (Xu et al., 2025). Designed so orchestration overhead is negligible.

**A lightweight selection head parallel to the LM head.** Given worker pool of size `L`:

1. Forward pass to the final hidden layer, take hidden state `h ∈ ℝ^d`
2. Selection head emits `L` logits — one per worker
3. `argmax` → dispatch

**The key efficiency decision: decision-only parametrisation.** Fugu uses the orchestrator's *logits*, not
its *generated text*. Prompting and execution are the worker's job. So inference computes a hidden state at
an early token position, applies the head, and dispatches — **skipping autoregressive decoding entirely.**
That is what makes the latency comparable to a direct frontier-model call.

**Unlike Trinity, Fugu assigns no roles.** The selected model is always invoked as a worker. Narrowing the
coordination space to *selection only* is what keeps the decision cheap.

**Singular-value fine-tuning.** For selected weight matrices, decompose and train only the singular-value
scales, keeping orthogonal components fixed. An extremely small trainable set, and enough adaptation for
the routing representation.

## Training stage 1 — SFT on soft performance distributions

The interesting part is the *target*, not the method.

1. For each training question `q_i`, run **every** worker `n` times, score each against ground truth
2. Per-worker score: `r̄_{i,j} = (1/n) Σ_k r^{M_j}_k`
3. Convert to a **soft distribution** over workers rather than a hard argmax label:

```
p_i(j) = exp(r̄_{i,j} / τ) / Σ_{j'} exp(r̄_{i,j'} / τ)
```

4. Minimise `KL(p_i ‖ π_θ(·|q_i))`

**Why soft targets matter:** when several workers are near-equally capable, a hard label discards the
information that they *are* near-equivalent, and the router overfits to noise. The soft target encodes the
measured performance *distribution*. This is directly applicable to alpha — see the gap analysis.

Supervision comes from measured worker performance, not orchestrator generation, so the stage is stable
and cheap.

## Training stage 2 — sep-CMA-ES on end-to-end tasks

Single-step SFT gives clean correctness but misses how models behave *inside a harness*. So: collect real
multi-turn trajectories from **Claude Code, Codex, and OpenCode**, and build end-to-end tasks involving
repository context, iterative editing, tool calls, and execution feedback.

Maximise expected terminal reward directly:

```
J(θ) = E_{τ~π_θ}[R(τ)],  R(τ) ∈ {0,1}
```

sep-CMA-ES: parent `θ_t`, step size `σ_t`, diagonal covariance `D_t`; sample `λ` candidates
`θ⁽ᵏ⁾ = θ_t + σ_t D_t z⁽ᵏ⁾`; fitness by replicated end-to-end runs; top-μ recombined fitness-weighted.

**Why evolutionary rather than RL here:** the SFT stage already places parameters in a strong region, so
the search refines routing at finer granularity; and it optimises *end-to-end outcome* directly, which
matters when success signals are sparse or noisy and reliable per-step ranking labels cannot be built.
Empirically substantially more stable than SFT on the same end-to-end data.

**The finding that matters most for us:** end-to-end trajectories reveal differences invisible in
performance scores. Some models are strong at high-level reasoning and planning but unreliable operating
tools; others are unimpressive on standalone benchmarks but robust inside an interactive harness.
**Benchmarks do not measure the thing you need when the thing is a scaffold.**

## Architecture: Fugu-Ultra (quality-first)

Builds on **Conductor** (Nielsen et al., 2025). Up to **5 workflow steps**, trained with **GRPO, no KL
divergence penalty**.

Each step is a natural-language subtask + a worker id + an **access list** indexing which previous subtask
solutions enter the worker's context. That is enough to express best-of-N, sequential chains, and
arbitrary parallel trees.

**Two-stage reward:**

1. **Format condition** → `0` if subtasks / agents / access lists cannot be parsed
2. **Correctness** → `1` if the executed workflow's output matches ground truth, else `0.5`

**Group-relative advantage** over `G` completions: `A_i = (r_i − mean) / std`.

Worker pool: Gemini-3.1-Pro, Claude-Opus-4.8, GPT-5.5.

## The two mechanisms worth stealing outright

### 1. Intra-workflow agent isolation — preventing "orchestration collapse"

> "the first agent to interact with the environment sets the trajectory for all future agents, leading to
> redundant contributions from future agents as they are steered to follow the path initialized by the
> first agent."

Function calling in a multi-agent system means any agent can act at any time, so the orchestrator must
track *which* agent emitted each call and where it sits in the workflow, or call loops route to the wrong
agent.

Fugu's fix: **an agent observes other agents only through its access list.** Otherwise it sees a transcript
preserving only its own actions.

This prevents collapse — but note the cost: agents cannot build on each other's discoveries.

### 2. Persistent shared memory across workflows

Complete isolation is also suboptimal — agents would re-make the same tool calls rediscovering the same
artifacts. So:

- **Within** a workflow: agents are isolated from each other
- **Across** workflows in a multi-turn conversation: full shared memory of environment interactions

That tension — isolate to prevent collapse, share to prevent redundant rediscovery — is resolved by
*workflow boundary*, not by a global setting. This is a genuinely useful design pattern.

## Emergent optimal topologies

Not hand-designed; they appeared in analysis of strong trajectories:

| Topology | When it wins |
|---|---|
| **Debate and aggregation** | Divergent candidate generation, then converge |
| **Build and debug** | Separate agents for construction vs diagnosis |
| **Bringing in a specialist** | Generalist gets stuck; a targeted worker is injected |

And the per-step adaptivity finding: Fugu selects **one** model per input yet still beats GPT-5.5 on
Terminal Bench, by **alternating** between GPT-5.5 and Claude-Opus-4.8 and calling Opus at *particular,
critical debugging points*. The router learned a temporal policy, not just a per-query mapping.

## Why this is a behavioural analogue of model merging

Parameter-space merging needs weight access and architectural compatibility, so it only works on
open-source checkpoints. Data-flow merging needs activation access. Both are inapplicable to heterogeneous
API-only systems.

Fugu composes at the **behavioural** level, treating frontier models as black-box agents and learning to
route, coordinate, verify and synthesise. No parameter access, no architectural compatibility — so it works
across closed-source models, heterogeneous providers, and user constraints (favour a provider, exclude a
model, respect data-residency) **without retraining**.

**Composability as user control** is an underrated part of this: pools are configurable, so a new frontier
model is incorporated by adding it to the pool, not by retraining.

## What this means for alpha

| Fugu component | alpha's equivalent | State |
|---|---|---|
| Learned router over a worker pool | `bots/capability_dispatch.py` (761 ln) | exists; selection is by department/name, not learned |
| Soft performance-distribution targets | `evaluation/`, `benchmarks/`, `autonomous_benchmark_harness.py` | measurement exists; not fed to dispatch |
| `capability_tags` | `capabilities/catalog.py`, `capabilities/eligibility.py` | exists; consulted only for leader election |
| Auction-based selection | `ContractNetAuctionEngine` | **referenced only by its own test — never runs** |
| Task→agent matching | `match_bot_for_task` | reachable only from a manual endpoint |
| Intra-workflow isolation | — | **absent** |
| Shared memory across workflows | `memory/` (~20 subsystems) | **far ahead** |
| Multi-worker debate | `deliberation/` (debate, adversary, council, MoA) | **ahead** |
| Specialist injection | `subagents/builtins/` (10 specialists) | **ahead** |

**alpha already owns the expensive half** — the multi-agent substrate and the memory. What it lacks is the
learned allocation policy on top, and its allocation policy is currently *unwired* rather than merely
unsophisticated. That is the cheapest high-value improvement in this entire dossier.

The three mechanisms to port first, in order:

1. **Intra-workflow agent isolation.** alpha's groups and swarm fan-out almost certainly suffer
   orchestration collapse, where the first agent's trajectory constrains every sibling. This is a real
   quality bug today, not a future feature.
2. **Soft performance targets from measured outcomes.** alpha has the measurement infrastructure; wiring it
   to dispatch converts a dead component into a working one.
3. **Temporal routing policy.** Fugu learned *when* in a task to switch specialists, not just which.
   alpha's swarm has no notion of this.

The full gap analysis is in [`04_alpha_gap_analysis.md`](04_alpha_gap_analysis.md).
