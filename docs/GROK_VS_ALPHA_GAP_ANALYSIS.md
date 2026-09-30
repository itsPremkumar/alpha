# Grok (xAI) vs Alpha — Feature Comparison & Prioritized Gap Analysis

**Date:** 2026-09-30
**Author:** WorkBuddy AI research pass
**Scope:** Compare xAI's "Grok" capabilities (Grok 4.20 multi-agent council, Grok Bot product, Grok Build Parallel Agents/Arena) against the Alpha codebase, and produce a concrete list of features Alpha should add.

---

## 1. Executive Summary (read this first)

The premise of the request was "Grok can multiply itself, split tasks, collaborate autonomously, auto-create specialized agents — what do we need to add to Alpha?" After reading both the public Grok material and Alpha's actual source, the answer is **surprising and good news**:

> **Alpha already implements almost every one of those capabilities — and in several cases with stronger governance than Grok Bot.**

Specifically, Alpha already has:
- **Self-multiplying specialized agents** → `backend/packages/harness/alpha/bots/dynamic_profiles.py` (autonomous plan drafts a specialist profile, routes it through an approval gate + authority ceiling, installs it durably).
- **Chief-bot + specialized-bot teamwork with ownership handoff** → `bots/handoff.py` (`TaskHandoffPackage`, `escalate_task`, `execute_handoff`, `resolve_succession`), `bots/reassignment.py` (`claim_task`, `match_bot_for_task`, `reassign_after_failure`), a **Task Auction Matchmaker**, and Workforce Teams with moderator synthesis.
- **Human-in-the-loop approval** → `action/uap.py` (15-primitive Universal Action Protocol) + `projects/approval_queue.py` wired to bot installs, cost governance, RSI review, and DAG workflows.
- **Browser-operated app control, multi-agent teams, persistent bot roster, arena benchmarking, perpetual autonomy** → all present.

So this is **not** a "we are far behind" situation. The real work is a short list of **genuine gaps** where Grok is clearly ahead, plus a few places where Alpha is already ahead and should be *marketed* as differentiators.

