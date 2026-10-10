# Agent Live Status & Work Coordination

An enhanced plan for two problems that are actually one problem:

1. **Nobody can see what anybody else is doing.** Two agents edit the same file at
   the same time, and the damage is discovered by a failing test rather than by
   either agent.
2. **A crashed agent is indistinguishable from a finished one.** Its work
   vanishes from every display at once, and whatever it was holding stays held.

Both are already 80% built in this repository — as five disconnected primitives.
This document is the plan to make them one honest system.

**Status:** plan, not implemented. Nothing described here has been written.
**Scope:** any group room (nested included), plus project-linked rooms which
additionally get work claims and lock enforcement.

---

## 1. The two findings that decide the design

Before any architecture, two measured facts about the current code. Everything
below follows from them.

### Finding 1 — a crashed agent and a finished agent both read as `idle`

`backend/packages/harness/alpha/groups/presence.py` resolves a member's presence
from exactly two sources: `BotRegistry` (`status`, `last_active`) and
`AttendanceLedgerEngine` (`last_pulse`). Neither source is told that a process
died.

Trace a crashed agent through `_resolve()` (`presence.py:215`):

| Step | Value |
| --- | --- |
| `registry.get(name)` | row exists — `GroupChatService` auto-provisions every room member into the registry |
| `registry.get(name)["status"]` | `"active"` — **nothing flips this on crash** |
| `attendance.get(name)` | last pulse row, now hours old; `att_status` is `""` or a stale word, so the attendance branch is skipped |
| falls through to | `reg_status == "active"` |
| `_parse_epoch(last_active)` | a real timestamp, but old |
| `now - stamp > PRESENCE_WINDOW_SECONDS` (90) | true |
| **returns** | **`MemberPresence(name, "idle", ..., detail="no recent activity")`** |

So the exact state the user is describing — *"the work does not display and
clearly display that the agent has crashed"* — is not a missing feature that
needs adding. It is the **current, deliberate output of the existing resolver**,
and it is wrong in the specific way that matters: `idle` is what an agent looks
like when it has *finished cleanly*.

The UI then renders that as a neutral grey dot (`frontend/src/lib/time.ts:134`,
`PRESENCE_WINDOW_SECONDS = 90`). Two agents, one done, one dead, one picture.

> **The invariant this plan must establish: a `working → idle` transition requires
> proof of completion.** Either a terminal run event, or an explicit release.
> Silence is not completion, and must never render as it.

### Finding 2 — the crash evidence already exists, durably; presence just never looks at it

`RunManager` (`packages/harness/alpha/runtime/runs/manager.py`) already
terminalises every crash path with a named, persisted reason:

```python
ORPHAN_RECOVERY_STOP_REASON      = "orphan_recovered"      # lease expired, owner unreachable
GATEWAY_SHUTDOWN_RECOVERY_REASON = "gateway_shutdown"     # drained mid-flight
MODEL_FAILURE_RECOVERY_REASON    = "model_failure"
NETWORK_WAIT_RECOVERY_REASON     = "network_waiting"
RECOVERABLE_RUN_STOP_REASONS     = frozenset({those four})
```

`GroupRunService` has the same: `runner.py` rewrites any `status == "running"`
row to `interrupted` with `error="Gateway restarted while this run was in
flight."` at load time.

**Every one of those is a hard, durable proof of a crash, and none of them is
read by `presence.py`, which never touches the run store at all.** The system
already knows. The status display is simply not connected to it.

This is why the plan is integration rather than invention — and why phase 1 is
small.

### Finding 3 — conflict detection is dead code in production

`projects/conflicts.py::detect_lock_conflicts()` looks for two live locks, same
project, same scope, same path, different owners. But `LockManager.acquire()`
*raises* `LockConflictError` on exactly that condition — so by construction the
state it hunts for can never exist through the public API.

`backend/tests/test_projects_coordination.py:53` documents this itself:

