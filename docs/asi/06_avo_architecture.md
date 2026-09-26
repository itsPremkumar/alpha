# NVIDIA AVO — Architecture Deep Dive

Paper-level detail from **arXiv:2603.24517**, "AVO: Agentic Variation Operators for Autonomous
Evolutionary Search" (25 March 2026, cs.LG). Terry Chen, Zhifan Ye, Bing Xu, Zihao Ye, Timmy Liu, Ali
Hassani, Tianqi Chen, Andrew Kerr, Haicheng Wu, Yang Xu, Yu-Jung Chen, Hanfeng Chen, Aditya Kane, Ronny
Krashinsky, Ming-Yu Liu, Vinod Grover, Luis Ceze, Roger Bringmann, John Tran, Wei Liu, Fung Xie, Michael
Lightstone, Humphrey Shi — NVIDIA.

This is the technical companion to [`05_nvidia_avo.md`](05_nvidia_avo.md), which covers the results and
the ARC-AGI-3 transfer. This document covers the mechanism.

---

## 1. The exact problem AVO solves

Classical evolutionary search maintains a population `P = {(xᵢ, f(xᵢ))}` and iterates:

```
P_{t+1} = Update(P_t, (x_{t+1}, f(x_{t+1})))      x_{t+1} = Vary(P_t)
```

`Vary` — the **variation operator** — is the mechanism that produces new candidates. That is the whole
formalism, and the whole contribution is what `Vary` becomes.

### 1.1 What prior work does (and why it is limiting)

**LLM-augmented variation** decomposes the operator in two:

```
Vary(P_t) = Generate(Sample(P_t))
```

- `Sample` selects parents from `P_t`, guided by score- and diversity-based heuristics
- `Generate` is the LLM, prompted with the sampled parents

Named systems in the paper:

| System | `Sample` | `Generate` |
|---|---|---|
| **FunSearch** | fixed heuristic parent selection | LLM, single call |
| **AlphaEvolve** | island-based DB inspired by MAP-Elites; prompt sampler with predefined fitness/diversity heuristics | LLM, single call |
| **LoongFlow** | MAP-Elites archive with **Boltzmann selection** | LLM inside a fixed **Plan-Execute-Summarize** pipeline |
| **EvoPrompting** | fixed | LLM |

**Learned variation** goes further:

| System | What is learned | What stays fixed |
|---|---|---|
| **TTT-Discover** | `Generate` — the LLM policy itself, via test-time gradient updates | `Sample` remains a fixed algorithm: **PUCT-based selection rule**; a buffer manages the population with predetermined update rules |

The paper's diagnosis of all of these:

> "the LLM only participates in Generate: the sampling strategy, evaluation protocol, population
> management, and the order of operations are all determined by the framework, not by the LLM."

And the consequence:

> "**it produces a single output per invocation, with no ability to proactively consult reference
> materials, test its changes, interpret feedback, or revise its approach before committing a candidate.**"

### 1.2 What AVO does

```
Vary(P_t) = Agent(P_t, K, f)
```

**One autonomous agent run replaces the entire decomposition.** The agent subsumes `Sample`, `Generate`,
*and* evaluation. It has full agency over when to consult references, what to test, and how to revise.

| Symbol | Meaning in AVO |
|---|---|
| `P_t` | the **full lineage** of solutions and their scores |
| `K` | a **domain-specific knowledge base** — CUDA guides, PTX ISA docs, Blackwell specs, FA4 source |
| `f` | the **scoring function** |
| `x_i` | a CUDA kernel: source code with inline PTX |

**`f` is `n`-dimensional:** correctness against a reference implementation, plus throughput in TFLOPS per
test configuration, `f(xᵢ) = (f₁(xᵢ), …, fₙ(xᵢ))`.

**And the critical hard gate:**

> "A candidate `xᵢ` that fails correctness **is assigned zero score** (i.e., `f_j(xᵢ) = 0`) regardless of
> throughput."

Correctness is not a weighted term. It is a gate that zeroes the candidate.

**Two scoping decisions worth noting:**

- AVO is **orthogonal to population structure** — it could be used in archive-based, island-based, or
  single-lineage regimes. The paper studies **single-lineage** deliberately, "to isolate the effect of the
  operator itself."
- Branching and archive management are explicitly **left to future extensions.**

---

## 2. Anatomy of a single variation step

A variation step producing `x_{t+1}` from lineage `P_t` is an autonomous agent loop. Observed behaviour,
per the paper:

1. The agent examines **multiple prior implementations** in `P_t` within a *single* step, **comparing their
   profiling characteristics** to identify bottlenecks and opportunities
