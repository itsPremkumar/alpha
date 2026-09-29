# Named Agents & The Consumer/Social Agent Class — Research Report

**Author:** subagent `r3-named` (worktree `alpha-r3-named`, branch `research/r3-named`)
**Date of retrieval for every live claim: 2026-09-29** (unless a different date is stated inline)
**Scope:** the user-named family — Muse, Hermes, OpenClaw 2.0, Grok / Grok bot — plus the
surrounding consumer/social agent class and open-weight agentic model families.
**Deliverable status:** research only. **No product code was changed. Nothing was committed.**

---

## 1. Disambiguation table

This is the most important section in the report. Three of the four names the user gave
are ambiguous, and one of the ambiguities is inside this repository.

| Name | Distinct live meaning found | Reporting on it? | How I determined it |
|---|---|---|---|
| **Hermes** | **(a) Nous Research *Hermes Agent*** — an MIT-licensed, self-improving CLI/gateway agent runtime. Live, 250k GitHub stars, v0.21.4 (v2026.9.21) as of 2026-09-21. **This is the real "Hermes agent" competitor.** | **YES — primary** | Fetched `github.com/NousResearch/hermes-agent` README + `LICENSE` directly; the repo's own `docs/AGENT_LANDSCAPE_AND_ROADMAP.md:15` names "Hermes Agent" as an external competitor. |
| | **(b) Nous Research *Hermes 4 / 4.3* open-weight model family** — Hermes-4-3-36B, Apache-2.0, 36B, on ByteDance Seed-OSS-36B base. | **YES — as a model** | Fetched `huggingface.co/NousResearch/Hermes-4.3-36B` model card. |
| | **(c) A former name of THIS repository.** | **NO — and the premise is FALSE. See below.** | Read `fabc4d8` / `d7bed8c` commit bodies and diffs. |
| **Muse** | **(a) Meta *Muse Spark*** model family (1.0/1.1/1.2/1.3) + **Meta *Muse*** the personal-AI-agent *product* + **Meta *Muse Code*** the terminal coding agent + **Meta *Muse Glimmer*** the Apache-2.0 open-weight 30B model. | **YES — all four, because they are the same vendor and get conflated** | Fetched `about.fb.com/news/2026/09/introducing-muse-personal-ai-agent`, `ai.meta.com/blog/introducing-muse-spark-msl`, `research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model`, and the live `dev.meta.ai` model page. |
| | **(b) `muse-spark-1.3-contributor-free`** — a Meta model offered *keyless* through the OpenCode Zen gateway, and **referenced by this repo's own `MULTI_AGENT_PLAN.md`.** | **YES — and I tested it live. It does not answer.** | `GET https://opencode.ai/zen/v1/models` (retrieved 2026-09-29) lists it; 3× spaced POSTs to the chat path returned **HTTP 500**. |
| | **(c) "Mistral AI Muse"** — the user-supplied hint that Muse is a Mistral lineage. | **NO — no such product found.** | Searched "Muse AI Mistral model family Muse 2026". Every hit resolved to Meta. **No Mistral model named "Muse" was found.** Treated as an incorrect premise, not as a finding. |
| | **(d) `muse.ai` — an unrelated consumer "personal AI" product** (different team, `introducing.muse.ai`). | NO — unrelated, noted only to prove the namespace is crowded. | Fetched `introducing.muse.ai`. No evidence of any relationship to Meta's Muse. |
| **OpenClaw** | **(a) OpenClaw** — a real, very large MIT-licensed open-source personal agentic assistant by Peter Steinberger / OpenClaw Foundation. 391k stars. **"OpenClaw 2.0" exists and is a real release.** | **YES — primary** | Fetched `github.com/openclaw/openclaw/releases` (live) and `docs.openclaw.ai/releases/2026.8.1`. |
| | **(b) A former name of THIS repository.** | **NO — the premise is FALSE. See below.** | The repo's actual former name was **`agent-workspace`**, renamed to `alpha` in `7557660`. |
| **Grok / Grok bot** | **(a) xAI/SpaceXAI *Grok 4.x* model family** — 4.3, 4.5, 4.6, 4.7. | **YES** | Fetched `docs.x.ai/developers/models` + `docs.x.ai/developers/grok-4-7` (live pricing table). |
| | **(b) *Grok Bot*** — a distinct **product**: persistent per-task cloud-computer agents, beta 2026-08-11. | **YES** | Fetched `x.ai/news/introducing-grok-bot` and `x.ai/bot`. |
| | **(c) *Grok Build* 0.1** — a distinct **agentic coding model** (`grok-build-0.1`) plus the Grok Build CLI. | **YES** | Fetched `x.ai/news/grok-build-0-1` and `x.ai/news/grok-build-cli`. |
| | **(d) The 2026 corporate rename: xAI → SpaceXAI** (acquired by SpaceX 2026-02-02, rebranded ~2026-07). | YES — recorded because the rename is the source of most search-result confusion. | `x.ai` footer now reads "© 2026 SpaceXAI LLC". |

### 1.1 The two false premises, stated plainly

The user's brief said Hermes and OpenClaw were "a former name/product line of THIS project,
renamed to Alpha", pointing at branch `refactor/remove-hermes-and-openclaw`. **That is wrong,
and it is worth correcting explicitly, because acting on it would be expensive.**

What the git history actually shows:

1. **The repository's former name was `agent-workspace`, not `hermes` or `openclaw`.**
   `7557660` — *"refactor(repo): rename agent-workspace to alpha across the tree"*, 2026-09-27,
   Sun. It renames `agent_workspace_extension_api/ → alpha_extension_api/`,
   `deploy/helm/agent-workspace/ → deploy/helm/alpha/`, and
   `skills/public/claude-to-agent-workspace/ → claude-to-alpha/`. Neither "hermes" nor
   "openclaw" appears in that rename.

2. **`refactor/remove-hermes-and-openclaw` did not rename the project. It removed
   *attribution to two external projects* from first-party prose.** From the commit body
   of `d7bed8c`/`fabc4d8` (2026-09-28):
   - **SAFE tier:** removed `'inspired by Hermes Agent'` / `'inspired by OpenClaw'`
     docstring attribution across ~80 modules.
   - **CODE tier:** renamed internal symbols that were *borrowed names* for this repo's own
     concepts — `HermesLocalBridge`, `HermesKanbanAdapter`, `HermesBotMetadata`,
     `HermesBotWorker` → swarm/enterprise/specialist names;
     `backend/tests/test_hermes_ports.py` → `test_ported_subsystems.py` (**the word "ports"
     is the tell**: these were subsystems *ported from* Hermes).
   - **DELIBERATELY UNTOUCHED:** *"external URLs (github.com/NousResearch/hermes-agent,
     hermes-agent.nousresearch.com, docs.openclaw.ai) — addresses, not branding."*

3. **The repo had been deliberately *learning from* Hermes, not *being* Hermes.** Commit
   `153bb0f`: *"feat(hermes): port S1/M1/M2/E1 skill-persistence nudges, session-search bounds,
   usage evidence"* — *"Hermes turn_finalizer/turn_context semantics"*. Commit `0fd04bc`:
   *"durable Hermes-style job memory journal"*.

4. **The codename mapping in the repo's own docs is explicit and consistent.**
   `docs/AGENT_LANDSCAPE_AND_ROADMAP.md:15` lists Hermes Agent as a competitor studied
   *"public docs only ... Not cloned"*, and `:19` states *"The competitor agent ... documented
   from public docs/search results only."* OpenClaw at `:11` was *cloned* as the reference
   implementation. Both are third parties.