```python
manager.acquire("p", "file", "a.py", "coder")
# Same owner re-acquire is not a conflict; simulate a second owner by
# direct insert (acquire would raise, which is the live behavior).
manager._locks["lk-other"] = ResourceLock(..., owner_bot="frontend")
```

Only the **refusal** (HTTP 423) is live. Detection, `Conflict` records,
`conflict_detected` events and the ADR-on-resolve path are unreachable without
poking at a private dict. Meanwhile `CollaborationConfig.lock_policy` accepts
`"strict"` and **nothing ever reads it**.

So today's real behaviour is: a conflicting write is refused with a 423 that
carries the holder's name — good — but nothing records that it happened, nothing
tells the *other* agent, nothing reconciles after a crash, and the `"strict"`
policy that sounds like enforcement is decorative.

---

## 2. What already exists (build on this, do not rebuild it)

| Concern | Module | Reality |
| --- | --- | --- |
| Presence resolution | `alpha/groups/presence.py` | Stateless. Reads registry + attendance. **No store, no heartbeat, no crash.** |
| Room membership | `alpha/groups/service.py`, `roster.py`, `scope.py` | Mature. Nesting, rules, inheritance, relay, 5 orchestration modes. |
| Crew reconciliation | `alpha/projects/crew.py` | `ensure_crew()` — idempotent, **safe on every read**, the repo's established reconcile pattern. |
| Membership + task | `alpha/projects/membership.py` | Has `current_task_id`, `blocked_reason`, `heartbeat()`. **Nothing reads the first two.** |
| Exclusive ownership | `alpha/projects/locks.py` | file/dir/task/artifact, TTL 1800s, access requests, supervisor override, dir-covers-children. HTTP-only — **no model tool**. |
| Conflict records | `alpha/projects/conflicts.py` | Structurally unreachable (Finding 3). |
| Event bus | `alpha/projects/events.py` | Append-only JSONL, 34 typed events, sequence numbers. |
| Run lifecycle | `alpha/runtime/runs/manager.py` | Sole lifecycle owner. Leases, orphan recovery, 4 crash stop reasons. |
| Liveness health | `alpha/bots/health.py` | `healthy/stale/stalled/dead` + task leases. **A different axis from presence.** |
| Group runs | `alpha/groups/runner.py` | Fan-out + moderator synthesis. Per-member `member_results` on completion only — **nothing during the run**. |
| Room messages | `alpha/groups/room.py` | 19 `MessageIntent` values, already including `status`, `handoff`, `blocker`, `warning`, `escalation`. |
| Tool surface | `tools/builtins/group_chat_tool.py` | 7 actions. No presence, no locks. |
| Frontend | `lib/groups-tree.ts`, `GroupTreeSidebar.tsx`, `MessagesSection.tsx` | Pure derivation, busy dot gated on a measured count, `null ≠ 0` discipline already established. |

**Two hard invariants this plan must not break** (both pinned by existing tests):

1. `GroupRoom.members` is **crew-owned**. `crew._sync_room()` *deletes* any entry
   project membership does not claim. Presence, claims and nested/rule-matched
   members must **never** be written there — they live in `GroupRoster` and are
   computed at read time.
2. `direct_count` and `effective_count` must **always travel together**. A header
   claiming "3 members" over six visible bots is a fabricated count, and
   `test_groups_routes_features.py` already enforces it.

---

## 3. Architecture — three layers, three phases

