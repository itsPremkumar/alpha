# ASI / AGI / RSI Reference

Research dossier on artificial superintelligence, artificial general intelligence, and recursive
self-improvement — collected 2026-09-26, with the gap analysis against this codebase.

## What this folder is for

Three things, in order of how much they matter:

1. **A measured record of what frontier systems actually do**, with numbers and sources, so nobody
   in this project argues from vibes. Every quantitative claim here carries a URL.
2. **The orchestration architectures that produced the strongest recent results**, because they are
   directly transferable to alpha and are far better documented than the speculative stuff.
3. **An honest gap analysis** of alpha against all of it.

## Evidence labels used throughout

| Label | Meaning |
|---|---|
| **MEASURED** | Published measurement, with a source. |
| **VENDOR-CLAIMED** | A lab's own report of its own system. Directionally useful, not independently verified. |
| **SPECULATIVE** | Reasoning or forecast. Not evidence. |
| **UNCONFIRMED** | Named in secondary sources; I could not verify it from a primary source. |

Do not upgrade a label without a primary source. That discipline is the entire point of this folder —
see `../IMPLEMENTATION_MATRIX.md` for the same convention applied to our own work.

## Documents

| File | Contents |
|---|---|
| [`01_state_of_evidence.md`](01_state_of_evidence.md) | What is actually measured about RSI and takeoff, with the hard numbers and the counter-arguments. |
| [`02_learned_orchestration.md`](02_learned_orchestration.md) | Sakana Fugu, Trinity, Conductor. Learned routing, evolutionary optimisation of an orchestrator, and intra-workflow agent isolation. The most directly transferable content here. |
| [`03_agentic_architecture.md`](03_agentic_architecture.md) | Architectural patterns for agent systems that actually work: scaffolds, isolation, the trust boundary, evaluation, and the failure modes that recur. |
| [`04_alpha_gap_analysis.md`](04_alpha_gap_analysis.md) | Where alpha stands against all of the above, honestly, including where alpha is ahead. |
| [`05_nvidia_avo.md`](05_nvidia_avo.md) | NVIDIA AVO — the agent-decided variation operator, 100% on ARC-AGI-3, and the security argument that indicts alpha's single-envelope design. |
| [`06_avo_architecture.md`](06_avo_architecture.md) | AVO at paper level: the `Vary(P) = Agent(P, K, f)` formulation, the commit rule, the trajectory analysis, and three optimizations with ablations. |
| [`07_agent_stack_security.md`](07_agent_stack_security.md) | NVIDIA's layered agent stack, the six common security gaps, five design rules, four security profiles — and what each says about alpha. |
| [`SOURCES.md`](SOURCES.md) | Every URL, what it supports, and when it was retrieved. |

## The three findings that matter most

**1. Task-length time horizons are doubling every ~4 months, not ~7.** MEASURED (METR, via Anthropic).
March 2024: ~4-minute tasks. March 2025: ~1.5 hours. March 2026: 12 hours. Claude Mythos Preview is at
"at least" 16 hours — the top of what METR can currently measure. This is the single most decision-relevant
number in the folder. It is also *why* capability keeps compounding: the bottleneck moves from writing code
to choosing what to work on.

**2. Orchestration is a scaling axis independent of model scale.** MEASURED (Sakana Fugu, arXiv
2606.21228v2). A learned orchestrator routes each query to the right frontier model and beats every model
in its own pool — 73.7 on SWE-Bench Pro where the best individual worker scores 69.2. This is the most
important *architectural* result here, and alpha is better positioned for it than almost anything else,
because alpha already has swarm, groups, deliberation and capability dispatch.

**3. Full RSI has not happened, and the interesting bottleneck is judgement, not execution.** MEASURED +
VENDOR-CLAIMED (Anthropic Institute, 2026-09). AI is superhuman at executing a well-specified experiment
(~52× kernel-optimisation speedup versus a human's ~4× in 4-8 hours) and at picking a next step 64% of the
time. It is *not* yet reliable at deciding which problems are worth solving. That gap — execution versus
direction-setting — is the actual frontier, and it maps precisely onto alpha's dead `ContractNetAuctionEngine`
and unwired capability dispatch.

## Read this before quoting any of it

The most common failure mode in this project is asserting a state that was never measured. That failure
has a direct equivalent in the literature: OpenClaw publishes a "what we do not claim" section listing its
own holes, Anthropic footnotes its own flattering statistics ("8× lines of code is almost certainly an
overstatement"), and Sakana notes that "all scores other than Fugu's are reported by the model providers."

Follow that convention. If a number here is inconvenient for a roadmap, it is more likely the number is
right than the roadmap.

## Unconfirmed names

**"NVIDIA avo" — RESOLVED.** An earlier revision of this folder recorded it as unconfirmed and guessed it
might be Alpamayo. That was wrong. AVO is **Agentic Variation Operators**, NVIDIA's long-horizon autonomous
agent architecture (arXiv:2603.24517), and it turned out to be the most directly relevant external reference
to alpha's architecture in this dossier. See [`05_nvidia_avo.md`](05_nvidia_avo.md). The error is recorded
in `SOURCES.md` rather than quietly overwritten.

**"ChatGPT Astra" — still unconfirmed.** No OpenAI model by that name was found in any primary source.
Verified OpenAI models in this period are GPT-5.5, GPT-5.6 Sol, and the GPT-6 Sol / Terra / Luna variants.
Recorded as UNCONFIRMED. If "Astra" is a real model I failed to find, provide a source and I will research it
properly rather than guess.
