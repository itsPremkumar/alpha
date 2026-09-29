# Alpha collaboration-surface audit

**Scope.** This is Alpha's half of a gap analysis against **Octo** (mininglamp,
Apache 2.0), whose model is: agents join as **Bots** with an **AgentCard**; work
happens in a **Channel** split into **Threads**; when a discussion crystallises,
key points are extracted **for human confirmation** and become a **Matter**.

A sibling is researching Octo. This document answers the other question: **what
does Alpha actually have, and is it wired?**

**Method.** Code reading, not doc reading. The repo has a documented pattern of
fully-implemented capabilities that no production caller ever reaches, so every
subsystem below is classified into exactly one of:

- **implemented-and-tested** — code, routes, tests, and a production caller
- **implemented-and-unwired** — code + routes exist; no production caller outside
  its own package
- **declared-but-inert** — declared in a registry/config/docs; the wiring it
  claims does not exist or is unreachable
- **documented-but-absent** — documented, no code
- **implemented-but-overstated** *(added by this audit)* — reachable and doing
  something, but not the thing its own docstring, route summary or nav blurb
  claims. This class did not appear in the assignment's taxonomy and it is where
  the two worst findings live.

**Headline result of the inertness audit:** the two classes the assignment was
primed to find — *implemented-and-unwired* and *declared-but-inert* — turned out
to be **nearly empty in the collaboration layer**. Alpha's collaboration code is
wired. What is not clean is **truthfulness about it**: one module fabricates
successful delegations (§4) and one primary-tab subsystem is entirely in-memory
while its docstring says "persistent" (§1c). Neither is dead code; both are worse
than dead code, because both are reachable and will report success.

Evidence standard: every claim carries `file:line`. Anything not read is marked
UNVERIFIED. No product code was changed to produce this document.

**Live-stack caveat — read this before trusting any "it answers" claim below.**
The assignment states a stack is running and that live read-only probes on
8001/3000 are encouraged. **No stack was reachable for the entire duration of
this audit.** `127.0.0.1:2026`, `:3000`, `:8001` and `:8002` all refused
connection on first probe and again on the final probe before this document was
written; `Get-NetTCPConnection -State Listen` shows no listener on any of them
(Postgres 5432 and Redis 6379 *are* up, so the machine is not merely asleep).
Per the constraint that only the lead starts or restarts a server, this audit did
**not** start one.

The consequence is stated plainly because it limits the evidence: **every claim in
this document is read from source, not observed from a live response.** The
assignment's own preference — "prefer live probes on 8001 over reading route
decorators: a route that exists is not a route that answers with data" — could not
be honoured, and the alternative was not chosen because it is not available here.
Where a claim depends specifically on runtime behaviour rather than on source
(reachability of a route, live data shape), it is marked UNVERIFIED-by-probe. The
strongest single piece of corroborating evidence available instead is
`backend/tests/test_deploy_surface_parity.py`, which pins the deploy-surface
parity of these exact paths, and the fact that every subsystem traced has both a
registered entry in the generated `contracts/feature_manifest.json` and at least
one production importer.

---

## 1. Groups / channels / rooms

### What a "group" is in Alpha

Two entirely different things in this repository both want the word *channel*.
Conflating them is the single easiest way to misread Alpha against Octo.

**(a) A group room** — `alpha/groups/`, a multi-bot conversation *inside one
Gateway process*.

Object model — `GroupRoom` (`backend/packages/harness/alpha/groups/room.py:39-53`):

| field | type | note |
|---|---|---|
| `room_id` | `str` | `room_<8 hex>` (`service.py:120`) |
| `name` | `str` | lowercased key, `[A-Za-z0-9 _-]{1,64}` (`groups.py:28`) |
| `topic` | `str` | free text |
| `members` | `list[str]` | **bot names**, lowercased |
| `mode` | `Literal["mention","moderated","quorum","parallel","round_robin"]` | `room.py:10` |
| `moderator` | `str \| None` | defaults to `members[0]` |
| `kanban_board_id` | `str \| None` | **declared, never read** (see §7) |
| `project_id` | `str \| None` | optional link to a Project |
| `log` | `list[GroupMessage]` | **flat list — there is no Thread concept inside a room** |

`GroupMessage` (`room.py:18-28`) is `{id, sender, content, intent, mentions,
metadata, created_at}` where `intent ∈ {discussion, proposal, vote, action, pass,
card_update}` (`room.py:11`).

**A room has exactly one log. There is no per-topic thread inside it.** This is
the sharpest structural difference from Octo, where a Channel is explicitly split
into Threads. Alpha's nearest Thread analogue is the *conversation/thread* in
`alpha/runtime`, which is a chat transcript for a human+agent pair, not a thread
inside a channel.

Persistence — **a single JSON file per installation**, not a database:
`runtime_home()/groups/rooms.json` (`groups/service.py:20-30`), loaded at
construction (`service.py:44-54`), saved on every mutation
(`service.py:56-69`), whole-file rewrite via `tmp.replace()` (`service.py:64-67`).
Single-process by design — the module docstring says so at `groups.py:8-10`.

Routes — `backend/app/gateway/routers/groups.py`, prefix `/api/groups`
(`groups.py:25`), 12 route decorators:

| route | line |
|---|---|
| `GET ""` | `groups.py:97` |
| `POST ""` | `groups.py:106` |
| `GET "/by-project/{project_id}"` | `groups.py:143` |
| `POST "/by-project/{project_id}"` | `groups.py:129` |
| `GET "/{name}"` | `groups.py:152` |
| `POST "/{name}/messages"` | `groups.py:173` |
| `POST "/{name}/runs"` | `groups.py:197` |
| `GET "/{name}/runs"` | `groups.py:240` |
| `GET "/{name}/runs/{run_id}"` | `groups.py:253` |
| `POST "/{name}/runs/{run_id}/cancel"` | `groups.py:268` |
| `DELETE "/{name}"` | `groups.py:289` |

Registered at `app/gateway/app.py:1161`.

**Authorization is absent on this router; authentication is not.** Ten of the
eleven routes carry no `require_permission` and no route dependency; only
`DELETE /{name}` calls `require_admin_user` (`groups.py:291`).

I initially wrote this up as "no auth at all" and that was **wrong** — corrected
here rather than in a footnote. The Gateway mounts a **default-deny**
`AuthMiddleware` (`backend/app/gateway/auth_middleware.py:89-95`: reject any
non-public path with 401, two-stage cookie-then-JWT), and `/api/groups/*` is
**not** in either public list — `_PUBLIC_PATH_PREFIXES` is only
`/health`, `/docs`, `/redoc`, `/openapi.json`, `/api/v1/auth/oauth/`,
`/api/v1/auth/callback/` (`auth_middleware.py:39-46`) and `_PUBLIC_EXACT_PATHS`
lists the auth endpoints, the GitHub webhook, and the five peer-network card and
ingress paths (`auth_middleware.py:50-78`). So **every group route requires a
valid session**.

What is genuinely missing is the *authorization* layer: `POST /{name}/messages`
(`groups.py:173`) and `POST /{name}/runs` (`groups.py:197`) — which spend model
tokens and spawn one subagent per member — are reachable by **any authenticated
user**, with no permission check and no admin gate, while their siblings in
`projects.py` all carry `@require_permission("projects", …)` and
`peer_network.py` carries `@require_permission("threads", …)` on all 15. In a
multi-user Gateway that is a privilege gap between collaboration surfaces, not an
open door. UNVERIFIED by live probe (no stack reachable); verified by reading the
decorator set and the middleware's public-path lists.