2. It consults `K` to understand relevant hardware constraints
3. It implements a candidate optimization
4. It invokes `f` to test
5. On failure — correctness failure, or no improvement on the benchmark suite — it **diagnoses and revises**,
   repeating **edit → evaluate → diagnose** until it commits

**Strategy shifts over the run:**

> "early steps may focus on structural changes informed by reference implementations in `K`, while later
> steps can shift toward micro-architectural tuning guided by profiling feedback from `f` and patterns
> observed across the accumulated lineage `P_t`."

### 2.1 The commit rule — this is the part alpha is missing

> "In our current implementation, **we persist a new committed version only when it passes correctness
> checks and matches or improves the benchmark score relative to the best committed version so far;
> unsuccessful intermediate attempts remain part of the agent's internal search trajectory but are not added
> to the committed lineage.**"

Four properties, all of which matter:

1. **Correctness is a hard gate**, not a score term
2. **Comparison is against the best committed version**, not the previous one — a regression is rejected
3. **Failed attempts stay in the trajectory but not the lineage** — the search history is preserved without
   polluting the committed set
4. Each committed version is **a git commit with its score** — full state continuity across the whole run

**An honest limitation of AVO's own gate:** it is *score-based*. There is no invariant check beyond
correctness. A kernel that improves throughput while violating some unmeasured behavioural property would be
accepted. alpha's `testing/differential_invariant_fuzzer.py` is, on this narrow axis, **ahead of AVO** —
alpha has the better primitive and has not wired it to a promotion decision.

### 2.2 Continuous evolution and the supervisor

Each committed version is a git commit. The agent loop runs without human intervention.

**Two named failure modes:**

| Failure mode | Description |
|---|---|
| **Stall** | the agent exhausts its current line of exploration |
| **Unproductive cycles** | edits that repeatedly fail to improve scores |

**The self-supervision mechanism:**

> "Once triggered, the mechanism reviews the overall evolutionary trajectory and **steers the search toward
> several candidate optimization directions**. This conditional intervention effectively redirects
> exploration with fresh perspective when the current strategy has plateaued."

Note it redirects toward **several** candidate directions, not one — it re-opens the fan-out rather than
picking a single successor. And it operates on the **trajectory**, not the current step.

---

## 3. Experimental setup

**The agent.** An internally-developed general-purpose coding agent powered by frontier LLMs. Tools:
autonomous code editing, shell execution, filesystem navigation, documentation retrieval.

**Persistent memory is the conversation history:**

> "It maintains persistent memory through its conversation history, which accumulates the full context of
> prior edits, compiler outputs, profiling results, and reasoning across the evolutionary process."

That is the entire memory implementation. Worth noting against alpha's ~20 memory subsystems — see §6.

**No specialisation:**

> "**No task-specific modifications are made to the agent for kernel optimization**; the same agent used for
> general software engineering tasks is deployed here, with the domain-specific knowledge base `K` and
> scoring function `f` provided to the agent."

**Hardware/software.** NVIDIA B200 GPUs, CUDA 13.1, PyTorch 2.10.0.

**Baselines.**
- **cuDNN 9.19.1** — NVIDIA's closed-source attention kernel, includes custom Blackwell optimisations
- **FlashAttention-4** — official implementation, commit `71bf77c`

**Benchmark configurations.**
- Forward-prefill throughput, head dimension 128, BF16
- Sequence lengths {4096, 8192, 16384, 32768}
- Total tokens held at 32768 by adjusting batch size per sequence length (batch 8 at seq 4096, batch 1 at
  seq 32768)
- **MHA:** 16 heads, causal and non-causal
- **GQA:** two Qwen3-family configurations — 32 Q heads / 4 KV heads (group 8, as Qwen3-30B-A3B) and
  32 Q heads / 8 KV heads (group 4, as Qwen3-8B)
- Uses **FA4's own timing script**, same warm-up and repeat rounds as the FA4 paper
- **Ran the experiment 10 times** for average and standard deviation
- Same setup for both agent evolution and final benchmarking

---

## 4. Results

### 4.1 Multi-head attention

| Masking | vs cuDNN | vs FA4 |
|---|---|---|
| **Causal** | **+0.4% to +3.5%** across all configurations | **+5.0% to +10.5%** |
| **Non-causal** | +1.8% to +2.4% at sequence lengths > 16384; **within measurement noise at shorter sequences** | — |

Peak throughput: **up to 1668 TFLOPS** at BF16.

