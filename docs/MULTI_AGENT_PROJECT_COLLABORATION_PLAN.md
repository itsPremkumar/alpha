# Multi-Agent Project Collaboration — Enhanced Plan

**Branch analysed:** `main` (`b4f2b2d`), workspace `C:\Users\PREM KUMAR\Videos\alpha`
**Scope:** New Project creation → attach N AI agents → auto-create project group →
shared memory → @mention → planning/coordination/handoff → per-agent tabs in UI.

---

## 1. TL;DR — the headline finding

**Roughly 80% of what you are asking for is already built. It is not wired together.**

The single most important fact in this codebase:

> `GroupChatService.get_or_create_project_room()` — the exact function that creates a
> group for a project and joins all its agents — exists and works.
> **It has zero callers.** I grepped the whole tree: the only reference is its own
> definition (`groups/service.py:123`). The endpoint that exposes it,
> `POST /api/groups/by-project/{project_id}` (`app/gateway/routers/groups.py:129`),
> is never called by the frontend either.

So the "project group" you assume exists effectively never materialises. You are not
asking for a new feature so much as **for the wiring that turns five disconnected
primitives into one product**. That is a much smaller and much safer job than building
it from scratch — and it is why this plan is mostly integration, not invention.

---

## 2. What you asked for → what already exists

| # | Your ask | Existing code | Status |
|---|---|---|---|
| 1 | Create a project | `POST /api/projects` → `ProjectRow`; UI `ProjectsSection.tsx` | ✅ works |
| 2 | New Project creation tab | `projects` NavTab + "New project" form (`ProjectsSection.tsx:117-126`) | ✅ works |
| 3 | Add multiple agents to a project | `POST /api/projects/{id}/join` → `MembershipStore` (`projects/membership.py:90`) | ✅ backend, ❌ no UI |
| 4 | Shared memory across those agents | `ThreeLevelContextRouter` Level 2 → `projects/<id>/context.json` (`projects/context_router.py:137`) | ✅ works, ❌ incomplete |
| 5 | Auto-create a project group, all agents join | `get_or_create_project_room()` (`groups/service.py:123`) | ⚠️ **exists, dead code** |
| 6 | Agents talk: plan / coordinate / ask ideas | `GroupRoom` + `GroupOrchestrator`, 5 modes (`groups/room.py`, `groups/orchestration.py`) | ✅ works |
| 7 | Real fan-out so every agent actually runs | `GroupRunService.start_run` (`groups/runner.py`) — one subagent per member + moderator synthesis | ✅ works |
| 8 | @mention in group mode | `GroupOrchestrator.parse_mentions` — `@handle`, `@all`, `@everyone` (`orchestration.py:17`) | ✅ group only |
| 9 | Perfect coordination / advanced settings | `locks.py`, `handoffs.py`, `conflicts.py`, `decisions.py`, `goals.py`, `quorum.py`, `standup_engine.py`, `evidence.py` | ✅ all exist, ❌ not exposed or wired |
| 10 | One tab per agent in the UI | — | ❌ does not exist |

Net: **2 real gaps in the backend (wiring + memory bridge), 2 in the frontend
(per-agent tabs, @mention picker), and one data-model gap.** Everything else is
configuration and surfacing.

---

## 3. The eight real gaps (with evidence)

### Gap 1 — The project group is never created *(critical)*
`get_or_create_project_room()` is unreachable. Consequence: a project with 5 agents has
no room; agents can only DM pairwise (`bots/dm.py`), never as a crew.

### Gap 2 — Two sources of truth for "who is on this project"
- `runtime_home()/projects/membership.json` — written by `POST /projects/{id}/join`
- `runtime_home()/groups/rooms.json` → `room.members` — written by the groups service

They never sync. Join a bot via membership and it is **not** in the room. Add it to the
room and membership does not know. This drift is the root cause of most "agents didn't
coordinate" symptoms.

### Gap 3 — Group conversation never reaches shared memory *(critical)*
`get_project_memory()` merges: state, **last 3 decisions**, active locks, constitution
hash (`context_router.py:145-153`). The **group transcript is not included**.

So agents hold a meeting and the meeting evaporates. A bot that joins later, or a bot
whose context window rolled over, has no idea what the crew agreed. This is precisely
the failure mode you are trying to prevent with "shared memory".

### Gap 4 — @mention is not universal
`parse_mentions` lives inside `groups/orchestration.py` and is used only there.
`bots/dm.py` (`send_dm`) has **no mention parsing**. The lead agent's `message_agent`
tool has none. Frontend has no `@` picker — the only autocomplete in the app is the
slash-command palette in `Composer.tsx:97-198`.