```
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 0  ·  ACTIVITY LEDGER            (phase 1 · visibility first)     │
│  alpha/groups/activity.py                                                  │
│  Who is working, on what, since when, and what they are holding.          │
│  Derived from RunManager ground truth + an explicit heartbeat.            │
│  Two axes: ACTIVITY (working/idle/blocked/unresponsive) × HEALTH (from    │
│  alpha.bots.health). Never collapsed into one enum.                       │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ emits into the room transcript
┌───────────────────────────────▼────────────────────────────────────────┐
│  LAYER 1  ·  WORK CLAIMS                (phase 2 · advisory coordination)  │
│  alpha/groups/claims.py                                                   │
│  "I intend to touch X."  Visible. Expiring. NON-REFUSING by default.     │
│  Soft-conflict detection that actually fires (fixes Finding 3).           │
│  Orphaned-claim marking when the holder is confirmed crashed.             │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ promoted to, only when asked for
┌───────────────────────────────▼────────────────────────────────────────┐
│  LAYER 2  ·  ENFORCEMENT + RECLAMATION   (phase 3 · opt-in strict)        │
│  lock_policy: "strict" actually refuses.  Reclaim moves a dead holder's   │
│  claim to a live agent, and records the evidence for the transfer.        │
└────────────────────────────────────────────────────────────────────────┘
```

**Why layered and not one system.** Visibility must never be able to block
work. If the same mechanism that draws the status dot can also refuse a write,
then a bad heartbeat turns into lost work, and operators will turn it off — and
then they have no visibility either. Splitting them means phase 1 ships and is
useful even if phases 2 and 3 are never built.

**Why claims are a separate store from locks, not a flag on them.** They are
different guarantees:

| | Work claim | Resource lock |
| --- | --- | --- |
| Guarantee | *intent*, visible to peers | *exclusive ownership*, enforced |
| On overlap | `soft_conflict`, nothing refused | `LockConflictError` → HTTP 423 |
| Lifetime | short, renewed by heartbeat | TTL 1800s |
| Survives holder crash | **no** → `orphaned`, reclaimable | **yes** → blocks until TTL |
| Written by | an agent saying "I'm on it" | an agent about to mutate |

Two guarantees, two stores, is honest. One store with a boolean would be a lie
about half its states.

---

## 4. PHASE 1 — Activity Ledger

### 4.1 Two axes, never one enum

Presence currently answers one question with one word. That is why `idle` had to
absorb both "finished" and "crashed". Split it:

```python
# alpha/groups/activity.py

ActivityState = Literal[
    "working",     # holds a task or a live run; heartbeat fresh
    "idle",        # proven not to be working
    "blocked",     # reported blocked, with a reason
    "unresponsive",# was working, no terminal event, heartbeat gone  ← NOT "idle"
    "crashed",     # hard evidence of death  (§4.3)
    "offline",     # administratively out: suspended / archived
    "unknown",     # no owning system has any record
]
```

Health is **not** a second field on the same record — it is the existing
`alpha.bots.health` verdict (`healthy/stale/stalled/dead`) read alongside, and
the wire payload carries both plus a `tone` the UI maps to one dot:

```python
@dataclass
class AgentActivity:
    room_id: str
    bot_name: str
    activity: ActivityState
    detail: str                      # human sentence, never empty
    health: str | None               # healthy|stale|stalled|dead|sleeping|None
    tone: Literal["busy","ok","warn","bad","off","unknown"]
    since: float                     # epoch, when this activity was entered
    run_id: str | None               # the RunManager run doing the work
    claim_ids: list[str]             # claims held right now
    held_paths: list[str]            # derived from claims, for display
    last_heartbeat_at: float | None  # None when never reported — never "now"
    evidence: dict[str, Any]         # WHY we believe this (see §4.3)
    attributed_by: str               # "explicit" | "roster_match" — see §4.4
```

`last_heartbeat_at` follows the existing `_parse_epoch` discipline in
`presence.py`: an unreadable stamp is `None`, **never `time.time()`**, which would
manufacture a fresh heartbeat from a missing one.

### 4.2 Where the heartbeat comes from — it must not depend on the agent

The naive design — "agents post a heartbeat regularly" — fails exactly when it
matters. A crashed agent cannot report its own crash, and a heartbeat that costs
a model call is a heartbeat that gets skipped under load.

So the primary signal is **event-driven from ground truth**:

| Signal | Source | Fires on |
| --- | --- | --- |
| Run started | `RunManager.try_start()` | every run, free |
| Run terminalised | `RunManager.set_status()` / `update_run_completion()` | success, error, timeout, **and all 4 crash reasons** |
| Ownership lost | `RunRecord.ownership_lost` | fenced worker, peer takes over |
| Group run member settled | `GroupRunService._execute` | per member — **this is the first live per-member signal in a group run** |
| Explicit heartbeat | `POST /{name}/heartbeat` | optional, for phase detail an agent wants to publish |

Rather than patching `RunManager` (whose lifecycle ownership is load-bearing and
covered by a large lease/CAS contract), publish on the existing in-process bus —
`alpha/events/bus.py`, already bounded, drop-oldest, and a no-op when disabled:

```
group.activity.started   { bot_name, run_id, thread_id, project_id?, room_name? }
group.activity.terminal  { bot_name, run_id, status, stop_reason, error }
group.activity.ownership_lost { bot_name, run_id }
group.activity.heartbeat { bot_name, activity, detail, claim_ids }
group.activity.released  { bot_name, run_id }
```

An `ActivityReconciler` subscribes to the `group.activity.` prefix and folds
those into the ledger. `RunManager` is untouched; the coupling is one-way,
optional, and degrades to nothing when the bus is off.

**This is the load-bearing choice of the whole plan.** It means presence is
correct *by construction* — it is a projection of the run lifecycle that already
owns the truth — rather than correct only while agents behave.

### 4.3 The derivation table — what may honestly be claimed

Every state carries its evidence. This table is the contract; a state with no
evidence row here must not exist in the code.

| Evidence | `activity` | Claim strength |
| --- | --- | --- |
| `RunRecord.status` terminal, `stop_reason ∈ RECOVERABLE_RUN_STOP_REASONS` | **`crashed`** | **hard** |
| `RunRecord.status` terminal, `status ∈ {error, timeout}` | **`crashed`** | **hard** (+ verbatim `error`) |
| `RunRecord.ownership_lost is True` | **`crashed`** | **hard** |
| `GroupRun.status == "interrupted"` (gateway restart) | **`crashed`** | **hard** |
| `run_id` recorded, run absent from the store, no terminal event | **`unresponsive`** | inferred — **never rendered "crashed"** |
| `bots/health` liveness `stalled` (task lease expired) | **`unresponsive`** | measured |
| `bots/health` liveness `dead` | **`unresponsive`** | measured |
| `working`, heartbeat age ≤ 90s, run live or none | **`working`** | measured |
| Terminal `success`, or an explicit `group.activity.released` | **`idle`** | **proven** |
| `blocked_reason` set by an explicit heartbeat | **`blocked`** | reported |
| Registry `status ∈ {suspended, archived}` | **`offline`** | measured |
| Registry row `active`, never any activity | **`unknown`** | measured |

Three rules make the table a contract rather than a table:

1. **`working → idle` requires proof.** A terminal success or an explicit
   release. A heartbeat simply going quiet is `unresponsive`. This single rule is
   Finding 1's fix.
2. **`unresponsive` is not `crashed`, and must never be styled as `crashed`.**
   Heartbeat silence is *evidence of a problem*, not proof of death — an agent
   inside a 20-minute tool call is silent too. Only the first four rows earn the
   word. A UI that renders both in red-crash styling has re-created the original
   lie one layer up.
3. **A crashed agent keeps its row.** It does not disappear. Its last `detail`,
   `run_id`, `since` and held `claim_ids` persist, with `activity: "crashed"`.
   This is directly the user's requirement, and it is also what makes the
   orphaned claims in phase 2 discoverable at all.

### 4.4 Attribution, and the trust boundary

A run must be attributable to a room, and the attribution must not be
client-asserted. `app/gateway/AGENTS.md` is explicit: `body.context` is
whitelist-merged and `body.config` is copied verbatim, and any new key needs
**both** a trust decision and a destination decision.