The non-causal honesty is worth preserving: at shorter sequences AVO does **not** beat the baselines, and the
paper says so plainly.

### 4.2 Grouped-query attention — transfer test

The evolved MHA kernel was adapted to GQA **by prompting the agent**, with no human guidance on required
changes. Completed in **~30 minutes**.

| Masking | vs cuDNN | vs FA4 |
|---|---|---|
| Causal | up to **+7.0%** | up to **+9.3%** |
| Non-causal | up to **+6.0%** | up to **+4.5%** |

> "the optimizations discovered by the agent during MHA evolution are **not specific to the MHA
> configurations used during evolution**, but generalize to the distinct compute and memory access patterns
> of GQA."

This is the real generality claim: the agent learned *hardware* reasoning, not a config-specific trick.

---

## 5. Trajectory analysis — the most transferable section

### 5.1 Committed versions are a tiny fraction of the search

> "The 40 committed versions shown in the trajectory represent **only the successful outcomes of a much
> larger search**. Over the 7-day evolution, the agent **explored over 500 candidate optimization
> directions internally**, including attempts that failed correctness checks, regressed throughput, or were
> abandoned after profiling."

**Ratio: ~12 failed-or-abandoned attempts per committed version.**

This is the most important number in the paper for anyone building an evolutionary loop. The committed
trajectory is not the work; it is the residue.

### 5.2 Progress is discrete, not gradual

Throughput improves in **distinct steps separated by plateaus**. The five largest gains correspond to
**architectural inflection points**:

| Version | Change |
|---|---|
| **v8** | QK-PV interleaving with bitmask causal masking |
| **v13** | restructured single-pass softmax computation |
| **v20** | branchless accumulator rescaling + lighter memory fence for unmasked iterations |
| **v30** | correction/MMA pipeline overlap |
| **v33** | register rebalancing across warp groups |

Remaining 35 versions contribute individually smaller but collectively substantial micro-architectural
refinement.

**Implication:** a search that only records incremental improvements will look like it is plateauing while
the real progress is concentrated in a few structural discoveries. Measure *what kind* of change was made,
not just how much.

### 5.3 Diminishing returns

- **v1–v20:** largest absolute gains per version, closing the gap between naive and well-optimised
- **v21–v40:** smaller but compounding, via cycle-level scheduling and refined resource allocation

---

## 6. The three analysed optimizations

Each required **jointly reasoning about multiple hardware subsystems** — synchronisation and memory
ordering, pipeline scheduling, register allocation — "rather than tuning any single parameter in isolation."

### 6.1 Branchless accumulator rescaling (v19 → v20)

**Measured: +8.1% non-causal, +1.6% causal geomean.** The largest single optimization.

**Bottleneck.** Online softmax maintains a running row-maximum. When it changes as new key blocks are
processed, the output accumulator `O` must be rescaled. v19 did this with a **conditional branch**: check
whether any thread in the warp needed rescaling, skip if unchanged. That avoids wasted work but:

- introduces **warp synchronisation overhead on every iteration** of the key-block loop
- the conditional control flow **prevents lighter memory fences** in the correction path

**The fix.** Replace the branch with a **branchless speculative path**. The rescale factor is *always*
computed; a predicated select substitutes `1.0` when rescaling is unnecessary. The cost of a
multiply-by-one is negligible against the synchronisation overhead removed.

**The second-order effect is the interesting part.** Removing the branch also removed **warp divergence** in
the correction path, which allowed replacing a **blocking memory fence** (stalls until all pending writes
complete) with a **non-blocking fence** (merely enforces ordering). The paper is explicit about why the
weaker fence is safe:

> "The non-blocking fence is safe here because the branchless path guarantees that all threads in the warp
> follow the same control flow, **ensuring reconvergence before the next synchronization point**."

That is a two-step causal chain: remove a branch → remove divergence → earn the right to a weaker fence.
No amount of local tuning gets there.

**Why the causal/non-causal asymmetry:** the branchless path applies only to **fully unmasked** iterations.
Non-causal attention processes all key blocks unmasked; causal retains the branched logic for masked
blocks. Hence +8.1% vs +1.6%.

### 6.2 Correction/MMA pipeline overlap (v29 → v30)

**Measured: +1.1% non-causal, +0.4% causal.**

**Bottleneck.** FA4's pipeline processes two Q-tiles concurrently (**dual Q-stage**), each needing a PV
GEMM followed by output normalisation by the correction warp. In v29 the two stages were **serialised at
the MMA-to-correction boundary**: the correction warp waited for *both* PV GEMMs before normalising either,
leaving it **idle throughout the second GEMM**.

