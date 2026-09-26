# NVIDIA AVO — Agentic Variation Operators

Primary source: NVIDIA Technical Blog, 21 August 2026, "NVIDIA AVO Reaches 100% on ARC-AGI-3."
Paper: **arXiv:2603.24517**, "AVO: Agentic Variation Operators for Autonomous Evolutionary Search."
Security companion: "Building Agent Systems for Both Long-Horizon Capability and Enforceable Security,"
NVIDIA Developer Forums, 21 August 2026.

> **Correction to this folder's earlier record.** `README.md` and `SOURCES.md` previously listed
> "NVIDIA avo" as **UNCONFIRMED**, speculating it might be Alpamayo. That was wrong. AVO is real, it is
> NVIDIA's long-horizon autonomous agent architecture, and it is the single most relevant external
> reference to alpha's architecture in this entire dossier. The earlier record has been corrected rather
> than left standing, because a wrong note in a reference folder is worse than no note.

---

## 1. What AVO is

A **general-purpose coding agent system** built by NVIDIA around one idea:

> "In our GPU-kernel optimization work, AVO **replaces the predefined variation step of conventional
> evolutionary-search systems with an autonomous agent** that decides how to generate the next
> candidate — what to inspect, what to change, what to test, and what to commit."

That is the whole contribution. Classical evolutionary search has a fixed mutation operator. AVO replaces it
with an LLM agent that decides the mutation. Everything else — the population, selection, fitness — is
conventional.

