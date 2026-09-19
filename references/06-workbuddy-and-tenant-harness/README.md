# 06. WorkBuddy & Tencent Agent Harness — Ideas, Inspiration & Borrowed Design

This directory captures **product-level ideas and design inspiration** taken from
**WorkBuddy AI** / **Tencent CodeBuddy (AICoding / "Code Buddy")** and adjacent
tenant-grade agent harnesses, translated into concrete proposals for Alpha.

It is **not** a competitive teardown and **not** a claim of feature parity. It is a
working notebook: *what the product does, why it feels good, what Alpha already has,
and what Alpha should build as a result.*

> Scope note: Alpha already covers a very large surface (89 harness engines,
> 100+ built-in tools, 55 gateway routers). Everything in this directory is filtered
> through one question: **what does WorkBuddy do that Alpha still does not, or does
> worse?** Anything already fully covered is marked as such and not re-proposed.

---

## Document Catalog

| # | Document | Focus |
| :- | :--- | :--- |
| 1 | [WorkBuddy & Tenant Harness Analysis](./workbuddy-tenant-harness-analysis.md) | What WorkBuddy / CodeBuddy is, its architecture and feature surface, and the mental model behind it |
| 2 | [Borrowed Ideas & Alpha Gap Map](./borrowed-ideas-and-gap-map.md) | Feature-by-feature comparison: WorkBuddy capability → Alpha status → concrete proposal |
| 3 | [Inspiration Notes — Product Craft & Feel](./inspiration-notes-product-craft.md) | The non-obvious lessons: how it *behaves*, how it earns trust, how it fails gracefully |

---

## Why WorkBuddy Is a Useful Reference

Most of Alpha's research library studies **coding harnesses** (Claude Code, Devin,
OpenHands, Aider, Cursor, OpenClaw, Goose) or **research swarms** (AVO, RSI,
Hermes, MetaGPT). WorkBuddy sits in a different and less-studied category:

**A bundled, consumer-grade, desktop-first agent product that ships a full
capability stack as a single install.**

That combination produces design constraints Alpha has not yet been forced to
solve:

| Constraint WorkBuddy lives with | Why it matters for Alpha |
| :--- | :--- |
| Ships to non-technical users | Error messages, empty states, and defaults carry the product — not the docs |
| Runs on the user's own machine | Filesystem safety, permission prompts, and "don't touch my stuff" boundaries are first-class |
| Bundled runtime, no setup wizard | Deterministic startup, health gates, and self-repair matter more than feature count |
| Session-scoped, project-scoped memory | Memory is a *trust* feature, not a *recall* feature — users must see and edit it |
| Skills + connectors as the extension story | Extension discovery, install-time security audit, and marketplace UX |
| Cloud-account bound | Identity, entitlements, and per-user isolation are architectural, not bolted on |

Alpha is currently strongest at the *autonomy* end (swarm orchestration, RSI, deep
research, councils) and comparatively thin at the *product trust* end (what a user
sees, learns, and controls). These documents argue that the trust end is where the
highest-leverage remaining work lives.

---

## The One-Sentence Thesis

> Alpha has built an autonomous agent **operating system**.
> WorkBuddy has built an agent **product**.
> The next tier for Alpha is not more engines — it is making the engines
> *legible, steerable, and safe* to a human who did not build them.

---

## How to Use These Documents

1. Read the **analysis** for the mental model.
2. Read the **gap map** to pick work: each row ends in a proposal with a
   suggested engine name, integration point, and difficulty.
3. Read the **inspiration notes** before designing UI or UX-facing surfaces —
   most of the value there is in tone, defaults, and failure behaviour.

Proposals are tagged `[TIER-1]` (high leverage, low risk), `[TIER-2]`
(high leverage, architectural), and `[TIER-3]` (strategic / speculative).