| Source | `attributed_by` | Trust |
| --- | --- | --- |
| Server-assigned room/project binding on the run | `explicit` | trusted |
| No binding; bot name matches the room's `effective_members()` | `roster_match` | inferred — **disclosed, never silent** |

A run with neither is **not** attributed to any room. It is a real run with no
known room, and guessing would put one project's activity dot in an unrelated
room — the exact class of fabricated claim this repository's guides forbid
throughout.

For nested rooms, attribution matches against `effective_members()`, never
`room.members`, so an inherited or rule-matched member is attributed correctly.
Inheritance stays a projection: editing the child never orphans it.

### 4.5 Storage and reconcile-on-read

`runtime_home()/groups/activity.json`, same discipline as `locks.json` and
`membership.json`: `threading.Lock`, `.tmp` + `replace()`, and a load failure
that logs and starts empty rather than raising.

Reconciliation is **lazy, on read** — the established pattern in this codebase:
`crew.ensure_crew()` ("safe to call on every read") and
`GET /{id}/locks` calling `sweep_expired()`. Each activity read sweeps expired
records and re-derives from the run store, so the display is correct without a
background loop.

This is also the cheap option. A new supervisor loop would move the manifest
count 9 → 10 and force `contracts/feature_manifest.json` plus the counts in
`README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md` and `docs/COMPARISON.md`
to be regenerated together. Lazy-on-read moves **zero** counts and is always
fresher than a sweep interval.

> **Optional later:** a `presence_reconcile` loop for rooms nobody is watching,
> so a crashed agent is noticed even with no UI open. If added, it is *in
> addition to* — never instead of — reconcile-on-read, and the five doc files
> plus the manifest move in the same change set.

### 4.6 HTTP surface

All declared **before** `GET /{name}` at `routers/groups.py:608`, or Starlette
answers `Room 'activity' not found` — the same trap as `/tree` and
`/{name}/rules/preview`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/groups/{name}/activity` | full ledger + `by_activity` + `by_tone` |
| `GET` | `/api/groups/{name}/activity/{bot}` | one agent, full evidence |
| `POST` | `/api/groups/{name}/activity/heartbeat` | explicit heartbeat / phase report |
| `GET` | `/api/groups/{name}/activity/stream` | SSE, opt-in (see §7.4) |

`GET /{name}/members` keeps its current shape and its existing four aliases. It
gains `activity` and `health` **beside** `state`; `state` keeps its five values
so `test_groups_routes_features.py` stays green. New clients read `activity`.

Both membership counts continue to travel together, and `by_activity` — like
the existing `by_state` — is a breakdown that must sum to `count`, kept
deliberately separate from the two membership counts.

### 4.7 Model surface — actions, not a new tool

Adding a `@tool` changes `BUILTIN_TOOLS`, `contracts/feature_manifest.json`, and
the counts in five doc files. The repo's own precedent (`swarm` → `strategy` /
`telemetry` / `trace`; `bot_roster` → 19 actions) is to add actions instead. So
`group_chat` gains:

| Action | Reads |
| --- | --- |
| `status` | who is working, on what, and who crashed — the agent's own read |
| `heartbeat` | report its own phase / claim ids |
| `claim` / `release` | phase 2 |

Every action stays a closed `Literal` with every member handled in the
dispatcher — `test_every_advertised_action_is_handled_in_the_dispatcher` exists
because `action="status"` once sat in a `Literal` with no branch and returned
"unknown action" for a schema-valid call.

---

## 5. PHASE 2 — Work Claims

### 5.1 The model

```python
# alpha/groups/claims.py

@dataclass
class WorkClaim:
    claim_id: str
    room_id: str
    project_id: str | None      # when the room is project-linked
    holder: str                 # bot name, lowercased
    kind: Literal["file", "dir", "symbol", "task", "artifact", "requirement"]
    subject: str                # path / symbol / task id
    intent: Literal["reading", "editing", "reviewing"]
    run_id: str | None
    created_at: float
    renewed_at: float
    expires_at: float           # short: 120s default
    state: Literal["active", "released", "expired", "orphaned"]
    orphaned_at: float | None
    orphan_evidence: dict | None   # why we believe the holder died
