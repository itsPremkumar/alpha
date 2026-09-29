# Research: autonomous / computer-use / browser agents

**Slice owner:** `research/r2-autonomous` worktree.
**All URLs retrieved 2026-09-29** unless a different retrieval date is stated inline.
Nothing in this document is inferred from training data: every factual claim carries
the URL it came from. Claims I could not verify are marked **UNVERIFIED**.

---

## 1. Dead, renamed, acquired or discontinued products

This section leads because four of the twenty-odd systems in the assigned category
are no longer the thing they were named for. A competitive analysis that describes
them as they were in 2025 is worthless, and worse than no analysis, because it
recommends building against a product that is gone.

### 1.1 Project Mariner — **DEAD** (shut down 2026-05-04)

Google retired Project Mariner with no announcement. The landing page now reads:
*"Thank you for using Project Mariner. It was shut down on May 4th, 2026 and its
technology voyaged to other Google products."*

- <https://labs.google.com/mariner/landing> (retrieved 2026-09-29) — the shutdown
  notice itself, on Google's own domain. This is the primary source; the news
  articles are downstream of it.
- <https://www.theverge.com/tech/925559/google-project-mariner-shut-down> (retrieved 2026-09-29)
- <https://www.pcmag.com/news/google-closes-project-mariner-web-browsing-ai-shut-down-earlier-this-week> (retrieved 2026-09-29)

Consequences the project should note:
- Mariner's capability did not die; it was folded into **Gemini Agent**, **AI Mode**
  search, and Chrome's **auto browse** (`auto-browse`/`auto browse`), which Google
  shipped to Chrome and then to **Android** (rollout from late June 2026, Android 12+,
  US only at announcement, built on Gemini 3.1 —
  <https://blog.google/products-and-platforms/products/chrome/bringing-chrome-ai-to-android>,
  retrieved 2026-09-29).
- Google shut it down *two weeks before* I/O 2026 with no warning. Both PCMag and
  The Verge note this is unlike Google's normal deprecation practice. Wired reported
  the Mariner team was moved onto "an upcoming OpenClaw-like agent"
  (**Wired's Maxwell Zeff**, reported second-hand via PCMag — I did not reach the
  Wired article itself, so that specific claim is **UNVERIFIED at the primary
  source**).
- **There is no Mariner API product to build against.** The DeepMind page
  (<https://deepmind.google/models/project-mariner>, retrieved 2026-09-29) still
  carries a "Coming to the Gemini API" line; that line is now stale.

### 1.2 OpenAI Operator — **DEAD as a product** (folded into ChatGPT agent, July–Aug 2025)

- OpenAI's own post is dated *"July 17, 2025 update: Operator is now fully
  integrated into ChatGPT as ChatGPT agent… the standalone Operator site
  (operator.chatgpt.com) will sunset in the coming weeks."*
  <https://openai.com/index/introducing-operator> (retrieved 2026-09-29)
- OpenAI Help Center, current: *"Operator functionality is now integrated into ChatGPT
  agent mode. The Operator website is no longer accessible."*
  <https://help.openai.com/en/articles/11752874-chatgpt-agent> (retrieved 2026-09-29)
- The underlying CUA model survives as an API tool: `computer-use` in the OpenAI
  API / Agents SDK. That is the developer surface; "Operator" as a product is not.

Two further secondary claims I **could not verify at a primary source**:
- A third-party page (Coursiv, 2026-09-10) claims Operator's browser workflows now
  live in **"ChatGPT Work's Cloud browser"**, a cloud-hosted browser that runs on a
  separate machine. <https://coursiv.io/blog/chatgpt-operator> (retrieved 2026-09-29).
  Not confirmed on openai.com. **UNVERIFIED.**
- A tracker (Presenc, retrieved 2026-09-29) claims Operator's standalone surface was
  shut 2025-08-31 and cites Anthropic computer use as the reason OpenAI lost the
  developer-API category. Opinionated aggregator; **UNVERIFIED.**

Verified pricing facts from the Help Center page: agent mode is paid-plans-only
(Pro, Plus, Business, Enterprise, Edu), capped at **40 messages/month** (Plus),
**400/month** (Pro), **40/month** (Business/Enterprise), and **30 credits/message**
for Business/Enterprise on flexible pricing. Enterprise/Edu workspace owners get a
toggle **defaulted to OFF**.

### 1.3 Claude for Chrome → **Claude in Chrome**, GA on 2026-08-26; side panel folded into "Claude Cowork"

Anthropic's own blog index lists, in sequence:
- *The Claude in Chrome side panel is now Claude Cowork* — 2026-08-12
- *Claude in Chrome is generally available* — 2026-08-26
- *Claude gets its own browser in Cowork* — 2026-08-26

<https://claude.com/blog> (retrieved 2026-09-29). Product page:
<https://claude.com/claude-in-chrome> (retrieved 2026-09-29), which states GA
"on all paid plans", installs from the Chrome Web Store, and notes Enterprise admins
can restrict it to approved domains.

This is **not** a discontinuation — it is a graduation — but the names a reader would
search for ("Claude for Chrome") are not the current names, and the autonomy level
changed materially: Anthropic's launch post says Claude *"can now also take actions
autonomously in the browser, instead of needing approval for every one. A safety
classifier validates each action before it's performed."*

### 1.4 OpenAI Swarm — **DEAD as a supported product** (replaced by the Agents SDK)

`openai/swarm` still exists and is still MIT-licensed, but its own README opens with
an `[!IMPORTANT]` banner: *"Swarm is now replaced by the OpenAI Agents SDK, which is a
production-ready evolution of Swarm… We recommend migrating to the Agents SDK for all
production use cases."*

- <https://github.com/openai/swarm> (retrieved 2026-09-29) — MIT, 21,944 stars,
  **29 commits total**, created 2024-02-22. Twenty-nine commits over the whole life of
  the repo is the tell: it is a frozen sample, not a product.
- <https://openai.github.io/openai-agents-js/> (retrieved 2026-09-29) — the Agents SDK
  describes itself as *"a production-ready upgrade of our previous experimentation for
  agents, Swarm"* and ships primitives Swarm never had: Guardrails (parallel run-input
  validation, fail-fast), MCP server tool calling, and Sessions.
- PyPI `openai-swarm` 0.1.1, released **2024-10-17**, unchanged.
  <https://pypi.org/project/openai-swarm/> (retrieved 2026-09-29).

Do not build on Swarm. Its successor (Agents SDK, Apache-2.0, Python + TypeScript) is
the thing to compare against.

### 1.5 Microsoft AutoGen — **MAINTENANCE MODE** (superseded by Microsoft Agent Framework)

`microsoft/autogen`'s README now carries a `[!CAUTION] ⚠️ Maintenance Mode` banner:
*"AutoGen is now in maintenance mode. It will not receive new features or enhancements
and is community managed going forward… New users should start with Microsoft Agent
Framework. Existing users are encouraged to migrate."*

- <https://github.com/microsoft/autogen> (retrieved 2026-09-29) — license is a split:
  **CC-BY-4.0 for docs, MIT for code** (`LICENSE` / `LICENSE-CODE`). ~59–61k stars,
  3,782 commits, still receiving community PRs.
- Successor: **Microsoft Agent Framework (MAF) 1.0**, production-ready, .NET + Python.
  <https://github.com/microsoft/agent-framework> (retrieved 2026-09-29).
- **Timeline claim, secondary source only:** LangChain's comparison page
  (<https://www.langchain.com/resources/langchain-vs-autogen>, retrieved 2026-09-29,
  dated 2026-06-23) states AutoGen entered maintenance mode **October 2025** and MAF
  reached **1.0 GA on 2026-04-03**, superseding both AutoGen and Semantic Kernel.
  This is a competitor's blog, so treat the *dates* as **UNVERIFIED**; the *fact of the
  maintenance banner* is verified from the repo itself above.
- **AG2** — the community fork of the pre-0.2 Autogen lineage, now maintained as AG2
  under `ag2` on PyPI — is still alive and is **not** deprecated:
  <https://docs.ag2.ai/latest/docs/home/quickstart> (retrieved 2026-09-29).
  Anyone saying "AutoGen is dead" is conflating the Microsoft project with the fork.
  AutoGen also still ships **AutoGen Bench** (`agbench`) and **Magentic-One**, the latter
  being an actual computer/web-using agent team — relevant to §4.

### 1.6 Naming correction carried over from the sibling coding-agent slice

The sibling agent found four dead products in the coding-agent slice (Amazon Q
Developer CLI → Kiro CLI closed source; Gemini CLI → Antigravity CLI closed-source
Go binary; Windsurf → Devin Desktop under Cognition; Continue acquired by Cursor,
wound down, data deleted after 2026-07-15). I have **not** independently verified
those four; I record them here only as context. My own independently verified finding
set is §1.1–§1.5 above, and every one of those is read off a vendor-controlled
domain or repository.