### Gap 5 — `ProjectRow` has no agents column
`persistence/projects/model.py::ProjectRow` = `id, user_id, name, instructions,
presentation, status, created_at, updated_at`. To show a roster in the project list you
need N+1 requests. Projects cannot be created *with* agents in one call.

### Gap 6 — Orchestration mode is hard-coded
`get_or_create_project_room` always sets `mode="moderated"`, `moderator=members[0]`
(`service.py:148-149`). Your "advanced settings" ask cannot be satisfied without
persisting a per-project collaboration config.

### Gap 7 — The 1→2 agent transition is unhandled
Solo projects (`<2` unique members) return `None` — no room (`service.py:140-142`).
Correct, but when the user adds the 2nd agent the room must appear, and when they drop
to 1 it must be parked without destroying history. Nothing does this today.

### Gap 8 — No UI for any of it
No agent picker at project creation, no roster view, no per-agent tabs, no
collaboration settings panel. `frontend/src/lib/projects.ts` has no `join`/`presence`
functions at all — the client is 5 CRUD calls and `projectThreads`.

---

## 4. Enhanced architecture — one authoritative primitive

Rather than bolting sync logic onto three stores, introduce a single orchestration
entry point. Everything the UI and the API touches goes through it.

```
                        ┌──────────────────────────────┐
   UI / API  ──────────►│  ProjectCrewService          │  ← NEW, thin
                        │  projects/crew.py            │
                        └───────────────┬──────────────┘
                                        │ keeps these three consistent, always
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │ MembershipStore    │   │ GroupChatService   │   │ 3-Level Router     │
   │ membership.json    │   │ rooms.json         │   │ context.json       │
   │ WHO is on it       │   │ HOW they talk      │   │ WHAT they know     │
   └────────────────────┘   └────────────────────┘   └────────────────────┘
```

**`ProjectCrewService`** (`backend/packages/harness/alpha/projects/crew.py`) — new, ~200 lines, pure composition over existing code:

```python
def ensure_crew(project_id) -> CrewView:        # idempotent; safe to call on every read
def attach(project_id, bot, role="worker") -> CrewView
def detach(project_id, bot) -> CrewView
def set_collaboration(project_id, **settings) -> CollaborationConfig
def crew_view(project_id) -> CrewView           # ONE payload for the whole UI
```

`CrewView` is the important part for the frontend — one request instead of eight:

```python
@dataclass
class CrewView:
    project_id: str
    members: list[MemberBrief]      # name, role, status, current_task, blocked_reason
    room: RoomBrief | None          # name, mode, moderator, unread, last_seq  (None if solo)
    collaboration: CollaborationConfig
    shared_memory: dict             # Level-2 summary + transcript digest
    goals: list[GoalNode]
    open_conflicts: list[Conflict]
    active_locks: list[Lock]
    recent_events: list[ProjectEvent]
```

Invariants `ensure_crew` enforces on every call:
1. `room.members` ⇔ `membership.presence()` — reconciled both directions, membership wins.
2. Room exists **iff** `len(members) >= 2`; going 2→1 parks the room (keeps log), never deletes.
3. Leaving releases that bot's locks (`locks.py`) and blocks its open handoffs (`handoffs.py`).
4. Every mutation emits to `events.py` (`member_joined`, `member_left`, `room_synced`).

---

## 5. Shared memory — closing Gap 3

Extend `get_project_memory()` (`context_router.py:137`) so the conversation persists:

```python
base_memory = {
    ...
    "transcript_digest":  <last N group messages, role-tagged, compressed>,
    "open_questions":     <messages with intent in (proposal, vote) not yet resolved>,
    "member_briefs":      {bot: "<last contribution, 1 line>"},
    "pending_mentions":   {bot: ["<unanswered @mentions>"]},   # drives per-agent tab badge
}
```

Two additions:

- **`pending_mentions`** is what makes @mention *mean* something. An @mention that is
  not answered stays pending and is re-injected into that bot's next turn. This turns
  "tag and ask" from decoration into a work queue.
- **Compaction.** When `room.log` exceeds `transcript_digest_n` (default 40), summarise
  the overflow into `context.json` and emit a `standup` event. Reuse
  `projects/standup_engine.py` rather than writing a new summariser.

Budget guard: `system_prompt_snippet(max_chars)` already truncates at 4000
(`context_router.py:80-82`). Make the budget configurable per project (Gap 6) and split
it L1/L2/L3 proportionally instead of truncating tail-first.

---

## 6. Universal @mention

Extract to a shared module so all surfaces use one implementation:

**New:** `backend/packages/harness/alpha/bots/mentions.py`
```python
MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_-]+)")
def parse_mentions(text, members, *, roster=None) -> list[str]
def resolve_handle(handle, members, roster) -> str | None   # supports roles/departments
def pending_for(bot, messages) -> list[GroupMessage]
```