```

Short TTL (120s, renewed by the same heartbeat that renews the activity record)
is the whole point: an advisory claim from an agent that then goes quiet must
**evaporate**, not sit there misleading peers for 30 minutes the way a lock does.

### 5.2 Soft conflicts that actually fire

This is where Finding 3 gets fixed. Claims are advisory, so two agents claiming
the same path both succeed — and a **soft conflict is raised** instead of
refused:

```python
def detect_soft_conflicts(room_id, *, now) -> list[SoftConflict]
```

Matching rules, all derived from what `LockManager._live_holder()` already
proves works:

- **exact** — same `kind` + same `subject`, different holders.
- **dir covers children** — a `dir` claim on `src/api/` overlaps a `file` claim
  on `src/api/routes.py`. Same rule as `locks.py:132`, so the two layers cannot
  disagree about what a directory covers.
- **path normalisation** — `"./a.py"` and `"a.py"` are the same subject. Neither
  store normalises today, which is a latent bug worth fixing here.
- **self-overlap is not a conflict** — one agent holding two overlapping claims is
  it reasoning about a file it already owns, and flagging it would train every
  agent to ignore the signal.
- **crashed-holder claims are not live conflicts** — they are `orphaned`
  (§5.3), and a soft conflict against an orphan is reported as *reclaimable*.

Each conflict carries both holders' `activity` states, so the room can say
"`coder` and `frontend` both claim `src/api.py`; `coder` is `crashed` since
14:03, its claim is reclaimable" — which is actionable, where today's 423 says
only "Locked by coder".

### 5.3 Crash → orphaned claim

When the reconciler confirms a holder `crashed` (§4.3 hard rows only), its
active claims move to `state="orphaned"` with `orphan_evidence` naming the stop
reason. Nothing is transferred. An orphan is a **claim that is available**,
which is exactly the state a surviving peer needs to see in order to take over —
and exactly the state that disappears today along with the crashed agent.

This is the seam between the two phases and the reason phase 1 must land first:
without a durable crash verdict there is no honest trigger for orphaning.

### 5.4 Communication — reuse the transcript, do not add a sixth channel

This repository already has **five** inter-agent messaging mechanisms (room
transcript + relay, quorum, thread A2A mailbox, bot DM mesh, bot inbox) and they
do not talk to each other. A sixth would be worse than none.

So claims and activity publish into the **room transcript** using the
`MessageIntent` values that already exist:

| Event | `intent` |
| --- | --- |
| claim taken / released | `status` |
| soft conflict detected | `warning` |
| holder confirmed crashed | `blocker` |
| claim orphaned | `handoff` |
| reclaim performed | `handoff` + `decision` |

`post_message()` already resolves next speakers, so this is also the wake-up
path: a conflict message with `@coder` actually reaches the orchestrator. The
`taint.py` screening already applies to every contribution.

This is what closes the loop the user described — *"they can make decisions and
communicate with each other and they can coordinate the work"* — without
inventing a transport.

---

## 6. PHASE 3 — Enforcement & reclamation (opt-in)

> **Implemented.** The refusal below is live: `groups/enforcement.py::
> StrictEnforcer` supplies the verdict, and `groups/write_watch.py::
> enforce_write` consults it from `ReadBeforeWriteMiddleware` on every
> `write_file`/`str_replace` call — a write against another agent's live
> claim is refused before the handler runs, stamped with the holder's
> name and reason. Advisory stays the default and never regresses.
> Reclamation of a confirmed-dead claim remains an explicit decision,
> never an automatic transfer.

`lock_policy` already accepts `"strict"`. Making it mean something:

**Refusal.** With `lock_policy: strict`, an agent about to mutate a path held by
another bot's **active lock or claim** is refused, with the holder's name, its
`activity` state, and how long it has held it. Default stays `advisory` so
nothing regresses.

**Reclamation.** An orphaned claim is transferred by an explicit decision —
`supervisor`, the room moderator, or the claim's own `kind == "task"` successor.
The transfer is recorded as a `decision` message plus a `conflict_resolved`
event. It is never automatic: the crash is *evidence*, and the transfer is a
*decision*, and collapsing them would let a transient network blip hand a
half-finished edit to a second agent.

**Crash-driven lock release.** Today a crashed agent's `ResourceLock` blocks its
peers for the full 1800s TTL with nobody notified. Under strict mode, a lock
whose owner is *hard-confirmed crashed* is marked reclaimable and its TTL is
shortened, so recovery does not wait out half an hour. Default off; TTL
shortening is a policy decision, not a silent optimisation.

---

## 7. Frontend

### 7.1 New pure module

`frontend/src/lib/group-activity.ts` — no `fetch`, matching the
`groups-tree.ts` pattern. Exports `toneFor(activity, health)`,
`activityHeadline(records, error?)`, `conflictRows(claims, records)`,
`sortByUrgency(records)`. Unknown enum → verbatim. Absent measurement →
`null`, never `0` or `""`.

Tests go in `frontend/src/lib/group-activity.test.mjs`, because **`pnpm test`
globs `src/lib/*.test.mjs` only** — a suite anywhere else silently never runs.

### 7.2 UI changes

- **`GroupTreeSidebar`** — the busy dot is gated on a measured count
  (`entry.busyCount !== null && entry.busyCount > 0`). Extend to: busy dot,
  **crashed badge** in a distinct colour, and a stale count when the read failed.
  A failed read keeps the row visibly unmeasured.
- **`MessagesSection`** — a per-agent activity row: name, state word, current
  task, elapsed (`since`), and held paths as chips. Crashed agents keep their row
  with the crash reason — never blank.
- **New `ConflictBanner`** — soft conflicts and orphaned claims, at the top of
  the room, each naming both holders and offering *request access* /
  *reclaim* in strict mode.

### 7.3 Honesty pins

Follow `groups-nesting-honesty.test.mjs`, which is 17 source-regex pins. Pin at
minimum: `unresponsive` and `crashed` never share a colour path; a failed read
never renders `0`; both membership counts or neither; a crashed agent's row is
never conditionally hidden.

### 7.4 Polling, then SSE

The existing view polls on a 10s timer (`MessagesSection.tsx:234`). Presence
should join that timer rather than start its own — one cadence, one place.
`GET /{name}/activity/stream` (SSE) is opt-in and additive for rooms where a 10s
lag on a crash notice is too slow; the same `StreamBridge` / `sse_consumer`
machinery already used by run streams applies, including the `StreamGap` contract
so a reconnect after a trimmed buffer discloses the gap instead of silently
resuming.

---

## 8. Tests

TDD is mandatory in `backend/tests/`.

| File | Covers |
| --- | --- |
| `test_group_activity.py` | derivation table row by row; **`working → idle` requires proof**; `unresponsive` ≠ `crashed`; a crashed row persists its detail, run id and claims; unknown enum → verbatim |
| `test_group_activity_attribution.py` | `explicit` vs `roster_match`; untrusted room binding refused; nested inherited member attributed; a run matching no room is attributed to nothing |
| `test_group_activity_reconcile.py` | all four `RECOVERABLE_RUN_STOP_REASONS` → `crashed`; ownership-lost; absent run → `unresponsive`; restart-survivability; corrupt store degrades |
| `test_group_claims.py` | exact + dir-covers-children + normalisation; self-overlap not a conflict; orphaned holder is reclaimable not live; claim TTL expiry |
| `test_group_activity_routes.py` | route order ahead of `/{name}`; 404 not empty; `by_activity` sums to `count`; both membership counts together |
| `test_group_activity_tool.py` | every advertised `group_chat` action handled in the dispatcher; closed `Literal` |
| frontend `src/lib/group-activity.test.mjs` | tone mapping, headlines, conflict rows, `null ≠ 0` |

**Suites that must stay green:** `test_groups_routes_features.py`,
`test_group_nesting.py`, `test_group_nesting_routes.py`, `test_group_runs.py`,
`test_group_chat_orchestration.py`, `test_projects_crew.py`,
`test_projects_locks.py`, `test_projects_coordination.py`,
`test_projects_membership.py`, `test_route_order_literal_above_catchall.py`,
`test_no_orphan_modules.py`, `test_feature_manifest_wiring.py`.

`test_no_orphan_modules.py` is the one to watch: a new module with no import, no
dotted loader path and no config entry **fails the build**. Every new module
needs a production reference, never a test-only one.

---

## 9. Docs to update in the same change set

Per the documentation policy, this is not a follow-up:

- `backend/packages/harness/alpha/groups/AGENTS.md` — the activity + claims
  contract, and the two hard invariants restated.
- `backend/packages/harness/alpha/projects/AGENTS.md` — how claims differ from
  locks and how they compose.
- root `AGENTS.md` — the "Nested groups" table gains a row for the ledger.
- `docs/INDEX.md` — regenerated by `scripts/generate_docs_index.py`, never hand
  edited. This document is classified via a `FILE_OVERRIDES` entry.

**Capability counts do not move** if the plan is followed: no new `@tool` (only
`group_chat` actions), no new router (extend `routers/groups.py`), no new
supervisor loop (reconcile-on-read). 136 tools / 66 routers / 45 middlewares /
9 loops / 117 engines all stay correct, so `contracts/feature_manifest.json` does
not need regenerating for this work.

> **Pre-existing drift found while scoping this, unrelated to the plan:** the
> root `AGENTS.md` says "63 routers … 9 supervisor loops" and `backend/AGENTS.md`
> says "61 routers … 8 supervisor loops". The generated manifest and the root
> guide agree at 63 and 9; **`backend/AGENTS.md` is stale on both.** Worth
> correcting in a separate change so it is not confused with this feature.

---

## 10. Rollout

| Phase | Ships | Value even if nothing after it lands | Counts move |
| --- | --- | --- | --- |
| **1** Activity ledger + `status` action + UI | crash is visible and provable | ✅ | no |
| **2** Claims + soft conflicts + transcript signalling | peers warn each other | ✅ | no |
| **3** `strict` refusal + reclamation | conflicts can actually be prevented | ✅ | no |

Phase 1 is small — mostly connecting a display to evidence the run store already
holds — and it fixes the user's most painful symptom on its own. Phases 2 and 3
are where the coordination gets real. Nothing in phase 1 can block work, which is
what makes it safe to land first.

---

## 11. What this plan does not solve

Stated plainly, because a plan that claims too much is worse than none:

- **Cross-process exactly-once.** Every store here is single-process JSON under
  `runtime_home()`, atomic and restart-recoverable. Under `GATEWAY_WORKERS > 1`
  two workers can each believe they hold the same claim. The SQL run-lease
  machinery is the only real answer, and it is not what this plan uses. Do not
  describe this as distributed coordination.
- **True file-level merge.** Worktree leases (`projects/workspace.py`) give
  agents separate branches; nothing here reconciles two divergent edits to one
  file. A claim prevents a *collision*, not a semantic conflict.
- **Prompt-level trust.** `post_message` writes to a transcript other agents
  read. `groups/taint.py` screens and discloses; per its own docs it is a
  mitigation, not a guarantee. A malicious peer can still lie about its claims.
- **A crashed *Gateway*.** Every agent is equally dead; the display correctly
  shows all of them `crashed`, which is honest but not actionable. Recovery is
  `SafeRunRecoveryService`'s job, deliberately not duplicated here.