---

## 2. Method

**What I fetched:** official vendor domains (`openai.com`, `labs.google.com`,
`deepmind.google`, `claude.com`, `anthropic.com`), official help centers, official
blog indexes, official GitHub repos and arXiv, plus PCMag / The Verge / GIGAZINE as
secondary corroboration only — never as the sole source for a status claim.

**Retrieval date for every URL in this document: 2026-09-29.**

**What I could not reach / caveats:**
- Several vendor marketing pages render client-side; where that happened I used the
  blog index listing (which is server-rendered) instead and said so.
- I did **not** execute anything: no browser session, no vendor benchmark harness,
  no login-walled product. Every "success rate" in this document is a number the
  vendor or paper published, not a number I measured.
- Where a page needed a subscription or was blocked, that is stated inline as
  **UNVERIFIED** rather than filled from memory.
- **Status of the Alpha source analysis (section 6):** see §9. I completed the
  competitor research and the Alpha source read; I did not run the Alpha test suite
  or the live stack (explicitly out of scope for this slice).

---

## 3. Landscape table

"Action space" is what the agent can actually *do*, not what marketing says. "Grounding"
is how a proposed action is bound to an element. **Run by** separates a number produced
by the benchmark's own maintainers from one produced by the vendor.

| System | Status 2026-09-29 | Action space | Perception / grounding | Headline reported success rate + benchmark + who ran it | Permission model | Cost per task | License / usable? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **ChatGPT agent** (ex-Operator) | Live, paid plans only | Own virtual browser; full mouse/keyboard on a remote machine | Screenshot pixels | "up to 90% on OSWorld" is **not** a verified OpenAI claim — see §5. BenchLM lists GPT-5.5 at **78.7%** on OSWorld-Verified (aggregator, unverified) | Takeover; blocked-site list; Enterprise toggle default OFF | Capped by message quota: 40/mo Plus, 400/mo Pro, 30 credits/message Business flexible | Proprietary SaaS. CUA available via API/Agents SDK |
| **Anthropic computer use** (`computer_toolset_20260801`) | **GA** | 17 member tools: screenshot, zoom, 5 click variants, drag, mouse_move, down/up, cursor_position, scroll, type, key, hold_key, wait — **all pixel coordinates**, plus batch actions | Pure pixels + `zoom` for small text; no DOM/a11y | Vendor does not publish a headline OSWorld number on this page. Prompts are *advice*, not a contract | **None in the API.** Vendor's stated posture: isolate in a VM/container, don't give it credentials, allowlist domains, **have a human confirm consequential actions**. A prompt-injection classifier scans tool output and can be opted out of via support | Per token + per screenshot (image input dominates) | API, pay-per-token. Reference impl is Apache-2.0 on `anthropics/anthropic-quickstarts` |
| **Anthropic browser use tool** | GA, separate toolset | Page-level structured actions — reads/acts on **the page itself**, explicitly "doesn't need a full desktop environment" | Structured page/DOM, not pixels | — | Same posture + a per-toolset **halt text** for a failed batch | Cheaper than computer use: no screenshot round-trip | Same |
| **Claude in Chrome** (ex-`Claude for Chrome`) | **GA since 2026-08-26**, all paid plans | Reads the tab you are on, clicks/types/fills forms using your existing logins | Real page + vision | Injection-resistance: own eval, 123 cases / 29 scenarios. 23.6% → **11.2%** success with mitigations; on a 4-type browser-attack "challenge" set, 35.7% → **0%** | **A safety classifier auto-approves each action before it runs** (same mechanism as Claude Code auto mode). Enterprise can limit to approved domains. Built-in browser in Cowork carries "the same safeguards" | Subscription | Chrome Web Store extension |
| **Project Mariner** | **DEAD 2026-05-04** | n/a — retired | n/a | n/a | n/a | was $249.99/mo AI Ultra | Nothing to license |
| **Gemini in Chrome / auto browse** (Mariner's heir) | Live; Android rollout from late June 2026, US, Android 12+/4GB, on Gemini 3.1 | In-browser agent, "auto browse" | Browser agent, unspecified publicly | None published that I could verify | **Asks for confirmation before sensitive tasks** ("making purchases or posting on social media") — stated on blog.google | Bundled with Chrome | Proprietary |
| **Manus** | Live; **Meta acquisition disputed** (§1) | Cloud browser + VM + code execution; "Wide Research" = parallel sub-agents | Cloud browser, visual | Claims a "Benchmarks" page; I did **not** verify its contents — **UNVERIFIED** | Cloud isolation is the isolation | Credit-based. Pro $20/mo / 4,000 credits; Pro+ $40 / 8,000; Extended $200 / 40,000; Free 300 **daily**. Third-party guide (nocode.mba, 2026-09-25) gives task bands: simple 10–50, web research 100–300, deep research 500–900, code 200–500, web app 500–1,000+ | Proprietary SaaS; API listed |
| **browser-use** (OSS library) | Alive. PyPI **0.13.8, 2026-08-16**; 13.x shipped Jun–Aug 2026 | Playwright-driven DOM/CDP actions | **DOM/CDP, not pixels** | Self-claims **#1 on "Odysseys" at 87.4%** on 200 long-horizon web tasks, and 100-task "full benchmark is open source: browser-use/benchmark". **Self-reported; Odysseys' own methodology I could not verify** — **UNVERIFIED** | No in-agent confirmation gate found. Auth is your problem; ships an explicit auth-examples doc | Your own LLM cost | **MIT.** You can genuinely use it |
| **Browser Use Cloud** | Live | Managed browsers: stealth, proxies, **CAPTCHA solving**, 3 free concurrent | — | — | This is anti-detection infra, which is the opposite of a permission model | Free tier / paid | Proprietary |
| **browsercode** | New 2026-04-21, MIT, 648★ | **`browser_execute(code)`** — agent writes JavaScript that drives Chrome over raw CDP. No per-action schema | Code-level CDP | Claims "outperforms every browser agent we have tested" — **no named benchmark; unverifiable marketing** | None | Local | **MIT. A fork of `anomalyco/opencode`** with a vendored TS port of `browser-use/browser-harness` |
| **Skyvern** | Alive. Docs **v1.0.22** | Playwright + natural language; cloud browsers with CDP, proxies, browser sessions (5–240 min, default 60) | Vision ("reads the page visually") **plus** DOM, plus a **Planner–Agent–Validator** split | None verified | Task/workflow auth; vault for credentials (not examined) | Cloud pricing not retrieved | OSS core + commercial cloud |
| **Stagehand** (Browserbase) | Alive. npm **3.6.0**, MIT, ~1.0M weekly downloads, 22k GitHub★ | **Hybrid by design**: `act` / `extract` / `observe` / `agent`. Deterministic primitives *plus* an autonomous mode — explicitly "most teams combine both: agent for exploration, individual primitives for critical paths" | DOM-first with AI fallback | None published | Enterprise Browserbase controls; local mode has none | Per Browserbase session | **MIT.** Also Python/Go/Ruby/Java/Rust since 2026-01-13 |
| **UFO³ / UFO²** (Microsoft) | Alive. UFO² in **LTS**; UFO³ Galaxy multi-device | **Hybrid GUI + native Windows APIs** via MCP-shaped commands | **UI Automation tree** (not pixels) + app introspection | arXiv 2504.14603, self-run: on **OSWorld-W**, UFO² + o1 = **32.7%**, UFO² + GPT-4o = 28.6%, Operator computer-use = 14.3%. WAA: 30.5% / 27.9% / 20.8% | 7-state FSM; PiP nested desktop via RDP loopback so the agent never takes the user's mouse. `CONFIRM` status exists | Model cost + your Windows box | **MIT.** Genuinely usable on Windows |
| **OmniAct** | A *benchmark*, not a product — Writer's 9,802 desktop/web episodes, cited on the OSWorld page as a comparison | — | — | — | — | — | Dataset |
| **OpenAI Swarm** | **DEAD** — 29 commits, replaced by Agents SDK | — | — | — | Agents SDK adds **Guardrails**: parallel run-input validation, fail-fast | — | MIT but frozen; use Agents SDK (Apache-2.0) |
| **Microsoft AutoGen** | **MAINTENANCE MODE**, community-managed. Successor **MAF 1.0** GA | — | — | Ships **agbench** and **Magentic-One** (web browse + code exec + files) | — | — | CC-BY-4.0 docs / **MIT code** |
| **AG2** (fork) | Alive, not deprecated | — | — | — | — | — | Apache-2.0 |
| **UI-TARS-2 / UI-TARS-1.5-7B / Aguvis** (ByteDance) | Models ship on HuggingFace; referenced by the OSWorld page as a baseline and by Huzzle as a leaderboard entry | Screenshot → coordinate | Pixels | Huzzle's third-party page (2026-07-30) quotes **UI-TARS-2 at 53.1%** and **uitars-1.5-7b at 29.6%** on OSWorld **Verified** — third-party reading of a public leaderboard, **plausible but not confirmed by me against os-world.github.io** | — | — | Open weights |

### 3.1 Benchmarks that are still alive

| Benchmark | Status | Note |
| --- | --- | --- |
| **OSWorld** (original) | Superseded | 369 tasks / 361 after excluding 8 Google-Drive tasks. Human 72.36%, best model at paper time **12.24%** |
| **OSWorld-Verified** | Live | July 2025 repaired release. **Results load via JavaScript — I could not read the table.** Confirmation is a *process*: you must schedule a meeting and run your agent on the maintainers' side, and disclose your implementation. That process is the strongest honesty signal in this whole category |
| **OSWorld 2.0** | **New, released 2026-06-26** | <https://osworld-v2.xlang.ai/> — long-horizon, ~318 tool calls per workflow on 108 workflows per a third-party leaderboard. **UNVERIFIED — I did not fetch osworld-v2.xlang.ai** |
| **WebArena / VisualWebArena / WorkArena / WebShop / MiniWoB++** | Live research benchmarks | 812 / 910 / 23k / 125 / 12k instances per the OSWorld comparison table. Structural, reproducible, but small and saturated |
| **WebVoyager** | Live; see §5 | The only mainstream *open-web, no-sandbox* browser benchmark |
| **AgentBench** | Live, 1,091 instances, 8 environments | Umbrella; partly closed-source components |
| **AndroidWorld / Android-in-the-Wild (AitW)** | Live; Google Research | 30k episodes. **I did not verify current status — UNVERIFIED** |
| **Odysseys** | **Vendor-run by browser-use** | 200 long-horizon web tasks. Definition and evaluator not independently inspected |
| **WAA (Windows Agent Arena)** | Used by UFO² | Not verified directly |

---

## 4. Per-system notes

Each entry answers the eight questions. Where I did not read a primary source, the
answer is **UNVERIFIED** and I name the source I would need.

### 4.1 ChatGPT agent (ex-Operator) / OpenAI CUA

1. **Action space.** Own virtual computer running in the cloud; full mouse+keyboard on
   a remote machine, plus code interpreter and connectors. Not your logged-in browser.
2. **Perception.** Screenshot pixels. No DOM, no accessibility tree — that is the point
   of the CUA model. OpenAI's Operator post describes a model "trained to interact with
   graphical user interfaces (GUIs)—the buttons, menus, and text fields people see on
   a screen": <https://openai.com/index/introducing-operator> (retrieved 2026-09-29).
3. **Grounding / reliability.** Coordinate prediction from pixels. The product history
   is the reliability record: Operator was folded into ChatGPT agent because of
   "reliability gaps on commerce flows (CAPTCHAs, complex JavaScript checkouts)" — but
   that reason comes from **a tracker, not OpenAI — UNVERIFIED**
   (<https://presenc.ai/research/openai-operator-update-tracker-2026>).
   A third-party review claims OSWorld shows "a 20-plus point gap between Operator and
   the actual top performers" (<https://coasty.ai/blog/openai-operator-review-2026-20260403>,
   retrieved 2026-09-29) — self-interested publisher; **UNVERIFIED**.
   **Published success rates: none on openai.com that I could retrieve.**
4. **Safety.** Takeover by the user; a blocked-site list applied "across both the
   virtual browser and connectors"; Enterprise/Edu workspace toggle **defaulted OFF**;
   RBAC. Verbatim from <https://help.openai.com/en/articles/11752874-chatgpt-agent>
   (retrieved 2026-09-29). **There is no API-level confirmation gate** — the developer
   path is the raw `computer-use` tool, and gating is the developer's problem.
5. **Long horizon.** Quota-bounded (40–400 messages/month), not run-bounded. Nothing
   documented about checkpoint, resume, or crash survival. **UNVERIFIED.**
6. **Benchmark openness.** OpenAI is named in the OSWorld-Verified acknowledgement list,
   i.e. it cooperates with third-party verification. That is a better channel than most
   vendors use. Its own numbers: **none published.**
7. **Cost.** Subscription + message quota; per-message credit pricing on Business.
   API cost for CUA is dominated by screenshot token volume.
8. **Licence.** Proprietary SaaS. Developer path is the Agents SDK (Apache-2.0) plus the
   `computer-use` API tool.

### 4.2 Anthropic computer use + browser use

Source for all of §4.2: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>,
retrieved 2026-09-29 (2,247 lines; I read the first 886).

1. **Action space.** `computer_toolset_20260801` is **one** `tools` entry that expands to
   **17 member tools**: `screenshot`, `zoom`, `left/right/middle/double/triple_click`,
   `left_click_drag`, `mouse_move`, `left_mouse_down`, `left_mouse_up`,
   `cursor_position`, `scroll`, `type`, `key` (with `repeat` 1–100), `hold_key`
   (`duration` ≤ 300 s), `wait` (≤ 300 s). Every `tool_use` block carries
   `"toolset_name": "computer"` and is dispatched on the `(toolset_name, name)` pair.
   **Batch actions** return several blocks in one response, run *in order*, stop at the
   first failure, and every skipped block must be answered with a fixed halt string.
   A separate **browser use** toolset exists for page-only work and "doesn't need a full
   desktop environment".
2. **Perception.** Pure pixels. `zoom` returns a region at full resolution, scaled to
   fit, with coordinates still expressed in **full-screenshot space**, never zoomed
   space. **Cost of the pixel choice:** every step pays a full-screen image round-trip,
   and the only structural mitigation is a `screenshot` suffix. Anthropic's own advice —
   "Claude sometimes assumes outcomes of its actions without explicitly checking their
   results" — is a pixel-loop pathology a prompt can only nudge, not fix.
3. **Grounding.** Coordinates are defined relative to the screenshots *you* return;
   downscale and you must scale coordinates back up. That is a documented footgun. No
   verified success rate is published on this page.
4. **Safety.** The API ships **no** confirmation gate. Anthropic's own `<Warning>` is
   four pieces of advice: dedicated VM/container with minimal privileges; do not give the
   model login credentials; allowlist domains; **have a human confirm decisions with
   meaningful real-world consequences**. A prompt-injection classifier over tool output
   steers the model to re-check provenance, and it **can be opted out of by contacting
   support**. Two details worth stealing verbatim: (a) `display_width_px` /
   `display_height_px` / `display_number` were *removed* from the tool so coordinates can
   never mean something different from the screenshot space; (b) the batch-action
   section warns that "a batch can complete a multistep action within one turn", so a
   per-action confirmation must run **before each block**, not once per turn.
5. **Long horizon.** None in the API. Guidance is external: run end-to-end verification
   **at the start of each session**, not only after implementation
   ("Effective harnesses for long-running agents").
6. **Benchmark openness.** Model-level OSWorld numbers live on Anthropic's model pages,
   not here. **UNVERIFIED** in this slice.
7. **Cost.** Per token; screenshots dominate.
8. **Licence.** API. Reference implementation in `anthropics/anthropic-quickstarts`
   (`computer-use-demo`).

### 4.3 Claude in Chrome

1. **Action space.** Reads the page you are on; clicking links, navigating, typing,
   filling forms, **using your existing logins**. Not a VM — *your* browser, *your*
   sessions.
2. **Perception.** Real page content plus vision. Structurally far less
   injection-exposed than pixels on a remote desktop, because the agent is reading the
   same DOM the user is.
3. **Grounding.** Not disclosed. **UNVERIFIED.**
4. **Safety — the strongest published position in this category.** Two claims, both
   attributed to Anthropic:
   - The GA mechanism: "A safety classifier validates each action before it's performed
     to ensure it's safe and matches your request", the same mechanism as Claude Code
     auto mode (<https://claude.com/blog>, entry dated 2026-08-26, retrieved 2026-09-29).
   - The research-preview injection eval: **123 test cases across 29 attack scenarios**.
     Autonomous-mode mitigations took injection success from **23.6% → 11.2%**; on a
     four-attack-type browser "challenge" set they took it from **35.7% → 0%**
     (<https://gigazine.net/gsc_news/en/20250827-claude-for-chrome>, 2025-08-27,
     retrieved 2026-09-29). This is a **secondary source reporting Anthropic's own
     figures**; I did not reach the Anthropic research post. **Partially UNVERIFIED** —
     the numbers are specific enough to be real, but the primary is unconfirmed.
   - Enterprise admins can restrict the extension to approved domains.
   - The Cowork built-in browser is stated to carry "the same safeguards … including the
     checks that review Claude's actions against what you asked for", and its own
     prompt-injection risk is disclosed plainly.
5. **Long horizon.** Undocumented.
6. **Benchmark.** None published.
7. **Cost.** Subscription, paid plans.
8. **Licence.** Chrome Web Store extension. Not usable as a library.

### 4.4 Manus

Sources: <https://manus.im/solutions/finance> (product nav),
<https://manus.im/docs/introduction/plans>, <https://aipricecompare.org/apps/manus.html>
(2026-09-02), <https://www.nocode.mba/articles/manus-ai-pricing> (2026-09-25), all
retrieved 2026-09-29.

**Ownership is genuinely contested and I will not paper over it:**
- sig.ai (<https://geo.sig.ai/brands/manus-ai>) says **Meta acquired Manus for $2B+ in
  December 2025**.
- Tracxn (<https://tracxn.com/d/companies/manus/...>) says **acquired by Meta on
  2026-04-27**.
- nocode.mba says the acquisition **was blocked by Chinese regulators in April 2026**
  and Manus remains independent.
- aipricecompare (2026-09-02) says **April–Jun 2026 regulators ordered the deal
  unwound**, and notes an Aug 2026 rename of the Standard and Customizable plans to
  "Pro".
- The App Store listing still reads **"Manus from Meta"**
  (<https://app.sensortower.com/overview/6740909540>); Tracxn lists the company as
  *acquired*.

These cannot all be true. The most likely reading is: acquired Dec 2025, ordered
unwound Apr–Jun 2026, ownership and branding still in flux. **I found no primary
source — no filing, no Meta or Manus announcement — so this stays UNVERIFIED**, and it
is a caution about Manus rather than a finding.

1. **Action space.** Cloud browser + VM + code execution + document/spreadsheet/slides
   generation. "Wide Research" fans a request into parallel sub-agents.
2. **Perception.** Visual on the cloud browser. **UNVERIFIED** in detail.
3. **Grounding.** **UNVERIFIED.** The site links a "Benchmarks" page I did not fetch.
4. **Safety.** Cloud isolation. No documented per-action gate.
5. **Long horizon.** Scheduled Tasks; Wide Research parallelism; a credit system that
   makes runaway visible. Resume behaviour undocumented.
6. **Benchmark.** **UNVERIFIED.**
7. **Cost.** The most useful published cost *model* in the category, because the bands
   are stated: simple query 10–50 credits, web research 100–300, deep research 500–900,
   code 200–500, web app 500–1,000+, wide research 800–1,500+. Plans: Free (300 daily
   credits), Pro $20 / 4,000, Pro+ $40 / 8,000, Extended $200 / 40,000; 20 concurrent
   tasks on paid. **Third-party sourced — UNVERIFIED against manus.im.**
8. **Licence.** Proprietary; an API is listed. Not usable as a library.

### 4.5 browser-use

1. **Action space.** Playwright. The repo's own `AGENTS.md` documents the default set:
   `search` (DuckDuckGo/Google/Bing), `navigate`, click, scroll, plus custom
   `@controller.action(...)` registrations.
2. **Perception.** **DOM/CDP, not pixels.** This is the strategic difference from every
   CUA product above it.
3. **Grounding.** Element handles from a live DOM query. The reliability lever is the
   *prompt*, and the project publishes a "Prompting Guide" asserting "Prompting can
   drastically improve performance and solve existing limitations of the library."
   That is an admission to take seriously: the product's answer to unreliability is
   better prompting.
4. **Safety.** **No in-agent confirmation gate found.** It ships explicit authentication
   examples. The commercial sibling (**Browser Use Cloud**) sells *stealth, proxy
   rotation and CAPTCHA solving* — anti-detection infrastructure, which is the opposite
   of a permission model and worth naming explicitly in a competitive read.
5. **Long horizon.** Parallel agents; one browser context per agent.
6. **Benchmark.** Self-claimed **#1 on "Odysseys" at 87.4%** over 200 long-horizon web
   tasks, plus a 100-task benchmark published open source at `browser-use/benchmark`.
   **Both self-reported.** Odysseys' evaluator was not inspected. **UNVERIFIED.**
7. **Cost.** Your own LLM cost, plus optional cloud browser sessions.
8. **Licence.** **MIT.** Python ≥ 3.11; PyPI 0.13.8 (2026-08-16). PostHog telemetry is
   on by default with a documented opt-out.

**Two adjacent 2026 repos worth knowing about:**
- **`browser-use/browsercode`** (<https://github.com/browser-use/browsercode>, created
  2026-04-21, MIT, 648★, 15,418 commits) — a **fork of `anomalyco/opencode`** whose one
  new primitive is `browser_execute(code)`: the agent writes JavaScript that drives
  Chrome over raw CDP, and reusable scripts land in `.bcode/agent-workspace/`. Its claim
  — "outperforms every browser agent we have tested it against" — names **no benchmark
  and no competitor**, so it is marketing, not evidence.
- **`ifuryst/open-browser-use`** (<https://pkg.go.dev/github.com/ifuryst/open-browser-use@v0.1.39>,
  published 2026-05-17, MIT) — extension + CLI across JS/Python/Go, describing itself as
  "an open-source alternative to the Chrome Browser Use capability recently shipped in
  Codex.app". **I did not verify that Codex.app shipped such a capability — UNVERIFIED,
  but if true it means OpenAI shipped a browser-agent surface without announcing it.**

### 4.6 Skyvern

- Alive; docs **v1.0.22** (<https://www.skyvern.com/docs>, retrieved 2026-09-29).
- "Skyvern is a Playwright extension that adds AI-powered browser automation"
  (<https://github.com/skyvern-ai/skyvern>) — the same DOM-first thesis as browser-use.
- Perception is stated as vision ("reads the page visually", "automates browser-based
  workflows using LLMs and computer vision") **combined** with the Playwright DOM.
- **Architecture worth stealing: Planner–Agent–Validator.** A distinct validator stage is
  the cheapest known defence against an agent declaring a task done when it is not.
- Browser lifecycle is unusually well specified: `launch_cloud_browser()` returns a
  Playwright context; sessions time out at **5–240 minutes** (default 60) with a 422
  below 5; cookies / localStorage / auth persist across pages; **Browser Sessions keep a
  browser alive between API calls**, which is the concrete mechanism for a multi-step
  flow that survives separate model turns.
- **No published success rate. UNVERIFIED. Cost not retrieved. Licence: OSS core +
  commercial cloud.**

### 4.7 Stagehand / Browserbase

- npm `@browserbasehq/stagehand` **3.6.0**, MIT, ~1,011,517 weekly downloads, 938
  versions, 22k GitHub★ (<https://www.npmjs.com/package/@browserbasehq/stagehand> and
  <https://docs.browserbase.com/welcome/quickstarts/stagehand>, both 2026-09-29).
- **The most important design idea in the category, stated by the vendor itself:** "Most
  teams combine both: agent for exploration, individual primitives for critical paths"
  (<https://www.browserbase.com/stagehand>). Four primitives — `act`, `extract`,
  `observe`, `agent`. `agent` is for a checkout flow; the deterministic primitives are for
  the part that must not hallucinate.
- **Self-healing caching** is the reliability *and* cost story: observed
  action/observation pairs are cached; a cached workflow **runs without LLM inference**
  and re-engages the model only when the site changes and the automation breaks. That is
  the cheapest known answer to both token cost and drift, and it is the single most
  transferable idea in this report.
- Multi-region (us-west-2 default, us-east-1, eu-central-1, ap-southeast-1), stealth,
  CAPTCHA solving, `keepAlive` (the browser survives `stagehand.close()` **and a parent
  process crash**), local CDP attach to your own Chrome.
- Python + Go + Ruby + Java + Rust since **2026-01-13** via a canonical TypeScript
  implementation (<https://www.browserbase.com/changelog/canonical-stagehand>).
- **No published success rate. UNVERIFIED.**

### 4.8 Microsoft UFO³ / UFO²

- Alive. **UFO² entered Long-Term Support** (2025-04); **UFO³ Galaxy** is the current
  recommended path — multi-device, DAG-based `ConstellationAgent` + `TaskOrchestrator`,
  Unified AIP protocol, Windows + Linux device agents today
  (<https://github.com/microsoft/UFO>, <https://microsoft.github.io/UFO/infrastructure/agents/overview>,
  both 2026-09-29).
- **The action space is hybrid, and that is the headline**: "a unified GUI–API execution
  model, where agents seamlessly combine traditional GUI actions (e.g., clicks,
  keystrokes) with native Windows or application-specific APIs", exposed as **MCP
  commands** (Win32 API on Windows, shell on Linux, AppleScript planned on macOS)
  (<https://arxiv.org/html/2504.14603v1>, 2026-09-29).
- **Perception: the UI Automation tree**, not pixels. AppAgents "use UI Automation for
  control element discovery". The paper's claim is that "deep OS integration unlocks a
  scalable path toward reliable… desktop automation".
- **Reliability, self-run, arXiv 2504.14603** (vendor-run → self-reported). OSWorld-**W**:
  UFO²+o1 **32.7%**, UFO²+GPT-4o 28.6%, Operator computer-use 14.3%, Agent S 12.2%,
  OmniAgent 8.2%. WAA: 30.5% / 27.9% / 20.8% / 18.2% / 19.5%. This is a Windows-only
  subset measured **before** the 2025 Verified repair, so it is not comparable to a 2026
  leaderboard number.
- **Non-interference: the best answer in the category to the real UX problem.** A
  **Picture-in-Picture nested desktop built on Windows Remote Desktop loopback**: the
  agent automates a virtual desktop while the user keeps working in the real one, so
  mouse and keyboard are never contested. Alpha has no equivalent.
- Stealable details: a 7-state FSM with a 4-phase pipeline
  (`DATA_COLLECTION → LLM → ACTION → MEMORY`), a global **blackboard** for inter-agent
  state, and a `CONFIRM` agent status.
- **MIT licence. Genuinely runnable on Windows today.**

### 4.9 OpenAI Swarm and Microsoft AutoGen — both superseded

See §1.4 and §1.5. One line each: Swarm is a 29-commit educational sample whose
successor (Agents SDK) is Apache-2.0 and ships Guardrails; AutoGen is community-managed
and its successor (Microsoft Agent Framework 1.0) is GA with A2A and MCP
interoperability. **Neither is worth building on in 2026.** AutoGen's `agbench` and
Magentic-One remain the interesting artifacts.

### 4.10 OmniAct, UI-TARS, Aguvis, AgentBench, WebArena/WebVoyager

- **OmniAct** is a **dataset/benchmark** (Writer, 9,802 desktop+web episodes), not a
  product. Cited on the OSWorld page as a comparison.
- **UI-TARS / Aguvis** ship as open-weight models. The OSWorld page lists UI-TARS as an
  evaluated baseline. Third-party leaderboard readings (Huzzle Labs, 2026-07-30,
  <https://labs.huzzle.com/huzzle-world>) quote **UI-TARS-2 at 53.1%** and
  **uitars-1.5-7b at 29.6%** on OSWorld Verified. Plausible, but I did not confirm
  against the live leaderboard — **UNVERIFIED**.
- **AgentBench** is live (1,091 instances, 8 environments) per the OSWorld comparison
  table; some component environments are not open.
- **WebArena / VisualWebArena / WorkArena / WebShop / MiniWoB++** are all still listed on
  the OSWorld page (812 / 910 / 23k / 125 / 12k instances). Structural and reproducible,
  and structurally narrow — MiniWoB++ has 125 tasks and **one** execution-based
  evaluator, which is why it has been saturated for years.
- **WebVoyager**: I did not fetch its page this run. **UNVERIFIED** beyond §3.1.

---

## 5. Benchmark honesty

This is the section I will not soften.

### 5.1 The headline numbers circulating today are mostly incomparable

Four different things are all being called "OSWorld":

| Name | What it is | Who controls it |
| --- | --- | --- |
| **OSWorld** | The 2024 original. 369 tasks, 361 after excluding 8 Google-Drive tasks. Paper-time best model **12.24%**, human **72.36%** | HKU et al. |
| **OSWorld-Verified** | July 2025 repair: fixed community-reported broken examples, AWS support cutting evaluation to under an hour | HKU et al. |
| **OSWorld 2.0** | **Released 2026-06-26**, long-horizon | HKU et al. |
| **"OSWorld" in a press release** | Usually a vendor's own run of one of the above, on its own scaffold, with its own action budget | The vendor |

The official page (<https://os-world.github.io/>, retrieved 2026-09-29) states of its
leaderboard: *"These are official results **evaluated by our team** under unified
settings and environment."* It also states the submission process: to appear on the
verified board you must **schedule a meeting and run your agent on the maintainers'
side**, and disclose your implementation.

**That is the single most important sentence in this report.** A benchmark that
requires the maintainer to run your code, under their environment, before it appears
on the board, cannot be quietly gamed by a scaffold. Very few benchmarks in software
have that property. Everything below should be read against it.

### 5.2 What I could not read, stated plainly

The OSWorld results table **loads via JavaScript**. My fetch returned the page with the
literal string **"Loading verified benchmark data..."** and no numbers. **I could not read
the official leaderboard.** Every third-party number below is therefore **UNVERIFIED
against the primary**.

### 5.3 The conflicting numbers, side by side, all retrieved 2026-09-29

| Claim | Source | Benchmark named | Who ran it | Verdict |
| --- | --- | --- | --- | --- |
| Agent S3 w/ bBoN **63.5%** | codesota.com | "OSWorld" (2026) | aggregator | The **least inflated** aggregator number in circulation — 63.5% is in the plausible band for a mid-2025 run |
| Qwen3.8 Max **86.1%**; Claude Mythos 5 **85%**; Claude Fable 5 **85%**; GPT-5.5 **78.7%** | benchlm.ai, "data verified 2026-08-31" | OSWorld-**Verified** | aggregator | **Unverified.** Plausible *shape* (frontier models near 85% in 2026) but the provenance chain is editorial review, not a run log. Credit where due: the page's own "honest limit" line says "A success rate reflects the complete model-agent system, not the base model alone" |
| Z-Agent **90.2%**, "OSWorld's first 90%+", 361 tasks, 325.59 points; cross-app 78.81% over 93 tasks; GIMP 24/26 | GlobeNewswire press release, Intelligence Indeed, 2026-08-05 | "OSWorld" | **the vendor** | **A press release.** Self-declared first, self-run, and it does not say the run went through the maintainers' verification process. Treat as marketing |
| Claude Sonnet 4.6 **72.5%**; Operator **38%**; "Coasty scored 82%" | coasty.ai | "OSWorld" | a competitor, self-reported | **Advertising. Do not use.** The 44-point self-gap is the tell |
| HuzzleWorld-8B **58.6%** (#11 of 41 overall, #1 among ≤8B) | labs.huzzle.com, 2026-07-30 | OSWorld Verified | vendor, on the public board | The **most credible vendor number in this set**, because it is a *small* claim on a *large* board and it publishes its competitors' numbers too |
| Browser Use **87.4%**, "Odysseys", 200 tasks | browser-use README | **Odysseys** (not OSWorld) | vendor | **Vendor-run benchmark, unknown evaluator.** Not comparable to anything in this table |
| UFO² **32.7%** OSWorld-W | arXiv 2504.14603 | OSWorld-**W**, Windows subset | vendor | Honest self-report on a *harder, narrower* slice. **Not comparable to 2026 numbers** |

### 5.4 Claims that are outright wrong

- **"up to 90% on OSWorld" attributed to ChatGPT agent / Operator.** I found no such
  claim on `openai.com`. It circulates in vendor-adjacent blogs. OpenAI's own
  introduction post makes **no** OSWorld claim at all. **Do not repeat it.**
- **"OpenAI Operator scored 20.8% / 14.3%."** Those are UFO²'s table numbers
  (arXiv 2504.14603), measured on OSWorld-**W**, the Windows subset. Quoting them as
  Operator's general OSWorld score is wrong.
- **Any comparison between an "OSWorld" number and an "OSWorld-Verified" number.** They
  are different task sets. The Verified release says explicitly: "Please compare your
  OSWorld results with the new benchmark results when running the latest version" —
  which means old numbers are **not** comparable by default.

### 5.5 The structural problem, named

A success rate in this category is a property of **nine** things at once, and a number
quoted without them is not a measurement:

1. benchmark version and exact task subset (361 vs 369; the -W subset)
2. model *and* version
3. agent scaffold / harness / prompt
4. observation modality (screenshot / a11y tree / DOM / hybrid — OSWorld publishes
   separate verified tables for Screenshot, A11y tree, Screenshot + A11y tree, and
   Set-of-Mark)
5. action interface and action budget
6. attempt policy (one shot vs best-of-N)
7. environment revision and evaluator
8. who executed it
9. whether the run was maintainer-verified

**Any number that does not name its observation modality is not finished.**

### 5.6 Bottom line

- **Credible:** anything on the OSWorld-Verified board that arrived through the public
  evaluation process.
- **Weak but not worthless:** small, self-limiting claims on public boards (Huzzle's
  58.6% at ≤8B).
- **Meaningless:** vendor-run benchmarks the vendor designed (Odysseys), vendor press
  releases (Z-Agent), and competitor-blog tables (Coasty).
- **The marketing claim that is most wrong in aggregate** is that browser/computer
  agents are "production-ready". Stanford HAI's 2026 AI Index is quoted (second-hand) as
  saying OSWorld went from ~12% to over 66% in twelve months and that this "reclassifies
  computer-using agents from the experimental phase to the production-ready phase". A
  benchmark score is **not** production readiness — and the Intelligence Indeed release
  makes that exact point in its own closing section: "there is still a distance between
  the laboratory and the production line." A vendor arguing against its own headline is
  the most credible sentence in this entire ecosystem.

---

## 6. Alpha gap analysis

Every row was read in the source at `C:\Users\PREM KUMAR\Videos\alpha-r2-autonomous\backend\`.
`PRESENT` means I read the code that implements it. `PARTIAL` means the mechanism exists
but a named capability is missing. `ABSENT` means I searched and found nothing.

### 6.1 Action space and grounding

| Capability | Competitor(s) | Alpha | Evidence | Effort | Real gap or deliberate choice? |
| --- | --- | --- | --- | --- | --- |
| Stateful multi-step browser loop | browser-use, Skyvern, Stagehand, ChatGPT agent | **PRESENT** | `community/browser_automation/tools.py:279` navigate, `:308` snapshot, `:321` click, `:342` type, `:385` back; session survives tool calls (`session.py:257` `BrowserSession`) | — | — |
| Grounding by index into a just-observed table, never a selector | browser-use `jev-ultrafast`; Anthropic browser use tool | **PRESENT** | Ref-indexed snapshots; Alpha's own `browser_supervisor_tool.py:10-13` docstring names `browser-use/jev-ultrafast` as the pattern it adopted: "the model names an element **index** from an observed table, never a selector, coordinate or script" | — | Alpha already copied the right idea |
| Non-pixels perception for the browser | browser-use, Stagehand, Skyvern | **PRESENT (DOM)** | Playwright `Page` + accessibility-tree-backed element list (`session.py:481` `_snapshot_impl`) | — | Alpha is on the DOM side of the fork. Correct choice |
| **Scroll as a first-class action** | Anthropic (`scroll`), Stagehand | **ABSENT** from the browser tool set | Only 8 browser tools exist (`community/browser_automation/__init__.py:14-25`): navigate, snapshot, click, type, get_text, back, screenshot, close. **No `scroll`, no `key`/`hotkey`, no `wait`, no `select`/dropdown, no `zoom`, no tab switching tool** | **S** | **Real gap.** Infinite-scroll and lazy-loaded pages are unreachable; so is any keyboard-driven web UI. `session.py:649` already has `_activate_tab`, so tab control is implemented but not exposed |
| **Human confirmation before an irreversible browser action** | Claude in Chrome (classifier + auto-approve), Anthropic's own documented advice, Google auto-browse ("always ask for confirmation before completing sensitive tasks") | **ABSENT** | Grepping the whole harness for `requires_approval` / `approval_required` / `human_in_the_loop` / `confirm_required` returns 18 files — `browser_automation/tools.py` is **not among them**. `computer_use/guard.py:44-48` blacklists only four *hotkey combos*; `sandbox/computer_use.py:40-46` tiers only *shell commands* | **M** | **Real and serious.** A browser click that submits a form, sends an email, or posts is irreversible and Alpha gates none of it. See §8 R1 |
| **Post-action verification, not just post-action screenshot** | Stagehand (`observe` returns state you assert on), Skyvern (Planner–Agent–**Validator**) | **PARTIAL** | Every browser action returns a fresh snapshot (`tools.py:214` `_snapshot_command`), but nothing *asserts* the outcome. There is no validator stage | **M** | **Real gap.** An agent that submits a form gets a new snapshot either way; "did it work?" is left to the same model that guessed |
| **Self-healing cached actions (no LLM inference on repeat)** | Stagehand auto-cache + self-heal | **ABSENT** | No cache store is consulted in `session.py:_snapshot_impl` / `_click` / `_type` | **M** | **Real gap, and it is also the cost gap.** Every repeat visit re-pays full LLM + screenshot cost |
| **Isolated browser execution (container/VM)** | Anthropic's documented requirement (dedicated VM/container); Manus (cloud browser); UFO² (PiP nested desktop via RDP loopback) | **ABSENT** | Browser runs Playwright **in the Gateway process**: `session.py:184` `_PlaywrightLoopThread` is a private *daemon thread in this process*. No `SandboxProvider` anywhere in `session.py`; no sandbox import in `community/browser_automation/` | **L** | **Real gap with a security dimension.** The sandbox provider exists for shell work; the browser bypasses it entirely, so a malicious page runs with the Gateway's filesystem access |

### 6.2 Desktop / computer-use route

| Capability | Competitor(s) | Alpha | Evidence | Effort | Real gap or deliberate choice? |
| --- | --- | --- | --- | --- | --- |
| Full desktop mouse/keyboard | Anthropic (17 member tools), UFO², Operator | **PRESENT** | `tools/builtins/os_computer_tool.py:233` `desktop_mouse_action`, `:283` `desktop_keyboard_action`, `:333` `desktop_window_manage`, `:151` `desktop_screenshot` | — | — |
| UIA-tree perception, not pixels | UFO² (UI Automation), Alpha's own `system_one` | **PRESENT** | `computer_use/accessibility.py` (20 KB, optional `pywinauto`, *honest-unavailable* contract); `system_one_policy.py` projects UIA elements to bounded semantic fields with `DEFAULT_MAX_ELEMENTS = 200` (`system_one_policy.py:59`) and hard char caps (`:57-61`) | — | Alpha matches the strongest perception design in the category |
| Index-based desktop grounding that never sends coordinates to the model | — (Alpha-specific) | **PRESENT** | `system_one_policy.py:287-317` `ComputerActionSpace.resolve(operation, target)`; model returns an index, geometry is resolved locally after re-observing semantic identity (`ComputerElement.semantic_signature`, `:221`) | — | **A genuine advantage.** See §7 |
| Deterministic safety guard | Anthropic ships none; Google's products gate; Skyvern relies on the platform | **PRESENT** | `computer_use/guard.py:219` `observe_pointer` panic-corner kill-switch; `:245` `check_action`; `:450` `set_window_bounds` window-boundary lock; `:155` `acquire_lease`; `:328` `arm_destructive_confirmation(keys, *, armed_by="operator")` | — | See §7 |
| **Blast-radius tiering of commands** | Browser Use Cloud sells *stealth/CAPTCHA* — the opposite posture | **PRESENT** | `sandbox/computer_use.py:40` `ActionSafetyTier` (SAFE/SENSITIVE/FORBIDDEN), `:59` `BlastRadiusPolicy`, `:120` `ComputerWorker`, `:136` `grant_approval(approved_by="operator")` | — | See §7 |
| Non-interference (agent does not steal the user's mouse) | **UFO²'s PiP nested desktop on Windows RDP loopback** | **ABSENT** | `desktop_*` tools act on the real desktop; `dispatcher.py` wraps `pyautogui`/`pynput` on the live session | **L** | **Real gap.** Nothing in the repo creates a nested/virtual desktop |
| Sandbox-classified command execution | — | **PARTIAL by design** | `sandbox/computer_use.py:34` `NO_EXECUTION_DISCLOSURE`: "validation only: this engine classified the command against the blast-radius policy and spawned no process" | **S** (wire it) | **Deliberate and honest**, but it means the gate protects a path that does not execute. See §7 |

### 6.3 Long-horizon / durability — where Alpha is ahead

| Capability | Competitor(s) | Alpha | Evidence | Effort | Real gap or deliberate choice? |
| --- | --- | --- | --- | --- | --- |
| Per-effect `UNKNOWN` vocabulary for "what might have happened" | **No consumer product I found has this.** Anthropic ships *none* of it | **PRESENT in code, ABSENT in production** | `runtime/side_effects/ledger.py:172` `SideEffectLedger` Protocol; `:219` `InMemorySideEffectLedger`; `persistence/side_effects/sql.py:74` `SqlSideEffectLedger` over a `tool_side_effects` table; migration `persistence/migrations/versions/0027_side_effect_ledger.py` | **M** | **THE finding.** See below |
| Connectivity as a first-class state so an outage is not a task failure | Nobody in this category has it | **PRESENT** | `runtime/network/__init__.py:1-45`: four states, `UNKNOWN` never rounded to `OFFLINE` and still *permits* an attempt, `DEGRADED` never parks, single probe never flips | — | See §7 |
| Honest session lifecycle vocabulary | — | **PRESENT** | `runtime/sessions/states.py`, 17 states in ACTIVE/WAITING/TERMINAL, derived (no new table, no new worker) | — | See §7 |
| Bounded, non-looping process supervisor | — | **PRESENT** | `runtime/supervisor/policy.py` `SupervisorPolicy`/`RestartLedger`; sliding-window restart budget; `RESTART`/`SAFE_MODE`/`GIVE_UP` | **S** (wiring) | **PARTIAL** — `runtime/AGENTS.md:59` admits "the supervisor is **not yet wired** into the Windows launcher" |
| Ordered shutdown whose report cannot lie | — | **PRESENT** | `runtime/shutdown.py:1-45`: 8-phase ordered drain, `is_clean` only when every step completed, emergency path capped at 5 s | — | See §7 |
| **Browser-session survival across a crash** | Stagehand `keepAlive`; browsercode's persistent browser; Skyvern Browser Sessions | **PARTIAL** | `session.py` keeps a session alive across *tool calls in one thread*; `BrowserSessionManager` has an idle timeout and a 32-session cap (`session.py:139` `_DEFAULT_MAX_SESSIONS = 32`), but nothing persists a browser profile or a mid-flow cursor across a Gateway restart | **L** | **Real gap.** This is exactly the "resume a 40-step flow" problem nobody in the category has solved either |

### 6.4 Documentation defect found while reading

`backend/packages/harness/alpha/runtime/AGENTS.md` contradicts itself about the
side-effect ledger:

- Line 41-42: "the durable SQL implementation and its cross-process conditional
  transitions are in `alpha.persistence.side_effects`."
- Line 57-58, in the "Honesty boundary" list: "**the side-effect ledger has no SQL
  repository yet**."

The SQL repository **exists** (`persistence/side_effects/sql.py:74`,
migration `0027_side_effect_ledger.py`). The honesty bullet is stale and
under-claims. Meanwhile the bullet that *should* exist — that **nothing calls the
ledger** — is absent from both.

---

## 7. What Alpha does that competitors do not

Five things, each with a file:line, each verified by reading the code rather than the
docs. Two of them are not in any competitor's public architecture; one is an
architectural mistake to copy.

1. **The model never receives a coordinate, a bounding box, a selector, a UIA handle,
   typed text, a key, or a hotkey — only an index.**
   `computer_use/system_one_policy.py:287-317` (`ComputerActionSpace.resolve`) and
   `:221` (`ComputerElement.semantic_signature`). The tool re-observes the
   accessibility tree and compares the target's *semantic identity* before resolving
   fresh geometry locally. **Every** CUA product in this report passes raw pixel
   coordinates across the model boundary — Anthropic's docs are explicit that
   "Coordinates are in screenshot pixels", and OpenAI's Operator is built on exactly
   that. Alpha refuses the coordinate channel at the protocol level. This is the single
   strongest design decision in the codebase for this slice.

2. **A confirmation token the model cannot mint.**
   `computer_use/guard.py:328` `arm_destructive_confirmation(keys, *, armed_by="operator")`.
   The module docstring records the bug that motivated it: *"A bare boolean
   (`confirmed=True`) used to be enough, which meant the *model* could lift the
   blacklist by writing one more argument into its own tool call; there is deliberately
   no longer any boolean that a model-facing tool can set."* The same pattern appears
   independently in `sandbox/computer_use.py:136` `grant_approval(approval_id, *,
   approved_by="operator")` — approvals are granted **out of band** by an operator. No
   competitor in this report has a capability model where the agent structurally cannot
   self-approve. Anthropic's answer is "ask a human to confirm"; Google's answer is "a
   classifier approves"; Alpha's answer is "the model has no vocabulary for yes".

3. **An `UNKNOWN` side-effect status, and the `UNKNOWN → COMPLETED` transition is
   banned.** `runtime/side_effects/ledger.py` (`reconcile()` takes a
   `ReconciliationVerdict`; `UNDETERMINED` *reopens* the entry rather than settling it,
   because recording "could not tell" as a failure is how a duplicate gets created).
   Every product in this report has the same latent double-charge bug and none of them
   names it. Caveat, and it is a big one: **nothing calls this ledger in production**
   (§6.3). It is a correct design that is not yet a feature.

4. **A network outage is a state, not a task failure.**
   `runtime/network/__init__.py:1-45`. Four states; `UNKNOWN` is never rounded to
   `OFFLINE`; a `TIMEOUT` "proves nothing and must not park work"; entering `OFFLINE`
   and re-entering `ONLINE` each need corroborating consecutive observations; and
   `network_waiting` sits in `RECOVERABLE_RUN_STOP_REASONS` so the recovery service
   keeps the run alive. A long-horizon browser agent that loses Wi-Fi at step 30 of 40
   is the canonical failure this solves, and no browser agent product addresses it.

5. **Honesty is enforced at the boundary, not promised in prose.**
   `computer_use/dispatcher.py:_optional_import` returns
   `{"ok": false, "status": "unavailable", "dispatched": false, "reason": "dependency not
   installed: ..."}` and *never* a fabricated success;
   `tools/builtins/os_computer_tool.py:63-79` `_timeout_payload` says the abandoned
   thread "could not be cancelled and may still be in flight";
   `sandbox/computer_use.py:34` discloses on every result that no process was spawned.
   Competitors report "we ran it" where Alpha reports "we classified it and did not
   run it". In a category where the dominant failure mode is a fabricated success, this
   is a defensible position to build a product on.

**Deliberately *not* listed as an advantage:** the browser tool set is smaller than
browser-use's, and Alpha's browser runs unconfined in the Gateway process where
Anthropic's own guidance says it must not.

---

## 8. Ranked recommendations

Effort: S ≤ 1 day, M ≤ 1 week, L > 1 week. "Proves demand" names the competitor whose
shipped product demonstrates a market for the capability.

| # | What | Why | Proves demand | Effort | Risk | Honest caveat |
| --- | --- | --- | --- | --- | --- | --- |
| **R1** | **Add an irreversible-action gate to the browser tools.** Require an operator-issued, action-bound approval token — reusing the exact `grant_approval` shape already in `sandbox/computer_use.py:136` — before any action that submits a form, triggers a `POST`/`DELETE` navigation, or lands on a URL matching a payments/auth/checkout pattern. Wire it into `browser_type(submit=True)` first, since that is the one existing browser action that can commit a transaction | Alpha has the strongest guard in this category and **it does not cover the surface where the risk actually is**. Every competitor that touches a real user's browser gates it: Claude in Chrome's classifier, Google's "always ask for confirmation", Anthropic's explicit warning. Alpha is the only one that does not | Claude in Chrome (GA, all paid plans); Google auto-browse; Anthropic docs | **M** | False positives block legitimate research flows; a heuristic URL matcher will miss checkouts behind redirects | A regex cannot enumerate irreversible web actions. This is a **deny-by-default on submit plus a named-danger surface**, not a complete solution |
| **R2** | **Wire `SideEffectLedger` into the tool-dispatch middleware.** One call to `begin()` before the tool runs, `mark_in_flight()` with a lease, then `complete()`/`fail()`; a reclaimer on the existing `reclaim_expired_leases` tick in `app/gateway/deps.py:63`. Start with the `desktop_*` and browser tools only | The whole `UNKNOWN`/`reconcile` design — the most defensible idea in the codebase per §7.3 — currently **executes zero times**. One grep for `mark_in_flight\|reclaim_expired\|list_unknown` outside `side_effects/` and tests returns nothing. Meanwhile the *run-level* signal already exists: `SafeRunRecoveryService` refuses to replay a pending browser/tool/shell node and records `recovery_confirmation_required`, but cannot name *which* effect needs confirming. This connects the two | Nobody. **This is Alpha-only** | **M** | Middleware ordering bugs could double-record; needs the conditional-transition guard in `SqlSideEffectTransitionLost` to be exercised in production | The ledger is only as good as the tool boundary. It cannot see an effect performed inside a shell command, only a tool call |
| **R3** | **Add `browser_scroll`, `browser_key`, and `browser_select` (dropdown), and expose the tab tools that already exist** (`session.py:649` `_activate_tab` is implemented and unreachable from the model) | Alpha has 8 browser tools; the shortest path to parity with Anthropic's `scroll`/`key` is ~60 lines. Infinite scroll, lazy loading, keyboard-driven UIs and dropdowns are currently unreachable, which means Alpha's browser agent cannot complete a meaningful class of real web task | Anthropic (17 member tools); Stagehand (`act`/`observe`/`extract` + agent) | **S** | Element refs shift after a scroll; every scroll must return a fresh snapshot or the ref-index invariant breaks | This is capability parity, not advantage. It closes a gap; it does not win anything |
| **R4** | **Add action caching with self-healing invalidation** — key on (URL pattern, element role, element name, surrounding DOM digest); on cache hit, replay without an LLM call; on any mismatch or tool error, invalidate and fall back to the model | The cheapest known answer to both token cost and long-horizon drift. Stagehand ships exactly this and calls it the reason its workflows "run without LLM inference". Alpha re-pays full model + snapshot cost on every repeat visit, which is the dominant cost in this category | Stagehand (1M weekly downloads); browsercode's `.bcode/agent-workspace/` script reuse | **M** | A stale cache that replays confidently is *worse* than no cache — it converts a recoverable perception error into a silent wrong action. The digest must include enough page state, and a replay must be verifiable | Cache hit rate is everything. If Alpha's flows are not repetitive, this is infrastructure with no payoff. Measure before building |
| **R5** | **Run the browser inside a sandbox provider**, or at minimum a dedicated OS process with a scoped profile directory | Anthropic's documented requirement is a "dedicated virtual machine or container with minimal privileges"; Manus sells a cloud browser for exactly this reason; UFO² built a nested desktop so the agent cannot touch the user's session. Alpha's browser runs **in the Gateway process** (`session.py:184` private daemon thread), so a hostile page has the Gateway's filesystem and credentials. Alpha also holds live ChatGPT/Anthropic/Google API keys in that process | Anthropic docs; Manus (Cloud Browser); UFO² (PiP) | **L** | Playwright's loop-affinity model (`session.py:3-7`) makes this genuinely hard; a scoped browser profile is the cheap 80% | A scoped profile directory and a URL allowlist reduce blast radius but do **not** isolate. Do not describe the cheap version as isolation |
| **R6** | **Publish Alpha's own numbers on OSWorld-Verified 2.0, or say nothing** | §5 shows the entire category is drowning in unverifiable numbers. Alpha's own docs already set the standard (`docs/PRODUCTION_READINESS_INVENTORY.md` is "the authority on what is implemented"). A single verified run — with model, scaffold, observation modality, action budget and attempt policy all named — is worth more than any feature in this list, because it is the only thing competitors in this report cannot copy | The whole category (Huzzle published ≤8B numbers *with* its competitors' figures) | **L** | If Alpha's number is bad, publishing it costs credibility — which is exactly why only a verified number is worth publishing | **OSWorld 2.0 shipped 2026-06-26 and I did not fetch its leaderboard. Do not quote a number from an aggregator.** If the honest result is "Alpha has no comparable benchmark", say that instead |
| **R7** | **Add a validator stage to the browser loop** — after a submit, re-observe and assert the expected state change before reporting success | Skyvern's Planner–**Agent–Validator** split and Stagehand's `observe`-then-assert are the two independent answers to "the agent believes it succeeded". Alpha returns a snapshot and lets the same model that guessed judge the result | Skyvern; Stagehand | **M** | Without a stated expectation there is nothing to assert; this needs an `expect:` parameter on the mutating tools, which is an API change | A validator is only as good as the expectation. It must be able to return "unverified" and be shown |
| **R8** | **Fix `runtime/AGENTS.md` lines 41-42 vs 57-58** and add the missing honesty bullet about the ledger having no production caller | The file contradicts itself about whether the SQL repository exists. A guide that both under-claims and self-contradicts is worse than one that says nothing | n/a — internal | **S** | None | Pure documentation. Do it in the same change set as R2 so the claim becomes true before it is written |
| **R9** | **Expose the SentinelGuard's panic-corner and window-boundary state to the browser tools' siblings — i.e. give `browser_*` a per-thread execution lease** | `desktop_*` tools take a lease per thread via `_begin_side_effect` → `guard.acquire_lease` (`os_computer_tool.py:114-140`); `browser_*` tools take **nothing**. Two concurrent browser sessions in one thread can interleave against one another with no mutual exclusion | UFO²'s `CONFIRM` status; UFO's single HostAgent per session | **S** | Leases are process-local, so this does not survive a restart and must not be described as if it does | Consistency win, not capability. The browser is already serialised by the per-thread session manager in practice |
| **R10** | **Adopt a nested/virtual desktop for the `desktop_*` route** (UFO²'s approach: Windows Remote Desktop loopback, agent works in the PiP desktop, user keeps the real one) | The single biggest *UX* blocker to desktop agents is that they steal the mouse and keyboard. UFO² solved it with OS-native infrastructure rather than a prompt. Alpha's `desktop_*` tools act on the live session today | UFO² (MIT, arXiv 2504.14603, Windows, LTS) | **L** | Windows-only; RDP loopback has its own performance and licensing profile | This is a real differentiator **if** Alpha ever ships a desktop product. If the desktop route stays an optional Windows power-user feature, it is wasted effort |

**Not recommended, deliberately:** building a new browser agent framework. Alpha's
index-into-observed-table grounding is already correct and already matches what
browser-use converged on. The gap is safety, durability, isolation and cost — not the
action space.

---

## 9. Confidence and gaps

### 9.1 Confidence by claim type

| Claim type | Confidence | Why |
| --- | --- | --- |
| **Dead/renamed/discontinued status** (§1) | **High** | Every §1 claim is read off the vendor's own domain or the vendor's own repo README. Project Mariner's shutdown notice is on `labs.google.com`; Swarm's replacement banner is in `openai/swarm`'s README; AutoGen's maintenance banner is in `microsoft/autogen`'s README; Operator's sunset is in OpenAI's own post and Help Center |
| **Alpha source facts** (§6-§7) | **High** | Read directly. Where a claim is "absent", I state the grep that shows absence, and I distinguish "not wired in production" (`mark_in_flight` has no caller) from "not implemented" (`SideEffectLedger` exists but is unused) |
| **Action space / perception / safety architecture** (§3, §4) | **High** | Anthropic's and UFO's docs are explicit and quotable; Alpha's docstrings say what they do |
| **Pricing** (§3, §4.4) | **Medium-High for Anthropic/OpenAI/Manus-plan-tiers** | Anthropic/OpenAI figures are vendor-published. Manus figures are **third-party** |
| **Benchmark numbers** (§5) | **Low, and that is the finding** | I could not read the official OSWorld table (JavaScript-rendered). Every number in §5.3 is either an aggregator, a vendor press release, or a competitor's blog |
| **Ownership status of Manus** | **Low** | Four sources, four incompatible stories, no primary. Explicitly UNVERIFIED |

### 9.2 What I did not do

- **Did not run anything.** No `make dev`, no live stack, no browser session, no
  benchmark harness. Every "success rate" is one a vendor or paper published.
- **Did not verify the four coding-agent-slice deaths** reported by the sibling agent
  (Kiro CLI, Antigravity CLI, Devin Desktop, Continue). Recorded as context only (§1.6).
- **Did not fetch** `osworld-v2.xlang.ai`, the WebVoyager page, the OpenAI
  `computer-use` API reference, Anthropic's *browser use tool* reference page (I read
  the computer use page and its cross-links), Manus's "Benchmarks" page, Skyvern's
  architecture page, or Browserbase's pricing.
- **Did not read** `docs/architecture/durable-runtime.md` in full; my §6.3/§7
  long-horizon analysis is from `runtime/*/AGENTS.md` docstrings plus the code I read.
- **Did not audit** `agent_eye/`'s provider internals beyond its docstrings, and did not
  read the `web_search` provider implementations (DDG/Brave/Tavily/SearXNG/Sofya) —
  those are documented in `backend/AGENTS.md` and are search, not computer-use.

### 9.3 Unresolved question a reviewer should pick up

**Is the browser tool set's 8-tool ceiling deliberate or inherited?** Every tool has a
clean docstring and a consistent snapshot-returning shape, which suggests intent. But
`session.py` implements `_activate_tab`, `_tabs`, `dispatch_input`, `start_screencast`
and `push_live_frame` that no model-facing tool exposes — some are for the live viewer,
but `_dispatch_input` (`session.py:725`) in particular looks like an input channel
waiting for a caller. **Whether that is a deliberate human-in-the-loop path or a
half-finished autonomous one is worth a maintainer's answer**, because it determines
whether R3 is three tools or ten.

### 9.4 Housekeeping — NOT DONE, outside this slice

`scripts/generate_docs_index.py` fails closed on unclassified Markdown under `docs/`.
**This file will need a `FILE_OVERRIDES` entry before `docs/INDEX.md` will build.**
I was told explicitly not to add it, so I have not. Whoever merges this must add the
entry in the same change set or the index build will fail.




---