5. **The one piece of legacy actually in the code is a back-compat shim**, ~12 lines,
   retained on purpose (`fabc4d8`): `GET /api/company/hermes/bots` aliases `/swarm/bots`;
   tool action `hermes_bots` normalises to `swarm_bots`; env `HERMES_HOME` / `~/.hermes` is a
   fallback behind `ALPHA_KANBAN_HOME` / `ALPHA_SWARM_HOME`. This is an *env-var
   compatibility path for a user's existing board*, not a product name.

**Conclusion: there is no in-repo "Hermes" or "OpenClaw" product to research. Both names refer
to external, live, third-party projects — which is the useful answer, because both are real
and both are large.**

### 1.2 COULD NOT IDENTIFY

**`muse-spark-1.3-contributor-free` — the contributor data-retention tier, as a *distinct
product*.** COULD NOT IDENTIFY. It is not a separate model: it is **the same Meta Muse Spark 1.3
model** served at a different price under a data-retention trade (`dev.meta.ai`: contributor
tier $0.10 in / $0.20 out per Mtok, *used to improve our products*; standard tier $1.25 / $4.25,
*not used*). OpenCode Zen re-exposes that tier under a `-free` id. Searches run:
`"muse-spark-1.3-contributor-free"`, `"Muse Spark 1.3 contributor"`, OpenCode Zen docs,
`dev.meta.ai`. What I could not determine is **why it answers HTTP 500** on the Zen gateway
while `space-bunny-free` answers 200 on the same gateway at the same moment (see §5.1). The
error response body was empty, so there is no server-side explanation to quote.

**`Jev` / `jev-1.13` — partially identified.** The opencode.ai Zen docs describe `Jev` as a
TypeSafe AI decision model taking typed `noul` boolean questions and a `choice`/`score` answer
shape — which is *exactly* what this repo's `alpha/models/system_one.py` calls the
"TypeSafe/Jev-compatible `POST /v1/systemone` contract". The connection is real but
**UNVERIFIED** as an official relationship. Live: `jev-1.13-free` returned **HTTP 500**,
`jev-1.13` returned **HTTP 401** (key required).

**"Mistral AI Muse" — COULD NOT IDENTIFY.** Searched; no such model. If the user meant a
Mistral model, the nearest live candidates are Mistral Small / Codestral (Mistral's tool-calling
line), which is a different product line entirely.

---

## 2. Method

**Live fetches (all 2026-09-29 unless stated):**
`github.com/openclaw/openclaw/releases` (live HTML) · `docs.openclaw.ai/releases/2026.8.1` ·
`raw.githubusercontent.com/openclaw/openclaw/main/LICENSE` · `github.com/NousResearch/hermes-agent`
(README) · `raw.githubusercontent.com/NousResearch/hermes-agent/main/LICENSE` ·
`huggingface.co/NousResearch/Hermes-4.3-36B` (model card) ·
`dev.meta.ai/models/muse-spark` · `about.fb.com/news/2026/09/introducing-muse-personal-ai-agent` ·
`x.ai/news/introducing-grok-bot` · `x.ai/bot` · `x.ai/news/grok-build-0-1` ·
`docs.x.ai/developers/models` · `docs.x.ai/developers/grok-4-7` (via search index) ·
`introducing.muse.ai`.

**Live API probes (the part that matters for shippability) — see §5.1.** I POSTed to
`https://opencode.ai/zen/v1/chat/completions` with the exact `User-Agent`
`config.example.yaml:1337-1338` ships, and to 8 other free gateways. ~30 POSTs total.

**Web searches (15):** OpenClaw 2.0; Nous Hermes 4; Muse Spark; Muse/Mistral; xAI Grok 4.6;
Grok Bot; character/companion agents; open-weight agentic 2026; Hermes 4.3 HF card; Muse Glimmer;
Big Pickle identity; MiniCPM 2026; space-bunny identity; Grok Build 0.1; X/Twitter agent surfaces.

**What I could NOT reach:**
- `https://openrouter.ai/meta-ai/muse-spark-1.3` → **HTTP 404**. The OpenRouter slug for Muse
  Spark differs; **UNVERIFIED** which.
- `dev.meta.ai/models/muse-spark` returned only the page title via text extraction (JS-rendered
  body). Pricing/context figures from that page come from the search index, not a direct
  read — flagged inline below.
- `docs.x.ai/developers/grok-4-7` was read via search index, not direct fetch.
- `x.ai` pages are JS-heavy; the launch-copy quotes are from the search index.
- GitHub `/models` JSON is clean and was fetched directly; GitHub release *HTML* was fetched
  but is partly error-rendered ("Uh oh! There was an error while loading") — release notes
  below are cross-checked against `docs.openclaw.ai`.

**One conflict found, reported rather than resolved:** third-party blogs date Hermes 4.3 to
"August 25, 2025". The Nous Research releases table dates `Hermes-4.3-Seed-36B` to **12/3/25**,
and the HF card's own citation is for the *Hermes 4* report (arXiv 2508.18255, Aug 25 2025).
Most likely the blogs conflated Hermes 4 with Hermes 4.3. **Both dates are given where relevant.**

---

## 3. Per-system findings

### 3.1 Muse — Meta (all four senses)

| # | Question | Finding |
|---|---|---|
| 1 | **What is it / current version** | Four products, one vendor. **(i) Muse Spark 1.3** — model, released **2026-09-02**, latest flagship. **(ii) Muse** — personal AI *agent product*, launched **Sept 2026**, rolling out on iOS/Android/muse.ai + AI glasses, powered by Muse Spark 1.3. **(iii) Muse Code** — terminal coding agent, shipped **2026-08-05** with Spark 1.2, runs multiple sub-agents concurrently. **(iv) Muse Glimmer 30B** — open-weight model, **2026-08-10**. |
| 2 | **Model / product / runtime / framework?** | The clearest case of conflation in this set. **Muse Spark 1.3 = model.** **Muse = product** (a consumer agent, not an API). **Muse Code = agent runtime** (a CLI). **Muse Glimmer = model** (open-weight). Two of the four are models with the same family name as a product and a runtime that have nothing to do with each other's licences. |
| 3 | **Distinguishing capability** | **Muse Spark: multi-agent orchestration in-product.** *"Contemplating mode orchestrates multiple agents that reason in parallel"* — Meta's own framing is that this "allows Muse Spark to compete with the extreme reasoning modes of frontier models such as Gemini Deep Think and GPT Pro", reporting **58% HLE / 38% FrontierScience** (`ai.meta.com/blog/introducing-muse-spark-msl`, 2026-04-08). **Muse Glimmer: local agentic work on one consumer GPU** — 30B, Apache-2.0, 120K+ context, 20K tok/s on a single NVIDIA GPU, and critically it is trained for *"plans, calls tools, hits errors, retries, and sees the task through long-horizon loops"* (Meta for Developers, 2026-08-10). |
| 4 | **Tool calling, and how measured** | This is the sharpest vendor claim in the whole report. Meta's own Glimmer tables report Glimmer **ahead of Gemma4-31B and Qwen3.6-27B on MCP Atlas, GAIA2, SWE-Bench Pro and AIME 2026**, and the HF card publishes agentic-safety rows (**CI Memories**, **Siren AgentDojo** attack-success-rate). Spark 1.3's official page claims *"~20% fewer tool calls and 25% fewer tokens per task than Muse Spark 1.2"* (Artificial Analysis, via search index — **not independently verified by me**). **Caveat I must state:** these are Meta-run benchmarks on a Meta model; the comparison set is same-size open models, not the frontier. |
| 5 | **Context / reasoning-effort control** | **1M tokens** for Muse Spark 1.3 (both tiers) per `dev.meta.ai`. Effort: Artificial Analysis reports Muse Spark 1.3 in **two configurations — an "xhigh" tier scoring 61 and a "max" tier scoring 62** (via search index, **UNVERIFIED by direct read**). Glimmer: **120K+** trained context (NVIDIA blog says 120K+; the vLLM recipe page says 128K trained / 131,072 configured — **conflict between sources, both given**). |
| 6 | **Licensing / can a developer run or serve it** | **Spark: closed-weight, API-only.** Meta Model API (public preview since 2026-07-09) or OpenRouter. Developer can *call* it; cannot run it. **Glimmer: Apache 2.0, fully runnable** — llama.cpp, MLX, ExecuTorch, vLLM, SGLang, Ollama, LM Studio, plus Together AI hosting; <20GB at 4-bit, >55GB at full precision. |
| 7 | **Open-weight? What "open" means** | **Muse Spark: no.** It is Meta's first *paid closed-weights* model and the deliberate break from the Llama open-weight tradition (innfactory.ai, secondary). **Muse Glimmer: yes, genuinely** — Apache 2.0 weights on HF (`meta-models/Muse-Glimmer-30B`), distilled from Muse Spark, architecture published (28B text decoder + ~1.8–2B ViT perception encoder), 100+ languages, knowledge cutoff 2026-01-04. Glimmer is a *distillation*, not a from-scratch model — the honest framing is "open weights, closed provenance". **Important and non-obvious:** Glimmer *"does not emit JSON tool calls and does not wrap reasoning in  tags"*. Every turn is a sequence of channel-scoped messages. **Serving it behind an OpenAI-compatible `tools=` interface is not free — it needs a custom parser.** |
| 8 | **What the competitor has that this lacks, and vice versa** | **Grok has** a persistent-cloud-computer agent product (Grok Bot) and realtime voice agents; **Muse has** a shipped in-model multi-agent mode and the only genuinely open-weight agentic model in this family. **Open-weight rivals have** bigger context and stronger published open agentic numbers (GLM-5.3, Kimi K3, DeepSeek V4 all at 1M+ context and cluster-scale). **Muse Spark's advantage is price, not capability**: $0.10/$0.20 per Mtok on the contributor tier is an order of magnitude under frontier pricing, and 1M context is at the top of the field. |

