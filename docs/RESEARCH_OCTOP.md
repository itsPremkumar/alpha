# Octop (TencentCloud) — research report

**Target.** `https://github.com/TencentCloud/Octop` — *"A smarter, self-hosted AI
assistant — multi-user, multi-agent."* Researched **2026-09-29** for an Alpha
comparison. Written by a research subagent in an isolated worktree; no Alpha
product code was touched, nothing committed.

> **Correction on the record.** An earlier pass on this report researched a
> *different* project sharing the near-identical name "Octo"
> (`Mininglamp-OSS`, Apache-2.0 Go/React enterprise-IM platform), selected by
> name inference from a search result rather than from a canonical URL. It is
> **not** the subject of this report. Its analysis was discarded and overwritten,
> not merged.

**Method.** GitHub figures pulled live from `api.github.com` on 2026-09-29. The
source tree was enumerated in full (`git/trees/main?recursive=1`, **4,270 entries,
not truncated**), so absence claims in §5 are made against a complete inventory
rather than a sample. Every claim carries a source. Anything resting only on a
README or roadmap is marked **UNVERIFIED**; the roadmap's own disclaimer is
quoted verbatim in §5.1.

**One stated limitation.** GitHub's code-search API returned HTTP 401 without
credentials, so absence claims are based on the complete *file-path* inventory
plus files read in full. A function named `redact` inside an unrelated filename
would not have been found. Each such claim says so.

---

## 1. Canonical identity

`https://api.github.com/repos/TencentCloud/Octop`, retrieved 2026-09-29:

| Field | Value |
|---|---|
| Full name | `TencentCloud/Octop` |
| Owner | TencentCloud (Tencent's cloud arm) |
| Description | A smarter, self-hosted AI assistant - multi-user, multi-agent. |
| Stars | **5,688** |
| Forks | **707** |
| Watchers (subscribers) | **50** |
| Open issues + PRs | **588** (brief's 326 issues + 262 PRs reconciles to this) |
| Licence | **MIT** |
| Language | Python |
| Created | **2026-07-08** |
| Last push | 2026-09-29 13:13 UTC |
| Size | 28,351 KB |
| Homepage | `https://octop.cloud` |
| Topics | `agent`, `agentic-ai`, `ai`, `ai-agent`, `ai-agents`, `local-first`, `long-term-memory` |
| Wiki / Discussions / Pages | all enabled |
| Latest release | `1.0.2b5` (2026-09-29) — README badge reads `1.0.2b5`; stable `1.0.0` shipped 2026-09-14 |

**Correction to the brief:** the version is `1.0.2b5`, not `1.0.2` — still a
pre-release. `1.0.0` has been stable for ~2 weeks.

### 1.1 Name disambiguation

`Octop` is **not** `Octo` (Mininglamp-OSS) and **not** the ByteDance/UC-Berkeley
"Octo" transformer policy. Three unrelated projects; the extra `p` is the only
difference and it is load-bearing, because a search for "Octo" does not reliably
surface this repository. MIT vs Apache-2.0 vs BSD; Python vs Go/TypeScript;
self-hosted personal assistant vs enterprise IM vs robot RL. **This report is
about `TencentCloud/Octop` only.**

### 1.2 The fact that should weight every comparison: it is 12 weeks old

Created 2026-07-08; today 2026-09-29. In ~12 weeks: 5,688 stars, 707 forks, 588
open issues+PRs, and 1.0.0 stable since 2026-09-14.

- **Against:** 12 weeks is not enough time to harden a self-hosted system.
  262 open PRs against 636 commits is a heavy review queue. The 707 forks are
  overwhelmingly evaluation forks, not contributors — only **50 watchers**
  suggests shallow deep engagement relative to the star count.
- **For:** MIT is maximally permissive, the object model is directly liftable,
  and the engineering discipline visible in `AGENTS.md` and the two ADRs is
  genuinely high for the project's age.

**Treat Octop as a fast, well-resourced, young project — not a mature one.**

---

## 2. Sources read (all 2026-09-29)

| # | Source | What it settled |
|---|--------|-----------------|
| S1 | `README.md` (28.5 KB) | Feature table, roadmap, install, data layout, security claims |
| S2 | `AGENTS.md` (30.5 KB) | Module boundaries, hard bans, conventions, branching, "Where to look" |
| S3 | `docs/architecture.md` | Layering, boot sequence, per-user isolation, storage, i18n |
| S4 | `docs/adr/001-single-process-model.md` | Single-process decision and its explicit trade-off table |
| S5 | `docs/adr/002-database-backends.md` | Three storage layers, dual-driver rules, PG hard limits |
| S6 | `docs/expert-teams.md` (Chinese) | **AgentTeams coordinator design** — 23 confirmed decisions |
| S7 | `docs/acp.md` | Bidirectional ACP, runners, permission gates, routes |
| S8 | `docs/personas.md` | The 16 MBTI codes, their real data shape, and their real ceiling |
| S9 | `docs/configuration.md` | `config.json` schema, all env overrides, wizard flow, secrets |
| S10 | `docs/memory-slim.md` (Chinese) | Live SQLite memory maintenance and its fail-closed edges |
| S11 | `CHANGELOG.md` (45 KB, 740 lines) | Release cadence, `1.0.0` date, `schema v18`, `--workers N` |
| S12 | `api/routers/security.py` (read in full) | The five security policy sections and the tool-guard CRUD surface |
| S13 | 19 × `infra/db/migrations/NNN_*.sql` | Real tables: `published_experts`, `knowledge_bases`, `skill_packages`, `agents.kind` |
| S14 | `git/trees/main?recursive=1` | Complete 4,270-entry file inventory — basis for all absence claims |

---

## 3. AgentTeams — the coordinator design, in my own words

`docs/expert-teams.md` (S6) is written in Chinese and is a **decision log, not a
spec** — 23 numbered "confirmed decisions" plus a table of "explicit non-goals
for v1". It is the highest-quality design document in the repository and the most
interesting thing here.

### 3.1 A team is not a new entity. It is an agent with `kind=team`.

This is the central design choice and it is the most transferable idea in the
whole project.

Migration `016_agent_teams.sql` is the entire schema change:

```sql
-- Schema v16: team hosts are ordinary agents with kind=team.
-- Roster lives in the host workspace `.octop/manifest.json`, not a member table.
ALTER TABLE agents ADD COLUMN kind TEXT NOT NULL DEFAULT 'expert';
```

Two lines. **There is no teams table and no team_members table.** A team is an
ordinary agent row with a discriminator, and its roster is a JSON file inside its
own workspace:

```json
{ "kind": "team", "members": ["expert-a", "expert-b"] }
```

`team_id` **is** the coordinator's `agent_id`. `GET /api/teams` and `GET /api/agents`
both read `member_ids` out of that one manifest file. The consequence is
architectural: creating a team cannot fail a transaction, cannot orphan a
membership row, cannot need a migration, and is editable with the same file tools
an agent already has. The cost is that you cannot answer "which teams is
`expert-a` in?" with an indexed query — only by scanning manifests.

### 3.2 The coordinator is deliberately hobbled

`_build_harness_config` in `kind=team` does the following (S6):

- attaches **no** cron, knowledge, mobile, plugin, MCP, or skill packs
- boots with `init_workspace=False` and does **not** seed harness `_builtin_skills`
  or built-in subagents
- `tools_disabled` retains only `agent_list`, `ask_agent`, memory, and
  `current_time`
- `team_peers` is narrowed to exactly the roster
- seeds three files into its workspace: `SOUL.md` (persona), `AGENTS.md`
  (coordination rules, a real 2,117-byte committed template), and
  `.octop/manifest.json`

The coordinator **cannot touch the filesystem, browser, search, MCP, skills, or
plugins.** It is a scheduler with no hands. Decision 23 states this as a rule;
the code enforces it by subtraction.

### 3.3 The genuinely novel bit: the coordinator must rewrite the task

This is the idea I would steal first. Decisions 9, 11, 12 and the runtime section:

- The user always talks to the coordinator. A new user message **always** goes to
  the coordinator first, even mid-member-response (decision 12).
- `@mention` is a **hint only** — it does not force dispatch to the named member
  (decision 10). The coordinator decides.
- When the coordinator dispatches, the member receives **the coordinator's
  rewritten task specification, not the user's raw words** (S6 runtime section).
  The group-chat transcript is passed only as *System* background context.
- The coordinator's system prompt carries a runtime constraint to "digest first,
  then rewrite", and **forbids forwarding verbatim**.
- A dispatched member is forcibly downgraded: request-level
  `peer_invoke_mode=sync` and `team_peers` narrowed to colleagues, with pulling
  more people into the group forbidden (decision 13).
- When members finish, they report **back to the coordinator**, which summarises
  and judges whether the work is done. The callback prompt explicitly forbids
  restating member text already posted to the wall (S6).

So the model that plans is not the model that executes, and there is a mandatory
**context-transformation step** between them. That is a real answer to the problem
that a raw subagent-dispatch architecture has: the executor sees the same context
the planner saw, which means every executor independently re-reasons the intent.
Octop instead makes the coordinator the sole interpreter of user intent.

### 3.4 Fan-out is parallel, fan-in is serial, and the room is one thread

- Room id **= the coordinator's `thread_id`** (S6 decision 17).
- Each member's LangGraph checkpoint is a derived id `{room}~{member_id}`.
  Checkpoints stay isolated per expert; the design explicitly refuses to let
  multiple experts share one `threads.thread_id` (non-goal).
- Member output is relayed through the gateway with a `speaker_agent_id` tag on
  the fan-in message, so the room transcript has real per-speaker attribution.
- Concurrency: **serialised per callee, parallel across callees**; the
  coordinator's callbacks serialised per source `thread_id`. The coordinator may
  dispatch to several members in one round (decision 14).
- The WS room stream relays member tokens live; a `snapshot` is only fanned in
  when live push did not happen.
- Stop semantics: cancelling from the dashboard **only cancels the coordinator's
  current stream**; queued and running member jobs keep going (decision 16).
- The room is invisible to IM. On successful dispatch, IM gets "Requested
  {member} to handle it, please wait..." immediately, the full member message
  after, and "[Moderator summary]..." for
  the coordinator. Team coordinators are forced to `response_mode=stream` at
  channel registration so the IM adapter cannot collapse away the dispatch
  narration.

### 3.5 The honest weak spot

**In-flight dispatch state is in-process and is lost on restart.** The tracker is
`TeamJobTracker` in `src/octop/infra/agents/teams/jobs.py` — **2,553 bytes** — and
the non-goals list says so outright: "in-process inbox persistence is out of
scope; restart loses in-flight tasks".

It is idempotent by inbox `job_id` and releases on either the `record_peer_turn`
or `on_reply` path **including on failure** (S6) — that part is careful. But a
restart mid-team-run silently loses the roster's outstanding work, and a member
that was mid-flight never calls back. The ~63 KB `team_manager.py` carries the
coordination logic; the durability story is 2.5 KB and deliberately absent.

### 3.6 Full API surface

| Method | Path | Notes |
|---|---|---|
| GET | `/api/teams` | current user's teams with member summaries |
| POST | `/api/teams` | creates coordinator + **≥2 members** |
| GET | `/api/teams/{team_id}` | detail |
| PATCH | `/api/teams/{team_id}` | name/model/welcome/members — **rejects changing a member with work in flight** |
| DELETE | `/api/teams/{team_id}` | deletes the coordinator |

Error codes: `TEAM_NOT_FOUND`, `TEAM_MEMBERS_TOO_FEW`, `TEAM_MEMBER_INVALID`,
`TEAM_MEMBER_BUSY`, `TEAM_MEMBER_NOT_SHAREABLE`. `kind=team` cannot be
`is_shared`. Exposed in the dashboard as four tabs: "My experts | My teams |
Expert library | Market".

---

## 4. ACP — bidirectional, with real permission gates

`docs/acp.md` (S7). The brief asked whether this is A2A; the answer is
**no — it is [Agent Client Protocol](https://agentclientprotocol.com/), a
different spec entirely, and it is a client/server protocol for coding agents,
not an agent-to-agent collaboration protocol.**

### 4.1 Two directions, both stdio JSON-RPC

| Direction | Octop is | Other side | Use |
|---|---|---|---|
| **Inbound** | ACP **server** (`octop acp`) | Zed, OpenCode | an IDE drives *your* Octop expert as its coding agent |
| **Outbound** | ACP **client** | OpenCode, CodeBuddy, Claude Code, Codex… | your expert delegates coding work out |

**Both are stdio. `docs/acp.md` states plainly: "Octop does not expose an HTTP
ACP endpoint."** This is stated as a current limitation, not a design choice.
Consequence: an Octop expert cannot be an ACP server reachable by a remote
machine, and an Octop-hosted service cannot be driven over the network by ACP.

### 4.2 Inbound: `octop acp --agent main`

Starts a **standalone `OctopServer`** that reads `~/.octop` and speaks ACP on
stdin/stdout. It does **not** require `octop run` to be alive — it is a separate
process. Zed config:

```json
{ "agent_servers": { "Octop": {
    "command": "octop", "args": ["acp", "--agent", "main"], "env": {} } } }
```

Sessions map to `thread_id`; the workspace stays at `~/.octop/agents/<agent_id>/`.

### 4.3 Outbound: runners and the `acp_runner` tool

**Scope split (S7) — this is the part worth copying:**

| Setting | Scope | Stored in |
|---|---|---|
| Runner cards (command, args, enabled…) | **per user**, shared by all agents | `settings` key `acp_runners:user:{id}` |
| `acp_runner` enable toggle | **per agent** | agent `config_json.acp.tool_enabled` |

So a user configures their runners once, then opts each individual expert into
delegation. Legacy per-agent runner config is migrated to the user-global store
on first load. Dashboard page: `/acp`.

Seven built-in runners, all **stdio subprocesses**:

| id | command | args |
|---|---|---|
| `opencode` | `opencode` | `acp` |
| `codebuddy` | `codebuddy` | `--acp` |
| `claude_code` | `npx` | `-y @zed-industries/claude-agent-acp` |
| `codex` | `npx` | `-y @zed-industries/codex-acp` |
| `kimi_code` | `kimi` | `acp` |
| `cursor_cli` | `agent` | `acp` |
| `pi` | `npx` | `-y pi-acp` |

Runner shape: `{ enabled, command, args, env, trusted, tool_parse_mode, stdio_buffer_limit_bytes }`.

`acp_runner` actions: `list | start | message | respond | status | close`.

### 4.4 The permission gates — concretely

Three distinct gates, and it matters that they are three and not one:

1. **Per-agent opt-in.** `config_json.acp.tool_enabled` must be on for that agent;
   otherwise the tool does not exist in its toolset. This is a *capability*
   boundary, not a prompt.
2. **The runner declares `trusted`.** A field on the runner object itself. A
   runner the user has not trusted is a different object than one they have.
   *(Exact enforcement semantics of `trusted` are not spelled out in `docs/acp.md`
   — **UNVERIFIED**, though the field is real in the API contract.)*
3. **The delegate's own permission prompts surface in Octop chat.** The external
   agent's `[permission_required]` events are relayed into the Octop
   conversation, and Octop answers them with `action=respond` plus the **exact
   option id**. So a subprocess asking "may I delete this file?" is answered by
   a human in the Octop UI, and the answer is a specific option rather than free
   text.

**Honest read:** the third gate is the real one, and it is genuinely good —
delegation does not silently inherit Octop's own tool-approval policy, it routes
the child's approval requests back to the human through Octop's UI. But note
this is *Octop rendering someone else's permission model*; it is not a
capability-scoped permission system of Octop's own. The `tool_guard` YAML rules
(S12) and the HITL tool list are Octop's own and apply to Octop's own tools.

Also worth knowing: `trusted: true` appears in the documented runner example, and
the troubleshooting table admits real failure modes — the ACP service cache is
process-wide, so **a newly added custom runner requires an `octop run` restart**
to become visible. That is a genuinely user-hostile rough edge.

---

## 5. Persistence, deployment, privacy, and what is actually shipped

### 5.1 The single-process decision (ADR 001) and exactly what it costs

ADR 001 is **Accepted, 2026-06**. Decision: everything in one Python process
under uvicorn; no Redis/RabbitMQ/Celery, no separate worker, no required backing
service beyond the LLM provider.

Its stated rationale is honest and, for the audience, right: `pip install octop &&
octop init` is a real advantage over "also run Redis".

**The ADR publishes its own cost table, which is more useful than any review
would be:**

| Benefit | Cost |
|---|---|
| Zero external dependencies | **Vertical scaling only (one machine)** |
| Simple deployment (one process, one port) | **Heavy CPU tasks block the event loop** |
| Fast local dev | **No horizontal worker scaling** |

Consequences it commits to: all async with `run_in_executor` for blocking work;
`SharedServices` (the DI container) is process-global.

**What this actually means, beyond the ADR:**

- **There is no queue anywhere in the system.** Every surface — web, IM, cron —
  is routed through one in-process `HarnessProcessor`. If that process is busy,
  everything waits. For a household/small-team single-user product that is a
  reasonable trade; it is a hard ceiling for anything else.
- **One machine, one user population.** Not horizontally scalable at all.
- **The blocker is CPU, not I/O.** The ADR assumes LLM calls dominate, which is
  true for chat — but Octop *also* ships an in-browser terminal, headless
  Chromium browser automation, remote desktop, and a local ONNX embedding model
  (the `local-embedding` extra). All of those are CPU-heavy and run in the same
  event loop. The "we are I/O-bound" premise is weakest exactly where the
  differentiating features are.
- **Nuance:** `CHANGELOG.md` 1.0.2b1 mentions `octop run --workers N`, i.e.
  uvicorn worker processes. That is a *different* thing from horizontal scaling —
  N workers against one SQLite file is what the changelog says it fixed
  (`database is locked`). Multiple workers against one process-global
  `SharedServices` is in tension with ADR 001's own reasoning.

**Verdict: a defensible, well-argued decision for the stated audience, taken by
someone who wrote down what it costs. Alpha's own service topology (nginx →
gateway + frontend, with a separate provisioner) is a multi-machine model; this
is a single-box model. They are not comparable on scale, and should not be.**

### 5.2 Storage: three layers, explicitly not to be conflated (ADR 002)

Accepted 2026-07-20. This is unusually well-specified.

| Layer | What | Default | Override |
|---|---|---|---|
| 1 — control plane | users, agents, providers, channels, cron, settings, secrets | SQLite `~/.octop/octop.db` (WAL) | PostgreSQL via `config.json` / `OCTOP_DATABASE_*` / wizard |
| 2 — harness checkpoints | LangGraph state | `{workspace}/checkpoints.sqlite` | `PostgresSaver` when memory is PG |
| 3 — agent memory (octop-memory) | long-term memory | `{workspace}/memory.sqlite` | same DSN, schema `agent_<id>`, or `config_json.memory.backend` |

Workspace *content* files (SOUL, skills, JSONL) are not in any database — they
go through `BackendWorkspace`, which is what makes the pluggable backend story
work. Default `filesystem` rooted at `~/.octop/agents/<agent_id>/`; S3/COS
mounted over the same root. Migrations come in **paired files**
`NNN_x.sql` + `NNN_x.pg.sql`; 19 pairs exist, latest `019_bridge_connections`.

Hard limits stated by the ADR and `docs/configuration.md`:
- **"Single active Octop writer; no multi-instance write promise."** PostgreSQL is
  for compliance/externalised state, **not** for HA or multi-node.
- **"Greenfield only — no SQLite→PG data migrator."** Switching to PG means
  starting over. Same for memory: "There is no automatic SQLite→PG memory data
  migration."
- Control-plane `*.pg.sql` **must not** run `CREATE EXTENSION`; `vector` stays in
  docker/ops. octop-memory uses built-in `tsvector`, not pgvector.
- Live PG tests are gated behind `@pytest.mark.postgresql` + `OCTOP_TEST_DATABASE_URL`.

### 5.3 Security, verified against code rather than the README

`api/routers/security.py` (S12, read in full) is the honest source. The global
`SecurityPolicy` has **five** sections, all exposed at `GET/PUT /api/security`
behind `require_permission("security")`:

| Section | What it is | Enforcement location |
|---|---|---|
| `hitl` | tools requiring human approval | `DEFAULT_HITL_TOOLS` from `octop_harness.security.models`; session state in `infra/agents/security/hitl_session.py`; resume at `POST /api/agents/{aid}/chat/hitl/resume` |
| `filesystem` | workspace path policy | harness |
| **`pii`** | **PII redaction policy** | **implementation is in `octop_harness.security`, not this repo** |
| `skill_scan` | skill content scanning | harness |
| `tool_guard` | shell command guardrails | **Octop-side, fully visible** |

**Important correction to my own reading, stated plainly:** the README's "PII
redaction" claim is *not* unfounded — there is a real `pii` policy block with a
full CRUD API. But the **enforcement code is in the sibling repo
`octop-harness`**, not in this repository. Octop stores and serves the policy; it
does not implement the redaction. Anyone evaluating the claim from this repo alone
cannot verify it.

The **tool guard is entirely Octop-side and is the best-engineered security
feature here.** User-editable YAML at `~/.octop/security/tool_guard/`, with a full
API: `GET /tool-guard/rules`, `GET|PUT /tool-guard/rules/raw`,
`POST /tool-guard/rules/reset`. Each rule carries `tools`, `params`, `category`,
`severity`, `remediation`, `patterns` **and `exclude_patterns`**. Every mutation
writes an audit record (`security.tool_guard_rules.update` / `.reset`, actor
`admin`), and the harness is reloaded afterwards. That is a real, auditable,
user-extensible control — and the `exclude_patterns` field is a detail most
projects in this space omit.

Auth and isolation:
- JWT (`~/.octop/secrets/jwt_secret`, 32 bytes), TTL 86400s default
- Login lockout: `login_max_attempts: 5`, `login_lockout_seconds: 900`
- Six captcha providers: `slider`, `turnstile`, `hcaptcha`, `recaptcha-v3`,
  `tencent`, `geetest-v4`
- Ownership enforced **at the row level** — `agents.user_id` matched against the
  caller, admin bypass allowed (S3). Not a separate per-user manager any more.
- A **named-permission** RBAC exists: `require_permission("security")`, plus
  migrations `006_user_permissions`, `014_user_policy`, `018_user_role`
- Audit log: `infra/db/repos/audit.py`
- **CLI has no authentication at all** — S2: *"No `octop user login` — CLI trusts
  local filesystem access to `~/.octop`."* That is a deliberate and defensible
  local-trust model, but it must be stated plainly in any comparison.
- TLS/ACME (Let's Encrypt, `acme_staging` flag) via `infra/setup/tls/`
- Weakness the project states itself: JWT rotation "invalidates every outstanding
  access token immediately… **no zero-downtime rotation today**."

### 5.4 Multi-tenancy

Multi-user, not multi-tenant. Users, agents, workspaces, cron, channels, and
**knowledge bases** are all scoped by `user_id`, and `knowledge_base_members` is a
join table (migration 005) so knowledge bases can be shared *within* one
deployment. There is **no organisation/tenant entity** and no cross-tenant
isolation story — ADR 002's "single active Octop writer" confirms a single
deployment is the unit of trust. For a household/small-team product this is
correct; for enterprise multi-tenancy it does not exist.

### 5.5 The shipped / Beta / planned / unverified split

The roadmap's own disclaimer, quoted verbatim (S1):

> "This roadmap may shift as the community grows; treat it as indicative only."

Evidence key: **T** = code artefact in the 4,270-entry tree; **M** = table in a
migration; **R** = route exists; **D** = documented design; **P** = README/roadmap
text only.

#### Shipped — verified in code

| Capability | What it actually is | Evidence |
|---|---|---|
| Single-process server | `OctopServer.start()` in `infra/server.py`, uvicorn from `launch.py`; web + CLI + IM + cron in one process | T, D, ADR 001 |
| SQLite control plane | `SqlitePool` (WAL), 19 paired migrations | T, M, ADR 002 |
| PostgreSQL control plane | `PostgresPool`, psycopg3, greenfield-only | T, ADR 002 |
| Pluggable workspace backends | `infra/backend/resolver.py`; filesystem / Docker sandbox / PostgreSQL / COS / S3 | T, R (`storage_backends.py`) |
| Multi-user JWT + row-level ownership | `api/deps.py`, `agents.user_id` check, admin bypass | T, D |
| Named-permission RBAC | `require_permission(...)`, migrations 006/014/018 | T, M, R |
| Tool approval (HITL) | `DEFAULT_HITL_TOOLS`, `hitl_session.py`, SSE resume route | T, R |
| Shell command guardrails | editable YAML + full CRUD + audit + `exclude_patterns` | T, R (S12) |
| PII redaction | policy block served by Octop; **enforcement in `octop-harness`** | R, partial T |
| Expert catalogue | `infra/agents/experts/catalog.py`, scanned at boot from `experts/library/` | T |
| **Expert sharing** | `published_experts` table (migration 005), `publish.py`, `published_creation.py` | T, M |
| **Expert market** | `market_creation.py` (46 KB `skillhub_market.py`), `ExpertMarketTab.tsx`, `expertMarket.ts` | T, R |
| SkillHub / skill packages | `skill_packages` table (migration 002), 15 KB + 46 KB market clients, `SkillHubTab.tsx` | T, M, R |
| 16 MBTI personas | `mbti_profiles.py` structured dataclasses, `agents.persona_mbti`, `/api/mbti/*` | T, R, D |
| AgentTeams | `016_agent_teams`, `infra/agents/teams/`, `api/routers/teams.py`, `team_manager.py` 63 KB | T, M, R, D |
| ACP bidirectional | `api/routers/acp.py`, 7 runners, `octop acp` inbound, `acp_runner` tool | T, R, D |
| Knowledge base (RAG) | `knowledge_bases` / `knowledge_documents` / `knowledge_base_members` (005), `knowledge_bases.py` | T, M, R |
| Plugins | `PluginManager.seed_bundled()`, `plugins/` with demo plugins, `api/routers/plugins.py` | T, R |
| Connectors + OAuth + MCP | `infra/connectors/`, `connectors.py`, `internal_mcp.py` | T, R |
| 7 IM channels | Feishu, DingTalk, QQ, WeChat, Telegram, Discord, WeCom + more via gateway | T, R |
| Cron (APScheduler) | `infra/cron/`, `cron.py`, `cron run-now` | T, R, changelog |
| Terminal in browser | `api/routers/terminal.py`, `dashboard/src/pages/Control/Terminal/` | T, R |
| Remote desktop | `dashboard/src/pages/Control/RemoteDesktop/`, `octop-desktop.yml` workflow | T |
| Live memory maintenance | `docs/memory-slim.md`, `memory_slim_control.py`, loopback control port | T, D |
| SSO / OIDC | `auth_oidc.py`, `sso_providers` (005), `sso_provider_kind` (015) | T, M, R |
| TLS / ACME | `infra/setup/tls/`, `tls.py` router | T, R |
| Desktop client | packaging + releases; **source not in this repo** | partial — see below |
| Usage / audit / observability | `usage.py`, `observability.py`, `audit.py` repo | T, R |
| Subagents | `infra/agents/subagents/`, `subagents.py` router | T, R |
| Segmented history archive | `infra/history/`, `docs/versioned-history.md` | T, D — but **off by default** |

#### Beta / in progress

| Capability | Status | Evidence |
|---|---|---|
| **AgentTeams** | **Beta** per README *and* "In progress" in the roadmap — the project labels it Beta in three places. Real code, real routes, but explicitly not finished. In-flight job state is lost on restart. | R, D, README |
| Mobile client | **closed beta**, "internal testing" | roadmap only |
| Browser skill **recording** | Roadmap says **Planned**, but frontend work exists: `useSkillRecordingWorkflow.ts` (7.7 KB) + `SkillRecordGuideModal.tsx` (4.5 KB). **No backend route found.** So: UI shell exists, backend does not. The roadmap understates. | T, partial |
| Terminal AI+ | Terminal shipped; "a more capable terminal AI assistant" is Planned. `useTerminalAutopilot.ts` + `AutopilotPlan.tsx` suggest partial autopilot work. | T, partial |
| Remote desktop | Shipped, but "one-click isolated desktop on headless Linux" is a qualified sub-claim I did not verify separately. | partial |

#### Planned — roadmap text only, no code

| Capability | Roadmap wording |
|---|---|
| Self-evolution | "automatically distill everyday conversations into reusable skills". **Zero files matching `evolut` in 4,270 entries.** Entirely aspirational — and this is the capability Alpha most directly has. |
| **Project** (project-scoped workspaces) | groups agents, files, conversations around a shared goal. **No `projects` table in 19 migrations.** Entirely aspirational. |
| Managed Agents | platform-hosted lifecycle |
| Cloud–edge continuum | offload selected tasks to the cloud |
| Plugin marketplace | there *is* a `PluginMarketPanel.tsx` and `skillhub_market.py` — the mechanism is shared with the expert/skill market, so this is more "generalise the existing market" than greenfield. |
| Conversational control plane | chat covers the full dashboard surface |

#### UNVERIFIED or contradicted

| Claim | Finding |
|---|---|
| **"16 MBTI persona templates plus an interactive quiz"** | The 16 templates are real and documented. **The quiz does not exist: zero files matching `quiz` in the complete 4,270-entry tree**, and `docs/personas.md` documents only `/api/mbti/codes`, `/api/mbti/codes/{code}`, `/api/mbti/preview/{code}` — a catalogue and a preview, not a quiz. **Readme claim with no code behind it.** |
| **"PC client — SHIPPED"** | Releases exist, but `desktop/` in this repo contains **86 entries of packaging scripts only** (`package-release.sh`, `bootstrap-runtime.sh`, `vendor-wheels.sh`) — no application source. The desktop app's source is not in this repository, so it cannot be reviewed here. |
| "16 MBTI personas" as a *capability* | Octop itself deflates this honestly: the rendered persona "is the only knob that distinguishes one persona-equipped agent from another at the prompt level." It is a prompt template with a colour and a glyph — no behavioural machinery. Marketing weight >> substance. |
| PII redaction *implementation* | Real policy surface; enforcement is in `octop-harness`, unverifiable from this repo. |
| PyPI `octop` package, `octop.cloud` docs site | **Not independently verified in this pass.** |
| "RAG knowledge base" quality | Tables and routes exist; retrieval quality, chunking, and ranking are unexamined. |
| `trusted` field semantics on ACP runners | Field is real in the API contract; enforcement meaning is not documented. |

### 5.6 Documentation drift found in-repo

Two concrete instances, both of which argue for discounting the README slightly:

1. **`AGENTS.md` §5 says the schema version assertion is `v == 7`.** The
   migrations directory contains **19** pairs, latest `019_bridge_connections`, and
   `CHANGELOG.md` already references "schema v18". The `AGENTS.md` statement is
   three migrations out of date and directs contributors to bump a stale constant.
2. **The README's architecture tree contradicts `docs/architecture.md`.** The
   README shows `UserManager → HarnessAgentManager (per user) → AgentRuntime (per
   agent)`. `docs/architecture.md` §2 explicitly says: *"there is no longer a
   separate per-user `AgentManager`. The single global registry dispatches to
   whichever harness runtime matches the row."* The architecture doc is right; the
   README diagram is stale.

Also: `docs/expert-teams.md` and `docs/memory-slim.md` are **written in Chinese
with no English counterpart**, and the CHANGELOG is entirely Chinese. The English
README and docs are a translation layer over a Chinese-primary development
process. That is a real friction point for a comparison, and it is also why
`docs/expert-teams.md` — the best design document in the repo — is invisible to
most readers.

### 5.7 The memory model, and one very good failure

`docs/memory-slim.md` (S10) is about `octop memory slim`: online maintenance of
`memory.sqlite` in a *running* host. It is a maintenance feature, not a recall
architecture — the recall model (hierarchical + FTS) lives in the separate
`octop-memory` repo, which I did **not** fetch. Marked **UNVERIFIED** for recall
quality.

What S10 does show is an unusually rigorous honesty discipline, and it is worth
quoting because it is the opposite of the marketing voice:

- **It refuses to fake progress.** Backup/VACUUM reports no fake
  percentages or ETA; elapsed time increasing only means the service connection
  is still responsive, **not** a fraction complete.
- **It refuses to fake completion.** If a long read transaction still holds the
  WAL it reports "dedup done but space reclamation not done", releases the chat
  reservation, keeps a readable mixed format and the backup, and **does not
  report full compression and does not auto-rollback.**
- **It fails closed on capability mismatch.** A version-mismatched, memory-off,
  PostgreSQL, or non-running agent fails *before* rewriting anything.
- **It refuses the unsafe path over IM.** `/memory slim` is disabled on IM because
  the
  existing IM `user_id` is the agent owner used for session storage, **not a
  verifiable identity of the sender**. The user is told to use web or CLI. This is
  a genuinely correct refusal: a maintenance action that mutates a database must
  not be triggerable by an unverified IM sender, and the design says so.
- **It states its own untested edges.** Multi-GiB databases, full production IM
  end-to-end, and long-term disk growth are all marked **not tested**.
- **It records a partial test run as partial.** The full gates were not green —
  octop-memory had 24 pre-existing failures against an unwritable home directory,
  and the Octop full run aborted on a port-binding `PermissionError`. It records
  that it did not treat the aborted run or the PG skip as a pass.

**This is the strongest single quality signal in the repository.** Any comparison
should weight it far above the README.

---

## 6. Expert market, sharing, and the "shared resource pool"

The brief groups three roadmap items; they are three different mechanisms at
three different levels of maturity. Conflating them would be a mistake.

### 6.1 Expert sharing — shipped, and the cleanest of the three

- `published_experts` table, migration `005_shared_experts_sso_knowledge.sql`
- `infra/agents/experts/publish.py`, `published_creation.py`
- `GET /api/experts` surface; dashboard "share or publish experts"
- **Scope: within one deployment.** "publish your experts to other users in the
  same deployment" (roadmap wording). Not a cross-install or public market.
- Security-relevant detail from `docs/configuration.md`: an agent's
  `{workspace}/.env` — where per-agent provider API keys live — is **excluded from
  published-expert snapshots**. Snapshot hygiene is handled.

### 6.2 Expert market — shipped, and it is a SkillHub client

- `infra/agents/experts/market_creation.py` and
  `infra/agents/experts/skillhub_market.py` (**46,008 bytes**)
- `infra/skills/skillhub_market.py` (15,206 bytes), `skillhub_common.py`
- `infra/skills/skill_package_from_skillhub.py`
- Dashboard: `ExpertMarketTab.tsx`, `SkillHubTab.tsx` (16 KB),
  `SkillHubDetailDrawer.tsx`, `PluginMarketPanel.tsx`

So the "market" is a **client against an external SkillHub service**, not a
locally-hosted registry. The equivalent, reusable artifact is a market client
pattern (fetch catalogue → preview detail → import as a first-class local object
→ local enable/disable override), not a market implementation. `skill_packages`
is the local table (migration 002) holding what you imported.

### 6.3 "Shared resource pool" — SHIPPED per the roadmap, but I could not find it

The roadmap marks **"Shared resource pool — a central pool of skills and
sub-agents that any user can drop into a new expert without rebuilding from
scratch"** as shipped (`[x]`). Evidence check:

- In the complete 4,270-entry tree, `pool` matches **4 paths only**:
  `src/octop/infra/db/pool.py` (the database pool), `tests/unit/db/test_db_pool.py`,
  `tests/unit/db/test_postgres_pool_unit.py`, and
  `dashboard/src/pages/Settings/Models/.../ActiveModelPool.tsx` — which is an
  **LLM model** pool, not a skills/sub-agents pool.
- The nearest real objects are `skill_packages` (migration 002,
  `api/routers/skill_packages.py` at 26 KB) and `infra/agents/subagents/` with
  `api/routers/subagents.py`.

**Verdict: the *capability* plausibly exists as per-user `skill_packages` +
`subagents` tables you can attach to a new expert, but there is no object called
a "shared resource pool", no market-level sharing surface for skills/sub-agents
distinct from the SkillHub import path, and no code artefact matching the
description. Marked UNVERIFIED-as-described.** Worth a direct question to the
maintainer before treating it as a differentiator.

### 6.4 Agents, teams, and the library/market tabs

Dashboard Experts page has four tabs: "My experts | My teams | Expert library |
Market". `kind !== team` filters the
first; teams are `kind=team` and cannot be shared. The "hidden template" for
coordinators lives at `infra/agents/teams/template/` and is deliberately absent
from the expert catalogue (`template_name = team-host` is internal only) — a
coordinator cannot be copied into your library as a starting point.

---

## 7. Comparison-ready capability cards

Each card states what the thing **is** in checkable terms, with the specific
mechanism so it can be verified against another codebase rather than believed.

| Capability | What it is, concretely | Mechanism to check |
|---|---|---|
| **Expert** | An agent row with its own workspace, providers, channels, cron and skills. The unit of personalisation. | `agents` table; `~/.octop/agents/<agent_id>/`; `POST /api/agents` |
| **Team** | An expert with `kind=team` acting as a coordinator. Not a new entity. | `ALTER TABLE agents ADD COLUMN kind` (migration 016); `.octop/manifest.json` |
| **Roster** | A JSON file in the coordinator's workspace. No join table. | `{workspace}/.octop/manifest.json` |
| **Team room** | The coordinator's `thread_id`. Members get derived checkpoint ids. | `{room}~{member_id}`; `speaker_agent_id` on fan-in |
| **Dispatch** | Coordinator → inbox → member, with the task **rewritten** by the coordinator. | `peer_invoke_mode: sync\|async\|both`; `agent_list` + `ask_agent` |
| **In-flight tracking** | In-process, idempotent by `job_id`, released on success *or* failure. Lost on restart. | `infra/agents/teams/jobs.py` (2,553 bytes) |
| **Shared expert** | An expert published for reuse by other users in the same deployment. | `published_experts` (migration 005) |
| **Expert market entry** | A SkillHub record imported as a local `skill_package`. | `skillhub_market.py`; `skill_packages` (migration 002) |
| **Persona** | A prompt template rendered into `SOUL.md` at boot. The *only* per-persona prompt differentiator. | `mbti_profiles.py`; `agents.persona_mbti`; `{agent_name}`, `{user_display}`, `{custom}` |
| **Workspace** | A `BackendWorkspace` abstraction over filesystem / Docker / PostgreSQL / COS / S3, independent of the control plane. | `infra/backend/resolver.py` |
| **Memory** | Layer 3 — `{workspace}/memory.sqlite` or PG schema `agent_<id>`, travels with the workspace. Recall model lives in `octop-memory` (not fetched). | `config_json.memory.backend` |
| **Knowledge base** | RAG corpus with its own members table; shareable inside a deployment. | `knowledge_bases`, `knowledge_documents`, `knowledge_base_members` (005) |
| **ACP inbound** | Octop's expert exposed as an ACP stdio server to an external IDE. Separate process. | `octop acp --agent main`; Zed `agent_servers` |
| **ACP outbound** | Octop delegates to a coding CLI over ACP stdio, per-user runners, per-agent opt-in, child approval prompts surfaced in chat. | `acp_runner` `action=respond`; `config_json.acp.tool_enabled` |
| **Tool approval** | A named set of tools requiring a human; resumable, with a pending/expired lifecycle. | `DEFAULT_HITL_TOOLS`; `POST /api/agents/{aid}/chat/hitl/resume` |
| **Shell guard** | User-editable YAML rules with severity, remediation, and exclusion patterns; every change audited. | `~/.octop/security/tool_guard/`; `PUT /api/security/tool-guard/rules/raw` |
| **Channel** | An IM platform bound to one agent, 1:1. Credentials per platform. | `channels` table; `octop channel install` |
| **Session key** | `<agent>:<surface>:<user>:<scope>`, e.g. `<aid>:dashboard:<user_id>:dm` — the same runtime from web, CLI, IM, and cron. | `docs/architecture.md` §3 |
| **Scheduled job** | APScheduler job; `task_type=text` pushes directly, `agent` runs the agent then pushes. | `cron_jobs`; `octop cron run-now` |
| **Plugin** | A third-party Python module seeded from `~/.octop/plugins/`, bundled plugins default-off. | `plugins/*/plugin.yaml`; `PluginManager.seed_bundled()` |
| **Connector** | OAuth app or MCP gateway instance holding encrypted credentials. | `infra/connectors/`; `connector_instances` (013) |

---

## 8. Honest assessment

### 8.1 The five strongest ideas worth borrowing

1. **The coordinator-rewrites-the-task contract** (§3.3). The planner is the sole
   interpreter of user intent, and executors receive a *written task spec*, not
   the raw conversation, with the transcript demoted to System background. Most
   multi-agent harnesses hand the executor the same context the planner saw. This
   one line of design removes a whole class of redundant re-reasoning and of
   executors wandering off toward their own reading of the request.
2. **A team is an expert with a discriminator, not a new entity** (§3.1). Two-line
   migration, roster in a workspace file the agent can already edit, no membership
   table to migrate, no transaction to fail, no orphan rows. The trade (no indexed
   reverse lookup) is named and accepted. This is the most immediately liftable idea
   in the repository.
3. **Subtract the coordinator's capabilities instead of adding a policy**
   (§3.2). A role that cannot touch the filesystem does not need a rule saying it
   should not. Aggressive tool removal plus a negative-claim test beats a
   permissions document.
4. **A user-editable, audited shell-command guard with exclusion patterns**
   (§5.3). YAML at a known path, full CRUD, `severity` + `remediation` +
   `exclude_patterns` per rule, every mutation written to the audit log, harness
   reloaded on change. The `exclude_patterns` field is the detail most guardrail
   systems omit and it is the one that makes them usable in practice.
5. **Fail-closed and refuse-to-fake discipline, stated in the product**
   (§5.7). "No fake percentage or ETA." "Dedup done but space reclamation not done"
   is reported as exactly that. A maintenance action triggered from IM is refused
   because the IM `user_id` is an agent owner, not a verified sender. Aborted test
   runs are recorded as aborted, not as passes. This is the behaviour to copy, and
   it is stronger than anything in Octop's marketing copy.

**Honourable mention:** the four-library split (harness / gateway / memory /
browser) is the right seam, and the `BackendWorkspace` abstraction is what makes
"pluggable storage" real rather than nominal — workspace *content* is never read
by `Path.read_text` in the service, so a COS-backed workspace actually works.

### 8.2 The five places Octop is weaker or thinner than its README implies

1. **"16 MBTI persona templates plus an interactive quiz" — the quiz does not
   exist** (§5.5). Zero `quiz` matches in the complete file inventory; the
   documented API is a catalogue and a preview. And Octop's own doc deflates the
   rest: the persona "is the only knob that distinguishes one persona-equipped
   agent from another at the prompt level." A prompt template with a colour and a
   glyph, sold in a Highlights table next to genuinely substantial features.
   **This is the clearest instance of a README outrunning the code.**
2. **"Shared resource pool" is marked shipped but has no identifiable
   implementation** (§6.3). Four `pool` matches in the tree, all of them the
   database pool or an LLM model pool. The likely reality is per-user
   `skill_packages` + `subagents` tables — useful, but not a "central pool any
   user can drop into a new expert". Roadmap checkboxes are not evidence.
3. **AgentTeams is Beta by the project's own admission, in three places, and its
   durability story is 2.5 KB of in-process state** (§3.5). A restart mid-run
   silently loses in-flight member work and the callback never fires. The design
   is excellent; the durability is explicitly out of scope. Anyone evaluating
   multi-step team work must know this.
4. **The single-process premise is weakest exactly where the differentiators are**
   (§5.1). ADR 001 argues "we are LLM-bound, so asyncio handles it" — and then
   ships an in-browser terminal, headless Chromium automation, remote desktop, and
   a local ONNX embedding model. All CPU-heavy, all in the same event loop, with a
   published cost of "heavy CPU tasks block the event loop". The zero-dependency
   win is real; so is the ceiling, and it is a one-machine ceiling with no
   horizontal path.
5. **The desktop client's source is not in the repository, and the docs are
   drifting** (§5.5, §5.6). "PC client — SHIPPED" is evidenced by 86 entries of
   packaging scripts that bootstrap a runtime from elsewhere; the app cannot be
   reviewed from this repo. Meanwhile `AGENTS.md` tells contributors to bump a
   schema assertion that is three migrations stale (`v == 7` vs 19 migrations), and
   the README's architecture tree contradicts `docs/architecture.md` on whether a
   per-user `AgentManager` still exists. The two best documents in the project —
   `docs/expert-teams.md` and `docs/memory-slim.md` — are **Chinese-only with no
   English counterpart**, so the engineering quality is substantially
   under-advertised to an English reader while the English README is
   over-advertised.

### 8.3 Bottom line for a comparison

Octop is a **well-engineered, honestly-documented-internals, young,
single-box product for households and small teams**, with one genuinely novel
multi-agent design idea (the rewriting coordinator), one unusually well-built
security control (the tool guard), and the best failure-honesty discipline I have
read in a comparable project. Its README overstates it in the persona and
resource-pool rows and understates it in the Chinese-only design docs.

It is **not** an enterprise multi-tenant platform, **not** horizontally
scalable, and **not** durable across restarts for in-flight team work. Compared
with Alpha, the interesting axes are: coordinator-rewrite dispatch semantics
(worth stealing), the workspace-backend abstraction boundary (worth stealing), the
audited user-editable tool guard (worth stealing), and — as a gap check — that
Alpha already ships the thing Octop lists as **Planned: self-evolution**
(distilling conversations into reusable skills), and Octop's **Planned: Project**
(project-scoped workspaces) is an area where Alpha's thread/project model is
already further along.