Move `GroupOrchestrator.parse_mentions` to delegate here (no behaviour change —
`backend/tests/test_group_chat_orchestration.py` must stay green).

Then wire into:
- `bots/dm.py::send_dm` — mentions in a DM create a pending item for the target
- `message_agent` tool (lead agent → agent)
- project group posting (already)
- the per-agent tab composer (new UI)

Beyond `@handle`, `@all`, `@everyone` (already supported), add **role mentions**
resolved through `bots/templates.py` departments — `@qa`, `@security`, `@frontend`
expand to every crew member with that role. That is what makes "ask the group for an
idea" work without naming individuals.

**Frontend:** adapt the slash-command palette in `Composer.tsx:97-198` into an
`@`-triggered picker. It already has filtering, keyboard nav, and a popup — roughly
100 lines of reuse instead of a new component.

---

## 7. Advanced collaboration settings (your "perfect coordination")

Persist per project at `runtime_home()/projects/<id>/project-config/collaboration.json`:

```yaml
orchestration_mode: moderated      # mention | moderated | quorum | parallel | round_robin
moderator: architect               # null → auto (first member)
max_concurrent_speakers: 3
mention_policy: strict             # strict = only mentioned agents reply | advisory
auto_handoff: true                 # wire projects/handoffs.py on task completion
conflict_policy: moderator         # block | vote (quorum.py) | moderator
lock_policy: advisory              # advisory | strict — wire projects/locks.py
require_evidence: true             # wire projects/evidence.py before marking done
memory_budget_chars: 6000
transcript_digest_n: 40
standup_interval_turns: 10         # wire projects/standup_engine.py
```

Every key maps to machinery that **already exists** — this is configuration surfacing,
not new engine work. Defaults reproduce today's behaviour so nothing regresses.

---

## 8. API surface

New or changed in `backend/app/gateway/routers/projects.py`:

| Method | Path | Change |
|---|---|---|
| `POST` | `/api/projects` | **changed** — accept `agents: [{name, role}]`, create crew atomically |
| `GET` | `/api/projects/{id}/crew` | **new** — the single `CrewView` payload |
| `POST` | `/api/projects/{id}/agents` | **new** — bulk attach (replaces per-bot `join`) |
| `DELETE` | `/api/projects/{id}/agents/{bot}` | **new** — detach + release locks |
| `PATCH` | `/api/projects/{id}/collaboration` | **new** — advanced settings |
| `GET` | `/api/projects/{id}/memory` | **new** — 3-level view for debugging |
| `POST` | `/api/projects/{id}/mentions/resolve` | **new** — clear a pending mention |
| `POST` | `/api/projects/{id}/join` | **kept**, delegates to crew (back-compat) |

Keep `join`/`leave` working — `test_projects_router.py` and
`test_workforce_integration.py` cover them.

**Frontend client** (`frontend/src/lib/projects.ts`) gains `getCrew`, `attachAgents`,
`detachAgent`, `updateCollaboration`, `getProjectMemory`.

---

## 9. UI spec

**A. Project creation** — `ProjectsSection.tsx`
Add an agent picker to the existing "New project" form: multi-select from
`BotRegistry` via `listBots()` (20 role templates in `bots/templates.py`).
On submit → `POST /api/projects` with `agents`.
Single agent selected → legacy solo mode (no room). 2+ → crew is provisioned.

**B. Project workspace tabs** — new `ProjectCrewSection.tsx`
```
[ New Project ] [ Group ] [ architect ] [ coder ] [ reviewer ] [ + Add agent ]
```
- **Group tab** — the shared room. Reuse `MessagesSection.tsx` (718 lines, already
  WhatsApp-style group rooms + `startRoomRun` + council). Do not rebuild it; embed it.
- **Per-agent tab** — that bot's private DM thread with the project
  (`canonical_bot_chat_id` = `bot-chat-{slug}`, `bots/dm.py`), plus: role, status,
  current task, held locks, its Level-1 memory, and a **pending-mentions badge**.
- **`+ Add agent`** — opens the picker; on add, `attach()` provisions room membership.

Follow the existing local-tab pattern in `WorkforceSection.tsx:36-46` / `:1855` —
there is no generic `<Tab>` component and no per-tab URL routing in this checkout.

**C. Collaboration settings drawer** — the YAML in §7, as a form.

**D. @mention picker** — `Composer.tsx` slash palette, cloned for `@`.

---

## 10. Phased roadmap