**The three biggest genuine gaps:**
1. **Inference-time native multi-agent *reasoning* council** (Grok 4.20's Harper/Benjamin/Lucas: automatic task decomposition → parallel specialist thinking → multi-round debate/peer-review → captain synthesis, auto-triggered). Alpha's existing "council" (`enterprise/council.py`) is *release governance*, not task reasoning. This is the single largest capability delta.
2. **Persistent always-on "Agent Computer"** — a per-bot VM (browser + filesystem + terminal) that keeps running when the user closes the laptop, with **shared state across a user's bots**. Alpha's sandbox is per-run/thread-isolated and runs are trigger-driven.
3. **Routine capture from demonstration** and a **curated connector marketplace** — both missing in Alpha.

Everything else is P1/P2 polish. See §5 for the prioritized list.

---

## 2. What "Grok" actually is (three layers)

To compare fairly we must separate three different xAI products that the public lumps together as "Grok":

| Layer | What it is | Released |
|---|---|---|
| **Grok 4.20 inference-time council** | Inside the *model response*: 4 specialized replicas (Captain/Harper/Benjamin/Lucas) that auto-decompose a query, think in parallel, debate/peer-review, then synthesize one answer. Native, always-on for complex queries. | Feb 2026 |
| **Grok Bot** (product) | A persistent "digital colleague team": each bot runs on its own cloud VM (browser+FS+terminal), 7×24 autonomous execution, chief-bot coordination, shared VM state, app-level operation (drives real Gmail/Calendar/CRM even without APIs), routine capture, connector marketplace, human approval gates. | Aug 2026 |
| **Grok Build Parallel Agents + Arena** | Dev environment: up to 8 coding agents in parallel on one task; Arena Mode ranks/competes outputs. | Feb 2026 |

Alpha maps against **all three**.

---

## 3. Capability-by-capability comparison matrix

Status legend: ✅ **Have** · 🟡 **Partial** · 🔴 **Gap** · 🟢 **Alpha ahead**

| # | Grok capability | Alpha status | Evidence in Alpha |
|---|---|---|---|
| 1 | Persistent named bot identities ("digital colleagues" team) | ✅ Have | `docs/WORKFORCE.md` (Bots/Teams/Projects), `bots/profile.py`, bot roster, presence, DM inbox |
| 2 | Chief-bot coordinates specialized bots; bots message each other & pass ownership | ✅ Have (deeper) | `bots/handoff.py`, `bots/reassignment.py`, `capabilities/` Task Auction Matchmaker, Teams moderator synthesis, `/handoff` command |
| 3 | Self-multiplying: auto-create specialized profiles/agents per task | ✅ Have (gated) | `bots/dynamic_profiles.py` (`DynamicProfileStore` propose→approve→install), `planning/autonomous` `NewProfileSpec`; authority ceiling + `ApprovalQueue` gate |
| 4 | Task splitting / decomposition | ✅ Have | Lead-agent plan mode (`write_todos`), `subagents` `task()`/`batch_task()` |
| 5 | Autonomous parallel collaboration of specialists | 🟡 Partial | Subagents use benefit-based routing with parallel scopes, but dispatch is **lead-discretion**, not an automatic native council |
| 6 | **Inference-time multi-agent *reasoning* council** (Harper/Benjamin/Lucas debate + synthesis, auto) | 🔴 Gap | `enterprise/council.py` is **release-governance** (staging/quorum/signing), not task reasoning. No automatic specialist debate exists |
| 7 | 24/7 always-on execution independent of user session | 🟡 Partial | Scheduler + autonomous run mode + `test_autonomous_asi_perpetual`; but no persistent per-bot runtime; runs are trigger-driven |
| 8 | **Persistent Agent Computer** (per-bot VM: browser+FS+terminal, shared state) | 🔴 Gap | Sandbox is per-run/thread-isolated; `browser/cdp_bridge.py` + `community/browser_automation` exist but not as a persistent shared VM |
| 9 | App-level operation via real browser (no clean API needed) | 🟡 Partial | Browser automation present; **missing** authenticated Chrome-profile import + residential egress routing to operate arbitrary sites |
| 10 | Human approval at action boundaries (email/pay/delete/prod) | 🟡 Partial→✅ | `action/uap.py` + `ApprovalQueue` wired to bot installs, cost governance, RSI, DAG; an explicit per-primitive pause-and-confirm UX could be tightened |
| 11 | **Routine/Skill capture from demonstration** | 🔴 Gap | Skills are authored/proposed/installed (`propose_skill`, skill-creator); no "observe user doing a task once → record as reusable Routine" path |
| 12 | **Connector marketplace + one-click app connectors** (Gmail/Cal/Outlook/OneDrive/X, Composio 1000+) | 🔴 Gap (ecosystem) | MCP + extensions + managed integrations (Lark) + `SkillScan`; no curated marketplace/catalog UX |
| 13 | Parallel Agents (≤8 same task) + **Arena Mode** (competitive eval + rank) | 🟡 Partial | `benchmarks/arena.py` is SWE-Bench eval + leaderboard feeding the matchmaker; `batch_task` exists; **no per-task best-of-N selection arena** |
| 14 | Adaptive activation / capability cascade (Fast/Expert/Full) | 🟡 Partial | Lead-discretion delegation; no formal tier cascade |
| 15 | Run verification / honest "completed vs verified" | 🟢 Alpha ahead | `runtime/runs/verification` (acceptance criteria + evidence); `selfrepair` classification |
| 16 | Cross-installation federation | 🟢 Alpha ahead | `peer_network/` (Alpha-to-Alpha) |
| 17 | Guardrails (token/turn/loop caps, citation report contract, acceptance checks) | 🟢 Alpha ahead | `subagents` guardrails, `report_contract.py`, `acceptance_checks.py` |
| 18 | Multi-provider model routing / pricing / discovery | 🟢 Alpha ahead | `config.yaml` `model_routing`, `model_pricing`, `free_gateways`, discovery |
| 19 | IM channel integrations (Feishu/Slack/Telegram/DingTalk) | 🟢 Alpha ahead | `app/channels/` (Grok is X-centric) |
| 20 | Action-security scanning & request-scoped secrets | 🟢 Alpha ahead | `skills/skillscan/`, `runtime/secret_context.py` |

---

## 4. Where Alpha is already ahead (use as differentiators)

Don't pitch Alpha as "behind Grok." Pitch these as Alpha's edge:

- **Governed self-replication.** Alpha's `DynamicProfileStore` enforces `enforce_grant` so *a child never exceeds its creator's authority*, records a governance ledger, and routes every new profile through `ApprovalQueue`. Grok Bot is less explicit about this ceiling.
- **Honest verification.** Alpha separates `success` from `verified` via the run-verification overlay; Grok Bot markets "autonomous delivery" without that honesty layer.
- **Deeper swarm mechanics.** Task Auction Matchmaker, work-discovery, reassignment, succession, and escalation are more mechanically complete than Grok Bot's described "bots message each other in a thread."
- **Provider-agnostic + multi-channel.** Alpha is not locked to one ecosystem (X); it federates and bridges many IMs and many model providers.
- **Security substrate.** SkillScan + request-scoped secrets + injection-resistant skill loading.

---

## 5. Prioritized features to add to Alpha

### P0 — Build these first (Grok's true differentiators)

#### P0-1 · Inference-time Multi-Agent Reasoning Council
**Problem:** Alpha has no automatic, model-native specialist debate. Subagent delegation is the lead agent's discretionary choice; there is no "on any complex query, automatically spin Harper/Benjamin/Lucas-style specialists, debate, synthesize."
**What to build:**
- A `reasoning_council` module: given a query, an auto-classifier decides complexity tier (Fast / Expert / Full-council).
- On Full-council: decompose into sub-questions, dispatch parallel specialist agents (Research/Facts, Math/Code/Logic, Creative/Balance, Coordinator), run N rounds of structured peer-review (each agent critiques the others' claims), then Coordinator synthesizes with citations.
- Reuse existing `subagents` machinery (`task()` with `category=research|quick|...`) and `report_contract` for citations.
- **Adaptive activation**: simple queries bypass the council entirely (mirror Grok's overhead control). RL/threshold tuning to keep cost at ~1.5–2.5× not 4×.
- Reuse `AlphaSummarizationMiddleware` + `TokenBudgetMiddleware` so the council respects budgets.
**Why first:** It is the capability most associated with "Grok is smarter," and Alpha's foundation (subagents, categories, report contract) makes it reachable without new infra.

#### P0-2 · Persistent Agent Computer (always-on runtime + shared cross-bot state)
**Problem:** Grok Bot's core differentiator is a bot that runs on a persistent VM 24/7, even when the user is offline, and shares filesystem/browser/credentials with sibling bots. Alpha's sandbox is per-run/thread-isolated and runs end when the request ends.
**What to build:**
- An `agent_computer` abstraction: a durable, per-(user,bot) compute context (container/VM) with persistent browser session, filesystem, and credential vault, surviving across runs.
- Scheduler/autonomous mode already exists; extend it to dispatch into a *persistent* computer rather than a fresh sandbox.
- **Shared state plane**: bots under one account mount the same workspace + credential store, so a handoff (`bots/handoff.py`) needs zero re-config.
- "Continue while laptop closed": background workers + the existing supervisor loops (`autonomy/supervisor.py`) drive execution; results land in the real target tool, not just chat.

### P1 — High-value, build after P0

#### P1-1 · Routine / Skill capture from demonstration
Observe a user (or the lead agent) performing a multi-step, cross-system task once, record the path as a `Routine`/`Skill`, and allow scheduled/on-demand replay. Reuse `skills/` projection + `propose_skill` but add a *recorder* that captures tool-call sequences into a parameterized skill.

#### P1-2 · Connector marketplace + first-party connectors
A curated catalog UX (Gmail, Google Calendar, Outlook, OneDrive, X, plus a Composio-style 1000+ app bridge) with one-click install, analogous to how `extensions_config.json` + `extensions/` already manage MCP/integrations. Add a marketplace surface + connector health/rate-limit metadata.

#### P1-3 · Task Arena (parallel candidate generation + best-of-N)
Extend `benchmarks/arena.py` beyond SWE-Bench calibration into a *per-task* arena: spawn N agents on the same task, score outputs against `acceptance_criteria`/verification receipts, and auto-select/merge the best. Feeds the existing Task Auction Matchmaker reputation scores.

### P2 — Polish / narrowing gaps

#### P2-1 · Tighten action-level approval UX
Wire every dangerous `UAP` primitive (`SEND`, `DELETE`, `EXECUTE`, payments, prod changes) through `ApprovalQueue` with a pause-and-confirm UI + resumption token, so the agent visibly stops at judgment points (Grok Bot behavior). Much of the plumbing exists; this is mostly surfacing + endpoint work.

#### P2-2 · Chrome-profile import + egress policy
Let a bot import an authenticated Chrome profile and route egress through a residential/whitelisted proxy to operate sites that block datacenter IPs. Builds on `browser/cdp_bridge.py`.

#### P2-3 · Capability cascade / adaptive activation
Formalize Fast→Expert→Full-council tiers (fold into P0-1) so overhead scales with task difficulty.

---

## 6. Recommended build sequence

1. **P0-1 Reasoning Council** — highest leverage, reuses existing `subagents` + `report_contract`; no new infra.
2. **P0-2 Agent Computer** — largest infra effort; start with a persistent workspace + background worker, then shared cross-bot state.
3. **P1-1 Routine capture** — depends on `skills/` + a recorder.
4. **P1-2 Connector marketplace** — depends on `extensions` + a catalog UI.
5. **P1-3 Task Arena** — depends on `benchmarks/arena.py` + verification receipts.
6. **P2-1/2/3** — tightening once the core is live.

---

## 7. Sources

- Grok Bot launch (7×24 autonomous agent team, chief-bot + specialized bots): https://aiproducthub.cn/newsflash/xai-grok-bot-launches-24-7-agent/
- Grok Bot product detail (persistent cloud VM, app-level operation, routine capture, connectors, approval): https://gongke.net/tools/grok-bot
- Grok 4.20 four-agent reasoning council (Captain/Harper/Benjamin/Lucas): https://www.nextbigfuture.com/2026/02/how-the-xai-grok-4-20-agents-work.html
- Grok Build Parallel Agents (≤8) + Arena Mode: https://grokai.org/xai-grok-build-parallel-agents-arena-mode/
- Grok 4 model overview: https://awesomeagents.ai/models/grok-4/

## 8. Alpha files referenced

- `backend/packages/harness/alpha/bots/dynamic_profiles.py` (autonomous profile creation + governance)
- `backend/packages/harness/alpha/bots/handoff.py`, `bots/reassignment.py` (task handoff/escalation/succession/reassignment)
- `backend/packages/harness/alpha/action/uap.py` (Universal Action Protocol primitives)
- `backend/packages/harness/alpha/projects/approval_queue.py` (human-in-the-loop)
- `backend/packages/harness/alpha/enterprise/council.py` (release-governance council — *not* task reasoning)
- `backend/packages/harness/alpha/benchmarks/arena.py` (SWE-Bench eval + leaderboard → matchmaker)
- `backend/packages/harness/alpha/subagents/AGENTS.md` (delegation, guardrails, report contract)
- `backend/docs/WORKFORCE.md` (Bots/Teams/Projects workforce layer)
- `backend/packages/harness/alpha/browser/cdp_bridge.py`, `community/browser_automation/` (browser automation)