**The fix.** Restructure so the correction warp begins normalising the first stage's output **as soon as its
own PV GEMM completes**, overlapping that work with the second stage's GEMM. A sequential dependency becomes
pipelined execution.

### 6.3 Register rebalancing across warp groups (v32 → v33)

**Measured: +2.1% non-causal, ~0% causal.**

**Bottleneck.** Blackwell partitions a fixed budget of **2048 warp-registers per SM** across warp groups.
v32 followed FA4's allocation: **192 / 80 / 48** (8 softmax warps / 4 correction warps / remaining 4).

**Profiling revealed** the correction warp group was **spilling values to slower local memory** under its
80-register budget, while the softmax group had substantial headroom.

**The fix.** Redistribute **8 registers from the softmax group to each of the other two** → **184 / 88 / 56**.

**Why this was viable** — and this is the part that shows real reasoning rather than knob-turning: the
redistribution is only safe because *the AVO kernel's own softmax implementation* processes score values in
small fragments with packed arithmetic, giving low peak register usage and ample headroom even at 184. The
agent's earlier architectural choices **created the budget** that made a later reallocation possible.

The correction warps benefit specifically because, **following the v30 pipeline overlap**, they now run
concurrently with the second PV GEMM and are therefore on the execution critical path. Two changes
compound: v30 created the critical-path exposure, v33 relieved it.

---

## 7. What this means for alpha — precise mapping

| AVO element | alpha | Verdict |
|---|---|---|
| `Vary = Agent(P, K, f)` — agent decides the variation | `runtime/sentinel/` is a **fixed** OBSERVE→DIAGNOSE→FIX→VERIFY→COMMIT pipeline | **Partial.** The variation step is prescribed, not chosen. |
| Knowledge base `K` consulted on demand | `research/`, `coding/`, docs retrieval tools | Present |
| `f` as a multi-dimensional score vector | `evaluation/`, `benchmarks/`, `autonomous_benchmark_harness.py` | Present |
| **Correctness as a hard zero-gate** | `runtime/runs/verification.py` | Present but not enforced at a promotion point |
| **Commit only if ≥ best committed** | none | **MISSING** |
| **Failed attempts in trajectory, not lineage** | `bots/governance_ledger.py` (events, no candidate distinction) | **MISSING** |
| **Each committed version = git commit + score** | none — no candidate versioning | **MISSING** |
| **Stall / unproductive-cycle detection** | `metacognition/monitor.py`, `reasoning/governor.py`, `loop_detection_middleware.py` | Exists, **intervention unverified** |
| **Supervisor redirects to several directions** | none | **MISSING** |
| ~12 failed attempts per commit | not measured | **MISSING** as a tracked metric |
| Trajectory records *kind* of change (5 inflection points) | none | **MISSING** |
| Diminishing-returns curve | none | **MISSING** |
| **No task-specific agent modification** | `subagents/builtins/` — 10 specialists | **Opposite bet.** AVO's evidence is that one general agent + good tools beat specialisation. |
| Persistent memory | ~20 subsystems incl. dreaming | **alpha far ahead** — and AVO's is just the transcript |
| Agent self-contained, no task-specific edits | — | alpha's `lead_agent/prompt.py` word-stream guard suggests a similar instinct |

### The four highest-value transfers, in order

1. **The commit rule.** Correctness as a hard gate, comparison against *best committed*, failed attempts
   kept out of the lineage but preserved in the trajectory, each commit versioned with its score. This is
   one function and it is the thing that makes self-modification safe.
2. **Candidate lineage.** `champion`/`challenger` = 0 files in alpha. AVO's `P_t` is a first-class object
   with scores; alpha's ledger is a flat event stream.
3. **A supervisor that redirects to several directions on stall**, rather than a loop detector that kills.
4. **Track the failed:committed ratio and the kind of each change.** ~12:1 is the honest denominator, and
   "5 discrete inflection points among 40 versions" is the honest shape of progress. Without those, any
   self-improvement metric in alpha will read as smooth continuous gain, which is not what happens.

### The one place alpha is ahead of AVO

AVO's commit rule is **score-based only** — there is no invariant check beyond correctness. A change that
improves the score while violating an unmeasured property would be committed.

alpha has `testing/differential_invariant_fuzzer.py`, an invariant-preserving differential fuzzer. It is
better than AVO's gate on this axis. It is also not wired to any promotion decision.

**The correct synthesis is therefore not "copy AVO."** It is: take AVO's commit structure and add alpha's
invariant fuzzer as the gate AVO lacks. Neither project has the whole thing.