| Phase | Work | Files | Est. |
|---|---|---|---|
| **P0** | This audit + contract | `docs/MULTI_AGENT_PROJECT_COLLABORATION_PLAN.md` | done |
| **P1** | `ProjectCrewService` + `/crew` + `/agents`; wire `get_or_create_project_room`; back-compat `join`/`leave` | new `projects/crew.py`; `routers/projects.py` | ✅ **done** — 15 tests in `backend/tests/test_projects_crew.py` |
| **P2** | Shared-memory bridge: transcript digest, member briefs, pending mentions, compaction | new `projects/memory_bridge.py`; `projects/context_router.py` | ✅ **done** — 15 tests in `backend/tests/test_projects_memory_bridge.py` |
| **P3** | Universal mentions: `bots/mentions.py`, wire DM + `message_agent`, role mentions | new `bots/mentions.py`; `bots/dm.py`, `groups/orchestration.py` | |
| **P4** | UI: agent picker, crew tabs, per-agent tabs, `@` picker | `ProjectsSection.tsx`, new `ProjectCrewSection.tsx`, `Composer.tsx`, `lib/projects.ts` | |
| **P5** | Collaboration settings: schema, API, drawer, enforcement in orchestrator | new `projects/collaboration.py`, `groups/orchestration.py` | |
| **P6** | Observability: crew event stream, coordination health metric | `projects/events.py`, `routers/projects.py` | |

**Tests** (repo convention: TDD is mandatory in `backend/tests/`):
`test_projects_crew.py` (attach/detach/1→2/2→1/idempotency),
`test_projects_memory_bridge.py` (transcript → L2, pending mentions, compaction),
`test_bots_mentions.py` (handle, @all, role expansion, pending resolution),
`test_projects_collaboration.py` (mode enforcement, settings validation),
plus frontend `src/lib/projects-crew.test.mjs` for the client.

**Existing suites that must stay green:**
`test_group_chat_orchestration.py`, `test_projects_membership.py`,
`test_projects_router.py`, `test_projects_memory.py`, `test_bots_dm.py`,
`test_bots_groups_a2a.py`, `test_group_runs.py`, `test_workforce_integration.py`,
`test_projects_locks.py`, `test_projects_coordination.py`.

---

## 11. Repo invariants to respect

Pulled from `AGENTS.md` and the module guides — these will bite if ignored:

1. **TDD is mandatory** in `backend/tests/`. No feature without a test in the same change set.
2. **`ruff format --check` is enforced in CI** — run `make format` (backend) before pushing.
3. **Docs must move with code** — update `README.md` and the relevant `AGENTS.md` in the same change set.
4. **Never inject into the static system prompt.** Per-turn context (roster, crew, mentions)
   rides `DynamicContextMiddleware` reminders keyed off runtime `bot_name` / `repo_root` —
   this is a prefix-cache rule. Put crew context there, not in the system prompt.
5. **Config is not the roster.** Bots are runtime data in `runtime_home()/bots/roster.json`
   (`BotRegistry`), **not** `config.yaml`. There is no `bots:` block and there should not be one.
6. **Locks are process-local.** `context_router.py` locks do not give multi-process
   coherence; `LockManager` is JSON-backed. Do not assume distributed correctness.
7. **Async + thread safety.** New file-backed stores must follow the
   `threading.Lock` + `.tmp` + `replace()` atomic-write pattern used by
   `membership.py` and `service.py`.
8. **Frontend has no component tests** — only `node --test` logic tests
   (`src/lib/*.test.mjs`). `pnpm rstest` in the root `AGENTS.md` is stale for this checkout.
9. **Version lockstep** — if this ships in a release, `backend/pyproject.toml`,
   `frontend/package.json`, and `deploy/helm/.../Chart.yaml` must match; use
   `scripts/bump_version.sh`.

---

## 12. Decisions (resolved — user approved the recommended defaults)

Chosen defaults, now encoded in `CollaborationConfig` and `resolve_moderator()`:

1. **Moderator** — `architect` if present, else the first-attached agent.
2. **Default mode** — `moderated` (today's hard-coded value; safest).
3. **User as room member** — yes; the human operator posts into the Group tab and
   is never auto-removed by crew reconciliation.
4. **Per-agent tab scope** — that agent's project-scoped activity plus a DM composer.
5. **Migration** — lazy: `ensure_crew()` reconciles on first read, no startup pass.

---

## 13. Bottom line

Your instinct is right and the timing is good: the primitives for shared memory, group
chat, @mention, handoffs, locks, conflict resolution, and quorum voting all exist and
are individually tested. What is missing is a **single owner of the crew concept**.

Build `ProjectCrewService` first (P1). That one change makes the project group actually
exist, keeps membership and room in sync, and gives the UI one endpoint to render
everything. Phases 2–5 then become mostly incremental. Do **not** start with the UI —
it would be built on top of the drift described in Gap 2 and would have to be rebuilt.