### How does an agent join a group?

**By being named. There is no approval step anywhere in this path.**

`get_or_create_room` (`service.py:80-130`) auto-provisions every named member
into the bot registry:

```
bot_registry.get_or_create(m_clean)   # service.py:100 and :114
```

`POST /api/groups` will happily create a room named with members that do not
exist as bots — the call creates them (`groups.py:119` → `service.py:109-115`).
The default member list for a new room is `["architect", "coder", "reviewer"]`
(`service.py:110`), and those three bot profiles are themselves auto-created.
There is no `pending` state, no invitation, no admin gate, no approval queue in
the group layer at all. **Alpha has no equivalent of "bot requests to join, human
approves."**

### Is a group the same concept as an Octo Channel?

**No.** Three structural differences, each verified in code:

1. **Scope.** A group room is intra-process (`groups.py:8-10`). An Octo Channel is
   a durable collaboration space that bots from different hosts join.
2. **Threads.** Group rooms have a flat `log` (`room.py:51`). Octo Channels are
   split into Threads.
3. **Join.** Auto-provisioning on name (`service.py:100`) versus an explicit
   join-and-approve flow.

Alpha's *actual* cross-installation analogue of a Channel is the **peer-network
conversation** (§3), which is a different subsystem entirely and does support
`many_to_many` topology (`peer_network/models.py:69-76`).

### (b) `app/channels/` — IM bridges, not collaboration channels

`backend/app/channels/` holds Feishu, Slack, Telegram, Discord, DingTalk, Signal,
WeChat, WeCom, GitHub, and "buzz" adapters (`ls` of that directory). These are
**inbound/outbound IM platform bridges**: they receive a message on an external
chat app and dispatch it into the same single agent thread. They are not
Alpha-internal agent collaboration spaces, they have no threads, no membership,
and no agents joining them. Treating `app/channels/` as Octo's Channel concept
would be a category error. Their size is also the honest reason the router list
in §7 does not treat them as a collaboration surface.

### Production callers of `alpha.groups` (outside its own package, excluding tests)

Counted by `Select-String 'alpha\.groups'` across `backend/`, excluding
`backend/tests/` and `backend/packages/harness/alpha/groups/`:

| caller | file:line | what it does |
|---|---|---|
| `app/gateway/routers/groups.py` | `groups.py:60` | the HTTP surface |
| `app/gateway/routers/war_rooms.py` | 6 hits | war-room runs over rooms |
| `alpha/tools/builtins/war_room_tool.py` | 12 hits | agent-facing war room tool |
| `alpha/tools/builtins/group_chat_tool.py` | 2 hits | agent-facing group chat tool |
| `alpha/commands/channel_ops.py` | 4 hits | slash-command group ops |
| `alpha/commands/module_a_handlers.py` | 1 hit | slash-command handler |
| `alpha/projects/memory_bridge.py` | 2 hits | transcript → project memory |
| `alpha/projects/crew.py` | `crew.py:226` | membership↔room reconciliation |
| `alpha/kanban/bridge.py` | 1 hit | room → kanban board |

**8 distinct production modules** (the router itself excluded — `groups.py` is the
HTTP surface for the package, not a consumer of it). **Classification:
implemented-and-tested.**
Groups is the most thoroughly wired collaboration subsystem in Alpha, and the
only one where I could not manufacture an inertness claim.

### §1 verdict

`implemented-and-tested`. Real routes, real persistence, real agent-facing tools,
real UI (`teamops.ts:5-51` → `TeamOps` section). What it does **not** have is
Octo's two distinguishing moves: sub-threads inside a space, and an approval gate
on entry.

### (c) A **third** thing called a channel: the company engine — and it is volatile

`alpha/company/` contains a fourth distinct "channel" concept,
`GroupChannel` (`backend/packages/harness/alpha/company/group_chat.py:39-46`):
`{channel_id, name, description, member_bot_names, is_default, created_by,
created_at}`. It is served at `/api/company/groups*`
(`app/gateway/routers/company.py:295`, `:307`, `:325`, `:342`).

**This one has no persistence at all.** Every subsystem engine in
`AutonomousCompanyEngine` is a plain in-memory dict
(`company/organization.py:48-56`): `_organizations`, `_responsibility_engines`,
`_kpi_engines`, `_discovery_engines`, `_chat_engines` (→ `GroupChatEngine`, whose
`_channels`/`_messages` are dicts at `company/group_chat.py:55-57`),
`_attendance_engines`, `_bot_medics`, `_kanban_engines`. The engine itself is a
process global (`organization.py:721-724`), so state survives across requests —
but a search of the whole `alpha/company/` package for `json.dump`, `json.load`,
`write_text`, `read_text`, `open(`, `sqlite`, `SQLAlchemy` returns **zero hits in
`organization.py`, `group_chat.py`, `attendance.py`, `kpi.py`, `organization.py`,
`responsibility.py`, `discovery.py`, `bot_medic.py`, `kanban.py`,
`production_line.py`, `strategy.py`, `executive.py`, `self_improvement.py`.**
The only persistence in the package is the *adapter* layer,
`enterprise_kanban.py` (SQLite, `:55`, `:103`, `:158`, `:217`, `:243`).

So: **every organization, department, bot assignment, group channel, attendance
record and KPI the "War Room" shows is lost on Gateway restart**, while the kanban
cards it syncs *into* survive, because they live in a different SQLite-backed
engine. And the module docstring calls it a "Master controller instantiating and
operating **persistent** autonomous organizations" (`organization.py:46`).