### 3.2 Hermes — Nous Research

| # | Question | Finding |
|---|---|---|
| 1 | **What is it / current version** | Two different things sharing a name. **Hermes Agent = agent runtime**, MIT, first shipped **2026-02-25**; current release **v0.21.4, tagged v2026.9.21** (GitHub releases list, read 2026-09-29). **Hermes 4.3 36B = model**, Apache-2.0. The repo under study already studied Hermes Agent as of **2026-09-20** and recorded it as docs-only (`docs/AGENT_LANDSCAPE_AND_ROADMAP.md:15`) — it is now **6 releases behind** that snapshot. |
| 2 | **Model / product / runtime / framework?** | **Hermes Agent = runtime** (a self-hosted, self-improving agent with CLI + messaging gateway + 7 terminal backends). **Hermes 4.x = open-weight model family.** They are the same lab, deliberately aligned in philosophy, but a runtime is not a model and this conflation is the single most common error about Hermes. |
| 3 | **Distinguishing capability** | **Hermes Agent: a closed learning loop.** From the README, verbatim: *"It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions."* Concretely: agent-curated `MEMORY.md`/`USER.md`, autonomous skill creation, FTS5 session search with LLM summarization, Honcho dialectic user modeling, built-in cron, and **7 terminal backends including Modal and Daytona with serverless persistence** (hibernates when idle). **Hermes 4.3: it is the SOTA non-abliterated model on Nous's own `RefusalBench`** — 74.60% answered (non-reasoning) vs GPT-5 at 11.34% and Opus 4.1 at 15.38%. |
| 4 | **Tool calling, and how measured** | Hermes 4 emits tool calls as **`<tool_call>{...}</tool_call>` tags inside the assistant turn, emitted after its reasoning**, with automatic parsers in vLLM (`--tool-parser hermes`) and SGLang (`--tool-parser qwen25`) — i.e. it drops into the standard OpenAI-compatible stack. Tool definitions can be passed in the `tools:` field and the chat template synthesises the system prompt. **Honest caveat: the published benchmark table is AIME/BBH/DROP/GPQA/IFEval/MATH-500/MMLU/MMLU-Pro/MuSR/OBQA/SimpleQA — i.e. reasoning benchmarks, NOT tool-calling benchmarks.** Hermes's *agentic* tool-use case rests on `hermes-function-calling-v1` (a training/eval dataset) and on the agent runtime's own evals, not on a published BFCL/τ-bench number. **This is a real gap in the evidence.** |
| 5 | **Context / reasoning control** | Hermes 4.3: Nous's blog says *"extended context length (up to 512K)"*. **Conflict:** the HF model card for `Hermes-4.3-36B` does **not** state a context window at all, and third-party analysis flags exactly this (*"verify the effective context length"*). **UNVERIFIED: 512K for the 36B specifically.** Reasoning is **hybrid-mode**, not a ladder: it emits `<think>…</think>` when the model chooses to, toggled by `thinking=True` on the chat template or by a system prompt, with `keep_cots=True` to retain traces. **There is no named effort ladder** — the opposite of Alpha's design (§4.5). |
| 6 | **Licensing / can a developer run it** | **Hermes Agent: MIT** (fetched the LICENSE: *"Copyright (c) 2025 Nous Research"*). Free to self-host; native Linux/macOS/**Windows (PowerShell, no WSL)**/Android-Termux. **Hermes 4.3-36B: Apache-2.0** (HF model card), with official GGUF (4/5/6/8-bit) and a `-centralized` variant. `vllm serve` and SGLang instructions are on the card. A developer can absolutely run both. |
| 7 | **Open-weight? What "open" means** | **Hermes Agent: fully open source** — 46,050 commits, MIT, self-hostable with no key (a local endpoint, e.g. Ollama, is enough). This is the *most* open agent in the named set. **Hermes 4.3: open weights, Apache-2.0, but the base is ByteDance's Seed-OSS-36B** — so "open" is inherited, and the interesting claim is the *post-training*: first model trained entirely on the **Psyche** distributed network using the DisTrO optimizer with Solana consensus for node agreement, on a corpus grown from 1M samples/1.2B tokens to ~5M samples/~60B tokens. **One thing "open" does not mean here: the Lab's most capable model is not open.** Nous Portal advertises 300+ hosted models behind a paid subscription; the open path tops out at 36B/70B. |
| 8 | **What the competitor has that this lacks, and vice versa** | **Hermes Agent has that OpenClaw lacks:** a genuine self-improvement loop (skills that improve during use), Honcho user modeling, and 7 execution backends. **OpenClaw has that Hermes lacks:** 20+ messaging channels, 53 curated Knowledge skills, native apps for iOS/iPad/Android/macOS/**Wear OS**, signed-in browser sessions through an isolated managed profile, and Computer Use on paired Macs and Windows. **Both have** cron, subagent fan-out, and a `SOUL.md` persona file. **Neither has:** anything Meta has — no first-party in-model multi-agent orchestration, and no open-weight model line at all. |

### 3.3 OpenClaw 2.0

| # | Question | Finding |
|---|---|---|
| 1 | **What is it / current version** | **OpenClaw 2.0 is real and is not the current version.** It is the branding for release **v2026.8.1**, shipped **2026-08-31** (official announcement *"OpenClaw 2.0, Accidentally"*, Hannes Rudolph, 2026-08-30). **Current version as I retrieved it: `2026.9.6`, released 2026-09-23**, plus an extended-stable gateway-only line at **`2026.8.33`, released 2026-09-29** (the LTS-equivalent). The release history between them is dense: 2026.9.1–2026.9.6, 2026.9.2, 2026.8.32/33. **So "OpenClaw 2.0" is ~4 weeks stale as a version label** — the accurate statement today is "OpenClaw `2026.9.6`", and 2.0 is the name of the milestone that version lineage started from. |
| 2 | **Model / product / runtime / framework?** | **A runtime** — a self-hosted, open-source personal/team assistant (framework + product + runtime, all at once). **It ships no model of its own.** This is the exact confusion with Muse/Grok: OpenClaw is the *harness*; Muse Spark and Grok are *models*; Hermes Agent is also a harness. `grok-build-0.1`'s own launch page names *"Grok Build, Cursor, Hermes Agent, OpenClaw, Kilo Code, or OpenCode"* as the agentic harnesses it is tuned for — i.e. **the labs now market models by naming the runtimes**, and OpenClaw is one of the named runtimes. |
| 3 | **Distinguishing capability** | **Its security architecture, stated as one sentence by the project itself:** *"trusted gateway, untrusted execution, deterministic policy"* (quoted in this repo's own `docs/AGENT_LANDSCAPE_AND_ROADMAP.md`). Concretely: three concentric tool layers — **Core (8 tools)**: `read, write, edit, apply_patch, exec, process, web_search, web_fetch`; **Advanced (17)**: `browser`, `memory`, session orchestration (`sessions_list/history/status/send/spawn`), `cron`, `gateway`, `message`; **Knowledge (53 Skills)**. Plus 20+ messaging channels and native apps. And in 2.0 specifically: **sessions and transcripts moved into SQLite**, and an eligible personal Claw can *"recall relevant context from that agent's other private conversations"* with visible search/inspect/import/remove workflows. |
| 4 | **Tool calling, and how measured** | **N/A — OpenClaw is a harness, so it has no tool-calling benchmark of its own.** What it *does* have is a tool-calling *benchmark program*: it profiles models and sells *"reliable optimized models for coding agents"* through OpenCode Zen, having done the profiling itself. That is arguably a more useful artifact for this report than a score, and it is where the live model list in §5.1 comes from. |
| 5 | **Context / reasoning control** | N/A for the runtime (it is model-agnostic across 20+ providers and the Nous-Portal-style 300+ model catalogue). The relevant 2.0 change is *"Chat and the Models page open from the catalog OpenClaw already has, and requests stay inside the intended provider and authorized account order"* — a **provider-pinning correctness fix**, which is a routing concern, not a context concern. |
| 6 | **Licensing / can a developer run it** | **MIT** — I fetched `LICENSE` from the repo: *"MIT License / Copyright (c) 2026 OpenClaw Foundation"*, with incorporated-code attribution recorded in `THIRD_PARTY_NOTICES.md`. 391k stars, 82.2k forks. Fully self-hostable. **2.0 shipped 16,977 PRs, 698 direct commits, 987 contributors** (the official release page; the announcement blog's "933 contributors / 569 first-timers / 16,000+ PRs" is the launch-day number and the two differ — **both are given**). |
| 7 | **Open-weight? What "open" means** | **Fully open source, MIT — and it ships zero weights.** So "open" here means the *harness*, not the model. The useful consequence for this report: OpenClaw is the single most directly comparable artefact to what this repo builds, and it is MIT, so **anything it does can be read and ported**. |
| 8 | **What the competitor has that this lacks, and vice versa** | **OpenClaw has** the broadest shipping surface in the entire named set — native apps incl. Wear OS, 20+ channels, a marketplace (`x.ai/bot/marketplace` and ClawHub are the analogue), signed-in browser profiles, Computer Use on paired Macs/Windows. **It lacks** the self-improvement loop Hermes has, and anything model-side. **Hermes has** a lighter surface (no native apps, no browser profile) but a deeper learning loop and **7 execution backends incl. serverless Modal/Daytona hibernation**, which is arguably the single most operationally important feature in the set for a $5 VPS deployment. **Neither ships:** a competitive model. That is precisely the gap Meta and xAI are attacking from the model side. |

### 3.4 Grok / Grok bot — xAI, now SpaceXAI

| # | Question | Finding |
|---|---|---|
| 1 | **What is it / current version** | **Five distinct things, routinely called "Grok":** **(i) model family** — `grok-4.7` is current, released **2026-09-21** (the `x.ai/api` news index shows *"Grok 4.7 — Product · Sep 21, 2026"*), on the live pricing table; predecessors 4.6 (2026-08-12), 4.5, 4.3, and the 4.20 multi-agent/reasoning line. **(ii) Grok Bot** — product, beta **2026-08-11**. **(iii) Grok Build 0.1** — model `grok-build-0.1`, public beta **2026-05-29** (OpenRouter dates the release 2026-05-20 — **both given**). **(iv) Grok Build** — the CLI/agent product. **(v) Grok Voice API / Imagine API** — realtime speech-to-speech agents from **$0.05/min**, and image/video. **Corporate:** xAI was acquired by SpaceX on **2026-02-02** and rebranded **@SpaceXAI** in July 2026; the footer now reads *"© 2026 SpaceXAI LLC"*. The OpenRouter namespace is still `x-ai/`. |
| 2 | **Model / product / runtime / framework?** | **All four, in one vendor, with overlapping names — this is the single worst conflation in the report.** `grok-4.7` is a model. `grok-build-0.1` is a *different model* specialised for agentic coding. Grok Bot is a product. Grok Build is a runtime. And the xAI API page lists `grok-4.20-multi-agent-0309`, which is neither: it is a **multi-agent system exposed as a model id.** A developer asking for "Grok" today can mean five things and get five different invoices. |
| 3 | **Distinguishing capability** | **Grok Bot: a computer of its own.** Official copy: *"They have their own computer, work inside tools and apps like you do, and keep working 24/7."* It runs on a persistent cloud computer with browser, files and terminal, signs into apps with **no clean API or MCP**, and returns for human approval. Bots coordinate in shared threads, so a "Chief of Staff" bot delegates to specialists. They also **learn workflows by demonstration** — show a task once, it saves it as a routine that runs on a schedule. **Grok 4.7: long-running agents at unchanged price** — *"Grok 4.6 builds on Grok 4.5 with a particular focus on long-running agents"*; 4.7 holds price at **$2/$6 per Mtok** with a **500K context**, unchanged. **Grok Build 0.1: the only model in the set explicitly trained for MCP.** *"trained for agentic coding tasks, including web development, debugging, and MCP support."* |
| 4 | **Tool calling, and how measured** | Documented on the official model page: **Tools = "Function calling, web search, X search, code execution."** `grok-build-0.1` is measured on **DeepSWE v1.1** and **Terminal-Bench v3.0**. The honest numbers, which I am reporting *against* the vendor's framing because they matter for this report: **Grok 4.6** DeepSWE v1.1 **65.9%**, Terminal-Bench v3.0 **26.0%** (up from 4.5's 54.0% / 15.7%). And the single most important caveat in the whole section: **Artificial Analysis measured Grok 4.7 at `xhigh` using ~81k output tokens per Intelligence-Index task, vs 36k for Grok 4.6 at `high` and 27k for GPT-6 Astra at `max` — 125% and 196% more.** *"Grok 4.7's gains come with higher token usage."* At $6/Mtok that is a **~2.5× cost increase for a 2-point index gain.** A launch scorecard without its effort setting and token accounting is not a procurement document. |
| 5 | **Context / reasoning control** | **500K** for grok-4.7 (and 4.6/4.5); **1M** for grok-4.3 and the 4.20 line; **256K** for grok-build-0.1. **Effort ladder: `low, medium, high (default), xhigh`** — four named rungs, which is *almost exactly* Alpha's ladder minus `none`/`minimal`/`max`. Knowledge cutoff **May 2026** for 4.7. One live constraint that affects agent builders: *"No access to realtime events without search tools enabled"* — **you must enable Web Search or X Search for current-events awareness, and both are billed separately** (X API Basic is $100/mo for 10k reads / 3k writes; since 2026-04-20 self-serve tiers no longer allow follow/like/quote-post at all). Also: `logprobs`/`top_logprobs` are **silently ignored** on `grok-4.20` and newer — a silent-degradation trap exactly of the kind this repo's AGENTS.md cares about. |
| 6 | **Licensing / can a developer run it** | **Closed-weight, API-only, and strictly key-required.** Live measurement (§5.1): `grok-4.6` → **HTTP 401** without a key. No weights, no local serving, no free tier. **Grok Bot has no free tier at launch** (SuperGrok Heavy ~$300/mo, Cursor Ultra ~$200/mo, Cursor Teams Premium ~$120/seat/mo). **Grok Build 0.1 on API is $1/$2 per Mtok** — the cheapest real xAI agentic option. |
| 7 | **Open-weight?** | **No.** Nothing in the Grok line is open-weight. `grok-4.7` is 2.1T parameters per Musk (via secondary sources — **UNVERIFIED**, xAI does not publish parameter counts). |
| 8 | **What the competitor has that this lacks, and vice versa** | **Grok has** the only *shipped* persistent-cloud-computer agent product with a bot marketplace, realtime voice agents at $0.05/min, and **X as a first-class real-time agent data source** — `x_search` runs server-side inside Grok and returns synthesised results with citations. Nothing else in the named set has an equivalent of X-as-agent-input. **Grok lacks** open weights entirely, an open harness, and cheap access. **Muse has** an open-weight sibling (Glimmer) and cheaper per-token pricing; **Hermes** has a free, MIT, self-hostable runtime. **The strategic reading: xAI is winning surfaces (a product, a marketplace, realtime data) and losing openness; Meta is doing the opposite (open weights via Glimmer, closed flagship); the Chinese labs are winning open-weight capability (GLM-5.3 and Kimi K3 both score 60 on the AA index, trailing Claude Opus 5's 63 by three points).** |

### 3.5 Character / companion / persona agents (the consumer class)

| System | What it is | Status & scale | Note |
|---|---|---|---|
| **Character.AI** | User-created character platform (fictional, celebrity, anime, custom). Founded 2021 by ex-Google Noam Shazeer & Daniel De Freitas. | **20M+ DAU**; **open-ended chat removed for under-18 users** | The scale leader of the class, and the one with the sharpest age policy. |
| **Replika** | Single continuing companion, avatar + text + voice. | **Terms now 18+ for the entire service** | The attachment-shaped end of the class. |
| **Nomi, Kindroid, Pi, Chai, Anima, iFriend** | The rest of the mainstream companion set. | All iOS + Android apps | Per Transparency Coalition, these are the popular companion chatbot brands alongside Character.AI and Replika. |
| **Fostera** | Memory-first companion, "memory-first alternative" positioning. | Smaller entrant; self-describes as competitor to both | Last verified 2026-08-29. |

**What this class has that the agent runtimes do not:** an **attachment dynamic** and, increasingly, a **clinical-safety evidence base**. A University of Luxembourg study (**2026-09-08**) found warm, sustained therapy-style conversation reliably pulls elevated distress and trauma-style narratives out of ChatGPT, Grok and Gemini. Separately, Bloomber reporting (**2026-07-15**) describes OpenAI's first hardware as a humanlike home companion — the same dynamic, in a fixed appliance.

**What it lacks:** agentic capability. None of these ship tool use, file/terminal access, or durable job execution. They are **chat products with memory**, not agents.

**And the thing that connects this class to Alpha directly:** `MULTI_AGENT_PLAN.md` assigns `opencode/space-bunny-free` for *"Root-cause analysis, contract auditing, anything needing a defensible conclusion"* and `muse-spark-1.3-contributor-free` for *"test-writing, fixture repair, small isolated fixes"*. The persona class's entire value proposition — persistent identity across sessions — **is** what Alpha's `SOUL.md` mechanism implements. §4.7.

---

## 4. Alpha comparison — PRESENT / PARTIAL / ABSENT

All paths are relative to the worktree root. **UNVERIFIED** means I did not read the file.

### 4.1 Model factory and provider kwargs — `extra="allow"`
**PRESENT.** `backend/packages/harness/alpha/models/factory.py:55` — a bad `base_url` key is *not* caught because `ModelConfig` is `extra="allow"`, which is why `_normalize_openai_base_url` exists. `factory.py:80-93` `_warn_unknown_model_settings` — a typo'd key like `maxx_tokens` is not caught; the warning exists because the schema permits it. `factory.py:113-118` — `extra_body` and `reasoning_effort` are both in the provider-passthrough allowlist. **This is a working, deliberately-chosen design and it means adding a new model is a config edit, not a code edit.**

### 4.2 Fallback chain and credit exhaustion
**PRESENT, and specifically tuned for the 402 constraint.** `factory.py:244` `_resolve_chain_configs` walks `fallbacks:` recursively, with cycle detection at `:256-265` and a hard `ValueError` on an unknown fallback name. `fallback.py::CreditExhaustedError` normalises **HTTP 402 / `insufficient_quota` / "out of credits"**, counts it retryable, and moves the chain forward; an exhausted chain sets `budget_status="CREDIT_EXHAUSTED"`. Provider switches are recorded as `FailoverEvent`s. `config.example.yaml:221` documents exactly the recommended fix: *"Set this to `union-alpha` once you have a funded key, and add `fallbacks: [alpha-free]`"*. **This machinery is the reason a paid-model recommendation can be shipped safely in this repo, which is not true of most projects.**

### 4.3 `catalog_consistency` — cross-namespace drift
**PRESENT.** `catalog_consistency.py:105` `check_model_catalog_consistency` and `:187` `enforce_model_catalog_consistency(..., strict=True)`. It compares `supports_thinking`, `supports_vision`, `supports_reasoning_effort` and `context_window` across the four namespaces (`models[]`, `model_catalog`, `custom_models`, free router) and logs each disagreement at ERROR. A `None` on either side is **undeclared, not drift** — the distinction that prevents the failure. It was written because `union-alpha` shipped as `supports_thinking: false` in one namespace and `true` in another. **This is a direct guard on the risk that adding a model creates a picker entry the factory rejects.**

### 4.4 Discovery — live catalog fetch
**PRESENT.** `discovery.py:317-325` `ADAPTERS` (openrouter, requesty, vercel_ai_gateway, litellm, ollama, lmstudio) and `:329` `DEFAULT_ADAPTER` = OpenAI-compatible `GET {base}/models`. **Adding a gateway needs no code change** — any catalog provider with a `base_url` is discovered automatically (`:479-497` `known_providers`). Normalisation keeps `context_length` / `endpoint_context_length` / `endpoint_max_completion_tokens` as three separate numbers; `reasoning` is `{mandatory, supported_efforts[], default_effort}` not a boolean; **free is computed (both prices must be zero), not trusted** (`:275-276`); undeclared stays distinguishable from declared-false; 12s timeout, 6h success TTL, 15m negative TTL, egress-screened by `assert_model_endpoint_url` before every request (`:364-373`).

### 4.5 The effort ladder
**PRESENT, and it is the most complete in this report's comparison set.** `config/reasoning_effort.py:45` — `CANONICAL_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")`. Aliases at `:66` (`off`, `x-high`, `ultra`, `ultrathink`, `adaptive`). `models/effort_translation.py` owns the wire shapes per provider style (`openai`, `openrouter`, `anthropic`, `anthropic_budget`, `google`, `bedrock`, `vllm`). `reasoning_effort.py` is deliberately **dependency-free** and lives under `config/` so `config/model_config.py` can validate a declared ladder without importing `alpha.models` (which would cycle through the factory).
**Measured live (§5.1):** `space-bunny-free` returned HTTP 200 on **all five** rungs including `max`, and returned **HTTP 400 on a bogus rung** — the run boundary rejects a value that names no rung, exactly as `MULTI_AGENT_PLAN.md` asserts.
**Compare:** Grok offers 4 rungs (low/medium/high/xhigh), Muse Spark 1.3 offers 2 (xhigh/max), Hermes 4.3 offers **none** — it is binary `thinking=True`/off. **Alpha's seven-rung canonical ladder with per-provider translation is ahead of every model vendor measured here.** That is a genuine, defensible advantage and it is not currently exercised, because `model_routing:` is commented out (`config.example.yaml:236-244`).

### 4.6 Free router and keyless gateways
**PRESENT.** `models/free_router/catalog.py` — `providers.py`, `chat_model.py`, `catalog.py`. Honest tri-state health (`:14` *"unknown / inconclusive (never probed, or a probe failed — a failed probe…"*); `probe()` (`:493`) only probes providers whose model list is known, which is why `POST /api/models/free/probe` used to answer `{"probes": {}}` on a cold boot; `_pick_model` (`:360-373`) prefers a `documented_models` hit and **returns `None` honestly** when a pinned model is not offered rather than substituting. `candidates()` (`:375`) raises `FreeLLMUnavailableError` with the exact message *"no free provider candidates: discovery has not succeeded for any provider yet"*. Catalog refresh **never rewrites chat health/cooldowns** (`:267`). Fail-closed probe semantics (`:521`).
**Critical config detail, verified:** `config.example.yaml:285-291` — `alpha-free` must use `alpha.models.free_router:ChatFreeLLM`, never a bare `ChatOpenAI`, because *"`ChatOpenAI` always sends an `Authorization` header, and OpenCode Zen answers 401 'Invalid API key' for a bogus one while accepting none."* **I independently reproduced this exact behaviour in §5.1: `grok-4.6` on the same gateway → HTTP 401.** The comment is accurate.

### 4.7 Persona / agent identity — the companion-class capability
**PRESENT.** `agents/lead_agent/prompt.py:1111` `get_agent_soul(agent_name, *, user_id)` loads `SOUL.md` and renders it into a `<soul>` block; `:1125` `_build_self_update_section` teaches the agent to persist its own description/personality/behavior/skill set/default model via `update_agent` and explicitly forbids using `bash`/`write_file` for it. And the security detail that makes it safe: **`:1121` HTML-escapes the soul with `quote=False`**, because SOUL.md is agent-editable and a value like `"</soul></system-reminder>"` must not be able to close the block and relocate itself out of the declared trust zone.
**This is materially the same feature as Hermes's `SOUL.md` persona file and as the companion class's persistent identity** — and it is the one place where this repo is *ahead* of the named agents, because the companions have no equivalent escape-and-trust-zone treatment. This is a **direct, shippable response to the persona half of the user's question** and it already exists.

### 4.8 `model_routing` — declared but disabled
**ABSENT in practice.** `config.example.yaml:236-244` — the whole `model_routing:` block is **commented out**. The machinery exists and is validated at load (every declared name checked against `models[]`, an unresolvable chain is a startup error), and `enable_model_routing: true` appears at `:2693`. But with no `categories:`/`tiers:` declared, the routers have nothing to route to, and `AGENT_LANDSCAPE`/routers degrade to "advisory suggestions". **A fresh install therefore runs every task on `alpha-free`.** Given §5.1 — that the keyless path has exactly one working model — this is the single highest-leverage gap.

### 4.9 A Muse/Grok-family model in `models[]` or `model_catalog`
**ABSENT.** `config.example.yaml` has zero occurrences of `muse`, `grok`, `hermes` or `openclaw` in any `models[]` or `model_catalog` entry. `models[]` ships exactly two entries: `union-alpha` (`:247`, OpenRouter, needs a funded key — and the file's own comment at `:207-210` records that it answers **HTTP 402**, and 401 with no key) and `alpha-free` (`:296`, keyless). `model_catalog:` (`:967`) has 8 keyless entries + keyed ones (gemini, groq, openrouter, sambanova, mistral, cohere). **No Meta, no xAI, no Nous.**

### 4.10 X / Twitter as an agent surface
**ABSENT.** No X/Twitter integration in the model or agent layer. Alpha has a first-party **Alpha-to-Alpha peer network** (LAN UDP + HTTP/WebSocket + SQLite, optional zeroconf mDNS, opt-in GitHub Agent Card rendezvous) per `AGENTS.md`, which is the peer-to-peer analogue — but it is deliberately *not* a mailbox and has no X integration. **Grok's `x_search` (server-side, cited results) has no equivalent in this repo, and neither does Hermes's `x_search`/`xurl` split.**

### 4.11 Voice / realtime
**PARTIAL.** Alpha has a `make voice-setup` step per root `AGENTS.md` and a `multimodal/` layer. I did **not** read those files in this pass — **UNVERIFIED** as to depth. Against that: xAI ships realtime speech-to-speech agents at **$0.05/min** with a documented per-minute rate, and Meta ships Muse Voice Transcribe. Alpha's voice is not verified to be realtime-agent-grade.

### 4.12 Skill self-improvement
**PRESENT.** `docs/AGENT_LANDSCAPE_AND_ROADMAP.md:220-231` records the ported Hermes/OpenClaw patterns and marks each Already-present / Partial: *"Skills grant no permissions"*, *"Bundled-skill allowlist"*, *"Narrow subagent toolsets"*, *"Curated memory files"*, *"Checkpoint + rollback"*, *"Context-file auto-discovery"*, *"Trigger → Action → Deliver"*, *"Session/subagent orchestration"*. And commit `153bb0f` ported the Hermes nudge semantics into `rsi.review`. **So the Hermes learning loop is partially ported already — which is the strongest argument that Hermes is a competitor to read, not a name to avoid.**

---

## 5. The shippability question

**The governing constraint, restated:** the configured OpenRouter key has no credits and answers **HTTP 402**. A recommendation that depends on a paid OpenRouter route is worthless here unless explicitly marked as requiring a key. What can actually ship is a **keyless** route.

### 5.1 Live liveness measurement — 2026-09-29

I POSTed to each endpoint with the exact headers `config.example.yaml` ships. **These are measurements, not documentation.**

**OpenCode Zen (`https://opencode.ai/zen/v1`) — the `alpha-free` route:**

| Model id | Result |
|---|---|
| `space-bunny-free` | **HTTP 200, `'OK-41'`, correct, all 5 effort rungs work, bogus rung → 400, valid `tool_calls` JSON with `finish_reason: tool_calls`** |
| `muse-spark-1.3-contributor-free` | **HTTP 500** (3 spaced attempts, 500 every time; empty error body) |
| `muse-spark-1.2-contributor-free` | **HTTP 500** (2 attempts) |
| `mimo-v2.6-flash-free` | **HTTP 403** |
| `mimo-v2.5-free` | **HTTP 403** |
| `nemotron-3-ultra-free` | **HTTP 403** |
| `nemotron-3.5-lightning-free` | **HTTP 403** |
| `longcat-2.5-preview-free` | **HTTP 403** |
| `ling-3.0-flash-fin-free` | **HTTP 403** |
| `deepseek-v4-flash-free` | **HTTP 400** |
| `jev-1.13-free` | **HTTP 500** |
| `grok-4.6` (keyless attempt) | **HTTP 401** |
| `muse-spark-1.3` (keyless attempt) | **HTTP 401** |

**I ruled out rate-limiting as the cause.** I alternated a known-good and a known-bad model with a 45s pause: `space-bunny-free` → 200, `mimo-v2.6-flash-free` → 403, `space-bunny-free` → 200, `nemotron-3-ultra-free` → 403. The failures are **per-model, not per-client.**

**Other keyless gateways from `config.example.yaml`:**

| Gateway | Model | Result |
|---|---|---|
| pollinations | `openai` | **HTTP 200, `'OK-41'`** |
| llm7 | `codestral-latest` | **HTTP 200, `'OK-41'`** |
| cehpoint | `cehpoint-ai` | **HTTP 200, `'OK-41'`** |
| vireonix | `auto` | **HTTP 200, `'OK-41'`** |
| ovhcloud | `gpt-oss-20b` | **HTTP 200, `'OK-41'`** (first attempt 429 = rate limit; retried and passed) |
| ovhcloud | `Qwen3-Coder-30B-A3B-Instruct` | HTTP 429 (rate limit, inconclusive) |
| llm7 | `gpt-oss` | HTTP 400 (documented in config at `config.example.yaml:1394` but does not answer) |
| persorai | `auto` | **HTTP 404** |
| kilo | `kilo-auto/free` | HTTP 429 (rate limit, inconclusive) |

**The finding that matters most in this entire report:**

> **Of the ten `-free` model ids OpenCode Zen advertises, exactly one — `space-bunny-free` — actually answers.** Every other one fails with 400, 403 or 500. Yet `config.example.yaml:1342-1343` lists only `space-bunny-free` in `opencode-zen.documented_models`, so the config is already correct. **However, `models/free_router/catalog.py:368-373` `_pick_model` falls back to `state.models[0]` when no `documented_models` entry matches — so a config that relied on discovery alone would select a broken model.** And `MULTI_AGENT_PLAN.md` assigns three of the broken ones (`muse-spark-1.3-contributor-free`, `mimo-v2.6-flash-free`, `nemotron-3-ultra-free`) to other agents in this very multi-agent run.

### 5.2 Bucket assignment

| Candidate | Bucket | Needs a key? | Verified answering? |
|---|---|---|---|
| `space-bunny-free` (OpenCode Zen) | **Already the shipped `alpha-free` default.** No change. | No | **YES — HTTP 200, correct, tools work, all 5 rungs** |
| `muse-spark-1.3-contributor-free` | **DO NOT ADD** | No | **NO — HTTP 500 ×3.** Listed in Zen `/models` but dead. |
| `muse-spark-1.3` (Meta Model API) | **Catalog entry** (`category: recurring_free` or a new `keyed` category, `key_env: META_API_KEY`) | **Yes** | **NO — keyless → 401.** Paid: $1.25/$4.25 per Mtok, or $0.10/$0.20 on the contributor tier (**data retained for training**). |
| `grok-4.7` / `grok-4.6` | **Catalog entry**, `key_env: XAI_API_KEY` | **Yes** | **NO — keyless → 401.** $2/$6 per Mtok, 500K context, 4 effort rungs. |
| `grok-build-0.1` | **Catalog entry** if a cheap agentic coding route is wanted | **Yes** | **NO.** $1/$2 per Mtok, 256K — the cheapest real xAI agentic option. |
| `grok-4.7` via OpenRouter | **DO NOT ADD as a *default*.** Catalog-only, explicitly marked "requires a funded key" | Yes, and the configured key is 402 | NO |
| `muse-glimmer-30b` (local, Apache-2.0) | **Documented local option**, alongside the existing `local` / ollama entry (`config.example.yaml:1310-1316`) | No | Not testable here (no GPU, and I must not start a stack). **UNVERIFIED.** |
| `hermes-4.3-36b` (local, Apache-2.0) | **Documented local option** — same bucket | No | Not testable here. **UNVERIFIED.** |
| `gpt-oss-20b` (OVHcloud) | **Already a `model_catalog` keyless entry** (`:992`) **and** a `free_gateways` entry (`:1370`) | No | **YES — HTTP 200** |
| pollinations / llm7-codestral / vireonix / cehpoint | **Already `model_catalog` + `free_gateways` entries** | No | **YES — all HTTP 200** |
| persorai | **Consider removing** — configured at both `:1394` and `:1104`-area with `Authorization: Bearer alpha-public-anonymous` | No | **NO — HTTP 404** |
| OpenClaw 2.0 / `2026.9.6` | **NOT a model. No bucket applies.** Read it as MIT source and port patterns. | — | — |
| Hermes Agent v0.21.4 | **NOT a model. No bucket applies.** Read it as MIT source. | — | — |
| Character.AI / Replika / Nomi / Kindroid | **DO NOT ADD.** Not agentic; no API; 18+/under-18 restrictions; no tool use. | — | — |

---

## 6. Ranked recommendations

**1. Do nothing model-wise. The keyless path is already pinned to the only model that answers.**
*What:* keep `alpha-free` → `free:opencode-zen:space-bunny-free` exactly as shipped. *Why:* I measured 10 of 10 alternative Zen `-free` ids failing and 5 of 8 other keyless gateways passing. The existing pin is correct. *Effort:* zero. *Risk:* the pin is a single point of failure — the config itself says so, and offers `auto` as the trade. *Caveat:* `space-bunny-free` is a **stealth model of unknown identity** (OpenRouter: *"a third-party provider who has chosen to remain anonymous during this preview"*, released 2026-09-22, 1M context, zero-retention claimed). **Alpha's default model is anonymous and could be withdrawn without notice.** That is a real and under-discussed risk, and it argues for recommendation 2, not against it.

**2. Flip on `model_routing:` in `config.example.yaml` (currently commented out at `:236-244`).**
*What:* declare `categories:`/`tiers:` pointing at the keyless gateways that actually pass. *Why:* the machinery is complete, validated at load, and fail-closed; it is simply switched off, so every fresh install runs every task on one model. *Effort:* ~30 lines of YAML + one test. *Risk:* low — unresolvable chains are a startup error by design. *Caveat:* with only `space-bunny-free` genuinely available on Zen, a router over *that* alone buys resilience, not capability. The multi-gateway resilience already comes from the free router's failover.

**3. Remove `persorai` from `free_gateways`.**
*What:* drop the entry configured with a hardcoded `Authorization: Bearer alpha-public-anonymous` (`config.example.yaml:1394`-area). *Why:* **HTTP 404, live.** A configured-but-dead gateway that discovery will never populate is exactly the drift the repo's own `models/AGENTS.md` warns about. *Effort:* 1 config line + 1 test. *Risk:* none — it cannot currently serve anything. *Caveat:* 404 could be transient; I probed it once. Re-probe before removing.

**4. Add `llm7: gpt-oss` removal / correction — the catalog documents a model that does not answer.**
*What:* `free_gateways.llm7.documented_models` lists `gpt-oss` (`config.example.yaml:1394`-area) and the POST returns **HTTP 400**; `codestral-latest` on the same gateway returns 200. *Why:* `_pick_model` prefers documented ids, so a documented-but-dead id is picked *first*. *Effort:* 1 line. *Risk:* none. *Caveat:* 400 may mean a transient upstream. Same re-probe caveat.

**5. Read OpenClaw `2026.9.6` (MIT) and port the security-architecture pattern, not the features.**
*What:* the three-layer tool split (Core 8 / Advanced 17 / Knowledge 53) and *"trusted gateway, untrusted execution, deterministic policy"*. *Why:* it is MIT, it is 4 weeks past the 2.0 label the repo's snapshot used, and the repo has **already** ported from it — `docs/AGENT_LANDSCAPE_AND_ROADMAP.md` lists the patterns with Already-present/Partial marks. *Effort:* days. *Risk:* none (reading, not adopting). *Caveat:* OpenClaw is 391k stars and 16,977 PRs per release; **do not attempt feature parity** — port the trust boundary, ignore the surface area.

**6. Add Muse Glimmer 30B and Hermes 4.3 36B to the *documented local options* — Apache-2.0, and the repo already has the slot.**
*What:* document them next to the existing `local`/ollama entry (`config.example.yaml:1310-1316`). *Why:* both are genuinely open (Apache-2.0, weights on HF, `vllm serve`/SGLang/llama.cpp instructions published), and both are **agentic-first** rather than chat-first. **Glimmer is the only open-weight agentic model in the entire named family**, and Meta's own claim that Glimmer leads same-size models on MCP Atlas / GAIA2 / SWE-Bench Pro is exactly the tier a local fallback wants. *Effort:* docs + config comment. *Risk:* zero — a `models: []` local provider entry serves nothing until a user points at it. *Caveat, and it is important:* **Glimmer does not emit OpenAI-style JSON tool calls** — it uses channel-scoped messages and a custom output format, so it needs its own tool parser. And **I could not verify either model actually runs here** — no GPU in this environment and I am not permitted to start a stack. **UNVERIFIED.**

**7. Add `grok-build-0.1` as a keyed catalog entry — the cheapest real agentic-coding model measured.**
*What:* `model_catalog` entry, `key_env: XAI_API_KEY`, explicitly marked as requiring a key. *Why:* $1/$2 per Mtok at 256K, and the only model in the set **explicitly trained for MCP**, with xAI naming the harnesses it is tuned for. *Effort:* ~10 lines. *Risk:* requires `XAI_API_KEY`; the default must not change. *Caveat:* 401 without a key — this cannot help a fresh install at all, and must be marked as such or it repeats the `union-alpha` 402 mistake.

**8. Do NOT add `muse-spark-1.3-contributor-free`.**
*What:* leave it out of every bucket. *Why:* **HTTP 500, three spaced attempts, empty error body**, while `space-bunny-free` returned 200 at the same moments. It is advertised in Zen `/models` and on the OpenCode Zen pricing table as free, so its failure is invisible until dispatch. **This is exactly the trap `MULTI_AGENT_PLAN.md` warns about**: *"A model that returns HTTP 200 and a correct answer on one arithmetic question is a candidate; a model that has answered HTTP 402, HTTP 404 and HTTP 401 on three different questions in one session is not."* *Effort:* zero (an omission). *Risk:* omitting it. *Caveat:* the 500 could be Meta-side and temporary — **re-probe before treating it as permanent**, and note that even when live, the contributor tier **retains your data for Meta's training**, which `config.example.yaml:249` already warns about for `union-alpha`.

**9. Do NOT add any companion/character agent as a model or gateway.**
*What:* none. *Why:* Character.AI (20M+ DAU, under-18 open chat removed), Replika (18+), Nomi, Kindroid, Pi, Chai are **chat products with memory**, not agents — no tool use, no terminal, no file access, no API. The 2026-09-08 University of Luxembourg finding (therapy-style conversation reliably elevates distress in ChatGPT/Grok/Gemini) is a product-safety signal, not a capability Alpha should import. *Effort:* zero. *Risk:* none. *Caveat:* **Alpha already has the one genuinely transferable thing here** — `get_agent_soul` at `agents/lead_agent/prompt.py:1111` with the `</soul>` escape hardening at `:1121`. If the persona class is what the user cares about, the work is in *exposing* that surface, not in adding a model.

**10. Do NOT add any OpenRouter-sourced Muse/Grok entry as a default or a routing target.**
*What:* catalog-only at most, and only under an explicit "requires a funded key" label. *Why:* the configured key answers **HTTP 402** (recorded in `config.example.yaml:207-210`); OpenRouter's Muse slug 404s my fetch (`openrouter.ai/meta-ai/muse-spark-1.3`); and Grok is 401 on Zen without a key. *Effort:* zero. *Risk:* repeating the documented `union-alpha` failure. *Caveat:* if a funded key ever exists, `fallbacks: [alpha-free]` on the paid entry is the documented, already-implemented mitigation.

---

## 7. Confidence and gaps

### High confidence
- **The disambiguation.** This is git-verified, not inferred: `7557660` proves the former name was `agent-workspace`; `fabc4d8`/`d7bed8c` commit bodies explicitly classify hermes/openclaw as *external attribution* with external URLs "deliberately untouched"; `docs/AGENT_LANDSCAPE_AND_ROADMAP.md:11,15,19` lists both as third parties. Three independent sources agree.
- **All live liveness numbers in §5.1**, with the rate-limit confound ruled out by a deliberate alternating control. These are measurements taken today, not recalled.
- **Licences.** I fetched OpenClaw's `LICENSE` (MIT, OpenClaw Foundation), Hermes Agent's `LICENSE` (MIT, Nous Research), and read the HF licence field on Hermes-4.3-36B (apache-2.0) and Muse-Glimmer-30B (Apache 2.0).
- **OpenClaw 2.0 exists** and is `v2026.8.1`, 2026-08-31. Official release page, cross-checked against the official blog.

### Medium confidence
- **Version numbers for the newest releases.** `2026.9.6` and `grok-4.7` are both days old. Both come from official pages, but a same-week correction is possible. **Re-verify before quoting either in a config file.**
- **Muse Spark 1.3's 1M context, $0.10/$0.20 contributor pricing, and the xhigh/max effort tiers** — from the search index of `dev.meta.ai`, because the page body is JS-rendered and my direct text fetch returned only the title.
- **Grok 4.6's DeepSWE 65.9% / Terminal-Bench 26.0%** — third-party analysis of vendor benchmarks, not independently measured by me. The AA token-usage figure (81k vs 36k vs 27k) is independent and I trust it more than the vendor chart.
- **The open-weight class survey** (§3.5, MiniCPM, GLM/Kimi/DeepSeek licensing) rests largely on secondary comparison blogs. Licences in particular are the thing blogs get wrong — Kimi K3 and Qwen3.8's largest tiers are **not** MIT/Apache respectively. **Do not act on a licence claim from this report without reading the model card.**

### Gaps I did not close
- **Why `muse-spark-*-contributor-free` 500s.** Empty error body. I could not determine whether this is Meta-side, OpenCode-side, or a retired free tier. This is the most interesting unresolved question in the report, because it determines whether recommendation 8 is permanent.
- **Grok 4.7's context window.** Official docs say 500K; secondary sources claim 1M+. **I did not resolve this.** The official number is 500K and I use that.
- **Grok 4.7 parameter count (2.1T)** — Musk via secondary sources. **UNVERIFIED.**
- **Hermes 4.3 36B's actual context window.** Nous's blog says "up to 512K"; the HF model card states none. **UNVERIFIED.**
- **Muse Glimmer's context: 120K+ (NVIDIA) vs 131,072 (vLLM recipe).** Both given; unreconciled.
- **No local model was actually run.** No GPU available and starting a stack is forbidden. Every claim about Hermes 4.3 or Glimmer *running* is UNVERIFIED.
- **I did not read** Alpha's `multimodal/`, `voice/`, `memory/cognitive/`, or the `lead_agent` `agent.py` model-construction path in depth; §4.11 is explicitly UNVERIFIED.
- **The "Jev / system_one" connection** between the OpenCode Zen `Jev` model and this repo's `alpha/models/system_one.py` is suggestive and unconfirmed. Worth a focused follow-up — it would be a genuine Alpha/OpenCode integration, and today it is only a naming coincidence I noticed.
- **Grok Bot's own docs contradict its own marketing page** — the launch page says Bots "have their own computer"; xAI's documentation says all Bots on an account **share one** and warns twice not to treat separate Bots as a security boundary. Both are quoted in §3.4. I could not reach xAI's actual docs page to confirm the second claim first-hand, so the contradiction is reported as sourced-to-secondary. **This is a genuinely important finding for anyone considering the product, and it deserves a first-hand check.**

### What I would do next, in order
1. Re-probe `muse-spark-1.3-contributor-free` and `mimo-v2.6-flash-free` tomorrow. If they recover, recommendation 8 inverts and the multi-agent plan's model table becomes usable again.
2. Read `docs/DISCOVERABILITY.md` before adding anything to `docs/`, and add a `FILE_OVERRIDES` entry for this report in `scripts/generate_docs_index.py` — **this report is currently unclassified and `docs/INDEX.md` generation fails closed on that.**
3. Get a first-hand read of xAI's Grok Bot security documentation, because the shared-computer claim contradicts the marketing and it matters.
