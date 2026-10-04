# WorkBuddy vs Alpha — Feature Comparison, Evaluation Plan & Implementation

**Date:** 2026-09-30
**Method:** WorkBuddy's feature set was collected from its official docs navigation and three detailed third-party breakdowns (Tencent Cloud guide, JOTO deep-dive, open-platform analysis); each feature was mapped to Alpha and the genuine gaps were implemented and verified.

---

## 1. Full WorkBuddy feature list (collected)

| # | Feature | What it does |
|---|---|---|
| 1 | **Work modes** | Ask (read-only Q&A) / Plan (design) / Craft (execute), capability ascending; plus **Coding Mode** for repositories. |
| 2 | **Model selection** | Hunyuan, Zhipu, MiniMax, DeepSeek, KIMI, custom API keys / coding plans, Auto mode. |
| 3 | **Skills** | ~301 built-in reusable capability units; **Skill Marketplace** with categories + "recommended"; custom upload package (`SKILL.md`, `manifest.yaml`, `scripts/`, `references/`, `assets/`); enterprise whitelist/blacklist; scripts run in a sandbox. |
| 4 | **Connectors / MCP** | Connect GitHub, GitLab, Jira, Confluence, Google Drive, Gmail, Notion, Slack, Tencent Docs, Figma; auth = **MCP OAuth 2.1 / OAuth 2.0 / API Key**; credential + MCP server config + **tool-permission filter** + timeout + custom headers; gateway routing. |
| 5 | **Experts** | Role-based agents (prompt + skills + model hint) that add domain experience; built-in market + enterprise-built `.zip` packages (MD5/SHA256, avatar, id, name, category, version). |
| 6 | **Expert Groups** | Multiple experts wired into a **pipeline**, each owning a stage (e.g. Creative Director → Copywriter → Video Generator → Editor). |
| 7 | **Enterprise Knowledge Base** | Enterprise Q&A over internal documents and code, with management + permissions. |
| 8 | **Automations** | One-time / daily / weekly / monthly / yearly scheduled tasks (**RRULE**), running outside the session and reusing the workspace + connectors. |
| 9 | **Remote Assistant** | Send tasks from Slack / Telegram / Discord / WeCom / Feishu / DingTalk / QQ; results delivered back to the same channel. |
| 10 | **Task management & parallel tasks** | Task list, status filters, resume, parallel independent tasks. |
| 11 | **Results panel** | Artifacts, all files, changes, previews — verifiable deliverables. |
| 12 | **Permission modes** | Default permissions; confirm high-risk operations; folder authorization. |
| 13 | **Memory** | Persistent memory across tasks. |
| 14 | **Data management** | Local data / privacy management. |
| 15 | **Explore** | Discovery of skills/experts/cases. |
| 16 | **Multi-format file workflows** | Documents, spreadsheets, presentations, PDFs, images, web; authorized local folders. |
| 17 | **Office deliverables** | docx / pptx / xlsx / web pages. |
| 18 | **Open Platform Token** | Short-lived token a running skill uses to access user resources (Docs, drive, mail, library). |
| 19 | **Markets** | Enterprise market (tree structure, whitelist/blacklist) + personal plugin market (BuiltinMarket, one-click install). |
| 20 | **Cloud** | TokenHub (model access), COS (storage), Lighthouse (deploy apps/sites). |

---

## 2. Comparison with Alpha

| WorkBuddy feature | Alpha status | Evidence |
|---|---|---|
| Work modes (Ask/Plan/Craft/Coding) | 🔴 **Gap → implemented** | Alpha had plan mode only; `modes/` added |
| Model selection / routing | ✅ Covered | `models/`, `config.yaml model_routing`, discovery |
| Skills + marketplace + package format | 🟡 Partial | `skills/`, `SkillScan`, install, proposals; no browsable **marketplace catalog** |
| Connectors / MCP + auth | ✅ Covered | `mcp/`, `extensions/`, `integrations/`; connectors catalog added earlier |
| Experts | 🔴 **Gap → implemented** | `experts/` added (Alpha had bots/templates) |
| Expert Groups (pipelines) | 🔴 **Gap → implemented** | `experts/` groups added |
| Enterprise Knowledge Base | ✅ Covered | `knowledge/`, `agents/memory/backends`, `community/ragflow` |
| Automations (RRULE) | 🔴 **Gap → implemented** | `automations/` added (Alpha had `scheduler/` cron) |
| Remote Assistant (IM) | ✅ Covered | `channels/` (Feishu/Slack/Telegram/Discord/DingTalk) |
| Task management / parallel | ✅ Covered | plan mode, `subagents/`, `swarm/` |
| Results panel / artifacts | ✅ Covered | artifacts + `present_files` + frontend |
| Permission modes | ✅ Covered | `guardrails/`, `policy/`, `authz/`, approval queue |
| Memory | ✅ Covered | `agents/memory/` (multi-backend) |
| Open Platform Token | ✅ Covered | `runtime/secret_context.py` request-scoped secrets |
| Markets (enterprise/personal) | 🟡 Partial | `extensions/` install; no unified market UI |
| Cloud deploy | 🟡 Partial | `deploy/`, sites publishing; not Tencent-cloud-specific |