This is the sharpest **implemented-but-overstated** finding in the audit, and it
matters for the Octo comparison because "War Room" is a **primary, always-visible
nav tab** (`NavTabs.tsx:77`, `isPrimary: true`, blurb "Autonomous AI Software
Enterprise War Room") — i.e. the most prominent collaboration surface in the
product is the one with no durable state.

---

## 2. Projects and crews

### What a Project is

Persistence is a **real SQL table**, unlike group rooms:
`ProjectRow.__tablename__ = "projects"` (`backend/packages/harness/alpha/persistence/projects/model.py:22-36`)
with `id`, `user_id`, `name`, `instructions`, `presentation` (JSON), `status`,
timestamps. Repository: `ProjectRepository` (`persistence/projects/sql.py:42`).

Critically, a project is **not a bag of agents** — it is an owner-scoped
namespace for *chat threads*. `GET /api/projects/{id}/threads`
(`projects.py:191-215`) is just `get_thread_store().search(project_id=...)`
over `threads_meta.project_id`. The model docstring is explicit that membership
lives outside this row (`persistence/projects/model.py:3-6`).

### What a Crew is

A **reconciling view over three otherwise-uncoordinated stores** — this is the
best-documented design decision in the subsystem and the module docstring says so
first thing (`backend/packages/harness/alpha/projects/crew.py:1-22`):

- `membership.json` — who is on the project (`projects/membership.py:32`)
- `rooms.json` — how they talk (`groups/service.py:28`)
- `context.json` — what they collectively know (`projects/context_router.py`)

`ProjectCrewService._sync_room` (`crew.py:220-266`) reconciles on **every** call.
The four stated invariants (`crew.py:15-21`): room members mirror membership;
a room exists iff the crew has 2+ agents (dropping to 1 *parks* rather than
deletes); leaving releases locks and emits `agent_left`; every reconciliation
that changed something emits `room_synced`.

`CrewView` (`crew.py:155-174`) is the single UI payload: members, room, collab
config, shared memory, state, active locks, recent events.

`CollaborationConfig` (`crew.py:65-122`) is a real per-project policy surface:
orchestration mode, moderator, max concurrent speakers, mention policy, auto
handoff, conflict policy, lock policy, `require_evidence`, memory budget,
transcript digest size, standup interval.

### Routes

`projects.py` has **86 route decorators** on `/api/projects` — by far the largest
collaboration surface in Alpha. The crew-relevant ones:

| route | line | notes |
|---|---|---|
| `GET /{project_id}/crew` | `projects.py:343-354` | **the reconciling read** |
| `POST /{project_id}/agents` | `projects.py:357-369` | bulk attach |
| `DELETE /{project_id}/agents/{bot_name}` | `projects.py:372` | detach |
| `PATCH /{project_id}/collaboration` | (present in decorator list) | policy patch |
| `POST /{project_id}/join` | `projects.py:245-263` | agent joins |
| `POST /{project_id}/leave` | `projects.py:266-283` | agent leaves |
| `POST /{project_id}/heartbeat` | `projects.py:286-302` | presence |
| `GET /{project_id}/presence` | `projects.py:305-316` | presence read |
| `GET /{project_id}/approvals` | `projects.py:1108` | **human approval queue** |
| `POST /{project_id}/approvals/{request_id}/resolve` | `projects.py:1122` | **human decision** |
| `GET /{project_id}/events` | `projects.py:749` | event stream |
| `GET /{project_id}/locks`, `POST .../locks` | present | resource locks |
| `GET/POST .../decisions` | present | ADRs |
| `GET/POST .../handoffs` + `/accept` | present | handoffs |
| `POST .../conflicts`, `/conflicts/detect`, `/conflicts/resolve` | present | conflict resolution |

### The reconciling read — confirmed

The assignment flagged that `GET /crew` is a *reconciling* read that writes as a
side effect. **Confirmed in code, not merely in a comment.**

```
projects.py:352   return get_crew_service().ensure_crew(project_id).to_dict()
crew.py:369-381   def ensure_crew(...)  -> "Make the crew consistent. Safe to call on every read."
crew.py:375           ensure_workspace(project_id)          # creates directories
crew.py:377           members = get_membership_store().presence(project_id)
crew.py:377           room, changes = self._sync_room(...)   # may create a room
crew.py:378-379       if changes: self._emit_sync(...)      # emits an event
crew.py:380           self._maybe_compact(project_id)       # may WRITE memory
crew.py:238               room = svc.get_or_create_project_room(...)   # CREATES a room
crew.py:265               svc.persist()                                # WRITES rooms.json
```

So a `GET` can: create the project workspace directory, **create a group room**,
append/remove room members, rewrite `rooms.json`, emit `room_synced` into the
event bus, and fold the transcript into Level-2 project memory. The docstring is
honest about the intent (`crew.py:370`, `crew.py:450-451`: "Read-only view;
still reconciles so the UI never renders stale drift") — but the HTTP verb does
not match the effect, and this is the project's own documented finding.

### Production callers

`alpha.projects.*` is imported by **64 distinct lines in `app/gateway/routers/projects.py`
alone**, plus `app/gateway/app.py` (2), `app/gateway/routers/bots.py` (2),
`benchmarks.py` (1), `ops_integration.py` (1), and ~30 sibling harness modules
(`integrations/github_bridge.py`, `workflow/dag_engine.py`,
`models/cost_governor.py`, `rsi/review.py`, `evolution/retrospective_engine.py`,
`bots/dm.py`, `tools/builtins/reversible_delete_tool.py`, …).
**Classification: implemented-and-tested.**

### UI reachability

`frontend/src/lib/projects.ts` exposes 24 exported functions including
`getCrew` (`projects.ts:438`), `updateCollaboration` (`projects.ts:450`),
`attachProjectAgents` (`projects.ts:213`), `getProjectMemory` (`projects.ts:518`),
and has dedicated test files `projects.test.mjs`, `projects-crew.test.mjs`,
`project-detail.test.mjs`, `project-overview.test.mjs`.
Workspace views: **Projects** (`NavTabs.tsx:88`) and **Team Ops** (`NavTabs.tsx:86`).

### §2 verdict

`implemented-and-tested`, with one caveat that is a *design* smell rather than an
inertness: the crew read is a write.

---

## 3. Peer network and Agent Cards

### The object

`PeerCard` — `backend/packages/harness/alpha/peer_network/models.py:100-165`.
Fields, in declaration order (this is exactly what the public route serialises):

`protocol` · `protocol_version` · `card_type` · `agent_id` · `name` ·
`description` · `version` · `url` · `websocket_url` · `preferred_transport` ·
`capabilities` · `skills` · `supports` · `pairing_required` · `issued_at` ·
`alpha_instance`

**That is 16 fields**, consistent with the lead's observation. Constants:
`PROTOCOL = "alpha-a2a"`, `PROTOCOL_VERSION = "1.0"`,
`CARD_TYPE = "application/alpha-peer-card+json"` (`models.py:41-43`).

### Is this the same concept as the public AgentCard spec?

**No. It is a different thing that borrowed the name — and the repository says so
itself.** Three pieces of evidence:

1. **The card type string is not A2A's.** A2A's public Agent Card is served as
   `application/json` at `/.well-known/agent-card.json`. Alpha declares
   `card_type: "application/alpha-peer-card+json"` (`models.py:43`) and
   `protocol: "alpha-a2a"` (`models.py:41`).
2. **The field set does not match A2A's required fields.** The public
   `AgentCard` requires at minimum `name`, `description`, `url`, `version`,
   `capabilities` (an *object* with `streaming`/`pushNotifications`/
   `stateTransitionHistory`), `defaultInputModes`, `defaultOutputModes`, and
   `skills` (an array of objects each carrying `id`/`name`/`description`/`tags`).
   Alpha's card has **no `defaultInputModes`, no `defaultOutputModes`, no
   `provider`, no `documentationUrl`, no `securitySchemes`, no `security`**, its
   `capabilities` is a flat `list[str]` (`models.py:120`) rather than the
   required object, and its `skills` is an untyped `list[dict]` (`models.py:121`).
   An A2A SDK client validating against the spec would reject it.
3. **The repo refuses the claim itself**, twice, in the same breath:
   `docs/ALPHA_PEER_NETWORK.md:66-69` — "it is not a claim of full conformance to
   every A2A SDK feature"; and `peer_network/models.py:5-6` — "aligned with the
   A2A concepts … **without pretending to implement every A2A SDK feature**".

**On the sibling's Hermes hypothesis.** The sibling found that repo history once
stripped *attribution* to an external project called Hermes and left
`HermesLocalBridge` symbols as ported code — i.e. a borrowed name carrying a
different meaning. For "AgentCard" the situation is *not* the same, and the
distinction matters. Alpha's `PeerCard` is **not** ported from Hermes or from
anyone; `docs/ALPHA_PEER_NETWORK.md:13-14` states the implementation was designed
around an in-repo reference plan
(`references/ALPHA_TO_ALPHA_COMPLETELY_FREE_COMMUNICATION_OPTIONS.md`), and the
naming is a deliberate, documented A2A-*flavoured* local convention with a
`alpha_` prefix and a non-standard media type — which is the honest way to borrow
a name. The peer_network module even carries a 10-line naming note specifically
disambiguating the three colliding acronyms MCP / A2A / ACP
(`peer_network/models.py:8-29`), and `alpha/config/acp_config.py` +
`tools/builtins/invoke_acp_agent_tool.py` are named as *the real* ACP.
**Verdict on this specific question: borrowed name, different meaning, but
Alpha's own and honestly labelled — not an unattributed import.**

**There is also a second, entirely unrelated "card" in the same repo**, which is
the actual naming trap. `AgentCapabilityCard` in
`alpha/protocols/a2a.py:19-31` has `agent_id`, `name`, `description`, `version`,
`skills: list[str]`, `supported_protocols`, `input_schema`, `output_schema`,
`auth_mode`, `availability`, `endpoint_url`, `created_at`. It is served at
`/api/protocols/a2a/cards` (`app/gateway/routers/a2a.py:60-80`). It shares no
storage, no identity model, and no transport with `PeerCard`. See §4.

### Routes and persistence

Public, no browser session (`peer_network.py:261-274`), all three return the same
`PeerCard.to_dict()`:

| route | line |
|---|---|
| `GET /.well-known/agent-card.json` | `peer_network.py:261` |
| `GET /.well-known/agent.json` | `peer_network.py:266` |
| `GET /api/peer-network/card` | `peer_network.py:271` |

Authenticated management routes: 15 (status, peers, discover, github/publish,
pair, pair/rotate, trust patch, conversations CRUD, messages, read receipt, SSE
events) at `peer_network.py:104-255`. Registered `app.py:1165-1166`.
Nginx forwards all three exact paths (`docker/nginx/nginx.conf:296,305`).

Persistence: **SQLite WAL**, `${ALPHA_HOME}/peer_network/network.sqlite3`
(`docs/ALPHA_PEER_NETWORK.md:223-228`), synchronous repository
(`peer_network/storage.py`, must be called via `asyncio.to_thread` per
`backend/AGENTS.md:294-296`).

Lifecycle: the service is constructed and `start()`ed in the Gateway lifespan
(`app/gateway/app.py:455-459`) and `stop()`ed first on shutdown
(`app.py:669-672`). **This is genuinely wired**, unlike the documented libp2p gap,
which the docs correctly refuse to claim (`docs/ALPHA_PEER_NETWORK.md:33`).

### Security posture — genuinely strong, and worth crediting

- Public pairing/messaging routes authenticate with the network token, never a
  browser session (`peer_network.py:276-293`).
- The pairing code is never placed in the card (`peer_network/models.py:216-219`).
- `GET /status`, `POST /pair/rotate`, `POST /github/publish` are **permission AND
  admin** because their bodies carry the inbound bearer
  (`peer_network.py:104-117`, `:157-171`, `:134-147`). The comments at
  `peer_network.py:160-168` record that the rotate route was originally at bare
  `threads:write` and leaked the credential one route over.
- The model-facing tool strips endpoints, cards, pairing codes and credentials
  (`docs/ALPHA_PEER_NETWORK.md:213-219`).

### Production callers

`app.py` lifespan ×3 (`app.py:141`, `455-459`, `669-672`), `routers/peer_network.py`
(3), `tools/builtins/peer_network_tool.py` (2), `routers/bots.py` (2),
`routers/ops_integration.py` (1). **Classification: implemented-and-tested.**

### §3 verdict

`implemented-and-tested`, and honest about its own non-conformance — which is
**better** than Octo's position if Octo claims spec conformance, since an Alpha
peer and a spec-conformant A2A agent would silently fail to interop and both
sides would be wrong.

---

## 4. A2A / interop

### The `alpha-a2a` envelope

`PeerEnvelope` (`peer_network/models.py:168-210`): `id`, `protocol`,
`protocol_version`, `kind`, `sender_id`, `recipients`, `conversation_id`, `text`,
`payload`, `created_at`, `reply_to`, `idempotency_key`. 19 bounded `kind` values
(`models.py:47-67`) that are deliberately A2A-*flavoured* (`task_request`,
`task_accept`, `task_reject`, `task_progress`, `task_result`, `review_request`,
`review_result`, `file_offer`, …).

**How far does it get?** It goes as far as: UDP/mDNS discovery → out-of-band
pairing → HTTP-first/WebSocket-fallback transport → per-recipient
queued/delivered/read receipts with retry. What it does **not** do: speak real
A2A. There is **no A2A SDK dependency** in the backend (`docs/PROTOCOLS.md:243`
rates A2A interop **"S"**), and `docs/ALPHA_PEER_NETWORK.md:70-72` says so
explicitly.

**The Helm discovery defect — confirmed.** UDP 8743 is published in compose:

```
docker/docker-compose.yaml:127   "${BIND_HOST:-127.0.0.1}:${ALPHA_PEER_NETWORK_DISCOVERY_PORT:-8743}:.../udp"
docker/docker-compose-dev.yaml:218   (same)
```

…and a repo-wide search for `8743` returns **no hit anywhere under `deploy/`**. So
in a Helm deployment the beacon is sent and nothing on the LAN or the Service can
hear it. The two ways to reach a peer still work — manually addressed `POST
/api/peer-network/pair` (`peer_network.py:150-154`) or the opt-in GitHub card
rendezvous (`peer_network.py:134-147`) — so this is a *discovery* outage, not a
total outage. That distinction matters and the earlier framing ("no peer can
discover the node at all") is right for Helm specifically and only for discovery.

### The *other* A2A — and it is the weakest thing in this report

`alpha/protocols/a2a.py` is a separate, unrelated module reached at
`/api/protocols/a2a/*` (`routers/a2a.py:18`). Its docstring claims "Google A2A
(Agent-to-Agent) Interoperability Protocol Adapter" (`protocols/a2a.py:1-5`) and
its route docstring claims it "Dispatches a task delegation request to a target
agent using the A2A protocol" (`routers/a2a.py:85`).

**It does not dispatch anything.** `A2AProtocolAdapter.delegate`
(`protocols/a2a.py:75-110`) looks the card up, checks `availability != "offline"`,
and then:

```python
# Simulate or dispatch delegation deliverable
deliverable_content = f"[A2A Delivered by {card.name}]: Successfully resolved objective: '{request.task_objective}'"   # :101
response = A2ADelegationResponse(
    request_id=request.request_id,
    status="completed",                                                              # :104
    deliverable={"summary": deliverable_content, "agent": card.name},                 # :105
    evidence=[f"Verified by A2A endpoint: {card.endpoint_url or 'local-bus'}"],        # :106
    ...
)
```

It **fabricates a successful completion** by string interpolation, and then
asserts an `evidence` string for work that never happened — on a path the
repository's own claim-honesty rules (`AGENTS.md:31-35`) would forbid elsewhere.
Three of the four cards registered at import time
(`routers/a2a.py:23-49`: `primary-orchestrator`, `agent-researcher`,
`agent-security-auditor`) are hardcoded literals with no relationship to any real
agent.

Wiring status: the router is registered (`app.py:1177`) and the tool *is* in the
agent's real toolset — `alpha/tools/builtins/a2a_tool.py`, in
`tools/builtins/__init__.py:1,300`, in `tools/tools.py:13,226,386`, and in
`contracts/feature_manifest.json`. So **this is the worst failure mode in the
audit: fully wired, fully reachable by the model and by HTTP, and it returns a
fabricated success.** It is not inert — it is *dishonest*.

**Classification for §4: two separate things.**
- `alpha.peer_network` / `alpha-a2a` envelope — `implemented-and-tested`,
  reachable, honestly labelled as non-conformant.
- `alpha.protocols.a2a` — `implemented-and-wired-but-fabricating`. Reachable via
  `POST /api/protocols/a2a/delegate` and via the `a2a_tool`; answers `status:
  "completed"` for work it never performed.

---

## 5. The "Matter" analogue — human confirmation of a crystallised discussion

**This is the most important section, so it is stated up front: Alpha has
something real here, and it is not the same shape as Octo's Matter. Two
mechanisms exist, both genuine; neither is triggered by a discussion
crystallising.**

### Does anything turn a discussion into a tracked unit of work?

Searched for the mechanism by every name it could wear —
`crystallis*`, `key_point`, `extract_points`, `summarize_discussion` across the
whole backend. Results:

- `alpha/memory/cognitive/consolidation.py:151-179` — "Structural Belief
  Crystallization" during a "Deep Sleep" pass. This is *belief* consolidation
  into a semantic graph, not discussion→work, and it has nothing to do with
  humans confirming anything.
- `alpha/tools/builtins/cognitive_memory_tool.py:61` — "Crystallize a factual
  statement into the semantic graph". Same subsystem, same answer.
- `alpha/evolution/evidence/provenance.py:57` / `service.py:226` — a `proposal`
  entry kind. This is an **evidence ledger** for self-improvement claims, and the
  proposal is authored by the system, not extracted from a human conversation.

**No production code path extracts key points from a group-room transcript and
proposes them to a human.** The nearest thing is
`alpha/projects/memory_bridge.py:40` (`OPEN_INTENTS = ("proposal", "vote")`),
which *filters already-posted* room messages whose intent is `proposal` or `vote`
into project memory. It does not produce them. Grepping for who ever posts
`intent="proposal"` into a room finds exactly one producer:
`alpha/tools/builtins/group_chat_tool.py:86` — i.e. the agent itself, when the
model chooses to.

### What Alpha does have instead — two real human-confirmation mechanisms

**(1) `ApprovalQueue` — human sign-off before a high-blast-radius action.**
`backend/packages/harness/alpha/projects/approval_queue.py`. This is the closest
thing in the repository to the spirit of Octo's confirmation step, and it is
**fully implemented and fully wired**:

- `ApprovalRequest` (`approval_queue.py:34-57`) carries `risk_level`,
  `action_type`, `details`, `diff_preview`, `status ∈ {pending, approved,
  rejected, timed_out}`, `resolved_by`, `resolution_comment`.
- `request_approval()` (`:98-133`) persists and emits `approval_requested`.
- `resolve_request()` (`:135-168`) is the **human** decision, and it emits
  `approval_granted` / `approval_rejected`.
- Persistence: `runtime_home()/projects/<id>/approvals/queue.json`
  (`:30-31`), atomic `tmp.replace()` (`:89-94`).
- Routes: `GET /api/projects/{id}/approvals` (`projects.py:1108`) and
  `POST /api/projects/{id}/approvals/{request_id}/resolve` (`projects.py:1122`).

**Production producers of approval requests — 6 call sites, all real:**
`bots/dynamic_profiles.py:386`, `evolution/retrospective_engine.py:111`,
`models/cost_governor.py:356`, `rsi/review.py:414`,
`tools/builtins/reversible_delete_tool.py:218,284`, and
`workflow/dag_engine.py:514`.

**Consumers that block on it — also real.** `workflow/dag_engine.py:354` takes an
`approval_queue` and *pauses the run* pending resolution
(`dag_engine.py:17`: "resumes on approval, and fails" otherwise), and
`reversible_delete_tool.py` refuses to execute a destructive action without a
resolved approval (`:203`, `:284`).

**UI reachability — four separate surfaces, all live:**
- `WorkforceSection.tsx:389-407` — "Pending approvals" panel via
  `fetchPendingApprovals()` (`lib/workforce.ts:236-243`, `/policy/approvals?status=pending`).
- `WorkforceSection.tsx:869-907` — "Human Approval Queue" panel reading
  `warRoom.data?.pending_approvals` and resolving via
  `resolveApprovalRequest()` (`lib/workforce.ts:384-392`).
- `ForgeSection.tsx:1017-1059` — approvals list, create, approve/reject.
- `HumanApprovalCard.tsx:12-37` rendered inline in the chat transcript from
  `MessageItem.tsx:388-389` — **an approve/deny card in the message stream
  itself**, plus the lion-pet reacts to it (`useLionPetActivity.ts:43`).
- `WorkflowsSection.tsx:872-891` — per-node approve/deny on a paused workflow run.

**(2) A second, independent policy engine.**
`alpha/policy` with `listApprovals` / `createApproval` / `decideApproval`
(`lib/policy.ts:145-178`, backing `GET/POST /api/policy/approvals*`). Note this is
a **different store** from `projects/approval_queue.py`; the frontend reaches both
and `WorkforceSection` shows both.

### So: does Alpha have Octo's Matter?

**Partly — and the gap is precise.**

| Octo Matter | Alpha | Verdict |
|---|---|---|
| a discussion *crystallises* into candidate items | — | **absent.** No extraction code exists. |
| items are shown to a human for confirmation | `ApprovalQueue` pending list + inline chat card | **present**, but for *actions*, not for *conclusions* |
| a human confirms/rejects | `POST /projects/{id}/approvals/{id}/resolve` (4 UI surfaces) | **present and wired** |
| confirmed item becomes tracked work | `reversible_delete_tool` gates execution; `dag_engine` resumes | **present, narrow** |

Alpha's human-in-the-loop machinery is **real, is the most under-advertised
capability in the repo, and covers the second half of Octo's idea well.** What
Alpha has no equivalent for is the **first half**: nothing ever looks at a group
room transcript and says "these are the points, confirm or reject them." A human
must notice the discussion themselves and initiate the approval, or the agent must
decide unprompted to raise one.

**Plain statement, as asked:** *Alpha has no automatic discussion→Matter
extraction. It has a stronger-than-expected confirmation-and-gate layer for
actions that a human or an agent chooses to escalate.*

### Neighbouring candidates I checked and rejected

- **Missions** (`alpha/missions/store.py`, `alpha/mission/`) — real routes
  (`routers/missions.py`, 11 routes) with `transition`, `acceptance`,
  `observations`, `threads`, `artifacts`. But the acceptance registry
  (`mission/acceptance.py`) is evaluated *after* execution and by the system
  judge, not confirmed *by a human before* work. Not a Matter.
- **Swarm acceptance criteria** — `docs/WORKFORCE.md:310` states it correctly:
  "Acceptance criteria are verified separately from task execution, so a worker
  can complete while the plan remains `partial_success`". That is a *gate*, not a
  *human confirmation*.
- **Kanban `Review / Audit` column** — `docs/WORKFORCE.md:284` lists it, and
  `lib/kanban-board.ts:26` has an `approval` column with hint "Needs sign-off".
  But the card lifecycle is bot-driven (`kanban_board_tool` actions
  `create_card`/`assign_card`/`move_card`/`complete_card`) and completion is gated
  by *evidence submission*, not human sign-off (`docs/WORKFORCE.md:289`).
- **Evidence-gated completion** — `docs/WORKFORCE.md:202-203` and
  `CollaborationConfig.require_evidence` (`crew.py:80`). Evidence, not a human.
- **Council / deliberation** (`alpha/governance/council/`, `routers/council.py`)
  and **war rooms** (`alpha/groups/war_room.py`, `routers/war_rooms.py`) — these
  are *multi-agent* review, the opposite of a human confirmation step.
- **RSI review** (`alpha/rsi/review.py:26`) — reuses the approval queue, and its
  docstring is explicit that "the resolution must come from the human". This is a
  **real, correctly-scoped use** of the mechanism, for self-improvement
  promotion. Confirms the mechanism's intent; still not a Matter.

---

## 6. Frontmatter: what is reachable in the UI

There is **no Next.js App Router route tree** — `frontend/src/app/` contains
exactly four files (`globals.css`, `layout.tsx`, `manifest.ts`, `page.tsx`). All
navigation is a client-side view switch over a single registry,
`WORKSPACE_TABS` in `frontend/src/components/NavTabs.tsx:74-109`.

**26 registered views.** The registry and the renderer are in exact 1:1
correspondence: `WorkspaceView` declares 27 union members
(`NavTabs.tsx:34-61`, including `"chat"`), and `ChatView.tsx` branches on all 27
— `"chat"` at `ChatView.tsx:1627`/`:1673` and the other 26 in the ternary chain
at `ChatView.tsx:1813-1957`. **No view is registered without a renderer, and no
renderer exists without a view.** Per collaboration concept:

| concept | view (`NavTabs.tsx` line) | client module | reaches |
|---|---|---|---|
| group room | Team Ops `:86` | `lib/teamops.ts:5-51` | `/api/groups*` ✅ |
| company channels / attendance / KPIs / kanban | War Room `:77` (**primary tab**) | `lib/war-room.ts`, `lib/workforce.ts:276-392` | `/api/company/*`, `/api/war-rooms/*` ⚠️ in-memory only (§1c) |
| swarms | Team Ops `:86` | `lib/teamops.ts:52-186` | `/api/swarms*` ✅ |
| jobs | Team Ops `:86` | `lib/teamops.ts:199-227` | `/api/jobs*` ✅ |
| company KPIs / org chart / fleet health / kill switch | Team Ops `:86` | `lib/teamops.ts:243-348` | `/api/company/*`, `/api/bots/*` ✅ |
| bot inbox / presence | Workforce `:87` | `lib/workforce.ts:236-243` | ✅ |
| pending approvals | Workforce `:87` | `lib/workforce.ts:236-243` | ✅ |
| Human Approval Queue (project) | Workforce `:87` | `lib/workforce.ts:384-392` | ✅ |
| chat approvals (inline card) | Chat `:76` | `HumanApprovalCard.tsx:12` | ✅ |
| projects / crew / collaboration policy | Projects `:88` | `lib/projects.ts:188-520` | ✅ |
| project memory, ADR, locks, handoffs | Projects `:88` | `lib/project-detail.ts:202-216` | ✅ |
| peer network (Alpha Network) | Alpha Network `:82` | `lib/peer-network.ts:172-266` | `/api/peer-network*` ✅ |
| A2A capability cards + delegation | Protocols `:107` | `lib/protocols.ts:62-135` | `/api/protocols/a2a*` ⚠️ see §4 |
| agent roster / messages / deliveries / blueprints / incidents | Protocols `:107` | `lib/protocols.ts:136-329` | ✅ |
| war room | War Room `:77` | `lib/war-room.ts` | `/api/war-rooms*` ✅ |
| deliberation (quorum/dissent/taint) | Deliberation `:78` | `lib/deliberation.ts` | ✅ |
| bots | Bots `:79` | `lib/bots.ts` | ✅ |
| kanban board | Board `:80` | `lib/kanban-board.ts`, `lib/kanban.ts` | ✅ |
| agent chats + group rooms | Messages `:81` | `lib/inbox.ts` | ✅ |
| workflow run approvals | Workflows `:100` | `lib/workflows.ts:620-631` | ✅ |
| scheduled tasks | Scheduled `:95` | `lib/scheduled.ts` | ✅ |
| policy approvals (2nd store) | Forge `:101` | `lib/policy.ts:145-178` | ✅ |
| channels (IM) | Channels `:89` | `lib/channels.ts` | ✅ |

**Three conclusions from the frontmatter.**

1. **There is no view for `GET /api/groups/{name}/runs` streaming.** `docs/WORKFORCE.md:268-278`
   documents an event stream (`round_started`, `member_message`,
   `revision_requested`, `run_completed`) served at
   `/api/groups/{name}/runs/{run_id}/stream`. **That route does not exist** —
   `groups.py` has no `.../stream` decorator; the documented stream is
   `documented-but-absent`. `teamops.ts:46-51` can start a run but has no stream
   reader.
2. **There is no view over `GET /api/protocols/a2a/cards` that tells the operator
   it is fabricated.** The Protocols tab renders the cards as data
   (`lib/protocols.ts:62-73`). A card reading `availability: "available"` is
   presented identically whether or not delegating to it actually does anything —
   and per §4 it does not.
3. **`kanban_board_id` on `GroupRoom` (`room.py:49`) has no consumer**, so the
   room↔board link is invisible in every view.

---

## 7. Inertness audit — production caller counts

Method: `Select-String` for each package's import namespace across `backend/`,
then **excluding** (a) `backend/tests/**`, (b) the package's own directory, and
(c) `backend/.head_projects.py` (a tracked scratch file, `backend/.head_projects.py`,
61,466 bytes — it is not imported by anything and is counted separately).
`rg` was not used: it is broken in this environment and silently returns nothing.

| subsystem | production callers outside own package | classification |
|---|---|---|
| `alpha.groups` (rooms) | **8** (`war_rooms.py`, `war_room_tool.py`, `group_chat_tool.py`, `channel_ops.py`, `module_a_handlers.py`, `projects/memory_bridge.py`, `projects/crew.py`, `kanban/bridge.py`) | implemented-and-tested |
| `alpha.projects` | **~40 modules**, incl. 64 import lines in `routers/projects.py` alone | implemented-and-tested |
| `alpha.peer_network` | **5** (`app.py` lifespan ×3, `routers/peer_network.py`, `peer_network_tool.py`, `bots.py`, `ops_integration.py`) | implemented-and-tested |
| `alpha.protocols.a2a` | **3** (`routers/a2a.py`, `tools/builtins/a2a_tool.py`, `protocols/__init__.py`) — all **registered** (`app.py:1177`, `tools/tools.py:226`) | implemented-and-**fabricating** (§4) |
| `alpha.projects.approval_queue` | **6 producers** + **2 routers** + **4 frontend surfaces** | implemented-and-tested |
| `alpha.workflow.dag_engine` (HIL gates) | wired via `dag_engine.py:354`; **blocked runs** in production | implemented-and-tested |
| `alpha.missions` | **1** (`routers/missions.py`, 11 routes) + 2 tools (`compile_mission_tool.py`, `mission_hierarchy_tool.py`) | implemented-and-tested (thin caller set is the router itself) |
| `alpha.kanban` | **4** (`kanban_board_tool.py` ×2, `planning/autonomous.py`, `autoplan_tool.py`) + `capabilities/catalog.py` | implemented-and-tested |
| `alpha.groups.war_room` | **2** (`war_room_tool.py`, `routers/war_rooms.py`) | implemented-and-tested |
| `alpha.learning.review_queue` | **2** (`learning_fork_middleware.py`, `autonomy/loops.py`) | implemented-and-tested |
| `alpha.governance.council` | **2** (`routers/council.py`, `quality_council_tool.py`) | implemented-and-tested |
| `alpha.mission` (singular: engine/goalloop/acceptance) | **3** (`routers/missions.py`, `compile_mission_tool.py`, `mission_hierarchy_tool.py`, `planning/autonomous.py`) | implemented-and-tested |
| `alpha.rsi.review` | reuses approval queue; reachable via `/api/{evolution,projects}/…/rsi` | implemented-and-tested |
| `alpha.selfrepair.engine` | documented as classification-only, refuses repair (`backend/AGENTS.md:53-62`) | honest |
| `alpha.company` (orgs, channels, attendance, KPI) | **1** production module outside itself (`routers/company.py` + `tools/builtins/company_tool.py`), and **zero persistence** | **implemented-but-volatile** — see §1(c) |

**Genuinely inert (declared-but-inert), the complete list found:**

| item | file:line | why inert |
|---|---|---|
| `GroupRoom.kanban_board_id` | `groups/room.py:49` | the room↔board link has **zero readers** repo-wide, so nothing ever sets or reads it |
| `A2AProtocolAdapter.get_response()` / `self._history` | `protocols/a2a.py:112-113`, written at `:87/:97/:109` | **never called** — no route, tool, or test reads a delegation response back; the history is write-only |
| libp2p transport | `docs/ALPHA_PEER_NETWORK.md:33` | self-declared seam, reported unavailable |
| `GET /api/groups/{name}/runs/{run_id}/stream` | documented at `docs/WORKFORCE.md:268-278` | **no such decorator exists** in `groups.py` — documented-but-absent |

**Explicitly checked and found NOT inert** (recorded because the first pass
suspected them): `GroupChatService.get_or_create_project_room` — the crew
module's docstring says it "was written but never called"
(`projects/crew.py:10-11`), and that was true when written, but
`projects/crew.py:238` now calls it, as do `POST /api/groups/by-project/{id}`
(`groups.py:135`) and `GET /api/groups/by-project/{id}` (`groups.py:146`). The
docstring is now **stale on that specific point**.

**The single most surprising inertness result: there is no large inert
collaboration subsystem in this repository.** Every collaboration package I traced
had at least one production caller outside itself and a registered entry in
`contracts/feature_manifest.json`. This audit was commissioned on the hypothesis
that Alpha's collaboration layer would be full of code nobody calls. That
hypothesis is **not supported** for collaboration — the inertness in this repo is
concentrated in *claims of conformance* (§4) and in *auth* (§1), not in dead
code.

### Bonus finding, reported because it is exactly the class of defect this audit was commissioned to find

**`backend/app/gateway/routers/groups.py` — authorization gap on the two routes
that spend real resources.** The Gateway's `AuthMiddleware` is default-deny
(`app/gateway/auth_middleware.py:89-95`) and `/api/groups/*` is not public
(`auth_middleware.py:39-78`), so these routes are not open. But `groups.py`
carries **zero** `@require_permission` decorators, and
`require_admin_user` appears exactly once, on `DELETE /{name}` (`groups.py:291`).
That means `POST /{name}/messages` (`groups.py:173`) and `POST /{name}/runs`
(`groups.py:197`, which fans an objective out to one subagent per member and then
runs a moderator synthesis) are executable by **any authenticated user**, while
every sibling route in `projects.py` requires `"projects": read/write` and all 15
management routes in `peer_network.py` require `"threads": read/write`. A group
room also has no notion of an owner: `GroupRoom` (`groups/room.py:39-53`) has no
`owner_id` field, and membership is a flat `members: list[str]`, so there is
nothing for a permission check to bind to even if one were added.
UNVERIFIED by live probe (no stack reachable); verified by reading the decorator
set, the router registration at `app.py:1161`, and the middleware's public-path
lists.

---

## Summary tables

### Concept → route → persistence → production callers → UI reachable

| Octo concept | Alpha route | persistence | prod. callers | UI reachable? |
|---|---|---|---|---|
| **Bot** + card | `GET /api/bots`, `POST /api/bots/{n}/ensure` (`bots.py`); `GET /.well-known/agent-card.json` | bot profiles + `peer_network/network.sqlite3` | many | ✅ Bots, Protocols |
| **Bot joins** | `POST /api/bots/{n}/ensure` (`bots.py`); auto-provision `service.py:100` | same | many | ✅ |
| **Approval to join** | — | — | **0** | ❌ **absent** |
| **Channel** | `POST /api/projects` (thread namespace) | `projects` table | ~40 | ✅ Projects |
| **Thread inside Channel** | — (project *is* a thread namespace; group room has one flat `log`) | — | — | ⚠️ structurally absent |
| **Group room** (Alpha-native) | `/api/groups/*` (11 routes) | `groups/rooms.json` | 8 | ✅ Team Ops |
| **Company channel** | `/api/company/groups*` (`company.py:295,307,325,342`) | **none — process memory** | 1 | ⚠️ War Room, volatile |
| **Crew** | `GET /api/projects/{id}/crew` (`projects.py:343`) | membership.json + rooms.json + context.json | ~40 | ✅ Projects |
| **Agent Card (public spec)** | `GET /.well-known/agent-card.json` (`peer_network.py:261`) | SQLite | 5 | ✅ Alpha Network |
| ↳ conforms to A2A AgentCard? | **no** — `card_type: application/alpha-peer-card+json`, missing `defaultInputModes`/`defaultOutputModes`/security | — | — | — |
| **A2A transport** | `alpha-a2a` envelope over HTTP/WS/UDP | SQLite WAL | 5 | ✅ Alpha Network |
| ↳ real A2A SDK | **no** — `docs/PROTOCOLS.md:243` rates it "S"; no SDK dep | — | — | — |
| **Google-A2A module** | `POST /api/protocols/a2a/delegate` (`a2a.py:83`) | **in-memory dict** (`protocols/a2a.py:59-61`) | 3, registered | ⚠️ reachable but **fabricates completion** |
| **Matter (discussion→confirmed work)** | — | — | **0** | ❌ **absent** |
| ↳ human confirmation of an *action* | `GET/POST /api/projects/{id}/approvals*` (`projects.py:1108,1122`) | `projects/<id>/approvals/queue.json` | 6 producers, 4 UI | ✅ ×4 surfaces |
| ↳ blocks execution until resolved | `workflow/dag_engine.py:354`, `reversible_delete_tool.py:284` | — | ✅ | ✅ |
| **Cross-installation discovery** | `POST /api/peer-network/discover` (`:127`) | UDP 8743 | 5 | ✅ |
| ↳ in Helm | **no** — `8743` absent from `deploy/` (only compose `:127`, `:218`) | — | — | — |

---

## Three capabilities Alpha has that Octo appears to lack

1. **A real, evidence-gated, human-blocking execution layer.** Six distinct
   subsystems raise approval requests
   (`dynamic_profiles.py:386`, `retrospective_engine.py:111`,
   `cost_governor.py:356`, `rsi/review.py:414`, `reversible_delete_tool.py:218,284`,
   `dag_engine.py:514`), and two of them **actually refuse to proceed** until a
   human resolves the request (`dag_engine.py:354`, `reversible_delete_tool.py:284`).
   With four independent UI surfaces including an inline approve/deny card in the
   chat transcript. Octo's Matter confirms a *conclusion*; Alpha's blocks an
   *action*, which is the harder guarantee.
2. **A full autonomy/supervision plane Alpha-owned**: crash-loop-bounded process
   supervisor, `UNKNOWN`-state side-effect ledger, session lifecycle vocabulary,
   ordered shutdown drained through the lifespan (`AGENTS.md:278-289`), and 8
   supervisor loops registered in `autonomy.loops` with live coverage at
   `GET /api/ops/integration-health` surfaced in the Integration tab
   (`NavTabs.tsx:105`). Octo is a collaboration surface; Alpha also ships a
   runtime that survives restarts.
3. **Inter-installation agent peering with a real credential model** — pairing
   codes never in the card (`models.py:216-219`), three routes admin-gated
   because their bodies carry the inbound bearer (`peer_network.py:104-117`,
   `157-171`), per-recipient delivery receipts so a partial fan-out is never
   reported as success (`docs/ALPHA_PEER_NETWORK.md:165-166`), and exact-path
   public surfaces with CSRF exemptions. This is more security-conscious than a
   typical "connect two agents" story.

## Three places Alpha is closest to Octo but not actually there

1. **Discussion → Matter.** Alpha has the confirmation half (6 surfaces, blocks
   execution) and *nothing* for the crystallisation half. Grep for
   `crystallis|key_point|extract_points|summarize_discussion` returns only the
   cognitive-memory "belief crystallisation" (`memory/cognitive/consolidation.py:151`)
   and an evidence-ledger `proposal` kind (`evolution/evidence/provenance.py:57`).
   The nearest link is a *filter* (`projects/memory_bridge.py:40`,
   `OPEN_INTENTS = ("proposal", "vote")`) over messages the agent chose to post.
   **This is the single clearest place a user-visible feature is missing.**
2. **Agent Card as a spec object.** Alpha has 16 fields at the right URL, and is
   *more* honest than a conformance-claiming implementation would be — but it is
   `application/alpha-peer-card+json`, not A2A. An A2A SDK client rejects it
   (`capabilities` is `list[str]` where the spec requires an object; no
   `defaultInputModes`/`defaultOutputModes`/`security`). Same name, different
   meaning: the pattern the sibling found with Hermes, except Alpha at least
   labels it.
3. **Threads inside a Channel.** A group room is one flat `log` (`room.py:51`) and
   a project is a *filter over chat threads* (`projects.py:200-205`), not a space
   containing threads. Alpha's concurrency primitive is the **run**, and the two
   documented threading models — `docs/WORKFORCE.md:268-278` group-run SSE and
   `/api/missions/{id}/threads` — are the closest shapes, and neither is a thread
   inside a channel that a bot has joined.

---

## Handoff: two things that need a decision I am not allowed to make

1. **`docs/INDEX.md` generation will fail closed on this file.**
   `scripts/generate_docs_index.py` classifies every Markdown under `docs/`, and
   an unclassified document is a build failure (per `AGENTS.md:26-28`). This file
   has no `FILE_OVERRIDES` entry — the override table lives at
   `scripts/generate_docs_index.py:148+`, with neighbours at `:153`
   (`ALPHA_PEER_NETWORK.md`) and `:360-361` (`WORKFORCE.md`). Adding an entry
   means editing a file I do not own, so I did not. **The lead must add:**
   ```python
   "ALPHA_COLLABORATION_AUDIT.md": DocumentSpec(
       "architecture",
       "Collaboration-surface audit: object model, wiring and UI reachability vs. Octo.",
   ),
   ```
   Before `pnpm --dir frontend exec tsc --noEmit` or any CI docs gate that shells
   out to the generator will pass.

2. **`docs/WORKFORCE.md` is wrong in two places this audit verified.**
   I changed no product docs, but these are the exact defects, for whoever owns
   `docs/WORKFORCE.md`:
   - **`:268-278` documents `GET /api/groups/{name}/runs/{run_id}/stream`. That
     route does not exist.** `groups.py` has no `.../stream` decorator, and
     `lib/teamops.ts` — the only client — has no stream reader either. The
     documented five-event stream (`round_started`, `member_message`,
     `revision_requested`, …) is fictional; `docs/WORKFORCE.md:260-266` describes
     a five-step run lifecycle whose only implemented evidence is a `run.to_dict()`
     poll at `groups.py:253-265`.
   - **`:324-334` (Workforce tabs) is CORRECT** — I initially wrote this up as a
     defect and it is not. All six documented tabs exist and render:
     `WorkforceSection.tsx:1927-1932` (`inbox`, `presence`, `curator`,
     `automation`, `oversight`, `insights`), plus a seventh `warroom` tab at
     `:1926` that the doc omits. Recorded as **accurate**, because it was
     checked and the check contradicted the draft.
   - **`:413-421` lists the Teams/Groups API as 6 routes. `groups.py` has 11**,
     and the five the doc omits are the interesting ones: `by-project/{id}`
     (both verbs), and the whole `/{name}/runs` family
     (start / list / get / cancel).

   Two further stale claims worth a follow-up, in files I do not own:
   `projects/crew.py:10-11` says `get_or_create_project_room` "was written but
   never called" — it is called at `crew.py:238`, `groups.py:135` and
   `groups.py:146`; and `company/organization.py:46` calls its organizations
   "persistent" when the whole engine is process-memory (§1c).

## Method notes, so this can be re-run or challenged

- **`rg` was not used anywhere.** It is broken in this environment and silently
  returns nothing, which would have made every count below a plausible zero.
  Everything used `Select-String`, and every "zero hits" claim in this document
  was checked against a command that was also shown to produce non-zero hits on
  the same pattern family — an `rg` null result here proves nothing.
- **Caller counts are computed, not estimated**, by
  `Get-ChildItem -Recurse -Include *.py <root> | Select-String '<namespace>'`,
  grouped by file, then filtered to exclude `backend/tests/**`, the package's own
  directory, and `backend/.head_projects.py`.
- **`backend/.head_projects.py`** is a **tracked** 61,466-byte file
  (`git ls-files` confirms it) that duplicates ~54 lines of
  `routers/projects.py`. It matches nothing (`grep -rn '\.head_projects'`
  aside, no module imports it). It is unrelated to collaboration and outside my
  remit, but it is dead tracked weight inside the backend package directory and
  someone should decide whether it is intentional.