**Architecture** (from the paper's Figure 1):

```
inputs
  ↓
main agent  ⟲  inspect context → plan → implement → evaluate
  │              (persistent memory + tools)
  ↓
candidate selection + lineage updates
  ↓
supervisor  ← monitors the broader search trajectory, intervenes on stagnation
```

Two mechanisms carry the long-horizon capability:

- **Persistent memory** — carries forward prior implementations, evaluation results, compiler and profiler
  output, and accumulated reasoning, so the agent resumes from current state instead of reconstructing the
  search.
- **Supervisor** — watches the *broader trajectory* for stagnation or repeated unproductive cycles and
  **redirects the main agent** when progress stalls. Note this is a trajectory-level monitor, not a
  step-level one.

## 2. Result 1 — GPU-kernel optimisation

| Measure | Value |
|---|---|
| Continuous autonomous operation | **7 days** |
| Optimisation directions explored | **500+** |
| Kernel versions committed | **40** |
| vs cuDNN (multihead attention, DGX B200) | up to **+3.5%** |
| vs FlashAttention-4 | up to **+10.5%** |
| Adaptation to grouped-query attention | **~30 minutes** additional autonomous work |

The claim being made is not "the kernel is fast." It is: *the system sustained a productive engineering
loop over seven days and 500 iterations without each step being manually prescribed.*

## 3. Result 2 — ARC-AGI-3, and the benchmark-harness distinction

ARC-AGI-3: the agent enters unfamiliar game-like environments with **no instructions, no rules, and no
stated goal**, and must infer the dynamics through interaction.

| Measure | Value |
|---|---|
| Score | **100.00 RHAE** |
| Environments | **25 / 25** (public set) |
| Levels | **183 / 183** |
| Environment actions used | **6,624** |
| VISTA comparator, same 183 levels | 7,542 actions (**AVO ~12% fewer**) |
| Claude Opus 5 model-only baseline (ARC Prize, High reasoning) | **~30%** |
| Observation modality | **text-only**, exact 64×64 text grid, **no image tokens** |

**The load-bearing sentence:**

> "**Evaluating a model is not the same as evaluating an agent.** Model capability matters enormously, but
> the surrounding system determines how effectively that capability can be converted into sustained
> autonomous progress."

And the generalisation:

> "What transfers is not domain knowledge, but the machinery for sustained autonomous progress."

That is the same thesis as Sakana Fugu's result (see [`02_learned_orchestration.md`](02_learned_orchestration.md)),
reached from a different direction: Fugu showed a learned orchestrator beating every model in its own pool;
AVO shows one architecture transferring from GPU kernels to interactive reasoning. **Both say the harness,
not the model, is the lever.**

### Cross-model behaviour

AVO was also paired with **GPT-5.6 Sol** on a subset. Result: *"Sol reached matched levels faster in
wall-clock time in several cases, while Opus used fewer environment actions."* NVIDIA calls these
**"complementary operating profiles across models"** and explicitly leaves systematic comparison to future
work. This is the same finding Fugu reported: different models are strong at different things, and the
router should know which.

### NVIDIA's own caveats — preserved verbatim in spirit

These are unusually good, and they should be carried forward rather than dropped:

- *"This should not be interpreted as a controlled ablation: the two systems differ in agent backend,
  observation representation, memory, context management, and other implementation details."*
- On the memory system: *"although this experiment does not isolate its individual contribution."*
- *"These numbers therefore should not be interpreted as a direct measurement of the performance
  contribution of AVO; rather, they illustrate that model-level evaluation alone does not characterize the
  performance of a complete agent."*
- Results cover the **public set only** — not the semi-private or private competition sets.

**Label: VENDOR-CLAIMED.** The 30% → 100% framing in secondary coverage ("AVO pushed Claude Opus 5 to a
perfect score") is marketing compression of a claim NVIDIA itself refuses to make as a causal measurement.
The honest statement is: *the same model, inside a different harness, scored very differently on a
benchmark that reports a model-only number.*

### Comparators worth knowing

- **VISTA** — a visual harness; instantiates with Claude Opus 5 via Claude Code, or GPT-5.6 Sol via Codex
- **Tycho** — active abstraction with programmatic world models (arXiv:2607.28287)

---

## 4. The security post — the part that indicts alpha's architecture

Published the same day, and more important for this project than the benchmark result.

> "Prompts, model safeguards and harness logic **can guide** an agent's behavior, but they **do not create
> an authoritative boundary** around what it can do."

> "The agent and harness should be able to **propose** actions; identity, policy enforcement, credentials,
> isolation, and auditability should remain in the runtime environment."

And the sentence that should be quoted in alpha's threat model:

> "**The harness is intentionally programmable.** It can be extended, modified, and composed with new
> tools. **Security controls that depend on the harness behaving exactly as expected can weaken** as
> models, tools, and agent workflows evolve."

**Design principle:**

| Layer | Responsibility |
|---|---|
| **Above the boundary** | models, agents, harnesses, tools — reason, plan, **propose** |
| **Below the boundary** | the runtime — **binds actions to identity, applies policy, enforces what is allowed** |

NVIDIA's implementation of the lower layer is **OpenShell**, a kernel-level sandbox.

### What this says about alpha

`bots/authority_ceiling.py` is a **harness-level** control. By NVIDIA's argument it is therefore a guide,
not a boundary — and alpha's case is strictly worse than the case NVIDIA is describing, because:

1. alpha's harness is **self-modifying** (`bots/self_modification.py`, `metacompiler/dynamic_tool_synthesizer.py`)
2. the ceiling is enforced by a component **inside** that harness
3. the whole stack shares **one process envelope** with the credentials and the shell

So alpha's authority ceiling is a lock whose key is held by the thing it constrains. The requirement
"alpha cannot modify the component that enforces the ceiling" is necessary but **not sufficient** — it
assumes the component cannot be reached around, and in a single envelope with the agent's own code it can.

**This does not mean alpha must adopt OpenShell.** It means the current design must stop describing
`authority_ceiling.py` as a boundary, in code comments, in docs, and in reports, and must record it as a
**known limitation** — which is exactly what OpenClaw does with its "what we do not claim" section.

---

## 5. What transfers to alpha, and what does not

| AVO component | alpha's equivalent | Verdict |
|---|---|---|
| **Agent-decided variation operator** | `runtime/sentinel/` OBSERVE→DIAGNOSE→FIX→VERIFY→COMMIT | **Partial.** Sentinel is a *fixed pipeline*. AVO's contribution is that the agent decides what to inspect, change, test and commit. alpha's variation step is predetermined. |
| **Supervisor watching for stagnation** | `metacognition/monitor.py`, `reasoning/governor.py`, `loop_detection_middleware.py` | **Exists, unverified.** The question is whether it *intervenes* or merely observes. AVO's supervisor **redirects the agent**. |
| **Candidate selection + lineage updates** | `bots/governance_ledger.py` (events) | **MISSING.** No candidate lineage, no champion/challenger, no record of what was tried and why it was rejected. Confirmed by scan: `champion` 0 files, `challenger` 0 files. |
| **Persistent memory across the search** | `memory/` — ~20 subsystems, incl. `dreaming/phases.py` | **alpha is AHEAD.** |
| **Grounded external feedback** | `runtime/runs/verification.py`, `testing/differential_invariant_fuzzer.py` | **Present and good.** |
| **Below-the-boundary enforcement** | `authority_ceiling.py` (harness-level) | **Architectural gap** — see §4. |
| **Deterministic promotion gate** | none | **MISSING.** AVO's "what to commit" is agent-decided; alpha has no gate that can refuse. |

### The two highest-value transfers

**1. Candidate lineage.** This is the missing half of my earlier "no promotion gate" finding. A promotion
gate says *whether* to accept a change. Lineage says *what was tried, what was selected, what was rejected
and why* — which is what makes a self-modifying system auditable rather than merely gated. alpha has an
event ledger; it does not have a candidate tree.

**2. An agent-decided variation step with a deterministic commit gate.** This is the synthesis of AVO and
Hermes `/goal gate`, and it is the actual shape of safe self-improvement:

```
agent decides what to change   (AVO: the variation operator is the agent)
  → candidate evaluated in a sandbox      (alpha: reversible_delete, checkpoint_mode — present)
  → invariant suite must pass             (alpha: differential_invariant_fuzzer — present)
  → DETERMINISTIC GATE, exit code authoritative, BEFORE commit   (alpha: MISSING)
  → lineage recorded                      (alpha: MISSING)
  → supervisor watches for stagnation     (alpha: exists, unverified)
```

alpha has rows 2, 3 and 6. It is missing rows 4 and 5 — and row 4 is the one that makes the whole thing
safe rather than merely powerful.

### What does not transfer

- **The 30% → 100% framing.** That is model-plus-harness on a benchmark with a model-only score. It is not
  a measurement of harness contribution, and NVIDIA says so.
- **Kernel optimisation as a target domain.** Irrelevant to alpha.
- **NVIDIA's sandbox.** Adopting OpenShell is a platform decision with real cost, not a design detail.
  alpha's honest move is to *document* the single-envelope limitation.

---

## 6. The combined thesis, across all four references

| Source | Claim |
|---|---|
| **Sakana Fugu** | A learned orchestrator beats every model in its own pool (73.7 vs 69.2 on SWE-Bench Pro). Orchestration is a scaling axis. |
| **NVIDIA AVO** | One harness transfers from GPU kernels to interactive reasoning. *"Evaluating a model is not the same as evaluating an agent."* |
| **Anthropic** | Execution is superhuman; choosing what to work on is not. That gap is the frontier. |
| **OpenClaw** | A policy control inside one envelope is not a boundary. Harnesses are programmable, so harness-level security decays. |

All four point the same way, and it is not the direction this project has been going.

**The lever is the system, not the model.** alpha has more agent machinery than any of these projects —
swarm DAGs, groups, deliberation, ten specialists, ~20 memory subsystems, an authority ceiling concept that
neither comparator has. What alpha lacks is the small number of things that make the machinery
*trustworthy*: a deterministic promotion gate, candidate lineage, and an honest statement of which controls
are boundaries and which are guides.

That is a much shorter list than a feature roadmap, and it is the whole list.
