# Sources

All retrieved 2026-09-26. Ordered by document. Labels match the evidence convention in
[`README.md`](README.md): **MEASURED** (independent published measurement), **VENDOR-CLAIMED** (a lab
reporting on its own system), **SPECULATIVE** (reasoning or forecast).

---

## `01_state_of_evidence.md`

**MEASURED — METR, task-length time horizons.** The primary measurement this dossier rests on.
- https://metr.org/time-horizons/ — current horizon estimates
- https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/ — original ~7-month doubling
  trend (arXiv 2503.14499)
- https://arxiv.org/html/2503.14499v3 — "frontier AI time horizon has doubled approximately every seven
  months since 2019, though the trend may have accelerated since 2024"
- https://ai2027-tracker.com/predictions/metr-doubling/ — Time Horizon 1.1: 170→228 tasks; long (8h+) tasks
  14→31
- https://epoch.ai/benchmarks/metr-time-horizons — 50% and 80% horizon figures

**MEASURED — benchmark saturation.**
- https://www.swebench.com/ — SWE-bench, low single digits → saturated in ~2 years
- https://arxiv.org/abs/2409.11363 — CORE-Bench, ~20% (2024) → saturated 15 months later

**MEASURED — productivity estimate bias.** Load-bearing for every self-reported number in this dossier.
- https://arxiv.org/pdf/2507.09089 — developer estimates of AI productivity uplift can be overestimated

**VENDOR-CLAIMED — Anthropic Institute, "When AI builds itself."** The richest single source on RSI
evidence. Updated 2026-09-18. Marina Favaro and Jack Clark.
- https://www.anthropic.com/institute/recursive-self-improvement
- Supports: 4-month time-horizon acceleration; Opus 3 ~4min → Sonnet 3.7 ~1.5h → Opus 4.6 12h → Mythos
  Preview ≥16h; >80% merged code Claude-authored; 8× code/engineer/day; ~4× employee self-report (n=130);
  kernel optimisation ~3× → ~52× vs human ~4×; automated W2S researcher 97% of gap over 800h / ~$18k vs
  humans ~23% over ~1 week; next-step judgement 51% → 64% with the 127-moment bias check at ~20%; three
  futures; Amdahl's law already binding on code review; Project Glasswing 10,000+ vulns
- Also: https://alignment.anthropic.com/2026/automated-w2s-researcher/ (weak-to-strong supervision
  research, April 2026)
- Also: https://www.anthropic.com/glasswing

**VENDOR-CLAIMED — capability claims in the Fugu report's related work**, cited there and not independently
verified here: Gemini-3-Deep-Think IMO gold; GPT-5.5 disproving an 80-year-old Erdős conjecture in
combinatorial geometry; Claude-Mythos zero-days in OpenBSD/FreeBSD (Carlini et al. 2026).

**SPECULATIVE — RSI definitions and the takeoff debate.**
- https://arxiv.org/html/2607.07663v1 — "Recursive Self-Improvement in AI: From Bounded to Open-ended."
  The bounded/open-ended distinction this dossier uses.
- https://tecunningham.github.io/posts/2026-06-05-rsi-definitions.html — RSI definitions
- https://www.pelles.ai/blog/recursive-self-improvement-state-of-the-evidence — argues measured evidence shows
  **no** fast takeoff
- https://www.nytimes.com/2026/09/16/science/ai-recursive-self-improvement.html — NYT, 2026-09-16
- https://github.com/lobehub/awesome-rsi — curated RSI research map

**Other lab RSI programmes.**
- https://sakana.ai/rsi-lab/ — Sakana RSI Lab (June 2026)
- https://www.alphaxiv.org/abs/2607.15524 — Recursive Harness Self-Improvement (RHI), Sakana AI + UC Berkeley

---

## `02_learned_orchestration.md`

**MEASURED — Sakana Fugu Technical Report.** arXiv:2606.21228v2, 23 June 2026, CC BY 4.0. The most
directly transferable source in this dossier.
- https://arxiv.org/html/2606.21228v2 — full report
- https://sakana.ai/fugu/ — project page
- https://sakana.ai/ — lab; notes FuguLLM and the RSI Lab

Supports: learned-orchestrator architecture; decision-only parametrisation; singular-value fine-tuning;
soft performance-distribution SFT targets with temperature τ and KL loss; sep-CMA-ES on end-to-end
trajectories; Fugu-Ultra's Conductor lineage, 5-step workflows, two-stage reward (format 0 / correctness
1-or-0.5), GRPO without KL penalty, group-relative advantage; intra-workflow agent isolation and
orchestration collapse; persistent shared memory across workflows; the full benchmark table; emergent
topologies (debate/aggregation, build-and-debug, specialist injection); temporal routing between GPT-5.5
and Claude-Opus-4.8 at critical debugging points; the behavioural analogue of model merging.

**Foundational lineages referenced by Fugu, not read directly:**
- Trinity — Xu et al., 2025 (lightweight coordinator, assigns roles as well as selecting)
- Conductor — Nielsen et al., 2025 (RL-trained workflow designer)

---

## `03_agentic_architecture.md`