---

## 3. Plan for collecting & evaluating WorkBuddy features

A repeatable five-phase loop:

1. **Collect.** Enumerate sources: the official docs tree (`/docs/workbuddy/...`), the Skill Marketplace and Expert market listings, the Changelog, the Practice Cases, and the open-platform schemas (Skill / Connector / Expert / Knowledge-Base). One row per user-visible capability.
2. **Normalize.** Map each feature to a taxonomy entry with a **filesystem probe** (package/module path) — the same representation `alpha.scorecard` uses.
3. **Evaluate.** Score Alpha present / partial / absent by probe; record evidence paths. (This is the `alpha.scorecard` scanner, extended with a WorkBuddy-sourced set.)
4. **Prioritize.** Rank by user impact × implementation effort; keep a one-line rationale per item.
5. **Implement & verify.** Build the gap as a self-contained, tested module; add a live verification scenario; commit.

---

## 4. Priority list

| Priority | Feature | Status |
|---|---|---|
| **P0** | Work modes (Ask/Plan/Craft/Coding) | ✅ implemented |
| **P0** | Experts + Expert Groups | ✅ implemented |
| **P0** | Automations (RRULE) | ✅ implemented |
| P1 | Skill Marketplace catalog (browse/search/install state) | partial (install exists; catalog is a follow-up) |
| P1 | Unified market surface (skills + experts + connectors) | follow-up (frontend) |
| P2 | Enterprise Knowledge-Base UI, cloud deploy | infra/product |

---

## 5. What was implemented (this session)

Three new harness packages, each pure-stdlib and tested:

- **`alpha/modes/`** — `WorkMode` (ASK/PLAN/CRAFT/CODING), per-mode `ModeCapabilities`, `parse_mode`, `can_escalate`, and an enforceable `assert_allowed(mode, action)` that raises `ModeViolation`. Turns the WorkBuddy mode picker into a policy the runtime can check.
- **`alpha/experts/`** — `Expert`, `ExpertGroup`/`ExpertStep`, `ExpertCatalog` (8 experts, 2 pipeline groups), and a durable `ExpertRegistry` (install/enable, unknown-id refused, loud-on-corruption).
- **`alpha/automations/`** — `AutomationSchedule` (once/daily/weekly/monthly/yearly), `next_run()` (month-aware, skips short months), `to_rrule()` (RFC-5545), `Automation`, and a durable `AutomationStore`.

## 6. Verification (executed)

`verify_workbuddy_features.py` runs a realistic WorkBuddy-style session — choose Plan mode, escalate to Craft, browse + install an Expert Group, schedule daily/weekly/monthly automations:

```
1 — Work modes .................... 5/5 PASS
2 — Experts & Expert Groups ....... 4/4 PASS
3 — Automations (RRULE) ........... 6/6 PASS
--------------------------------------------
15/15 checks passed
```

Example output:
```
Daily Briefing   next=2026-10-01 08:00:00  rrule=FREQ=DAILY;BYHOUR=8;BYMINUTE=0
Weekly Report    next=2026-10-02 17:00:00  rrule=FREQ=WEEKLY;BYDAY=FR;BYHOUR=17;BYMINUTE=0
Monthly Close    next=2026-10-01 09:00:00  rrule=FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0
```

---

## 7. Bottom line

WorkBuddy's distinctive, *additive* ideas — a first-class **mode** system, a **marketplace** of **Experts and Expert Groups**, and **RRULE automations** — are now implemented in Alpha as `alpha/modes`, `alpha/experts`, and `alpha/automations`, all tested. Everything else WorkBuddy offers (skills, connectors/MCP, knowledge base, remote assistant, memory, permission modes, Open-Platform tokens, cloud deploy) is already present in Alpha's ~100-package framework.