**OpenClaw 2026.9.6**, MIT, OpenClaw Foundation. Released 2026-09-23.
- https://github.com/openclaw/openclaw — 390,548 stars at retrieval
- https://docs.openclaw.ai/start/why-openclaw — **the seven testable properties**, the single-trust-envelope
  critique, the Hermes `SECURITY.md` quote, and the "What we do not claim" section
- https://docs.openclaw.ai/llms.txt — full documentation index
- https://docs.openclaw.ai/channels/bot-loop-protection — bot-to-bot loop protection defaults
- https://docs.openclaw.ai/channels/ambient-room-events — rooms listen but stay silent
- https://docs.openclaw.ai/cli/audit — activity records, execution identity, decision receipts
- https://docs.openclaw.ai/cli/policy — `policy.jsonc`, `policy check/compare/watch`, scoped overlays
- https://docs.openclaw.ai/concepts/memory-provenance — deletion limits
- https://docs.openclaw.ai/ci/pipeline — CI job graph, scope gates, fail-fast order
- https://github.com/openclaw/openclaw/releases — release evidence bundles and the named operator lane
  waiver for 2026.9.6

**Hermes Agent v0.21.5**, MIT, Nous Research. Released 2026-09-24. 249,100 stars.
- https://github.com/NousResearch/hermes-agent
- https://hermes-agent.nousresearch.com/docs/user-guide/features/goals — `/goal`, the auxiliary judge with
  strict JSON verdict, `wait` parking, subgoals, completion contracts
- https://hermes-agent.nousresearch.com/docs/user-guide/features/code-execution — `execute_code`, tool RPC
- https://hermes-agent.nousresearch.com/docs/reference/slash-commands — `/learn`, `/refine`, `/curator`,
  `/review`, staged skill/memory write approval
- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills — progressive disclosure, knowledge-base
  skills, conditional activation, quarantine, trust tiers
- https://hermes-agent.nousresearch.com/docs/llms.txt — full documentation index

**Third-party ecosystem signal** (context for supply-chain risk, not a capability source):
- https://github.com/VoltAgent/awesome-openclaw-skills — 5,400+ community skills, 52,806 stars
- https://github.com/SafeAI-Lab-X/ClawKeeper — third-party safety layer, "the Norton for OpenClaw". A
  vendor shipping an antivirus product for a platform is evidence the built-in posture is considered
  insufficient.
- https://github.com/Graphify-Labs/graphify — 121,557 stars; deterministic AST code knowledge graph, "no
  vector store". Relevant to alpha's `memory/codebase/indexer.py`; **not yet audited.**

---

## `04_alpha_gap_analysis.md`

All alpha claims are from files read or tests run in this session on 2026-09-26. No external source.

---

## Unconfirmed — recorded, not guessed

The original request named two items that could not be verified from any primary source. They are recorded
here rather than resolved by invention.

### "NVIDIA avo"

No NVIDIA project by that name was found. Searches run: `openclaw 2.0 AI agent`, `NVIDIA Alpamayo
NemoClaw agentic AI architecture self-improving 2026`, and GitHub repo search for `openclaw`,
`openclaw agent`.

Most likely referent: **Alpamayo**, NVIDIA's autonomous-vehicle reasoning model, which is real and appears
in NVIDIA's own agentic-platform framing alongside NemoClaw, Nemotron 3 and BioNeMo. If you meant something
else by "avo", say so and I will research that instead.

**NVIDIA NemoClaw**, confirmed and relevant: announced at GTC 2026, an open-source stack for the OpenClaw
community providing sandboxing (OpenShell kernel-level), fleet management and audit.
- https://www.nvidia.com/en-us/ai/nemoclaw/
- https://developer.nvidia.com/blog/building-a-memory-driven-agent-with-nvidia-nemoclaw/ — describes a
  human-readable `self model` knowledge layer of people, projects and priorities
- https://www.nextplatform.com/code/2026/03/18/the-open-agentic-ai-world-according-to-nvidia/5209529 —
  Alpamayo, BioNeMo, Nemotron 3 in one framing

Note: NVIDIA positions NemoClaw as enabling *"Nous Research Hermes… self-improving AI agents that share
collective wisdom."* The two projects are integrated, so treat NemoClaw's sandbox claims and Hermes'
capability claims as coming from the same commercial interest when assessing either.

### "ChatGPT Astra"

No OpenAI model by that name was found in any primary source. Verified OpenAI models in this period are
**GPT-5.5** and the **GPT-6 Sol / Terra / Luna** variants named in the Hermes v0.21.5 release notes.

Recorded as **UNCONFIRMED**. If "Astra" is a real model I failed to find, provide a source and I will
research it properly.

### Verified Anthropic model names (for the record)

From Anthropic's own model index and the RSI article's figures: **Mythos, Fable, Opus, Sonnet, Haiku**.
Release sequence visible in their session-success chart: Sonnet 4.5 → Opus 4.5 → Opus 4.6 → Mythos Preview
(internal) → Mythos Preview → Opus 4.7 → Opus 4.8 → Sonnet 5 → Opus 5 → Fable and Mythos 5.1.

"Fable 5.1" and "Opus 5.5" were both named in the request and both appear in vendor material, though
Anthropic's own chart labels the final entry "Fable and Mythos 5.1" and shows "Opus 5" separately. The
"5.5" suffix is not confirmed. Recorded as **VENDOR-CLAIMED, partially corroborated**.
